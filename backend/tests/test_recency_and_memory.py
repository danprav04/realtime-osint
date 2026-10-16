"""
backend/tests/test_recency_and_memory.py - Tests for Continuous Recency Weighting,
Multilingual Denial Stance Classification, Tiered Memory Compaction, and Precedent Baselines.
"""

import math
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch

from backend.app.services.embedding_service import (
    compute_recency_weight,
    EmbeddingService,
)
from backend.app.pipeline.arabic_normalizer import classify_claim_stance
from backend.app.pipeline.models import ScoringContext, Stage2Result
from backend.app.pipeline.stage2_score import calibrate_stage2_result
from backend.app.services.memory_rollup_service import (
    compact_aged_events,
    get_historical_context_and_baselines,
    get_dynamic_source_credibility,
)
from backend.app.db.models import (
    Base,
    Event,
    EventEmbedding,
    HistoricalRollup,
    Theme,
    Source,
)
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker


# ============================================================================
# 1. Continuous Exponential Recency Decay Tests
# ============================================================================

def test_compute_recency_weight_exponential_decay():
    """Verify continuous exponential decay w(dt) = max(0.10, 0.5^(dt / 18))."""
    now = datetime(2026, 10, 17, 12, 0, 0, tzinfo=timezone.utc)
    half_life = 18.0

    # At delta_t = 0, weight should be exactly 1.0
    w0 = compute_recency_weight(now, now=now, half_life_hours=half_life, min_weight=0.10)
    assert pytest.approx(w0, 0.01) == 1.0

    # At delta_t = 18 hours (1 half life), weight should be exactly 0.50
    t18 = now - timedelta(hours=18)
    w18 = compute_recency_weight(t18, now=now, half_life_hours=half_life, min_weight=0.10)
    assert pytest.approx(w18, 0.01) == 0.50

    # At delta_t = 36 hours (2 half lives), weight should be 0.25
    t36 = now - timedelta(hours=36)
    w36 = compute_recency_weight(t36, now=now, half_life_hours=half_life, min_weight=0.10)
    assert pytest.approx(w36, 0.01) == 0.25

    # At delta_t = 72 hours (4 half lives), weight should be ~0.0625, but capped at min_weight 0.10
    t72 = now - timedelta(hours=72)
    w72 = compute_recency_weight(t72, now=now, half_life_hours=half_life, min_weight=0.10)
    assert pytest.approx(w72, 0.001) == 0.10

    # Edge cases: future timestamp
    tfuture = now + timedelta(hours=2)
    w_future = compute_recency_weight(tfuture, now=now, half_life_hours=half_life)
    assert w_future == 1.0

    # String ISO format
    iso_str = (now - timedelta(hours=18)).isoformat()
    w_iso = compute_recency_weight(iso_str, now=now, half_life_hours=half_life)
    assert pytest.approx(w_iso, 0.01) == 0.50

    # Invalid / None inputs
    assert compute_recency_weight(None, now=now) == 1.0
    assert compute_recency_weight("not-a-date", now=now) == 1.0


# ============================================================================
# 2. Multilingual Denial & Contradiction Stance Classification Tests
# ============================================================================

def test_classify_claim_stance_multilingual_denials():
    """Verify denial/refutation trigger detection across Arabic, Farsi, Hebrew, and English."""
    
    # Arabic denial
    ar_text = "نفى المتحدث العسكري باسم القوات المسلحة وقوع أي استهداف للمطار"
    res_ar = classify_claim_stance(ar_text, "سقوط صواريخ قرب المطار")
    assert res_ar["is_contradiction"] is True
    assert res_ar["stance"] == "contradicting_or_denying"
    assert "نفى" in res_ar["matched_triggers"]

    # Farsi denial
    fa_text = "وزارت دفاع ایران گزارش‌ها درباره حمله پهپادی را تکذیب کرد"
    res_fa = classify_claim_stance(fa_text, "گزارش حمله به پایگاه")
    assert res_fa["is_contradiction"] is True
    assert res_fa["stance"] == "contradicting_or_denying"
    assert any("تکذیب" in trig for trig in res_fa["matched_triggers"])

    # Hebrew denial
    he_text = "דובר צהל הכחיש את הדיווחים הערביים על פגיעה בבסיס"
    res_he = classify_claim_stance(he_text, "דיווח על פגיעה בבסיס")
    assert res_he["is_contradiction"] is True
    assert res_he["stance"] == "contradicting_or_denying"
    assert any("הכחיש" in trig for trig in res_he["matched_triggers"])

    # English denial
    en_text = "Defense Ministry officials rejected claims of an unprovoked missile strike"
    res_en = classify_claim_stance(en_text, "Claims of missile strike")
    assert res_en["is_contradiction"] is True
    assert res_en["stance"] == "contradicting_or_denying"
    assert "rejected claims" in res_en["matched_triggers"]

    # Neutral corroborating text (no denial)
    neutral_text = "Explosions reported near the port facility following radar anomalies."
    res_n = classify_claim_stance(neutral_text, "Radar anomalies detected")
    assert res_n["is_contradiction"] is False
    assert res_n["stance"] == "neutral_progression"
    assert res_n["matched_triggers"] == []


