"""
backend/tests/test_m2_challenger_stress.py - Challenger Empirical Stress Test Suite

Adversarial Verification for Milestone 2:
1. Fault Injection into RSS, NewsAPI, and AIS plugins (HTTP 500, 502, 503, 504,
   connection failures, timeouts, unhandled exceptions) while verifying supervisor
   continues running unaffected plugins and persists events without interruption.
2. Rapid-fire 10+ config reload payloads over Redis channel 'osint:config:reload'
   verifying supervisor task reconciliation under concurrency without deadlocks,
   race conditions, or leaked tasks.
"""

import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.app.db.models import Base, Event, Source, Theme
from backend.app.plugins.ais_plugin import AISSourcePlugin
from backend.app.plugins.base import BaseSourcePlugin, Location, NormalizedEvent
from backend.app.plugins.newsapi_plugin import NewsAPISourcePlugin
from backend.app.plugins.reddit_plugin import RedditSourcePlugin
from backend.app.plugins.rss_plugin import RSSSourcePlugin
from backend.app.plugins.supervisor import IngestionSupervisor, create_plugin_instance
from backend.app.services.redis_bus import (
    CHANNEL_CONFIG_RELOAD,
    CHANNEL_EVENTS_NEW,
    RedisBus,
)


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


# ==============================================================================
# Helper Mock Factories
# ==============================================================================

