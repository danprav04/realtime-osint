import asyncio
import json
from datetime import datetime, timezone
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.app.api.health import router as health_router
from backend.app.config import get_settings
from backend.app.db.models import Alert, Base, Event, Source, SystemSettings, Theme
from backend.app.db.seed import seed_initial_data, DEFAULT_SOURCES_DATA, DEFAULT_SETTINGS_DATA
from backend.app.db.session import check_db_health
from backend.app.main import app
from backend.app.services.redis_bus import RedisBus, get_redis_bus


import pytest_asyncio

# Fixture for isolated test database engine
@pytest_asyncio.fixture
async def async_engine():
    # Use SQLite with foreign keys enabled via PRAGMA
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.execute(text("PRAGMA foreign_keys = ON;"))
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def session_factory(async_engine):
    return async_sessionmaker(
        bind=async_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )


# ==============================================================================
# CHALLENGE 1: Foreign Key Constraints & Relationships
# ==============================================================================


@pytest.mark.asyncio
async def test_fk_source_requires_valid_theme(session_factory):
    """Verify that creating a Source with non-existent theme_id fails FK constraint."""
    async with session_factory() as session:
        # Enable PRAGMA foreign_keys on SQLite connection explicitly
        await session.execute(text("PRAGMA foreign_keys = ON;"))
        invalid_source = Source(
            theme_id=999999,  # Non-existent theme
            name="Orphan Source",
            source_type="rss",
            config_json={"url": "https://example.com/feed"},
        )
        session.add(invalid_source)
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()


@pytest.mark.asyncio
async def test_fk_event_requires_valid_theme(session_factory):
    """Verify that creating an Event with non-existent theme_id fails FK constraint."""
    async with session_factory() as session:
        await session.execute(text("PRAGMA foreign_keys = ON;"))
        now = datetime.now(timezone.utc)
        invalid_event = Event(
            theme_id=999999,  # Non-existent theme
            source_type="rss",
            source_name="Unknown",
            external_id="ext-orphan-1",
            title="Orphan Event",
            raw_text="No theme exists.",
            published_at=now,
        )
        session.add(invalid_event)
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()


@pytest.mark.asyncio
async def test_fk_alert_requires_valid_event(session_factory):
    """Verify that creating an Alert with non-existent event_id fails FK constraint."""
    async with session_factory() as session:
        await session.execute(text("PRAGMA foreign_keys = ON;"))
        invalid_alert = Alert(
            event_id="non-existent-event-uuid",
            importance_score=9,
            channel="telegram",
            status="sent",
        )
        session.add(invalid_alert)
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()


@pytest.mark.asyncio
async def test_theme_cascade_delete_sources_and_events(session_factory):
    """Verify cascade delete: Deleting Theme deletes its Sources and Events."""
    async with session_factory() as session:
        await session.execute(text("PRAGMA foreign_keys = ON;"))
        theme = Theme(
            name="Theme for Cascade Test",
            scoring_prompt="Evaluate tactical significance.",
        )
        session.add(theme)
        await session.flush()

        source = Source(
            theme_id=theme.id,
            name="Cascade Source",
            source_type="rss",
            config_json={"url": "https://cascade.org/feed"},
        )
        session.add(source)
        await session.flush()

        now = datetime.now(timezone.utc)
        event = Event(
            theme_id=theme.id,
            source_id=source.id,
            source_type="rss",
            source_name="Cascade Source",
            external_id="cascade-evt-1",
            title="Event to be Cascaded",
            raw_text="Will be deleted on theme cascade.",
            published_at=now,
        )
        session.add(event)
        await session.flush()

        alert = Alert(
            event_id=event.id,
            importance_score=9,
            channel="telegram",
        )
        session.add(alert)
        await session.commit()

        # Delete Theme
        await session.delete(theme)
        await session.commit()

        # Verify Sources, Events, and Alerts are all deleted
        sources_remaining = (
            await session.execute(select(Source).where(Source.theme_id == theme.id))
        ).scalars().all()
        assert len(sources_remaining) == 0

        events_remaining = (
            await session.execute(select(Event).where(Event.theme_id == theme.id))
        ).scalars().all()
        assert len(events_remaining) == 0

        alerts_remaining = (
            await session.execute(select(Alert).where(Alert.event_id == event.id))
        ).scalars().all()
        assert len(alerts_remaining) == 0


