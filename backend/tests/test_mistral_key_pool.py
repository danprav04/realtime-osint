"""
backend/tests/test_mistral_key_pool.py - Unit & Integration Tests for Mistral Multi-Key Pool
=============================================================================================
Tests:
  1. Model limits lookup (TPM, RPS, min_interval) for all official Mistral models.
  2. Mistral key masking, deduplication, and list parsing.
  3. Round-robin key selection and load distribution across healthy keys.
  4. Independent per-key pacing slots adhering to model RPS limits.
  5. HTTP 429 rate limit failover (per-model cooldown isolation).
  6. Monthly allowance ($10) quota exhaustion detection and cooldown until month reset.
  7. HTTP 401/403 unauthorized key deactivation.
  8. Live telemetry reporting and pool observability.
  9. MistralProvider execution with automatic key rotation and failover.
  10. Live integration test with real multi-key pool (when credentials present in .env).
"""

import asyncio
from datetime import datetime, timezone
import json
import os
import pytest
import httpx

from backend.app.config import Settings, get_settings
from backend.app.pipeline.models import AIStageConfig
from backend.app.pipeline.providers.factory import (
    get_shared_mistral_pool,
    get_stage_provider,
    resolve_api_keys,
)
from backend.app.pipeline.providers.mistral import MistralProvider
from backend.app.pipeline.providers.mistral_key_pool import (
    KeyStatus,
    MistralKey,
    MistralKeyPool,
    MISTRAL_MODEL_LIMITS,
    get_mistral_model_limits,
    seconds_until_next_month_utc,
)


def test_mistral_model_limits():
    """Verify official model limits catalog and resolution."""
    # 1. ministral-3b: 12.5 RPS, 1,300,000 TPM
    lim_3b = get_mistral_model_limits("ministral-3b-2512")
    assert lim_3b["rps"] == 12.50
    assert lim_3b["tpm"] == 1_300_000
    assert abs(lim_3b["min_interval"] - (1.0 / 12.50)) < 1e-4

    # 2. ministral-8b: 3.13 RPS, 625,000 TPM
    lim_8b = get_mistral_model_limits("ministral-8b-2512")
    assert lim_8b["rps"] == 3.13
    assert lim_8b["tpm"] == 625_000

    # 3. codestral-2508: 2.08 RPS, 625,000 TPM
    lim_code = get_mistral_model_limits("codestral-2508")
    assert lim_code["rps"] == 2.08
    assert lim_code["tpm"] == 625_000

    # 4. ministral-14b: 0.50 RPS, 937,500 TPM
    lim_14b = get_mistral_model_limits("ministral-14b-2512")
    assert lim_14b["rps"] == 0.50
    assert lim_14b["tpm"] == 937_500
    assert abs(lim_14b["min_interval"] - 2.0) < 1e-4

    # 5. mistral-large-2512: 1.00 RPS, 250,000 TPM
    lim_large = get_mistral_model_limits("mistral-large-2512")
    assert lim_large["rps"] == 1.00
    assert lim_large["tpm"] == 250_000

    # 6. mistral-small-2603: 1.00 RPS, 20,000 TPM
    lim_small = get_mistral_model_limits("mistral-small-2603")
    assert lim_small["rps"] == 1.00
    assert lim_small["tpm"] == 20_000

    # 7. mistral-large-4: 0.83 RPS, 20,000 TPM
    lim_l4 = get_mistral_model_limits("mistral-large-4")
    assert lim_l4["rps"] == 0.83
    assert lim_l4["tpm"] == 20_000

    # 8. labs-leanstral-1-5-1: 0.63 RPS, 5,000,000 TPM
    lim_lean = get_mistral_model_limits("labs-leanstral-1-5-1")
    assert lim_lean["rps"] == 0.63
    assert lim_lean["tpm"] == 5_000_000


def test_seconds_until_next_month_utc():
    """Verify next calendar month calculation."""
    sec = seconds_until_next_month_utc()
    assert sec >= 60.0
    # Can't be more than 32 days
    assert sec <= 32 * 86400.0


def test_mistral_key_initialization_and_masking():
    """Verify MistralKey properties and key masking."""
    k = MistralKey(key="mstrl_8L1oy0pR9KwU4Blem0HBzzM2gTPdVLP5_2472t9")
    assert k.masked_key == "mstr...72t9"
    assert k.status == KeyStatus.ACTIVE
    assert k.monthly_allowance_usd == 10.0
    assert k.is_available() is True


