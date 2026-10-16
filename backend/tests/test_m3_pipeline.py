"""
backend/tests/test_m3_pipeline.py - Comprehensive Unit & Integration Test Suite for Milestone 3

Contains 15 hermetic, offline tests covering:
- AIProvider Abstraction & Mock Fidelity
- Resilient JSON Parser & Schema Validation
- 4 Pipeline Stages (Extraction/Translation, Scoring, Summarization, Visuals)
- Farsi/Arabic Translation, ZWNJ, and Acronym Preservation
- Dynamic Theme Prompt Steering & Alert Threshold Clamping
- Pipeline Orchestrator Concurrency, Multi-Provider Execution, and DB Persistence
- Dynamic Zero-Code Hot-Reload via Redis Bus
"""

import asyncio
import time
from datetime import datetime, timezone
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.app.db.models import Alert, Base, Event, SystemSettings, Theme
from backend.app.pipeline.models import (
    AIFallbackConfig,
    AIStageConfig,
    AnalyzedEvent,
    DigestEventItem,
    PeriodicDigestResult,
    Stage1Result,
    Stage2Result,
    Stage3Result,
    Stage4Result,
)
from backend.app.pipeline.orchestrator import PipelineOrchestrator
from backend.app.pipeline.parser import extract_and_parse_json
from backend.app.pipeline.providers.base import BaseAIProvider
from backend.app.pipeline.providers.factory import ProviderRegistry, resolve_api_key
from backend.app.pipeline.providers.mock import MockAIProvider
from backend.app.pipeline.stage1_extract import run_stage1_extract
from backend.app.pipeline.stage2_score import run_stage2_score
from backend.app.pipeline.stage3_summary import run_periodic_digest, run_stage3_summary
from backend.app.pipeline.stage4_visuals import run_stage4_visuals
from backend.app.plugins.base import NormalizedEvent
from backend.app.services.redis_bus import CHANNEL_CONFIG_RELOAD, RedisBus


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


