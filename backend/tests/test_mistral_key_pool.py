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
from collections import deque
from datetime import datetime, timezone
import json
import os
import time
from unittest.mock import AsyncMock
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
    _get_loop_time,
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


# ============================================================================
# 1. Concurrent Atomic Key Reservation (Even Distribution Across 4 Keys)
# ============================================================================

@pytest.mark.asyncio
async def test_concurrent_atomic_key_reservation_even_distribution_4_keys():
    """
    Verify that when 16 concurrent requests call acquire_next_key() simultaneously,
    the 4 keys in the pool are selected in strictly equal distribution (4 each).
    """
    keys = ["mstrl_k1", "mstrl_k2", "mstrl_k3", "mstrl_k4"]
    pool = MistralKeyPool(keys=keys)
    model = "ministral-3b-2512"

    tasks = [pool.acquire_next_key(model=model) for _ in range(16)]
    selected_keys = await asyncio.gather(*tasks)

    counts = {k: 0 for k in keys}
    for item in selected_keys:
        assert item is not None
        counts[item.key] += 1

    for k in keys:
        assert counts[k] == 4, f"Key {k} received {counts[k]} requests, expected exactly 4"


@pytest.mark.asyncio
async def test_concurrent_atomic_key_reservation_with_cooldown_excludes_key():
    """
    Verify that when Key 2 is in cooldown for a model, 12 concurrent requests
    are evenly distributed across the 3 remaining active keys (4 each).
    """
    keys = ["mstrl_k1", "mstrl_k2", "mstrl_k3", "mstrl_k4"]
    pool = MistralKeyPool(keys=keys)
    model = "mistral-small-2603"

    # Put Key 2 in model cooldown
    k2 = pool.get_keys()[1]
    k2.mark_rate_limited(is_daily=False, retry_delay_sec=30.0, model=model)

    tasks = [pool.acquire_next_key(model=model) for _ in range(12)]
    selected_keys = await asyncio.gather(*tasks)

    counts = {k: 0 for k in keys}
    for item in selected_keys:
        assert item is not None
        counts[item.key] += 1

    assert counts["mstrl_k2"] == 0, "Key 2 in cooldown was selected"
    assert counts["mstrl_k1"] == 4
    assert counts["mstrl_k3"] == 4
    assert counts["mstrl_k4"] == 4


# ============================================================================
# 2. Pacing & 0.08s Jitter Margin
# ============================================================================

@pytest.mark.asyncio
async def test_pacing_slot_008s_interval_ministral_3b():
    """
    Verify that ministral-3b-2512 enforces a minimum spacing of 0.08s (12.5 RPS)
    between consecutive calls on the same key.
    """
    key = MistralKey("mstrl_pacing_key")
    model = "ministral-3b-2512"

    await key.acquire_pacing_slot(model=model)
    t0 = time.monotonic()
    await key.acquire_pacing_slot(model=model)
    elapsed = time.monotonic() - t0

    # 1.0 / 12.50 = 0.08s spacing
    assert elapsed >= 0.075, f"Elapsed {elapsed:.4f}s is less than minimum 0.08s spacing"


@pytest.mark.asyncio
async def test_short_wait_cooldown_sleep_with_jitter_margin():
    """
    Verify that when all keys have a transient short cooldown (<= 3.0s),
    acquire_next_key selects the closest key and acquire_pacing_slot recovers
    with a jitter margin rather than immediately failing.
    """
    pool = MistralKeyPool(keys=["mstrl_jitter_k1"])
    provider = MistralProvider(key_pool=pool, default_model="ministral-3b-2512")

    k1 = pool.get_keys()[0]
    # Set short 0.12s cooldown
    k1.mark_rate_limited(is_daily=False, retry_delay_sec=0.12, model="ministral-3b-2512")

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.is_closed = False

    async def mock_post(url, json=None, headers=None):
        req = httpx.Request("POST", url)
        return httpx.Response(
            200,
            request=req,
            json={"choices": [{"message": {"content": "ok_recovered"}}]},
        )

    mock_client.post = mock_post
    provider._client = mock_client

    t0 = time.monotonic()
    result = await provider.generate_text("test recovery", model="ministral-3b-2512")
    elapsed = time.monotonic() - t0

    assert result == "ok_recovered"
    assert elapsed >= 0.10, "Did not wait for the short cooldown"
    assert elapsed < 1.0, "Waited excessively long for short cooldown"
    await provider.close()


# ============================================================================
# 3. 60-Second Sliding-Window TPM Tracking & Enforcement
# ============================================================================

