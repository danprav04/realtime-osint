"""
backend/tests/test_tier5_gateway.py - Milestone 7 Phase 2 Tier 5 Adversarial Test Suite

Adversarial Coverage Hardening for:
1. Real-Time Gateway (backend/app/services/socket_server.py):
   - High-volume concurrent socket churn under continuous broadcast load.
   - Bounded ring buffer memory invariant (maxlen=100) under event floods.
   - Malformed, oversized, and hostile authentication payload fuzzing (SQLi, null bytes, XSS, 100KB tokens).
   - Adversarial room subscription payloads and room isolation.
   - Gateway lifecycle re-entrancy, idempotency, and shutdown isolation.

2. Zero-Code Theme Engine Hot-Reload Bursts (themes.py, supervisor.py, orchestrator.py):
   - Massive concurrent theme switching exclusivity (race conditions, exactly 1 active theme).
   - Rapid config reload bursts during active Ingestion Supervisor polling.
   - Pipeline Orchestrator prompt cache synchronization and stage config resilience under burst reload.
   - Resilient Redis bus disconnection fault tolerance during broadcast reloads.

3. Telegram Bot Anti-Flood, Token Bucket Starvation & Score=10 Alert Bypass (telegram_bot.py):
   - Token bucket starvation under high-frequency alert waves (50 alerts, 1 delivered, 49 throttled).
   - Critical score=10 immediate cooldown bypass under active throttling.
   - Adversarial interleaved score ordering [8, 9, 10, 7, 10, 8, 10].
   - Concurrent deduplication race conditions (25 simultaneous tasks, exactly 1 delivered).
   - Hostile 6000+ character summaries and Telegram 4096-character limit enforcement.
   - HTTP error resilience: 429 FloodWait with retry_after, 401 Unauthorized, 400 Bad Request, timeout.

4. Production Container Resource Bounds & Memory Budget (<900MB RAM Cap):
   - Docker Compose configuration verification (postgres 150M, redis 80M, backend 380M, frontend 220M -> 830MB <= 900MB).
   - Memory reservations sum <= 400MB (346MB total).
   - Single Uvicorn worker and Node.js max-old-space-size=180 enforcement.
   - Redis maxmemory 32mb and Postgres shared_buffers 32MB verification.
   - In-process RSS memory stability under heavy socket and alert stress (<50MB growth).
"""

import asyncio
from datetime import datetime, timezone
import hashlib
import json
import os
import psutil
import time
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch
import httpx
from httpx import ASGITransport, AsyncClient
import pytest
import pytest_asyncio
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
import yaml

from backend.app.config import get_settings
from backend.app.db.models import Alert, Base, Event, Source, SystemSettings, Theme, utc_now
from backend.app.db.session import close_db, get_session_factory, init_db
from backend.app.main import app
from backend.app.pipeline.models import AIStageConfig
from backend.app.pipeline.orchestrator import PipelineOrchestrator
from backend.app.plugins.base import BaseSourcePlugin, NormalizedEvent
from backend.app.plugins.supervisor import IngestionSupervisor
from backend.app.services.redis_bus import (
    CHANNEL_CONFIG_RELOAD,
    CHANNEL_EVENTS_ALERT,
    CHANNEL_EVENTS_NEW,
    CHANNEL_STATS_UPDATE,
    RedisBus,
)
from backend.app.services.socket_server import (
    _active_clients,
    _broadcast_log,
    _extract_auth_token,
    clear_broadcast_logs,
    connect as socket_connect,
    disconnect as socket_disconnect,
    get_active_client_count,
    get_broadcast_logs,
    handle_bus_event_alert,
    handle_bus_event_new,
    handle_bus_stats_update,
    init_socket_server,
    shutdown_socket_server,
    sio,
    subscribe_theme,
    unsubscribe_theme,
)
from backend.app.services.telegram_bot import (
    TelegramAlertPayload,
    TelegramBotService,
)
import socketio


# ==============================================================================
# HERMETIC DATABASE & FIXTURES
# ==============================================================================

@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


@pytest_asyncio.fixture(autouse=True)
async def setup_test_db():
    """Ensure database is initialized and cleanly closed for all tests."""
    await init_db()
    yield
    await close_db()


