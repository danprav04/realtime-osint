"""
backend/tests/test_m2_plugins.py - Milestone 2 Test Suite: Data Source Plugins & Supervisor

Tests:
1. NormalizedEvent & Location schema validation, attribute/subscript access, and timezone enforcement.
2. RSSSourcePlugin: Ingestion, HTML stripping, date parsing, language detection, error isolation.
3. TelegramSourcePlugin: MTProto/web preview scraping, Persian/Arabic detection, credential fallback.
4. NewsAPISourcePlugin: GDELT 2.0 Doc API, NewsAPI, Currents, keyless fallback, error handling.
5. RedditSourcePlugin: Public JSON API, AsyncPRAW compatibility, keyword & upvote filtering.
6. AISSourcePlugin: Digitraffic Finland GeoJSON, coordinates, MMSI, vessel telemetry normalization.
7. IngestionSupervisor: Lifecycle, error isolation across concurrent tasks, DB persistence,
   Redis bus dispatch, and zero-code hot-reload via 'osint:config:reload'.
"""

import asyncio
import json
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List
import pytest
import httpx
from sqlalchemy import select

from backend.app.db.models import Event, Source, Theme
from backend.app.db.session import async_sessionmaker, create_async_engine
from backend.app.plugins.base import BaseSourcePlugin, Location, NormalizedEvent
from backend.app.plugins.rss_plugin import RSSSourcePlugin
from backend.app.plugins.telegram_plugin import TelegramSourcePlugin
from backend.app.plugins.newsapi_plugin import NewsAPISourcePlugin
from backend.app.plugins.reddit_plugin import RedditSourcePlugin
from backend.app.plugins.ais_plugin import AISSourcePlugin
from backend.app.plugins.supervisor import (
    IngestionSupervisor,
    create_plugin_instance,
)
from backend.app.services.redis_bus import (
    CHANNEL_CONFIG_RELOAD,
    CHANNEL_EVENTS_NEW,
    RedisBus,
)


# ============================================================================
# 1. NormalizedEvent & Location Schema Compliance
# ============================================================================

def test_normalized_event_schema_defaults():
    """Validates default fields, UUID generation, and UTC timestamps."""
    event = NormalizedEvent(
        theme_id="us-iran-conflict",
        source_type="rss",
        source_name="Reuters Middle East",
        external_id="reuters_001",
        title="Diplomatic summit scheduled",
        raw_text="Envoys to meet in Geneva for nuclear talks.",
    )
    assert event.id is not None
    assert len(event.id) > 10
    assert event.theme_id == "us-iran-conflict"
    assert event.source_type == "rss"
    assert event.language == "en"
    assert event.published_at.tzinfo is not None
    assert event.ingested_at.tzinfo is not None
    assert event.metadata == {}


def test_normalized_event_source_type_normalization():
    """Auto-normalizes alternative source type designations (e.g. news_api, maritime_ais)."""
    ev1 = NormalizedEvent(
        theme_id=1,
        source_type="news_api",
        source_name="GDELT",
        external_id="g1",
        title="T1",
        raw_text="R1",
    )
    assert ev1.source_type == "newsapi"
    assert ev1.theme_id == "1"

    ev2 = NormalizedEvent(
        theme_id="taiwan-strait",
        source_type="maritime_ais",
        source_name="AISStream",
        external_id="a1",
        title="T2",
        raw_text="R2",
    )
    assert ev2.source_type == "ais"


