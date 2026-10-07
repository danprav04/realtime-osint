"""
Unit and integration test suite for Milestone 4:
- Real-Time Gateway (Socket.IO ASGI Server, API key auth, memory buffer, Redis pub/sub)
- Theme Engine & REST API routes (themes, sources, events, stats, hot-reload broadcast)
- Telegram Notification Bot (template compliance, cooldown, score=10 bypass, 24h dedup, resilience)
"""

import asyncio
from datetime import datetime, timezone
import json
import time
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
import socketio
from backend.app.config import get_settings
from backend.app.db.models import Event, Source, Theme, utc_now
from backend.app.db.session import close_db, get_session_factory, init_db
from backend.app.main import app
from backend.app.services.redis_bus import (
    CHANNEL_CONFIG_RELOAD,
    CHANNEL_EVENTS_ALERT,
    CHANNEL_EVENTS_NEW,
    CHANNEL_STATS_UPDATE,
    get_redis_bus,
)
from backend.app.services.socket_server import (
    clear_broadcast_logs,
    connect as socket_connect,
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
    get_telegram_bot,
)


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


@pytest_asyncio.fixture(autouse=True)
async def setup_and_teardown_db():
    await init_db()
    clear_broadcast_logs()
    yield
    await close_db()


@pytest.fixture
def api_headers():
    settings = get_settings()
    return {"X-API-Key": settings.API_KEY}


# ==============================================================================
# 1. REAL-TIME GATEWAY & SOCKET.IO TESTS
# ==============================================================================

@pytest.mark.asyncio
async def test_socket_auth_valid_token_dict():
    """Verify Socket.IO connection accepts valid API key in auth dict."""
    settings = get_settings()
    environ = {"QUERY_STRING": ""}
    auth = {"api_key": settings.API_KEY}
    
    # Should connect without raising ConnectionRefusedError
    await socket_connect("test_sid_1", environ, auth)
    assert get_active_client_count() >= 1


@pytest.mark.asyncio
async def test_socket_auth_valid_token_query_param():
    """Verify Socket.IO connection accepts valid API key in query string."""
    settings = get_settings()
    environ = {"QUERY_STRING": f"token={settings.API_KEY}"}
    
    await socket_connect("test_sid_2", environ, None)
    assert get_active_client_count() >= 1


@pytest.mark.asyncio
async def test_socket_auth_valid_token_header():
    """Verify Socket.IO connection accepts valid API key in HTTP headers."""
    settings = get_settings()
    environ = {"HTTP_X_API_KEY": settings.API_KEY, "QUERY_STRING": ""}
    
    await socket_connect("test_sid_3", environ, None)
    assert get_active_client_count() >= 1


@pytest.mark.asyncio
async def test_socket_auth_rejected_invalid_token():
    """Verify Socket.IO rejects invalid token with ConnectionRefusedError."""
    environ = {"QUERY_STRING": "token=invalid_unauthorized_key"}
    auth = {"token": "wrong_key"}

    with pytest.raises(socketio.exceptions.ConnectionRefusedError):
        await socket_connect("test_sid_invalid", environ, auth)


@pytest.mark.asyncio
async def test_socket_room_subscription():
    """Verify client can join and leave theme rooms."""
    with patch.object(sio, "enter_room", new_callable=AsyncMock) as mock_enter, \
         patch.object(sio, "leave_room", new_callable=AsyncMock) as mock_leave:
        
        await subscribe_theme("test_sid_room", {"theme_id": "us-iran-conflict"})
        mock_enter.assert_awaited_once_with("test_sid_room", "theme:us-iran-conflict")

        await unsubscribe_theme("test_sid_room", {"theme_id": "us-iran-conflict"})
        mock_leave.assert_awaited_once_with("test_sid_room", "theme:us-iran-conflict")


@pytest.mark.asyncio
async def test_socket_broadcast_event_new():
    """Verify event:new bus message emits to Socket.IO and logs to bounded buffer."""
    clear_broadcast_logs()
    test_event = {
        "id": "evt-123",
        "title": "Naval Escort Deployed",
        "importance_score": 8,
    }

    with patch.object(sio, "emit", new_callable=AsyncMock) as mock_emit:
        await handle_bus_event_new("osint:events:new", test_event)
        mock_emit.assert_awaited_once_with("event:new", test_event)

    logs = get_broadcast_logs()
    assert len(logs) == 1
    assert logs[0]["type"] == "event:new"
    assert logs[0]["payload"]["id"] == "evt-123"


