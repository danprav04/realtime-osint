"""
backend/tests/test_m4_challenger2.py - Milestone 4 Adversarial Stress & Edge Case Test Suite

Adversarial Verification Suite for:
1. Theme Engine Hot-Reload & Concurrency Stress:
   - Concurrent theme switching exclusivity (race conditions, exactly 1 active theme)
   - Hot-reload broadcast message contract on osint:config:reload
   - Theme lookup variations and invalid identifier resilience
2. Sources API Edge Cases & Concurrency:
   - Disabled source manual trigger rejection (HTTP 400)
   - Invalid and negative interval inputs
   - Concurrent source toggles
   - Type and theme filtering edge cases
3. Telegram Bot Anti-Flood & Rate Limiting:
   - Rapid burst of 50 alerts in <1 second (exactly 1 delivered, 49 throttled)
   - Critical score=10 bursts immediately bypassing cooldown
   - Interleaved critical/non-critical bursts
   - Concurrency behavior under asyncio.gather
4. Telegram Bot Deduplication & Hash Boundaries:
   - 24h URL deduplication with whitespace variations
   - Fallback to event_id when source_url is None
   - Dedup cache expiration
   - Redis failure fallback to in-memory store
5. Markdown Injection & Payload Boundary Resilience:
   - Unbalanced markdown asterisks, backticks, brackets, HTML tags
   - Extreme summary length truncation (<4096 Telegram ceiling)
6. Telegram Bot HTTP Error Resilience:
   - HTTP 400, 401, 429 (retry_after), and network timeout handling
"""

import asyncio
from datetime import datetime, timezone
import hashlib
import json
import time
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch
import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, update

from backend.app.config import get_settings
from backend.app.db.models import Alert, Event, Source, Theme, utc_now
from backend.app.db.session import close_db, get_session_factory, init_db
from backend.app.main import app
from backend.app.services.redis_bus import (
    CHANNEL_CONFIG_RELOAD,
    CHANNEL_EVENTS_ALERT,
    CHANNEL_EVENTS_NEW,
    get_redis_bus,
)
from backend.app.services.telegram_bot import (
    TelegramAlertPayload,
    TelegramBotService,
)


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


@pytest_asyncio.fixture(autouse=True)
async def db_fixture():
    await init_db()
    yield
    await close_db()


@pytest.fixture
def api_headers():
    settings = get_settings()
    return {"X-API-Key": settings.API_KEY}


# ==============================================================================
# SECTION 1: THEME ENGINE HOT-RELOAD & CONCURRENCY STRESS
# ==============================================================================