@pytest.mark.asyncio
async def test_source_deletion_sets_event_source_id_null(session_factory):
    """Verify that deleting a Source sets event.source_id to NULL (ON DELETE SET NULL)."""
    async with session_factory() as session:
        await session.execute(text("PRAGMA foreign_keys = ON;"))
        theme = Theme(name="Theme For Set Null Test", scoring_prompt="Prompt")
        session.add(theme)
        await session.flush()

        source = Source(
            theme_id=theme.id,
            name="Source to Delete",
            source_type="rss",
            config_json={"url": "https://delete.org/rss"},
        )
        session.add(source)
        await session.flush()

        now = datetime.now(timezone.utc)
        event = Event(
            theme_id=theme.id,
            source_id=source.id,
            source_type="rss",
            source_name="Source to Delete",
            external_id="evt-set-null-1",
            title="Event Survives Source",
            raw_text="Event must survive when source is deleted.",
            published_at=now,
        )
        session.add(event)
        await session.commit()

        # Delete Source
        await session.delete(source)
        await session.commit()

        # Verify Event still exists, but source_id is NULL
        refreshed_event = (
            await session.execute(select(Event).where(Event.id == event.id))
        ).scalars().first()
        assert refreshed_event is not None
        assert refreshed_event.source_id is None


@pytest.mark.asyncio
async def test_event_unique_constraint_theme_and_external_id(session_factory):
    """Verify uq_events_theme_external_id prevents duplicate events per theme."""
    async with session_factory() as session:
        theme = Theme(name="Unique Constraint Theme", scoring_prompt="Prompt")
        session.add(theme)
        await session.flush()

        now = datetime.now(timezone.utc)
        evt1 = Event(
            theme_id=theme.id,
            source_type="rss",
            source_name="Source A",
            external_id="duplicate-ext-id",
            title="First Event",
            raw_text="First occurrence",
            published_at=now,
        )
        session.add(evt1)
        await session.commit()

        # Attempt to insert same external_id for same theme
        evt2 = Event(
            theme_id=theme.id,
            source_type="rss",
            source_name="Source A",
            external_id="duplicate-ext-id",
            title="Duplicate Event",
            raw_text="Duplicate occurrence",
            published_at=now,
        )
        session.add(evt2)
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()


# ==============================================================================
# CHALLENGE 2: JSON Serialization & Round-Trip Deserialization
# ==============================================================================


