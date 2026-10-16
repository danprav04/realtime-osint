import asyncio
from datetime import datetime, timezone
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.app.auth import verify_admin_password, verify_api_key_string
from backend.app.config import Settings, get_settings
from backend.app.db.models import Alert, Base, Event, Source, SystemSettings, Theme
from backend.app.db.seed import seed_initial_data
from backend.app.db.session import check_db_health, init_db
from backend.app.main import app
from backend.app.services.redis_bus import (
    CHANNEL_CONFIG_RELOAD,
    CHANNEL_EVENTS_ALERT,
    CHANNEL_EVENTS_NEW,
    RedisBus,
)


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


# --- 1. Settings & Configuration Tests ---


def test_settings_defaults():
    settings = get_settings()
    assert settings.APP_NAME == "Real-time OSINT Gathering Service"
    assert settings.MEMORY_BUDGET_MB == 900
    assert settings.DEFAULT_ALERT_THRESHOLD == 7
    assert settings.API_KEY is not None
    assert "sqlite" in settings.DATABASE_URL or "postgres" in settings.DATABASE_URL


def test_settings_url_normalization():
    s1 = Settings(DATABASE_URL="postgres://user:pass@localhost:5432/testdb")
    assert s1.DATABASE_URL.startswith("postgresql+asyncpg://")

    s2 = Settings(DATABASE_URL="postgresql://user:pass@localhost:5432/testdb")
    assert s2.DATABASE_URL.startswith("postgresql+asyncpg://")


def test_settings_alias_choices(monkeypatch):
    # Test standard environment variables
    monkeypatch.setenv("API_KEY", "env_standard_key")
    monkeypatch.delenv("API_AUTH_SECRET", raising=False)
    monkeypatch.setenv("PORT", "8888")
    monkeypatch.delenv("HOST_PORT", raising=False)
    s_standard = Settings()
    assert s_standard.API_KEY == "env_standard_key"
    assert s_standard.PORT == 8888

    # Test alternative alias environment variables
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.setenv("API_AUTH_SECRET", "env_secret_alias")
    monkeypatch.delenv("PORT", raising=False)
    monkeypatch.setenv("HOST_PORT", "9999")
    s_alias = Settings()
    assert s_alias.API_KEY == "env_secret_alias"
    assert s_alias.PORT == 9999

    s3 = Settings(DATABASE_URL="sqlite+aiosqlite:///./test.db")
    assert s3.DATABASE_URL == "sqlite+aiosqlite:///./test.db"


# --- 2. Database Models Unit Tests ---


def test_theme_model_instantiation():
    theme = Theme(
        name="Test Conflict Theme",
        description="Monitoring test tensions",
        keywords=["missile", "radar", "navy"],
        entities=["Actor A", "Actor B"],
        scoring_prompt="Score 1-10 on tactical importance.",
        is_active=True,
        alert_threshold=8,
    )
    theme_dict = theme.to_dict()
    assert theme_dict["name"] == "Test Conflict Theme"
    assert theme_dict["alert_threshold"] == 8
    assert "missile" in theme_dict["keywords"]
    assert "Actor A" in theme_dict["entities"]
    assert theme_dict["is_active"] is True


def test_source_model_instantiation():
    source = Source(
        theme_id=1,
        name="Test Feed",
        source_type="rss",
        config_json={"url": "https://example.com/rss.xml"},
        is_enabled=True,
        polling_interval_sec=120,
    )
    src_dict = source.to_dict()
    assert src_dict["name"] == "Test Feed"
    assert src_dict["source_type"] == "rss"
    assert src_dict["config_json"]["url"] == "https://example.com/rss.xml"
    assert src_dict["polling_interval_sec"] == 120


def test_event_and_alert_model_instantiation():
    published_time = datetime.now(timezone.utc)
    event = Event(
        theme_id=1,
        source_id=1,
        source_type="rss",
        source_name="Test Source",
        external_id="ext-12345",
        title="Breaking Naval Event",
        raw_text="Patrol vessels intercepted commercial ship in waterway.",
        translated_text="Patrol vessels intercepted commercial ship in waterway.",
        language="en",
        importance_score=9,
        score_rationale="Kinetic interception of commercial vessel.",
        summary="Naval forces intercepted a tanker.",
        headline="Vessel Intercepted",
        entities_json=["Patrol Vessel", "Commercial Ship"],
        location_json={"lat": 26.5, "lon": 56.5, "name": "Strait"},
        published_at=published_time,
    )
    event_dict = event.to_dict()
    assert event_dict["importance_score"] == 9
    assert event_dict["external_id"] == "ext-12345"
    assert event_dict["location_json"]["name"] == "Strait"
    assert event_dict["title"] == "Breaking Naval Event"

    alert = Alert(
        event_id=event.id,
        importance_score=9,
        channel="telegram",
        status="sent",
    )
    alert_dict = alert.to_dict()
    assert alert_dict["event_id"] == event.id
    assert alert_dict["importance_score"] == 9
    assert alert_dict["channel"] == "telegram"
    assert alert_dict["status"] == "sent"