# ============================================================================
# 3. Embedding Trustworthiness with Denial Stance Suppression
# ============================================================================

def test_compute_trustworthiness_and_corroboration_denial():
    """Verify that peer reports containing denial trigger Contested / Disputed trust label."""
    now = datetime(2026, 10, 17, 12, 0, 0, tzinfo=timezone.utc)
    
    ev1 = Event(
        id="ev-1",
        theme_id=1,
        source_id=1,
        source_name="Local Activist",
        source_type="telegram",
        external_id="ext-1",
        title="Airbase targeted by strike",
        headline="Airbase targeted by strike",
        summary="Explosions reported at military airbase",
        raw_text="Explosions reported at military airbase Semnan",
        importance_score=7,
        published_at=now,
    )

    # Peer 2 claims denial
    ev2 = Event(
        id="ev-2",
        theme_id=1,
        source_id=2,
        source_name="State News",
        source_type="rss",
        external_id="ext-2",
        title="Military officially denied reports of attack",
        headline="Military officially denied reports of attack",
        summary="Military authorities officially denied reports of any strike on the base.",
        raw_text="Authorities denied reports of any strike on the semnan base.",
        importance_score=6,
        published_at=now,
    )

    emb_svc = EmbeddingService()
    # High cosine similarity vector representation
    v1 = [1.0] + [0.0] * 63
    v2 = [0.99] + [0.01] * 63

    node_metrics, edges, clusters = emb_svc.compute_trustworthiness_and_corroboration(
        events=[ev1, ev2],
        embeddings=[v1, v2],
        similarity_threshold=0.75,
    )

    assert len(node_metrics) == 2
    # ev1 should be marked as Contested / Disputed due to ev2's official denial
    m1 = node_metrics[0]
    assert m1["trust_label"] == "Contested / Disputed"
    assert "Disputed report" in m1["verdict"]
    assert "State News" in m1["verdict"]


# ============================================================================
# 4. Stage 2 Guardrail Contradiction Score Protection
# ============================================================================

def test_stage2_guardrail_contradiction_protection():
    """Verify that when is_contradiction=True, the +1 corroboration bonus is suppressed."""
    s2 = Stage2Result(
        importance_score=7,
        priority_level="high",
        severity_score=7,
        novelty_score=6,
        verification_confidence="medium",
        confidence_score=6,
        rationale="Initial assessment",
        recommended_alert=True,
    )

    scoring_ctx = ScoringContext(
        is_contradiction=True,
        contradicting_sources=["Official Wire"],
        contradiction_details=["Official Wire refuted the report"],
        corroboration_status="Disputed / Conflicting Reports",
        recency_weighted_corroboration=0.0,
    )

    # Apply guardrails
    guarded = _apply_score_guardrails(
        s2=s2,
        event_dict={"source_type": "telegram", "source_name": "Channel X"},
        extracted_entities=["Airbase"],
        scoring_context=scoring_ctx,
    )

    breakdown = guarded.to_breakdown_dict()
    assert breakdown["is_contradiction"] is True
    assert breakdown["corroboration_status"] == "Disputed / Conflicting Reports"
    assert "Official Wire" in breakdown["contradicting_sources"]
    assert any("Dispute/denial identified" in note for note in guarded.calibration_notes)


# ============================================================================
# 5. Database Rollup Compaction & Vector Pruning Integration Test
# ============================================================================