@pytest.mark.asyncio
async def test_json_roundtrip_complex_nested_structures(session_factory):
    """
    Verify complex nested JSON structures, coordinates, unicode (Persian/Arabic),
    and bounding boxes round-trip perfectly through database storage and .to_dict().
    """
    async with session_factory() as session:
        # Complex Theme with Unicode/Farsi/Arabic keywords & entities
        farsi_keywords = ["طهران", "تنگه هرمز", "سپاه پاسداران", "موشک هایپرسونیک"]
        arabic_entities = ["الحرس الثوري", "الخليج العربي", "باب المندب"]
        theme = Theme(
            name="Middle East Persian & Arabic Theme",
            description="نظارت اطلاعاتی و تحلیل وقایع",
            keywords=farsi_keywords,
            entities=arabic_entities,
            scoring_prompt="Score tactical events from 1 to 10 in Persian Gulf.",
            is_active=True,
            alert_threshold=8,
        )
        session.add(theme)
        await session.flush()

        # Complex Source with AIS Bounding Box & nested config
        ais_config = {
            "bbox": [25.123456, 54.987654, 27.876543, 57.123456],
            "zone_name": "Strait of Hormuz - Sector 4",
            "provider": "aisstream",
            "filters": {
                "mmsi_whitelist": [123456789, 987654321],
                "ship_types": ["tanker", "cargo", "military"],
                "speed_knots_min": 0.5,
            },
            "retry_policy": {"max_retries": 5, "backoff_factor": 1.5},
            "enabled_channels": ["ch16", "ch70"],
        }
        source = Source(
            theme_id=theme.id,
            name="AIS Precision Tracker",
            source_type="maritime_ais",
            config_json=ais_config,
            polling_interval_sec=120,
        )
        session.add(source)
        await session.flush()

        # Complex Event with coordinates, deep metadata, and translation
        location_data = {
            "lat": 26.5667,
            "lon": 56.25,
            "name": "Strait of Hormuz Chokepoint",
            "country": "OM",
            "accuracy_meters": 15.5,
            "sub_regions": ["Sector North", "Sector South"],
        }
        complex_metadata = {
            "raw_headers": {"User-Agent": "OSINT-Bot/1.0", "X-Feed-Type": "Atom"},
            "telemetry": {
                "signal_strength_dbm": -82.4,
                "confidence_score": 0.985,
                "multi_source_ids": ["src_01", "src_02", "src_03"],
            },
            "farsi_excerpt": "نیروهای دریایی سپاه یک فروند نفتکش را توقیف کردند",
            "tags": ["naval", "kinetic", "tanker_seizure"],
        }
        now = datetime.now(timezone.utc)
        event = Event(
            theme_id=theme.id,
            source_id=source.id,
            source_type=source.source_type,
            source_name=source.name,
            external_id="complex-evt-999",
            title="Naval Seizure in Strait of Hormuz",
            raw_text="IRGC patrol crafts intercepted commercial tanker.",
            translated_text="IRGC naval forces seized an oil tanker.",
            language="fa",
            location_json=location_data,
            metadata_json=complex_metadata,
            importance_score=9,
            score_rationale="Kinetic commercial vessel seizure in vital chokepoint.",
            summary="IRGC seized oil tanker in the Strait of Hormuz.",
            headline="Tanker Seized in Strait of Hormuz",
            entities_json=["IRGC Navy", "Commercial Tanker", "Strait of Hormuz"],
            published_at=now,
        )
        session.add(event)

        # Complex System Settings
        settings_data = {
            "pipeline": {
                "stage1": {"provider": "groq", "model": "openai/gpt-oss-120b", "temp": 0.1},
                "stage2": {"provider": "gemini", "model": "gemini-2.0-flash", "temp": 0.2},
            },
            "geo_fences": [
                {"name": "Hormuz", "bounds": [25.0, 54.0, 27.5, 57.5]},
                {"name": "Bab el-Mandeb", "bounds": [12.0, 43.0, 14.0, 44.5]},
            ],
            "debug_flags": {"log_payloads": True, "dry_run": False},
        }
        setting = SystemSettings(key="complex_config", value_json=settings_data)
        session.add(setting)

        await session.commit()

        # Query back in a clean query
        q_theme = (await session.execute(select(Theme).where(Theme.id == theme.id))).scalars().first()
        q_src = (await session.execute(select(Source).where(Source.id == source.id))).scalars().first()
        q_evt = (await session.execute(select(Event).where(Event.id == event.id))).scalars().first()
        q_set = (await session.execute(select(SystemSettings).where(SystemSettings.key == "complex_config"))).scalars().first()

        # 1. Assert DB loaded exact JSON structures
        assert q_theme.keywords == farsi_keywords
        assert q_theme.entities == arabic_entities
        assert q_src.config_json["bbox"] == [25.123456, 54.987654, 27.876543, 57.123456]
        assert q_src.config_json["filters"]["ship_types"] == ["tanker", "cargo", "military"]
        assert q_evt.location_json["lat"] == 26.5667
        assert q_evt.location_json["accuracy_meters"] == 15.5
        assert q_evt.metadata_json["telemetry"]["confidence_score"] == 0.985
        assert q_evt.metadata_json["farsi_excerpt"] == "نیروهای دریایی سپاه یک فروند نفتکش را توقیف کردند"
        assert q_set.value_json["geo_fences"][1]["name"] == "Bab el-Mandeb"

        # 2. Assert .to_dict() serialization produces valid standard JSON
        theme_json = json.dumps(q_theme.to_dict())
        src_json = json.dumps(q_src.to_dict())
        evt_json = json.dumps(q_evt.to_dict())
        set_json = json.dumps(q_set.to_dict())

        # 3. Assert deserialization from JSON string restores identical values
        loaded_evt = json.loads(evt_json)
        assert loaded_evt["location_json"]["name"] == "Strait of Hormuz Chokepoint"
        assert loaded_evt["metadata_json"]["farsi_excerpt"] == "نیروهای دریایی سپاه یک فروند نفتکش را توقیف کردند"
        assert loaded_evt["entities_json"] == ["IRGC Navy", "Commercial Tanker", "Strait of Hormuz"]
        assert loaded_evt["importance_score"] == 9

        loaded_src = json.loads(src_json)
        assert loaded_src["config_json"]["bbox"] == [25.123456, 54.987654, 27.876543, 57.123456]