@pytest.mark.asyncio
async def test_60s_sliding_window_tpm_tracking_and_rotation():
    """
    Verify 60s sliding-window TPM tracking on mistral-small-2603 (20,000 TPM limit):
    When Key 1 hits 20,000 tokens, traffic automatically rotates to Key 2.
    """
    pool = MistralKeyPool(keys=["mstrl_tpm_k1", "mstrl_tpm_k2"])
    k1, k2 = pool.get_keys()
    model = "mistral-small-2603"

    # Record 20,500 tokens on Key 1 within current window
    k1.record_token_usage(model, 20500)

    # Key 1 is at capacity for mistral-small-2603
    assert k1.is_available(model=model) is False
    # Key 2 remains available
    assert k2.is_available(model=model) is True

    # 10 acquisitions must all route strictly to Key 2
    for _ in range(10):
        selected = await pool.acquire_next_key(model=model)
        assert selected is not None and selected.key == "mstrl_tpm_k2"


@pytest.mark.asyncio
async def test_sliding_window_tpm_pruning_after_60_seconds():
    """
    Verify that token records older than 60 seconds roll off the sliding window,
    restoring key availability.
    """
    key = MistralKey("mstrl_prune_key")
    model = "mistral-small-2603"

    now = _get_loop_time()
    # Insert an expired record from 65s ago and a recent record from 10s ago
    key._model_token_records[model] = deque([
        (now - 65.0, 18000),
        (now - 10.0, 2000),
    ])

    # Recording new token usage prunes the 65s record
    key.record_token_usage(model, 1000)

    # Only recent 2,000 + 1,000 = 3,000 tokens should remain
    total_tok = sum(t for _, t in key._model_token_records[model])
    assert total_tok == 3000
    assert key.is_available(model=model) is True


@pytest.mark.asyncio
async def test_tpm_token_usage_parsing_from_response():
    """
    Verify that update_from_response correctly parses usage.total_tokens from
    HTTP 200 JSON body and records it in the model's sliding window.
    """
    key = MistralKey("mstrl_resp_token_key")
    model = "mistral-large-2512"

    req = httpx.Request("POST", "https://api.mistral.ai/v1/chat/completions")
    resp_200 = httpx.Response(
        200,
        request=req,
        json={
            "choices": [{"message": {"content": "response content"}}],
            "usage": {"prompt_tokens": 120, "completion_tokens": 80, "total_tokens": 200},
        },
    )

    key.update_from_response(resp_200, model=model)
    records = key._model_token_records.get(model, deque())
    assert len(records) == 1
    assert records[0][1] == 200


# ============================================================================
# 4. Transient 429 Reason Formatting ('minute rate limit / model RPS limit')
# ============================================================================

def test_transient_429_reason_formatting_minute_rps_limit():
    """
    Verify that non-monthly 429 responses are formatted with:
    'minute rate limit / model RPS limit', ensuring compatibility with orchestrator.py.
    """
    key = MistralKey("mstrl_reason_key")
    model = "ministral-8b-2512"

    req = httpx.Request("POST", "https://api.mistral.ai/v1/chat/completions")
    resp_429 = httpx.Response(
        429,
        request=req,
        headers={"retry-after": "4"},
        json={"message": "Requests rate limit reached"},
    )

    key.update_from_response(resp_429, model=model)

    reason = key._model_cooldown_reasons.get(model, "")
    assert "minute rate limit / model RPS limit" in reason
    assert "retry-after 4" in reason


@pytest.mark.asyncio
async def test_transient_429_orchestrator_compatibility_check():
    """
    Verify that when MistralProvider raises a RateLimitError on transient 429,
    the error message satisfies orchestrator.py:819's is_minute_only check:
    any(k in err_str for k in ("minute rate limit", ...)) and not any(k in err_str for k in ("daily", ...)).
    """
    pool = MistralKeyPool(keys=["mstrl_single_k"])
    provider = MistralProvider(key_pool=pool, default_model="ministral-3b-2512", max_retries=1)

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.is_closed = False

    async def mock_post(url, json=None, headers=None):
        req = httpx.Request("POST", url)
        return httpx.Response(
            429,
            request=req,
            headers={"retry-after": "5"},
            json={"message": "Rate limit reached"},
        )

    mock_client.post = mock_post
    provider._client = mock_client

    with pytest.raises(RuntimeError) as exc_info:
        await provider.generate_text("test 429", model="ministral-3b-2512")

    err_str = str(exc_info.value).lower()
    assert "mistral ratelimiterror: 429" in err_str
    assert "minute rate limit / model rps limit" in err_str

    # Orchestrator is_minute_only filter check
    is_minute_only = (
        any(k in err_str for k in ("minute rate limit", "perminute", "per_minute", "(rpm)", "(tpm)", "requests per minute", "tokens per minute"))
        and not any(k in err_str for k in ("daily", "perday", "per_day", "(rpd)", "(tpd)", "requests per day", "tokens per day"))
    )
    assert is_minute_only is True, "orchestrator is_minute_only filter failed on Mistral 429 reason"
    await provider.close()