@pytest.fixture
async def async_test_session():
    """Create an isolated in-memory SQLite database session."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    async_session = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with async_session() as session:
        yield session

    await engine.dispose()


@pytest.mark.asyncio
async def test_compact_aged_events_and_prune_routine_vectors(async_test_session):
    """Verify compacting events >7 days old into HistoricalRollup and pruning routine vectors."""
    session = async_test_session

    # 1. Create Theme & Source
    theme = Theme(id=1, slug="iran-regional", name="Iran Regional", keywords=[], entities=[])
    session.add(theme)
    source = Source(id=1, theme_id=1, name="Reuters", source_type="rss", target="https://test.rss")
    session.add(source)
    await session.commit()

    # 2. Create aged events (10 days old)
    aged_date = datetime.now(timezone.utc) - timedelta(days=10)
    
    # Event 1: Critical (importance_score = 9) -> Embedding vector must be PRESERVED
    ev1 = Event(
        id="aged-crit-1",
        theme_id=1,
        source_id=1,
        source_name="Reuters",
        source_type="rss",
        external_id="ext-1",
        title="Major Ballistic Missile Launch Detected",
        summary="Ballistic missile test conducted in desert facility.",
        raw_text="Ballistic missile launch detected from Semnan facility.",
        importance_score=9,
        is_analyzed=True,
        published_at=aged_date,
        entities_json=["Semnan", "IRGC"],
        location_json={"name": "Semnan", "lat": 35.5, "lon": 53.4},
    )
    session.add(ev1)
    emb1 = EventEmbedding(
        event_id="aged-crit-1",
        model_name="text-embedding-3-large",
        embedding_vector=[0.123] * 64,
        dimensions=64,
        created_at=aged_date,
    )
    session.add(emb1)

    # Event 2: Routine (importance_score = 4) -> Embedding vector must be PRUNED ([])
    ev2 = Event(
        id="aged-routine-2",
        theme_id=1,
        source_id=1,
        source_name="Reuters",
        source_type="rss",
        external_id="ext-2",
        title="Routine Diplomatic Meeting in Geneva",
        summary="Ambassadors discussed trade negotiations.",
        raw_text="Diplomats held bilateral discussions on tariffs.",
        importance_score=4,
        is_analyzed=True,
        published_at=aged_date,
        entities_json=["Geneva"],
        location_json={"name": "Geneva", "lat": 46.2, "lon": 6.1},
    )
    session.add(ev2)
    emb2 = EventEmbedding(
        event_id="aged-routine-2",
        model_name="text-embedding-3-large",
        embedding_vector=[0.456] * 64,
        dimensions=64,
        created_at=aged_date,
    )
    session.add(emb2)

    await session.commit()

    # 3. Run compaction (with synthetic embedding generator mock)
    mock_emb_svc = AsyncMock()
    mock_emb_svc.generate_embedding = AsyncMock(return_value=[0.999] * 64)

    with patch("backend.app.services.memory_rollup_service.get_embedding_service", return_value=mock_emb_svc):
        compaction_res = await compact_aged_events(age_days=7, session=session)

    assert compaction_res["status"] == "ok"
    assert compaction_res["rollups_created"] >= 1
    assert compaction_res["vectors_pruned"] == 1  # Exactly ev2 pruned

    # 4. Verify in DB
    # Critical event still has its vector
    await session.refresh(emb1)
    assert len(emb1.embedding_vector) == 64

    # Routine event has pruned vector
    await session.refresh(emb2)
    assert emb2.embedding_vector == []

    # Verify HistoricalRollup row
    rollups = (await session.execute(HistoricalRollup.__table__.select())).all()
    assert len(rollups) >= 1
    rollup = rollups[0]
    assert rollup.total_events == 2
    assert rollup.critical_events_count == 1
    assert rollup.max_importance_score == 9
    assert rollup.is_compacted is True


@pytest.mark.asyncio
async def test_historical_context_and_baselines_retrieval(async_test_session):
    """Verify retrieving historical precedents and entity baselines from rollups."""
    session = async_test_session

    rollup = HistoricalRollup(
        id="rollup-epoch-1",
        theme_id=1,
        period_start=datetime.now(timezone.utc) - timedelta(days=14),
        period_end=datetime.now(timezone.utc) - timedelta(days=13),
        epoch_key="2026-10-03",
        total_events=12,
        critical_events_count=3,
        max_importance_score=9,
        avg_importance_score=6.5,
        executive_summary="Multiple GPS spoofing incidents and naval movements in Strait of Hormuz.",
        entity_counts_json={"Hormuz": 8, "IRGC Navy": 6},
        location_counts_json={"Strait of Hormuz": 7},
        source_stats_json={"Reuters": 5, "UKMTO": 7},
        notable_events_json=[{"headline": "Vessel harassed near Hormuz", "score": 9}],
        embedding_vector=[0.1] * 32,
        is_compacted=True,
    )
    session.add(rollup)
    await session.commit()

    context = await get_historical_context_and_baselines(
        theme_id=1,
        candidate_text="Naval patrol reported near Hormuz",
        candidate_entities=["Hormuz"],
        session=session,
    )

    assert "entity_baselines" in context
    assert context["entity_baselines"].get("Hormuz") == 8
    assert "historical_precedents" in context
    assert len(context["historical_precedents"]) >= 1
    assert context["historical_precedents"][0]["epoch_key"] == "2026-10-03"


@pytest.mark.asyncio
async def test_dynamic_source_credibility_calculation(async_test_session):
    """Verify dynamic source credibility scoring with recency decay."""
    session = async_test_session

    now = datetime.now(timezone.utc)
    ev_fresh = Event(
        id="ev-fresh",
        theme_id=1,
        source_id=1,
        source_name="Trusted Wire",
        source_type="rss",
        external_id="tw-1",
        title="Confirmed Port Activity",
        summary="Naval operations active.",
        raw_text="Confirmed naval operations.",
        importance_score=8,
        is_analyzed=True,
        published_at=now - timedelta(hours=2),
        metadata_json={
            "scoring_breakdown": {
                "corroboration_count": 3,
                "corroboration_status": "Multi-Source Corroborated",
            }
        },
    )
    session.add(ev_fresh)
    await session.commit()

    leaderboard = await get_dynamic_source_credibility(theme_id=1, session=session)
    assert len(leaderboard) >= 1
    wire = leaderboard[0]
    assert wire["source_name"] == "Trusted Wire"
    assert wire["total_reports"] == 1
    assert wire["corroboration_rate"] == 1.0
    assert wire["credibility_score"] >= 8.0
    assert "Tier 1" in wire["tier"]