# ==============================================================================
# CHALLENGE 3: Idempotency of seed_initial_data (5 Consecutive Executions)
# ==============================================================================


@pytest.mark.asyncio
async def test_seed_initial_data_5x_idempotency(session_factory):
    """
    Stress-test seed_initial_data by executing it 5 times consecutively.
    Must produce exactly 1 active theme, 7 sources, and 3 settings,
    with zero duplicate keys, zero extra rows, and zero integrity errors.
    """
    async with session_factory() as session:
        # Run 1: Should seed initial records
        run1 = await seed_initial_data(session)
        assert run1 == {"themes": 1, "sources": len(DEFAULT_SOURCES_DATA), "settings": len(DEFAULT_SETTINGS_DATA)}

        # Runs 2 through 5: Must be completely no-op (0 created)
        for i in range(2, 6):
            run_i = await seed_initial_data(session)
            assert run_i == {"themes": 0, "sources": 0, "settings": 0}, f"Run {i} failed idempotency: {run_i}"

        # Verify database totals
        all_themes = (await session.execute(select(Theme))).scalars().all()
        assert len(all_themes) == 1
        active_theme = all_themes[0]
        assert active_theme.name == "US-Iran conflict"
        assert active_theme.is_active is True

        all_sources = (await session.execute(select(Source))).scalars().all()
        assert len(all_sources) == len(DEFAULT_SOURCES_DATA)
        for src in all_sources:
            assert src.theme_id == active_theme.id

        # Verify source types
        source_types = [s.source_type for s in all_sources]
        assert "rss" in source_types
        assert "telegram" in source_types
        assert "reddit" in source_types
        assert "maritime_ais" in source_types

        all_settings = (await session.execute(select(SystemSettings))).scalars().all()
        assert len(all_settings) == len(DEFAULT_SETTINGS_DATA)
        setting_keys = {s.key for s in all_settings}
        assert {"ai_pipeline_stages", "notification_settings", "system_general"}.issubset(setting_keys)


# ==============================================================================
# CHALLENGE 4: /health/ready Unhealthy / Failure Behavior
# ==============================================================================


@pytest.mark.asyncio
async def test_health_ready_db_failure_behavior(monkeypatch):
    """Verify that when database health check fails, /health/ready returns 503 Service Unavailable."""
    async def mock_unhealthy_db():
        return {
            "status": "unhealthy",
            "dialect": "postgresql",
            "error": "connection refused: host unreachable at port 5432",
            "latency_ms": 105.2,
        }

    monkeypatch.setattr("backend.app.api.health.check_db_health", mock_unhealthy_db)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health/ready")
        assert resp.status_code == 503
        data = resp.json()
        assert data["detail"] == "Service not ready"