def make_rss_xml(title: str, guid: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0">
        <channel>
            <title>Healthy Feed Channel</title>
            <item>
                <title>{title}</title>
                <link>https://example.com/news/{guid}</link>
                <description>Intelligence report for {title}</description>
                <guid>{guid}</guid>
                <pubDate>Wed, 07 Oct 2026 00:00:00 GMT</pubDate>
            </item>
        </channel>
    </rss>"""


def make_gdelt_json(title: str, url: str) -> Dict[str, Any]:
    return {
        "articles": [
            {
                "url": url,
                "title": title,
                "seendate": "20261007T000000Z",
                "domain": "reuters.com",
                "language": "English",
                "sourcecountry": "US",
            }
        ]
    }


def make_ais_geojson(mmsi: int, lat: float, lon: float) -> Dict[str, Any]:
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": {
                    "mmsi": mmsi,
                    "sog": 12.0,
                    "cog": 90.0,
                    "shipName": f"PATROL-{mmsi}",
                    "destination": "BANDAR ABBAS",
                    "timestampExternal": int(datetime.now(timezone.utc).timestamp() * 1000),
                },
            }
        ],
    }


# ==============================================================================
# TEST SUITE 1: Fault Injection & Plugin Error Isolation
# ==============================================================================

@pytest.mark.asyncio
async def test_fault_injection_rss_newsapi_ais_isolation(monkeypatch):
    """
    Stress Test 1.1: Multi-Plugin Fault Injection
    Simultaneously injects HTTP 500, ConnectTimeout, and ReadTimeout into:
    - RSS plugin (broken feed endpoint)
    - NewsAPI plugin (broken GDELT endpoint)
    - AIS plugin (broken Digitraffic endpoint)
    While running alongside healthy RSS, NewsAPI, and AIS plugins.

    Verifies:
    1. Supervisor continues running unaffected plugins.
    2. Unaffected plugins continue generating NormalizedEvents.
    3. Events from unaffected plugins are persisted to DB and published to Redis bus.
    4. Injected plugins quarantine errors (status becomes degraded/error, last_error populated).
    5. No task or supervisor crashes.
    """
    # In-memory SQLite DB
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)

    bus = RedisBus()
    supervisor = IngestionSupervisor(session_factory=session_maker, redis_bus=bus)

    # Seed theme
    async with session_maker() as session:
        theme = Theme(
            name="US-Iran Conflict",
            description="Stress test theme",
            is_active=True,
            keywords=["Persian Gulf", "Hormuz"],
            scoring_prompt="Assess tensions",
        )
        session.add(theme)
        await session.commit()
        theme_id = str(theme.id)

    # Redis event collector
    collected_redis_events: List[Dict[str, Any]] = []

    async def on_new_event(ch: str, msg: Any):
        collected_redis_events.append(msg if isinstance(msg, dict) else json.loads(msg))

    bus.subscribe(CHANNEL_EVENTS_NEW, on_new_event)

    # State tracking for mock routing
    failing_urls = {
        "https://broken-rss.test/feed": "http_500",
        "https://api.gdeltproject.org/api/v2/doc/doc?query=broken_news": "timeout",
        "https://broken-ais.test/locations": "connect_error",
    }

    # Selective mock routing: fail specific endpoints, serve healthy ones
    async def mock_selective_get(self, url, *args, **kwargs):
        url_str = str(url)
        if "broken-rss.test" in url_str:
            raise httpx.HTTPStatusError(
                "500 Internal Server Error",
                request=httpx.Request("GET", url),
                response=httpx.Response(500, text="Internal Server Error"),
            )
        elif "broken_news" in url_str:
            raise httpx.ReadTimeout("Read operation timed out after 15.0s", request=httpx.Request("GET", url))
        elif "broken-ais.test" in url_str:
            raise httpx.ConnectError("Failed to establish a new connection: [Errno 111] Connection refused", request=httpx.Request("GET", url))
        elif "healthy-rss.test" in url_str:
            return httpx.Response(200, text=make_rss_xml("Healthy Naval Movement Update", "healthy_rss_001"))
        elif "healthy_gdelt" in url_str:
            return httpx.Response(200, json=make_gdelt_json("Healthy Diplomatic Channel Active", "https://reuters.com/healthy/1"))
        elif "healthy-ais.test" in url_str:
            return httpx.Response(200, json=make_ais_geojson(999888777, 26.5, 56.4))
        else:
            return httpx.Response(200, text="OK")

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_selective_get)

    # Create 3 FAILING plugins
    rss_failing = RSSSourcePlugin(
        source_id="rss_fail_1",
        theme_id=theme_id,
        name="Broken RSS Outlet",
        config={"url": "https://broken-rss.test/feed"},
        poll_interval=1,
    )
    news_failing = NewsAPISourcePlugin(
        source_id="news_fail_1",
        theme_id=theme_id,
        name="Broken GDELT Feed",
        config={"provider": "gdelt", "query": "broken_news"},
        poll_interval=1,
    )
    ais_failing = AISSourcePlugin(
        source_id="ais_fail_1",
        theme_id=theme_id,
        name="Broken AIS Telemetry",
        config={"provider": "digitraffic", "endpoint": "https://broken-ais.test/locations"},
        poll_interval=1,
    )

    # Create 3 HEALTHY plugins
    rss_healthy = RSSSourcePlugin(
        source_id="rss_healthy_1",
        theme_id=theme_id,
        name="Healthy RSS Wire",
        config={"url": "https://healthy-rss.test/feed"},
        poll_interval=1,
    )
    news_healthy = NewsAPISourcePlugin(
        source_id="news_healthy_1",
        theme_id=theme_id,
        name="Healthy GDELT Stream",
        config={"provider": "gdelt", "query": "healthy_gdelt"},
        poll_interval=1,
    )
    ais_healthy = AISSourcePlugin(
        source_id="ais_healthy_1",
        theme_id=theme_id,
        name="Healthy Hormuz AIS",
        config={"provider": "digitraffic", "endpoint": "https://healthy-ais.test/locations", "location_name": "Strait of Hormuz"},
        poll_interval=1,
    )

    # Register all 6 plugins into supervisor
    for p in [rss_failing, news_failing, ais_failing, rss_healthy, news_healthy, ais_healthy]:
        await supervisor.add_plugin(p)

    supervisor._running = True
    for sid, p in supervisor.plugins.items():
        supervisor._start_plugin_task(sid, p)

    # Run for 2.5 seconds (at least 2 poll cycles with poll_interval=1)
    await asyncio.sleep(2.5)

    # Check status of supervisor and plugins
    status = supervisor.get_status()
    assert status["is_running"] is True
    assert status["total_plugins"] == 6
    assert status["active_tasks"] == 6  # All 6 tasks remain alive (failing tasks do NOT exit)

    # Verify failing plugins were quarantined with errors
    assert rss_failing.status in ("degraded", "error")
    assert "HTTP fetch failure" in (rss_failing.last_error or "")

    assert news_failing.status in ("active", "degraded", "error")
    assert "GDELT query failure" in (news_failing.last_error or "")

    assert ais_failing.status in ("active", "degraded", "error")
    assert "Digitraffic AIS fetch error" in (ais_failing.last_error or "")

    # Verify healthy plugins successfully ingested events
    assert rss_healthy.events_count >= 1
    assert news_healthy.events_count >= 1
    assert ais_healthy.events_count >= 1

    # Verify healthy events were persisted to Database
    async with session_maker() as session:
        events_res = await session.execute(select(Event))
        saved_events = events_res.scalars().all()
        saved_types = {e.source_type for e in saved_events}
        assert "rss" in saved_types
        assert "newsapi" in saved_types
        assert "ais" in saved_types
        assert len(saved_events) >= 3

    # Verify healthy events reached Redis Bus
    assert len(collected_redis_events) >= 3
    redis_source_types = {e["source_type"] for e in collected_redis_events}
    assert "rss" in redis_source_types
    assert "newsapi" in redis_source_types
    assert "ais" in redis_source_types

    await supervisor.stop()
    await engine.dispose()


@pytest.mark.asyncio
async def test_fault_recovery_after_transient_failure(monkeypatch):
    """
    Stress Test 1.2: Transient Fault Recovery
    Injects an outage (HTTP 503 Service Unavailable) into an RSS and an AIS plugin,
    verifies graceful error state, then heals the endpoint and verifies automatic recovery.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)

    bus = RedisBus()
    supervisor = IngestionSupervisor(session_factory=session_maker, redis_bus=bus)

    outage_active = True

    async def mock_flapping_get(self, url, *args, **kwargs):
        nonlocal outage_active
        if outage_active:
            raise httpx.HTTPStatusError("503 Service Unavailable", request=None, response=httpx.Response(503))
        else:
            if "flapping-rss" in str(url):
                return httpx.Response(200, text=make_rss_xml("Recovered RSS Event", "recov_001"))
            elif "flapping-ais" in str(url):
                return httpx.Response(200, json=make_ais_geojson(123456789, 25.0, 55.0))
            return httpx.Response(200, text="OK")

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_flapping_get)

    p_rss = RSSSourcePlugin("recov_rss", 1, "Flapping RSS", config={"url": "https://flapping-rss.test"}, poll_interval=1)
    p_ais = AISSourcePlugin("recov_ais", 1, "Flapping AIS", config={"provider": "digitraffic", "endpoint": "https://flapping-ais.test"}, poll_interval=1)

    await supervisor.add_plugin(p_rss)
    await supervisor.add_plugin(p_ais)
    supervisor._running = True
    supervisor._start_plugin_task("recov_rss", p_rss)
    supervisor._start_plugin_task("recov_ais", p_ais)

    # Phase 1: Outage active
    await asyncio.sleep(1.2)
    assert p_rss.last_error is not None
    assert p_rss.events_count == 0

    # Phase 2: Heal the network!
    outage_active = False
    await asyncio.sleep(1.5)

    # Verified automatic recovery
    assert p_rss.status == "active"
    assert p_rss.last_error is None
    assert p_rss.events_count >= 1

    assert p_ais.status == "active"
    assert p_ais.last_error is None
    assert p_ais.events_count >= 1

    await supervisor.stop()
    await engine.dispose()


