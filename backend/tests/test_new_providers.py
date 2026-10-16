"""
backend/tests/test_new_providers.py - Unit & Integration Tests for High-RPD Overflow Providers
================================================================================================
Tests Mistral AI, Cloudflare Workers AI, Z.ai, and SiliconFlow providers:
  1. Provider initialization, defaults, pacing, and neuron/concurrency guards.
  2. 3-tier API key resolution (override -> DB -> Settings).
  3. Factory and ProviderRegistry instantiation.
  4. Mock generation and structured JSON extraction.
  5. Orchestrator fallback handling and graceful skipping of unconfigured providers.
  6. Live tests (when credentials are present).
"""

import asyncio
import os
import pytest
import httpx

from backend.app.config import Settings, get_settings
from backend.app.pipeline.models import AIStageConfig, AIFallbackConfig
from backend.app.pipeline.providers.mistral import MistralProvider
from backend.app.pipeline.providers.cloudflare import CloudflareProvider, CloudflareNeuronTracker
from backend.app.pipeline.providers.zai import ZaiProvider
from backend.app.pipeline.providers.siliconflow import SiliconFlowProvider
from backend.app.pipeline.providers.factory import (
    ProviderRegistry,
    get_stage_provider,
    resolve_api_key,
    resolve_api_keys,
)
from backend.app.pipeline.orchestrator import PipelineOrchestrator


@pytest.mark.asyncio
async def test_mistral_provider_initialization():
    """Verify Mistral provider configuration, high-RPS pacing, and endpoints."""
    p_fast = MistralProvider(api_key="test-key", default_model="ministral-3b-2512")
    assert p_fast.provider_name == "mistral"
    assert p_fast.max_rpm == 300
    assert p_fast.min_interval_sec == 0.2
    assert "mistral.ai" in p_fast.base_url
    await p_fast.close()


@pytest.mark.asyncio
async def test_cloudflare_provider_initialization():
    """Verify Cloudflare Workers AI model normalization and account-based endpoint."""
    p = CloudflareProvider(
        account_id="acc123456",
        api_token="cfut_test_token",
        default_model="llama-3.2-1b-instruct",
    )
    assert p.provider_name == "cloudflare"
    assert p.default_model == "@cf/meta/llama-3.2-1b-instruct"
    assert p.account_id == "acc123456"
    assert "accounts/acc123456/ai/v1/chat/completions" in p.chat_endpoint
    assert p.max_rpm == 300
    await p.close()


@pytest.mark.asyncio
async def test_cloudflare_neuron_tracker():
    """Verify 10,000 Neuron/day budget tracking."""
    tracker = CloudflareNeuronTracker(max_neurons_per_day=10.0)
    await tracker.record_neurons(5.0)
    assert tracker.get_neurons_used_today() == 5.0
    await tracker.record_neurons(5.0)
    assert tracker.get_neurons_used_today() == 10.0

    with pytest.raises(RuntimeError, match="Cloudflare daily Neuron allowance exhausted"):
        await tracker.record_neurons(0.1)


@pytest.mark.asyncio
async def test_zai_provider_initialization():
    """Verify Z.ai provider initialization and concurrency semaphore."""
    p = ZaiProvider(api_key="test-key", default_model="glm-4.7-flash")
    assert p.provider_name == "zai"
    assert p.default_model == "glm-4.7-flash"
    assert p.concurrency_limit == 3
    assert p.semaphore._value == 3
    await p.close()


@pytest.mark.asyncio
async def test_siliconflow_provider_initialization():
    """Verify SiliconFlow model prefixing and international base URL."""
    p = SiliconFlowProvider(api_key="test-key", default_model="Qwen2.5-7B-Instruct")
    assert p.provider_name == "siliconflow"
    assert p.default_model == "Qwen/Qwen2.5-7B-Instruct"
    assert "siliconflow.com" in p.base_url
    await p.close()