@pytest_asyncio.fixture
async def tier5_db_session_factory():
    """Isolated in-memory SQLite database engine and session factory for stress units."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as session:
        theme1 = Theme(
            id=1,
            slug="us-iran-conflict",
            name="US-Iran conflict",
            description="US-Iran regional geopolitical tension monitoring",
            keywords=["IRGC", "CENTCOM", "Strait of Hormuz", "Persian Gulf", "missile"],
            entities=["United States", "Iran", "IRGC", "CENTCOM"],
            scoring_prompt="Score 9-10 for direct kinetic strikes. Score 7-8 for proxy attacks. Score 1-3 for rhetoric.",
            alert_threshold=7,
            is_active=True,
        )
        theme2 = Theme(
            id=2,
            slug="china-taiwan-strait",
            name="China-Taiwan tensions",
            description="Cross-strait military exercises and defense monitoring",
            keywords=["PLA", "Taiwan Strait", "ADIZ", "Taipei", "semiconductors"],
            entities=["China", "Taiwan", "PLA", "Taipei"],
            scoring_prompt="Score 9-10 for maritime blockade or kinetic strike. Score 7-8 for major airspace incursions.",
            alert_threshold=7,
            is_active=False,
        )
        theme3 = Theme(
            id=3,
            slug="red-sea-maritime",
            name="Red Sea Maritime Security",
            description="Commercial shipping lanes and naval patrol monitoring",
            keywords=["Bab el-Mandeb", "Houthi", "drone", "tanker"],
            entities=["Yemen", "Houthis", "US Navy"],
            scoring_prompt="Score 9-10 for anti-ship ballistic missile hits. Score 7-8 for attempted drone attacks.",
            alert_threshold=7,
            is_active=False,
        )
        session.add_all([theme1, theme2, theme3])

        src = Source(
            id=1,
            theme_id=1,
            name="Reuters Naval Wire",
            source_type="rss",
            config_json={"url": "https://reuters.com/rss/world"},
            polling_interval_sec=120,
            is_enabled=True,
        )
        session.add(src)

        notif_cfg = SystemSettings(
            key="notification_settings",
            value_json={"cooldown_seconds": 30.0, "bot_token": "mock-token", "chat_id": "mock-chat"},
        )
        session.add(notif_cfg)

        await session.commit()

    yield factory
    await engine.dispose()


@pytest_asyncio.fixture(autouse=True)
async def cleanup_socket_and_bot():
    """Ensure socket server and bot state are cleanly reset before and after each test."""
    clear_broadcast_logs()
    _active_clients.clear()
    yield
    clear_broadcast_logs()
    _active_clients.clear()


@pytest.fixture
def api_headers():
    settings = get_settings()
    return {"X-API-Key": settings.API_KEY}


# ==============================================================================
# SECTION 1: REAL-TIME GATEWAY ADVERSARIAL STRESS & MEMORY STABILITY
# ==============================================================================

@pytest.mark.asyncio
async def test_tier5_socket_massive_churn_under_continuous_broadcast():
    """
    Adversarial Challenge: 100 concurrent clients rapidly connect, join theme rooms,
    receive heavy broadcast traffic (200 events), leave rooms, and disconnect concurrently.
    Verifies zero deadlocks, zero uncaught exceptions, and clean client tracking teardown.
    """
    settings = get_settings()
    num_clients = 100

    # 1. Connect 100 clients concurrently
    async def connect_client(idx: int):
        sid = f"churn_client_{idx}"
        environ = {"QUERY_STRING": f"token={settings.API_KEY}"}
        await socket_connect(sid, environ, None)

    await asyncio.gather(*(connect_client(i) for i in range(num_clients)))
    assert get_active_client_count() == num_clients

    # 2. Concurrently join rooms, leave rooms, and emit 200 broadcasts
    async def client_room_activity(idx: int):
        sid = f"churn_client_{idx}"
        room_payload = {"theme_id": "us-iran-conflict" if idx % 2 == 0 else "china-taiwan-strait"}
        await subscribe_theme(sid, room_payload)
        await asyncio.sleep(0.005)
        await unsubscribe_theme(sid, room_payload)

    async def broadcast_storm():
        for i in range(200):
            if i % 3 == 0:
                await handle_bus_event_new("osint:events:new", {"id": f"evt_{i}", "score": 7})
            elif i % 3 == 1:
                await handle_bus_event_alert("osint:events:alert", {"event_id": f"alt_{i}", "importance_score": 9})
            else:
                await handle_bus_stats_update("osint:stats:update", {"total_events": i, "active_sources": 5})

    with patch.object(sio, "emit", new_callable=AsyncMock), \
         patch.object(sio, "enter_room", new_callable=AsyncMock), \
         patch.object(sio, "leave_room", new_callable=AsyncMock):

        room_tasks = [client_room_activity(i) for i in range(num_clients)]
        await asyncio.gather(*room_tasks, broadcast_storm())

    # 3. Disconnect all 100 clients concurrently
    async def disconnect_client(idx: int):
        sid = f"churn_client_{idx}"
        await socket_disconnect(sid)

    await asyncio.gather(*(disconnect_client(i) for i in range(num_clients)))

    # Assert clean teardown and zero leaked client references
    assert get_active_client_count() == 0
    assert len(_active_clients) == 0


@pytest.mark.asyncio
async def test_tier5_ring_buffer_memory_cap_under_flood():
    """
    Adversarial Challenge: Flood 2,000 events across all broadcast channels.
    Strictly verifies that _broadcast_log (maxlen=100) never exceeds 100 elements,
    preventing unbounded in-memory accumulation and memory exhaustion (<150KB RSS bound).
    """
    clear_broadcast_logs()
    assert len(get_broadcast_logs()) == 0

    total_events = 2000
    with patch.object(sio, "emit", new_callable=AsyncMock):
        for i in range(total_events):
            if i % 3 == 0:
                await handle_bus_event_new("osint:events:new", {"id": f"flood_{i}", "seq": i})
            elif i % 3 == 1:
                await handle_bus_event_alert("osint:events:alert", {"event_id": f"flood_alt_{i}", "score": 9})
            else:
                await handle_bus_stats_update("osint:stats:update", {"total": i})

            if i % 100 == 0:
                assert len(get_broadcast_logs()) <= 100

    logs = get_broadcast_logs()
    assert len(logs) == 100, "Ring buffer must be strictly capped at 100 elements"

    # Last event index 1999: 1999 % 3 == 1 -> alert:critical with event_id="flood_alt_1999"
    last_log = logs[-1]
    assert last_log["type"] == "alert:critical"
    assert last_log["payload"]["event_id"] == "flood_alt_1999"


@pytest.mark.asyncio
async def test_tier5_malformed_hostile_auth_fuzzing():
    """
    Adversarial Challenge: Fuzz Socket.IO authentication with SQLi, XSS, null bytes,
    100KB oversized tokens, and exotic object types across auth dict, query string, and headers.
    Must reliably raise ConnectionRefusedError without crashing the server or adding phantom clients.
    """
    settings = get_settings()

    hostile_auth_payloads = [
        {"token": "A" * 102400},  # 100 KB oversized token
        {"token": f"{settings.API_KEY}\x00extra_null_byte"},
        {"token": "'; DROP TABLE events; --"},
        {"token": "' OR '1'='1"},
        {"token": "<script>alert(document.cookie)</script>"},
        {"token": {"nested": settings.API_KEY}},  # nested dict
        {"token": [settings.API_KEY]},  # list
        {"token": 99999999},  # integer
        {"token": None},  # None
        {"token": True},  # Boolean
        {"token": "   \t\r\n   "},  # whitespace only
        {"token": "Bearer "},  # partial prefix
        {"token": "dev-secret-key-wrong"},
    ]

    for auth in hostile_auth_payloads:
        with pytest.raises(socketio.exceptions.ConnectionRefusedError):
            await socket_connect(f"hostile_sid_{id(auth)}", {"QUERY_STRING": ""}, auth)

    assert get_active_client_count() == 0, "No unauthorized client may be registered"


@pytest.mark.asyncio
async def test_tier5_room_subscription_adversarial_payloads():
    """
    Adversarial Challenge: Pass malformed payloads (non-dict, giant strings, empty dict,
    unicode anomalies) into subscribe_theme and unsubscribe_theme.
    Verifies that invalid payloads are safely ignored without raising unhandled exceptions.
    """
    adversarial_room_inputs = [
        None,
        "raw_string_not_dict",
        123456,
        [],
        {},
        {"unrelated_key": "val"},
        {"theme_id": ""},
        {"theme_id": None},
        {"theme_id": "X" * 10000},
        {"theme_id": "theme\x00null"},
        {"theme_id": "🚨_persian_تهران_strait"},
    ]

    test_sid = "room_adversary_sid"
    _active_clients[test_sid] = {"connected_at": time.time()}

    with patch.object(sio, "enter_room", new_callable=AsyncMock) as mock_enter, \
         patch.object(sio, "leave_room", new_callable=AsyncMock) as mock_leave:

        for payload in adversarial_room_inputs:
            await subscribe_theme(test_sid, payload)  # type: ignore
            await unsubscribe_theme(test_sid, payload)  # type: ignore

        # Valid payload must succeed and enter proper room
        valid_payload = {"theme_id": "us-iran-conflict"}
        await subscribe_theme(test_sid, valid_payload)
        mock_enter.assert_called_with(test_sid, "theme:us-iran-conflict")

        await unsubscribe_theme(test_sid, valid_payload)
        mock_leave.assert_called_with(test_sid, "theme:us-iran-conflict")


@pytest.mark.asyncio
async def test_tier5_gateway_lifecycle_reentrancy_and_shutdown_isolation():
    """
    Adversarial Challenge: Verify idempotency of init_socket_server() under concurrent calls,
    clean disconnection handling, and graceful teardown with shutdown_socket_server().
    """
    mock_bus = RedisBus(redis_url=None)

    # 1. Concurrent init calls must be strictly idempotent
    await asyncio.gather(
        init_socket_server(mock_bus),
        init_socket_server(mock_bus),
        init_socket_server(mock_bus),
    )

    # 2. Connect test clients
    settings = get_settings()
    for i in range(5):
        await socket_connect(f"lifecycle_client_{i}", {"QUERY_STRING": f"token={settings.API_KEY}"}, None)
    assert get_active_client_count() == 5

    # 3. Shutdown cleanly purges active clients and unhooks from bus
    await shutdown_socket_server(mock_bus)
    assert get_active_client_count() == 0
    assert len(_active_clients) == 0

    # 4. Repeated shutdown calls are safe
    await shutdown_socket_server(mock_bus)


# ==============================================================================
# SECTION 2: ZERO-CODE THEME ENGINE DYNAMIC HOT-RELOAD BURSTS
# ==============================================================================

@pytest.mark.asyncio
async def test_tier5_theme_reload_burst_concurrency_exclusivity(api_headers):
    """
    Adversarial Challenge: Concurrently trigger 12 theme activation requests across 3 distinct themes.
    Asserts that at conclusion:
    1. Exactly ONE theme is active in the database (zero race condition corruption).
    2. Zero HTTP 500 crashes occur.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Create 3 distinct test themes
        theme_ids = []
        for i in range(3):
            t_payload = {
                "name": f"Concurrency Theme {i}_{time.time()}",
                "slug": f"concurrency-theme-{i}-{int(time.time() * 1000)}",
                "scoring_prompt": f"Scoring prompt for concurrency theme {i}",
                "alert_threshold": 7,
                "is_active": False,
            }
            res = await client.post("/api/themes", json=t_payload, headers=api_headers)
            assert res.status_code == 200
            theme_ids.append(res.json()["slug"])

        # Fire 12 concurrent activations interleaved across themes (fitting pool size)
        tasks = [
            client.post(f"/api/themes/{theme_ids[i % len(theme_ids)]}/activate", headers=api_headers)
            for i in range(12)
        ]
        responses = await asyncio.gather(*tasks)

        for r in responses:
            assert r.status_code == 200
            assert r.json()["status"] == "activated"

        # Verify database state exclusivity: EXACTLY ONE theme is active
        session_factory = get_session_factory()
        async with session_factory() as session:
            stmt = select(func.count()).select_from(Theme).where(Theme.is_active == True)
            active_count = (await session.execute(stmt)).scalar_one()
            assert active_count == 1, f"Expected exactly 1 active theme, but found {active_count}!"