@pytest.mark.asyncio
async def test_unhandled_exception_in_plugin_poll_is_quarantined():
    """
    Stress Test 1.3: Unhandled Exception Quarantine
    A rogue plugin raises an unhandled ZeroDivisionError in its poll() method.
    The supervisor's _run_plugin_worker must catch it, record status='error',
    and keep peer plugins running without crashing the loop.
    """
    class RoguePlugin(BaseSourcePlugin):
        async def poll(self) -> List[NormalizedEvent]:
            # Simulate catastrophic unexpected bug in plugin
            return 1 / 0  # ZeroDivisionError

        async def test_connection(self) -> bool:
            return False

    class InnocentPlugin(BaseSourcePlugin):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.polled = 0

        async def poll(self) -> List[NormalizedEvent]:
            self.polled += 1
            return [
                NormalizedEvent(
                    theme_id=self.theme_id,
                    source_type="rss",
                    source_name=self.name,
                    external_id=f"innocent_{self.polled}",
                    title="Innocent Title",
                    raw_text="Innocent Content",
                )
            ]

        async def test_connection(self) -> bool:
            return True

    supervisor = IngestionSupervisor()
    p_rogue = RoguePlugin("rogue_1", 1, "Rogue Crash Plugin", poll_interval=1)
    p_innocent = InnocentPlugin("innocent_1", 1, "Innocent Peer Plugin", poll_interval=1)

    await supervisor.add_plugin(p_rogue)
    await supervisor.add_plugin(p_innocent)
    supervisor._running = True
    supervisor._start_plugin_task("rogue_1", p_rogue)
    supervisor._start_plugin_task("innocent_1", p_innocent)

    await asyncio.sleep(1.5)

    assert p_rogue.status == "error"
    assert "division by zero" in (p_rogue.last_error or "")
    assert p_innocent.polled >= 1

    await supervisor.stop()


