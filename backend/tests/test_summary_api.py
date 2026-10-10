"""
backend/tests/test_summary_api.py - Tests for AI Situational Summary Engine & REST API
======================================================================================
Verifies cluster-first synthesis, embedding corroboration weighting, outlier separation,
OSINT telemetry fusion, smart caching & invalidation, and REST API endpoints.
"""

from datetime import timedelta
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.app.config import get_settings
from backend.app.db.models import Base, Event, Theme, utc_now
from backend.app.main import create_app
from backend.app.pipeline.orchestrator import get_pipeline_orchestrator
from backend.app.services.summary_service import SituationSummaryService


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
async def test_summary_service_cluster_synthesis_and_outliers(async_test_session, monkeypatch):
    """
    Verify SituationSummaryService embeds un-embedded reports, groups corroborated
    reports into semantic clusters, separates isolated outliers, and fuses telemetry.
    """
    monkeypatch.setenv("GEMINI_API_KEY", "mock_test_key")
    orchestrator = get_pipeline_orchestrator()
    monkeypatch.setattr(orchestrator, "use_mock", True)

    now = utc_now()
    theme = Theme(
        id=1,
        slug="us-iran-conflict",
        name="US-Iran conflict",
        description="Monitoring US-Iran naval and regional developments",
        keywords=["Strait of Hormuz", "IRGC", "CENTCOM"],
        entities=["IRGC", "US Navy", "CENTCOM"],
        scoring_prompt="Score 1-10",
        is_active=True,
        alert_threshold=7,
    )
    async_test_session.add(theme)

    # 3 corroborated events on the same naval storyline across 3 independent sources
    ev1 = Event(
        id="sum-ev-1",
        theme_id=1,
        source_type="rss",
        source_name="Reuters",
        external_id="ext-sum-1",
        title="IRGC fast attack craft intercept commercial tanker in Strait of Hormuz",
        headline="IRGC Naval Vessels Intercept Tanker in Strait of Hormuz",
        raw_text="IRGC naval forces approached and hailed a commercial tanker transiting the Strait of Hormuz near Bandar Abbas.",
        summary="BLUF: IRGC fast attack vessels intercepted a commercial oil tanker in the Strait of Hormuz, prompting US Navy Fifth Fleet surveillance.",
        importance_score=9,
        score_rationale="Direct maritime interception in strategic chokepoint.",
        entities_json=["IRGC", "US Navy", "Strait of Hormuz"],
        location_json={"name": "Strait of Hormuz", "lat": 26.56, "lon": 56.25},
        metadata_json={"trend_category": "Naval Escalation"},
        is_analyzed=True,
        published_at=now - timedelta(hours=2),
        ingested_at=now - timedelta(hours=2),
    )
    ev2 = Event(
        id="sum-ev-2",
        theme_id=1,
        source_type="newsapi",
        source_name="Associated Press",
        external_id="ext-sum-2",
        title="US Navy Fifth Fleet monitors IRGC tanker interception in Strait of Hormuz",
        headline="US Fifth Fleet Responds to IRGC Tanker Interception in Hormuz",
        raw_text="US Navy vessels and maritime patrol aircraft monitored IRGC naval fast attack craft intercepting a commercial tanker in the Strait of Hormuz.",
        summary="BLUF: US Navy Fifth Fleet assets responded after IRGC naval craft intercepted a commercial tanker in the Strait of Hormuz.",
        importance_score=8,
        score_rationale="Multi-source confirmation of naval confrontation.",
        entities_json=["IRGC", "US Navy", "Strait of Hormuz", "CENTCOM"],
        location_json={"name": "Strait of Hormuz", "lat": 26.56, "lon": 56.25},
        metadata_json={"trend_category": "Naval Escalation"},
        is_analyzed=True,
        published_at=now - timedelta(hours=1, minutes=30),
        ingested_at=now - timedelta(hours=1, minutes=30),
    )
    ev3 = Event(
        id="sum-ev-3",
        theme_id=1,
        source_type="telegram",
        source_name="@MaritimeOSINT",
        external_id="ext-sum-3",
        title="Commercial oil tanker hailed by IRGC navy boats in Strait of Hormuz",
        headline="Tanker Hailed by IRGC Fast Boats in Strait of Hormuz",
        raw_text="AIS tracking and VHF audio confirm IRGC naval fast attack craft intercepted a commercial tanker in the Strait of Hormuz.",
        summary="BLUF: Maritime AIS telemetry confirms IRGC fast attack craft intercepted a commercial tanker in the Strait of Hormuz.",
        importance_score=8,
        score_rationale="AIS and VHF confirmation of Hormuz naval incident.",
        entities_json=["IRGC", "Strait of Hormuz"],
        location_json={"name": "Strait of Hormuz", "lat": 26.56, "lon": 56.25},
        metadata_json={"trend_category": "Naval Escalation"},
        is_analyzed=True,
        published_at=now - timedelta(hours=1),
        ingested_at=now - timedelta(hours=1),
    )

    # 1 completely unrelated isolated outlier claim
    ev_outlier = Event(
        id="sum-ev-outlier",
        theme_id=1,
        source_type="reddit",
        source_name="r/UnverifiedRumors",
        external_id="ext-sum-outlier",
        title="Agricultural wheat harvest festival schedule announced in northern province",
        headline="Regional Agricultural Wheat Harvest Festival Announced",
        raw_text="Local municipal organizers announced the annual autumn wheat harvest agricultural fair and botanical exhibition.",
        summary="BLUF: Municipal authorities scheduled an autumn wheat harvest agricultural exhibition.",
        importance_score=3,
        score_rationale="Routine domestic agricultural news.",
        entities_json=["Ministry of Agriculture"],
        location_json={"name": "Karaj", "lat": 35.84, "lon": 50.93},
        metadata_json={"trend_category": "Domestic Affairs"},
        is_analyzed=True,
        published_at=now - timedelta(minutes=45),
        ingested_at=now - timedelta(minutes=45),
    )

    async_test_session.add_all([ev1, ev2, ev3, ev_outlier])
    await async_test_session.commit()

    service = SituationSummaryService()
    result = await service.generate_or_get_summary(
        session=async_test_session,
        theme_id="us-iran-conflict",
        time_range="24h",
        force_refresh=True,
    )

    assert result["cached"] is False
    assert result["theme_name"] == "US-Iran conflict"
    assert result["threat_trajectory"] in ("Escalating", "Elevated", "Volatile", "Stable", "De-escalating")
    assert result["threat_level_score"] >= 7
    assert "BLUF:" in result["executive_bluf"]
    assert len(result["strategic_assessment"]) > 20
    assert len(result["key_developments"]) >= 1
    assert len(result["watchlist_indicators"]) >= 2

    # Telemetry verification
    telemetry = result["telemetry"]
    assert telemetry["total_events"] == 4
    assert telemetry["embedded_events"] == 4
    assert telemetry["embedding_coverage_pct"] == 100.0
    assert telemetry["critical_alerts_count"] == 3
    assert telemetry["corroborated_reports_count"] >= 3
    assert telemetry["cross_source_edges_count"] >= 2
    assert any(e["name"] == "IRGC" for e in telemetry["top_entities"])
    assert any(l["name"] == "Strait of Hormuz" for l in telemetry["top_locations"])

    # Cluster verification
    clusters = result["clusters"]
    assert len(clusters) >= 1
    lead_cluster = clusters[0]
    assert lead_cluster["report_count"] == 3
    assert lead_cluster["avg_trustworthiness_score"] >= 70
    assert len(lead_cluster["independent_sources"]) == 3
    assert len(lead_cluster["events"]) == 3

    # Outlier verification
    outliers = result["unverified_outliers"]
    assert len(outliers["events"]) >= 1
    assert any(e["id"] == "sum-ev-outlier" for e in outliers["events"])