@pytest.mark.asyncio
async def test_round_robin_selection():
    """Verify MistralKeyPool rotates keys in round-robin fashion."""
    keys = ["mstrl_key1_1111", "mstrl_key2_2222", "mstrl_key3_3333"]
    pool = MistralKeyPool(keys=keys)
    assert pool.total_keys == 3

    k1 = await pool.acquire_next_key()
    assert k1 is not None and k1.key == "mstrl_key1_1111"

    k2 = await pool.acquire_next_key()
    assert k2 is not None and k2.key == "mstrl_key2_2222"

    k3 = await pool.acquire_next_key()
    assert k3 is not None and k3.key == "mstrl_key3_3333"

    k4 = await pool.acquire_next_key()
    assert k4 is not None and k4.key == "mstrl_key1_1111"


@pytest.mark.asyncio
async def test_independent_per_key_pacing():
    """Verify pacing on one key does not block another key in the pool."""
    k1 = MistralKey("mstrl_key1_1111")
    k2 = MistralKey("mstrl_key2_2222")

    # k1 paced with ministral-14b (0.5 RPS = 2s spacing)
    await k1.acquire_pacing_slot(model="ministral-14b-2512")
    # Immediate acquire on k2 should not be blocked
    t0 = asyncio.get_event_loop().time()
    await k2.acquire_pacing_slot(model="ministral-3b-2512")
    t1 = asyncio.get_event_loop().time()
    assert (t1 - t0) < 0.2


@pytest.mark.asyncio
async def test_429_model_rate_limit_failover():
    """Verify 429 on a model applies cooldown to that model, failover rotates to next key."""
    keys = ["mstrl_k1_1111", "mstrl_k2_2222"]
    pool = MistralKeyPool(keys=keys)

    # Key 1 acquires for ministral-3b
    k1 = await pool.acquire_next_key(model="ministral-3b-2512")
    assert k1.key == "mstrl_k1_1111"

    # Simulate 429 response from Mistral on Key 1 with retry-after: 5
    resp_429 = httpx.Response(
        status_code=429,
        headers={"retry-after": "5"},
        json={"message": "Rate limit exceeded for model ministral-3b-2512"},
        request=httpx.Request("POST", "https://api.mistral.ai/v1/chat/completions"),
    )
    k1.update_from_response(resp_429, model="ministral-3b-2512")

    # Key 1 is on cooldown for ministral-3b
    assert k1.is_available(model="ministral-3b-2512") is False
    # But Key 1 remains ACTIVE for other models (e.g. codestral-2508)
    assert k1.is_available(model="codestral-2508") is True

    # Next acquire for ministral-3b rotates to Key 2
    k2 = await pool.acquire_next_key(model="ministral-3b-2512")
    assert k2 is not None and k2.key == "mstrl_k2_2222"


@pytest.mark.asyncio
async def test_monthly_allowance_exhaustion_failover():
    """Verify monthly allowance ($10) quota exhaustion marks key daily/monthly exhausted."""
    keys = ["mstrl_k1_1111", "mstrl_k2_2222"]
    pool = MistralKeyPool(keys=keys)

    k1 = await pool.acquire_next_key()
    assert k1.key == "mstrl_k1_1111"

    resp_quota = httpx.Response(
        status_code=429,
        headers={},
        json={"message": "Quota exceeded. Your free tier monthly allowance has been exhausted."},
        request=httpx.Request("POST", "https://api.mistral.ai/v1/chat/completions"),
    )
    k1.update_from_response(resp_quota, model="ministral-3b-2512")

    assert k1.status == KeyStatus.DAILY_EXHAUSTED
    assert k1.is_available() is False
    assert k1.monthly_quota_exhaustions == 1

    # Rotation routes to Key 2
    k2 = await pool.acquire_next_key()
    assert k2 is not None and k2.key == "mstrl_k2_2222"


@pytest.mark.asyncio
async def test_unauthorized_401_deactivation():
    """Verify HTTP 401 permanently deactivates key."""
    keys = ["mstrl_k1_invalid", "mstrl_k2_valid"]
    pool = MistralKeyPool(keys=keys)

    k1 = await pool.acquire_next_key()
    assert k1.key == "mstrl_k1_invalid"

    resp_401 = httpx.Response(
        status_code=401,
        json={"message": "Unauthorized: Invalid API key"},
        request=httpx.Request("POST", "https://api.mistral.ai/v1/chat/completions"),
    )
    k1.update_from_response(resp_401)
    assert k1.status == KeyStatus.UNAUTHORIZED
    assert k1.is_available() is False

    k2 = await pool.acquire_next_key()
    assert k2 is not None and k2.key == "mstrl_k2_valid"