@pytest.mark.asyncio
async def test_socket_broadcast_alert_critical():
    """Verify alert:critical bus message emits and provides both id and event_id."""
    clear_broadcast_logs()
    test_alert = {
        "event_id": "evt-999",
        "title": "Air Defense Engagement",
        "importance_score": 9,
    }

    with patch.object(sio, "emit", new_callable=AsyncMock) as mock_emit:
        await handle_bus_event_alert("osint:events:alert", test_alert)
        mock_emit.assert_awaited_once()
        args = mock_emit.call_args[0]
        assert args[0] == "alert:critical"
        assert args[1]["id"] == "evt-999"
        assert args[1]["event_id"] == "evt-999"

    logs = get_broadcast_logs()
    assert len(logs) == 1
    assert logs[0]["type"] == "alert:critical"


@pytest.mark.asyncio
async def test_socket_broadcast_stats_update():
    """Verify stats:update bus message emits to connected clients."""
    clear_broadcast_logs()
    test_stats = {"total_events": 50, "high_priority_events": 5}

    with patch.object(sio, "emit", new_callable=AsyncMock) as mock_emit:
        await handle_bus_stats_update("osint:stats:update", test_stats)
        mock_emit.assert_awaited_once_with("stats:update", test_stats)

    logs = get_broadcast_logs()
    assert len(logs) == 1
    assert logs[0]["type"] == "stats:update"


@pytest.mark.asyncio
async def test_socket_bounded_ring_buffer():
    """Verify memory ring buffer caps history at 100 items."""
    clear_broadcast_logs()
    with patch.object(sio, "emit", new_callable=AsyncMock):
        for i in range(120):
            await handle_bus_event_new("osint:events:new", {"id": f"event-{i}"})

    logs = get_broadcast_logs()
    assert len(logs) == 100
    assert logs[0]["payload"]["id"] == "event-20"
    assert logs[-1]["payload"]["id"] == "event-119"


# ==============================================================================
# 2. THEME ENGINE REST API TESTS
# ==============================================================================

@pytest.mark.asyncio
async def test_theme_crud_and_activation(api_headers):
    """Test full theme lifecycle: create, list, retrieve, update, activate, delete."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Unauthenticated request should fail with 401
        unauth_res = await client.get("/api/themes")
        assert unauth_res.status_code == 401

        # 2. Create new theme
        theme_payload = {
            "name": f"South China Sea Maritime {time.time()}",
            "slug": "south-china-sea-maritime",
            "description": "Tracking maritime friction in the South China Sea",
            "keywords": ["Spratly", "Second Thomas Shoal", "Coast Guard"],
            "entities": ["China", "Philippines", "US Navy"],
            "scoring_prompt": "Score 1-10 on kinetic confrontations.",
            "importance_threshold": 8,
            "is_active": False,
        }
        create_res = await client.post("/api/themes", json=theme_payload, headers=api_headers)
        assert create_res.status_code == 200
        created = create_res.json()
        assert created["slug"] == "south-china-sea-maritime"
        assert created["importance_threshold"] == 8
        assert created["alert_threshold"] == 8

        # 3. Retrieve theme by slug
        get_res = await client.get("/api/themes/south-china-sea-maritime", headers=api_headers)
        assert get_res.status_code == 200
        assert get_res.json()["name"] == theme_payload["name"]

        # 4. Update theme
        update_res = await client.put(
            "/api/themes/south-china-sea-maritime",
            json={"importance_threshold": 9, "description": "Updated description"},
            headers=api_headers,
        )
        assert update_res.status_code == 200
        assert update_res.json()["importance_threshold"] == 9
        assert update_res.json()["description"] == "Updated description"

        # 5. Activate theme
        act_res = await client.post("/api/themes/south-china-sea-maritime/activate", headers=api_headers)
        assert act_res.status_code == 200
        assert act_res.json()["status"] == "activated"
        assert act_res.json()["active_theme_id"] == "south-china-sea-maritime"

        # Verify it is now active
        active_res = await client.get("/api/themes/active", headers=api_headers)
        assert active_res.status_code == 200
        assert active_res.json()["slug"] == "south-china-sea-maritime"
        assert active_res.json()["is_active"] is True

        # 6. Delete theme
        del_res = await client.delete("/api/themes/south-china-sea-maritime", headers=api_headers)
        assert del_res.status_code == 200
        assert del_res.json()["status"] == "deleted"

        # Verify 404
        assert (await client.get("/api/themes/south-china-sea-maritime", headers=api_headers)).status_code == 404


@pytest.mark.asyncio
async def test_theme_lookup_variations(api_headers):
    """Verify theme can be looked up by slug, numeric ID, or name."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Create theme
        t_data = {
            "name": f"Taiwan Strait Crisis {time.time()}",
            "slug": f"taiwan-strait-{int(time.time())}",
            "scoring_prompt": "Scoring prompt for Taiwan Strait.",
            "alert_threshold": 7,
        }
        res = await client.post("/api/themes", json=t_data, headers=api_headers)
        assert res.status_code == 200
        created = res.json()
        db_id = created["db_id"]
        slug = created["slug"]

        # Lookup by slug
        res_slug = await client.get(f"/api/themes/{slug}", headers=api_headers)
        assert res_slug.status_code == 200

        # Lookup by numeric ID
        res_num = await client.get(f"/api/themes/{db_id}", headers=api_headers)
        assert res_num.status_code == 200

        # Lookup by name (space separated)
        res_name = await client.get(f"/api/themes/{slug.replace('-', ' ')}", headers=api_headers)
        assert res_name.status_code == 200