def test_location_attribute_and_dict_access():
    """Location supports both attribute access (.lat) and dict subscripting (['lat'])."""
    loc = Location(name="Strait of Hormuz", lat=26.54, lon=56.33)
    assert loc.lat == 26.54
    assert loc.lon == 56.33
    assert loc.name == "Strait of Hormuz"

    # Dict-like subscript access
    assert loc["lat"] == 26.54
    assert loc["lon"] == 56.33
    assert loc["name"] == "Strait of Hormuz"
    assert loc.get("lat") == 26.54
    assert loc.get("unknown", "default") == "default"

    # NormalizedEvent accepts dict and parses into Location
    event = NormalizedEvent(
        theme_id="us-iran-conflict",
        source_type="ais",
        source_name="AIS Stream",
        external_id="ais_001",
        title="Naval patrol detected",
        raw_text="Patrol boat in patrol zone",
        location={"name": "Strait of Hormuz", "lat": 26.54, "lon": 56.33},
    )
    assert event.location is not None
    assert event.location.lat == 26.54
    assert event.location["lon"] == 56.33
    assert event.to_dict()["location"]["name"] == "Strait of Hormuz"


# ============================================================================
# 2. RSS / Atom Source Plugin Tests
# ============================================================================

@pytest.mark.asyncio
async def test_rss_plugin_ingestion_and_normalization(monkeypatch):
    """RSSSourcePlugin parses valid RSS feed XML and extracts NormalizedEvents."""
    sample_rss_xml = """<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0">
        <channel>
            <title>Al Jazeera Middle East</title>
            <link>https://www.aljazeera.com</link>
            <item>
                <title>Tensions heighten in Strait of Hormuz</title>
                <link>https://www.aljazeera.com/news/2026/10/6/hormuz-tensions</link>
                <description><![CDATA[<p>Commercial vessels report increased naval patrols in the region.</p>]]></description>
                <author>Staff Correspondent</author>
                <pubDate>Tue, 06 Oct 2026 19:30:00 GMT</pubDate>
                <guid>aljazeera_hormuz_123</guid>
            </item>
        </channel>
    </rss>
    """

    class MockResponse:
        status_code = 200
        text = sample_rss_xml

        def raise_for_status(self):
            pass

    async def mock_get(self, url, *args, **kwargs):
        return MockResponse()

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    plugin = RSSSourcePlugin(
        source_id=101,
        theme_id="us-iran-conflict",
        name="Al Jazeera Middle East",
        config={"url": "https://www.aljazeera.com/xml/rss/all.xml"},
        poll_interval=180,
    )

    events = await plugin.poll()
    assert len(events) == 1
    ev = events[0]
    assert ev.source_type == "rss"
    assert ev.title == "Tensions heighten in Strait of Hormuz"
    # Stripped HTML tags
    assert "Commercial vessels report increased naval patrols" in ev.raw_text
    assert "<p>" not in ev.raw_text
    assert ev.source_url == "https://www.aljazeera.com/news/2026/10/6/hormuz-tensions"
    assert ev.author == "Staff Correspondent"
    assert ev.published_at.year == 2026
    assert plugin.status == "active"
    assert plugin.events_count == 1
    await plugin.stop()


@pytest.mark.asyncio
async def test_rss_plugin_farsi_language_detection(monkeypatch):
    """RSSSourcePlugin identifies Persian/Arabic script and sets language correctly."""
    sample_irna_xml = """<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0">
        <channel>
            <title>IRNA Farsi News</title>
            <item>
                <title>رزمایش نیروهای مسلح در خلیج فارس آغاز شد</title>
                <link>https://irna.ir/news/8512345</link>
                <description>مانورهای دریایی مشترک در تنگه هرمز در حال برگزاری است.</description>
                <guid>irna_8512345</guid>
            </item>
        </channel>
    </rss>
    """

    class MockResponse:
        status_code = 200
        text = sample_irna_xml

        def raise_for_status(self):
            pass

    async def mock_get(self, url, *args, **kwargs):
        return MockResponse()

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    plugin = RSSSourcePlugin(
        source_id=102,
        theme_id="us-iran-conflict",
        name="IRNA Persian",
        config={"url": "https://irna.ir/rss"},
    )
    events = await plugin.poll()
    assert len(events) == 1
    assert events[0].language == "fa"
    assert "رزمایش" in events[0].title
    await plugin.stop()