@pytest.mark.asyncio
async def test_all_keys_exhausted():
    """Verify acquire_next_key returns None when all keys in cooldown."""
    k = MistralKey("mstrl_k1")
    pool = MistralKeyPool()
    pool.set_keys(["mstrl_k1"])

    key = await pool.acquire_next_key(model="ministral-3b-2512")
    assert key is not None

    key.mark_rate_limited(is_daily=False, retry_delay_sec=30.0, model="ministral-3b-2512")
    assert await pool.acquire_next_key(model="ministral-3b-2512") is None


def test_telemetry_reporting():
    """Verify telemetry dict includes per-key and aggregated pool stats."""
    keys = ["mstrl_key1_1111", "mstrl_key2_2222"]
    pool = MistralKeyPool(keys=keys)
    tel = pool.get_telemetry()
    assert tel["total_keys"] == 2
    assert tel["active_keys"] == 2
    assert tel["monthly_allowance_usd_total"] == 20.0
    assert len(tel["keys"]) == 2
    assert tel["keys"][0]["masked_key"] == "mstr...1111"


@pytest.mark.asyncio
async def test_mistral_provider_rotation_on_429():
    """Verify MistralProvider catches 429 on Key 1, fails over to Key 2, and succeeds."""
    keys = ["mstrl_mock_k1", "mstrl_mock_k2"]
    pool = MistralKeyPool(keys=keys)
    provider = MistralProvider(key_pool=pool, default_model="ministral-3b-2512")

    attempt_count = 0

    async def mock_post(url, json=None, headers=None):
        nonlocal attempt_count
        attempt_count += 1
        auth_hdr = headers.get("Authorization", "")
        if "mstrl_mock_k1" in auth_hdr:
            return httpx.Response(
                status_code=429,
                headers={"retry-after": "5"},
                json={"error": {"message": "Rate limit exceeded"}},
                request=httpx.Request("POST", url),
            )
        elif "mstrl_mock_k2" in auth_hdr:
            return httpx.Response(
                status_code=200,
                json={"choices": [{"message": {"content": "SUCCESS_FROM_KEY_2"}}]},
                request=httpx.Request("POST", url),
            )
        raise RuntimeError("Unexpected key")

    client = await provider.get_client()
    client.post = mock_post  # type: ignore

    res = await provider.generate_text("Hello test")
    assert res == "SUCCESS_FROM_KEY_2"
    assert attempt_count == 2
    await provider.close()


def test_factory_multi_key_resolution():
    """Verify 3-tier resolution parses comma-separated Mistral keys into a list."""
    st = Settings(MISTRAL_API_KEYS="mstrl_k1, mstrl_k2, mstrl_k3")
    keys = resolve_api_keys("mistral", settings=st)
    assert keys == ["mstrl_k1", "mstrl_k2", "mstrl_k3"]

    # Deduplication
    st_dups = Settings(MISTRAL_API_KEYS="mstrl_k1, mstrl_k1, mstrl_k2")
    keys_dups = resolve_api_keys("mistral", settings=st_dups)
    assert keys_dups == ["mstrl_k1", "mstrl_k2"]


@pytest.mark.asyncio
async def test_live_mistral_pool_chat_completion():
    """Live integration test against Mistral API using the multi-key pool configured in .env."""
    keys = get_settings().get_mistral_api_keys()
    if not keys:
        pytest.skip("No MISTRAL_API_KEYS configured")

    pool = get_shared_mistral_pool(keys=keys)
    assert pool.total_keys >= 1

    provider = MistralProvider(key_pool=pool, default_model="ministral-3b-2512")
    try:
        reply = await provider.generate_text(
            prompt="Respond in exactly one word: 'ACTIVE'",
            temperature=0.0,
            max_tokens=10,
        )
        assert len(reply.strip()) > 0
    finally:
        await provider.close()


@pytest.mark.asyncio
async def test_api_mistral_pool_endpoint():
    """Verify /api/settings/mistral-pool returns live pool telemetry."""
    from httpx import ASGITransport, AsyncClient
    from backend.app.main import create_app

    app = create_app()
    transport = ASGITransport(app=app)
    settings = get_settings()

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"X-API-Key": settings.API_KEY}
        resp = await client.get("/api/settings/mistral-pool", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "total_keys" in data
        assert "active_keys" in data
        assert "keys" in data
        assert data["total_keys"] >= 1