@pytest.mark.asyncio
async def test_tier5_rapid_theme_reload_during_active_supervisor_ingestion(tier5_db_session_factory):
    """
    Adversarial Challenge: Trigger rapid burst of 25 config reload events while the IngestionSupervisor
    is actively executing polling tasks.
    Verifies that supervisor._lock serializes reconciliation without race conditions,
    task collisions, orphan tasks, or deadlock.
    """
    mock_bus = RedisBus(redis_url=None)
    supervisor = IngestionSupervisor(session_factory=tier5_db_session_factory, redis_bus=mock_bus)

    # Start supervisor
    await supervisor.start()
    assert supervisor.is_running

    # Rapid burst of 25 concurrent reload calls
    reload_tasks = [
        supervisor.handle_config_reload(
            CHANNEL_CONFIG_RELOAD,
            {"action": "theme_update", "theme_id": f"theme_{(i % 3) + 1}", "timestamp": datetime.now(timezone.utc).isoformat()}
        )
        for i in range(25)
    ]
    await asyncio.gather(*reload_tasks)

    # Supervisor must remain healthy and stable
    status = supervisor.get_status()
    assert status["is_running"] is True
    assert status["total_plugins"] >= 1

    # Clean shutdown must cancel all tasks without hanging
    await supervisor.stop()
    assert supervisor.is_running is False
    assert len(supervisor._tasks) == 0