@pytest.mark.asyncio
async def test_rss_plugin_http_error_graceful_handling(monkeypatch):
    """RSSSourcePlugin gracefully handles HTTP 500 without throwing exceptions."""
    async def mock_get_error(self, url, *args, **kwargs):
        raise httpx.HTTPStatusError("500 Internal Server Error", request=None, response=httpx.Response(500))

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get_error)

    plugin = RSSSourcePlugin(
        source_id=103,
        theme_id="us-iran-conflict",
        name="Broken RSS Feed",
        config={"url": "https://broken-rss.example.com/feed"},
    )
    events = await plugin.poll()
    assert events == []
    assert plugin.status == "degraded"
    assert plugin.last_error is not None
    await plugin.stop()


# ============================================================================
# 3. Telegram Source Plugin Tests
# ============================================================================

@pytest.mark.asyncio
async def test_telegram_web_preview_scraping_fallback(monkeypatch):
    """TelegramSourcePlugin falls back to public web preview scraping when credentials omitted."""
    html_fixture = """
    <div class="tgme_page">
        <div class="tgme_widget_message_wrap">
            <div class="tgme_widget_message" data-post="irna_1313/98765">
                <div class="tgme_widget_message_text js-message_text">
                    رزمایش مشترک ذوالفقار در منطقه عمومی خلیج فارس و دریای عمان آغاز شد
                </div>
                <time datetime="2026-10-06T18:45:00+00:00" class="time">18:45</time>
            </div>
        </div>
    </div>
    """

    class MockResp:
        status_code = 200
        text = html_fixture

    async def mock_get(self, url, *args, **kwargs):
        return MockResp()

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    plugin = TelegramSourcePlugin(
        source_id=201,
        theme_id="us-iran-conflict",
        name="IRNA Telegram",
        config={"channel": "@irna_1313"},
    )
    await plugin.start()
    events = await plugin.poll()
    assert len(events) == 1
    ev = events[0]
    assert ev.source_type == "telegram"
    assert ev.metadata["channel_id"] == "@irna_1313"
    assert ev.metadata["message_id"] == 98765
    assert ev.language == "fa"
    assert "رزمایش مشترک" in ev.raw_text
    await plugin.stop()


@pytest.mark.asyncio
async def test_telegram_plugin_error_resilience(monkeypatch):
    """TelegramSourcePlugin handles network 404 or connection failures without raising."""
    async def mock_get_404(self, url, *args, **kwargs):
        return httpx.Response(404, text="Channel not found")

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get_404)

    plugin = TelegramSourcePlugin(
        source_id=202,
        theme_id="us-iran-conflict",
        name="Missing TG Channel",
        config={"channel": "@nonexistent_channel_xyz"},
    )
    events = await plugin.poll()
    assert events == []
    await plugin.stop()


# ============================================================================
# 4. News API Plugin Tests (GDELT 2.0 & NewsAPI)
# ============================================================================

@pytest.mark.asyncio
async def test_newsapi_gdelt_ingestion(monkeypatch):
    """NewsAPISourcePlugin ingests free GDELT 2.0 Doc API JSON articles."""
    gdelt_fixture = {
        "articles": [
            {
                "url": "https://reuters.com/world/iran-nuclear-statement-2026",
                "title": "IAEA inspectors complete facility inspection in Isfahan",
                "seendate": "20261006T173000Z",
                "domain": "reuters.com",
                "language": "English",
                "sourcecountry": "United States",
            }
        ]
    }

    class MockResp:
        status_code = 200

        def json(self):
            return gdelt_fixture

    async def mock_get(self, url, *args, **kwargs):
        return MockResp()

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    plugin = NewsAPISourcePlugin(
        source_id=301,
        theme_id="us-iran-conflict",
        name="GDELT Crisis Monitor",
        config={"provider": "gdelt", "query": "Iran IAEA nuclear"},
    )
    events = await plugin.poll()
    assert len(events) == 1
    ev = events[0]
    assert ev.source_type == "newsapi"
    assert "IAEA inspectors" in ev.title
    assert ev.source_url == "https://reuters.com/world/iran-nuclear-statement-2026"
    assert ev.published_at.year == 2026
    await plugin.stop()


