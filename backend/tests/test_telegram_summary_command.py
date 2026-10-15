"""
backend/tests/test_telegram_summary_command.py - Tests for Telegram AI Summary Command & Polling
================================================================================================
Verifies on-demand AI Situational Summary (/summary, /briefing, /sitrep) command execution,
time range and force-refresh argument parsing, SitRep formatting, Telegram message chunking,
chat authorization & auto-discovery, /help and /status commands, and polling lifecycle.
"""

import asyncio
from datetime import datetime, timedelta, timezone
import pytest
import pytest_asyncio
import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.app.db.models import Base, Event, Theme, utc_now
from backend.app.pipeline.orchestrator import get_pipeline_orchestrator
from backend.app.services.telegram_bot import TelegramBotService


@pytest_asyncio.fixture
async def async_test_session():
    """Create in-memory SQLite async engine and test session."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )
    async with session_factory() as session:
        yield session

    await engine.dispose()


@pytest.mark.asyncio
async def test_telegram_summary_command_execution(async_test_session, monkeypatch):
    """
    Verify /summary command triggers SituationSummaryService, formats the operational SitRep,
    and delivers both acknowledgment and structured briefing messages to the authorized chat.
    """
    monkeypatch.setattr(
        "backend.app.services.telegram_bot.get_session_factory",
        lambda: lambda: async_test_session,
    )

    orchestrator = get_pipeline_orchestrator()
    monkeypatch.setattr(orchestrator, "use_mock", True)

    now = utc_now()
    theme = Theme(
        id=1,
        slug="us-iran-conflict",
        name="US-Iran Conflict",
        description="Monitoring US-Iran naval movements and regional developments",
        keywords=["Strait of Hormuz", "IRGC", "CENTCOM"],
        entities=["IRGC", "US Navy", "CENTCOM"],
        scoring_prompt="Score 1-10",
        is_active=True,
        alert_threshold=7,
    )
    async_test_session.add(theme)

    # Add 2 sample events to populate intelligence synthesis
    ev1 = Event(
        id="cmd-ev-1",
        theme_id=1,
        source_type="rss",
        source_name="Reuters",
        external_id="ext-cmd-1",
        title="IRGC naval patrol approaches commercial vessel in Hormuz chokepoint",
        headline="IRGC Vessels Intercept Tanker in Hormuz",
        raw_text="IRGC patrol boats approached commercial vessel transiting Strait of Hormuz.",
        summary="BLUF: IRGC naval craft intercepted a commercial tanker in the Strait of Hormuz.",
        importance_score=9,
        score_rationale="Direct naval confrontation.",
        entities_json=["IRGC", "US Navy", "Strait of Hormuz"],
        location_json={"name": "Strait of Hormuz"},
        metadata_json={"trend_category": "Naval Escalation"},
        is_analyzed=True,
        published_at=now - timedelta(hours=1),
        ingested_at=now - timedelta(hours=1),
    )
    async_test_session.add(ev1)
    await async_test_session.commit()

    bot = TelegramBotService(
        bot_token=None,
        chat_id="test-chat-888",
        rate_limiting_enabled=False,
    )

    res = await bot.handle_command(command_text="/summary", chat_id="test-chat-888")
    assert res["status"] == "delivered"
    assert res["command"] == "summary"
    assert res["time_range"] == "24h"
    assert res["force_refresh"] is False
    assert "summary" in res

    # Verify messages dispatched in simulated/in-memory mode
    assert len(bot.sent_messages) >= 2
    # 1. Acknowledgment message
    ack_msg = bot.sent_messages[0]
    assert "Generating AI Situational Summary" in ack_msg["text"]

    # 2. Final SitRep message
    sitrep_msg = bot.sent_messages[1]
    text = sitrep_msg["text"]
    assert "OSINT SITUATIONAL SUMMARY (SitRep)" in text
    assert "US-Iran Conflict" in text
    assert "Threat Posture" in text
    assert "BLUF (Bottom Line Up Front):" in text
    assert "Key Operational Developments:" in text
    assert "Synthesized by" in text


@pytest.mark.asyncio
async def test_telegram_summary_time_range_and_refresh_arguments(async_test_session, monkeypatch):
    """
    Verify /summary parses time range (6h, 24h, 3d, 7d, all) and force-refresh flags,
    and supports command aliases (/briefing, /sitrep).
    """
    monkeypatch.setattr(
        "backend.app.services.telegram_bot.get_session_factory",
        lambda: lambda: async_test_session,
    )
    orchestrator = get_pipeline_orchestrator()
    monkeypatch.setattr(orchestrator, "use_mock", True)

    bot = TelegramBotService(
        bot_token=None,
        chat_id="auth-chat-1",
        rate_limiting_enabled=False,
    )

    # 1. /summary 6h
    res1 = await bot.handle_command("/summary 6h", chat_id="auth-chat-1")
    assert res1["status"] == "delivered"
    assert res1["time_range"] == "6h"
    assert res1["force_refresh"] is False

    # 2. /summary all refresh
    res2 = await bot.handle_command("/summary all refresh", chat_id="auth-chat-1")
    assert res2["status"] == "delivered"
    assert res2["time_range"] == "all"
    assert res2["force_refresh"] is True

    # 3. /briefing 3d force (alias)
    res3 = await bot.handle_command("/briefing 3d force", chat_id="auth-chat-1")
    assert res3["status"] == "delivered"
    assert res3["time_range"] == "3d"
    assert res3["force_refresh"] is True

    # 4. /sitrep 7d (alias)
    res4 = await bot.handle_command("/sitrep 7d", chat_id="auth-chat-1")
    assert res4["status"] == "delivered"
    assert res4["time_range"] == "7d"


@pytest.mark.asyncio
async def test_telegram_help_and_status_commands(async_test_session, monkeypatch):
    """
    Verify /help and /status provide useful operational and telemetry responses.
    """
    monkeypatch.setattr(
        "backend.app.services.telegram_bot.get_session_factory",
        lambda: lambda: async_test_session,
    )

    theme = Theme(
        id=2,
        slug="maritime-chokepoints",
        name="Maritime Chokepoints",
        scoring_prompt="Monitor and score maritime threats 1-10",
        is_active=True,
        alert_threshold=8,
    )
    async_test_session.add(theme)
    await async_test_session.commit()

    bot = TelegramBotService(
        bot_token=None,
        chat_id="admin-chat-123",
        rate_limiting_enabled=False,
    )

    # Test /help
    help_res = await bot.handle_command("/help", chat_id="admin-chat-123")
    assert help_res["status"] == "delivered"
    assert help_res["command"] == "help"
    assert any("/summary" in m["text"] for m in bot.sent_messages)
    assert any("Available Commands:" in m["text"] for m in bot.sent_messages)

    # Test /status
    status_res = await bot.handle_command("/status", chat_id="admin-chat-123")
    assert status_res["status"] == "delivered"
    assert status_res["command"] == "status"
    assert any("Maritime Chokepoints" in m["text"] for m in bot.sent_messages)
    assert any("≥8/10" in m["text"] for m in bot.sent_messages)


@pytest.mark.asyncio
async def test_telegram_chat_authorization_and_autodiscovery(async_test_session, monkeypatch):
    """
    Verify:
    1. Unauthorized chats are denied with Access Denied warning.
    2. Unconfigured bots automatically bind the first user chat_id.
    """
    monkeypatch.setattr(
        "backend.app.services.telegram_bot.get_session_factory",
        lambda: lambda: async_test_session,
    )

    # 1. Configured bot rejects unknown chat
    bot = TelegramBotService(
        bot_token=None,
        chat_id="authorized-ops-channel",
        rate_limiting_enabled=False,
    )

    denied_res = await bot.handle_command("/summary", chat_id="rogue-intruder")
    assert denied_res["status"] == "unauthorized"
    assert any("Access Denied" in m["text"] for m in bot.sent_messages)

    # 2. Unconfigured bot auto-discovers chat_id on first command
    fresh_bot = TelegramBotService(
        bot_token=None,
        chat_id=None,
        rate_limiting_enabled=False,
    )
    assert fresh_bot.chat_id is None

    disc_res = await fresh_bot.handle_command("/start", chat_id="first-arriving-admin")
    assert fresh_bot.chat_id == "first-arriving-admin"
    assert disc_res["status"] == "delivered"


@pytest.mark.asyncio
async def test_telegram_update_wrapper_routing(async_test_session, monkeypatch):
    """
    Verify handle_update parses standard Telegram getUpdates JSON structure and routes to handle_command.
    """
    monkeypatch.setattr(
        "backend.app.services.telegram_bot.get_session_factory",
        lambda: lambda: async_test_session,
    )

    bot = TelegramBotService(
        bot_token=None,
        chat_id="user-44",
        rate_limiting_enabled=False,
    )

    telegram_update = {
        "update_id": 100234,
        "message": {
            "message_id": 55,
            "text": "/help",
            "chat": {"id": "user-44", "type": "private"},
            "from": {"id": "user-44", "first_name": "Analyst"},
        },
    }

    res = await bot.handle_update(telegram_update)
    assert res is not None
    assert res["status"] == "delivered"
    assert res["command"] == "help"


@pytest.mark.asyncio
async def test_telegram_text_chunking_and_size_limits():
    """
    Verify _chunk_text cleanly splits text > 4000 chars into multiple sequential chunks
    without exceeding the 4096 character Telegram maximum per message.
    """
    bot = TelegramBotService(rate_limiting_enabled=False)

    paragraph = "Intelligence SITREP line detail with specific operational data.\n"
    massive_text = paragraph * 100  # ~6500 characters
    chunks = bot._chunk_text(massive_text, max_chars=4000)

    assert len(chunks) >= 2
    assert all(len(c) <= 4000 for c in chunks)
    # Ensure total text content is preserved without missing characters
    recombined = "\n".join(chunks)
    assert len(recombined) >= len(massive_text) - 10


@pytest.mark.asyncio
async def test_telegram_markdown_entity_parse_failure_fallback():
    """
    Verify send_text_message falls back to plain text if Telegram API returns 400 with parse error.
    """
    bot = TelegramBotService(
        bot_token="test_token_123",
        chat_id="chat-999",
        rate_limiting_enabled=False,
    )

    calls = []

    async def mock_post(url, json=None, timeout=None):
        calls.append(json)
        # First call has markdown parsing error
        if len(calls) == 1:
            req = httpx.Request("POST", url)
            return httpx.Response(
                400,
                text='{"ok":false,"error_code":400,"description":"Bad Request: can\'t parse entities in message text"}',
                request=req,
            )
        # Second call with plain text succeeds
        req = httpx.Request("POST", url)
        return httpx.Response(
            200,
            text='{"ok":true,"result":{"message_id":801}}',
            request=req,
        )

    # Monkeypatch http_client
    bot._http_client = httpx.AsyncClient()
    bot.http_client.post = mock_post  # type: ignore

    # Send message with tricky unclosed markdown
    res = await bot.send_text_message(
        text="*Unclosed bold text with Arabic [العربية] and symbol _",
        chat_id="chat-999",
    )

    assert res["status"] == "delivered"
    assert res["message_id"] == 801
    assert len(calls) == 2
    # Second call stripped markdown delimiters
    assert "*" not in calls[1]["text"]
    assert "_" not in calls[1]["text"]


@pytest.mark.asyncio
async def test_telegram_polling_task_lifecycle(monkeypatch):
    """
    Verify polling task can be started, processes updates via mock getUpdates,
    and terminates cleanly upon stop() without orphan tasks.
    """
    bot = TelegramBotService(
        bot_token="test_token_polling",
        chat_id="chat-polling",
        rate_limiting_enabled=False,
    )

    update_received = asyncio.Event()

    # Mock getUpdates response
    async def mock_get(url, params=None, timeout=None):
        offset = params.get("offset", 0) if params else 0
        req = httpx.Request("GET", url)
        update_received.set()
        if offset <= 501:
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "result": [
                        {
                            "update_id": 501,
                            "message": {
                                "message_id": 1,
                                "text": "/help",
                                "chat": {"id": "chat-polling"},
                            },
                        }
                    ],
                },
                request=req,
            )
        # When offset > 501, simulate idle long-polling response
        await asyncio.sleep(0.05)
        return httpx.Response(200, json={"ok": True, "result": []}, request=req)

    async def mock_post(url, json=None, timeout=None):
        req = httpx.Request("POST", url)
        return httpx.Response(
            200,
            json={"ok": True, "result": {"message_id": 999}},
            request=req,
        )

    bot._http_client = httpx.AsyncClient()
    bot.http_client.get = mock_get  # type: ignore
    bot.http_client.post = mock_post  # type: ignore

    bot._is_running = True
    polling_task = asyncio.create_task(bot._poll_updates_loop())
    bot._polling_task = polling_task

    # Wait for polling loop to process at least one update
    await asyncio.wait_for(update_received.wait(), timeout=3.0)

    # Let the loop process the update
    await asyncio.sleep(0.1)

    assert bot._last_update_id == 501
    assert any("/summary" in m["text"] for m in bot.sent_messages)

    # Stop bot and verify clean termination
    await bot.stop()
    assert polling_task.done()
    assert bot._polling_task is None

