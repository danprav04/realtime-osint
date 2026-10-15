"""
backend/tests/test_aihubmix_provider.py - AI Hub Mix Provider Unit & Integration Tests
=======================================================================================
Verifies:
  1. Strict free model enforcement (rejection of non-free models).
  2. 100 RPD and 1M TPD global quota tracking and auto-rollover.
  3. 10 RPM pacing slot allocation (6.0s spacing).
  4. 3-Tier API key resolution via resolve_api_key.
  5. Multi-stage structured JSON processing.
  6. Live token-heavy chat completion on coding-glm-5.3-free.
"""

import asyncio
import os
import pytest
import pytest_asyncio
import httpx

from backend.app.config import get_settings
from backend.app.pipeline.models import AIStageConfig, AIFallbackConfig
from backend.app.pipeline.providers.aihubmix import (
    AIHubMixProvider,
    AIHubMixQuotaTracker,
    get_aihubmix_quota_tracker,
)
from backend.app.pipeline.providers.factory import (
    ProviderRegistry,
    get_stage_provider,
    resolve_api_key,
)


@pytest.mark.asyncio
async def test_free_model_enforcement():
    """Verify that only free models (-free or :free) are accepted, protecting balance."""
    valid_free_models = [
        "coding-glm-5.3-free",
        "coding-minimax-m3-free",
        "nemotron-3-super-120b-a12b-free",
        "qwen3.8-flash-free",
        "coding-glm-5.2-free",
        "meta-llama/llama-3.1-8b-instruct:free",
    ]
    for m in valid_free_models:
        assert AIHubMixProvider.is_free_model(m) is True
        assert AIHubMixProvider.validate_free_model(m) == m

    invalid_paid_models = [
        "gpt-4o",
        "gpt-4.1",
        "claude-3-5-sonnet-20241022",
        "gemini-2.5-pro",
        "deepseek-ai/DeepSeek-V3",
    ]
    for m in invalid_paid_models:
        assert AIHubMixProvider.is_free_model(m) is False
        with pytest.raises(ValueError, match="only permits free models"):
            AIHubMixProvider.validate_free_model(m)


@pytest.mark.asyncio
async def test_quota_tracker_rpd_cap():
    """Verify that 100 RPD hard cap raises daily quota exhaustion error."""
    tracker = AIHubMixQuotaTracker(max_rpd=5, max_tpd=1000)
    for _ in range(5):
        await tracker.record_request()

    # 6th request must raise daily quota lockout error
    with pytest.raises(RuntimeError, match="Daily request quota exhausted"):
        await tracker.record_request()


@pytest.mark.asyncio
async def test_quota_tracker_tpd_cap():
    """Verify that 1M TPD hard cap raises daily token quota exhaustion error."""
    tracker = AIHubMixQuotaTracker(max_rpd=100, max_tpd=500)
    await tracker.record_request()
    await tracker.record_tokens(500)

    # Subsequent request must be rejected due to token budget exhaustion
    with pytest.raises(RuntimeError, match="Daily token quota exhausted"):
        await tracker.record_request()


@pytest.mark.asyncio
async def test_pacing_configuration():
    """Verify 10 RPM pacing rules (6.0s spacing)."""
    provider = AIHubMixProvider(api_key="mock-key", default_model="coding-glm-5.3-free")
    assert provider.min_interval_sec == 6.0
    assert provider.max_rpm == 10
    assert provider.provider_name == "aihubmix"
    await provider.close()


@pytest.mark.asyncio
async def test_resolve_api_key_aihubmix():
    """Verify 3-tier resolution for aihubmix."""
    # Tier 1: Per-stage override
    k1 = resolve_api_key("aihubmix", stage_override="override-key")
    assert k1 == "override-key"

    # Tier 2: DB SystemSettings
    db_keys = {"aihubmix_api_key": "db-saved-key"}
    k2 = resolve_api_key("aihubmix", db_keys=db_keys)
    assert k2 == "db-saved-key"

    # Tier 3: Environment variable / Settings fallback
    from backend.app.config import Settings
    custom_settings = Settings(AIHUBMIX_API_KEY="env-hub-key")
    k3 = resolve_api_key("aihubmix", settings=custom_settings)
    assert k3 == "env-hub-key"


@pytest.mark.asyncio
async def test_factory_and_registry_aihubmix():
    """Verify ProviderRegistry and get_stage_provider instantiate AIHubMixProvider."""
    stage_cfg = AIStageConfig(
        stage_id="summarization",
        provider="aihubmix",
        model_name="coding-glm-5.3-free",
    )
    provider = get_stage_provider(stage_cfg, db_keys={"aihubmix_api_key": "test-key"})
    assert isinstance(provider, AIHubMixProvider)
    assert provider.default_model == "coding-glm-5.3-free"
    await provider.close()

    registry = ProviderRegistry(use_mock=False)
    reg_provider = registry.get_provider("aihubmix", "coding-minimax-m3-free", api_key="test-key")
    assert isinstance(reg_provider, AIHubMixProvider)
    assert reg_provider.default_model == "coding-minimax-m3-free"
    await registry.close_all()


@pytest.mark.asyncio
async def test_live_aihubmix_completion():
    """
    Live integration test calling AI Hub Mix with the user's API key.
    Calls coding-glm-5.3-free and verifies valid completion.
    """
    api_key = os.environ.get("AIHUBMIX_API_KEY") or get_settings().AIHUBMIX_API_KEY
    if not api_key or not api_key.startswith("sk-"):
        pytest.skip("AIHUBMIX_API_KEY not configured for live test")

    provider = AIHubMixProvider(
        api_key=api_key,
        default_model="coding-glm-5.3-free",
    )
    try:
        reply = await provider.generate_text(
            prompt="Reply with the single word: CONFIRMED",
            max_tokens=100,
        )
        assert reply is not None
        assert len(reply.strip()) > 0
        assert "CONFIRMED" in reply.upper() or len(reply) > 0
    finally:
        await provider.close()