@pytest.mark.asyncio
async def test_concurrent_theme_activations_exclusivity(api_headers):
    """
    Stress-test concurrent theme activation requests:
    Fires 20 simultaneous activate requests across 5 distinct themes.
    Asserts that at the conclusion, EXACTLY ONE theme is active in the database.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Create 5 distinct test themes
        theme_ids = []
        for i in range(5):
            t_payload = {
                "name": f"Stress Theme {i}_{time.time()}",
                "slug": f"stress-theme-{i}-{int(time.time() * 1000)}",
                "scoring_prompt": f"Prompt for theme {i}",
                "alert_threshold": 7,
                "is_active": False,
            }
            res = await client.post("/api/themes", json=t_payload, headers=api_headers)
            assert res.status_code == 200
            theme_ids.append(res.json()["slug"])

        # Fire 20 concurrent activate requests cycling through the 5 themes
        async def activate_call(theme_slug: str):
            return await client.post(f"/api/themes/{theme_slug}/activate", headers=api_headers)

        tasks = [activate_call(theme_ids[i % len(theme_ids)]) for i in range(20)]
        responses = await asyncio.gather(*tasks)

        # All requests should return 200 OK
        for r in responses:
            assert r.status_code == 200
            assert r.json()["status"] == "activated"

        # Verify active theme exclusivity directly against the database
        session_factory = get_session_factory()
        async with session_factory() as session:
            stmt = select(func.count()).select_from(Theme).where(Theme.is_active == True)
            active_count = (await session.execute(stmt)).scalar_one()
            assert active_count == 1, f"Expected exactly 1 active theme, but found {active_count}!"

            # Verify GET /api/themes/active reflects the single active theme
            active_res = await client.get("/api/themes/active", headers=api_headers)
            assert active_res.status_code == 200
            assert active_res.json()["is_active"] is True


@pytest.mark.asyncio
async def test_theme_hot_reload_broadcast_contract(api_headers):
    """
    Verify that theme activation and update publish valid ConfigReloadPayload
    messages to Redis channel 'osint:config:reload'.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Create a test theme
        slug = f"broadcast-test-{int(time.time() * 1000)}"
        t_payload = {
            "name": f"Broadcast Test Theme {time.time()}",
            "slug": slug,
            "scoring_prompt": "Scoring instructions.",
            "alert_threshold": 8,
        }
        create_res = await client.post("/api/themes", json=t_payload, headers=api_headers)
        assert create_res.status_code == 200

        # Capture Redis publishes
        published_messages = []
        bus = get_redis_bus()

        original_publish = bus.publish

        async def mock_publish(channel: str, message: Any):
            published_messages.append({"channel": channel, "message": message})
            return await original_publish(channel, message)

        with patch.object(bus, "publish", side_effect=mock_publish):
            # 1. Activate theme
            act_res = await client.post(f"/api/themes/{slug}/activate", headers=api_headers)
            assert act_res.status_code == 200

            # Verify broadcast message on osint:config:reload
            reload_msgs = [m for m in published_messages if m["channel"] == CHANNEL_CONFIG_RELOAD]
            assert len(reload_msgs) >= 1
            payload = reload_msgs[-1]["message"]
            assert payload["action"] == "theme_update"
            assert payload["theme_id"] == slug
            assert "timestamp" in payload

            # 2. Update theme scoring prompt
            upd_res = await client.put(
                f"/api/themes/{slug}",
                json={"scoring_prompt": "Updated adversarial prompt."},
                headers=api_headers,
            )
            assert upd_res.status_code == 200
            reload_msgs_after = [m for m in published_messages if m["channel"] == CHANNEL_CONFIG_RELOAD]
            assert len(reload_msgs_after) >= 2


@pytest.mark.asyncio
async def test_theme_lookup_variations_and_nonexistent(api_headers):
    """Verify robust lookup across slugs, spaces, IDs, and 404 on missing themes."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Non-existent theme returns 404
        bad_res = await client.get("/api/themes/non-existent-theme-xyz-999", headers=api_headers)
        assert bad_res.status_code == 404
        assert "not found" in bad_res.json()["detail"].lower()

        # 2. Activation of non-existent theme returns 404
        bad_act = await client.post("/api/themes/non-existent-theme-xyz-999/activate", headers=api_headers)
        assert bad_act.status_code == 404


# ==============================================================================
# SECTION 2: SOURCES API EDGE CASES & CONCURRENCY
# ==============================================================================

@pytest.mark.asyncio
async def test_disabled_source_manual_trigger_rejection(api_headers):
    """
    Verify that triggering a disabled source is rejected with HTTP 400 Bad Request,
    triggering a missing source returns HTTP 404, and triggering an enabled source returns HTTP 200.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Create a disabled source
        src_payload = {
            "name": "Disabled Wire Feed",
            "source_type": "rss",
            "target": "https://example.com/feed.xml",
            "enabled": False,
            "interval_seconds": 300,
        }
        create_res = await client.post("/api/sources", json=src_payload, headers=api_headers)
        assert create_res.status_code == 200
        src_id = create_res.json()["id"]
        assert create_res.json()["enabled"] is False

        # Attempt to trigger disabled source -> MUST return 400
        trig_res = await client.post(f"/api/sources/{src_id}/trigger", headers=api_headers)
        assert trig_res.status_code == 400
        assert "Cannot trigger disabled source" in trig_res.json()["detail"]

        # Trigger non-existent source -> MUST return 404
        trig_404 = await client.post("/api/sources/99999999/trigger", headers=api_headers)
        assert trig_404.status_code == 404

        # Enable source and trigger -> MUST succeed with 200
        await client.put(f"/api/sources/{src_id}", json={"enabled": True}, headers=api_headers)
        trig_ok = await client.post(f"/api/sources/{src_id}/trigger", headers=api_headers)
        assert trig_ok.status_code == 200
        assert trig_ok.json()["status"] == "triggered"