@pytest.mark.asyncio
async def test_health_ready_bus_failure_behavior(monkeypatch):
    """
    Verify /health/ready checks event bus health and returns 503 when the bus is unhealthy.
    """
    async def mock_healthy_db():
        return {"status": "healthy", "dialect": "sqlite", "latency_ms": 1.2}

    async def mock_unhealthy_bus(self):
        return {
            "status": "unhealthy",
            "is_connected": False,
            "fallback_mode": False,
            "error": "Redis connection timed out (host unreachable: 6379)",
            "subscribed_channels": [],
        }

    monkeypatch.setattr("backend.app.api.health.check_db_health", mock_healthy_db)
    monkeypatch.setattr(RedisBus, "check_health", mock_unhealthy_bus)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        ready_resp = await client.get("/health/ready")
        assert ready_resp.status_code == 503
        data = ready_resp.json()
        assert data["detail"] == "Service not ready"


@pytest.mark.asyncio
async def test_health_payload_unhealthy_redis_logic_bug(monkeypatch):
    """
    Verify that when Redis is unhealthy, overall status in build_health_payload is 'unhealthy'.
    """
    from backend.app.api.health import build_health_payload

    async def mock_healthy_db():
        return {"status": "healthy", "dialect": "sqlite", "latency_ms": 1.2}

    async def mock_unhealthy_bus(self):
        return {
            "status": "unhealthy",
            "is_connected": False,
            "fallback_mode": False,
            "error": "Redis fatal error",
            "subscribed_channels": [],
        }

    monkeypatch.setattr("backend.app.api.health.check_db_health", mock_healthy_db)
    monkeypatch.setattr(RedisBus, "check_health", mock_unhealthy_bus)

    payload = await build_health_payload()
    assert payload["redis"]["status"] == "unhealthy"
    assert payload["status"] == "unhealthy"


@pytest.mark.asyncio
async def test_seed_initial_data_5x_multi_session(async_engine):
    """
    Verify seed_initial_data idempotency across 5 distinct database sessions.
    Simulates 5 separate application bootups.
    """
    factory = async_sessionmaker(
        bind=async_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    for i in range(1, 6):
        async with factory() as session:
            counts = await seed_initial_data(session)
            if i == 1:
                assert counts["themes"] == 1
                assert counts["sources"] == len(DEFAULT_SOURCES_DATA)
                assert counts["settings"] == len(DEFAULT_SETTINGS_DATA)
            else:
                assert counts == {"themes": 0, "sources": 0, "settings": 0}, f"Run {i} failed: {counts}"

    # Final verification
    async with factory() as session:
        themes = (await session.execute(select(Theme))).scalars().all()
        assert len(themes) == 1
        assert themes[0].name == "US-Iran conflict"
        sources = (await session.execute(select(Source))).scalars().all()
        assert len(sources) == len(DEFAULT_SOURCES_DATA)
        settings = (await session.execute(select(SystemSettings))).scalars().all()
        assert len(settings) == len(DEFAULT_SETTINGS_DATA)


@pytest.mark.asyncio
async def test_sqlite_engine_connect_lacks_fk_enforcement():
    """
    Verify that SQLite connections enforce foreign key constraints
    via PRAGMA foreign_keys = ON, preventing orphaned records.
    """
    # Create SQLite engine matching backend/app/db/session.py configuration
    test_engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},  # exactly as in session.py line 30
    )
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        orphan_source = Source(
            theme_id=99999,  # Non-existent
            name="Orphan",
            source_type="rss",
            config_json={},
        )
        session.add(orphan_source)
        # Because PRAGMA foreign_keys=ON is enforced, this raises IntegrityError!
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()

    await test_engine.dispose()