# ==============================================================================
# TEST SUITE 2: Rapid-Fire Config Reload & Concurrency Stress
# ==============================================================================

@pytest.mark.asyncio
async def test_rapid_fire_10_config_reloads_concurrency_and_no_deadlocks():
    """
    Stress Test 2.1: Rapid-Fire 10 Config Reloads over Redis Channel
    Fires 10 concurrent reload payloads across osint:config:reload in sub-second time.
    Verifies:
    1. Supervisor reconciles without deadlocking on its asyncio.Lock.
    2. All 10 reload tasks complete cleanly.
    3. No zombie/leaked worker tasks remain.
    4. Worker tasks correspond 1:1 with enabled sources.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)

    bus = RedisBus()
    supervisor = IngestionSupervisor(session_factory=session_maker, redis_bus=bus)

    # Seed theme with 3 sources
    async with session_maker() as session:
        theme = Theme(name="US-Iran Theme", is_active=True)
        session.add(theme)
        await session.flush()

        s1 = Source(theme_id=theme.id, name="Src 1", source_type="rss", config_json={"url": "https://s1.test"}, is_enabled=True, polling_interval_sec=60)
        s2 = Source(theme_id=theme.id, name="Src 2", source_type="newsapi", config_json={"query": "q2"}, is_enabled=True, polling_interval_sec=60)
        s3 = Source(theme_id=theme.id, name="Src 3", source_type="ais", config_json={}, is_enabled=False, polling_interval_sec=60)
        session.add_all([s1, s2, s3])
        await session.commit()

    await supervisor.start()
    assert len(supervisor.plugins) == 3
    # 2 enabled sources -> 2 running tasks
    assert len([t for t in supervisor._tasks.values() if not t.done()]) == 2

    # Rapid-fire 10 concurrent config reload payloads
    reload_actions = [
        {"action": "source_update", "burst_id": i, "timestamp": datetime.now(timezone.utc).isoformat()}
        for i in range(10)
    ]

    # Fire all 10 simultaneously
    publish_coros = [bus.publish(CHANNEL_CONFIG_RELOAD, payload) for payload in reload_actions]
    await asyncio.gather(*publish_coros)

    # Allow all tasks to settle
    await asyncio.sleep(0.5)

    # Verify no deadlocks: supervisor is responsive and running
    assert supervisor.is_running is True
    assert len(supervisor.plugins) == 3

    # Verify exactly 2 active tasks (s1 and s2 enabled, s3 disabled)
    active_tasks = [t for t in supervisor._tasks.values() if not t.done()]
    assert len(active_tasks) == 2

    # Verify task names match expected plugins
    task_names = [t.get_name() for t in active_tasks]
    assert any("Src 1" in name for name in task_names)
    assert any("Src 2" in name for name in task_names)

    # Check for leaked tasks in the entire event loop
    all_current_tasks = asyncio.all_tasks()
    osint_tasks = [t for t in all_current_tasks if t.get_name().startswith("osint_worker_") and not t.done()]
    assert len(osint_tasks) == 2, f"Expected 2 running worker tasks, found {len(osint_tasks)}: {[t.get_name() for t in osint_tasks]}"

    await supervisor.stop()
    await engine.dispose()


@pytest.mark.asyncio
async def test_rapid_fire_reloads_with_concurrent_database_mutations():
    """
    Stress Test 2.2: Dynamic Churn Under Rapid-Fire Reloads
    While firing rapid reloads, simultaneously mutate the database:
    - Add new sources
    - Disable active sources
    - Update polling intervals
    - Delete a source

    Verifies that the supervisor's running tasks converge to the exact final database state
    without task leakage, duplicated workers, or orphaned tasks.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)

    bus = RedisBus()
    supervisor = IngestionSupervisor(session_factory=session_maker, redis_bus=bus)

    # Initial setup
    async with session_maker() as session:
        theme = Theme(name="Dynamic Theme", is_active=True)
        session.add(theme)
        await session.flush()
        theme_id = theme.id

        src_a = Source(id=1, theme_id=theme_id, name="Source A", source_type="rss", config_json={"url": "https://a.test"}, is_enabled=True, polling_interval_sec=30)
        src_b = Source(id=2, theme_id=theme_id, name="Source B", source_type="newsapi", config_json={"query": "b"}, is_enabled=True, polling_interval_sec=30)
        session.add_all([src_a, src_b])
        await session.commit()

    await supervisor.start()
    assert len(supervisor.plugins) == 2
    assert len([t for t in supervisor._tasks.values() if not t.done()]) == 2

    # Perform a sequence of 10 interleaved DB mutations and reload dispatches
    for step in range(10):
        async with session_maker() as session:
            if step == 1:
                # Add Source C
                session.add(Source(id=3, theme_id=theme_id, name="Source C", source_type="ais", config_json={}, is_enabled=True, polling_interval_sec=40))
            elif step == 3:
                # Disable Source A
                sa = (await session.execute(select(Source).where(Source.id == 1))).scalar_one()
                sa.is_enabled = False
            elif step == 5:
                # Add Source D (disabled)
                session.add(Source(id=4, theme_id=theme_id, name="Source D", source_type="reddit", config_json={}, is_enabled=False, polling_interval_sec=50))
            elif step == 7:
                # Re-enable Source A and change polling interval
                sa = (await session.execute(select(Source).where(Source.id == 1))).scalar_one()
                sa.is_enabled = True
                sa.polling_interval_sec = 15
            elif step == 8:
                # Delete Source B
                sb = (await session.execute(select(Source).where(Source.id == 2))).scalar_one()
                await session.delete(sb)
            await session.commit()

        # Fire reload after each mutation
        await bus.publish(CHANNEL_CONFIG_RELOAD, {"action": "source_update", "step": step})
        # Micro-delay simulating rapid asynchronous incoming requests
        await asyncio.sleep(0.05)

    # Allow convergence
    await asyncio.sleep(0.4)

    # Final DB state check:
    # Source A (id=1): enabled, interval=15
    # Source B (id=2): deleted
    # Source C (id=3): enabled, interval=40
    # Source D (id=4): disabled, interval=50
    # Expected managed plugins: 3 (Source A, Source C, Source D)
    # Expected running tasks: 2 (Source A, Source C)
    assert len(supervisor.plugins) == 3
    assert "2" not in supervisor.plugins  # Source B removed
    assert supervisor.plugins["1"].is_enabled is True
    assert supervisor.plugins["1"].poll_interval == 15
    assert supervisor.plugins["3"].is_enabled is True
    assert supervisor.plugins["4"].is_enabled is False

    active_tasks = {sid: t for sid, t in supervisor._tasks.items() if not t.done()}
    assert set(active_tasks.keys()) == {"1", "3"}

    # Leaked task check across whole event loop
    all_tasks = asyncio.all_tasks()
    worker_tasks = [t for t in all_tasks if t.get_name().startswith("osint_worker_") and not t.done()]
    assert len(worker_tasks) == 2

    await supervisor.stop()
    await engine.dispose()