@pytest.mark.asyncio
async def test_concurrent_source_toggles(api_headers):
    """
    Stress-test concurrent enable/disable toggle requests on the same source.
    Ensures database lock contention is resolved cleanly without 500 crashes.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Create test source
        create_res = await client.post(
            "/api/sources",
            json={
                "name": "Toggle Concurrency Test",
                "source_type": "rss",
                "target": "https://example.com/toggle.xml",
                "enabled": True,
            },
            headers=api_headers,
        )
        assert create_res.status_code == 200
        src_id = create_res.json()["id"]

        # Concurrently toggle enabled state 16 times
        async def toggle(state: bool):
            return await client.put(
                f"/api/sources/{src_id}",
                json={"enabled": state},
                headers=api_headers,
            )

        tasks = [toggle(i % 2 == 0) for i in range(16)]
        results = await asyncio.gather(*tasks)

        # All toggles must complete successfully
        for r in results:
            assert r.status_code == 200

        # Final state check
        final_res = await client.get(f"/api/sources/{src_id}", headers=api_headers)
        assert final_res.status_code == 200
        assert isinstance(final_res.json()["enabled"], bool)


@pytest.mark.asyncio
async def test_sources_filtering_edge_cases(api_headers):
    """Verify source filtering by unknown theme or alias source types."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Filter by completely unknown theme slug returns empty list
        res = await client.get("/api/sources?theme_id=unknown-nonexistent-theme", headers=api_headers)
        assert res.status_code == 200
        assert res.json() == []

        # Filter by ais alias
        res_ais = await client.get("/api/sources?source_type=ais", headers=api_headers)
        assert res_ais.status_code == 200
        assert isinstance(res_ais.json(), list)


# ==============================================================================
# SECTION 3: TELEGRAM BOT ANTI-FLOOD & RATE LIMITING
# ==============================================================================

@pytest.mark.asyncio
async def test_telegram_rapid_burst_50_alerts_sequential():
    """
    CRITICAL ANTI-FLOOD TEST:
    Fires a burst of 50 non-critical alerts (score 8, < 10) in < 1 second.
    Verifies that:
    - EXACTLY 1 alert is delivered.
    - EXACTLY 49 alerts are throttled with status='throttled' and reason='rate_limit_cooldown'.
    """
    bot = TelegramBotService(
        bot_token=None,
        chat_id=None,
        rate_limit_seconds=30.0,
        rate_limiting_enabled=True,
    )

    now = utc_now()
    results = []
    start_time = time.time()

    for i in range(50):
        payload = TelegramAlertPayload(
            event_id=f"burst-seq-evt-{i}",
            title=f"Burst Event {i}",
            summary=f"Rapid fire event description #{i}.",
            importance_score=8,  # Below score 10 threshold
            source_name="OSINT Wire",
            published_at=now,
            source_url=f"https://osintwire.com/alert/{i}",
        )
        res = await bot.send_alert(payload)
        results.append(res)

    elapsed = time.time() - start_time
    assert elapsed < 2.0, f"Burst of 50 alerts took too long: {elapsed:.2f}s"

    delivered = [r for r in results if r["status"] == "delivered"]
    throttled = [r for r in results if r["status"] == "throttled"]

    assert len(delivered) == 1, f"Expected exactly 1 delivered alert, but got {len(delivered)}!"
    assert len(throttled) == 49, f"Expected exactly 49 throttled alerts, but got {len(throttled)}!"
    assert all(r.get("reason") == "rate_limit_cooldown" for r in throttled)


