import json
import logging
import os
import sys
import time
import uuid
from contextlib import asynccontextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Dict, List, Optional

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from backend.app.api import health_router, pipeline_router, public_router, settings_router
from backend.app.api.events import router as events_router
from backend.app.api.sources import router as sources_router
from backend.app.api.stats import router as stats_router
from backend.app.api.themes import router as themes_router
from backend.app.api.embeddings import router as embeddings_router
from backend.app.api.summary import router as summary_router
from backend.app.auth import require_api_key
from backend.app.config import get_settings
from backend.app.db.seed import seed_initial_data
from backend.app.db.session import close_db, get_session_factory, init_db
from backend.app.plugins.supervisor import get_ingestion_supervisor
from backend.app.services.redis_bus import get_redis_bus
from backend.app.services.socket_server import (
    get_broadcast_logs,
    init_socket_server,
    shutdown_socket_server,
    sio_app,
)
from backend.app.services.telegram_bot import get_telegram_bot

# Context variable for request trace ID correlation
trace_id_ctx: ContextVar[Optional[str]] = ContextVar("trace_id", default=None)


class JSONLogFormatter(logging.Formatter):
    """Structured JSON formatter with request tracing for container observability."""

    def format(self, record: logging.LogRecord) -> str:
        payload: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "environment": os.getenv("ENVIRONMENT", "production"),
        }
        trace_id = trace_id_ctx.get() or getattr(record, "trace_id", None)
        if trace_id:
            payload["trace_id"] = trace_id

        if record.levelno >= logging.WARNING:
            payload["source_file"] = record.filename
            payload["line_number"] = record.lineno
            payload["function"] = record.funcName

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        if hasattr(record, "extra_data") and isinstance(record.extra_data, dict):
            payload.update(record.extra_data)

        return json.dumps(payload, default=str)


def setup_application_logging() -> None:
    """Initialize structured JSON or standard format logging based on environment."""
    log_format = os.getenv("LOG_FORMAT", "text").lower()
    log_level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    log_level = getattr(logging, log_level_name, logging.INFO)

    root = logging.getLogger()
    root.setLevel(log_level)
    root.handlers.clear()

    handler = logging.StreamHandler(sys.stdout)
    if log_format == "json":
        handler.setFormatter(JSONLogFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    root.addHandler(handler)

    from backend.app.services.server_logs import get_server_log_buffer_handler
    buffer_handler = get_server_log_buffer_handler()
    if buffer_handler not in root.handlers:
        root.addHandler(buffer_handler)

    # Suppress verbose HTTP client logs to protect against bot token leakage in stdout
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


setup_application_logging()
logger = logging.getLogger("osint.main")


class RequestTracingMiddleware(BaseHTTPMiddleware):
    """Assigns trace ID to incoming requests, propagates via response header, and emits structured log."""

    async def dispatch(self, request: Request, call_next):
        trace_id = request.headers.get("X-Request-ID") or request.headers.get("X-Trace-ID") or str(uuid.uuid4())
        token = trace_id_ctx.set(trace_id)
        start_time = time.perf_counter()

        try:
            response: Response = await call_next(request)
            duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
            response.headers["X-Request-ID"] = trace_id

            if request.url.path not in ("/health", "/api/health", "/health/live", "/ready", "/live", "/health/ready"):
                logger.info(
                    f"{request.method} {request.url.path} {response.status_code} ({duration_ms}ms)",
                    extra={"extra_data": {
                        "http_method": request.method,
                        "http_path": request.url.path,
                        "status_code": response.status_code,
                        "duration_ms": duration_ms,
                    }},
                )
            return response
        finally:
            trace_id_ctx.reset(token)


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

    # 5. Start AI Pipeline Orchestrator (with free-tier rate pacing & model matrix)
    from backend.app.pipeline.orchestrator import get_pipeline_orchestrator
    orchestrator = get_pipeline_orchestrator()
    await orchestrator.start()

    # 6. Start Background Ingestion Supervisor
    supervisor = get_ingestion_supervisor()
    await supervisor.start()

    # 7. Start Automated 30-Minute AI Summary Scheduler
    from backend.app.services.summary_scheduler import get_summary_scheduler
    summary_scheduler = get_summary_scheduler()
    await summary_scheduler.start()

    logger.info("Application startup sequence completed successfully.")
    yield

    # Shutdown sequence
    logger.info("Initiating graceful shutdown sequence...")
    await summary_scheduler.stop()
    await supervisor.stop()
    await orchestrator.stop()
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
        expose_headers=["*"],
    )

    # Request Tracing Middleware
    application.add_middleware(RequestTracingMiddleware)

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
    application.include_router(embeddings_router)
    application.include_router(summary_router)
    application.include_router(public_router)

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
