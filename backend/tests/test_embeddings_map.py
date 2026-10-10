"""
backend/tests/test_embeddings_map.py - Tests for Embeddings Relevance Map & Trustworthiness
==========================================================================================
Verifies text document formulation, batching, deduplication, PCA 2D projection,
multi-source corroboration scoring, failover chains, and REST endpoints.
"""

import asyncio
from datetime import datetime, timezone
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.app.config import get_settings
from backend.app.db.models import Base, Event, EventEmbedding, Theme, utc_now
from backend.app.main import create_app
from backend.app.services.embedding_service import (
    EmbeddingService,
    build_embedding_document,
    build_semantic_embedding_text,
    generate_deterministic_mock_embedding,
    get_embedding_service,
    truncate_at_word_boundary,
)


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
async def test_build_embedding_document_format():
    """Verify composite text formulation includes content, time, place, and field."""
    now = datetime(2026, 10, 9, 14, 30, tzinfo=timezone.utc)
    ev = Event(
        id="test-doc-1",
        theme_id=1,
        source_type="telegram",
        source_name="@RocketAlert",
        external_id="ext-1",
        title="Red Alert in Haifa Bay",
        headline="Missile Interception Over Haifa Bay",
        raw_text="IDF Air Defense intercepted a suspicious aerial target over Haifa.",
        summary="Air defense systems successfully intercepted a drone over Haifa Bay.",
        location_json={"name": "Haifa Bay", "lat": 32.8, "lon": 35.0},
        entities_json=["IDF", "Haifa", "Hezbollah"],
        published_at=now,
    )

    doc = build_embedding_document(ev, theme_name="US-Iran conflict")
    assert "Headline: Missile Interception Over Haifa Bay" in doc
    assert "Timestamp: 2026-10-09 14:30 UTC" in doc
    assert "Location: Haifa Bay" in doc
    assert "Domain: US-Iran conflict" in doc
    assert "IDF, Haifa, Hezbollah" in doc
    assert "Source: @RocketAlert (telegram)" in doc
    assert "Content: Air defense systems successfully intercepted" in doc


@pytest.mark.asyncio
async def test_deterministic_mock_embedding_dimensions_and_similarity():
    """Verify mock embeddings generate 3072d vectors and reflect semantic similarity."""
    v1 = generate_deterministic_mock_embedding("Missile launched in Persian Gulf near Strait of Hormuz")
    v2 = generate_deterministic_mock_embedding("Rocket and missile fired near Strait of Hormuz naval route")
    v3 = generate_deterministic_mock_embedding("Local municipal election results in Tokyo Japan")

    assert len(v1) == 3072
    assert len(v2) == 3072
    assert len(v3) == 3072

    import numpy as np
    sim_1_2 = float(np.dot(v1, v2))
    sim_1_3 = float(np.dot(v1, v3))

    assert sim_1_2 > sim_1_3
    assert sim_1_2 > 0.40  # Shared terms give high similarity


@pytest.mark.asyncio
async def test_embed_events_deduplication(async_test_session):
    """Verify events are embedded once and never re-embedded against DB."""
    # Seed theme
    theme = Theme(
        id=1,
        name="Middle East Security",
        scoring_prompt="Score threat 1-10",
        keywords=[],
        entities=[],
    )
    async_test_session.add(theme)
    await async_test_session.commit()

    events = [
        Event(
            id=f"ev-{i}",
            theme_id=1,
            source_type="rss",
            source_name="Reuters",
            external_id=f"ext-{i}",
            title=f"Event {i} Title",
            raw_text=f"Report details for event {i}",
            published_at=utc_now(),
        )
        for i in range(5)
    ]
    for ev in events:
        async_test_session.add(ev)
    await async_test_session.commit()

    service = EmbeddingService()

    # First call: embeds all 5
    res1 = await service.embed_events(events, async_test_session, use_mock=True)
    assert len(res1) == 5

    # Check DB
    stmt = select(EventEmbedding)
    db_embs = list((await async_test_session.execute(stmt)).scalars().all())
    assert len(db_embs) == 5

    # Second call: all are cached, zero new embeddings created
    res2 = await service.embed_events(events, async_test_session, use_mock=True)
    assert len(res2) == 5
    db_embs_after = list((await async_test_session.execute(stmt)).scalars().all())
    assert len(db_embs_after) == 5