@pytest.mark.asyncio
async def test_newsapi_auto_fallback_to_gdelt_when_key_missing(monkeypatch):
    """When provider='newsapi' is requested without api_key, automatically falls back to GDELT."""
    called_gdelt = False

    class MockResp:
        status_code = 200

        def json(self):
            return {"articles": []}

    async def mock_get(self, url, *args, **kwargs):
        nonlocal called_gdelt
        if "gdeltproject.org" in str(url):
            called_gdelt = True
        return MockResp()

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    plugin = NewsAPISourcePlugin(
        source_id=302,
        theme_id="us-iran-conflict",
        name="NewsAPI without Key",
        config={"provider": "newsapi", "query": "Persian Gulf"},
    )
    await plugin.poll()
    assert called_gdelt is True
    await plugin.stop()


# ============================================================================
# 5. Reddit Plugin Tests
# ============================================================================

@pytest.mark.asyncio
async def test_reddit_plugin_public_json_and_keyword_filtering(monkeypatch):
    """RedditSourcePlugin filters submissions by theme keywords and min_upvotes."""
    reddit_fixture = {
        "data": {
            "children": [
                {
                    "data": {
                        "id": "abc123",
                        "title": "US CENTCOM issues statement regarding naval escort operations",
                        "selftext": "Naval forces in Bahrain announced updated rules of engagement.",
                        "score": 150,
                        "num_comments": 42,
                        "author": "intel_analyst",
                        "permalink": "/r/worldnews/comments/abc123/us_centcom_statement",
                        "created_utc": 1791300000,
                    }
                },
                {
                    "data": {
                        "id": "def456",
                        "title": "Unrelated topic: Local cooking competition winners announced",
                        "selftext": "Baking event in Omaha.",
                        "score": 250,
                        "num_comments": 12,
                        "author": "food_fan",
                        "permalink": "/r/worldnews/comments/def456/cooking",
                        "created_utc": 1791300100,
                    }
                },
            ]
        }
    }

    class MockResp:
        status_code = 200

        def json(self):
            return reddit_fixture

    async def mock_get(self, url, *args, **kwargs):
        return MockResp()

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    plugin = RedditSourcePlugin(
        source_id=401,
        theme_id="us-iran-conflict",
        name="Reddit Geopolitics Monitor",
        config={
            "subreddits": ["worldnews"],
            "keywords": ["CENTCOM", "naval", "Hormuz"],
            "min_upvotes": 50,
        },
    )
    events = await plugin.poll()
    # Only the first post matches the keywords
    assert len(events) == 1
    ev = events[0]
    assert ev.source_type == "reddit"
    assert "US CENTCOM" in ev.title
    assert ev.metadata["score"] == 150
    assert ev.source_url == "https://reddit.com/r/worldnews/comments/abc123/us_centcom_statement"
    await plugin.stop()


# ============================================================================
# 6. AIS Maritime Tracking Plugin Tests
# ============================================================================

@pytest.mark.asyncio
async def test_ais_plugin_digitraffic_geojson_normalization(monkeypatch):
    """AISSourcePlugin ingests GeoJSON vessel telemetry into NormalizedEvents with Location."""
    digitraffic_fixture = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [56.32, 26.45],  # [lon, lat]
                },
                "properties": {
                    "mmsi": 211234567,
                    "sog": 14.5,
                    "cog": 182.0,
                    "heading": 185,
                    "navStat": 0,
                    "shipName": "GULF TANKER V",
                    "destination": "FUJAIRAH",
                    "timestampExternal": 1791310000000,
                },
            }
        ],
    }

    class MockResp:
        status_code = 200

        def json(self):
            return digitraffic_fixture

    async def mock_get(self, url, *args, **kwargs):
        return MockResp()

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    plugin = AISSourcePlugin(
        source_id=501,
        theme_id="us-iran-conflict",
        name="Hormuz AIS Telemetry",
        config={
            "provider": "digitraffic",
            "location_name": "Strait of Hormuz",
        },
    )
    events = await plugin.poll()
    assert len(events) == 1
    ev = events[0]
    assert ev.source_type == "ais"
    assert ev.location is not None
    assert ev.location.lat == 26.45
    assert ev.location.lon == 56.32
    assert ev.location["name"] == "Strait of Hormuz"
    assert ev.metadata["mmsi"] == "211234567"
    assert ev.metadata["sog_knots"] == 14.5
    assert "GULF TANKER V" in ev.title
    await plugin.stop()