# ==============================================================================
# 3. SOURCES REST API TESTS
# ==============================================================================

@pytest.mark.asyncio
async def test_sources_crud_and_controls(api_headers):
    """Test full sources lifecycle: create, list, get, toggle, trigger, delete."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Add source
        src_payload = {
            "name": "Al Jazeera RSS Stream",
            "source_type": "rss",
            "target": "https://www.aljazeera.com/xml/rss/all.xml",
            "interval_seconds": 180,
            "enabled": True,
        }
        res = await client.post("/api/sources", json=src_payload, headers=api_headers)
        assert res.status_code == 200
        src = res.json()
        src_id = src["id"]
        assert src["target"] == src_payload["target"]
        assert src["interval_seconds"] == 180
        assert src["enabled"] is True
        assert src["status"] == "active"

        # 2. Get source by ID
        get_res = await client.get(f"/api/sources/{src_id}", headers=api_headers)
        assert get_res.status_code == 200
        assert get_res.json()["name"] == src_payload["name"]

        # 3. List sources
        list_res = await client.get("/api/sources?source_type=rss", headers=api_headers)
        assert list_res.status_code == 200
        assert any(s["id"] == src_id for s in list_res.json())

        # 4. Trigger while enabled -> should succeed
        trig_res = await client.post(f"/api/sources/{src_id}/trigger", headers=api_headers)
        assert trig_res.status_code == 200
        assert trig_res.json()["status"] == "triggered"
        assert trig_res.json()["timestamp"] is not None

        # 5. Disable source
        toggle_res = await client.put(f"/api/sources/{src_id}", json={"enabled": False}, headers=api_headers)
        assert toggle_res.status_code == 200
        assert toggle_res.json()["enabled"] is False

        # 6. Trigger while disabled -> MUST return 400 Bad Request
        trig_fail = await client.post(f"/api/sources/{src_id}/trigger", headers=api_headers)
        assert trig_fail.status_code == 400
        assert "Cannot trigger disabled source" in trig_fail.json()["detail"]

        # 7. Update status and error_message
        update_status = await client.put(
            f"/api/sources/{src_id}",
            json={"status": "degraded", "error_message": "Rate limit exceeded"},
            headers=api_headers,
        )
        assert update_status.status_code == 200
        assert update_status.json()["status"] == "degraded"
        assert update_status.json()["error_message"] == "Rate limit exceeded"

        # 8. Delete source
        del_res = await client.delete(f"/api/sources/{src_id}", headers=api_headers)
        assert del_res.status_code == 200
        assert del_res.json()["status"] == "deleted"

        # 9. Verify 404
        assert (await client.get(f"/api/sources/{src_id}", headers=api_headers)).status_code == 404


# ==============================================================================
# 4. EVENTS REST API TESTS
# ==============================================================================

@pytest.mark.asyncio
async def test_events_ingest_and_timeline_filtering(api_headers):
    """Test event ingestion, deduplication, and timeline filtering."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Ingest events with different scores
        evt1 = {
            "id": f"evt-t1-{time.time()}",
            "title": "Low Priority Statement on Diplomatic Talks",
            "summary": "Officials discussed bilateral relations.",
            "raw_text": "Diplomatic delegation held scheduled meetings in Geneva.",
            "source_type": "rss",
            "source_name": "Reuters",
            "importance_score": 4,
            "external_id": f"ext-1-{time.time()}",
            "published_at": utc_now().isoformat(),
        }
        evt2 = {
            "id": f"evt-t2-{time.time()}",
            "title": "Kinetic Interception in Strait of Hormuz",
            "summary": "Naval forces intercepted armed drone boat.",
            "raw_text": "Commercial vessel was targeted by kinetic drone.",
            "source_type": "telegram",
            "source_name": "@OSINTdefender",
            "importance_score": 9,
            "external_id": f"ext-2-{time.time()}",
            "published_at": utc_now().isoformat(),
        }

        res1 = await client.post("/api/events", json=evt1, headers=api_headers)
        assert res1.status_code == 200
        assert res1.json()["id"] == evt1["id"]

        res2 = await client.post("/api/events", json=evt2, headers=api_headers)
        assert res2.status_code == 200
        assert res2.json()["id"] == evt2["id"]

        # Deduplication check: re-posting same (theme, external_id) returns existing
        res_dup = await client.post("/api/events", json=evt2, headers=api_headers)
        assert res_dup.status_code == 200
        assert res_dup.json()["id"] == evt2["id"]

        # Filter by min_score=8 -> should include evt2, exclude evt1
        filter_high = await client.get("/api/events?min_score=8", headers=api_headers)
        assert filter_high.status_code == 200
        high_ids = [e["id"] for e in filter_high.json()]
        assert evt2["id"] in high_ids
        assert evt1["id"] not in high_ids

        # Filter by source_type=telegram
        filter_tg = await client.get("/api/events?source_type=telegram", headers=api_headers)
        assert filter_tg.status_code == 200
        tg_ids = [e["id"] for e in filter_tg.json()]
        assert evt2["id"] in tg_ids

        # Text search for 'Kinetic'
        filter_search = await client.get("/api/events?search=Kinetic", headers=api_headers)
        assert filter_search.status_code == 200
        search_ids = [e["id"] for e in filter_search.json()]
        assert evt2["id"] in search_ids

        # Pagination limit=1
        page_res = await client.get("/api/events?limit=1", headers=api_headers)
        assert page_res.status_code == 200
        assert len(page_res.json()) <= 1


