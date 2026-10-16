"""
backend/app/db/migrator.py - Programmatic & CLI Database Migration Controller

Handles:
1. Applying forward migrations (upgrade).
2. Rolling back migrations (downgrade).
3. Inspecting legacy unversioned databases (e.g., initialized via Base.metadata.create_all)
   and automatically stamping the baseline revision ('0001_initial_schema') so that
   existing data is preserved without table-already-exists collisions.
4. Inspection of current schema revision and pending migrations.
"""

import argparse
import asyncio
import logging
import os
import sys
from typing import Any, Dict, Optional, Tuple

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from backend.app.config import get_settings
from backend.app.db.session import get_engine, get_engine_args

logger = logging.getLogger("osint.db.migrator")


def _run_async(coro):
    """Safely execute an async coroutine whether an event loop is currently running or not."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            return executor.submit(asyncio.run, coro).result()
    else:
        return asyncio.run(coro)


def find_alembic_ini() -> str:
    """Locate alembic.ini across known repository paths."""
    candidates = [
        os.path.join(os.getcwd(), "backend", "alembic.ini"),
        os.path.join(os.getcwd(), "alembic.ini"),
        os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "alembic.ini")),
        os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "alembic.ini")),
        "/app/backend/alembic.ini",
        "/app/alembic.ini",
    ]
    for c in candidates:
        if os.path.isfile(c):
            return os.path.abspath(c)
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "alembic.ini"))


def get_alembic_config(override_url: Optional[str] = None) -> Config:
    """Build and configure an Alembic Config instance dynamically."""
    ini_path = find_alembic_ini()
    cfg = Config(ini_path)

    migrations_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "migrations"))
    if not os.path.isdir(migrations_dir):
        migrations_dir = "/app/backend/migrations"

    cfg.set_main_option("script_location", migrations_dir)

    settings = get_settings()
    db_url = override_url or settings.DATABASE_URL
    cfg.set_main_option("sqlalchemy.url", db_url)

    return cfg


async def inspect_db_state(engine: Optional[AsyncEngine] = None) -> Tuple[Optional[str], bool, list[str]]:
    """
    Inspect the database and return:
    (current_alembic_version, has_legacy_tables, table_names)
    """
    eng = engine or get_engine()
    current_rev: Optional[str] = None
    table_names: list[str] = []
    has_legacy_tables = False

    def _sync_inspect(connection):
        nonlocal current_rev, table_names, has_legacy_tables
        insp = inspect(connection)
        table_names = insp.get_table_names()

        # Check alembic_version table directly
        if "alembic_version" in table_names:
            try:
                row = connection.execute(text("SELECT version_num FROM alembic_version LIMIT 1")).fetchone()
                if row and row[0]:
                    current_rev = str(row[0]).strip()
            except Exception as e:
                logger.debug(f"Direct alembic_version query failed: {e}")

        # Check for core application tables
        core_tables = {"themes", "events", "sources"}
        if core_tables.intersection(table_names):
            has_legacy_tables = True

    async with eng.connect() as conn:
        await conn.run_sync(_sync_inspect)

    return current_rev, has_legacy_tables, table_names


def run_migrations(target_revision: str = "head", override_url: Optional[str] = None) -> Dict[str, Any]:
    """
    Synchronously execute migration lifecycle:
    1. Inspects existing tables.
    2. If legacy schema exists without alembic_version, stamps baseline '0001_initial_schema'.
    3. Upgrades to target_revision (defaults to 'head').
    """
    cfg = get_alembic_config(override_url)
    script = ScriptDirectory.from_config(cfg)
    head_rev = script.get_current_head()

    settings = get_settings()
    active_url = override_url or settings.DATABASE_URL

    temp_engine = create_async_engine(active_url, **get_engine_args(active_url))

    async def _inspect():
        try:
            return await inspect_db_state(temp_engine)
        finally:
            await temp_engine.dispose()

    try:
        current_rev, has_legacy_tables, table_names = _run_async(_inspect())
    except Exception as exc:
        logger.warning(f"Inspection connection failed ({exc}). Proceeding with direct upgrade.")
        current_rev, has_legacy_tables, table_names = None, False, []

    logger.info(f"Database inspection: current_rev={current_rev}, has_legacy_tables={has_legacy_tables}, tables_count={len(table_names)}")

    # Auto-stamp baseline if legacy unversioned tables are present
    if current_rev is None and has_legacy_tables:
        if "historical_rollups" in table_names:
            logger.info(f"Unversioned schema with latest tables detected. Stamping head revision '{head_rev}'...")
            command.stamp(cfg, head_rev)
            current_rev = head_rev
        else:
            logger.info(f"Unversioned schema with baseline tables detected. Stamping baseline revision '{baseline_rev}'...")
            command.stamp(cfg, baseline_rev)
            current_rev = baseline_rev

    # If already at target, return early
    if current_rev == head_rev and target_revision == "head":
        logger.info(f"Database schema is already at head revision: {current_rev}")
        return {
            "status": "already_up_to_date",
            "current_revision": current_rev,
            "head_revision": head_rev,
        }

    # Run upgrade
    logger.info(f"Applying database migrations to revision: '{target_revision}'...")
    command.upgrade(cfg, target_revision)

    # Re-verify current revision
    temp_engine2 = create_async_engine(active_url, **get_engine_args(active_url))

    async def _get_new_rev():
        try:
            rev, _, _ = await inspect_db_state(temp_engine2)
            return rev
        finally:
            await temp_engine2.dispose()

    try:
        new_rev = _run_async(_get_new_rev())
    except Exception:
        new_rev = target_revision

    logger.info(f"Database migrations applied successfully. Current revision: {new_rev}")
    return {
        "status": "success",
        "previous_revision": current_rev,
        "current_revision": new_rev,
        "head_revision": head_rev,
    }


def rollback_migration(target_revision: str = "-1", override_url: Optional[str] = None) -> Dict[str, Any]:
    """Roll back database schema to target_revision (defaults to previous revision '-1')."""
    cfg = get_alembic_config(override_url)
    logger.warning(f"Rolling back database migration to '{target_revision}'...")
    command.downgrade(cfg, target_revision)
    return {"status": "success", "downgraded_to": target_revision}


def stamp_revision(revision: str = "head", override_url: Optional[str] = None) -> Dict[str, Any]:
    """Manually stamp database version table to specified revision."""
    cfg = get_alembic_config(override_url)
    logger.info(f"Stamping database schema version to '{revision}'...")
    command.stamp(cfg, revision)
    return {"status": "success", "stamped_revision": revision}


def get_migration_status(override_url: Optional[str] = None) -> Dict[str, Any]:
    """Get current and head revision information."""
    cfg = get_alembic_config(override_url)
    script = ScriptDirectory.from_config(cfg)
    head_rev = script.get_current_head()

    settings = get_settings()
    active_url = override_url or settings.DATABASE_URL
    temp_engine = create_async_engine(active_url, **get_engine_args(active_url))

    async def _inspect():
        try:
            return await inspect_db_state(temp_engine)
        finally:
            await temp_engine.dispose()

    current_rev, has_legacy_tables, table_names = _run_async(_inspect())

    return {
        "current_revision": current_rev,
        "head_revision": head_rev,
        "is_up_to_date": current_rev == head_rev and head_rev is not None,
        "has_legacy_tables": has_legacy_tables,
        "tables": table_names,
    }


def main():
    """CLI entrypoint for running migrations."""
    parser = argparse.ArgumentParser(description="Realtime OSINT Database Migration Utility")
    subparsers = parser.add_subparsers(dest="command", help="Available migration commands")

    p_up = subparsers.add_parser("upgrade", help="Apply database migrations")
    p_up.add_argument("--revision", "-r", default="head", help="Target revision (default: head)")
    p_up.add_argument("--url", default=None, help="Database connection URL override")

    p_down = subparsers.add_parser("downgrade", help="Roll back migrations")
    p_down.add_argument("--revision", "-r", default="-1", help="Target rollback revision (default: -1)")
    p_down.add_argument("--url", default=None, help="Database connection URL override")

    p_stamp = subparsers.add_parser("stamp", help="Stamp database with a revision without executing SQL")
    p_stamp.add_argument("--revision", "-r", default="head", help="Revision to stamp (default: head)")
    p_stamp.add_argument("--url", default=None, help="Database connection URL override")

    p_status = subparsers.add_parser("status", help="Show current migration status")
    p_status.add_argument("--url", default=None, help="Database connection URL override")

    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)-5.5s [%(name)s] %(message)s")

    cmd = args.command or "upgrade"
    revision_arg = getattr(args, "revision", "head")
    url_arg = getattr(args, "url", None)

    try:
        if cmd == "upgrade":
            res = run_migrations(target_revision=revision_arg, override_url=url_arg)
            print(f"[OK] Upgrade complete: {res}")
        elif cmd == "downgrade":
            res = rollback_migration(target_revision=revision_arg, override_url=url_arg)
            print(f"[OK] Downgrade complete: {res}")
        elif cmd == "stamp":
            res = stamp_revision(revision=revision_arg, override_url=url_arg)
            print(f"[OK] Stamp complete: {res}")
        elif cmd == "status":
            res = get_migration_status(override_url=url_arg)
            print("\nDatabase Migration Status:")
            print(f"  Current Revision : {res['current_revision']}")
            print(f"  Head Revision    : {res['head_revision']}")
            print(f"  Is Up To Date    : {res['is_up_to_date']}")
            print(f"  Table Count      : {len(res['tables'])}")
        sys.exit(0)
    except Exception as exc:
        logger.error(f"Migration error: {exc}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
