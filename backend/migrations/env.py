import asyncio
import os
import sys
from logging.config import fileConfig
from typing import Optional

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config
from alembic import context

# Ensure application root is in sys.path
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from backend.app.config import get_settings
from backend.app.db.models import Base

# Alembic Config object
config = context.config

# Interpret the config file for Python logging.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def get_url() -> str:
    """Dynamically obtain database URL from application settings, avoiding hardcoded secrets."""
    cmd_url = config.get_main_option("sqlalchemy.url")
    if cmd_url and not cmd_url.startswith("postgresql+asyncpg://postgres:postgres_password@localhost"):
        return cmd_url
    try:
        settings = get_settings()
        return settings.DATABASE_URL
    except Exception:
        return cmd_url or "postgresql+asyncpg://postgres:postgres_password@localhost:5432/osint"


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode without an active DB connection."""
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Execute migrations within an active synchronous or adapted connection."""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Create an AsyncEngine and associate a connection with the migration context."""
    url = get_url()
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = url

    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode, safely adapting whether an event loop is already active."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(asyncio.run, run_async_migrations())
            future.result()
    else:
        asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