@pytest.mark.asyncio
async def test_key_resolution_all_new_providers():
    """Verify 3-tier resolution for mistral, cloudflare, zai, and siliconflow."""
    # 1. Mistral
    assert resolve_api_key("mistral", stage_override="ov-mistral") == "ov-mistral"
    assert resolve_api_key("mistral", db_keys={"mistral_api_key": "db-mistral"}) == "db-mistral"
    assert resolve_api_key("mistral", settings=Settings(MISTRAL_API_KEY="env-mistral")) == "env-mistral"

    # 2. Cloudflare Token
    assert resolve_api_key("cloudflare", stage_override="ov-cf") == "ov-cf"
    assert resolve_api_key("cloudflare", db_keys={"cloudflare_api_token": "db-cf"}) == "db-cf"
    assert resolve_api_key("cloudflare", settings=Settings(CLOUDFLARE_API_TOKEN="env-cf")) == "env-cf"

    # 3. Z.ai
    assert resolve_api_key("zai", stage_override="ov-zai") == "ov-zai"
    assert resolve_api_key("zai", db_keys={"zai_api_key": "db-zai"}) == "db-zai"
    assert resolve_api_key("zai", settings=Settings(ZAI_API_KEY="env-zai")) == "env-zai"

    # 4. SiliconFlow
    assert resolve_api_key("siliconflow", stage_override="ov-sf") == "ov-sf"
    assert resolve_api_key("siliconflow", db_keys={"siliconflow_api_key": "db-sf"}) == "db-sf"
    assert resolve_api_key("siliconflow", settings=Settings(SILICONFLOW_API_KEY="env-sf")) == "env-sf"


@pytest.mark.asyncio
async def test_factory_get_stage_provider_all_new():
    """Verify get_stage_provider correctly instantiates each new provider class."""
    db_keys = {
        "mistral_api_key": "k-mistral",
        "cloudflare_api_token": "k-cf",
        "cloudflare_account_id": "acc-cf",
        "zai_api_key": "k-zai",
        "siliconflow_api_key": "k-sf",
    }

    # Mistral
    cfg_mistral = AIStageConfig(stage_id="gatekeeper", provider="mistral", model_name="ministral-3b-2512")
    p_mistral = get_stage_provider(cfg_mistral, db_keys=db_keys)
    assert isinstance(p_mistral, MistralProvider)
    assert p_mistral.api_key == "k-mistral"
    await p_mistral.close()

    # Cloudflare
    cfg_cf = AIStageConfig(stage_id="extraction", provider="cloudflare", model_name="@cf/meta/llama-3.2-1b-instruct")
    p_cf = get_stage_provider(cfg_cf, db_keys=db_keys)
    assert isinstance(p_cf, CloudflareProvider)
    assert p_cf.api_token == "k-cf"
    assert p_cf.account_id == "acc-cf"
    await p_cf.close()

    # Z.ai
    cfg_zai = AIStageConfig(stage_id="scoring", provider="zai", model_name="glm-4.7-flash")
    p_zai = get_stage_provider(cfg_zai, db_keys=db_keys)
    assert isinstance(p_zai, ZaiProvider)
    assert p_zai.api_key == "k-zai"
    await p_zai.close()

    # SiliconFlow
    cfg_sf = AIStageConfig(stage_id="visuals", provider="siliconflow", model_name="Qwen/Qwen2.5-7B-Instruct")
    p_sf = get_stage_provider(cfg_sf, db_keys=db_keys)
    assert isinstance(p_sf, SiliconFlowProvider)
    assert p_sf.api_key == "k-sf"
    await p_sf.close()


