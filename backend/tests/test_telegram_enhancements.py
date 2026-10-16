"""
backend/tests/test_telegram_enhancements.py - Tests for Telegram Rich Alerts & Commands
========================================================================================
Validates the Rich Intelligence HTML formatting, inline keyboards, tiered audio alerts,
/latest command recaps, and /threshold dynamic configuration.
"""

import asyncio
from datetime import datetime, timedelta, timezone
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.app.db.models import Base, Event, SystemSettings, Theme, utc_now
from backend.app.services.redis_bus import RedisBus
from backend.app.services.telegram_bot import TelegramAlertPayload, TelegramBotService


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


def test_html_message_formatting_and_escaping():
    """Verify HTML message formatting, colored badges, escaping, and entity tags."""
    bot = TelegramBotService(rate_limiting_enabled=False)
    now = datetime(2026, 10, 16, 18, 0, 0, tzinfo=timezone.utc)

    # Score 10 Critical Flash
    p10 = TelegramAlertPayload(
        event_id="evt-10",
        title="Ballistic Missiles Launched <alert>",
        headline="Missile Attack in Progress & sirens active",
        summary="Multiple ballistic missiles launched towards maritime chokepoints <urgent>.",
        importance_score=10,
        source_name="CENTCOM Wire & Intel",
        published_at=now,
        source_url="https://centcom.mil/news/report?id=10&type=flash",
        trend_category="Ballistic Escalation",
        entities=["CENTCOM", "IRGC Navy", "Hormuz Chokepoint"],
        corroboration_status="Highly Corroborated",
        corroboration_count=3,
    )

    html10 = bot.format_html_message(p10)
    assert "🔴 <b>CRITICAL FLASH ALERT</b> [10/10]" in html10
    assert "<b>Missile Attack in Progress &amp; sirens active</b>" in html10
    assert "&lt;urgent&gt;" in html10
    assert "CENTCOM Wire &amp; Intel" in html10
    assert "🎯 <b>Category:</b> Ballistic Escalation" in html10
    assert "✅ <b>Verification:</b> Highly Corroborated (3 sources)" in html10
    assert "#CENTCOM" in html10
    assert "#IRGCNavy" in html10
    assert "🔗 <b>Link:</b>" in html10

    # Score 8 High Importance
    p8 = TelegramAlertPayload(
        event_id="evt-8",
        title="Naval Exercise Observed",
        headline="Frigate Conducts Live-Fire Drills",
        summary="Drills conducted near coastal shipping lanes.",
        importance_score=8,
        source_name="Reuters",
        published_at=now,
        source_url="https://reuters.com/article_8",
        trend_category="Naval Maneuvers",
        corroboration_status="Single-Source Unverified",
        corroboration_count=1,
    )
    html8 = bot.format_html_message(p8)
    assert "🟠 <b>HIGH IMPORTANCE ALERT</b> [8/10]" in html8
    assert "⚠️ <b>Verification:</b> Single-Source Unverified (1 source)" in html8

    # Score 7 Elevated
    p7 = TelegramAlertPayload(
        event_id="evt-7",
        title="Diplomatic Statement",
        summary="Foreign minister issues statement regarding regional security.",
        importance_score=7,
        source_name="TASS",
        published_at=now,
    )
    html7 = bot.format_html_message(p7)
    assert "🟡 <b>ELEVATED ALERT</b> [7/10]" in html7
    assert "Diplomatic Statement" in html7


def test_inline_keyboard_generation(monkeypatch):
    """Verify inline keyboard action buttons construction."""
    monkeypatch.setenv("FRONTEND_URL", "https://osint.example.com")
    bot = TelegramBotService(rate_limiting_enabled=False)
    now = utc_now()

    payload_with_url = TelegramAlertPayload(
        event_id="evt-btn-1",
        title="Breaking News With Link",
        summary="Test summary",
        importance_score=9,
        source_name="Reuters",
        published_at=now,
        source_url="https://reuters.com/world/middle-east/article1",
    )
    kb = bot.build_inline_keyboard(payload_with_url)
    assert kb is not None
    assert "inline_keyboard" in kb
    row = kb["inline_keyboard"][0]
    assert len(row) == 2
    assert row[0]["text"] == "🌐 Open Source"
    assert row[0]["url"] == "https://reuters.com/world/middle-east/article1"
    assert row[1]["text"] == "⚡ Live Feed"
    assert row[1]["url"] == "https://osint.example.com"

    # Without source URL
    payload_no_url = TelegramAlertPayload(
        event_id="evt-btn-2",
        title="Report Without URL",
        summary="Test summary",
        importance_score=7,
        source_name="Radio Intercept",
        published_at=now,
        source_url=None,
    )
    kb2 = bot.build_inline_keyboard(payload_no_url)
    assert kb2 is not None
    row2 = kb2["inline_keyboard"][0]
    assert len(row2) == 1
    assert row2[0]["text"] == "⚡ Live Feed"