# ============================================================================
# 5. max_tokens Preservation in mistral.py
# ============================================================================

@pytest.mark.asyncio
async def test_max_tokens_preservation_stage0_gatekeeper_256():
    """
    Verify that when caller specifies max_tokens=256 (e.g. Stage 0 Gatekeeper),
    MistralProvider passes exactly max_tokens=256 in the outgoing payload,
    never inflating it to 1536 or 1024.
    """
    pool = MistralKeyPool(keys=["mstrl_mt_key"])
    provider = MistralProvider(key_pool=pool, default_model="ministral-3b-2512")

    captured_payload = None

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.is_closed = False

    async def mock_post(url, json=None, headers=None):
        nonlocal captured_payload
        captured_payload = json
        req = httpx.Request("POST", url)
        return httpx.Response(
            200,
            request=req,
            json={"choices": [{"message": {"content": '{"keep": [0, 1]}'}}]},
        )

    mock_client.post = mock_post
    provider._client = mock_client

    # Stage 0 Gatekeeper invocation with max_tokens=256 and json_mode=True
    res = await provider.generate_text(
        prompt="Filter batch items",
        max_tokens=256,
        json_mode=True,
    )
    assert res == '{"keep": [0, 1]}'
    assert captured_payload is not None
    assert captured_payload["max_tokens"] == 256, (
        f"max_tokens was inflated to {captured_payload['max_tokens']}; expected strictly 256"
    )
    await provider.close()


@pytest.mark.asyncio
async def test_max_tokens_preservation_custom_small_values():
    """
    Verify that any custom small max_tokens (e.g. 64, 128, 500) is strictly respected
    both with and without json_mode.
    """
    pool = MistralKeyPool(keys=["mstrl_custom_key"])
    provider = MistralProvider(key_pool=pool, default_model="ministral-3b-2512")

    captured_payloads = []

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.is_closed = False

    async def mock_post(url, json=None, headers=None):
        captured_payloads.append(dict(json))
        req = httpx.Request("POST", url)
        return httpx.Response(
            200,
            request=req,
            json={"choices": [{"message": {"content": "ok"}}]},
        )

    mock_client.post = mock_post
    provider._client = mock_client

    # Test 1: max_tokens=64 without json_mode
    await provider.generate_text("Prompt 1", max_tokens=64, json_mode=False)
    assert captured_payloads[-1]["max_tokens"] == 64

    # Test 2: max_tokens=128 with json_mode
    await provider.generate_text("Prompt 2", max_tokens=128, json_mode=True)
    assert captured_payloads[-1]["max_tokens"] == 128

    # Test 3: Default max_tokens (when None specified) defaults to 2048
    await provider.generate_text("Prompt 3")
    assert captured_payloads[-1]["max_tokens"] == 2048
    await provider.close()


# ============================================================================
# 6. Milestone M2 Remediation Verification Tests
# ============================================================================

@pytest.mark.asyncio
async def test_exact_one_time_token_recording_per_call():
    """
    Verify that a single generate_text() call through MistralProvider records
    token usage exactly once (1x) into the key's sliding-window deque and TPM,
    preventing double-counting inflation.
    """
    pool = MistralKeyPool(keys=["mstrl_single_record_key"])
    provider = MistralProvider(key_pool=pool, default_model="ministral-3b-2512")

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.is_closed = False

    async def mock_post(url, json=None, headers=None):
        req = httpx.Request("POST", url)
        return httpx.Response(
            200,
            request=req,
            json={
                "choices": [{"message": {"content": "Test completion"}}],
                "usage": {
                    "prompt_tokens": 50,
                    "completion_tokens": 100,
                    "total_tokens": 150,
                },
            },
        )

    mock_client.post = mock_post
    provider._client = mock_client

    result = await provider.generate_text("Hello world", model="ministral-3b-2512")
    assert result == "Test completion"

    key = pool.get_keys()[0]
    records = list(key._model_token_records.get("ministral-3b-2512", []))

    # Must contain exactly 1 entry of 150 tokens, NOT 2 entries of 150 (300 tokens)
    assert len(records) == 1, f"Expected exactly 1 token record, got {len(records)}: {records}"
    assert records[0][1] == 150, f"Expected 150 tokens in record, got {records[0][1]}"

    # TPM reported must be exactly 150
    tpm = key.get_sliding_tpm("ministral-3b-2512")
    assert tpm == 150, f"Expected sliding TPM of 150, got {tpm}"

    # Perform a second call returning 200 tokens
    async def mock_post_2(url, json=None, headers=None):
        req = httpx.Request("POST", url)
        return httpx.Response(
            200,
            request=req,
            json={
                "choices": [{"message": {"content": "Second completion"}}],
                "usage": {
                    "prompt_tokens": 80,
                    "completion_tokens": 120,
                    "total_tokens": 200,
                },
            },
        )

    mock_client.post = mock_post_2
    await provider.generate_text("Second call", model="ministral-3b-2512")

    records_2 = list(key._model_token_records.get("ministral-3b-2512", []))
    assert len(records_2) == 2, f"Expected exactly 2 token records after 2 calls, got {len(records_2)}"
    assert [tok for _, tok in records_2] == [150, 200]
    assert key.get_sliding_tpm("ministral-3b-2512") == 350

    await provider.close()


