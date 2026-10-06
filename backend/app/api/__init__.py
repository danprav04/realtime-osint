"""API routers package for Real-time OSINT Gathering Service."""
from backend.app.api.health import router as health_router

__all__ = ["health_router"]
