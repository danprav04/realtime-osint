import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Dict, Any, List
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from backend.app.api import health_router, pipeline_router, settings_router
from backend.app.api.themes import router as themes_router
from backend.app.api.sources import router as sources_router
from backend.app.api.events import router as events_router
from backend.app.api.stats import router as stats_router
from backend.app.auth import require_api_key
from backend.app.config import get_settings
from backend.app.db.seed import seed_initial_data
from backend.app.db.session import close_db, get_session_factory, init_db
from backend.app.services.redis_bus import get_redis_bus
from backend.app.services.socket_server import (
    get_broadcast_logs,
    init_socket_server,
    shutdown_socket_server,
    sio_app,
)
from backend.app.services.telegram_bot import get_telegram_bot

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("osint.main")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Application lifespan manager handling startup initialization
    and clean graceful shutdown.
    """
    settings = get_settings()
    logger.info(f"Starting {settings.APP_NAME} v{settings.APP_VERSION}...")

    # 1. Initialize database and schemas
    await init_db()

    # 2. Seed initial pre-configured US-Iran theme and default settings
    session_factory = get_session_factory()
    async with session_factory() as session:
        try:
            await seed_initial_data(session)
        except Exception as exc:
            logger.error(f"Error during initial seed: {exc}", exc_info=True)

    # 3. Connect Redis Pub/Sub Bus (with graceful fallback if offline)
    bus = get_redis_bus()
    await bus.connect()

    # 4. Initialize Real-Time Gateway and Telegram Alert Dispatcher
    await init_socket_server(bus)
    telegram_bot = get_telegram_bot()
    await telegram_bot.start()

    logger.info("Application startup sequence completed successfully.")
    yield

    # Shutdown sequence
    logger.info("Initiating graceful shutdown sequence...")
    await telegram_bot.stop()
    await shutdown_socket_server(bus)
    await bus.disconnect()
    await close_db()
    logger.info("Application shutdown completed.")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application instance."""
    settings = get_settings()
    application = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        description=(
            "Production-grade, real-time Open Source Intelligence (OSINT) gathering service. "
            "Scrapes, parses, and analyzes live data from multiple configurable sources using AI."
        ),
        lifespan=lifespan,
    )

    # Configure CORS
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Mount Socket.IO ASGI server at /socket.io
    application.mount("/socket.io", sio_app)

    # Include routers
    application.include_router(health_router)
    application.include_router(settings_router)
    application.include_router(pipeline_router)
    application.include_router(themes_router)
    application.include_router(sources_router)
    application.include_router(events_router)
    application.include_router(stats_router)

    # Root endpoint
    @application.get("/", tags=["General"])
    async def get_root() -> Dict[str, Any]:
        return {
            "service": settings.APP_NAME,
            "version": settings.APP_VERSION,
            "status": "operational",
            "docs_url": "/docs",
            "health_url": "/health",
        }

    # Example secured test endpoint for verifying API Key authentication
    @application.get("/api/auth/verify", tags=["Auth"])
    async def verify_auth(api_key: str = Depends(require_api_key)) -> Dict[str, Any]:
        return {
            "status": "authenticated",
            "message": "API key successfully verified",
        }

    # Test inspection endpoint for test verification of WebSocket broadcasts
    @application.get("/test/websocket-logs", tags=["Testing"])
    async def get_test_websocket_logs() -> List[Dict[str, Any]]:
        return get_broadcast_logs()

    return application


app = create_app()