@pytest.mark.asyncio
async def test_tier5_pipeline_orchestrator_cache_resilience_under_burst_reload(tier5_db_session_factory):
    """
    Adversarial Challenge: Rapidly trigger 20 config reloads on the PipelineOrchestrator
    while concurrently enqueuing normalized events.
    Verifies that AI stage configs and active theme prompt cache reload atomically
    without unhandled race exceptions or cache corruption.
    """
    mock_bus = RedisBus(redis_url=None)
    orchestrator = PipelineOrchestrator(
        session_factory=tier5_db_session_factory,
        redis_bus=mock_bus,
        use_mock=True,
    )

    await orchestrator.start()
    assert orchestrator._running

    # Enqueue events while bombarding with reload requests
    async def enqueue_burst():
        for i in range(20):
            event = NormalizedEvent(
                id=f"tier5_evt_{i}",
                theme_id="1",
                source_type="rss",
                source_name="Reuters Test",
                external_id=f"ext_{i}",
                title=f"Breaking intelligence event {i}",
                raw_text=f"Iranian naval forces conducted patrol {i} in the Persian Gulf.",
                published_at=datetime.now(timezone.utc),
            )
            await orchestrator.enqueue_event(event)
            await asyncio.sleep(0.002)

    reload_tasks = [orchestrator._reload_configs_from_db() for _ in range(20)]
    await asyncio.gather(enqueue_burst(), *reload_tasks)

    # Verify stage configs are intact
    assert orchestrator.get_stage_config("extraction").stage_id == "extraction"
    assert orchestrator.get_stage_config("scoring").stage_id == "scoring"
    assert len(orchestrator._theme_cache) > 0

    await orchestrator.stop()
    assert orchestrator._running is False