# ============================================================================
# 7. Ingestion Supervisor: Error Isolation, DB Persistence & Hot Reload
# ============================================================================

@pytest.mark.asyncio
async def test_supervisor_error_isolation():
    """
    Error Isolation Guarantee:
    If one source plugin throws an unhandled exception or network timeout,
    peer source plugins continue polling uninterrupted without crashing the supervisor.
    """
    failing_poll_called = 0
    healthy_poll_called = 0

    class FailingPlugin(BaseSourcePlugin):
        async def poll(self) -> List[NormalizedEvent]:
            nonlocal failing_poll_called
            failing_poll_called += 1
            raise RuntimeError("Fatal upstream remote server timeout!")

        async def test_connection(self) -> bool:
            return False

    class HealthyPlugin(BaseSourcePlugin):
        async def poll(self) -> List[NormalizedEvent]:
            nonlocal healthy_poll_called
            healthy_poll_called += 1
            return [
                NormalizedEvent(
                    theme_id="us-iran-conflict",
                    source_type="rss",
                    source_name="Healthy Feed",
                    external_id=f"health_{healthy_poll_called}",
                    title="Healthy event title",
                    raw_text="Healthy description",
                )
            ]

        async def test_connection(self) -> bool:
            return True

    supervisor = IngestionSupervisor()
    p_fail = FailingPlugin("p_fail", "us-iran-conflict", "Failing Source", poll_interval=1)
    p_good = HealthyPlugin("p_good", "us-iran-conflict", "Healthy Source", poll_interval=1)

    await supervisor.add_plugin(p_fail)
    await supervisor.add_plugin(p_good)
    supervisor._running = True
    supervisor._start_plugin_task("p_fail", p_fail)
    supervisor._start_plugin_task("p_good", p_good)

    # Allow worker tasks to cycle
    await asyncio.sleep(1.2)
    await supervisor.stop()

    assert failing_poll_called >= 1
    assert healthy_poll_called >= 1
    assert p_fail.status == "error"
    assert "Fatal upstream remote server timeout" in (p_fail.last_error or "")
    assert p_good.status in ("active", "paused")