@pytest.mark.asyncio
async def test_pca_2d_projection_bounds(async_test_session):
    """Verify PCA 2D coordinates are properly bounded within [-90, 90]."""
    service = EmbeddingService()
    vectors = [generate_deterministic_mock_embedding(f"Distinct topic report with unique event code {i}") for i in range(12)]

    coords, pca_model = service.compute_pca_2d(vectors)
    assert len(coords) == 12
    assert pca_model is not None

    for x, y in coords:
        assert -90.0 <= x <= 90.0
        assert -90.0 <= y <= 90.0


@pytest.mark.asyncio
async def test_trustworthiness_scoring_multi_source_vs_outlier():
    """Verify multi-source corroboration yields high trust while outliers yield low trust."""
    service = EmbeddingService()

    # 3 corroborating reports from different sources about the same naval incident
    e1 = Event(
        id="c1",
        theme_id=1,
        source_type="telegram",
        source_name="@NavalAlerts",
        external_id="1",
        title="Tanker seized in Strait of Hormuz",
        raw_text="Commercial tanker boarded by armed speedboats near Hormuz.",
        published_at=utc_now(),
    )
    e2 = Event(
        id="c2",
        theme_id=1,
        source_type="newsapi",
        source_name="Reuters",
        external_id="2",
        title="Armed forces board oil tanker in Hormuz Strait",
        raw_text="Maritime security sources report commercial oil tanker boarded in Strait of Hormuz.",
        published_at=utc_now(),
    )
    e3 = Event(
        id="c3",
        theme_id=1,
        source_type="rss",
        source_name="AP News",
        external_id="3",
        title="Vessel intercepted in Persian Gulf waters",
        raw_text="US Navy reports commercial vessel boarded near Strait of Hormuz.",
        published_at=utc_now(),
    )
    # 1 completely isolated outlier
    e4 = Event(
        id="outlier",
        theme_id=1,
        source_type="reddit",
        source_name="r/Conspiracy",
        external_id="4",
        title="Unverified rumor of alien base discovered under Antarctic ice sheet",
        raw_text="Extraterrestrial pyramid found under polar ice shelf in Antarctica.",
        published_at=utc_now(),
    )

    events = [e1, e2, e3, e4]
    docs = [build_embedding_document(e) for e in events]
    vectors = [generate_deterministic_mock_embedding(d) for d in docs]

    node_metrics, edges, clusters = service.compute_trustworthiness_and_corroboration(
        events, vectors
    )

    # c1, c2, c3 should have high trustworthiness and corroborating sources
    m1 = next(m for m in node_metrics if m["event_id"] == "c1")
    assert m1["trustworthiness_score"] >= 75
    assert len(m1["corroborating_sources"]) >= 2
    assert m1["trustworthiness_label"] in ("Highly Corroborated", "Developing / Verified")

    # outlier should have low score
    m_out = next(m for m in node_metrics if m["event_id"] == "outlier")
    assert m_out["trustworthiness_score"] <= 45
    assert m_out["trustworthiness_label"] in ("Isolated Outlier", "Single-Source Unverified")