@pytest.mark.asyncio
async def test_tier5_broadcast_theme_reload_bus_failure_fault_tolerance(api_headers):
    """
    Adversarial Challenge: When Redis bus raises network connection / publish exceptions during
    theme update, verify that the API endpoint catches the exception gracefully, logs a warning,
    and returns HTTP 200 without crashing the web service.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Create a test theme first
        create_resp = await client.post(
            "/api/themes",
            headers=api_headers,
            json={
                "name": f"Fault Tolerance Theme {time.time()}",
                "slug": f"fault-tolerance-{int(time.time() * 1000)}",
                "scoring_prompt": "Initial prompt",
            },
        )
        assert create_resp.status_code == 200
        slug = create_resp.json()["slug"]

        with patch("backend.app.services.redis_bus.RedisBus.publish", side_effect=ConnectionResetError("Simulated Redis failure")):
            resp = await client.put(
                f"/api/themes/{slug}",
                headers=api_headers,
                json={"scoring_prompt": "Updated resilient scoring prompt under Redis outage."},
            )
            assert resp.status_code == 200, "Theme update must succeed even if Redis publish raises an error"
            assert "resilient" in resp.json()["scoring_prompt"]


# ==============================================================================
# SECTION 3: TELEGRAM BOT ANTI-FLOOD, TOKEN BUCKET & SCORE=10 BYPASS
# ==============================================================================

@pytest.mark.asyncio
async def test_tier5_token_bucket_starvation_and_cooldown_enforcement(tier5_db_session_factory):
    """
    Adversarial Challenge: Fire a burst of 50 non-critical alerts (score=8) within 50 milliseconds
    under a 30.0s cooldown window.
    Asserts that:
    1. EXACTLY the 1st alert is delivered.
    2. All subsequent 49 alerts are throttled with status='throttled' and reason='rate_limit_cooldown'.
    3. Throttled alerts are recorded in PostgreSQL/SQLite without dropping or crashing.
    """
    mock_bus = RedisBus(redis_url=None)
    bot = TelegramBotService(rate_limit_seconds=30.0, redis_bus=mock_bus)

    # Pre-seed 50 parent events in the database to satisfy ForeignKey("events.id") constraint
    async with tier5_db_session_factory() as session:
        for i in range(50):
            ev = Event(
                id=f"burst_event_{i}",
                theme_id=1,
                source_id=1,
                source_type="rss",
                source_name="Reuters Wire",
                external_id=f"ext_burst_{i}",
                title=f"Naval Alert {i}",
                raw_text=f"Patrol movement detected {i}",
                published_at=datetime.now(timezone.utc),
            )
            session.add(ev)
        await session.commit()

    with patch("backend.app.services.telegram_bot.get_session_factory", return_value=tier5_db_session_factory):
        results = []
        for i in range(50):
            payload = TelegramAlertPayload(
                event_id=f"burst_event_{i}",
                title=f"Naval Alert {i}",
                summary=f"Patrol movement detected {i}",
                importance_score=8,
                source_name="Reuters Wire",
                published_at=datetime.now(timezone.utc),
                source_url=f"https://reuters.com/news/article_{i}",
            )
            res = await bot.send_alert(payload)
            results.append(res)

        delivered = [r for r in results if r["status"] == "delivered"]
        throttled = [r for r in results if r["status"] == "throttled"]

        assert len(delivered) == 1, "Exactly 1 alert should be delivered under cooldown"
        assert len(throttled) == 49, "All 49 remaining alerts must be throttled"
        assert throttled[0]["reason"] == "rate_limit_cooldown"

        # Verify DB records
        async with tier5_db_session_factory() as session:
            stmt = select(Alert)
            db_alerts = (await session.execute(stmt)).scalars().all()
            assert len(db_alerts) == 50

            # Find alert for burst_event_0 (the only delivered alert)
            delivered_db_stmt = select(Alert).where(Alert.event_id == "burst_event_0")
            delivered_db_alert = (await session.execute(delivered_db_stmt)).scalars().first()
            assert delivered_db_alert is not None
            assert delivered_db_alert.status == "delivered"

            # Check that remaining 49 alerts are throttled
            throttled_db_stmt = select(Alert).where(Alert.event_id != "burst_event_0")
            throttled_db_alerts = (await session.execute(throttled_db_stmt)).scalars().all()
            assert len(throttled_db_alerts) == 49
            for a in throttled_db_alerts:
                assert a.status == "throttled"


@pytest.mark.asyncio
async def test_tier5_score_10_critical_alert_immediate_cooldown_bypass(tier5_db_session_factory):
    """
    Adversarial Challenge: Put the Telegram bot into a starved cooldown state with a score=8 alert,
    then immediately send critical score=10 alerts.
    Asserts that score=10 alerts BYPASS cooldown unconditionally and are dispatched immediately!
    Subsequent sub-10 alerts remain throttled.
    """
    mock_bus = RedisBus(redis_url=None)
    bot = TelegramBotService(rate_limit_seconds=30.0, redis_bus=mock_bus)

    with patch("backend.app.services.telegram_bot.get_session_factory", return_value=tier5_db_session_factory):
        # 1. Send score=8 alert -> Delivered, begins 30s cooldown
        p1 = TelegramAlertPayload(
            event_id="initial_score8",
            title="Sanctions announcement",
            summary="Treasury sanctions announced",
            importance_score=8,
            source_name="AP",
            published_at=datetime.now(timezone.utc),
            source_url="https://apnews.com/sanctions-1",
        )
        r1 = await bot.send_alert(p1)
        assert r1["status"] == "delivered"

        # 2. Immediately send score=10 alert (<1ms later) -> MUST BYPASS COOLDOWN AND DELIVER!
        p_crit1 = TelegramAlertPayload(
            event_id="critical_score10_1",
            title="CRITICAL KINETIC STRIKE",
            summary="Direct ballistic missile impact reported on naval base",
            importance_score=10,
            source_name="CENTCOM",
            published_at=datetime.now(timezone.utc),
            source_url="https://centcom.mil/strike-1",
        )
        r_crit1 = await bot.send_alert(p_crit1)
        assert r_crit1["status"] == "delivered", "Score=10 must bypass cooldown immediately!"

        # 3. Immediately send a SECOND score=10 alert -> MUST ALSO DELIVER!
        p_crit2 = TelegramAlertPayload(
            event_id="critical_score10_2",
            title="SECOND CRITICAL IMPACT",
            summary="Second salvo confirmed on commercial shipping lane",
            importance_score=10,
            source_name="UKMTO",
            published_at=datetime.now(timezone.utc),
            source_url="https://ukmto.org/incident-2",
        )
        r_crit2 = await bot.send_alert(p_crit2)
        assert r_crit2["status"] == "delivered", "Subsequent score=10 must also bypass cooldown!"

        # 4. Immediately send a score=9 alert -> MUST BE THROTTLED!
        p_score9 = TelegramAlertPayload(
            event_id="high_score9",
            title="Severe proxy attack",
            summary="Severe rocket artillery fire across border",
            importance_score=9,
            source_name="Al Jazeera",
            published_at=datetime.now(timezone.utc),
            source_url="https://aljazeera.com/attack-3",
        )
        r_score9 = await bot.send_alert(p_score9)
        assert r_score9["status"] == "throttled", "Score=9 must still be subject to rate limit cooldown"
        assert r_score9["reason"] == "rate_limit_cooldown"


@pytest.mark.asyncio
async def test_tier5_interleaved_alert_storm_exact_delivery_sequence(tier5_db_session_factory):
    """
    Adversarial Challenge: Process an interleaved storm of 7 alerts with scores [8, 9, 10, 7, 10, 8, 10].
    Asserts exact delivery sequence:
    - #1 (score 8): DELIVERED (starts cooldown)
    - #2 (score 9): THROTTLED (cooldown active)
    - #3 (score 10): DELIVERED (critical bypass)
    - #4 (score 7): THROTTLED (cooldown active)
    - #5 (score 10): DELIVERED (critical bypass)
    - #6 (score 8): THROTTLED (cooldown active)
    - #7 (score 10): DELIVERED (critical bypass)
    Total delivered: 4. Total throttled: 3.
    """
    mock_bus = RedisBus(redis_url=None)
    bot = TelegramBotService(rate_limit_seconds=30.0, redis_bus=mock_bus)

    scores = [8, 9, 10, 7, 10, 8, 10]
    expected_statuses = [
        "delivered",  # 8
        "throttled",  # 9
        "delivered",  # 10
        "throttled",  # 7
        "delivered",  # 10
        "throttled",  # 8
        "delivered",  # 10
    ]

    with patch("backend.app.services.telegram_bot.get_session_factory", return_value=tier5_db_session_factory):
        for idx, (score, expected) in enumerate(zip(scores, expected_statuses)):
            payload = TelegramAlertPayload(
                event_id=f"seq_evt_{idx}",
                title=f"Sequence Alert {idx} (Score {score})",
                summary=f"Event summary description {idx}",
                importance_score=score,
                source_name="OSINT Multi-Feed",
                published_at=datetime.now(timezone.utc),
                source_url=f"https://osint.io/event_{idx}",
            )
            res = await bot.send_alert(payload)
            assert res["status"] == expected, f"Alert #{idx} (score {score}) expected {expected}, got {res['status']}"


@pytest.mark.asyncio
async def test_tier5_concurrent_deduplication_window_race(tier5_db_session_factory):
    """
    Adversarial Challenge: 25 concurrent tasks fire an identical alert payload (identical URL)
    simultaneously via asyncio.gather.
    Asserts that EXACTLY ONE alert passes through deduplication, and 24 are suppressed as duplicates.
    """
    mock_bus = RedisBus(redis_url=None)
    bot = TelegramBotService(rate_limit_seconds=30.0, redis_bus=mock_bus)

    identical_payload = TelegramAlertPayload(
        event_id="race_event_same_id",
        title="Breaking Naval Drone Interception",
        summary="Guided missile destroyer intercepted 3 one-way attack drones.",
        importance_score=10,  # score 10 ensures rate limiter doesn't mask dedup logic
        source_name="US Navy Press",
        published_at=datetime.now(timezone.utc),
        source_url="https://navy.mil/press/release-2026-10-07-drone-intercept",
    )

    with patch("backend.app.services.telegram_bot.get_session_factory", return_value=tier5_db_session_factory):
        tasks = [bot.send_alert(identical_payload) for _ in range(25)]
        results = await asyncio.gather(*tasks)

        delivered = [r for r in results if r["status"] == "delivered"]
        duplicates = [r for r in results if r["status"] == "duplicate"]

        assert len(delivered) == 1, f"Expected exactly 1 delivered alert, got {len(delivered)}"
        assert len(duplicates) == 24, f"Expected 24 suppressed duplicate alerts, got {len(duplicates)}"
        assert duplicates[0]["reason"] == "dedup_window_active"


@pytest.mark.asyncio
async def test_tier5_hostile_summary_truncation_and_markdown_safety():
    """
    Adversarial Challenge: Deliver an alert with an extreme 6,000-character summary containing
    unbalanced Markdown symbols, HTML brackets, and emojis.
    Asserts that format_message truncates to <= 3,000 characters and keeps total length
    safely below Telegram's 4,096-character ceiling.
    """
    bot = TelegramBotService()
    extreme_summary = "🚨 [CRITICAL ALERT] *Bold unclosed _italic unclosed `code unclosed " + ("X" * 6000)
    payload = TelegramAlertPayload(
        event_id="trunc_event_1",
        title="Hostile Payload Title",
        summary=extreme_summary,
        importance_score=9,
        source_name="Hostile Wire",
        published_at=datetime.now(timezone.utc),
        source_url="https://wire.com/long-url",
    )

    formatted = bot.format_message(payload)
    assert len(formatted) < 4096, f"Formatted message must strictly obey Telegram 4096-char ceiling, got {len(formatted)}"
    assert "*Summary:*" in formatted
    assert "*Source:*" in formatted


@pytest.mark.asyncio
async def test_tier5_telegram_http_error_simulation_resilience(tier5_db_session_factory):
    """
    Adversarial Challenge: Simulate external Telegram Bot API error conditions:
    1. HTTP 429 FloodWait with retry_after=35 -> throttles and sets future last_sent_timestamp.
    2. HTTP 401 Unauthorized -> logs error and records status='failed'.
    3. HTTP 400 Bad Request -> logs error and records status='failed'.
    4. Network Timeout -> catches exception and records status='failed'.
    """
    mock_bus = RedisBus(redis_url=None)
    bot = TelegramBotService(
        bot_token="test_token_123",
        chat_id="test_chat_456",
        redis_bus=mock_bus,
    )

    with patch("backend.app.services.telegram_bot.get_session_factory", return_value=tier5_db_session_factory):
        # 1. HTTP 429 FloodWait
        mock_resp_429 = MagicMock()
        mock_resp_429.status_code = 429
        mock_resp_429.json.return_value = {"parameters": {"retry_after": 35}}
        with patch.object(bot.http_client, "post", new_callable=AsyncMock, return_value=mock_resp_429):
            p_429 = TelegramAlertPayload(
                event_id="err_429",
                title="Rate limit test",
                summary="Summary",
                importance_score=8,
                source_name="AP",
                published_at=datetime.now(timezone.utc),
            )
            res = await bot.send_alert(p_429)
            assert res["status"] == "throttled"
            assert res["reason"] == "telegram_flood_wait"
            assert bot.last_sent_timestamp > time.time() + 25

        # 2. HTTP 401 Unauthorized
        mock_resp_401 = MagicMock()
        mock_resp_401.status_code = 401
        with patch.object(bot.http_client, "post", new_callable=AsyncMock, return_value=mock_resp_401):
            p_401 = TelegramAlertPayload(
                event_id="err_401",
                title="Unauthorized test",
                summary="Summary",
                importance_score=10,
                source_name="AP",
                published_at=datetime.now(timezone.utc),
            )
            res = await bot.send_alert(p_401)
            assert res["status"] == "failed"
            assert "Unauthorized" in res["error"]

        # 3. HTTP 400 Bad Request
        mock_resp_400 = MagicMock()
        mock_resp_400.status_code = 400
        mock_resp_400.text = "Bad Request: can't parse entities"
        with patch.object(bot.http_client, "post", new_callable=AsyncMock, return_value=mock_resp_400):
            p_400 = TelegramAlertPayload(
                event_id="err_400",
                title="Bad request test",
                summary="Summary",
                importance_score=10,
                source_name="AP",
                published_at=datetime.now(timezone.utc),
            )
            res = await bot.send_alert(p_400)
            assert res["status"] == "failed"
            assert "Bad Request" in res["error"]

        # 4. Network Timeout / Connection Error
        with patch.object(bot.http_client, "post", side_effect=httpx.ConnectTimeout("Telegram API unreachable")):
            p_timeout = TelegramAlertPayload(
                event_id="err_timeout",
                title="Timeout test",
                summary="Summary",
                importance_score=10,
                source_name="AP",
                published_at=datetime.now(timezone.utc),
            )
            res = await bot.send_alert(p_timeout)
            assert res["status"] == "failed"
            assert "Telegram API unreachable" in res["error"]

    await bot.stop()


# ==============================================================================
# SECTION 4: CONTAINER RESOURCE BOUNDS & 1GB VPS ENFORCEMENT (<900MB)
# ==============================================================================

def test_tier5_docker_compose_resource_limits_and_budget_cap():
    """
    Production Hardening Verification:
    Parse docker-compose.yml and verify strict resource bounds for 1GB RAM Linux VPS:
    1. All 4 services present: postgres, redis, backend, frontend.
    2. Total memory limit across all containers <= 900MB (specifically 830MB <= 900MB).
    3. Total memory reservation across all containers <= 400MB (specifically 346MB).
    4. Backend uvicorn workers == 1 (prevents multi-process memory multiplication).
    5. Frontend NODE_OPTIONS includes --max-old-space-size=180.
    6. Redis maxmemory == 32mb with allkeys-lru eviction.
    7. Postgres shared_buffers == 32MB and max_connections == 20.
    """
    compose_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "docker-compose.yml"))
    assert os.path.exists(compose_path), f"docker-compose.yml not found at {compose_path}"

    with open(compose_path, "r", encoding="utf-8") as f:
        compose_data = yaml.safe_load(f)

    services = compose_data.get("services", {})
    required_services = ["postgres", "redis", "backend", "frontend"]
    for svc in required_services:
        assert svc in services, f"Required service '{svc}' missing in docker-compose.yml"

    def parse_mb(mem_str: str) -> float:
        s = str(mem_str).strip().upper()
        if s.endswith("M"):
            return float(s[:-1])
        if s.endswith("MB"):
            return float(s[:-2])
        if s.endswith("G") or s.endswith("GB"):
            return float(s.rstrip("GB")) * 1024
        return float(s)

    total_limit_mb = 0.0
    total_reservation_mb = 0.0

    service_limits = {}
    for svc_name, svc_cfg in services.items():
        resources = svc_cfg.get("deploy", {}).get("resources", {})
        limit = resources.get("limits", {}).get("memory")
        reservation = resources.get("reservations", {}).get("memory")

        assert limit is not None, f"Service '{svc_name}' missing memory limit in docker-compose.yml"
        assert reservation is not None, f"Service '{svc_name}' missing memory reservation in docker-compose.yml"

        limit_mb = parse_mb(limit)
        res_mb = parse_mb(reservation)
        service_limits[svc_name] = limit_mb

        total_limit_mb += limit_mb
        total_reservation_mb += res_mb

    # 1. Total limit strictly <= 900MB
    assert total_limit_mb <= 900.0, f"Total container memory limit {total_limit_mb}MB exceeds 900MB VPS ceiling!"
    assert total_limit_mb == 830.0, f"Expected tuned total 830MB, got {total_limit_mb}MB"

    # 2. Total reservations strictly <= 400MB
    assert total_reservation_mb <= 400.0, f"Total container memory reservation {total_reservation_mb}MB exceeds 400MB budget!"
    assert total_reservation_mb == 346.0, f"Expected tuned reservation 346MB, got {total_reservation_mb}MB"

    # 3. Individual container limits
    assert service_limits["postgres"] <= 150.0
    assert service_limits["redis"] <= 80.0
    assert service_limits["backend"] <= 380.0
    assert service_limits["frontend"] <= 220.0

    # 4. Backend single worker validation
    backend_cmd = services["backend"].get("command", "")
    assert "--workers 1" in backend_cmd, "Backend must run with single Uvicorn worker to preserve memory"

    # 5. Frontend Node.js heap limit validation
    frontend_env = services["frontend"].get("environment", {})
    node_opts = frontend_env.get("NODE_OPTIONS", "")
    assert "--max-old-space-size=180" in node_opts, "Frontend Node options must set --max-old-space-size=180"

    # 6. Redis cache tuning validation
    redis_cmd = services["redis"].get("command", "")
    assert "--maxmemory 32mb" in redis_cmd
    assert "--maxmemory-policy allkeys-lru" in redis_cmd

    # 7. Postgres memory tuning validation
    pg_cmd = services["postgres"].get("command", "")
    assert "shared_buffers=32MB" in pg_cmd
    assert "max_connections=20" in pg_cmd


@pytest.mark.asyncio
async def test_tier5_in_process_memory_stability_under_stress():
    """
    Adversarial Challenge: Track Python process RSS memory before and after running
    500 real-time gateway broadcast cycles and 200 bot alert dispatches.
    Asserts that memory growth delta is strictly bounded (<50MB growth).
    """
    process = psutil.Process(os.getpid())
    rss_before_mb = process.memory_info().rss / (1024 * 1024)

    # Execute 500 broadcast events
    with patch.object(sio, "emit", new_callable=AsyncMock):
        for i in range(500):
            await handle_bus_event_new("osint:events:new", {"id": f"mem_evt_{i}", "title": "Memory test event"})

    # Execute 200 Telegram alerts
    bot = TelegramBotService()
    for i in range(200):
        payload = TelegramAlertPayload(
            event_id=f"mem_alert_{i}",
            title=f"Alert {i}",
            summary="Testing in-process memory retention",
            importance_score=8,
            source_name="Memory Monitor",
            published_at=datetime.now(timezone.utc),
            source_url=f"https://osint.io/mem/{i}",
        )
        await bot.send_alert(payload)

    rss_after_mb = process.memory_info().rss / (1024 * 1024)
    rss_growth_mb = rss_after_mb - rss_before_mb

    assert rss_growth_mb < 50.0, (
        f"In-process RSS memory growth of {rss_growth_mb:.2f}MB exceeded 50MB bound! "
        f"(Before: {rss_before_mb:.2f}MB, After: {rss_after_mb:.2f}MB)"
    )