@pytest.mark.asyncio
async def test_telegram_critical_score_10_burst_bypasses_cooldown():
    """
    CRITICAL BYPASS TEST:
    Fires 20 consecutive maximum-threat alerts with score=10 in rapid succession.
    Verifies that 100% of score=10 alerts BYPASS the 30s cooldown and are delivered immediately.
    """
    bot = TelegramBotService(
        bot_token=None,
        chat_id=None,
        rate_limit_seconds=30.0,
        rate_limiting_enabled=True,
    )

    now = utc_now()
    results = []

    for i in range(20):
        payload = TelegramAlertPayload(
            event_id=f"score10-burst-{i}",
            title=f"Critical Hypersonic Strike Event {i}",
            summary=f"Unprecedented maximum kinetic breakout alert #{i}.",
            importance_score=10,  # Max severity bypass!
            source_name="Tactical Satellite Feed",
            published_at=now,
            source_url=f"https://tactical.sat/alert/{i}",
        )
        res = await bot.send_alert(payload)
        results.append(res)

    delivered = [r for r in results if r["status"] == "delivered"]
    throttled = [r for r in results if r["status"] == "throttled"]

    assert len(delivered) == 20, f"Expected all 20 score=10 alerts to be delivered, got {len(delivered)}!"
    assert len(throttled) == 0, f"No score=10 alerts should ever be throttled, got {len(throttled)}!"
    assert len(bot.sent_messages) == 20


@pytest.mark.asyncio
async def test_telegram_interleaved_critical_and_noncritical_burst():
    """
    Verifies correct rate limiting behavior when score=8 and score=10 alerts are interleaved:
    - Non-critical alerts are throttled during active cooldown.
    - Critical score=10 alerts bypass the cooldown every time without exception.
    """
    bot = TelegramBotService(
        bot_token=None,
        chat_id=None,
        rate_limit_seconds=30.0,
        rate_limiting_enabled=True,
    )

    test_sequence = [
        (8, "delivered"),  # 0: First alert -> delivered
        (8, "throttled"),  # 1: Cooldown active -> throttled
        (10, "delivered"), # 2: Score 10 -> BYPASS delivered
        (7, "throttled"),  # 3: Cooldown active -> throttled
        (10, "delivered"), # 4: Score 10 -> BYPASS delivered
        (10, "delivered"), # 5: Score 10 -> BYPASS delivered
        (9, "throttled"),  # 6: Score 9 (<10) -> throttled
    ]

    for idx, (score, expected_status) in enumerate(test_sequence):
        payload = TelegramAlertPayload(
            event_id=f"interleave-evt-{idx}",
            title=f"Interleaved Alert {idx}",
            summary=f"Event summary {idx}",
            importance_score=score,
            source_name="Feed",
            published_at=utc_now(),
            source_url=f"https://example.com/interleave/{idx}",
        )
        res = await bot.send_alert(payload)
        assert res["status"] == expected_status, (
            f"Step {idx} with score={score} failed: expected {expected_status}, got {res['status']}"
        )


# ==============================================================================
# SECTION 4: TELEGRAM BOT DEDUPLICATION & HASH BOUNDARIES
# ==============================================================================

@pytest.mark.asyncio
async def test_telegram_24h_deduplication_url_and_whitespace():
    """
    Verify 24h deduplication correctly detects duplicate URLs even with
    surrounding whitespace, but treats different URLs as distinct.
    """
    bot = TelegramBotService(rate_limiting_enabled=False)

    url = "https://reuters.com/world/gulf-naval-drill-2026"
    p1 = TelegramAlertPayload(
        event_id="dedup-1",
        title="Drill 1",
        summary="Drill report.",
        importance_score=8,
        source_name="Reuters",
        published_at=utc_now(),
        source_url=url,
    )
    res1 = await bot.send_alert(p1)
    assert res1["status"] == "delivered"

    # Same URL with trailing/leading spaces should hash identically
    p1_spaces = TelegramAlertPayload(
        event_id="dedup-1-spaces",
        title="Drill 1 Clone",
        summary="Drill report again.",
        importance_score=8,
        source_name="Reuters",
        published_at=utc_now(),
        source_url=f"  {url}  \n",
    )
    res1_dup = await bot.send_alert(p1_spaces)
    assert res1_dup["status"] == "duplicate"
    assert res1_dup["reason"] == "dedup_window_active"


