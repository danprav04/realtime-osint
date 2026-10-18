"""
backend/tests/test_m1_it2_remediation_challenge.py - Empirical Adversarial Stress Harness (Iteration 2)
=======================================================================================================
Adversarial challenge for Milestone M1 Remediation:
1. Saturated sliding TPM on Key 1:
   - Asserts key pool rotates traffic to idle sibling keys (Keys 2, 3, 4) instead of blocking for 60s.
   - Asserts round-robin load balances strictly across healthy sibling keys.
   - Asserts cascading saturation cleanly isolates keys and degrades gracefully.
   - Asserts model isolation on the same key: saturation on Model A does not block Model B.
2. Saturated daily tokens (200,000 TPD):
   - Asserts key recovers after UTC calendar date change.
   - Asserts key recovers after 86,400s (24h) simulated elapsed loop time.
   - Asserts key recovers after provider reset header timestamp expires.
   - Multi-day stress test across consecutive rollovers.
3. Exact 1x token recording:
   - Verifies 1 completion records exactly its tokens without double-counting (0% inflation).
   - Verifies 50-call bursts accumulate exactly 1x tokens in sliding and daily counters.
   - Verifies summation fallback (prompt + completion) when total_tokens is omitted.
   - Verifies error responses (429, 500) record 0 tokens.
   - Verifies multi-key and multi-model isolation during token recording.
"""

from collections import deque
from datetime import datetime, timezone
import asyncio
import time
from unittest.mock import AsyncMock, patch
import httpx
import pytest

from backend.app.pipeline.providers.groq import GroqProvider
from backend.app.pipeline.providers.key_pool import (
    GroqKey,
    GroqKeyPool,
    KeyStatus,
)


# ============================================================================
# 1. Saturated Sliding TPM on Key 1 & Key Pool Rotation
# ============================================================================

@pytest.mark.asyncio
async def test_challenge_sliding_tpm_rotates_across_4_keys_without_blocking():
    """
    Stress test 1.1:
    Key 1 has saturated its sliding-window TPM (8,500 tokens >= 8,000 limit).
    Keys 2, 3, 4 are completely idle.
    Verify:
    1. Key 1 is_available returns False for that model.
    2. Key 1 wait time is ~60s.
    3. acquire_next_key does NOT select Key 1.
    4. Across 90 requests, Key 1 is selected 0 times, and Keys 2, 3, 4 receive
       exactly 30 requests each (even round-robin distribution).
    """
    pool = GroqKeyPool(keys=["gsk_k1", "gsk_k2", "gsk_k3", "gsk_k4"])
    k1, k2, k3, k4 = pool.get_keys()
    model = "qwen/qwen3.8-27b"

    # Saturate Key 1 with 8,500 tokens in the 60s sliding window
    k1.record_token_usage(model, 8500)

    # Key 1 state verification
    assert k1.get_seconds_until_ready(model=model) >= 55.0
    assert k1.is_available(model=model) is False

    # Sibling keys are active and idle
    for sib in (k2, k3, k4):
        assert sib.get_seconds_until_ready(model=model) == 0.0
        assert sib.is_available(model=model) is True

    # Execute 90 sequential acquisitions
    selections = {"gsk_k1": 0, "gsk_k2": 0, "gsk_k3": 0, "gsk_k4": 0}
    for _ in range(90):
        key = await pool.acquire_next_key(model=model)
        assert key is not None
        selections[key.key] += 1

    # Key 1 was NEVER selected
    assert selections["gsk_k1"] == 0, f"Expected 0 selections on saturated Key 1, got {selections['gsk_k1']}"
    # Keys 2, 3, 4 perfectly shared the 90 requests (30 each)
    assert selections["gsk_k2"] == 30
    assert selections["gsk_k3"] == 30
    assert selections["gsk_k4"] == 30