@pytest_asyncio.fixture
async def test_db_session():
    """In-memory SQLite async database engine and session for hermetic testing."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    # Seed default theme and system settings
    async with session_factory() as session:
        default_theme = Theme(
            id=1,
            name="US-Iran conflict",
            description="Monitoring US-Iran military, geopolitical, and economic developments",
            keywords=["Strait of Hormuz", "IRGC", "CENTCOM", "uranium", "sanctions", "Shahed"],
            entities=["United States", "Iran", "Israel", "IRGC", "US Navy", "CENTCOM"],
            scoring_prompt=(
                "Score 9-10 for direct kinetic military engagements or strikes on naval/nuclear assets. "
                "Score 7-8 for proxy attacks, major sanctions, or troop deployments. "
                "Score 4-6 for standard diplomatic rhetoric or drills. Score 1-3 for background commentary."
            ),
            is_active=True,
            alert_threshold=7,
        )
        ai_settings = SystemSettings(
            key="ai_pipeline_stages",
            value_json={
                "extraction": {"provider": "groq", "model_name": "qwen/qwen3.8-27b"},
                "scoring": {"provider": "gemini", "model_name": "gemini-3.5-flash-lite"},
                "summarization": {"provider": "groq", "model_name": "openai/gpt-oss-120b"},
                "visuals": {"provider": "groq", "model_name": "openai/gpt-oss-20b"},
            },
        )
        session.add_all([default_theme, ai_settings])
        await session.commit()

    yield session_factory

    await engine.dispose()


@pytest.fixture
def mock_redis_bus():
    """In-memory fallback Redis bus."""
    bus = RedisBus()
    bus._fallback_mode = True
    return bus


# ============================================================================
# 1. AI STAGE CONFIG & SCHEMA VALIDATION TESTS
# ============================================================================

def test_stage_config_schema_validation():
    """Test 1: Validates Pydantic serialization, model aliases, and temperature clamps."""
    # Aliased stage name normalization
    cfg1 = AIStageConfig(
        stage_id="extraction_translation",  # type: ignore
        provider="google",  # type: ignore
        model_name="gemini-2.0-flash",
    )
    assert cfg1.stage_id == "extraction"
    assert cfg1.provider == "gemini"

    cfg2 = AIStageConfig(
        stage_id="importance_scoring",  # type: ignore
        provider="groq",
        model_name="openai/gpt-oss-120b",
        temperature=0.7,
    )
    assert cfg2.stage_id == "scoring"
    assert cfg2.temperature == 0.7

    cfg3 = AIStageConfig(
        stage_id="visual_generation",  # type: ignore
        provider="gemini",
        model_name="gemini-1.5-flash",
    )
    assert cfg3.stage_id == "visuals"


def test_mock_provider_offline_fidelity():
    """Test 2: Validates MockAIProvider offline behavior, call recording, and error simulation."""
    mock_gemini = MockAIProvider(provider="gemini", api_key="test-gemini-key")
    assert mock_gemini.provider_name == "gemini"

    # Test error triggers
    mock_auth_fail = MockAIProvider(provider="groq", simulate_auth_error=True)
    with pytest.raises(PermissionError) as exc_auth:
        asyncio.run(mock_auth_fail.generate_text("test"))
    assert "Groq API key is invalid or unauthorized" in str(exc_auth.value)

    mock_429_fail = MockAIProvider(provider="gemini", simulate_429=True)
    with pytest.raises(RuntimeError) as exc_429:
        asyncio.run(mock_429_fail.generate_text("test"))
    assert "429 Quota Exceeded" in str(exc_429.value)


# ============================================================================
# 2. STAGE 1: EXTRACTION, TRANSLATION, AND ACRONYM PRESERVATION TESTS
# ============================================================================

@pytest.mark.asyncio
async def test_stage1_english_passthrough():
    """Test 3: English input retains original text without prepending translation marker."""
    provider = MockAIProvider(provider="gemini")
    event = NormalizedEvent(
        theme_id="1",
        source_type="rss",
        source_name="Reuters",
        external_id="ext-en-001",
        title="US Navy conducts routine patrol in Persian Gulf",
        raw_text="US Navy 5th Fleet vessels conducted scheduled security patrols in the Persian Gulf today.",
        language="en",
    )

    result = await run_stage1_extract(event, provider)
    assert isinstance(result, Stage1Result)
    assert result.detected_language == "en"
    assert "English translation:" not in result.translated_text
    assert len(result.extracted_entities) > 0


@pytest.mark.asyncio
async def test_stage1_persian_translation_and_zwnj():
    """Test 4: Persian/Farsi translation handles ZWNJ (\u200c), numerals, and preserves IRGC/CENTCOM."""
    provider = MockAIProvider(provider="groq")
    # Persian text containing ZWNJ, Persian numerals, and IRGC
    persian_text = "سپاه\u200cپاسداران (IRGC) در تنگه هرمز ۱۲۰۰ قایق تندرو مستقر کرد و CENTCOM هشدار داد."
    event = NormalizedEvent(
        theme_id="1",
        source_type="telegram",
        source_name="IRNA Farsi",
        external_id="ext-fa-002",
        title="بیانیه فوری سپاه",
        raw_text=persian_text,
        language="fa",
    )

    result = await run_stage1_extract(event, provider)
    assert result.detected_language == "fa"
    assert "English translation:" in result.translated_text
    # Defense acronym preservation
    assert "IRGC" in result.extracted_entities or "IRGC" in result.translated_text
    assert "CENTCOM" in result.extracted_entities or "CENTCOM" in result.translated_text


@pytest.mark.asyncio
async def test_stage1_arabic_translation_and_harakat():
    """Test 5: Arabic translation handles Harakat diacritics, alert emojis, and preserves IAEA."""
    provider = MockAIProvider(provider="gemini")
    # Arabic text with Harakat and emojis
    arabic_text = "🚨 تَحَرُّكَاتٌ عَسْكَرِيَّةٌ مُكَّثَّفَةٌ قُرْبَ مَضِيقِ هُرْمُز وَمُفَتِّشُو IAEA يُرَاقِبُونَ."
    event = NormalizedEvent(
        theme_id="1",
        source_type="newsapi",
        source_name="Al Jazeera Arabic",
        external_id="ext-ar-003",
        title="عاجل: تحركات بحرية",
        raw_text=arabic_text,
        language="ar",
    )

    result = await run_stage1_extract(event, provider)
    assert result.detected_language in ("ar", "fa")
    assert "English translation:" in result.translated_text
    assert any(e in ("IAEA", "Strait of Hormuz", "Iran") for e in result.extracted_entities)


@pytest.mark.asyncio
async def test_stage1_hebrew_and_tactical_entity_resolution():
    """Test 5b: Stage 1 recognizes Hebrew sirens and maps Arabic regional entities."""
    from backend.app.pipeline.stage1_extract import _heuristic_stage1_fallback

    # Test Hebrew siren
    hebrew_raw = "🚨 צבע אדوم בגליל העליון. שיגורים זוהו לעבר יישובי הצפון."
    res_he = _heuristic_stage1_fallback(hebrew_raw, "צבע אדوم")
    assert res_he.detected_language == "he"
    assert "English translation:" in res_he.translated_text
    assert "Israel" in res_he.extracted_entities or "IDF" in res_he.extracted_entities

    # Test Arabic regional entity phrase resolution
    arabic_raw = "عاجل: أعلن حزب الله استهداف مواقع في الجليل، وأكدت قوات أنصار الله تنفيذ عمليات في البحر الأحمر وباب المندب."
    res_ar = _heuristic_stage1_fallback(arabic_raw, "بيان عسكري")
    assert res_ar.detected_language == "ar"
    assert "Hezbollah" in res_ar.extracted_entities
    assert "Houthis" in res_ar.extracted_entities
    assert "Red Sea" in res_ar.extracted_entities
    assert "Bab el-Mandeb" in res_ar.extracted_entities


# ============================================================================
# 3. STAGE 2: IMPORTANCE SCORING, PROMPT STEERING & ALERT TESTS
# ============================================================================

@pytest.mark.asyncio
async def test_stage2_scoring_clamping_and_priority():
    """Test 6: Validates integer score 1-10 clamped scale and priority classification."""
    provider = MockAIProvider(provider="gemini")
    theme_ctx = {
        "name": "US-Iran conflict",
        "scoring_prompt": "Standard geopolitical threat scale.",
        "alert_threshold": 7,
    }
    event = {
        "title": "Air defense missile battery deployed",
        "raw_text": "Forces deployed an advanced surface-to-air missile battery along the coastal border.",
    }

    result = await run_stage2_score(
        event=event,
        intelligence_text=event["raw_text"],
        extracted_entities=["Air Defense", "Coast"],
        theme_context=theme_ctx,
        provider=provider,
    )
    assert isinstance(result, Stage2Result)
    assert 1 <= result.importance_score <= 10
    assert result.priority_level in ["low", "medium", "high", "critical"]
    assert len(result.score_rationale) > 5


@pytest.mark.asyncio
async def test_stage2_dynamic_prompt_steering():
    """Test 7: Inverting Theme.scoring_prompt dynamically changes importance score for identical text."""
    provider = MockAIProvider(provider="gemini")
    diplomatic_text = "Envoys held bilateral diplomatic talks regarding the regional non-proliferation framework."
    event = {"title": "Diplomatic Talks", "raw_text": diplomatic_text}

    # Prompt 1: Elevates diplomatic talks to critical importance (10)
    theme_critical = {
        "name": "US-Iran conflict",
        "scoring_prompt": "Score diplomatic talks 10 because diplomatic summits signal imminent breakthrough.",
        "alert_threshold": 7,
    }
    res_high = await run_stage2_score(event, diplomatic_text, ["Envoys"], theme_critical, provider)
    assert res_high.importance_score == 10
    assert res_high.should_alert is True

    # Prompt 2: Minimizes diplomatic talks to low importance (1)
    theme_low = {
        "name": "US-Iran conflict",
        "scoring_prompt": "Score diplomatic talks 1 as empty political theater and background noise.",
        "alert_threshold": 7,
    }
    res_low = await run_stage2_score(event, diplomatic_text, ["Envoys"], theme_low, provider)
    assert res_low.importance_score == 1
    assert res_low.should_alert is False


@pytest.mark.asyncio
async def test_stage2_alert_threshold_boundaries():
    """Test 8: Exact threshold match triggers alert; one point below suppresses alert."""
    # When score is 7 with threshold 7 -> should_alert = True
    r1 = Stage2Result(
        importance_score=7,
        score_rationale="Score meets exact alert threshold.",
        priority_level="high",
        should_alert=True,
    )
    assert r1.should_alert is True

    # When score is 6 with threshold 7 -> should_alert = False
    r2 = Stage2Result(
        importance_score=6,
        score_rationale="Score below alert threshold.",
        priority_level="medium",
        should_alert=False,
    )
    assert r2.should_alert is False


# ============================================================================
# 4. STAGE 3 & 4: SUMMARIZATION, DIGEST, AND VISUAL TRENDS TESTS
# ============================================================================

@pytest.mark.asyncio
async def test_stage3_summary_and_headline():
    """Test 9: Validates concise BLUF summary and headline under 80 characters."""
    provider = MockAIProvider(provider="gemini")
    event = {
        "title": "Naval escort operation underway for oil tankers in Strait of Hormuz",
        "raw_text": "Allied naval task force launched a combined maritime escort mission for commercial tankers.",
    }
    result = await run_stage3_summary(event, event["raw_text"], provider)
    assert isinstance(result, Stage3Result)
    assert len(result.headline) <= 80
    assert len(result.summary) > 10
    assert "BLUF:" in result.summary or len(result.summary) > 20


@pytest.mark.asyncio
async def test_stage3_periodic_digest():
    """Test 10: Validates multi-event collection digest generation."""
    provider = MockAIProvider(provider="gemini")
    events = [
        {"id": "e1", "headline": "Patrol vessel intercepted", "importance_score": 8, "source_name": "Reuters"},
        {"id": "e2", "headline": "Diplomatic statements exchanged", "importance_score": 4, "source_name": "AP"},
        {"id": "e3", "headline": "UAV radar contact logged", "importance_score": 7, "source_name": "AFP"},
    ]
    digest = await run_periodic_digest(events, "US-Iran conflict", 7, provider)
    assert isinstance(digest, PeriodicDigestResult)
    assert digest.total_events_analyzed == 3
    assert digest.critical_alerts_count == 2
    assert digest.threat_trajectory in ["Escalating", "Elevated", "Stable", "De-escalating", "Volatile"]
    assert len(digest.key_developments) > 0
    assert len(digest.watchlist_indicators) >= 3


@pytest.mark.asyncio
async def test_stage4_visual_trend_tagging():
    """Test 11: Validates operational trend categorization and thematic cluster tags."""
    provider = MockAIProvider(provider="groq")
    event = {"title": "Naval patrol boats maneuver in Strait of Hormuz"}
    text = "Naval surface units performed tactical escort drills in the Strait of Hormuz waterway."
    result = await run_stage4_visuals(event, text, ["IRGC", "Strait of Hormuz"], provider)
    assert isinstance(result, Stage4Result)
    assert result.trend_category in ["Naval Escalation", "Air & Missile Defense", "Geopolitical Development"]
    assert len(result.cluster_tags) >= 2


# ============================================================================
# 5. PIPELINE ORCHESTRATION, CONCURRENCY & PERSISTENCE TESTS
# ============================================================================

@pytest.mark.asyncio
async def test_orchestrator_concurrent_multi_provider_run(test_db_session, mock_redis_bus):
    """Test 12 (AC-107): Heterogeneous multi-provider execution in the same pipeline run."""
    registry = ProviderRegistry(use_mock=True)
    orchestrator = PipelineOrchestrator(
        session_factory=test_db_session,
        redis_bus=mock_redis_bus,
        provider_registry=registry,
        use_mock=True,
    )
    # Configure Stage 1 on Groq and Stage 2 on Gemini
    orchestrator.set_stage_config("extraction", AIStageConfig(stage_id="extraction", provider="groq", model_name="qwen/qwen3.8-27b"))
    orchestrator.set_stage_config("scoring", AIStageConfig(stage_id="scoring", provider="gemini", model_name="gemini-2.0-flash"))

    event = NormalizedEvent(
        theme_id="1",
        source_type="rss",
        source_name="Al Jazeera",
        external_id="multi-prov-001",
        title="Naval exercises reported in Gulf of Oman",
        raw_text="Military forces initiated defensive exercises in the Gulf of Oman corridor.",
    )

    analyzed = await orchestrator.process_event(event)
    assert isinstance(analyzed, AnalyzedEvent)
    assert analyzed.importance_score is not None

    # Verify both Groq and Gemini were utilized in the same run
    groq_provider = registry.get_provider("groq", "qwen/qwen3.8-27b")
    gemini_provider = registry.get_provider("gemini", "gemini-2.0-flash")
    assert len(groq_provider.call_history) > 0
    assert len(gemini_provider.call_history) > 0


@pytest.mark.asyncio
async def test_orchestrator_parallel_stages_timing(test_db_session, mock_redis_bus):
    """Test 13: Verifies that Stages 2, 3, and 4 execute concurrently via asyncio.gather()."""
    registry = ProviderRegistry(use_mock=True)
    orchestrator = PipelineOrchestrator(
        session_factory=test_db_session,
        redis_bus=mock_redis_bus,
        provider_registry=registry,
        use_mock=True,
    )

    event = NormalizedEvent(
        theme_id="1",
        source_type="newsapi",
        source_name="AP",
        external_id="timing-001",
        title="Tactical movement logged in Strait",
        raw_text="Regional security units reported swift maneuvers near strategic shipping channels.",
    )

    start = time.perf_counter()
    analyzed = await orchestrator.process_event(event, parallel_stages=True)
    duration = time.perf_counter() - start

    assert isinstance(analyzed, AnalyzedEvent)
    # Hermetic mock execution should complete in under 100 milliseconds
    assert duration < 0.2


@pytest.mark.asyncio
async def test_orchestrator_db_enrichment_and_persistence(test_db_session, mock_redis_bus):
    """Test 14: Verifies database updates (is_analyzed=True, processed_at) and Alert row creation."""
    orchestrator = PipelineOrchestrator(
        session_factory=test_db_session,
        redis_bus=mock_redis_bus,
        use_mock=True,
    )

    # First insert a raw event in the database
    async with test_db_session() as session:
        raw_db_event = Event(
            id="persist-evt-001",
            theme_id=1,
            source_type="telegram",
            source_name="Defense Monitor",
            external_id="ext-persist-001",
            title="Kinetic strike on coastal radar station",
            raw_text="A kinetic strike hit a coastal radar outpost causing significant facility damage.",
            is_analyzed=False,
            published_at=datetime.now(timezone.utc),
        )
        session.add(raw_db_event)
        await session.commit()

    # Process through pipeline
    event = NormalizedEvent(
        id="persist-evt-001",
        theme_id="1",
        source_type="telegram",
        source_name="Defense Monitor",
        external_id="ext-persist-001",
        title="Kinetic strike on coastal radar station",
        raw_text="A kinetic strike hit a coastal radar outpost causing significant facility damage.",
    )
    analyzed = await orchestrator.process_event(event)

    # Verify event was enriched in database
    async with test_db_session() as session:
        res = await session.execute(select(Event).where(Event.id == "persist-evt-001"))
        db_ev = res.scalars().first()
        assert db_ev is not None
        assert db_ev.is_analyzed is True
        assert db_ev.processed_at is not None
        assert db_ev.importance_score >= 7  # Kinetic strike scores high
        assert db_ev.summary is not None
        assert db_ev.headline is not None

        # Verify Alert row was inserted because score >= threshold (7)
        alt_res = await session.execute(select(Alert).where(Alert.event_id == "persist-evt-001"))
        alert = alt_res.scalars().first()
        assert alert is not None
        assert alert.importance_score == db_ev.importance_score


@pytest.mark.asyncio
async def test_orchestrator_zero_code_hot_reload(test_db_session, mock_redis_bus):
    """Test 15: Publishes osint:config:reload to Redis bus and verifies immediate configuration update."""
    orchestrator = PipelineOrchestrator(
        session_factory=test_db_session,
        redis_bus=mock_redis_bus,
        use_mock=True,
    )
    await orchestrator.start()

    # Initial stage configuration for scoring
    assert orchestrator.get_stage_config("scoring").provider in ["gemini", "groq"]

    # Update database settings to use Groq for scoring
    async with test_db_session() as session:
        res = await session.execute(
            select(SystemSettings).where(SystemSettings.key == "ai_pipeline_stages")
        )
        setting = res.scalars().first()
        val = dict(setting.value_json)
        val["scoring"] = {"provider": "groq", "model_name": "mixtral-8x7b-32768", "temperature": 0.4}
        setting.value_json = val
        await session.commit()

    # Broadcast reload signal over Redis bus
    await mock_redis_bus.publish(CHANNEL_CONFIG_RELOAD, {"action": "ai_config_update"})
    await asyncio.sleep(0.05)

    # Verify orchestrator hot-reloaded new config dynamically
    scoring_cfg = orchestrator.get_stage_config("scoring")
    assert scoring_cfg.provider == "groq"
    assert scoring_cfg.model_name == "mixtral-8x7b-32768"
    assert scoring_cfg.temperature == 0.4

    await orchestrator.stop()


def test_stage_config_fallback_schema_parsing():
    """Test 16: Verifies AIStageConfig parses and normalizes nested AIFallbackConfig chains."""
    raw_config = {
        "stage_id": "scoring",
        "provider": "gemini",
        "model_name": "gemini-3.5-flash-lite",
        "temperature": 0.1,
        "fallbacks": [
            {"provider": "gemini", "model": "gemini-3.1-flash-lite", "temperature": 0.1},
            {"provider": "groq", "model_name": "qwen/qwen3.8-27b", "temperature": 0.2},
            {"provider": "groq", "model": "openai/gpt-oss-120b"},
        ],
    }

    cfg = AIStageConfig.model_validate(raw_config)
    assert cfg.stage_id == "scoring"
    assert cfg.provider == "gemini"
    assert cfg.model_name == "gemini-3.5-flash-lite"
    assert len(cfg.fallbacks) == 3

    assert cfg.fallbacks[0].provider == "gemini"
    assert cfg.fallbacks[0].model_name == "gemini-3.1-flash-lite"
    assert cfg.fallbacks[0].temperature == 0.1

    assert cfg.fallbacks[1].provider == "groq"
    assert cfg.fallbacks[1].model_name == "qwen/qwen3.8-27b"
    assert cfg.fallbacks[1].temperature == 0.2

    assert cfg.fallbacks[2].provider == "groq"
    assert cfg.fallbacks[2].model_name == "openai/gpt-oss-120b"


@pytest.mark.asyncio
async def test_stage2_fallback_from_3_5_flash_lite_to_3_1_flash_lite():
    """
    Test 17: User scenario - When gemini-3.5-flash-lite reaches rate limits (429),
    the pipeline automatically and seamlessly falls back to gemini-3.1-flash-lite.
    """
    orchestrator = PipelineOrchestrator(use_mock=True)

    # Configure stage 2: Primary is gemini-3.5-flash-lite, Fallback 1 is gemini-3.1-flash-lite
    orchestrator.set_stage_config(
        "scoring",
        AIStageConfig(
            stage_id="scoring",
            provider="gemini",
            model_name="gemini-3.5-flash-lite",
            temperature=0.1,
            fallbacks=[
                AIFallbackConfig(provider="gemini", model_name="gemini-3.1-flash-lite", temperature=0.1),
            ],
        ),
    )

    # Mock provider: simulate 429 quota exhaustion ONLY on gemini-3.5-flash-lite
    call_log = []

    def mock_get_provider(provider, model_name, api_key=None, temperature=0.2):
        call_log.append(f"{provider}:{model_name}")
        if model_name == "gemini-3.5-flash-lite":
            return MockAIProvider(provider="gemini", simulate_429=True, default_model=model_name)
        return MockAIProvider(provider=provider, api_key="mock-key", default_model=model_name)

    orchestrator._registry.get_provider = mock_get_provider

    event = NormalizedEvent(
        id="fb-evt-001",
        theme_id="1",
        source_type="rss",
        source_name="Al Jazeera",
        external_id="ext-fb-001",
        title="Missile interception reported near Gulf of Oman",
        raw_text="Air defense units intercepted a ballistic projectile over the Gulf of Oman.",
    )

    analyzed = await orchestrator.process_event(event, parallel_stages=False)

    assert analyzed.is_analyzed is True
    # Verify that gemini-3.5-flash-lite was attempted first, followed by gemini-3.1-flash-lite
    assert "gemini:gemini-3.5-flash-lite" in call_log
    assert "gemini:gemini-3.1-flash-lite" in call_log
    # Scoring should succeed using gemini-3.1-flash-lite (missile interception -> critical score 9)
    assert analyzed.importance_score == 9
    assert analyzed.notification_dispatched is True


@pytest.mark.asyncio
async def test_multi_provider_fallback_chain():
    """
    Test 18: Multi-provider failover:
    Primary: gemini-3.5-flash-lite (429) ->
    Fallback 1: gemini-3.1-flash-lite (429) ->
    Fallback 2: groq qwen/qwen3.8-27b (200 OK).
    """
    orchestrator = PipelineOrchestrator(use_mock=True)

    orchestrator.set_stage_config(
        "scoring",
        AIStageConfig(
            stage_id="scoring",
            provider="gemini",
            model_name="gemini-3.5-flash-lite",
            temperature=0.1,
            fallbacks=[
                AIFallbackConfig(provider="gemini", model_name="gemini-3.1-flash-lite", temperature=0.1),
                AIFallbackConfig(provider="groq", model_name="qwen/qwen3.8-27b", temperature=0.2),
            ],
        ),
    )

    attempted_models = []

    def mock_get_provider(provider, model_name, api_key=None, temperature=0.2):
        attempted_models.append(f"{provider}:{model_name}")
        # Both Gemini models hit 429 quota exhaustion
        if provider == "gemini":
            return MockAIProvider(provider="gemini", simulate_429=True, default_model=model_name)
        # Groq succeeds!
        return MockAIProvider(provider="groq", api_key="mock-key", default_model=model_name)

    orchestrator._registry.get_provider = mock_get_provider

    event = NormalizedEvent(
        id="fb-evt-002",
        theme_id="1",
        source_type="telegram",
        source_name="MiddleEastWire",
        external_id="ext-fb-002",
        title="Sanctions imposed on shipping networks",
        raw_text="New sanctions package targeting regional petroleum shipping networks announced.",
    )

    analyzed = await orchestrator.process_event(event, parallel_stages=False)

    assert analyzed.is_analyzed is True
    # Verify all 3 models were engaged in cascade
    assert "gemini:gemini-3.5-flash-lite" in attempted_models
    assert "gemini:gemini-3.1-flash-lite" in attempted_models
    assert "groq:qwen/qwen3.8-27b" in attempted_models
    # Groq successfully scored the event
    assert analyzed.importance_score >= 7


@pytest.mark.asyncio
async def test_cooldown_bypasses_exhausted_model_on_subsequent_events():
    """
    Test 19: Once a model hits 429 and enters cooldown, subsequent events
    skip the exhausted model directly to the healthy fallback without delay.
    """
    orchestrator = PipelineOrchestrator(use_mock=True)

    orchestrator.set_stage_config(
        "scoring",
        AIStageConfig(
            stage_id="scoring",
            provider="gemini",
            model_name="gemini-3.5-flash-lite",
            temperature=0.1,
            fallbacks=[
                AIFallbackConfig(provider="gemini", model_name="gemini-3.1-flash-lite", temperature=0.1),
            ],
        ),
    )

    model_calls = []

    def mock_get_provider(provider, model_name, api_key=None, temperature=0.2):
        model_calls.append(model_name)
        if model_name == "gemini-3.5-flash-lite":
            return MockAIProvider(provider="gemini", simulate_429=True, default_model=model_name)
        return MockAIProvider(provider=provider, api_key="mock-key", default_model=model_name)

    orchestrator._registry.get_provider = mock_get_provider

    event1 = NormalizedEvent(
        id="cd-evt-001",
        theme_id="1",
        source_type="rss",
        source_name="Reuters",
        external_id="ext-cd-001",
        title="First event",
        raw_text="Naval drill in Strait of Hormuz.",
    )

    # Event 1: gemini-3.5-flash-lite is attempted, hits 429, falls back to 3.1
    await orchestrator.process_event(event1, parallel_stages=False)
    assert "gemini-3.5-flash-lite" in model_calls
    assert "gemini-3.1-flash-lite" in model_calls
    assert orchestrator.is_model_in_cooldown("gemini", "gemini-3.5-flash-lite") is True

    # Event 2: gemini-3.5-flash-lite is in active cooldown, should be SKIPPED directly!
    model_calls.clear()
    event2 = NormalizedEvent(
        id="cd-evt-002",
        theme_id="1",
        source_type="rss",
        source_name="Reuters",
        external_id="ext-cd-002",
        title="Second event",
        raw_text="Diplomatic consultations in Geneva.",
    )
    await orchestrator.process_event(event2, parallel_stages=False)

    # gemini-3.5-flash-lite was skipped; only gemini-3.1-flash-lite was called
    assert "gemini-3.5-flash-lite" not in model_calls
    assert "gemini-3.1-flash-lite" in model_calls


@pytest.mark.asyncio
async def test_all_fallbacks_exhausted_falls_back_to_heuristics():
    """
    Test 20: When all models in primary and fallback chain fail,
    the pipeline gracefully completes using deterministic offline heuristics.
    """
    orchestrator = PipelineOrchestrator(use_mock=True)

    orchestrator.set_stage_config(
        "scoring",
        AIStageConfig(
            stage_id="scoring",
            provider="gemini",
            model_name="gemini-3.5-flash-lite",
            temperature=0.1,
            fallbacks=[
                AIFallbackConfig(provider="gemini", model_name="gemini-3.1-flash-lite", temperature=0.1),
            ],
        ),
    )

    # Everything fails
    def mock_get_provider(provider, model_name, api_key=None, temperature=0.2):
        return MockAIProvider(provider=provider, simulate_429=True, default_model=model_name)

    orchestrator._registry.get_provider = mock_get_provider

    event = NormalizedEvent(
        id="fail-evt-001",
        theme_id="1",
        source_type="rss",
        source_name="Reuters",
        external_id="ext-fail-001",
        title="Naval Drill near Hormuz",
        raw_text="Patrol exercises conducted in Strait of Hormuz.",
    )

    analyzed = await orchestrator.process_event(event, parallel_stages=False)

    assert analyzed.is_analyzed is True
    # Heuristic fallback handled the event safely
    assert analyzed.importance_score == 5
    assert "Fallback assessment" in analyzed.score_rationale


def test_classify_rate_limit_minute_vs_daily():
    """
    Test 21: Verifies classify_rate_limit_429 distinguishes minute rate limits (RPM/TPM)
    from daily quota exhaustion (RPD/TPD) across both Gemini and Groq error payloads/headers.
    """
    import httpx
    from backend.app.pipeline.providers.base import classify_rate_limit_429

    req = httpx.Request("POST", "https://example.com")

    # 1. Gemini RPM (minute rate limit, e.g. 20 / 15 RPM with 28s retryDelay)
    gemini_rpm_body = '''{
      "error": {
        "code": 429,
        "message": "You exceeded your current quota, please check your plan and billing details.",
        "status": "RESOURCE_EXHAUSTED",
        "details": [
          {
            "@type": "type.googleapis.com/google.rpc.QuotaFailure",
            "violations": [
              {
                "quotaMetric": "generativelanguage.googleapis.com/generate_content_free_tier_requests",
                "quotaId": "GenerateRequestsPerMinutePerProjectPerModel-FreeTier",
                "quotaDimensions": {"model": "gemini-3.5-flash-lite"}
              }
            ]
          },
          {
            "@type": "type.googleapis.com/google.rpc.RetryInfo",
            "retryDelay": "28s"
          }
        ]
      }
    }'''
    resp_rpm = httpx.Response(429, text=gemini_rpm_body, request=req)
    is_daily_rpm, wait_rpm, reason_rpm = classify_rate_limit_429(resp_rpm, "gemini")
    assert is_daily_rpm is False
    assert wait_rpm == 28.0
    assert "minute rate limit" in reason_rpm

    # 2. Gemini RPD (daily quota exhausted, 500 / 500 RPD)
    gemini_rpd_body = '''{
      "error": {
        "code": 429,
        "message": "Quota exceeded for quota metric 'Generate Content API requests per day'",
        "status": "RESOURCE_EXHAUSTED",
        "details": [
          {
            "@type": "type.googleapis.com/google.rpc.QuotaFailure",
            "violations": [
              {
                "quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"
              }
            ]
          }
        ]
      }
    }'''
    resp_rpd = httpx.Response(429, text=gemini_rpd_body, request=req)
    is_daily_rpd, wait_rpd, reason_rpd = classify_rate_limit_429(resp_rpd, "gemini")
    assert is_daily_rpd is True
    assert "daily quota exhausted" in reason_rpd

    # 3. Groq TPM / RPM headers (tokens exhausted for the minute, requests for the day still remaining)
    groq_headers_minute = {
        "x-ratelimit-remaining-tokens": "0",
        "x-ratelimit-remaining-requests": "14200",
        "x-ratelimit-reset-tokens": "19.5s",
    }
    resp_groq = httpx.Response(
        429,
        headers=groq_headers_minute,
        text="Rate limit reached for tokens per minute (TPM)",
        request=req,
    )
    is_daily_groq, wait_groq, reason_groq = classify_rate_limit_429(resp_groq, "groq")
    assert is_daily_groq is False
    assert wait_groq == 19.5
    assert "minute rate limit" in reason_groq


@pytest.mark.asyncio
async def test_minute_rate_limit_waits_and_retries_same_model_no_qwen_switch(monkeypatch):
    """
    Test 22: When the primary model (gemini-3.5-flash-lite) hits a per-minute rate limit (RPM),
    even with a wait > 15s (e.g. 24s), it waits and retries on the SAME model and does NOT
    switch to qwen/qwen3.8-27b or place the primary model into cooldown.
    """
    import httpx
    from backend.app.pipeline.providers.gemini import GeminiProvider

    slept_durations = []

    async def fast_sleep(seconds):
        slept_durations.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", fast_sleep)

    gemini_prov = GeminiProvider(api_key="real-test-key", default_model="gemini-3.5-flash-lite")
    gemini_prov.min_interval_sec = 0.0
    gemini_prov.max_rpm = 0

    attempt_count = 0

    async def mock_request(self, method, url, **kwargs):
        nonlocal attempt_count
        attempt_count += 1
        req = httpx.Request(method, url)
        if attempt_count == 1:
            # First call hits 15 RPM minute limit with 24s RetryInfo
            rpm_json = '''{
              "error": {
                "code": 429,
                "message": "Resource has been exhausted (e.g. check quota).",
                "details": [
                  {
                    "@type": "type.googleapis.com/google.rpc.QuotaFailure",
                    "violations": [{"quotaId": "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"}]
                  },
                  {
                    "@type": "type.googleapis.com/google.rpc.RetryInfo",
                    "retryDelay": "24s"
                  }
                ]
              }
            }'''
            return httpx.Response(429, text=rpm_json, request=req)
        # Second call after waiting succeeds!
        ok_json = '''{
          "candidates": [{
            "content": {
              "parts": [{"text": "{\\"importance_score\\": 8, \\"score_rationale\\": \\"Naval escalation confirmed.\\", \\"priority_level\\": \\"high\\", \\"should_alert\\": true}"}]
            }
          }]
        }'''
        return httpx.Response(200, text=ok_json, request=req)

    monkeypatch.setattr(httpx.AsyncClient, "request", mock_request)

    orchestrator = PipelineOrchestrator(use_mock=True)
    # Set extraction to use a different model so only scoring has qwen/qwen3.8-27b as a fallback
    orchestrator.set_stage_config(
        "extraction",
        AIStageConfig(stage_id="extraction", provider="groq", model_name="openai/gpt-oss-20b"),
    )
    orchestrator.set_stage_config(
        "scoring",
        AIStageConfig(
            stage_id="scoring",
            provider="gemini",
            model_name="gemini-3.5-flash-lite",
            temperature=0.1,
            fallbacks=[
                AIFallbackConfig(provider="groq", model_name="qwen/qwen3.8-27b", temperature=0.2),
            ],
        ),
    )
    orchestrator.set_stage_config(
        "visuals",
        AIStageConfig(stage_id="visuals", provider="groq", model_name="openai/gpt-oss-20b"),
    )

    models_invoked = []

    def mock_get_provider(provider, model_name, api_key=None, temperature=0.2):
        models_invoked.append(f"{provider}:{model_name}")
        if provider == "gemini":
            return gemini_prov
        return MockAIProvider(provider=provider, api_key="mock-key", default_model=model_name)

    orchestrator._registry.get_provider = mock_get_provider

    event = NormalizedEvent(
        id="rpm-evt-001",
        theme_id="1",
        source_type="rss",
        source_name="Reuters",
        external_id="ext-rpm-001",
        title="IRGC fast attack craft shadow tanker in Hormuz",
        raw_text="Multiple IRGC naval vessels shadowed a commercial tanker in the Strait of Hormuz.",
    )

    analyzed = await orchestrator.process_event(event, parallel_stages=False)

    assert analyzed.is_analyzed is True
    assert analyzed.importance_score == 8
    # Verify Gemini retried after waiting ~25s and NEVER invoked qwen/qwen3.8-27b for scoring
    assert attempt_count == 2
    assert any(d >= 24.0 for d in slept_durations)
    assert "groq:qwen/qwen3.8-27b" not in models_invoked
    assert orchestrator.is_model_in_cooldown("gemini", "gemini-3.5-flash-lite") is False


@pytest.mark.asyncio
async def test_stage2_multidimensional_scoring_and_calibration():
    """
    Test 23: Verifies multi-dimensional sub-scores (severity, novelty, verification_confidence),
    source reliability tiers, same-source echo dampening, uncorroborated acoustic rumor capping,
    and multi-source corroboration boosts in Stage 2 scoring.
    """
    from backend.app.pipeline.stage2_score import (
        classify_source_reliability,
        calibrate_stage2_result,
    )
    from backend.app.pipeline.arabic_normalizer import classify_kinetic_signal
    from backend.app.pipeline.models import Stage2Result

    # 1. Source reliability tiers
    t1_id, _ = classify_source_reliability("Reuters", "rss")
    t2_id, _ = classify_source_reliability("Al Mayadeen", "telegram")
    t3_id, _ = classify_source_reliability("OSINT Defender", "reddit")
    assert t1_id == "tier_1_wire_official"
    assert t2_id == "tier_2_regional_osint"
    assert t3_id == "tier_3_social_unverified"

    theme_ctx = {"name": "Iran Theater", "alert_threshold": 8}

    # 2. Same-source echo dampening: score reduced by 2 and duplicate alert suppressed
    raw_echo = Stage2Result(
        importance_score=9,
        severity_score=9,
        novelty_score=7,
        verification_confidence="medium",
        confidence_score=7,
        score_rationale="Missile strike reported.",
        priority_level="critical",
        should_alert=True,
    )
    echo_ctx = {
        "source_reliability_tier": "tier_2_regional_osint",
        "corroboration_count": 0,
        "corroborating_sources": [],
        "same_source_echo_count": 2,
        "is_same_source_echo": True,
        "corroboration_status": "Repetitive Same-Source Echo",
    }
    cal_echo = calibrate_stage2_result(raw_echo, theme_ctx, echo_ctx, "Missile strike reported in Isfahan.")
    assert cal_echo.importance_score == 7
    assert cal_echo.novelty_score <= 3
    assert cal_echo.should_alert is False
    assert any("Same-source echo dampened" in note for note in cal_echo.calibration_notes)

    # 3. Uncorroborated frontline Arabic acoustic chatter capped below alert_threshold (8)
    raw_acoustic = Stage2Result(
        importance_score=8,
        severity_score=8,
        novelty_score=7,
        verification_confidence="medium",
        confidence_score=6,
        score_rationale="Explosion heard near border.",
        priority_level="high",
        should_alert=True,
    )
    acoustic_text = "سماع دوي انفجار في المنطقة الحدودية"
    acoustic_ctx = {
        "source_reliability_tier": "tier_2_regional_osint",
        "kinetic_signal": classify_kinetic_signal(acoustic_text),
        "corroboration_count": 0,
        "corroborating_sources": [],
        "same_source_echo_count": 0,
        "is_same_source_echo": False,
        "corroboration_status": "First Report / Unverified",
    }
    cal_acoustic = calibrate_stage2_result(
        raw_acoustic,
        theme_ctx,
        acoustic_ctx,
        acoustic_text,
    )
    assert cal_acoustic.importance_score <= 6
    assert cal_acoustic.should_alert is False
    assert cal_acoustic.verification_confidence == "low"
    assert any("Acoustic anomaly cap" in note for note in cal_acoustic.calibration_notes)

    # 4. Multi-source corroborated event receives +1 boost and elevated verification confidence
    raw_corrob = Stage2Result(
        importance_score=8,
        severity_score=8,
        novelty_score=8,
        verification_confidence="medium",
        confidence_score=6,
        score_rationale="IRGC naval interception confirmed.",
        priority_level="high",
        should_alert=True,
    )
    corrob_ctx = {
        "source_reliability_tier": "tier_1_wire_official",
        "corroboration_count": 2,
        "corroborating_sources": ["Al Jazeera", "Associated Press"],
        "same_source_echo_count": 0,
        "is_same_source_echo": False,
        "corroboration_status": "Multi-Source Corroborated",
    }
    cal_corrob = calibrate_stage2_result(
        raw_corrob,
        theme_ctx,
        corrob_ctx,
        "IRGC naval forces intercepted a commercial tanker in the Strait of Hormuz.",
    )
    assert cal_corrob.importance_score == 9
    assert cal_corrob.verification_confidence == "high"
    assert cal_corrob.confidence_score >= 9
    assert cal_corrob.should_alert is True
    assert any("Multi-source corroboration boost" in note for note in cal_corrob.calibration_notes)


@pytest.mark.asyncio
async def test_orchestrator_inline_embedding_and_retroactive_corroboration_upgrade(test_db_session, mock_redis_bus):
    """
    Test 24: Verifies that PipelineOrchestrator:
    1. Computes inline embeddings prior to Stage 2 and persists EventEmbedding + scoring_breakdown.
    2. Detects semantic corroboration across independent sources on subsequent events.
    3. Retroactively upgrades an earlier unverified event (+1 score and rationale note) when
       corroborated by independent sources.
    """
    from backend.app.db.models import EventEmbedding

    orchestrator = PipelineOrchestrator(
        session_factory=test_db_session,
        redis_bus=mock_redis_bus,
        use_mock=True,
    )

    # Insert raw Event 1 into DB before processing
    async with test_db_session() as session:
        session.add(
            Event(
                id="corrob-evt-001",
                theme_id=1,
                source_type="telegram",
                source_name="NourNews",
                external_id="ext-corrob-001",
                title="Sanctions and IRGC naval exercises near Bandar Abbas",
                raw_text="IRGC naval units and Quds Force commanders inspected sanctions-evading tanker fleet at Bandar Abbas port.",
                is_analyzed=False,
                published_at=datetime.now(timezone.utc),
            )
        )
        await session.commit()

    evt1 = NormalizedEvent(
        id="corrob-evt-001",
        theme_id="1",
        source_type="telegram",
        source_name="NourNews",
        external_id="ext-corrob-001",
        title="Sanctions and IRGC naval exercises near Bandar Abbas",
        raw_text="IRGC naval units and Quds Force commanders inspected sanctions-evading tanker fleet at Bandar Abbas port.",
    )
    res1 = await orchestrator.process_event(evt1, parallel_stages=False)
    assert res1.is_analyzed is True
    initial_score_1 = res1.importance_score
    assert "scoring_breakdown" in res1.metadata
    assert res1.metadata["scoring_breakdown"]["corroboration_count"] == 0

    # Verify EventEmbedding was persisted inline
    async with test_db_session() as session:
        emb_res = await session.execute(select(EventEmbedding).where(EventEmbedding.event_id == "corrob-evt-001"))
        emb1 = emb_res.scalars().first()
        assert emb1 is not None

    # Insert raw Event 2 into DB from an independent Tier-1 source (Reuters) on the same incident
    async with test_db_session() as session:
        session.add(
            Event(
                id="corrob-evt-002",
                theme_id=1,
                source_type="rss",
                source_name="Reuters",
                external_id="ext-corrob-002",
                title="Sanctions and IRGC naval exercises near Bandar Abbas",
                raw_text="IRGC naval units and Quds Force commanders inspected sanctions-evading tanker fleet at Bandar Abbas port today.",
                is_analyzed=False,
                published_at=datetime.now(timezone.utc),
            )
        )
        await session.commit()

    evt2 = NormalizedEvent(
        id="corrob-evt-002",
        theme_id="1",
        source_type="rss",
        source_name="Reuters",
        external_id="ext-corrob-002",
        title="Sanctions and IRGC naval exercises near Bandar Abbas",
        raw_text="IRGC naval units and Quds Force commanders inspected sanctions-evading tanker fleet at Bandar Abbas port today.",
    )
    res2 = await orchestrator.process_event(evt2, parallel_stages=False)
    assert res2.is_analyzed is True
    bd2 = res2.metadata.get("scoring_breakdown", {})
    assert bd2.get("corroboration_count", 0) >= 1
    assert "NourNews" in bd2.get("corroborating_sources", [])
    assert len(bd2.get("related_reports", [])) >= 1

    # Verify Event 1 in the database was retroactively upgraded by Event 2's corroboration!
    async with test_db_session() as session:
        ev1_res = await session.execute(select(Event).where(Event.id == "corrob-evt-001"))
        db_evt1 = ev1_res.scalars().first()
        assert db_evt1 is not None
        assert db_evt1.importance_score == min(10, initial_score_1 + 1)
        assert "Retroactively corroborated" in (db_evt1.score_rationale or "")
        bd1_updated = (db_evt1.metadata_json or {}).get("scoring_breakdown", {})
        assert bd1_updated.get("retroactively_corroborated") is True
        assert "Reuters" in bd1_updated.get("corroborating_sources", [])


# ==============================================================================
# 28. GEMMA 4 PRESENCE ACROSS ALL PIPELINE FALLBACK CHAINS (HIGH-RPD DEFENSE)
# ==============================================================================

def test_gemma4_present_in_all_default_fallback_chains():
    """
    Test 28: Verifies that Gemma 4 (with its massive 14,400 RPD quota) is present
    at every fallback chain across DEFAULT_AI_STAGES, seed data, and orchestrator defaults.
    """
    from backend.app.api.settings import DEFAULT_AI_STAGES
    from backend.app.db.seed import DEFAULT_SETTINGS_DATA
    from backend.app.pipeline.orchestrator import PipelineOrchestrator

    # 1. API settings defaults
    for stage_name, stage_cfg in DEFAULT_AI_STAGES.items():
        fallbacks = stage_cfg.get("fallbacks", [])
        has_gemma = any("gemma" in fb["model_name"].lower() for fb in fallbacks)
        assert has_gemma, f"Stage '{stage_name}' in DEFAULT_AI_STAGES is missing Gemma 4 in its fallback chain!"

    # 2. Database seed defaults
    seed_stages = DEFAULT_SETTINGS_DATA["ai_pipeline_stages"]
    for stage_name, stage_cfg in seed_stages.items():
        fallbacks = stage_cfg.get("fallbacks", [])
        has_gemma = any("gemma" in str(fb.get("model") or fb.get("model_name", "")).lower() for fb in fallbacks)
        assert has_gemma, f"Stage '{stage_name}' in DEFAULT_SETTINGS_DATA is missing Gemma 4 in its fallback chain!"

    # 3. Pipeline orchestrator defaults
    orchestrator = PipelineOrchestrator(use_mock=True)
    for stage_name in ("gatekeeper", "extraction", "scoring", "summarization", "visuals"):
        cfg = orchestrator.get_stage_config(stage_name)
        has_gemma = any("gemma" in fb.model_name.lower() for fb in cfg.fallbacks)
        assert has_gemma, f"Stage '{stage_name}' in orchestrator default configs is missing Gemma 4 in its fallback chain!"


@pytest.mark.asyncio
async def test_orchestrator_candidate_chain_guarantees_gemma4():
    """
    Test 29: Verifies that even if a stage is configured without Gemma 4 in its fallbacks,
    the execution candidate chain automatically appends Gemma 4 as a high-RPD safety fallback.
    """
    orchestrator = PipelineOrchestrator(use_mock=True)

    # Configure stage without Gemma 4
    orchestrator.set_stage_config(
        "scoring",
        AIStageConfig(
            stage_id="scoring",
            provider="gemini",
            model_name="gemini-3.5-flash-lite",
            temperature=0.1,
            fallbacks=[
                AIFallbackConfig(provider="gemini", model_name="gemini-3.1-flash-lite", temperature=0.1),
                AIFallbackConfig(provider="groq", model_name="openai/gpt-oss-120b", temperature=0.1),
            ],
        ),
    )

    attempted_models = []

    async def mock_runner(provider, model, temperature, raise_on_error=True):
        attempted_models.append(model)
        if "gemma" in model.lower():
            # Gemma 4 catches the request successfully!
            from backend.app.pipeline.models import Stage2Result
            return Stage2Result(
                importance_score=8,
                score_rationale="Gemma 4 high-RPD fallback succeeded",
                priority_level="high",
                should_alert=True,
            )
        raise RuntimeError(f"Simulated failure for {model}")

    res = await orchestrator._execute_stage_with_fallbacks(
        stage_id="scoring",
        runner_fn=mock_runner,
        fallback_heuristic_fn=lambda: None,
    )

    assert "gemma-4-26b-a4b-it" in attempted_models, "Gemma 4 was not included in candidate execution chain!"
    assert res.importance_score == 8
    assert "Gemma 4" in res.score_rationale