def test_system_settings_model():
    setting = SystemSettings(
        key="test_key",
        value_json={"enabled": True, "threshold": 5},
    )
    set_dict = setting.to_dict()
    assert set_dict["key"] == "test_key"
    assert set_dict["value_json"]["threshold"] == 5


# --- 3. Async Database Integration & CRUD Tests ---


@pytest.mark.asyncio
async def test_db_init_and_crud():
    # Use in-memory SQLite engine for isolated test
    test_engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(
        bind=test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async with session_maker() as session:
        # Create theme
        theme = Theme(
            name="Integration Theme",
            description="Integration test theme",
            keywords=["cyber", "defense"],
            entities=["Agency A"],
            scoring_prompt="Evaluate threat level.",
            is_active=True,
            alert_threshold=7,
        )
        session.add(theme)
        await session.commit()
        assert theme.id is not None

        # Create source
        source = Source(
            theme_id=theme.id,
            name="Integration RSS",
            source_type="rss",
            config_json={"url": "https://example.org/feed"},
            is_enabled=True,
            polling_interval_sec=180,
        )
        session.add(source)
        await session.commit()
        assert source.id is not None

        # Create event
        now = datetime.now(timezone.utc)
        event = Event(
            theme_id=theme.id,
            source_id=source.id,
            source_type=source.source_type,
            source_name=source.name,
            external_id="event-abc-1",
            title="Cyber Breach Detected",
            raw_text="Major infrastructure breach detected.",
            language="en",
            importance_score=8,
            score_rationale="Critical infrastructure affected.",
            summary="A cyber attack hit infrastructure.",
            headline="Cyber Attack",
            entities_json=["Agency A"],
            published_at=now,
        )
        session.add(event)
        await session.commit()
        assert event.id is not None

        # Verify query with relationship
        result = await session.execute(
            select(Theme).options(selectinload(Theme.events)).where(Theme.id == theme.id)
        )
        fetched_theme = result.scalars().first()
        assert fetched_theme is not None
        assert len(fetched_theme.sources) == 1
        assert fetched_theme.sources[0].name == "Integration RSS"
        assert len(fetched_theme.events) == 1
        assert fetched_theme.events[0].title == "Cyber Breach Detected"

    await test_engine.dispose()


@pytest.mark.asyncio
async def test_db_health_check():
    # Ensure standard db engine is initialized
    await init_db()
    health = await check_db_health()
    assert health["status"] == "healthy"
    assert health["dialect"] in ("sqlite", "postgresql")
    assert "latency_ms" in health


# --- 4. Database Seed Tests ---


@pytest.mark.asyncio
async def test_seed_initial_data_idempotency():
    test_engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(
        bind=test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    from backend.app.db.seed import DEFAULT_SOURCES_DATA, DEFAULT_SETTINGS_DATA

    async with session_maker() as session:
        # First seed execution: creates items
        counts_1 = await seed_initial_data(session)
        assert counts_1["themes"] == 1
        assert counts_1["sources"] == len(DEFAULT_SOURCES_DATA)
        assert counts_1["settings"] == len(DEFAULT_SETTINGS_DATA)

        # Verify theme properties
        res = await session.execute(
            select(Theme).where(Theme.name == "US-Iran conflict")
        )
        theme = res.scalars().first()
        assert theme is not None
        assert "Strait of Hormuz" in theme.keywords
        assert "IRGC" in theme.keywords
        assert "CENTCOM" in theme.keywords
        assert len(theme.sources) == len(DEFAULT_SOURCES_DATA)

        # Verify source types seeded
        source_types = {s.source_type for s in theme.sources}
        assert "rss" in source_types
        assert "telegram" in source_types
        assert "reddit" not in source_types
        assert "maritime_ais" in source_types

        # Verify system settings seeded
        st_res = await session.execute(
            select(SystemSettings).where(
                SystemSettings.key == "ai_pipeline_stages"
            )
        )
        ai_stages = st_res.scalars().first()
        assert ai_stages is not None
        assert "extraction_translation" in ai_stages.value_json
        assert "importance_scoring" in ai_stages.value_json

        # Second seed execution: must be idempotent (0 new items)
        counts_2 = await seed_initial_data(session)
        assert counts_2["themes"] == 0
        assert counts_2["sources"] == 0
        assert counts_2["settings"] == 0

    await test_engine.dispose()


# --- 5. Redis Bus & Pub/Sub Fallback Tests ---


@pytest.mark.asyncio
async def test_redis_bus_fallback_pubsub():
    # Create RedisBus pointing to offline address to test transparent fallback
    bus = RedisBus(redis_url="redis://127.0.0.1:59999/0")
    connected = await bus.connect()
    assert connected is False
    assert bus.is_fallback_mode is True

    received_events = []
    received_reloads = []

    async def on_event(channel, msg):
        received_events.append((channel, msg))

    async def on_reload(channel, msg):
        received_reloads.append((channel, msg))

    bus.subscribe(CHANNEL_EVENTS_NEW, on_event)
    bus.subscribe(CHANNEL_CONFIG_RELOAD, on_reload)

    # Publish to events channel
    event_payload = {
        "event_id": "test-123",
        "title": "Tactical Alert",
        "importance_score": 8,
    }
    await bus.publish(CHANNEL_EVENTS_NEW, event_payload)

    # Publish to reload channel
    reload_payload = {"action": "reload_theme", "theme_id": 1}
    await bus.publish(CHANNEL_CONFIG_RELOAD, reload_payload)

    # Allow asyncio loop to execute tasks
    await asyncio.sleep(0.05)

    assert len(received_events) == 1
    assert received_events[0][0] == CHANNEL_EVENTS_NEW
    assert received_events[0][1]["title"] == "Tactical Alert"

    assert len(received_reloads) == 1
    assert received_reloads[0][0] == CHANNEL_CONFIG_RELOAD
    assert received_reloads[0][1]["action"] == "reload_theme"

    # Test bus health report
    health = await bus.check_health()
    assert health["status"] in ("degraded", "healthy")
    assert health["fallback_mode"] is True

    await bus.disconnect()


# --- 6. Authentication Unit Tests ---


def test_auth_verification():
    settings = get_settings()
    configured_key = settings.API_KEY

    # Valid API key
    assert verify_api_key_string(configured_key) is True
    # Invalid API key
    assert verify_api_key_string("wrong-key-value") is False
    # None or empty
    assert verify_api_key_string(None) is False
    assert verify_api_key_string("") is False

    # Admin password
    assert verify_admin_password(settings.ADMIN_PASSWORD) is True
    assert verify_admin_password("incorrect-pass") is False


# --- 7. FastAPI Endpoints & Health Route Tests ---


@pytest.mark.asyncio
async def test_fastapi_root_and_health_endpoints():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Root endpoint
        root_resp = await client.get("/")
        assert root_resp.status_code == 200
        root_data = root_resp.json()
        assert root_data["status"] == "operational"

        # Liveness probe
        live_resp = await client.get("/health/live")
        assert live_resp.status_code == 200
        assert live_resp.json()["status"] == "alive"

        # Readiness probe
        ready_resp = await client.get("/health/ready")
        assert ready_resp.status_code == 200
        assert ready_resp.json()["status"] == "ready"

        # Health endpoint
        health_resp = await client.get("/health")
        assert health_resp.status_code in (200, 503)
        h_data = health_resp.json()
        assert "database" in h_data
        assert "redis" in h_data
        assert "memory" in h_data
        assert h_data["memory"]["budget_limit_mb"] == 900
        assert h_data["memory"]["budget_compliant"] is True

        # API health alias
        api_health_resp = await client.get("/api/health")
        assert api_health_resp.status_code == health_resp.status_code


@pytest.mark.asyncio
async def test_api_key_authentication_enforcement():
    settings = get_settings()
    valid_key = settings.API_KEY
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Request with no API key -> 401
        no_auth = await client.get("/api/auth/verify")
        assert no_auth.status_code == 401
        assert "Invalid or missing API key" in no_auth.json()["detail"]

        # Request with invalid API key -> 401
        bad_auth = await client.get(
            "/api/auth/verify", headers={"X-API-Key": "invalid_key_xyz"}
        )
        assert bad_auth.status_code == 401

        # Request with valid X-API-Key header -> 200
        good_auth = await client.get(
            "/api/auth/verify", headers={"X-API-Key": valid_key}
        )
        assert good_auth.status_code == 200
        assert good_auth.json()["status"] == "authenticated"

        # Request with valid Bearer token -> 200
        bearer_auth = await client.get(
            "/api/auth/verify", headers={"Authorization": f"Bearer {valid_key}"}
        )
        assert bearer_auth.status_code == 200
        assert bearer_auth.json()["status"] == "authenticated"

        # Request with valid query param -> 200
        query_auth = await client.get(f"/api/auth/verify?api_key={valid_key}")
        assert query_auth.status_code == 200
        assert query_auth.json()["status"] == "authenticated"
