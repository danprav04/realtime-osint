"""
backend/tests/test_usage_tracker.py - Tests for Live Provider, Key & Model Usage Tracking
"""

import pytest
from backend.app.pipeline.usage_tracker import ProviderUsageTracker, get_usage_tracker


def test_tracker_singleton():
    t1 = get_usage_tracker()
    t2 = get_usage_tracker()
    assert t1 is t2


def test_record_call_and_telemetry():
    tracker = ProviderUsageTracker()
    tracker.reset_for_testing()

    # Record Gemini call
    tracker.record_call(
        provider="gemini",
        model="gemini-2.5-flash",
        key="AIzaSy1234567890abcdef",
        key_field="gemini_api_key",
        prompt_tokens=150,
        completion_tokens=50,
        total_tokens=200,
        success=True,
        status_code=200,
        duration_ms=450.0,
    )

    # Record Groq call with live headers
    tracker.record_call(
        provider="groq",
        model="qwen/qwen3.8-27b",
        key="gsk_abcdef1234567890xyz",
        key_field="groq_api_key",
        prompt_tokens=220,
        completion_tokens=80,
        total_tokens=300,
        success=True,
        status_code=200,
        duration_ms=120.0,
        remaining_requests=14390,
        remaining_tokens=29500,
        limit_requests=14400,
        limit_tokens=30000,
        reset_requests_sec=45.0,
        reset_tokens_sec=5.0,
    )

    # Record Groq 429
    tracker.record_call(
        provider="groq",
        model="qwen/qwen3.8-27b",
        key="gsk_abcdef1234567890xyz",
        key_field="groq_api_key",
        success=False,
        status_code=429,
        error_message="Rate limit 429",
        is_rate_limit=True,
        cooldown_sec=30.0,
    )

    # Record Mistral call
    tracker.record_call(
        provider="mistral",
        model="ministral-3b-2512",
        key="mstrl_testkey1234567890abcdef",
        key_field="mistral_api_key",
        prompt_tokens=100,
        completion_tokens=40,
        total_tokens=140,
        success=True,
        status_code=200,
        duration_ms=80.0,
    )

    # Record Cloudflare call
    tracker.record_call(
        provider="cloudflare",
        model="@cf/meta/llama-3.2-1b-instruct",
        key="cfut_testkey1234567890abcdef",
        key_field="cloudflare_api_token",
        prompt_tokens=80,
        completion_tokens=30,
        total_tokens=110,
        success=True,
        status_code=200,
        duration_ms=60.0,
    )

    ai_settings = {
        "extraction": {"model_name": "qwen/qwen3.8-27b"},
        "scoring": {"model_name": "gemini-2.5-flash"},
        "gatekeeper": {"model_name": "ministral-3b-2512"},
    }

    tel = tracker.get_telemetry(ai_settings=ai_settings)

    assert "summary" in tel
    assert tel["summary"]["total_requests"] >= 5
    assert tel["summary"]["total_tokens"] >= 750

    # Provider assertions
    providers = tel["providers"]
    assert "gemini" in providers
    assert "groq" in providers
    assert "mistral" in providers
    assert "cloudflare" in providers
    assert "zai" in providers
    assert "siliconflow" in providers

    mistral_prov = providers["mistral"]
    assert mistral_prov["total_requests"] >= 1
    assert mistral_prov["successful_requests"] >= 1
    assert mistral_prov["total_tokens"] >= 140

    cf_prov = providers["cloudflare"]
    assert cf_prov["total_requests"] == 1
    assert cf_prov["successful_requests"] == 1
    assert cf_prov["total_tokens"] == 110

    gemini_prov = providers["gemini"]
    assert gemini_prov["total_requests"] == 1
    assert gemini_prov["successful_requests"] == 1
    assert gemini_prov["total_tokens"] == 200
    assert len(gemini_prov["keys"]) >= 1
    assert gemini_prov["keys"][0]["masked_key"].startswith("AIza")

    groq_prov = providers["groq"]
    assert groq_prov["total_requests"] == 2
    assert groq_prov["successful_requests"] == 1
    assert groq_prov["rate_limited_count"] == 1
    assert groq_prov["total_tokens"] == 300
    assert len(groq_prov["keys"]) >= 1

    # Models assertions
    models = tel["all_models"]
    qwen_m = next((m for m in models if m["model_name"] == "qwen/qwen3.8-27b"), None)
    assert qwen_m is not None
    assert qwen_m["total_requests"] == 2
    assert qwen_m["successful_requests"] == 1
    assert "extraction" in qwen_m["stages"]

    gemini_m = next((m for m in models if m["model_name"] == "gemini-2.5-flash"), None)
    assert gemini_m is not None
    assert gemini_m["total_requests"] == 1
    assert "scoring" in gemini_m["stages"]