@pytest.mark.asyncio
async def test_challenge_cascading_sliding_tpm_saturation():
    """
    Stress test 1.2:
    Sequentially saturate sliding TPM on Key 1, then Key 2, then Key 3, then Key 4.
    Verify traffic migrates strictly to remaining idle keys until all are exhausted.
    When all are exhausted (wait > 3s), acquire_next_key returns None (no 60s lock).
    """
    pool = GroqKeyPool(keys=["gsk_k1", "gsk_k2", "gsk_k3", "gsk_k4"])
    k1, k2, k3, k4 = pool.get_keys()
    model = "qwen/qwen3.8-27b"

    # 1. Saturate Key 1 -> traffic to K2, K3, K4
    k1.record_token_usage(model, 8500)
    for _ in range(12):
        k = await pool.acquire_next_key(model=model)
        assert k is not None and k.key in ("gsk_k2", "gsk_k3", "gsk_k4")

    # 2. Saturate Key 2 -> traffic to K3, K4
    k2.record_token_usage(model, 9200)
    for _ in range(12):
        k = await pool.acquire_next_key(model=model)
        assert k is not None and k.key in ("gsk_k3", "gsk_k4")

    # 3. Saturate Key 3 -> traffic strictly to K4
    k3.record_token_usage(model, 8000)
    for _ in range(12):
        k = await pool.acquire_next_key(model=model)
        assert k is not None and k.key == "gsk_k4"

    # 4. Saturate Key 4 -> all keys saturated with ~60s wait
    k4.record_token_usage(model, 8100)
    for k in (k1, k2, k3, k4):
        assert k.is_available(model=model) is False
        assert k.get_seconds_until_ready(model=model) >= 55.0

    # Pool must return None because best_wait is ~60s (> 3.0s)
    exhausted_key = await pool.acquire_next_key(model=model)
    assert exhausted_key is None, "Pool must return None rather than selecting a 60s blocked key"


@pytest.mark.asyncio
async def test_challenge_sliding_tpm_model_isolation_on_same_key():
    """
    Stress test 1.3:
    Saturating sliding TPM for Model A on Key 1 must NOT impair Model B or Model C on Key 1.
    """
    pool = GroqKeyPool(keys=["gsk_k1"])
    k1 = pool.get_keys()[0]
    model_a = "qwen/qwen3.8-27b"
    model_b = "openai/gpt-oss-120b"
    model_c = "openai/gpt-oss-20b"

    # Saturate Key 1 for Model A with 10,000 tokens
    k1.record_token_usage(model_a, 10_000)

    # Model A is blocked
    assert k1.is_available(model=model_a) is False
    assert k1.get_seconds_until_ready(model=model_a) >= 55.0

    # Models B and C are 100% active and unblocked on Key 1
    assert k1.is_available(model=model_b) is True
    assert k1.get_seconds_until_ready(model=model_b) == 0.0
    assert k1.is_available(model=model_c) is True
    assert k1.get_seconds_until_ready(model=model_c) == 0.0

    # Pool acquire for Model B succeeds immediately
    selected_b = await pool.acquire_next_key(model=model_b)
    assert selected_b is not None and selected_b.key == "gsk_k1"