@pytest.mark.asyncio
async def test_embeddings_rest_api_endpoints():
    """Verify /api/embeddings/map, /status, and /backfill endpoints via FastAPI test client."""
    app = create_app()
    transport = ASGITransport(app=app)
    settings = get_settings()

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"X-API-Key": settings.API_KEY}

        # 1. Status endpoint
        resp_status = await client.get("/api/embeddings/status", headers=headers)
        assert resp_status.status_code == 200
        data_status = resp_status.json()
        assert "total_events" in data_status
        assert "embedded_events" in data_status
        assert "primary_model" in data_status

        # 2. Map endpoint
        resp_map = await client.get("/api/embeddings/map", headers=headers)
        assert resp_map.status_code == 200
        data_map = resp_map.json()
        assert "nodes" in data_map
        assert "edges" in data_map
        assert "clusters" in data_map
        assert "stats" in data_map

        # 3. Backfill endpoint
        resp_backfill = await client.post("/api/embeddings/backfill?limit=10", headers=headers)
        assert resp_backfill.status_code == 200
        data_backfill = resp_backfill.json()
        assert "status" in data_backfill


@pytest.mark.asyncio
async def test_failover_gemini_1_to_gemini_2_and_hf_tei(monkeypatch):
    """Verify that 429 quota exhaustion on Gemini 1 cleanly fails over to Gemini 2."""
    service = EmbeddingService(api_key="valid-test-key")

    call_log = []

    async def mock_call_gemini(texts, model, api_key):
        call_log.append(model)
        if model == "gemini-embedding-001":
            raise RuntimeError("QUOTA_EXCEEDED: 429 rate limit exceeded")
        # Gemini 2 succeeds
        return [generate_deterministic_mock_embedding(t) for t in texts]

    monkeypatch.setattr(service, "call_gemini_batch_api", mock_call_gemini)

    vectors, used_model = await service.generate_embeddings_batch(
        texts=["Report on drone activity over border area"],
        preferred_model="gemini-embedding-001",
    )

    assert used_model == "gemini-embedding-2"
    assert call_log == ["gemini-embedding-001", "gemini-embedding-2"]
    assert len(vectors) == 1
    assert len(vectors[0]) == 3072


def test_truncate_at_word_boundary_no_cut_words():
    """Verify cluster labels never truncate in the middle of a word."""
    raw = "Trump claims gas prices are dropping after diplomatic talks"
    truncated = truncate_at_word_boundary(raw, max_len=24)
    assert truncated == "Trump claims gas prices…"
    assert not truncated.endswith("pri…")

    short = "Riyadh Airport Flights"
    assert truncate_at_word_boundary(short, max_len=28) == "Riyadh Airport Flights"


def test_semantic_embedding_text_strips_boilerplate():
    """Verify semantic embedding text omits timestamp, source handle, and domain boilerplate."""
    now = datetime(2026, 10, 9, 14, 30, tzinfo=timezone.utc)
    ev = Event(
        id="sem-1",
        theme_id=1,
        source_type="telegram",
        source_name="@IntelChannel",
        external_id="sem-ext-1",
        title="Ballistic missile launch detected near Tabriz",
        headline="Ballistic Missile Launch Near Tabriz",
        raw_text="Multiple witnesses report missile launch from northwestern Iran.",
        summary="Ballistic missile launch reported near Tabriz in northwestern Iran.",
        location_json={"name": "Tabriz", "lat": 38.08, "lon": 46.29},
        entities_json=["IRGC", "Tabriz"],
        published_at=now,
    )
    sem_text = build_semantic_embedding_text(ev)
    assert "Ballistic Missile Launch Near Tabriz" in sem_text
    assert "IRGC, Tabriz" in sem_text
    assert "Timestamp:" not in sem_text
    assert "Source:" not in sem_text
    assert "Domain:" not in sem_text