@pytest.mark.asyncio
async def test_summary_caching_and_invalidation(async_test_session, monkeypatch):
    """
    Verify smart caching returns cached summary when unchanged,
    and invalidates when force_refresh=True or when a new event is ingested.
    """
    monkeypatch.setenv("GEMINI_API_KEY", "mock_test_key")
    orchestrator = get_pipeline_orchestrator()
    monkeypatch.setattr(orchestrator, "use_mock", True)

    now = utc_now()
    theme = Theme(
        id=1,
        slug="us-iran-conflict",
        name="US-Iran conflict",
        scoring_prompt="Score 1-10",
        is_active=True,
        alert_threshold=7,
    )
    async_test_session.add(theme)
    ev1 = Event(
        id="cache-ev-1",
        theme_id=1,
        source_type="rss",
        source_name="Reuters",
        external_id="ext-c-1",
        title="Naval patrol reported in Persian Gulf",
        headline="Naval Patrol Reported in Persian Gulf",
        raw_text="Routine naval patrol reported in the Persian Gulf.",
        summary="BLUF: Naval patrol activity observed in the Persian Gulf.",
        importance_score=6,
        published_at=now - timedelta(hours=1),
        ingested_at=now - timedelta(hours=1),
    )
    async_test_session.add(ev1)
    await async_test_session.commit()

    service = SituationSummaryService()

    # 1. First call generates fresh summary
    first = await service.generate_or_get_summary(
        session=async_test_session, theme_id="us-iran-conflict", time_range="all"
    )
    assert first["cached"] is False
    first_id = first["id"]

    # 2. Second call with force_refresh=False returns cached summary
    second = await service.generate_or_get_summary(
        session=async_test_session, theme_id="us-iran-conflict", time_range="all", force_refresh=False
    )
    assert second["cached"] is True
    assert second["id"] == first_id

    # 3. Call with force_refresh=True regenerates
    third = await service.generate_or_get_summary(
        session=async_test_session, theme_id="us-iran-conflict", time_range="all", force_refresh=True
    )
    assert third["cached"] is False
    assert third["id"] != first_id

    # 4. Ingesting a new event invalidates the cache automatically
    ev2 = Event(
        id="cache-ev-2",
        theme_id=1,
        source_type="telegram",
        source_name="@IntelFeed",
        external_id="ext-c-2",
        title="Air defense radar activation near Isfahan",
        headline="Air Defense Radar Activation Near Isfahan",
        raw_text="Air defense radar activation detected near Isfahan.",
        summary="BLUF: Air defense radar activation observed.",
        importance_score=8,
        published_at=now,
        ingested_at=now,
    )
    async_test_session.add(ev2)
    await async_test_session.commit()

    fourth = await service.generate_or_get_summary(
        session=async_test_session, theme_id="us-iran-conflict", time_range="all", force_refresh=False
    )
    assert fourth["cached"] is False
    assert fourth["telemetry"]["total_events"] == 2


@pytest.mark.asyncio
async def test_summary_rest_api_endpoints(monkeypatch):
    """Verify GET /api/summary and POST /api/summary/generate via FastAPI AsyncClient."""
    monkeypatch.setenv("GEMINI_API_KEY", "mock_test_key")
    orchestrator = get_pipeline_orchestrator()
    monkeypatch.setattr(orchestrator, "use_mock", True)

    app = create_app()
    transport = ASGITransport(app=app)
    settings = get_settings()

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"X-API-Key": settings.API_KEY}

        # 1. GET /api/summary
        resp = await client.get("/api/summary?time_range=all", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "executive_bluf" in data
        assert "threat_trajectory" in data
        assert "clusters" in data
        assert "unverified_outliers" in data
        assert "watchlist_indicators" in data
        assert "telemetry" in data

        # 2. POST /api/summary/generate
        resp_gen = await client.post("/api/summary/generate?time_range=all", headers=headers)
        assert resp_gen.status_code == 200
        data_gen = resp_gen.json()
        assert data_gen["cached"] is False
        assert "executive_bluf" in data_gen
