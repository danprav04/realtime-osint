---
name: database-migrations
description: >-
  Operational guide and lifecycle protocols for creating, inspecting, applying, rolling back, and deploying
  versioned database schema migrations across development and production environments in Realtime OSINT.
  Use whenever modifying database models, adding tables or columns, executing schema updates, or deploying migrations.
---

# Database Migrations & Schema Lifecycle Guide

This document establishes the architecture, conventions, and operational workflows for schema migrations in the Real-Time OSINT Gathering Service.

---

## 1. Architecture Overview

Database persistence is powered by **PostgreSQL 16** (production) with **SQLite/aiosqlite** (local development and offline fallback). Schema lifecycle is managed via **Alembic** adapted for SQLAlchemy 2.0 async engines (`asyncpg` and `aiosqlite`).

### Key Files & Locations
- **`alembic.ini`**: Root and backend configuration (`backend/alembic.ini` and `alembic.ini`).
- **`backend/migrations/`**: Alembic environment and migration versions.
  - **`env.py`**: Dynamic async migration runner. Reads `DATABASE_URL` directly from application settings (preventing hardcoded secrets in config files) and supports `render_as_batch=True` for SQLite compatibility.
  - **`script.py.mako`**: Standard revision template.
  - **`versions/`**: Chronologically sorted, versioned migration scripts (e.g. `0001_initial_schema.py`).
- **`backend/app/db/migrator.py`**: Programmatic migration controller and CLI tool. Handles legacy database auto-stamping, status inspection, and safe upgrades.
- **`run_migrations.py`**: Root-level convenience CLI runner (`python run_migrations.py`).
- **`deploy.ps1`**: Automated deployment script with integrated remote migration execution.

---

## 2. Smart Baseline Auto-Stamping

The Realtime OSINT production database originally initialized its schema using `Base.metadata.create_all`. When introducing Alembic migrations to an existing database, running an initial migration blindly would crash with `relation "themes" already exists`.

To solve this safely and idempotently:
1. When `run_migrations("head")` is invoked, [`migrator.py`](file:///c:/Users/Daniel/Documents/GitHub/realtime%20osint/backend/app/db/migrator.py) first inspects the database.
2. If the `alembic_version` table is missing, but core application tables (`themes`, `events`, `sources`) already exist, it detects a **legacy unversioned database**.
3. It automatically **stamps** the baseline revision (`0001_initial_schema`) without executing DDL, creating the version tracking table.
4. Subsequent migrations (e.g., `0002_...`) apply sequentially without collisions.
5. If the database is completely empty (fresh install), it executes `0001_initial_schema` directly to build all tables from scratch.

---

## 3. Migration Commands & CLI Usage

### Local Convenience Runner (`run_migrations.py`)

```powershell
# 1. Apply all pending migrations (upgrade to head)
python run_migrations.py

# 2. Check current schema revision and migration status
python run_migrations.py status

# 3. Roll back the most recent migration (-1 revision)
python run_migrations.py downgrade

# 4. Stamp database version without executing DDL
python run_migrations.py stamp head

# 5. Run against a specific database URL override
python run_migrations.py status --url "sqlite+aiosqlite:///local.db"
```

### Alembic Standard CLI

```powershell
# Check current revision
alembic current

# Show migration history
alembic history --verbose

# Apply pending migrations
alembic upgrade head

# Roll back one revision
alembic downgrade -1
```

---

## 4. Creating New Migrations

Whenever SQLAlchemy models in [`backend/app/db/models.py`](file:///c:/Users/Daniel/Documents/GitHub/realtime%20osint/backend/app/db/models.py) are modified (new tables, new columns, altered constraints, new indexes):

### Step 1: Generate the Migration Script
Run:
```powershell
alembic revision -m "add_custom_field_to_events"
```
Or create a new file in `backend/migrations/versions/` following the sorted naming convention:
`backend/migrations/versions/0002_add_custom_field_to_events.py`

### Step 2: Define `upgrade()` and `downgrade()`
Ensure both directions are explicitly defined and reversible:
```python
"""add_custom_field_to_events

Revision ID: 0002_add_custom_field
Revises: 0001_initial_schema
Create Date: 2026-10-16 ...
"""
from alembic import op
import sqlalchemy as sa

revision = "0002_add_custom_field"
down_revision = "0001_initial_schema"

def upgrade() -> None:
    op.add_column("events", sa.Column("custom_field", sa.String(length=100), nullable=True))

def downgrade() -> None:
    op.drop_column("events", "custom_field")
```

### Step 3: Verify Locally with the Migration Test Suite
```powershell
pytest backend/tests/test_migrations.py -v
```

---

## 5. Deployment Integration (`deploy.ps1`)

Database migrations are integrated directly into the automated production deployment script [`deploy.ps1`](file:///c:/Users/Daniel/Documents/GitHub/realtime%20osint/deploy.ps1).

### Supported Workflows:

1. **Full Deployment with Automated Migration**:
   ```powershell
   .\deploy.ps1
   # Reconciles backend/frontend containers and applies pending migrations automatically.
   ```

2. **Backend-Only Deploy with Automated Migration**:
   ```powershell
   .\deploy.ps1 -Service backend
   # Rebuilds and deploys backend container, then executes migrations.
   ```

3. **Migrate-Only (Zero Downtime Schema Update)**:
   ```powershell
   .\deploy.ps1 -Service migrate
   # Bypasses image building and executes migrations on the live production backend immediately.
   ```

4. **Bypass Migration (Optional)**:
   ```powershell
   .\deploy.ps1 -SkipMigration
   # Deploys code without triggering the migration runner step.
   ```

---

## 6. Zero-Downtime & Safety Rules

> [!IMPORTANT]
> The production server (`ubuntu@production`) runs live event ingestion continuously. Always follow non-blocking schema evolution principles:

1. **Non-Destructive Additions First**:
   - New columns must be `nullable=True` or have a `server_default`. Never add a non-null column without a default value to an active table with millions of rows.
2. **Never Drop Columns In-Flight**:
   - Deploy code that stops reading/writing the column first, then drop the column in a subsequent migration.
3. **Concurrent Indexes**:
   - On massive tables, create indexes concurrently to prevent read/write locks.
4. **Dialect Compatibility**:
   - Ensure DDL works across both PostgreSQL 16 and SQLite fallback (`render_as_batch=True`).
5. **No Secrets in Migration Scripts**:
   - Never hardcode database connection strings, credentials, or keys in migration files or `alembic.ini`. Rely on `backend.app.config.get_settings()`.