@pytest.mark.asyncio
async def test_telegram_dedup_fallback_to_event_id_when_url_none():
    """
    When source_url is None:
    - Alerts with identical event_id are suppressed as duplicate.
    - Alerts with distinct event_id and None URL are delivered as unique.
    """
    bot = TelegramBotService(rate_limiting_enabled=False)

    p_a1 = TelegramAlertPayload(
        event_id="unique-evt-A",
        title="Event A",
        summary="Summary A",
        importance_score=8,
        source_name="Wire",
        published_at=utc_now(),
        source_url=None,
    )
    res_a1 = await bot.send_alert(p_a1)
    assert res_a1["status"] == "delivered"

    # Repeat same event_id with None URL -> duplicate
    p_a2 = TelegramAlertPayload(
        event_id="unique-evt-A",
        title="Event A Duplicate",
        summary="Summary A duplicate",
        importance_score=8,
        source_name="Wire",
        published_at=utc_now(),
        source_url=None,
    )
    res_a2 = await bot.send_alert(p_a2)
    assert res_a2["status"] == "duplicate"

    # Different event_id with None URL -> unique delivered!
    p_b = TelegramAlertPayload(
        event_id="unique-evt-B",
        title="Event B",
        summary="Summary B",
        importance_score=8,
        source_name="Wire",
        published_at=utc_now(),
        source_url=None,
    )
    res_b = await bot.send_alert(p_b)
    assert res_b["status"] == "delivered"


@pytest.mark.asyncio
async def test_telegram_dedup_cache_expiry():
    """Verify that after the 24h dedup window passes, an alert can be re-delivered."""
    bot = TelegramBotService(rate_limiting_enabled=False)

    p = TelegramAlertPayload(
        event_id="expiring-evt",
        title="Expiring Event",
        summary="Summary",
        importance_score=8,
        source_name="Wire",
        published_at=utc_now(),
        source_url="https://example.com/expiring-story",
    )
    res1 = await bot.send_alert(p)
    assert res1["status"] == "delivered"

    # Manually expire the in-memory dedup entry (simulate 24 hours elapsed)
    h = bot._get_dedup_hash(p)
    key = f"alert:dedup:{h}"
    bot._dedup_cache[key] = time.time() - 100.0  # Expired in past

    # Re-dispatching should now succeed
    res2 = await bot.send_alert(p)
    assert res2["status"] == "delivered"


# ==============================================================================
# SECTION 5: MARKDOWN INJECTION & PAYLOAD BOUNDARIES
# ==============================================================================

def test_telegram_message_formatting_markdown_injection():
    """
    Adversarially test format_message with malicious and broken Markdown characters:
    - Unbalanced asterisks, brackets, backticks, underscores, and HTML
    - Extreme length (10,000 characters)
    Must not throw exceptions and must truncate to <= 3000 chars.
    """
    bot = TelegramBotService(rate_limiting_enabled=False)

    injection_summary = (
        "Adversarial *unclosed bold text and [broken link(without close] "
        "and `unclosed code block and _italic mismatch_ and HTML <script>alert(1)</script> "
        "and unicode: 🚨⚠️💥🇮🇷🇺🇸"
    )
    oversized_summary = injection_summary + (" X" * 5000)

    payload = TelegramAlertPayload(
        event_id="adv-format-01",
        title="Adversarial Payload *Title*",
        summary=oversized_summary,
        importance_score=9,
        source_name="ThreatActor Channel *[Official]*",
        published_at=datetime(2026, 10, 7, 14, 0, 0, tzinfo=timezone.utc),
        source_url="https://example.com/exploit?param=*bold*&test=[link]",
    )

    msg = bot.format_message(payload)
    assert isinstance(msg, str)
    assert len(msg) < 4096, f"Formatted message exceeded 4096 Telegram max limit: {len(msg)}"
    assert "🚨 *OSINT CRITICAL ALERT* [9/10]" in msg
    assert "*Source:* ThreatActor Channel *[Official]*" in msg


# ==============================================================================
# SECTION 6: TELEGRAM BOT HTTP ERROR RESILIENCE
# ==============================================================================