@pytest.mark.asyncio
async def test_tiered_audio_notifications(async_test_session, monkeypatch):
    """
    Verify tiered audio rules:
    Score 9-10 -> disable_notification=False (audible chime)
    Score 7-8 -> disable_notification=True (silent notification)
    """
    monkeypatch.setattr(
        "backend.app.services.telegram_bot.get_session_factory",
        lambda: lambda: async_test_session,
    )
    bot = TelegramBotService(rate_limiting_enabled=False)
    now = utc_now()

    theme = Theme(
        id=1,
        slug="tier-theme",
        name="Tier Watch",
        scoring_prompt="Assess importance 1-10",
        is_active=True,
        alert_threshold=7,
    )
    async_test_session.add(theme)
    await async_test_session.commit()

    # Pre-seed events
    for sc in (10, 9, 8, 7):
        ev = Event(
            id=f"tier_evt_{sc}",
            theme_id=1,
            source_type="rss",
            source_name="Test Wire",
            external_id=f"ext_tier_{sc}",
            title=f"Tier test event {sc}",
            raw_text="Test",
            importance_score=sc,
            published_at=now,
        )
        async_test_session.add(ev)
    await async_test_session.commit()

    # Score 10 -> audible
    p10 = TelegramAlertPayload(
        event_id="tier_evt_10",
        title="Score 10 Alert",
        summary="Flash report",
        importance_score=10,
        source_name="Wire",
        published_at=now,
    )
    await bot.send_alert(p10)
    rec10 = bot.sent_messages[-1]
    assert rec10["disable_notification"] is False, "Score 10 must be audible"

    # Score 9 -> audible
    p9 = TelegramAlertPayload(
        event_id="tier_evt_9",
        title="Score 9 Alert",
        summary="Critical report",
        importance_score=9,
        source_name="Wire",
        published_at=now,
    )
    await bot.send_alert(p9)
    rec9 = bot.sent_messages[-1]
    assert rec9["disable_notification"] is False, "Score 9 must be audible"

    # Score 8 -> silent
    p8 = TelegramAlertPayload(
        event_id="tier_evt_8",
        title="Score 8 Alert",
        summary="High report",
        importance_score=8,
        source_name="Wire",
        published_at=now,
    )
    await bot.send_alert(p8)
    rec8 = bot.sent_messages[-1]
    assert rec8["disable_notification"] is True, "Score 8 must be silent"

    # Score 7 -> silent
    p7 = TelegramAlertPayload(
        event_id="tier_evt_7",
        title="Score 7 Alert",
        summary="Elevated report",
        importance_score=7,
        source_name="Wire",
        published_at=now,
    )
    await bot.send_alert(p7)
    rec7 = bot.sent_messages[-1]
    assert rec7["disable_notification"] is True, "Score 7 must be silent"


@pytest.mark.asyncio
async def test_latest_command_execution(async_test_session, monkeypatch):
    """Verify /latest command returns recap of top recent critical alerts."""
    monkeypatch.setattr(
        "backend.app.services.telegram_bot.get_session_factory",
        lambda: lambda: async_test_session,
    )
    now = utc_now()
    theme = Theme(
        id=1,
        slug="me-conflict",
        name="Middle East Watch",
        scoring_prompt="Assess importance 1-10",
        is_active=True,
        alert_threshold=7,
    )
    async_test_session.add(theme)
    await async_test_session.commit()

    for i in range(1, 6):
        ev = Event(
            id=f"latest-ev-{i}",
            theme_id=1,
            source_type="rss",
            source_name=f"News Wire {i}",
            external_id=f"ext-latest-{i}",
            title=f"Critical Incident {i}",
            headline=f"Major Event #{i}",
            raw_text="Details of incident...",
            summary=f"BLUF: Summary of operational situation #{i}",
            importance_score=7 + (i % 3),
            published_at=now - timedelta(minutes=i * 10),
            source_url=f"https://news.org/story/{i}",
            is_analyzed=True,
        )
        async_test_session.add(ev)
    await async_test_session.commit()

    bot = TelegramBotService(chat_id="authorized-chat-1", rate_limiting_enabled=False)
    res = await bot.handle_command("/latest", chat_id="authorized-chat-1")

    assert res["status"] == "delivered"
    assert res["command"] == "latest"
    assert res["count"] == 5

    # Check delivered message text in sent_messages
    last_msg = bot.sent_messages[-1]["text"]
    assert "Recent Critical OSINT Alerts" in last_msg
    assert "Major Event #1" in last_msg
    assert "News Wire 1" in last_msg