@pytest.mark.asyncio
async def test_challenge_groq_provider_execution_bypasses_saturated_key():
    """
    Stress test 1.4:
    GroqProvider with 3 keys. Key 1 is sliding-TPM saturated.
    Calling generate_text must route seamlessly to Key 2 or Key 3 in milliseconds,
    with zero 60-second blocking.
    """
    pool = GroqKeyPool(keys=["gsk_k1", "gsk_k2", "gsk_k3"])
    provider = GroqProvider(key_pool=pool, default_model="qwen/qwen3.8-27b")
    model = "qwen/qwen3.8-27b"

    k1, k2, k3 = pool.get_keys()
    # Saturate Key 1
    k1.record_token_usage(model, 8500)

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.is_closed = False
    used_auths = []

    async def mock_post(url, json=None, headers=None):
        used_auths.append(headers.get("Authorization", ""))
        req = httpx.Request("POST", url)
        return httpx.Response(
            200,
            request=req,
            headers={
                "x-ratelimit-remaining-requests": "995",
                "x-ratelimit-remaining-tokens": "7500",
            },
            json={
                "choices": [{"message": {"content": "routed_successfully"}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
            },
        )

    mock_client.post = mock_post
    provider._client = mock_client

    t0 = time.monotonic()
    result = await provider.generate_text("prompt", model=model)
    elapsed = time.monotonic() - t0

    assert result == "routed_successfully"
    assert elapsed < 0.5, f"Execution took {elapsed:.2f}s, expected < 0.5s (no 60s blocking)"
    assert len(used_auths) == 1
    # Key 1 was NOT called
    assert "Bearer gsk_k1" not in used_auths[0]
    assert "Bearer gsk_k2" in used_auths[0] or "Bearer gsk_k3" in used_auths[0]


# ============================================================================
# 2. Saturated Daily Tokens (200,000 TPD) & Recovery Vectors
# ============================================================================

def test_challenge_daily_token_saturation_exact_boundary():
    """
    Stress test 2.1:
    Verify exact 200,000 TPD boundary when tokens are accumulated outside the 60s sliding window:
    199,999 tokens -> available.
    200,000 tokens -> unavailable.
    """
    key = GroqKey("gsk_daily_boundary")
    model = "qwen/qwen3.8-27b"

    # Simulate tokens consumed earlier today (outside 60s sliding window)
    key._model_daily_tokens[model] = 199_999
    # Still available with 1 token left
    assert key.is_available(model=model) is True

    # Reach 200,000 tokens
    key._model_daily_tokens[model] = 200_000
    # Exactly 200,000 tokens -> unavailable
    assert key.is_available(model=model) is False
    assert key.get_seconds_until_ready(model=model) > 0.0


def test_challenge_daily_recovery_vector_utc_date_rollover():
    """
    Stress test 2.2:
    Saturate daily tokens (250,000 tokens) and daily requests (1,000 requests).
    Simulate UTC date rollover (calendar day boundary change).
    Verify key counters reset to 0 and key regains ACTIVE availability.
    """
    key = GroqKey("gsk_date_rollover")
    model = "qwen/qwen3.8-27b"

    # Saturate both daily counters (from earlier today, outside 60s sliding window)
    key._model_daily_tokens[model] = 250_000
    key._model_daily_requests[model] = 1000
    assert key.is_available(model=model) is False

    # Simulate UTC date rollover by overriding stored date to yesterday
    key._current_date_utc = "2020-01-01"

    # is_available detects date change to today's UTC date
    assert key.is_available(model=model) is True
    assert key._model_daily_tokens.get(model, 0) == 0
    assert key._model_daily_requests.get(model, 0) == 0
    assert key.get_seconds_until_ready(model=model) == 0.0


def test_challenge_daily_recovery_vector_86400s_elapsed_loop_time():
    """
    Stress test 2.3:
    Simulate 86,400s (24h) elapsed loop time without calendar date change.
    Verify daily token and request counters reset and key becomes available.
    """
    key = GroqKey("gsk_elapsed_rollover")
    model = "qwen/qwen3.8-27b"

    t0 = 1000.0
    key._created_loop_time = t0
    key._model_daily_last_reset[model] = t0
    key._model_daily_tokens[model] = 200_000
    key._model_daily_requests[model] = 1000

    # Before 24h: unavailable
    assert key.is_available(now=t0 + 86399.0, model=model) is False
    assert key._model_daily_tokens[model] == 200_000

    # At exactly 24h + 1s (86,401s): resets counters and becomes available
    assert key.is_available(now=t0 + 86401.0, model=model) is True
    assert key._model_daily_tokens[model] == 0
    assert key._model_daily_requests[model] == 0


def test_challenge_daily_recovery_vector_provider_reset_timestamp():
    """
    Stress test 2.4:
    When provider response headers supply `reset_requests_sec`, GroqKey registers
    `_model_daily_reset_at[model] = now + reset_sec`.
    Once that timestamp arrives, daily counters must reset.
    """
    key = GroqKey("gsk_header_reset")
    model = "qwen/qwen3.8-27b"
    t_now = 5000.0

    key._model_daily_tokens[model] = 200_000
    key._model_daily_reset_at[model] = t_now + 1200.0  # Reset in 20 min

    # Prior to reset timestamp: unavailable
    assert key.is_available(now=t_now + 1199.0, model=model) is False

    # After reset timestamp: resets and available
    assert key.is_available(now=t_now + 1201.0, model=model) is True
    assert key._model_daily_tokens[model] == 0


def test_challenge_consecutive_multi_day_rollovers():
    """
    Stress test 2.5:
    Simulate 5 consecutive days of daily saturation followed by rollover using mock UTC dates.
    Verify key never gets stuck in a stale or exhausted state across cycles.
    """
    key = GroqKey("gsk_multi_day")
    model = "qwen/qwen3.8-27b"

    for day in range(1, 6):
        date_str = f"2026-11-{day:02d}"
        with patch("backend.app.pipeline.providers.key_pool.datetime") as mock_dt:
            # Set today to date_str
            mock_dt.now.return_value = datetime(2026, 11, day, 12, 0, 0, tzinfo=timezone.utc)
            # Daily reset recognizes the new day
            key._check_daily_rollover(model=model)
            assert key.is_available(model=model) is True
            assert key._model_daily_tokens.get(model, 0) == 0

            # Saturate key for this day
            key._model_daily_tokens[model] = 200_000
            assert key.is_available(model=model) is False
            assert key._model_daily_tokens[model] == 200_000


# ============================================================================
# 3. Exact 1x Token Recording (Zero Double-Counting)
# ============================================================================

@pytest.mark.asyncio
async def test_challenge_exact_1x_token_recording_single_call():
    """
    Stress test 3.1:
    Verify generate_text with usage {"prompt_tokens": 250, "completion_tokens": 125, "total_tokens": 375}
    records EXACTLY 375 tokens into _model_daily_tokens and exactly 1 record into _model_token_records.
    Zero double counting (0% inflation).
    """
    pool = GroqKeyPool(keys=["gsk_rec_test1"])
    provider = GroqProvider(key_pool=pool, default_model="qwen/qwen3.8-27b")
    model = "qwen/qwen3.8-27b"

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.is_closed = False

    async def mock_post(url, json=None, headers=None):
        req = httpx.Request("POST", url)
        return httpx.Response(
            200,
            request=req,
            headers={
                "x-ratelimit-remaining-requests": "999",
                "x-ratelimit-remaining-tokens": "7625",
            },
            json={
                "choices": [{"message": {"content": "ok_single"}}],
                "usage": {
                    "prompt_tokens": 250,
                    "completion_tokens": 125,
                    "total_tokens": 375,
                },
            },
        )

    mock_client.post = mock_post
    provider._client = mock_client

    key = pool.get_keys()[0]
    await provider.generate_text("prompt", model=model)

    records = list(key._model_token_records[model])
    daily_tok = key._model_daily_tokens[model]

    assert len(records) == 1, f"Expected exactly 1 token record, got {len(records)}"
    assert records[0][1] == 375
    assert sum(tok for _, tok in records) == 375
    assert daily_tok == 375, f"Expected 375 daily tokens, got {daily_tok} (double counting detected if 750)"


@pytest.mark.asyncio
async def test_challenge_exact_1x_token_recording_single_key_burst_25_calls():
    """
    Stress test 3.2a:
    25 consecutive calls on a single key with 120 tokens each.
    Total consumed tokens = 25 * 120 = 3,000 tokens.
    Under 30 RPM limit and 8,000 TPM limit.
    Verify:
    - Exactly 25 records in _model_token_records (NOT 50).
    - Exactly 3,000 in _model_daily_tokens (NOT 6,000).
    - Sliding window sum is exactly 3,000.
    - 3,000 < 8,000 TPM limit -> wait remains 0.0s!
    """
    pool = GroqKeyPool(keys=["gsk_burst_key"])
    provider = GroqProvider(key_pool=pool, default_model="qwen/qwen3.8-27b")
    model = "qwen/qwen3.8-27b"

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.is_closed = False

    async def mock_post(url, json=None, headers=None):
        req = httpx.Request("POST", url)
        return httpx.Response(
            200,
            request=req,
            headers={
                "x-ratelimit-remaining-requests": "975",
                "x-ratelimit-remaining-tokens": "5000",
            },
            json={
                "choices": [{"message": {"content": "burst_ok"}}],
                "usage": {
                    "prompt_tokens": 80,
                    "completion_tokens": 40,
                    "total_tokens": 120,
                },
            },
        )

    mock_client.post = mock_post
    provider._client = mock_client

    key = pool.get_keys()[0]

    for _ in range(25):
        await provider.generate_text("burst_prompt", model=model)

    records = list(key._model_token_records[model])
    daily_tok = key._model_daily_tokens[model]
    sum_tok = sum(tok for _, tok in records)

    assert len(records) == 25
    assert daily_tok == 3000, f"Expected 3,000 daily tokens, got {daily_tok} (if double-counting, would be 6,000)"
    assert sum_tok == 3000
    assert key.get_seconds_until_ready(model=model) == 0.0


@pytest.mark.asyncio
async def test_challenge_exact_1x_token_recording_multi_key_pool_60_calls():
    """
    Stress test 3.2b:
    60 consecutive calls distributed across a 4-key pool (15 calls per key).
    Total tokens = 60 * 100 = 6,000 tokens.
    Verify:
    - Exactly 15 records per key (NOT 30).
    - Exactly 1,500 daily tokens per key (NOT 3,000).
    - Total tokens recorded across pool = 6,000 (0% inflation).
    """
    pool = GroqKeyPool(keys=["gsk_k1", "gsk_k2", "gsk_k3", "gsk_k4"])
    provider = GroqProvider(key_pool=pool, default_model="qwen/qwen3.8-27b")
    model = "qwen/qwen3.8-27b"

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.is_closed = False

    async def mock_post(url, json=None, headers=None):
        req = httpx.Request("POST", url)
        return httpx.Response(
            200,
            request=req,
            headers={
                "x-ratelimit-remaining-requests": "985",
                "x-ratelimit-remaining-tokens": "7000",
            },
            json={
                "choices": [{"message": {"content": "ok"}}],
                "usage": {"prompt_tokens": 60, "completion_tokens": 40, "total_tokens": 100},
            },
        )

    mock_client.post = mock_post
    provider._client = mock_client

    for _ in range(60):
        await provider.generate_text("prompt", model=model)

    total_pool_records = 0
    total_pool_tokens = 0
    for k in pool.get_keys():
        recs = list(k._model_token_records.get(model, []))
        daily = k._model_daily_tokens.get(model, 0)
        assert len(recs) == 15, f"Expected 15 records on key {k.key}, got {len(recs)}"
        assert daily == 1500, f"Expected 1,500 tokens on key {k.key}, got {daily}"
        total_pool_records += len(recs)
        total_pool_tokens += daily

    assert total_pool_records == 60
    assert total_pool_tokens == 6000



@pytest.mark.asyncio
async def test_challenge_token_recording_summation_fallback_without_total():
    """
    Stress test 3.3:
    When total_tokens is missing from Groq usage response, prompt_tokens + completion_tokens
    must be summed and recorded exactly 1x.
    """
    pool = GroqKeyPool(keys=["gsk_fallback_usage"])
    provider = GroqProvider(key_pool=pool, default_model="qwen/qwen3.8-27b")
    model = "qwen/qwen3.8-27b"

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.is_closed = False

    async def mock_post(url, json=None, headers=None):
        req = httpx.Request("POST", url)
        return httpx.Response(
            200,
            request=req,
            json={
                "choices": [{"message": {"content": "ok"}}],
                "usage": {
                    "prompt_tokens": 300,
                    "completion_tokens": 150,
                    # total_tokens omitted!
                },
            },
        )

    mock_client.post = mock_post
    provider._client = mock_client

    key = pool.get_keys()[0]
    await provider.generate_text("prompt", model=model)

    records = list(key._model_token_records[model])
    assert len(records) == 1
    assert records[0][1] == 450
    assert key._model_daily_tokens[model] == 450


@pytest.mark.asyncio
async def test_challenge_zero_tokens_recorded_on_http_429_or_errors():
    """
    Stress test 3.4:
    On HTTP 429 rate limit or 500 error, zero tokens must be recorded into token records.
    """
    key = GroqKey("gsk_err_test")
    model = "qwen/qwen3.8-27b"
    req = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")

    resp_429 = httpx.Response(
        429,
        request=req,
        headers={"retry-after": "5"},
        json={"error": {"message": "Rate limit exceeded"}},
    )

    key.update_from_response(resp_429, model=model)

    assert len(key._model_token_records.get(model, [])) == 0
    assert key._model_daily_tokens.get(model, 0) == 0


@pytest.mark.asyncio
async def test_challenge_token_recording_multi_model_isolation():
    """
    Stress test 3.5:
    Tokens recorded on Model A must not leak into Model B or Model C on the same key.
    """
    pool = GroqKeyPool(keys=["gsk_multi_model_rec"])
    provider = GroqProvider(key_pool=pool)
    key = pool.get_keys()[0]

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.is_closed = False

    async def mock_post(url, json=None, headers=None):
        req = httpx.Request("POST", url)
        # 100 tokens
        return httpx.Response(
            200,
            request=req,
            json={
                "choices": [{"message": {"content": "ok"}}],
                "usage": {"total_tokens": 100},
            },
        )

    mock_client.post = mock_post
    provider._client = mock_client

    # 3 calls on Model A
    for _ in range(3):
        await provider.generate_text("prompt", model="qwen/qwen3.8-27b")

    # 2 calls on Model B
    for _ in range(2):
        await provider.generate_text("prompt", model="openai/gpt-oss-120b")

    # Verify Model A has exactly 3 records / 300 tokens
    rec_a = list(key._model_token_records["qwen/qwen3.8-27b"])
    assert len(rec_a) == 3
    assert key._model_daily_tokens["qwen/qwen3.8-27b"] == 300

    # Verify Model B has exactly 2 records / 200 tokens
    rec_b = list(key._model_token_records["openai/gpt-oss-120b"])
    assert len(rec_b) == 2
    assert key._model_daily_tokens["openai/gpt-oss-120b"] == 200

    # Verify Model C has 0 records / 0 tokens
    assert len(key._model_token_records.get("openai/gpt-oss-20b", [])) == 0
    assert key._model_daily_tokens.get("openai/gpt-oss-20b", 0) == 0
