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

    ai_settings = {
        "extraction": {"model_name": "qwen/qwen3.8-27b"},
        "scoring": {"model_name": "gemini-2.5-flash"},
    }

    tel = tracker.get_telemetry(ai_settings=ai_settings)

    assert "summary" in tel
    assert tel["summary"]["total_requests"] >= 3
    assert tel["summary"]["total_tokens"] >= 500

    # Provider assertions
    providers = tel["providers"]
    assert "gemini" in providers
    assert "groq" in providers

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