@pytest.mark.asyncio
async def test_supervisor_dynamic_reload_via_redis_channel():
    """
    Zero-Code Dynamic Reconfiguration:
    Publishing 'osint:config:reload' triggers atomic reconciliation without restart.
    """
    # Create an in-memory SQLite database for test reconciliation
    test_engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with test_engine.begin() as conn:
        from backend.app.db.models import Base
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(test_engine, expire_on_commit=False)
    bus = RedisBus()
    supervisor = IngestionSupervisor(session_factory=session_maker, redis_bus=bus)

    # 1. Seed initial theme with 1 source
    async with session_maker() as session:
        theme = Theme(
            name="US-Iran Conflict",
            description="Initial test theme",
            is_active=True,
            keywords=["Iran"],
            entities=["IRGC"],
            scoring_prompt="Evaluate tensions",
        )
        session.add(theme)
        await session.flush()

        src1 = Source(
            theme_id=theme.id,
            name="Reuters Initial",
            source_type="rss",
            config_json={"url": "https://reuters.com/feed"},
            is_enabled=True,
            polling_interval_sec=120,
        )
        session.add(src1)
        await session.commit()

    await supervisor.start()
    assert len(supervisor.plugins) == 1
    assert "Reuters Initial" in [p.name for p in supervisor.plugins.values()]

    # 2. Add a second source to database and publish reload event
    async with session_maker() as session:
        src2 = Source(
            theme_id=theme.id,
            name="GDELT Added Live",
            source_type="newsapi",
            config_json={"query": "Iran CENTCOM"},
            is_enabled=True,
            polling_interval_sec=60,
        )
        session.add(src2)
        await session.commit()

    # Trigger hot-reload by publishing to osint:config:reload channel
    await bus.publish(CHANNEL_CONFIG_RELOAD, {"action": "source_update"})

    # Give event loop time to process pub/sub and reconcile
    await asyncio.sleep(0.3)

    plugin_names = [p.name for p in supervisor.plugins.values()]
    assert len(supervisor.plugins) == 2
    assert "GDELT Added Live" in plugin_names

    # 3. Disable a source in DB and publish reload
    async with session_maker() as session:
        res = await session.execute(select(Source).where(Source.name == "Reuters Initial"))
        s = res.scalar_one()
        s.is_enabled = False
        await session.commit()

    await bus.publish(CHANNEL_CONFIG_RELOAD, {"action": "source_update"})
    await asyncio.sleep(0.3)

    # Reuters Initial should now be paused (disabled)
    reuters_plugin = next(p for p in supervisor.plugins.values() if p.name == "Reuters Initial")
    assert reuters_plugin.is_enabled is False

    await supervisor.stop()
    await test_engine.dispose()


@pytest.mark.asyncio
async def test_supervisor_db_persistence_and_redis_dispatch():
    """
    Supervisor persists ingested events to DB and dispatches to Redis bus channel 'osint:events:new'.
    """
    test_engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with test_engine.begin() as conn:
        from backend.app.db.models import Base
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(test_engine, expire_on_commit=False)
    bus = RedisBus()
    supervisor = IngestionSupervisor(session_factory=session_maker, redis_bus=bus)

    # Seed theme
    async with session_maker() as session:
        theme = Theme(
            name="US-Iran Conflict",
            description="Persistence test",
            is_active=True,
            keywords=["drone"],
            entities=["IRGC"],
            scoring_prompt="Evaluate",
        )
        session.add(theme)
        await session.commit()

    received_redis_events: List[Dict[str, Any]] = []

    async def handle_new_event(channel: str, data: Any):
        received_redis_events.append(data if isinstance(data, dict) else json.loads(data))

    bus.subscribe(CHANNEL_EVENTS_NEW, handle_new_event)

    test_event = NormalizedEvent(
        theme_id=str(theme.id),
        source_type="rss",
        source_name="AP News",
        source_url="https://apnews.com/article/1",
        external_id="ap_001_unique",
        title="Drone intercepted over Red Sea",
        raw_text="Destroyer shot down one attack drone.",
        location={"name": "Red Sea", "lat": 15.0, "lon": 42.0},
    )

    await supervisor.dispatch_and_persist(test_event)

    # Allow asyncio task to dispatch callback
    await asyncio.sleep(0.2)

    # 1. Check Redis Bus received event
    assert len(received_redis_events) >= 1
    assert received_redis_events[0]["external_id"] == "ap_001_unique"
    assert received_redis_events[0]["title"] == "Drone intercepted over Red Sea"

    # 2. Check Database persisted event
    async with session_maker() as session:
        res = await session.execute(select(Event).where(Event.external_id == "ap_001_unique"))
        persisted = res.scalar_one_or_none()
        assert persisted is not None
        assert persisted.title == "Drone intercepted over Red Sea"
        assert persisted.source_type == "rss"
        assert persisted.location_json["name"] == "Red Sea"

    await test_engine.dispose()