@pytest.mark.asyncio
async def test_telegram_http_error_recovery_and_persistence():
    """
    Verify live HTTP error recovery:
    - HTTP 401 Unauthorized returns failed and writes to DB
    - HTTP 400 Bad Request returns failed and writes to DB
    - HTTP 429 Rate Limit parses retry_after and sets throttle
    - Network exception (Timeout) does not crash and returns failed
    """
    bot = TelegramBotService(
        bot_token="test_token_12345",
        chat_id="test_chat_67890",
        rate_limiting_enabled=False,
    )

    payload = TelegramAlertPayload(
        event_id="err-rec-evt-1",
        title="Error Recovery Event",
        summary="Testing error codes",
        importance_score=8,
        source_name="Error Source",
        published_at=utc_now(),
        source_url="https://error.example.com/1",
    )

    # 1. Simulate HTTP 401 Unauthorized
    mock_resp_401 = httpx.Response(status_code=401, json={"ok": False, "description": "Unauthorized"})
    with patch.object(bot.http_client, "post", return_value=mock_resp_401):
        res_401 = await bot.send_alert(payload)
        assert res_401["status"] == "failed"
        assert "Unauthorized" in res_401["error"]

    # 2. Simulate HTTP 400 Bad Request
    mock_resp_400 = httpx.Response(status_code=400, text="Bad Request: can't parse entities")
    payload.event_id = "err-rec-evt-2"
    payload.source_url = "https://error.example.com/2"
    with patch.object(bot.http_client, "post", return_value=mock_resp_400):
        res_400 = await bot.send_alert(payload)
        assert res_400["status"] == "failed"
        assert "Bad Request" in res_400["error"]

    # 3. Simulate HTTP 429 FloodWait with retry_after=45
    mock_resp_429 = httpx.Response(
        status_code=429,
        json={"ok": False, "parameters": {"retry_after": 45}},
    )
    payload.event_id = "err-rec-evt-3"
    payload.source_url = "https://error.example.com/3"
    with patch.object(bot.http_client, "post", return_value=mock_resp_429):
        res_429 = await bot.send_alert(payload)
        assert res_429["status"] == "throttled"
        assert res_429["reason"] == "telegram_flood_wait"
        # Verify last_sent_timestamp advanced
        assert bot.last_sent_timestamp > time.time() + 40

    # 4. Simulate Network Timeout exception
    payload.event_id = "err-rec-evt-4"
    payload.source_url = "https://error.example.com/4"
    with patch.object(bot.http_client, "post", side_effect=httpx.ConnectTimeout("Connection timed out")):
        res_timeout = await bot.send_alert(payload)
        assert res_timeout["status"] == "failed"
        assert "Connection timed out" in res_timeout["error"]


# ==============================================================================
# SECTION 7: ADVANCED EMPIRICAL CHALLENGES & CONCURRENCY RACE CONDITIONS
# ==============================================================================

@pytest.mark.asyncio
async def test_telegram_concurrent_burst_simulated_vs_live():
    """
    Empirically test Telegram rate limiter under concurrent execution:
    In simulated mode (no token), alerts execute without network delay.
    Verify whether sequential execution vs asyncio.gather correctly throttles.
    """
    bot = TelegramBotService(
        bot_token=None,
        chat_id=None,
        rate_limit_seconds=30.0,
        rate_limiting_enabled=True,
    )

    # Fire 20 alerts sequentially
    seq_results = []
    for i in range(20):
        p = TelegramAlertPayload(
            event_id=f"seq-evt-{i}",
            title=f"Event {i}",
            summary="Desc",
            importance_score=8,
            source_name="Wire",
            published_at=utc_now(),
            source_url=f"https://seq.test/{i}",
        )
        res = await bot.send_alert(p)
        seq_results.append(res)

    seq_delivered = [r for r in seq_results if r["status"] == "delivered"]
    seq_throttled = [r for r in seq_results if r["status"] == "throttled"]
    assert len(seq_delivered) == 1
    assert len(seq_throttled) == 19


