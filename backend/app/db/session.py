import logging
import sqlite3
import time
from typing import AsyncGenerator, Dict, Any, Optional
from sqlalchemy import event, text
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from backend.app.config import get_settings
from backend.app.db.models import Base

logger = logging.getLogger("osint.db")

_engine: Optional[AsyncEngine] = None
_session_factory: Optional[async_sessionmaker[AsyncSession]] = None
_active_db_url: Optional[str] = None


@event.listens_for(Engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    """Enforce SQLite foreign key constraints on both sync and async SQLite connections."""
    is_sqlite = False
    if isinstance(dbapi_connection, sqlite3.Connection):
        is_sqlite = True
    elif hasattr(dbapi_connection, "_connection"):
        inner = dbapi_connection._connection
        if isinstance(inner, sqlite3.Connection) or (
            hasattr(inner, "_connection") and isinstance(inner._connection, sqlite3.Connection)
        ):
            is_sqlite = True
    elif "sqlite" in getattr(type(dbapi_connection), "__module__", ""):
        is_sqlite = True

    if is_sqlite:
        try:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON;")
            cursor.close()
        except Exception as exc:
            logger.warning(f"Failed to set PRAGMA foreign_keys on SQLite connection: {exc}")


def get_engine_args(url: str) -> Dict[str, Any]:
    """Configure engine parameters according to database dialect and resource limits."""
    settings = get_settings()
    engine_kwargs: Dict[str, Any] = {
        "echo": settings.DEBUG,
        "future": True,
    }

    if "sqlite" in url:
        engine_kwargs["connect_args"] = {"check_same_thread": False}
    else:
        # PostgreSQL / asyncpg connection pooling tuned for 1GB RAM budget
        engine_kwargs["pool_size"] = settings.DB_POOL_SIZE
        engine_kwargs["max_overflow"] = settings.DB_MAX_OVERFLOW
        engine_kwargs["pool_timeout"] = settings.DB_POOL_TIMEOUT
        engine_kwargs["pool_pre_ping"] = True

    return engine_kwargs


def get_engine() -> AsyncEngine:
    """Return the global async engine, creating it if necessary."""
    global _engine, _active_db_url
    if _engine is None:
        settings = get_settings()
        _active_db_url = settings.DATABASE_URL
        engine_kwargs = get_engine_args(_active_db_url)
        _engine = create_async_engine(_active_db_url, **engine_kwargs)
        logger.info(f"Initialized async SQLAlchemy engine with URL: {_active_db_url}")
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return the global async session factory."""
    global _session_factory
    if _session_factory is None:
        engine = get_engine()
        _session_factory = async_sessionmaker(
            bind=engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
            autocommit=False,
        )
    return _session_factory


# Alias for convenience
async_session_factory = get_session_factory


async def fallback_to_sqlite() -> AsyncEngine:
    """Fallback from unreachable PostgreSQL to SQLite async local storage."""
    global _engine, _session_factory, _active_db_url
    settings = get_settings()
    fallback_url = settings.DATABASE_URL_FALLBACK

    logger.warning(
        f"Primary database connection failed. Falling back to local SQLite: {fallback_url}"
    )

    if _engine is not None:
        await _engine.dispose()

    _active_db_url = fallback_url
    engine_kwargs = get_engine_args(fallback_url)
    _engine = create_async_engine(fallback_url, **engine_kwargs)
    _session_factory = async_sessionmaker(
        bind=_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
        autocommit=False,
    )
    return _engine


async def init_db() -> None:
    """
    Initialize database connection and verify connectivity.
    Applies versioned migrations (auto-stamping baseline on legacy databases)
    and falls back to SQLite if PostgreSQL is unavailable.
    """
    global _engine
    engine = get_engine()

    try:
        # Run versioned migrations (upgrades to head or auto-stamps existing legacy tables)
        from backend.app.db.migrator import run_migrations
        mig_result = run_migrations(target_revision="head")
        logger.info(f"Database schema verified/migrated successfully: {mig_result}")
    except Exception as exc:
        logger.warning(f"Versioned migration run encountered error ({exc}); attempting metadata fallback...")
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            logger.info("Database schema initialized successfully via metadata fallback.")
        except Exception as primary_exc:
            logger.error(f"Failed to initialize schema on primary database: {primary_exc}")
            # Trigger fallback to SQLite
            fallback_engine = await fallback_to_sqlite()
            async with fallback_engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            logger.info("Database schema initialized successfully on SQLite fallback.")


async def close_db() -> None:
    """Dispose of engine connections cleanly during application shutdown."""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None
        logger.info("Database engine disposed.")


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency for yielding database session with transaction management."""
    session_factory = get_session_factory()
    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


get_db_session = get_db


async def check_db_health() -> Dict[str, Any]:
    """Perform database ping query and report dialect, latency, and status."""
    engine = get_engine()
    start_time = time.perf_counter()
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        latency_ms = round((time.perf_counter() - start_time) * 1000, 2)
        return {
            "status": "healthy",
            "dialect": engine.dialect.name,
            "url": _active_db_url.split("@")[-1] if _active_db_url and "@" in _active_db_url else _active_db_url,
            "latency_ms": latency_ms,
        }
    except Exception as exc:
        latency_ms = round((time.perf_counter() - start_time) * 1000, 2)
        logger.warning(f"Database health check failed: {exc}")
        return {
            "status": "unhealthy",
            "dialect": engine.dialect.name if engine else "unknown",
            "error": str(exc),
            "latency_ms": latency_ms,
        }