# ============================================================================
# 8. Extended Edge Case & Lifecycle Unit Tests
# ============================================================================

def test_plugin_factory_all_variants():
    """Factory correctly maps source types (canonical and aliases) to plugin classes."""
    p_rss = create_plugin_instance("rss", 1, "theme1", "RSS 1")
    assert isinstance(p_rss, RSSSourcePlugin)

    p_tg = create_plugin_instance("telegram", 2, "theme1", "TG 1")
    assert isinstance(p_tg, TelegramSourcePlugin)

    p_news1 = create_plugin_instance("newsapi", 3, "theme1", "News 1")
    assert isinstance(p_news1, NewsAPISourcePlugin)

    p_news2 = create_plugin_instance("news_api", 4, "theme1", "News 2")
    assert isinstance(p_news2, NewsAPISourcePlugin)

    p_reddit = create_plugin_instance("reddit", 5, "theme1", "Reddit 1")
    assert isinstance(p_reddit, RedditSourcePlugin)

    p_ais1 = create_plugin_instance("ais", 6, "theme1", "AIS 1")
    assert isinstance(p_ais1, AISSourcePlugin)

    p_ais2 = create_plugin_instance("maritime_ais", 7, "theme1", "AIS 2")
    assert isinstance(p_ais2, AISSourcePlugin)

    # Unknown fallback
    p_unknown = create_plugin_instance("unknown_custom", 8, "theme1", "Fallback")
    assert isinstance(p_unknown, RSSSourcePlugin)


@pytest.mark.asyncio
async def test_base_plugin_lifecycle_methods():
    """BaseSourcePlugin start/stop hooks and is_enabled property work as expected."""
    class DummyPlugin(BaseSourcePlugin):
        async def poll(self) -> List[NormalizedEvent]:
            return []

        async def test_connection(self) -> bool:
            return True

    plugin = DummyPlugin(source_id=99, theme_id=1, name="Dummy", poll_interval=120, is_enabled=True)
    assert plugin.is_enabled is True
    assert plugin.status == "active"

    await plugin.start()
    assert plugin.is_running is True

    # Disable plugin
    plugin.is_enabled = False
    assert plugin.status == "paused"

    await plugin.stop()
    assert plugin.is_running is False


@pytest.mark.asyncio
async def test_rss_plugin_test_connection(monkeypatch):
    """RSSSourcePlugin test_connection reports valid vs broken feeds correctly."""
    class MockGoodResp:
        status_code = 200
        text = "<rss><channel><title>Good Feed</title></channel></rss>"

    class MockBadResp:
        status_code = 404
        text = "Not Found"

    async def mock_get_good(self, url, *args, **kwargs):
        return MockGoodResp()

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get_good)
    good_plugin = RSSSourcePlugin(1, 1, "Good", {"url": "https://example.com/rss"})
    assert await good_plugin.test_connection() is True

    async def mock_get_bad(self, url, *args, **kwargs):
        return MockBadResp()

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get_bad)
    bad_plugin = RSSSourcePlugin(2, 1, "Bad", {"url": "https://example.com/bad_rss"})
    assert await bad_plugin.test_connection() is False


@pytest.mark.asyncio
async def test_newsapi_currents_parsing(monkeypatch):
    """NewsAPISourcePlugin parses Currents API response format into NormalizedEvents."""
    currents_fixture = {
        "status": "ok",
        "news": [
            {
                "title": "Diplomatic sanctions discussion continues",
                "description": "European ministers met in Brussels.",
                "url": "https://currents.example.com/news/1",
                "author": "Brussels Bureau",
                "published": "2026-10-06T15:00:00+00:00",
                "language": "en",
                "category": ["regional", "diplomacy"],
            }
        ],
    }

    class MockResp:
        status_code = 200

        def json(self):
            return currents_fixture

    async def mock_get(self, url, *args, **kwargs):
        return MockResp()

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    plugin = NewsAPISourcePlugin(
        source_id=305,
        theme_id="us-iran-conflict",
        name="Currents News Service",
        config={
            "provider": "currents",
            "api_key": "test_currents_key",
            "query": "sanctions",
        },
    )
    events = await plugin.poll()
    assert len(events) == 1
    assert events[0].source_type == "newsapi"
    assert events[0].title == "Diplomatic sanctions discussion continues"
    assert events[0].author == "Brussels Bureau"