@pytest.mark.asyncio
async def test_threshold_command_execution(async_test_session, monkeypatch):
    """Verify /threshold command inspects and updates alert sensitivity dynamically."""
    monkeypatch.setattr(
        "backend.app.services.telegram_bot.get_session_factory",
        lambda: lambda: async_test_session,
    )
    theme = Theme(
        id=1,
        slug="me-watch",
        name="Regional Watch",
        scoring_prompt="Assess importance 1-10",
        is_active=True,
        alert_threshold=7,
    )
    async_test_session.add(theme)
    async_test_session.add(
        SystemSettings(key="notification_settings", value_json={"default_alert_threshold": 7})
    )
    await async_test_session.commit()

    mock_bus = RedisBus(redis_url=None)
    bot = TelegramBotService(chat_id="chat-thresh-1", redis_bus=mock_bus, rate_limiting_enabled=False)

    # 1. Query threshold without args
    res_query = await bot.handle_command("/threshold", chat_id="chat-thresh-1")
    assert res_query["status"] == "delivered"
    assert res_query["threshold"] == 7
    assert "Current Threshold:" in bot.sent_messages[-1]["text"]

    # 2. Update threshold to 9
    res_update = await bot.handle_command("/threshold 9", chat_id="chat-thresh-1")
    assert res_update["status"] == "delivered"
    assert res_update["new_threshold"] == 9
    assert "New Sensitivity:</b> ≥9/10" in bot.sent_messages[-1]["text"]

    # Verify DB was updated
    async with async_test_session:
        refreshed_theme = (await async_test_session.execute(select(Theme).where(Theme.id == 1))).scalars().first()
        assert refreshed_theme.alert_threshold == 9

    # 3. Invalid threshold boundary
    res_invalid = await bot.handle_command("/threshold 15", chat_id="chat-thresh-1")
    assert res_invalid["status"] == "invalid_argument"
    assert "Invalid Threshold Value" in bot.sent_messages[-1]["text"]


@pytest.mark.asyncio
async def test_pipeline_orchestrator_alert_bus_dispatch():
    """
    Verify orchestrator._publish_to_bus publishes rich fields to CHANNEL_EVENTS_ALERT
    without encountering NameError on metadata.
    """
    from backend.app.pipeline.models import AnalyzedEvent
    from backend.app.pipeline.orchestrator import get_pipeline_orchestrator
    from backend.app.services.redis_bus import CHANNEL_EVENTS_ALERT

    orchestrator = get_pipeline_orchestrator()
    published_alerts = []

    class MockBus:
        async def publish(self, channel: str, data: dict):
            if channel == CHANNEL_EVENTS_ALERT:
                published_alerts.append(data)

    orchestrator._redis_bus = MockBus()

    analyzed = AnalyzedEvent(
        id="orch-alert-evt-1",
        theme_id=1,
        source_type="rss",
        source_name="Al Jazeera",
        external_id="ext-orch-1",
        title="Breaking Naval Incident",
        headline="Warship Deployed in Red Sea",
        raw_text="Armed warship deployed in southern chokepoints.",
        summary="A naval task force has repositioned to safeguard transit lanes.",
        importance_score=9,
        score_rationale="Urgent strategic escalation.",
        trend_category="Naval Security",
        entities=["US Navy", "Red Sea"],
        metadata={"scoring_breakdown": {"corroboration_status": "Highly Corroborated", "corroboration_count": 2}},
        metadata_json={"scoring_breakdown": {"corroboration_status": "Highly Corroborated", "corroboration_count": 2}},
        published_at=utc_now(),
    )

    # Trigger _publish_to_bus with should_alert=True
    await orchestrator._publish_to_bus(analyzed, should_alert=True)

    assert len(published_alerts) == 1
    alt = published_alerts[0]
    assert alt["event_id"] == "orch-alert-evt-1"
    assert alt["headline"] == "Warship Deployed in Red Sea"
    assert alt["importance_score"] == 9
    assert alt["trend_category"] == "Naval Security"
    assert alt["corroboration_status"] == "Highly Corroborated"
    assert alt["corroboration_count"] == 2
    assert "metadata_json" in alt