def test_galaxy_coordinates_non_overlapping_and_perimeter():
    """Verify story galaxies never overlap and cluster 0 stays on the outer perimeter."""
    import math
    service = EmbeddingService()

    # Create 3 distinct corroborated clusters + 1 outlier
    topics = [
        ("Hormuz naval tanker boarded by IRGC speedboats", "@NavalWatch", "telegram"),
        ("Hormuz naval tanker boarded by IRGC forces", "Reuters", "newsapi"),
        ("Hormuz naval tanker boarded near Persian Gulf", "AP News", "rss"),
        ("Natanz centrifuge facility power outage reported", "BBC", "rss"),
        ("Natanz centrifuge nuclear site power grid incident", "Al Jazeera", "newsapi"),
        ("Natanz centrifuge facility blackout confirmed", "@OSINTDefender", "telegram"),
        ("Riyadh airport flights suspended amid airspace alert", "Arab News", "rss"),
        ("Riyadh airport flights grounded following drone alert", "Reuters", "newsapi"),
        ("Unconfirmed rumor of minor cyber outage in municipal water utility", "RedditUser", "reddit"),
    ]

    events = [
        Event(
            id=f"g-{idx}",
            theme_id=1,
            source_type=src_type,
            source_name=src_name,
            external_id=f"g-ext-{idx}",
            title=text,
            headline=text,
            raw_text=text,
            importance_score=7,
            published_at=utc_now(),
        )
        for idx, (text, src_name, src_type) in enumerate(topics)
    ]
    vectors = [generate_deterministic_mock_embedding(build_semantic_embedding_text(e)) for e in events]
    node_metrics, edges, clusters = service.compute_trustworthiness_and_corroboration(events, vectors)
    final_coords = service.compute_galaxy_coordinates(vectors, clusters, node_metrics)

    assert len(final_coords) == len(events)

    story_clusters = [c for c in clusters if c["id"] > 0]
    assert len(story_clusters) >= 2

    # Check that no two story cluster circles overlap
    for i in range(len(story_clusters)):
        for j in range(i + 1, len(story_clusters)):
            c1, c2 = story_clusters[i], story_clusters[j]
            dist = math.hypot(c1["center_x"] - c2["center_x"], c1["center_y"] - c2["center_y"])
            assert dist >= (c1["radius"] + c2["radius"] - 0.5)

    # Check that each story cluster has exactly one lead node
    for c in story_clusters:
        leads = [m for m in node_metrics if m["cluster_id"] == c["id"] and m.get("is_cluster_lead")]
        assert len(leads) == 1


@pytest.mark.asyncio
async def test_theme_slug_matching_and_off_theme_filtering(async_test_session):
    """Verify hyphenated theme slug matches 'US-Iran conflict' and filters Ukraine/Russia noise."""
    theme = Theme(
        id=1,
        name="US-Iran conflict",
        scoring_prompt="US-Iran conflict intelligence",
        keywords=["iran", "hormuz"],
        entities=["IRGC", "CENTCOM"],
    )
    async_test_session.add(theme)

    on_theme_event = Event(
        id="ev-iran-1",
        theme_id=1,
        source_type="rss",
        source_name="Reuters",
        external_id="iran-1",
        title="IRGC naval vessels conduct live-fire drill near Strait of Hormuz",
        headline="IRGC Naval Drill Near Strait of Hormuz",
        raw_text="Iranian naval units conducted exercises near the Strait of Hormuz.",
        importance_score=8,
        published_at=utc_now(),
    )
    off_theme_ukraine_event = Event(
        id="ev-ukr-1",
        theme_id=1,
        source_type="rss",
        source_name="Pravda",
        external_id="ukr-1",
        title="Zelensky meets military commanders in Kyiv to discuss Donetsk frontline",
        headline="Zelensky Reviews Donetsk Frontline in Kyiv",
        raw_text="Ukrainian President Zelensky visited Kyiv command center regarding Russian offensive.",
        importance_score=7,
        published_at=utc_now(),
    )
    async_test_session.add_all([on_theme_event, off_theme_ukraine_event])
    await async_test_session.commit()

    service = EmbeddingService()
    map_data = await service.get_relevance_map_data(
        async_test_session,
        theme_id="us-iran-conflict",
        min_score=4,
    )

    node_ids = [n["id"] for n in map_data["nodes"]]
    assert "ev-iran-1" in node_ids
    assert "ev-ukr-1" not in node_ids