@pytest.mark.asyncio
async def test_api_provider_usage_endpoint(monkeypatch):
    from httpx import ASGITransport, AsyncClient
    from backend.app.main import create_app
    from backend.app.config import get_settings

    app = create_app()
    transport = ASGITransport(app=app)
    settings = get_settings()

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"X-API-Key": settings.API_KEY}
        resp = await client.get("/api/settings/provider-usage", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "summary" in data
        assert "providers" in data
        assert "all_models" in data
        assert "all_keys" in data
        assert "gemini" in data["providers"]
        assert "groq" in data["providers"]
        assert "mistral" in data["providers"]
        assert "cloudflare" in data["providers"]
        assert "zai" in data["providers"]
        assert "siliconflow" in data["providers"]


@pytest.mark.asyncio
async def test_api_settings_endpoint():
    from httpx import ASGITransport, AsyncClient
    from backend.app.main import create_app
    from backend.app.config import get_settings

    app = create_app()
    transport = ASGITransport(app=app)
    settings = get_settings()

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"X-API-Key": settings.API_KEY}
        resp = await client.get("/api/settings", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "api_keys" in data
        api_keys = data["api_keys"]
        assert "mistral_api_key" in api_keys
        assert "cloudflare_api_token" in api_keys
        assert "cloudflare_api_key" in api_keys
        assert "zai_api_key" in api_keys
        assert "siliconflow_api_key" in api_keys


def test_model_rate_limit_isolates_from_key_status():
    """Verify that a model-specific 429 leaves the key active and only puts the model into cooldown."""
    tracker = ProviderUsageTracker()
    tracker.reset_for_testing()

    # Model hits 429
    tracker.record_call(
        provider="groq",
        model="llama-3.3-70b-versatile",
        key="gsk_isolated_test_key_123",
        key_field="groq_api_key",
        success=False,
        status_code=429,
        error_message="Rate limit 429 retry-after 307s",
        is_rate_limit=True,
        cooldown_sec=307.0,
        is_key_level=False,
    )

    tel = tracker.get_telemetry()
    groq_prov = tel["providers"]["groq"]

    # Key must remain ACTIVE with 0 cooldown!
    assert groq_prov["cooldown_keys_count"] == 0
    k = next(k for k in groq_prov["keys"] if "gsk_isolated" in k.get("masked_key", "") or k.get("key_field") == "groq_api_key")
    assert k["status"] == "active"
    assert k["cooldown_remaining_sec"] == 0.0
    assert "llama-3.3-70b-versatile" in k["throttled_models"]

    # Model must be in cooldown!
    assert groq_prov["cooldown_models_count"] >= 1
    m = next(m for m in groq_prov["models"] if m["model_name"] == "llama-3.3-70b-versatile")
    assert m["status"] == "cooldown"
    assert m["cooldown_remaining_sec"] > 250.0


def test_provider_classification_and_catalog_defaults():
    """Verify that Groq GPT-OSS models are classified under Groq, Gemini Embeddings under Gemini, etc."""
    tracker = ProviderUsageTracker()
    tracker.reset_for_testing()

    ai_settings = {
        "summarization": {
            "provider": "groq",
            "model_name": "openai/gpt-oss-20b",
            "fallbacks": [
                {"provider": "gemini", "model_name": "gemini-3.5-flash-lite"},
            ],
        },
        "scoring": {
            "provider": "gemini",
            "model_name": "gemma-4-26b-a4b-it",
            "fallbacks": [
                {"provider": "groq", "model_name": "openai/gpt-oss-120b"},
            ],
        },
    }

    tel = tracker.get_telemetry(ai_settings=ai_settings)
    models = tel["all_models"]

    # openai/gpt-oss-20b and openai/gpt-oss-120b must belong to groq
    gpt_20b = next((m for m in models if m["model_name"] == "openai/gpt-oss-20b"), None)
    assert gpt_20b is not None
    assert gpt_20b["provider"] == "groq"

    gpt_120b = next((m for m in models if m["model_name"] == "openai/gpt-oss-120b"), None)
    assert gpt_120b is not None
    assert gpt_120b["provider"] == "groq"

    # gemini-embedding-2 must belong to gemini
    gemini_emb2 = next((m for m in models if m["model_name"] == "gemini-embedding-2"), None)
    assert gemini_emb2 is not None
    assert gemini_emb2["provider"] == "gemini"

    # gemma-4-26b-a4b-it must belong to gemini
    gemma = next((m for m in models if m["model_name"] == "gemma-4-26b-a4b-it"), None)
    assert gemma is not None
    assert gemma["provider"] == "gemini"


def test_pool_model_not_in_cooldown_when_sibling_keys_available():
    """Verify that a multi-key pool does NOT block a model when other keys in the pool are available."""
    from backend.app.pipeline.providers.key_pool import GroqKeyPool, GroqKey
    from backend.app.pipeline.providers.factory import get_shared_groq_pool

    pool = get_shared_groq_pool()
    orig_keys = list(pool._keys)
    try:
        # Set up 4 keys in pool
        k1 = GroqKey("gsk_key_alpha_111111111111")
        k2 = GroqKey("gsk_key_beta_2222222222222")
        k3 = GroqKey("gsk_key_gamma_333333333333")
        k4 = GroqKey("gsk_key_delta_444444444444")
        pool._keys = [k1, k2, k3, k4]

        # Keys 1 and 2 hit 429 on qwen/qwen3.8-27b
        k1.mark_rate_limited(is_daily=True, retry_delay_sec=300.0, model="qwen/qwen3.8-27b")
        k2.mark_rate_limited(is_daily=True, retry_delay_sec=250.0, model="qwen/qwen3.8-27b")
        # Keys 3 and 4 remain completely active

        tracker = ProviderUsageTracker()
        tracker.reset_for_testing()

        # Record a 429 call that occurred on Key 1
        tracker.record_call(
            provider="groq",
            model="qwen/qwen3.8-27b",
            key="gsk_key_alpha_111111111111",
            key_field="groq_api_key",
            success=False,
            status_code=429,
            error_message="daily tokens exhausted (TPD 200k, retry-after 300s)",
            is_rate_limit=True,
            cooldown_sec=300.0,
        )

        tel = tracker.get_telemetry()
        groq_models = [m for m in tel["all_models"] if m["model_name"] == "qwen/qwen3.8-27b"]
        assert len(groq_models) == 1
        m_stat = groq_models[0]

        # Because Key 3 and Key 4 are still available, the model itself must NOT be in cooldown!
        assert m_stat["status"] != "cooldown"
        assert m_stat["cooldown_remaining_sec"] == 0.0

        # Now throttle keys 3 and 4 as well (all 4 keys now in cooldown)
        k3.mark_rate_limited(is_daily=True, retry_delay_sec=45.0, model="qwen/qwen3.8-27b")
        k4.mark_rate_limited(is_daily=True, retry_delay_sec=120.0, model="qwen/qwen3.8-27b")

        tel2 = tracker.get_telemetry()
        m_stat2 = next(m for m in tel2["all_models"] if m["model_name"] == "qwen/qwen3.8-27b")

        # Now all keys are throttled, so model must be in cooldown
        assert m_stat2["status"] == "cooldown"
        # Cooldown must be min(45, 120, 250, 300) = ~45.0s (first key to recover), NOT 300s!
        assert 40.0 <= m_stat2["cooldown_remaining_sec"] <= 50.0

    finally:
        pool._keys = orig_keys