def test_clock_consistency_sync_context_preserves_records_and_cooldowns():
    """
    Verify that _get_loop_time() returns a monotonic timestamp consistent with
    time.monotonic() when called outside an active event loop (RuntimeError fallback),
    and that synchronous calls to to_dict(), get_sliding_tpm(), and is_available()
    do not erase active model cooldowns or wipe token records.
    """
    # 1. Direct clock fallback verification outside event loop
    sync_time = _get_loop_time()
    mono_time = time.monotonic()
    assert abs(sync_time - mono_time) < 0.2, (
        f"_get_loop_time() returned {sync_time}, expected value close to time.monotonic() {mono_time}"
    )

    # 2. State preservation across sync methods
    key = MistralKey("mstrl_sync_key")
    model = "ministral-3b-2512"

    # Set up model cooldown of 30 seconds
    key.mark_rate_limited(is_daily=False, retry_delay_sec=30.0, model=model)
    # Record token usage
    key.record_token_usage(model, 500)

    assert model in key._model_cooldowns
    assert len(key._model_token_records[model]) == 1

    # Calling to_dict() in sync context MUST NOT clear the active cooldown
    telemetry = key.to_dict()
    assert model in key._model_cooldowns, "to_dict() prematurely erased active model cooldown!"
    assert model in telemetry["model_cooldowns"], "to_dict() missed active model cooldown in telemetry!"
    assert model in telemetry["throttled_models"]

    # Calling get_sliding_tpm() in sync context MUST NOT wipe token records
    tpm = key.get_sliding_tpm(model)
    assert tpm == 500, f"Expected 500 TPM in sync context, got {tpm}"
    assert len(key._model_token_records[model]) == 1, "get_sliding_tpm() purged token records in sync context!"

    # Availability check in sync context
    assert key.is_available(model=model) is False
    assert key.is_available(model="codestral-2508") is True


@pytest.mark.asyncio
async def test_acquire_next_key_pass_2_excludes_daily_exhausted():
    """
    Verify that acquire_next_key Pass 2 skips keys with status DAILY_EXHAUSTED
    even if their cooldown duration would otherwise fit within the 15.0s window.
    """
    pool = MistralKeyPool(keys=["mstrl_exhausted_k1", "mstrl_busy_k2"])
    k1, k2 = pool.get_keys()
    model = "ministral-3b-2512"

    # k1 is DAILY_EXHAUSTED with cooldown 2.0s
    k1.mark_rate_limited(is_daily=True, retry_delay_sec=2.0)
    assert k1.status == KeyStatus.DAILY_EXHAUSTED

    # k2 is in a short model cooldown of 1.0s (ready in 1.0s, which is > 0.05s so Pass 1 skips it)
    k2.mark_rate_limited(is_daily=False, retry_delay_sec=1.0, model=model)
    assert k2.status == KeyStatus.ACTIVE

    # Acquire next key: Pass 1 has no key with wait <= 0.05s.
    # In Pass 2: k1 MUST be skipped because it is DAILY_EXHAUSTED.
    # k2 has wait ~1.0s <= 15.0s and status ACTIVE, so k2 must be selected.
    selected = await pool.acquire_next_key(model=model)
    assert selected is not None, "Expected k2 to be selected in Pass 2"
    assert selected.key == "mstrl_busy_k2", f"Expected k2, but got {selected.key} (DAILY_EXHAUSTED key was selected!)"

    # If k2 also becomes DAILY_EXHAUSTED:
    k2.mark_rate_limited(is_daily=True, retry_delay_sec=2.0)
    selected_none = await pool.acquire_next_key(model=model)
    assert selected_none is None, "Expected None when all candidate keys are DAILY_EXHAUSTED"