# ==============================================================================
# 5. STATS REST API TESTS
# ==============================================================================

@pytest.mark.asyncio
async def test_stats_endpoint(api_headers):
    """Test dashboard metrics and visual analytics aggregations."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/api/stats", headers=api_headers)
        assert res.status_code == 200
        stats = res.json()

        assert "total_events" in stats
        assert "high_priority_events" in stats
        assert "active_sources" in stats
        assert "last_updated" in stats
        assert "score_distribution" in stats
        assert "source_breakdown" in stats
        assert "hourly_volume" in stats

        # Assert score distribution buckets
        assert "1-3" in stats["score_distribution"]
        assert "4-6" in stats["score_distribution"]
        assert "7-8" in stats["score_distribution"]
        assert "9-10" in stats["score_distribution"]

        # Assert source breakdown keys
        assert "rss" in stats["source_breakdown"]


# ==============================================================================
# 6. TELEGRAM NOTIFICATION BOT TESTS
# ==============================================================================

@pytest.mark.asyncio
async def test_telegram_message_formatting():
    """Verify Telegram alert formatting adheres to exact Markdown template."""
    bot = TelegramBotService(rate_limiting_enabled=False)
    pub_time = datetime(2026, 10, 7, 12, 30, 0, tzinfo=timezone.utc)
    payload = TelegramAlertPayload(
        event_id="test-evt-001",
        title="Commercial Tanker Boarded",
        summary="Armed boarding party seized commercial tanker in Strait of Hormuz.",
        importance_score=8,
        source_name="Al Jazeera English",
        published_at=pub_time,
        source_url="https://aljazeera.com/test-article",
    )

    msg = bot.format_message(payload)
    assert "🚨 *OSINT CRITICAL ALERT* [8/10]" in msg
    assert "*Summary:* Armed boarding party seized commercial tanker in Strait of Hormuz." in msg
    assert "*Source:* Al Jazeera English" in msg
    assert "*Time:* 2026-10-07 12:30:00 UTC" in msg
    assert "*Link:* https://aljazeera.com/test-article" in msg


@pytest.mark.asyncio
async def test_telegram_link_na_fallback():
    """Verify missing source_url falls back cleanly to 'N/A'."""
    bot = TelegramBotService(rate_limiting_enabled=False)
    payload = TelegramAlertPayload(
        event_id="test-evt-002",
        title="No Link Report",
        summary="Summary without URL.",
        importance_score=7,
        source_name="Local Wire",
        published_at=utc_now(),
        source_url=None,
    )
    msg = bot.format_message(payload)
    assert "*Link:* N/A" in msg


@pytest.mark.asyncio
async def test_telegram_cooldown_throttling_and_score_10_bypass():
    """
    Verify:
    1. First alert is delivered.
    2. Second alert within 30s is throttled (score < 10).
    3. Third alert with score=10 BYPASSES cooldown immediately!
    """
    bot = TelegramBotService(
        bot_token=None,
        chat_id=None,
        rate_limit_seconds=30.0,
        rate_limiting_enabled=True,
    )

    now = utc_now()
    # 1. First alert (score 8) -> delivered
    payload1 = TelegramAlertPayload(
        event_id="alert-1",
        title="Event 1",
        summary="First critical event.",
        importance_score=8,
        source_name="RSS",
        published_at=now,
        source_url="https://example.com/1",
    )
    res1 = await bot.send_alert(payload1)
    assert res1["status"] == "delivered"

    # 2. Second alert (score 8) immediately after -> throttled!
    payload2 = TelegramAlertPayload(
        event_id="alert-2",
        title="Event 2",
        summary="Second critical event.",
        importance_score=8,
        source_name="RSS",
        published_at=now,
        source_url="https://example.com/2",
    )
    res2 = await bot.send_alert(payload2)
    assert res2["status"] == "throttled"
    assert res2["reason"] == "rate_limit_cooldown"

    # 3. Third alert with score=10 -> CRITICAL BYPASS delivered immediately!
    payload3 = TelegramAlertPayload(
        event_id="alert-3",
        title="Event 3",
        summary="Max threat breakout engagement.",
        importance_score=10,
        source_name="RSS",
        published_at=now,
        source_url="https://example.com/3",
    )
    res3 = await bot.send_alert(payload3)
    assert res3["status"] == "delivered"


@pytest.mark.asyncio
async def test_telegram_24h_deduplication():
    """Verify duplicate alerts for identical URL within 24 hours are suppressed."""
    bot = TelegramBotService(rate_limiting_enabled=False)

    payload = TelegramAlertPayload(
        event_id="alert-dup-1",
        title="Breaking News Article",
        summary="Story reported.",
        importance_score=8,
        source_name="Reuters",
        published_at=utc_now(),
        source_url="https://reuters.com/world/breaking-story",
    )

    # First dispatch -> delivered
    res1 = await bot.send_alert(payload)
    assert res1["status"] == "delivered"

    # Second dispatch with identical URL -> duplicate!
    payload_clone = TelegramAlertPayload(
        event_id="alert-dup-2",
        title="Breaking News Article",
        summary="Story reported again.",
        importance_score=8,
        source_name="Reuters",
        published_at=utc_now(),
        source_url="https://reuters.com/world/breaking-story",
    )
    res2 = await bot.send_alert(payload_clone)
    assert res2["status"] == "duplicate"
    assert res2["reason"] == "dedup_window_active"


@pytest.mark.asyncio
async def test_telegram_missing_token_resilience():
    """Verify bot does not crash when tokens are missing and logs simulated dispatch."""
    bot = TelegramBotService(bot_token=None, chat_id=None, rate_limiting_enabled=False)
    payload = TelegramAlertPayload(
        event_id="resilience-evt",
        title="Resilience Test",
        summary="Testing missing credentials.",
        importance_score=8,
        source_name="Wire",
        published_at=utc_now(),
    )
    res = await bot.send_alert(payload)
    assert res["status"] == "delivered"
    assert res.get("simulated") is True
    assert len(bot.sent_messages) == 1


@pytest.mark.asyncio
async def test_telegram_error_simulations():
    """Verify error simulations for boundary and resilience verification."""
    bot = TelegramBotService()
    payload = TelegramAlertPayload(
        event_id="sim-evt",
        title="Error Sim",
        summary="Simulating errors.",
        importance_score=8,
        source_name="Wire",
        published_at=utc_now(),
    )

    # Invalid token simulation
    bot.simulate_error = "invalid_token"
    with pytest.raises(ValueError, match="Unauthorized / Invalid Token"):
        await bot.send_alert(payload)

    # Chat not found simulation
    bot.simulate_error = "chat_not_found"
    with pytest.raises(LookupError, match="Chat not found"):
        await bot.send_alert(payload)

    # Flood wait simulation
    bot.simulate_error = "flood_wait"
    with pytest.raises(RuntimeError, match="FloodWaitError"):
        await bot.send_alert(payload)


@pytest.mark.asyncio
async def test_test_websocket_logs_endpoint(api_headers):
    """Verify /test/websocket-logs endpoint returns recorded broadcast entries."""
    clear_broadcast_logs()
    await handle_bus_event_new("osint:events:new", {"id": "test-ws-log-evt"})

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/test/websocket-logs")
        assert res.status_code == 200
        logs = res.json()
        assert len(logs) >= 1
        assert logs[-1]["type"] == "event:new"
        assert logs[-1]["payload"]["id"] == "test-ws-log-evt"
