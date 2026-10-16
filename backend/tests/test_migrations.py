"""
backend/tests/test_migrations.py - Unit & Integration Tests for Database Migrations

Tests:
1. Fresh database upgrade from empty state to head revision.
2. Idempotent re-run of upgrade when database is already up-to-date.
3. Legacy schema auto-stamping (handles databases initialized before Alembic was added).
4. Downgrade rollback and re-upgrade cycle.
5. Status inspection utility.
"""

import os
import pytest
import tempfile
from sqlalchemy.ext.asyncio import create_async_engine
from backend.app.db.models import Base
from backend.app.db.migrator import (
    run_migrations,
    rollback_migration,
    stamp_revision,
    get_migration_status,
)


@pytest.fixture
def temp_db_url():
    """Create a temporary SQLite database file and provide its async URL."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    url = f"sqlite+aiosqlite:///{path}"
    yield url
    if os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass


def test_fresh_database_migration(temp_db_url):
    """Verify that a fresh, empty database is successfully migrated to head."""
    # 1. Initial status before migration
    status_before = get_migration_status(override_url=temp_db_url)
    assert status_before["current_revision"] is None
    assert status_before["head_revision"] == "0001_initial_schema"
    assert status_before["is_up_to_date"] is False

    # 2. Run migrations
    result = run_migrations(target_revision="head", override_url=temp_db_url)
    assert result["status"] == "success"
    assert result["head_revision"] == "0001_initial_schema"

    # 3. Status after migration
    status_after = get_migration_status(override_url=temp_db_url)
    assert status_after["current_revision"] == "0001_initial_schema"
    assert status_after["is_up_to_date"] is True
    assert "events" in status_after["tables"]
    assert "themes" in status_after["tables"]
    assert "sources" in status_after["tables"]
    assert "alerts" in status_after["tables"]
    assert "event_embeddings" in status_after["tables"]
    assert "system_settings" in status_after["tables"]
    assert "alembic_version" in status_after["tables"]


def test_migration_idempotency(temp_db_url):
    """Verify that re-running upgrade on an up-to-date database is a safe no-op."""
    # First migration
    res1 = run_migrations(target_revision="head", override_url=temp_db_url)
    assert res1["status"] == "success"

    # Second migration run
    res2 = run_migrations(target_revision="head", override_url=temp_db_url)
    assert res2["status"] == "already_up_to_date"
    assert res2["current_revision"] == "0001_initial_schema"


@pytest.mark.asyncio
async def test_legacy_database_auto_stamping(temp_db_url):
    """
    Verify that an unversioned database with pre-existing tables
    (such as a production database initialized via Base.metadata.create_all)
    is automatically stamped with baseline revision '0001_initial_schema'.
    """
    # 1. Create tables directly via metadata.create_all (simulates legacy database)
    eng = create_async_engine(temp_db_url)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await eng.dispose()

    # 2. Status should report has_legacy_tables=True, current_revision=None
    status_before = get_migration_status(override_url=temp_db_url)
    assert status_before["current_revision"] is None
    assert status_before["has_legacy_tables"] is True
    assert "events" in status_before["tables"]

    # 3. Running upgrade should detect legacy schema, stamp baseline without colliding
    result = run_migrations(target_revision="head", override_url=temp_db_url)
    assert result["status"] == "already_up_to_date" or result["status"] == "success"
    assert result["current_revision"] == "0001_initial_schema"

    # 4. Status should now report fully versioned and up-to-date
    status_after = get_migration_status(override_url=temp_db_url)
    assert status_after["current_revision"] == "0001_initial_schema"
    assert status_after["is_up_to_date"] is True


def test_downgrade_and_reupgrade_cycle(temp_db_url):
    """Verify that downgrade successfully removes tables and can be re-upgraded."""
    # Upgrade to head
    run_migrations(target_revision="head", override_url=temp_db_url)

    # Downgrade to base
    down_res = rollback_migration(target_revision="base", override_url=temp_db_url)
    assert down_res["status"] == "success"

    # Status after downgrade
    status_down = get_migration_status(override_url=temp_db_url)
    assert status_down["current_revision"] is None
    assert "events" not in status_down["tables"]

    # Re-upgrade to head
    up_res = run_migrations(target_revision="head", override_url=temp_db_url)
    assert up_res["status"] == "success"
    status_reup = get_migration_status(override_url=temp_db_url)
    assert status_reup["is_up_to_date"] is True
    assert "events" in status_reup["tables"]