@pytest.mark.asyncio
async def test_reddit_min_upvotes_boundary_rejection(monkeypatch):
    """RedditSourcePlugin ignores posts below min_upvotes."""
    reddit_fixture = {
        "data": {
            "children": [
                {
                    "data": {
                        "id": "low_score_1",
                        "title": "CENTCOM brief with very low score",
                        "selftext": "Content here",
                        "score": 5,  # below 50
                        "permalink": "/r/worldnews/comments/low_score_1",
                        "created_utc": 1791300000,
                    }
                }
            ]
        }
    }

    class MockResp:
        status_code = 200

        def json(self):
            return reddit_fixture

    async def mock_get(self, url, *args, **kwargs):
        return MockResp()

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    plugin = RedditSourcePlugin(
        source_id=402,
        theme_id="us-iran-conflict",
        name="Reddit High Bar",
        config={
            "subreddits": ["worldnews"],
            "keywords": ["CENTCOM"],
            "min_upvotes": 50,
        },
    )
    events = await plugin.poll()
    assert len(events) == 0


@pytest.mark.asyncio
async def test_ais_signals_direct_feed():
    """AISSourcePlugin processes pre-configured / offline test signals directly."""
    signals_data = [
        {
            "mmsi": "999888777",
            "lat": 25.10,
            "lon": 56.80,
            "sog": 18.0,
            "cog": 90.0,
            "ship_name": "CARGO VESSEL 9",
            "destination": "BANDAR ABBAS",
            "location_name": "Gulf of Oman",
        }
    ]

    plugin = AISSourcePlugin(
        source_id=502,
        theme_id="us-iran-conflict",
        name="Offline AIS Signals",
        config={"signals": signals_data},
    )
    events = await plugin.poll()
    assert len(events) == 1
    ev = events[0]
    assert ev.source_type == "ais"
    assert ev.location.lat == 25.10
    assert ev.location.lon == 56.80
    assert ev.metadata["ship_name"] == "CARGO VESSEL 9"


@pytest.mark.asyncio
async def test_supervisor_manual_crud_lifecycle():
    """IngestionSupervisor programmatic add, pause, resume, poll_once, and status work correctly."""
    supervisor = IngestionSupervisor()

    class TestPlugin(BaseSourcePlugin):
        async def poll(self) -> List[NormalizedEvent]:
            return [
                NormalizedEvent(
                    theme_id="test-theme",
                    source_type="rss",
                    source_name=self.name,
                    external_id="manual_001",
                    title="Manual poll title",
                    raw_text="Manual poll text",
                )
            ]

        async def test_connection(self) -> bool:
            return True

    p = TestPlugin(source_id="manual_src_1", theme_id="test-theme", name="Manual Source")
    await supervisor.add_plugin(p)
    assert supervisor.get_plugin("manual_src_1") is not None
    assert len(supervisor.list_plugins()) == 1

    # Single poll once
    events = await supervisor.poll_source_once("manual_src_1")
    assert len(events) == 1
    assert events[0].title == "Manual poll title"

    # Status check
    status = supervisor.get_status()
    assert status["total_plugins"] == 1
    assert "manual_src_1" in status["plugins"]

    # Pause and resume
    await supervisor.pause_plugin("manual_src_1")
    assert p.is_enabled is False

    await supervisor.resume_plugin("manual_src_1")
    assert p.is_enabled is True

    # Remove
    await supervisor.remove_plugin("manual_src_1")
    assert supervisor.get_plugin("manual_src_1") is None
    assert len(supervisor.list_plugins()) == 0