@pytest.mark.asyncio
async def test_supervisor_stop_cleanly_aborts_in_flight_reloads():
    """
    Stress Test 2.3: Race Condition on Supervisor Shutdown
    Fires rapid reload messages and calls supervisor.stop() immediately.
    Verifies:
    1. supervisor.stop() cleans up all tasks.
    2. In-flight reload callbacks do not resurrect dead plugins after shutdown.
    3. All worker tasks are terminated.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)

    bus = RedisBus()
    supervisor = IngestionSupervisor(session_factory=session_maker, redis_bus=bus)

    async with session_maker() as session:
        theme = Theme(name="Shutdown Theme", is_active=True)
        session.add(theme)
        await session.flush()
        session.add(Source(theme_id=theme.id, name="Persistent Src", source_type="rss", config_json={}, is_enabled=True, polling_interval_sec=60))
        await session.commit()

    await supervisor.start()
    assert supervisor.is_running is True

    # Fire 5 reload messages and immediately stop
    for i in range(5):
        await bus.publish(CHANNEL_CONFIG_RELOAD, {"action": "reload_all", "i": i})

    # Call stop immediately
    await supervisor.stop()

    assert supervisor.is_running is False
    assert len(supervisor._tasks) == 0

    # Wait to ensure no delayed task re-spawns
    await asyncio.sleep(0.3)

    all_tasks = asyncio.all_tasks()
    lingering_workers = [t for t in all_tasks if t.get_name().startswith("osint_worker_") and not t.done()]
    assert len(lingering_workers) == 0, f"Found resurrected workers: {[t.get_name() for t in lingering_workers]}"

    await engine.dispose()


@pytest.mark.asyncio
async def test_malformed_and_edge_case_reload_payloads():
    """
    Stress Test 2.4: Malformed Reload Payloads
    Sends malformed JSON, integer, None, and empty payloads to osint:config:reload.
    Supervisor must handle them robustly without crashing or breaking active workers.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)

    bus = RedisBus()
    supervisor = IngestionSupervisor(session_factory=session_maker, redis_bus=bus)

    async with session_maker() as session:
        theme = Theme(name="Theme Robust", is_active=True)
        session.add(theme)
        await session.flush()
        session.add(Source(theme_id=theme.id, name="Src R", source_type="rss", config_json={}, is_enabled=True, polling_interval_sec=60))
        await session.commit()

    await supervisor.start()

    weird_payloads = [
        "",
        "NOT_JSON_RAW_STRING",
        "{malformed: json",
        None,
        12345,
        {"action": "unknown_action_xyz"},
        {"theme_id": None, "action": None},
        [],
    ]

    for p in weird_payloads:
        await bus.publish(CHANNEL_CONFIG_RELOAD, p)

    await asyncio.sleep(0.3)

    # Supervisor still running healthy
    assert supervisor.is_running is True
    assert len(supervisor.plugins) == 1
    assert len([t for t in supervisor._tasks.values() if not t.done()]) == 1

    await supervisor.stop()
    await engine.dispose()
