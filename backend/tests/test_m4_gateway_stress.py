"""
Adversarial Stress and Edge-Case Test Suite for Milestone 4:
Real-Time Gateway (backend/app/services/socket_server.py)

Tests cover:
1. Authentication bypass attempts (empty tokens, whitespace, malformed types, invalid keys, header attacks).
2. High concurrent connection simulation & rapid connect-disconnect churn (100+ clients, zero deadlocks).
3. Rapid broadcast floods and memory bounds verification (<100 ring buffer bounds, RSS leak checks).
4. Room subscriptions, room isolation, and malformed room payloads.
5. Lifecycle re-entrancy, idempotency, and Redis bus integration.
6. Full ASGI Engine.IO polling handshake verification via HTTP transport.
"""

import asyncio
from collections import deque
import gc
import json
import os
import psutil
import time
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
import socketio

from backend.app.config import get_settings
from backend.app.main import app
from backend.app.services.redis_bus import (
    CHANNEL_CONFIG_RELOAD,
    CHANNEL_EVENTS_ALERT,
    CHANNEL_EVENTS_NEW,
    CHANNEL_STATS_UPDATE,
    RedisBus,
    get_redis_bus,
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


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


@pytest_asyncio.fixture(autouse=True)
async def cleanup_socket_state():
    """Ensure socket server state is cleanly reset before and after each test."""
    clear_broadcast_logs()
    _active_clients.clear()
    yield
    clear_broadcast_logs()
    _active_clients.clear()


# ==============================================================================
# SECTION 1: AUTHENTICATION BYPASS ATTEMPTS & ADVERSARIAL EDGE CASES
# ==============================================================================

@pytest.mark.asyncio
async def test_auth_bypass_empty_and_whitespace_tokens():
    """
    Adversarial challenge: Attempt authentication bypass using empty,
    whitespace-only, or falsy tokens across auth dict, query string, and headers.
    All attempts MUST be strictly rejected with ConnectionRefusedError.
    """
    settings = get_settings()
    configured_key = settings.API_KEY
    assert configured_key, "Test requires a configured API_KEY in settings"

    # 1. Empty and whitespace auth dict candidates
    empty_auth_payloads = [
        {"token": ""},
        {"api_key": ""},
        {"apiKey": ""},
        {"token": "   "},
        {"api_key": "\t\r\n"},
        {"apiKey": "  \n  "},
        {},
    ]
    for auth in empty_auth_payloads:
        with pytest.raises(socketio.exceptions.ConnectionRefusedError):
            await socket_connect(f"sid_empty_{id(auth)}", {"QUERY_STRING": ""}, auth)

    # 2. Empty query string keys
    empty_query_strings = [
        "token=",
        "api_key=",
        "apiKey=",
        "key=",
        "token=&api_key=&apiKey=",
        "token=%20%20%20",
        "key=%09%0A",
        "",
        "foo=bar&baz=qux",
    ]
    for qs in empty_query_strings:
        with pytest.raises(socketio.exceptions.ConnectionRefusedError):
            await socket_connect(f"sid_qs_{id(qs)}", {"QUERY_STRING": qs}, None)

    # 3. Empty or malformed HTTP headers
    malformed_headers = [
        {"HTTP_X_API_KEY": ""},
        {"HTTP_X_API_KEY": "   "},
        {"HTTP_AUTHORIZATION": ""},
        {"HTTP_AUTHORIZATION": "Bearer"},
        {"HTTP_AUTHORIZATION": "Bearer "},
        {"HTTP_AUTHORIZATION": "Bearer    "},
        {"HTTP_AUTHORIZATION": "Basic dXNlcjpwYXNz"},
        {"HTTP_AUTHORIZATION": "Token secret_key"},
    ]
    for hdr in malformed_headers:
        environ = {**hdr, "QUERY_STRING": ""}
        with pytest.raises(socketio.exceptions.ConnectionRefusedError):
            await socket_connect(f"sid_hdr_{id(hdr)}", environ, None)

    assert get_active_client_count() == 0


@pytest.mark.asyncio
async def test_auth_bypass_malformed_and_exotic_payload_types():
    """
    Adversarial challenge: Supply malformed data types (int, list, bool, None, nested dict)
    into auth parameter and environ headers to probe for unhandled exceptions or leaks.
    """
    settings = get_settings()

    malformed_auth_objects = [
        12345,
        True,
        False,
        ["token", "api_key"],
        ("tuple_token",),
        {"token": 99999},
        {"token": True},
        {"token": None},
        {"token": []},
        {"token": {}},
        {"token": {"nested": settings.API_KEY}},
        {"unrelated_key": settings.API_KEY},
    ]

    for malformed in malformed_auth_objects:
        with pytest.raises(socketio.exceptions.ConnectionRefusedError):
            await socket_connect(f"sid_malformed_{id(malformed)}", {"QUERY_STRING": ""}, malformed)

    assert get_active_client_count() == 0


@pytest.mark.asyncio
async def test_auth_injection_and_hostile_strings():
    """
    Adversarial challenge: Inject SQLi, directory traversal, null bytes,
    and massive string buffers into auth credentials. Must safely reject
    without unhandled crashes or hanging.
    """
    hostile_tokens = [
        "'; DROP TABLE events; --",
        "' OR '1'='1",
        "../../../../etc/passwd",
        "\x00nullbyte\x00",
        "dev-secret-key-123\x00extra",
        "A" * 65536,  # 64 KB oversized string
        "<script>alert(1)</script>",
        "🔑🔓💥🔥",
    ]

    for token in hostile_tokens:
        with pytest.raises(socketio.exceptions.ConnectionRefusedError):
            await socket_connect(
                f"sid_hostile_{id(token)}",
                {"QUERY_STRING": f"token={token}"},
                {"token": token},
            )

    assert get_active_client_count() == 0


@pytest.mark.asyncio
async def test_auth_valid_permutations_and_precedence():
    """
    Verify that all standard valid auth vectors succeed, and that auth dict
    takes precedence over query parameters.
    """
    settings = get_settings()
    valid_key = settings.API_KEY

    # Valid in auth dict via 'token', 'api_key', 'apiKey'
    for k in ("token", "api_key", "apiKey"):
        sid = f"sid_valid_auth_{k}"
        await socket_connect(sid, {"QUERY_STRING": ""}, {k: valid_key})
        assert sid in _active_clients

    # Valid in query string via 'token', 'api_key', 'apiKey', 'key'
    for qk in ("token", "api_key", "apiKey", "key"):
        sid = f"sid_valid_qs_{qk}"
        await socket_connect(sid, {"QUERY_STRING": f"{qk}={valid_key}"}, None)
        assert sid in _active_clients

    # Valid in HTTP headers
    sid_hdr = "sid_valid_hdr_xkey"
    await socket_connect(sid_hdr, {"HTTP_X_API_KEY": valid_key, "QUERY_STRING": ""}, None)
    assert sid_hdr in _active_clients

    sid_bearer = "sid_valid_hdr_bearer"
    await socket_connect(sid_bearer, {"HTTP_AUTHORIZATION": f"Bearer {valid_key}", "QUERY_STRING": ""}, None)
    assert sid_bearer in _active_clients

    # Precedence: auth dict with invalid key overrides valid query string -> must reject
    with pytest.raises(socketio.exceptions.ConnectionRefusedError):
        await socket_connect(
            "sid_precedence_test",
            {"QUERY_STRING": f"token={valid_key}"},
            {"token": "invalid_override_token"},
        )


# ==============================================================================
# SECTION 2: CONCURRENT CONNECTION CHURN & CLIENT TRACKING STRESS
# ==============================================================================

@pytest.mark.asyncio
async def test_concurrent_connections_burst():
    """
    Stress-test: Simulate 100 concurrent clients authenticating simultaneously.
    Verify tracking count, timestamp integrity, and zero race conditions.
    """
    settings = get_settings()
    num_clients = 100

    async def connect_client(index: int):
        sid = f"client_burst_{index}"
        environ = {"QUERY_STRING": f"token={settings.API_KEY}"}
        await socket_connect(sid, environ, None)

    # Launch 100 concurrent connections
    await asyncio.gather(*(connect_client(i) for i in range(num_clients)))

    assert get_active_client_count() == num_clients
    assert len(_active_clients) == num_clients

    # Verify all have valid timestamps
    for i in range(num_clients):
        sid = f"client_burst_{i}"
        assert sid in _active_clients
        assert "connected_at" in _active_clients[sid]
        assert isinstance(_active_clients[sid]["connected_at"], float)


@pytest.mark.asyncio
async def test_rapid_connect_disconnect_churn():
    """
    Stress-test: Simulate rapid interleaved connect and disconnect churn across
    200 clients. Ensures dictionary modifications have no concurrency exceptions,
    deadlocks, or leaked tracking state.
    """
    settings = get_settings()
    total_clients = 200

    # 1. Connect initial batch of 100
    for i in range(100):
        await socket_connect(f"churn_sid_{i}", {"QUERY_STRING": f"token={settings.API_KEY}"}, None)
    assert get_active_client_count() == 100

    # 2. Interleave disconnecting first 50 while connecting next 100
    disconnect_tasks = [socket_disconnect(f"churn_sid_{i}") for i in range(50)]
    connect_tasks = [
        socket_connect(f"churn_sid_{i}", {"QUERY_STRING": f"token={settings.API_KEY}"}, None)
        for i in range(100, total_clients)
    ]

    await asyncio.gather(*(disconnect_tasks + connect_tasks))

    # Expect: 100 - 50 + 100 = 150 active clients
    assert get_active_client_count() == 150
    assert len(_active_clients) == 150

    # 3. Disconnect remaining 150
    remaining_sids = list(_active_clients.keys())
    await asyncio.gather(*(socket_disconnect(sid) for sid in remaining_sids))

    assert get_active_client_count() == 0
    assert len(_active_clients) == 0


@pytest.mark.asyncio
async def test_disconnect_idempotency_and_unknown_sids():
    """
    Adversarial test: Repeatedly disconnect unknown, empty, or already disconnected SIDs.
    Must silently succeed without KeyError or state corruption.
    """
    # Disconnect non-existent SIDs
    await socket_disconnect("non_existent_sid_1")
    await socket_disconnect("non_existent_sid_2")
    await socket_disconnect("")

    # Connect one client, disconnect twice
    settings = get_settings()
    await socket_connect("sid_twice", {"QUERY_STRING": f"token={settings.API_KEY}"}, None)
    assert get_active_client_count() == 1

    await socket_disconnect("sid_twice")
    assert get_active_client_count() == 0

    # Second disconnect must not raise
    await socket_disconnect("sid_twice")
    assert get_active_client_count() == 0


# ==============================================================================
# SECTION 3: BROADCAST FLOODS & MEMORY BOUNDS (RING BUFFER STRESS)
# ==============================================================================

@pytest.mark.asyncio
async def test_broadcast_flood_ring_buffer_exact_cap():
    """
    Stress-test: Flood 5,000 events rapidly through broadcast handlers.
    Strictly verify that:
    1. _broadcast_log maxlen=100 is strictly respected.
    2. Exactly 100 items remain.
    3. Oldest items are evicted in strict FIFO order (no index corruption).
    """
    clear_broadcast_logs()
    assert len(get_broadcast_logs()) == 0

    total_events = 5000
    with patch.object(sio, "emit", new_callable=AsyncMock) as mock_emit:
        for i in range(total_events):
            await handle_bus_event_new("osint:events:new", {"id": f"event_{i}", "seq": i})

        assert mock_emit.await_count == total_events

    logs = get_broadcast_logs()
    assert len(logs) == 100
    # First item must be event_4900 (5000 - 100)
    assert logs[0]["payload"]["id"] == "event_4900"
    assert logs[0]["payload"]["seq"] == 4900
    # Last item must be event_4999
    assert logs[-1]["payload"]["id"] == "event_4999"
    assert logs[-1]["payload"]["seq"] == 4999


@pytest.mark.asyncio
async def test_broadcast_flood_mixed_event_types():
    """
    Stress-test: Interleave 3,000 broadcasts of all 3 bus channels:
    'event:new', 'alert:critical', and 'stats:update'.
    Verify correct tagging and ring buffer boundedness.
    """
    clear_broadcast_logs()

    with patch.object(sio, "emit", new_callable=AsyncMock):
        for i in range(1000):
            await handle_bus_event_new("osint:events:new", {"id": f"evt_{i}"})
            await handle_bus_event_alert("osint:events:alert", {"event_id": f"alt_{i}", "importance_score": 9})
            await handle_bus_stats_update("osint:stats:update", {"tick": i, "active": 10})

    logs = get_broadcast_logs()
    assert len(logs) == 100
    types = {log["type"] for log in logs}
    assert "event:new" in types
    assert "alert:critical" in types
    assert "stats:update" in types


@pytest.mark.asyncio
async def test_broadcast_alert_id_normalization_robustness():
    """
    Edge-case test: Verify handle_bus_event_alert normalizes id and event_id
    across diverse malformed payloads without crashing.
    """
    clear_broadcast_logs()
    test_cases = [
        ({"id": "only_id"}, "only_id", "only_id"),
        ({"event_id": "only_event_id"}, "only_event_id", "only_event_id"),
        ({"id": "both_id", "event_id": "both_event_id"}, "both_id", "both_event_id"),
        ({}, None, None),
        ("plain_string_payload", None, None),
        (12345, None, None),
    ]

    with patch.object(sio, "emit", new_callable=AsyncMock) as mock_emit:
        for payload, expected_id, expected_event_id in test_cases:
            await handle_bus_event_alert("osint:events:alert", payload)

    logs = get_broadcast_logs()
    assert len(logs) == len(test_cases)
    for idx, (payload, exp_id, exp_event_id) in enumerate(test_cases):
        p = logs[idx]["payload"]
        if isinstance(payload, dict) and exp_id:
            assert p.get("id") == exp_id
            assert p.get("event_id") == exp_event_id


@pytest.mark.asyncio
async def test_broadcast_memory_rss_stability():
    """
    Memory boundary stress-test:
    Flood 10,000 events with 5KB payload size each.
    Verify that Python GC reclaims evicted items and RSS does not grow uncontrollably.
    Target memory ceiling: backend must stay well within the <180MB budget.
    """
    process = psutil.Process(os.getpid())
    gc.collect()
    rss_before_mb = process.memory_info().rss / (1024 * 1024)

    # 5KB payload string
    large_payload_chunk = "X" * 5000

    with patch.object(sio, "emit", new_callable=AsyncMock):
        for i in range(10000):
            payload = {
                "id": f"large_evt_{i}",
                "content": large_payload_chunk,
                "timestamp": time.time(),
            }
            await handle_bus_event_new("osint:events:new", payload)

    gc.collect()
    rss_after_mb = process.memory_info().rss / (1024 * 1024)
    rss_growth = rss_after_mb - rss_before_mb

    logs = get_broadcast_logs()
    assert len(logs) == 100

    # Ensure memory growth is tightly bounded (less than 25MB RSS increase)
    assert rss_growth < 25.0, f"Memory leak detected: RSS grew by {rss_growth:.2f} MB"


# ==============================================================================
# SECTION 4: ROOM SUBSCRIPTION & ROOM ISOLATION
# ==============================================================================

@pytest.mark.asyncio
async def test_room_subscription_and_unsubscription():
    """
    Verify room subscription and unsubscription correctly calls enter_room/leave_room
    with formatted room name 'theme:<theme_id>'.
    """
    with patch.object(sio, "enter_room", new_callable=AsyncMock) as mock_enter, \
         patch.object(sio, "leave_room", new_callable=AsyncMock) as mock_leave:

        await subscribe_theme("sid_room_test", {"theme_id": "china-taiwan-strait"})
        mock_enter.assert_awaited_once_with("sid_room_test", "theme:china-taiwan-strait")

        await unsubscribe_theme("sid_room_test", {"theme_id": "china-taiwan-strait"})
        mock_leave.assert_awaited_once_with("sid_room_test", "theme:china-taiwan-strait")


@pytest.mark.asyncio
async def test_room_subscription_adversarial_payloads():
    """
    Adversarial test: Send non-dict, missing, empty, or malicious room names.
    Verify that invalid payloads (empty, non-dict, None) do not call enter_room/leave_room,
    and valid payload calls enter_room/leave_room with 'theme:<theme_id>'.
    """
    with patch.object(sio, "enter_room", new_callable=AsyncMock) as mock_enter, \
         patch.object(sio, "leave_room", new_callable=AsyncMock) as mock_leave:

        # 1. Non-dict or empty/falsy payloads should be ignored (no enter_room called)
        malformed_room_inputs = [
            None,
            "string_instead_of_dict",
            12345,
            {},
            {"theme_id": ""},
            {"theme_id": None},
            {"other_key": "val"},
        ]

        for data in malformed_room_inputs:
            await subscribe_theme("sid_test", data)
            await unsubscribe_theme("sid_test", data)

        assert mock_enter.await_count == 0
        assert mock_leave.await_count == 0

        # 2. String theme_id correctly joins room
        await subscribe_theme("sid_test", {"theme_id": "taiwan_crisis"})
        mock_enter.assert_awaited_once_with("sid_test", "theme:taiwan_crisis")

        await unsubscribe_theme("sid_test", {"theme_id": "taiwan_crisis"})
        mock_leave.assert_awaited_once_with("sid_test", "theme:taiwan_crisis")


# ==============================================================================
# SECTION 5: LIFECYCLE RE-ENTRANCY & BUS INTEGRATION
# ==============================================================================

@pytest.mark.asyncio
async def test_lifecycle_idempotency_and_reinitialization():
    """
    Verify that init_socket_server() and shutdown_socket_server() are
    fully re-entrant and idempotent.
    """
    bus = get_redis_bus()

    # Repeated inits should not duplicate subscriptions or fail
    await init_socket_server(bus)
    await init_socket_server(bus)

    # Seed client and verify active
    settings = get_settings()
    await socket_connect("sid_life_1", {"QUERY_STRING": f"token={settings.API_KEY}"}, None)
    assert get_active_client_count() == 1

    # Shutdown clears clients and unsubscribes
    await shutdown_socket_server(bus)
    assert get_active_client_count() == 0

    # Repeated shutdown should succeed cleanly
    await shutdown_socket_server(bus)

    # Re-initialization succeeds cleanly
    await init_socket_server(bus)
    await shutdown_socket_server(bus)


@pytest.mark.asyncio
async def test_redis_bus_to_socket_live_flow():
    """
    Test real message flow: publish via RedisBus in in-memory fallback mode
    and verify Socket.IO handlers receive, emit, and log the events.
    """
    clear_broadcast_logs()
    bus = get_redis_bus()
    await init_socket_server(bus)

    with patch.object(sio, "emit", new_callable=AsyncMock) as mock_emit:
        # 1. Publish new event
        test_new_event = {"id": "bus_evt_101", "title": "Strait Patrol Launched"}
        await bus.publish(CHANNEL_EVENTS_NEW, test_new_event)
        await asyncio.sleep(0.05)  # allow in-memory task to dispatch

        # 2. Publish alert
        test_alert = {"event_id": "bus_alt_202", "importance_score": 10}
        await bus.publish(CHANNEL_EVENTS_ALERT, test_alert)
        await asyncio.sleep(0.05)

        # 3. Publish stats
        test_stats = {"total_events": 42}
        await bus.publish(CHANNEL_STATS_UPDATE, test_stats)
        await asyncio.sleep(0.05)

    logs = get_broadcast_logs()
    assert len(logs) == 3
    assert logs[0]["type"] == "event:new"
    assert logs[0]["payload"]["id"] == "bus_evt_101"
    assert logs[1]["type"] == "alert:critical"
    assert logs[1]["payload"]["id"] == "bus_alt_202"
    assert logs[2]["type"] == "stats:update"
    assert logs[2]["payload"]["total_events"] == 42

    await shutdown_socket_server(bus)


# ==============================================================================
# SECTION 6: ASGI MOUNT & ENGINE.IO TRANSPORT HANDSHAKE VIA HTTP
# ==============================================================================

@pytest.mark.asyncio
async def test_asgi_socketio_http_handshake():
    """
    End-to-End ASGI test: Request Engine.IO polling handshake through FastAPI
    mount at /socket.io/. Verify rejected on missing/invalid token (packet 44 ConnectionRefused)
    and accepted with valid API key token (packet 40 CONNECT with active client tracking).
    """
    settings = get_settings()
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Unauthenticated connection attempt
        # Engine.IO handshake assigns transport SID
        r_unauth = await client.get("/socket.io/?EIO=4&transport=polling")
        assert r_unauth.status_code == 200
        unauth_sid = json.loads(r_unauth.text[1:])["sid"]

        # Client attempts Socket.IO connect (packet 40) without auth
        await client.post(f"/socket.io/?EIO=4&transport=polling&sid={unauth_sid}", content="40")
        poll_res = await client.get(f"/socket.io/?EIO=4&transport=polling&sid={unauth_sid}")
        # Must return packet 44 (CONNECT_ERROR) with Unauthorized message
        assert poll_res.text.startswith("44")
        assert "Unauthorized" in poll_res.text
        assert get_active_client_count() == 0

        # 2. Invalid API key token -> must also return packet 44
        r_bad = await client.get("/socket.io/?EIO=4&transport=polling&token=wrong_key_xyz")
        bad_sid = json.loads(r_bad.text[1:])["sid"]
        await client.post(f"/socket.io/?EIO=4&transport=polling&sid={bad_sid}", content="40")
        bad_poll = await client.get(f"/socket.io/?EIO=4&transport=polling&sid={bad_sid}")
        assert bad_poll.text.startswith("44")
        assert "Unauthorized" in bad_poll.text
        assert get_active_client_count() == 0

        # 3. Valid API key token via query param -> handshake succeeds with packet 40
        r_good = await client.get(f"/socket.io/?EIO=4&transport=polling&token={settings.API_KEY}")
        good_sid = json.loads(r_good.text[1:])["sid"]
        await client.post(f"/socket.io/?EIO=4&transport=polling&sid={good_sid}", content="40")
        good_poll = await client.get(f"/socket.io/?EIO=4&transport=polling&sid={good_sid}")
        assert good_poll.text.startswith("40")
        assert get_active_client_count() == 1

        # 4. Valid API key token via X-API-Key header -> handshake succeeds
        r_hdr = await client.get(
            "/socket.io/?EIO=4&transport=polling",
            headers={"X-API-Key": settings.API_KEY},
        )
        hdr_sid = json.loads(r_hdr.text[1:])["sid"]
        await client.post(
            f"/socket.io/?EIO=4&transport=polling&sid={hdr_sid}",
            content="40",
            headers={"X-API-Key": settings.API_KEY},
        )
        hdr_poll = await client.get(
            f"/socket.io/?EIO=4&transport=polling&sid={hdr_sid}",
            headers={"X-API-Key": settings.API_KEY},
        )
        assert hdr_poll.text.startswith("40")
        assert get_active_client_count() == 2