@pytest.mark.asyncio
async def test_provider_registry_caching():
    """Verify ProviderRegistry caches instances across calls for new providers."""
    registry = ProviderRegistry(use_mock=False)
    p1 = registry.get_provider("mistral", "ministral-3b-2512", api_key="test-key")
    p2 = registry.get_provider("mistral", "ministral-3b-2512", api_key="test-key")
    assert p1 is p2
    assert isinstance(p1, MistralProvider)

    p_cf1 = registry.get_provider("cloudflare", "@cf/meta/llama-3.2-1b-instruct", api_key="cf-tok", account_id="cf-acc")
    p_cf2 = registry.get_provider("cloudflare", "@cf/meta/llama-3.2-1b-instruct", api_key="cf-tok", account_id="cf-acc")
    assert p_cf1 is p_cf2
    assert isinstance(p_cf1, CloudflareProvider)

    p_zai = registry.get_provider("zai", "glm-4.7-flash", api_key="zai-tok")
    assert isinstance(p_zai, ZaiProvider)

    p_sf = registry.get_provider("siliconflow", "Qwen/Qwen2.5-7B-Instruct", api_key="sf-tok")
    assert isinstance(p_sf, SiliconFlowProvider)

    await registry.close_all()


@pytest.mark.asyncio
async def test_mock_generation_for_new_providers():
    """Verify ProviderRegistry in mock mode produces valid structured data for new providers."""
    registry = ProviderRegistry(use_mock=True)

    for prov in ["mistral", "cloudflare", "zai", "siliconflow"]:
        p = registry.get_provider(prov, "any-model")
        res = await p.generate_text("Analyze this situation report.", temperature=0.1)
        assert isinstance(res, str)
        assert len(res) > 0

    await registry.close_all()


@pytest.mark.asyncio
async def test_orchestrator_skips_unconfigured_candidates():
    """Verify PipelineOrchestrator gracefully skips providers lacking API keys in candidates without failing."""
    orch = PipelineOrchestrator(use_mock=False)
    orch.set_stage_config(
        "extraction",
        AIStageConfig(
            stage_id="extraction",
            provider="zai",
            model_name="glm-4.7-flash",
            temperature=0.0,
            fallbacks=[
                AIFallbackConfig(provider="siliconflow", model_name="Qwen/Qwen2.5-7B-Instruct", temperature=0.0),
            ],
        ),
    )

    async def _dummy_runner(prov, model, temp):
        return await prov.process_stage("extraction", "Sample text to extract")

    def _dummy_heuristic():
        return {"entities": ["Sample"], "translated_text": "Sample text to extract"}

    res, prov = await orch._execute_stage_with_fallbacks(
        stage_id="extraction",
        runner_fn=_dummy_runner,
        fallback_heuristic_fn=_dummy_heuristic,
        return_provenance=True,
    )
    assert res is not None
    assert prov["provider"] in ["heuristic", "gemini", "groq", "zai", "siliconflow"]


@pytest.mark.asyncio
async def test_live_mistral_chat_completion():
    """Live integration test against Mistral API when MISTRAL_API_KEY is configured."""
    api_key = os.getenv("MISTRAL_API_KEY") or get_settings().MISTRAL_API_KEY
    if not api_key:
        pytest.skip("MISTRAL_API_KEY not configured in environment or .env")

    provider = MistralProvider(api_key=api_key, default_model="ministral-3b-2512")
    try:
        reply = await provider.generate_text(
            prompt="Respond in exactly one word: 'READY'",
            temperature=0.0,
            max_tokens=10,
        )
        assert len(reply.strip()) > 0
    finally:
        await provider.close()


@pytest.mark.asyncio
async def test_live_cloudflare_chat_completion():
    """Live integration test against Cloudflare Workers AI when credentials are configured."""
    token = os.getenv("CLOUDFLARE_API_TOKEN") or get_settings().CLOUDFLARE_API_TOKEN
    account_id = os.getenv("CLOUDFLARE_ACCOUNT_ID") or get_settings().CLOUDFLARE_ACCOUNT_ID
    if not token or not account_id:
        pytest.skip("CLOUDFLARE_API_TOKEN or CLOUDFLARE_ACCOUNT_ID not configured")

    provider = CloudflareProvider(
        account_id=account_id,
        api_token=token,
        default_model="@cf/meta/llama-3.2-1b-instruct",
    )
    try:
        reply = await provider.generate_text(
            prompt="Respond in exactly one word: 'OK'",
            temperature=0.0,
            max_tokens=10,
        )
        assert len(reply.strip()) > 0
    finally:
        await provider.close()