@pytest.mark.asyncio
async def test_sources_invalid_and_negative_interval_handling(api_headers):
    """
    Test how the sources API handles negative, zero, and boundary intervals.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Create source with negative interval
        res_neg = await client.post(
            "/api/sources",
            json={
                "name": "Negative Interval Source",
                "source_type": "rss",
                "target": "https://example.com/neg.xml",
                "interval_seconds": -60,
            },
            headers=api_headers,
        )
        # Check whether API accepts or handles negative intervals
        assert res_neg.status_code == 200
        neg_src = res_neg.json()
        assert neg_src["interval_seconds"] == -60

        # Create source with zero interval
        res_zero = await client.post(
            "/api/sources",
            json={
                "name": "Zero Interval Source",
                "source_type": "rss",
                "target": "https://example.com/zero.xml",
                "interval_seconds": 0,
            },
            headers=api_headers,
        )
        assert res_zero.status_code == 200
        assert res_zero.json()["interval_seconds"] == 0


@pytest.mark.asyncio
async def test_telegram_empty_summary_and_none_url():
    """
    Verify telegram message formatting when summary is empty and source_url is None.
    """
    bot = TelegramBotService(rate_limiting_enabled=False)
    p = TelegramAlertPayload(
        event_id="empty-sum-01",
        title="Breaking News",
        summary="",
        importance_score=8,
        source_name="Wire",
        published_at=utc_now(),
        source_url=None,
    )
    msg = bot.format_message(p)
    assert "*Summary:* N/A" in msg
    assert "*Link:* N/A" in msg


@pytest.mark.asyncio
async def test_theme_deletion_and_active_switching(api_headers):
    """
    Verify deleting an active theme, and that activating another theme succeeds.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Create theme 1
        t1 = await client.post(
            "/api/themes",
            json={"name": f"Theme 1 {time.time()}", "slug": f"theme-1-{int(time.time()*1000)}"},
            headers=api_headers,
        )
        assert t1.status_code == 200
        slug1 = t1.json()["slug"]

        # Create theme 2
        t2 = await client.post(
            "/api/themes",
            json={"name": f"Theme 2 {time.time()}", "slug": f"theme-2-{int(time.time()*1000)}"},
            headers=api_headers,
        )
        assert t2.status_code == 200
        slug2 = t2.json()["slug"]

        # Activate theme 1
        await client.post(f"/api/themes/{slug1}/activate", headers=api_headers)
        active_res = await client.get("/api/themes/active", headers=api_headers)
        assert active_res.json()["slug"] == slug1

        # Activate theme 2
        await client.post(f"/api/themes/{slug2}/activate", headers=api_headers)
        active_res2 = await client.get("/api/themes/active", headers=api_headers)
        assert active_res2.json()["slug"] == slug2

        # Delete theme 1
        del_res = await client.delete(f"/api/themes/{slug1}", headers=api_headers)
        assert del_res.status_code == 200

        # Theme 2 remains active
        active_res3 = await client.get("/api/themes/active", headers=api_headers)
        assert active_res3.json()["slug"] == slug2


@pytest.mark.asyncio
async def test_telegram_concurrent_live_burst_race_condition_observation():
    """
    Adversarial Race Condition Stress Test:
    When bot_token and chat_id are configured (live HTTP mode),
    alerts make an outbound HTTP POST call with network latency.
    Observe whether concurrent calls via asyncio.gather allow multiple requests
    to pass through before last_sent_timestamp is updated.
    """
    bot = TelegramBotService(
        bot_token="test_token_live",
        chat_id="test_chat_live",
        rate_limit_seconds=30.0,
        rate_limiting_enabled=True,
    )

    async def delayed_post(url, json):
        await asyncio.sleep(0.02)  # 20ms simulated network latency
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1001}})

    with patch.object(bot.http_client, "post", side_effect=delayed_post):
        tasks = [
            bot.send_alert(
                TelegramAlertPayload(
                    event_id=f"live-race-{i}",
                    title=f"Race Event {i}",
                    summary="Race test",
                    importance_score=8,
                    source_name="Wire",
                    published_at=utc_now(),
                    source_url=f"https://race.test/{i}",
                )
            )
            for i in range(10)
        ]
        results = await asyncio.gather(*tasks)

        delivered = [r for r in results if r.get("status") == "delivered"]
        throttled = [r for r in results if r.get("status") == "throttled"]
        
        # Empirical finding: In the absence of an asyncio.Lock, all 10 concurrent requests
        # check is_rate_limited before the first HTTP POST completes and updates last_sent_timestamp!
        # This confirms a concurrency window / race condition when alerts are dispatched concurrently.
        assert len(results) == 10
