"""
backend/tests/test_m1_groq_adversarial.py - Empirical Adversarial Stress Test Suite
==================================================================================
Adversarially challenges Milestone M1:
1. Short minute cooldown retry sleep (best_wait <= 12.0s) boundary conditions & multi-key behavior.
2. All keys unauthorized (401/403) behavior and immediate PermissionError.
3. 24h TPD daily classification under mock responses with remaining_requests > 0.
4. Sliding-window TPM (8,000 TPM) and RPM (30 RPM) boundary enforcement & isolation.
5. Token recording mechanics between update_from_response and generate_text.
"""

from collections import deque
import asyncio
import time
from unittest.mock import AsyncMock, patch
import httpx
import pytest

from backend.app.pipeline.providers.base import classify_rate_limit_429
from backend.app.pipeline.providers.groq import GroqProvider
from backend.app.pipeline.providers.key_pool import (
    GroqKey,
    GroqKeyPool,
    KeyStatus,
)


# ============================================================================
# 1. Short minute cooldown retry sleep (best_wait <= 12.0s) behavior
# ============================================================================

@pytest.mark.asyncio
async def test_challenge_1_1_short_minute_cooldown_sleep_and_recover():
    """Verify provider sleeps and retries rather than crashing when best_wait <= 12.0s."""
    pool = GroqKeyPool(keys=["gsk_k1"])
    provider = GroqProvider(key_pool=pool, default_model="qwen/qwen3.8-27b")

    # Set a short 0.20s cooldown on Key 1
    key1 = pool.get_keys()[0]
    key1.mark_rate_limited(
        is_daily=False,
        retry_delay_sec=0.20,
        reason="Transient 429 short pause",
        model="qwen/qwen3.8-27b",
    )

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.is_closed = False

    call_count = 0

    async def mock_post(url, json=None, headers=None):
        nonlocal call_count
        call_count += 1
        req = httpx.Request("POST", url)
        return httpx.Response(
            200,
            request=req,
            json={"choices": [{"message": {"content": "ok_recovered"}}]},
        )

    mock_client.post = mock_post
    provider._client = mock_client

    t0 = time.monotonic()
    result = await provider.generate_text("hello", model="qwen/qwen3.8-27b")
    elapsed = time.monotonic() - t0

    assert result == "ok_recovered"
    assert call_count == 1
    # Slept the cooldown (~0.20s + 0.08s jitter margin)
    assert elapsed >= 0.15
    assert elapsed < 2.0


@pytest.mark.asyncio
async def test_challenge_1_2_multi_key_selects_minimum_wait_within_12s():
    """Pool of 4 keys with varying cooldowns: verify closest key (<= 12.0s) is selected and recovered."""
    pool = GroqKeyPool(keys=["gsk_k1", "gsk_k2", "gsk_k3", "gsk_k4"])
    provider = GroqProvider(key_pool=pool, default_model="qwen/qwen3.8-27b")

    k1, k2, k3, k4 = pool.get_keys()
    # K1: 50s cooldown
    k1.mark_rate_limited(is_daily=False, retry_delay_sec=50.0, model="qwen/qwen3.8-27b")
    # K2: 60s cooldown
    k2.mark_rate_limited(is_daily=False, retry_delay_sec=60.0, model="qwen/qwen3.8-27b")
    # K3: 20s cooldown
    k3.mark_rate_limited(is_daily=False, retry_delay_sec=20.0, model="qwen/qwen3.8-27b")
    # K4: 0.15s cooldown (<= 12.0s)
    k4.mark_rate_limited(is_daily=False, retry_delay_sec=0.15, model="qwen/qwen3.8-27b")

    # Best wait should be K4
    best_k, best_w = pool.get_best_wait_time(model="qwen/qwen3.8-27b")
    assert best_k is not None
    assert best_k.key == "gsk_k4"
    assert best_w <= 0.20

    used_keys = []
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.is_closed = False

    async def mock_post(url, json=None, headers=None):
        auth = headers.get("Authorization", "")
        used_keys.append(auth)
        req = httpx.Request("POST", url)
        return httpx.Response(
            200,
            request=req,
            json={"choices": [{"message": {"content": "k4_success"}}]},
        )

    mock_client.post = mock_post
    provider._client = mock_client

    result = await provider.generate_text("test", model="qwen/qwen3.8-27b")
    assert result == "k4_success"
    assert len(used_keys) == 1
    assert "gsk_k4" in used_keys[0]


@pytest.mark.asyncio
async def test_challenge_1_3_boundary_wait_greater_than_12s_raises_429():
    """Verify that when best_wait > 12.0s (e.g. 12.5s), provider does NOT sleep and raises RateLimitError."""
    pool = GroqKeyPool(keys=["gsk_k1"])
    provider = GroqProvider(key_pool=pool, default_model="qwen/qwen3.8-27b")

    k1 = pool.get_keys()[0]
    k1.mark_rate_limited(is_daily=False, retry_delay_sec=12.5, reason="TPM limit reset", model="qwen/qwen3.8-27b")

    best_k, best_w = pool.get_best_wait_time(model="qwen/qwen3.8-27b")
    assert best_w > 12.0

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.is_closed = False
    provider._client = mock_client

    t0 = time.monotonic()
    with pytest.raises(RuntimeError) as exc_info:
        await provider.generate_text("test", model="qwen/qwen3.8-27b")
    elapsed = time.monotonic() - t0

    # Must fail immediately without sleeping 12+ seconds
    assert elapsed < 1.0
    assert "Groq RateLimitError: 429 Rate limit reached" in str(exc_info.value)
    assert mock_client.post.call_count == 0


@pytest.mark.asyncio
async def test_challenge_1_4_exhaustion_after_max_attempts_terminates_cleanly():
    """Verify that if all keys repeatedly return 429 and sleep, it terminates at max_attempts without hanging."""
    pool = GroqKeyPool(keys=["gsk_k1"])
    provider = GroqProvider(key_pool=pool, default_model="qwen/qwen3.8-27b", max_retries=2)

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.is_closed = False

    call_count = 0

    async def mock_post(url, json=None, headers=None):
        nonlocal call_count
        call_count += 1
        req = httpx.Request("POST", url)
        # Always return 429 with 0.05s retry-after
        return httpx.Response(
            429,
            request=req,
            headers={"retry-after": "0.05"},
            json={"error": {"message": "Rate limit reached"}},
        )

    mock_client.post = mock_post
    provider._client = mock_client

    with pytest.raises(RuntimeError) as exc_info:
        await provider.generate_text("test", model="qwen/qwen3.8-27b")

    assert "429 Rate limit reached" in str(exc_info.value)
    assert call_count >= 1


# ============================================================================
# 2. All keys unauthorized (401) behavior
# ============================================================================

@pytest.mark.asyncio
async def test_challenge_2_1_all_keys_unauthorized_raises_immediate_permission_error():
    """Verify that when all keys in pool return 401, provider deactivates all keys and raises PermissionError."""
    pool = GroqKeyPool(keys=["gsk_invalid_1", "gsk_invalid_2"])
    provider = GroqProvider(key_pool=pool, default_model="qwen/qwen3.8-27b")

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.is_closed = False

    calls = []

    async def mock_post(url, json=None, headers=None):
        auth = headers.get("Authorization", "")
        calls.append(auth)
        req = httpx.Request("POST", url)
        return httpx.Response(
            401,
            request=req,
            json={"error": {"message": "Invalid API Key", "type": "invalid_request_error"}},
        )

    mock_client.post = mock_post
    provider._client = mock_client

    with pytest.raises(PermissionError) as exc_info:
        await provider.generate_text("test", model="qwen/qwen3.8-27b")

    assert "Groq API key is invalid or unauthorized" in str(exc_info.value)
    # Both keys were tried once and deactivated
    assert len(calls) == 2
    assert all(k.status == KeyStatus.UNAUTHORIZED for k in pool.get_keys())


@pytest.mark.asyncio
async def test_challenge_2_2_subsequent_request_fails_immediately_without_network_call():
    """Verify that subsequent requests after all keys are UNAUTHORIZED raise PermissionError with 0 network calls."""
    pool = GroqKeyPool(keys=["gsk_inv1", "gsk_inv2"])
    for k in pool.get_keys():
        k.mark_unauthorized()

    provider = GroqProvider(key_pool=pool, default_model="qwen/qwen3.8-27b")
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.is_closed = False
    provider._client = mock_client

    t0 = time.monotonic()
    with pytest.raises(PermissionError) as exc_info:
        await provider.generate_text("test", model="qwen/qwen3.8-27b")
    elapsed = time.monotonic() - t0

    assert "Groq API key is invalid or unauthorized" in str(exc_info.value)
    assert mock_client.post.call_count == 0
    assert elapsed < 0.1


@pytest.mark.asyncio
async def test_challenge_2_3_mixed_unauthorized_and_valid_keys():
    """Verify that unauthorized keys are quarantined while valid keys continue to handle traffic."""
    pool = GroqKeyPool(keys=["gsk_bad1", "gsk_bad2", "gsk_good1", "gsk_good2"])
    provider = GroqProvider(key_pool=pool, default_model="qwen/qwen3.8-27b")

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.is_closed = False

    async def mock_post(url, json=None, headers=None):
        auth = headers.get("Authorization", "")
        req = httpx.Request("POST", url)
        if "bad" in auth:
            return httpx.Response(401, request=req, json={"error": {"message": "Invalid key"}})
        return httpx.Response(
            200,
            request=req,
            json={"choices": [{"message": {"content": "ok_from_good"}}]},
        )

    mock_client.post = mock_post
    provider._client = mock_client

    # First request should deactivate bad1 and bad2 and succeed on good1
    res1 = await provider.generate_text("req1", model="qwen/qwen3.8-27b")
    assert res1 == "ok_from_good"

    # Confirm bad keys are UNAUTHORIZED
    k_bad1, k_bad2, k_good1, k_good2 = pool.get_keys()
    assert k_bad1.status == KeyStatus.UNAUTHORIZED
    assert k_bad2.status == KeyStatus.UNAUTHORIZED
    assert k_good1.status == KeyStatus.ACTIVE
    assert k_good2.status == KeyStatus.ACTIVE

    # Subsequent requests only rotate across good1 and good2
    res2 = await provider.generate_text("req2", model="qwen/qwen3.8-27b")
    assert res2 == "ok_from_good"
    assert k_bad1.total_requests == 1
    assert k_bad2.total_requests == 1


# ============================================================================
# 3. 24h TPD daily classification under mock responses with remaining_requests > 0
# ============================================================================

def test_challenge_3_1_tpd_classification_with_positive_remaining_requests():
    """Verify 429 for TPD exhaustion is classified as DAILY even with 990 remaining requests and short retry-after."""
    key = GroqKey("gsk_tpd_test")
    req = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    resp_429 = httpx.Response(
        429,
        request=req,
        headers={
            "retry-after": "45",  # Under 90s, but message is TPD!
            "x-ratelimit-remaining-requests": "990",
            "x-ratelimit-remaining-tokens": "0",
            "x-ratelimit-limit-requests": "1000",
            "x-ratelimit-limit-tokens": "8000",
        },
        json={
            "error": {
                "message": (
                    "Rate limit reached for model `qwen/qwen3.8-27b` on tokens per day (TPD): "
                    "Limit 200000, Used 200000. Please try again in 45s."
                )
            }
        },
    )

    key.update_from_response(resp_429, model="qwen/qwen3.8-27b")

    # MUST be classified as daily quota exhaustion
    assert key.daily_quota_exhaustions == 1
    assert key.minute_rate_limits == 0
    assert "daily tokens exhausted" in key._model_cooldown_reasons.get("qwen/qwen3.8-27b", "")
    assert key.is_available(model="qwen/qwen3.8-27b") is False
    # Key status remains active for other models
    assert key.status == KeyStatus.ACTIVE
    assert key.is_available(model="openai/gpt-oss-120b") is True


def test_challenge_3_2_tpd_classification_without_retry_after_header():
    """Verify TPD exhaustion without retry-after header falls back to daily 60s minimum."""
    key = GroqKey("gsk_tpd_no_header")
    req = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    resp_429 = httpx.Response(
        429,
        request=req,
        headers={
            "x-ratelimit-remaining-requests": "500",
            "x-ratelimit-remaining-tokens": "0",
        },
        json={
            "error": {
                "message": "Rate limit reached on tokens per day (TPD) for org_abc."
            }
        },
    )

    key.update_from_response(resp_429, model="qwen/qwen3.8-27b")

    assert key.daily_quota_exhaustions == 1
    assert key.minute_rate_limits == 0
    cd = key.get_model_cooldown_remaining("qwen/qwen3.8-27b")
    # Minimum 60s for daily
    assert cd >= 59.0


@pytest.mark.parametrize("error_phrase", [
    "tokens per day",
    "tokens/day",
    "(tpd)",
    "(TPD)",
    "on tokens per day (TPD): Limit 200000",
])
def test_challenge_3_3_tpd_phrasing_variants(error_phrase: str):
    """Verify standard Groq phrasing variants of TPD are classified as daily exhaustion."""
    key = GroqKey("gsk_variant_test")
    req = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    resp = httpx.Response(
        429,
        request=req,
        headers={"retry-after": "30", "x-ratelimit-remaining-requests": "999"},
        json={"error": {"message": f"Rate limit reached on {error_phrase} limit."}},
    )
    key.update_from_response(resp, model="qwen/qwen3.8-27b")
    assert key.daily_quota_exhaustions == 1
    assert key.minute_rate_limits == 0


def test_challenge_3_4_contrast_minute_tpm_limit_not_classified_as_daily():
    """Verify that minute TPM limit with positive remaining requests is classified as MINUTE, NOT daily."""
    key = GroqKey("gsk_minute_contrast")
    req = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    resp = httpx.Response(
        429,
        request=req,
        headers={
            "retry-after": "12",
            "x-ratelimit-remaining-requests": "990",
            "x-ratelimit-remaining-tokens": "0",
            "x-ratelimit-reset-tokens": "12.0s",
        },
        json={"error": {"message": "Rate limit reached for model on tokens per minute (TPM)"}},
    )
    key.update_from_response(resp, model="qwen/qwen3.8-27b")
    assert key.daily_quota_exhaustions == 0
    assert key.minute_rate_limits == 1


# ============================================================================
# 4. Sliding-window TPM (8,000 TPM) and RPM (30 RPM) boundary enforcement
# ============================================================================

@pytest.mark.asyncio
async def test_challenge_4_1_rpm_boundary_enforcement():
    """Verify that exceeding 30 RPM triggers positive wait in get_seconds_until_ready and sliding window pacing."""
    key = GroqKey("gsk_rpm_test")
    model = "qwen/qwen3.8-27b"

    now = 1000.0
    # Simulate 30 requests recorded in the last 60 seconds (1 second apart: 970.0 to 999.0)
    d = key._model_request_timestamps.setdefault(model, deque())
    for i in range(30):
        t = now - (30 - i)
        d.append(t)

    # At 30 requests in the window, get_seconds_until_ready must report wait time until earliest expires
    wait = key.get_seconds_until_ready(now=now, model=model)
    # Earliest is 970.0; expires at 970.0 + 60.2 = 1030.2; wait = 1030.2 - 1000 = 30.2s
    assert 29.0 <= wait <= 31.0

    # Advance time past the earliest timestamp (now = 1031.0)
    wait_after = key.get_seconds_until_ready(now=1031.0, model=model)
    # Now only 29 active timestamps, wait should be 0.0
    assert wait_after == 0.0


@pytest.mark.asyncio
async def test_challenge_4_2_tpm_boundary_enforcement_and_window_expiry():
    """Verify that exceeding 8,000 TPM triggers pacing wait and clears once window expires."""
    key = GroqKey("gsk_tpm_test")
    model = "qwen/qwen3.8-27b"

    now = 2000.0
    # Record 7,500 tokens at t = 1980.0
    d = key._model_token_records.setdefault(model, deque())
    d.append((1980.0, 7500))

    # Below 8,000 tokens: wait must be 0
    assert key.get_seconds_until_ready(now=now, model=model) == 0.0

    # Add 600 tokens at t = 1995.0 -> total 8,100 tokens (>= 8,000)
    d.append((1995.0, 600))

    # Now total = 8,100 tokens: wait until first batch expires (1980 + 60.2 = 2040.2 -> wait ~40.2s)
    wait = key.get_seconds_until_ready(now=now, model=model)
    assert 39.0 <= wait <= 41.0

    # Advance time to 2041.0: first batch of 7,500 tokens expired, leaving only 600 tokens
    wait_after = key.get_seconds_until_ready(now=2041.0, model=model)
    assert wait_after == 0.0


@pytest.mark.asyncio
async def test_challenge_4_3_tpm_rpm_isolation_per_key_model_bucket():
    """Verify that saturating 8,000 TPM or 30 RPM on (Key 1, Model A) leaves other buckets unaffected."""
    key1 = GroqKey("gsk_bucket1")
    key2 = GroqKey("gsk_bucket2")
    m_a = "qwen/qwen3.8-27b"
    m_b = "openai/gpt-oss-120b"

    now = 3000.0
    # Saturate Key 1 for Model A with 9,000 tokens
    d1 = key1._model_token_records.setdefault(m_a, deque())
    d1.append((now - 10, 9000))

    # (Key 1, Model A) has wait
    assert key1.get_seconds_until_ready(now=now, model=m_a) > 0.0

    # (Key 1, Model B) has 0 wait!
    assert key1.get_seconds_until_ready(now=now, model=m_b) == 0.0

    # (Key 2, Model A) has 0 wait!
    assert key2.get_seconds_until_ready(now=now, model=m_a) == 0.0


# ============================================================================
# 5. Token Recording Mechanics: Single vs Double Counting Audit
# ============================================================================

@pytest.mark.asyncio
async def test_challenge_5_1_token_recording_audit():
    """
    Empirical audit: Verify how many tokens are recorded in GroqKey when generate_text executes.
    Reveals whether update_from_response AND generate_text both record tokens.
    """
    pool = GroqKeyPool(keys=["gsk_audit_key"])
    provider = GroqProvider(key_pool=pool, default_model="qwen/qwen3.8-27b")

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.is_closed = False

    async def mock_post(url, json=None, headers=None):
        req = httpx.Request("POST", url)
        return httpx.Response(
            200,
            request=req,
            headers={
                "x-ratelimit-remaining-requests": "999",
                "x-ratelimit-remaining-tokens": "7000",
            },
            json={
                "choices": [{"message": {"content": "ok"}}],
                "usage": {
                    "prompt_tokens": 400,
                    "completion_tokens": 100,
                    "total_tokens": 500,
                },
            },
        )

    mock_client.post = mock_post
    provider._client = mock_client

    key = pool.get_keys()[0]
    # Execute generate_text
    await provider.generate_text("hello", model="qwen/qwen3.8-27b")

    daily_tokens = key._model_daily_tokens.get("qwen/qwen3.8-27b", 0)
    token_records = list(key._model_token_records.get("qwen/qwen3.8-27b", []))
    sum_records = sum(tok for _, tok in token_records)

    # In remediated implementation, only update_from_response calls record_token_usage
    # Resulting in exactly 1 record of 500 tokens = 500 total recorded tokens (0% double-counting).
    assert len(token_records) == 1
    assert daily_tokens == 500
    assert sum_records == 500


@pytest.mark.asyncio
async def test_challenge_5_2_double_counting_impact_on_tpm_throttling():
    """
    Empirical test: Demonstrate that without double-counting, 4,500 tokens does NOT cause false TPM throttling.
    A single request with total_tokens=4,500 is within the 8,000 TPM limit.
    With exact 1x recording, get_seconds_until_ready reports 0.0s wait.
    """
    pool = GroqKeyPool(keys=["gsk_audit_key2"])
    provider = GroqProvider(key_pool=pool, default_model="qwen/qwen3.8-27b")

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.is_closed = False

    async def mock_post(url, json=None, headers=None):
        req = httpx.Request("POST", url)
        return httpx.Response(
            200,
            request=req,
            headers={
                "x-ratelimit-remaining-requests": "999",
                "x-ratelimit-remaining-tokens": "3500",
            },
            json={
                "choices": [{"message": {"content": "ok"}}],
                "usage": {
                    "prompt_tokens": 3500,
                    "completion_tokens": 1000,
                    "total_tokens": 4500,
                },
            },
        )

    mock_client.post = mock_post
    provider._client = mock_client

    key = pool.get_keys()[0]
    await provider.generate_text("hello", model="qwen/qwen3.8-27b")

    # With exactly 4,500 tokens recorded (within 8,000 TPM limit), get_seconds_until_ready must report 0.0s wait
    wait = key.get_seconds_until_ready(model="qwen/qwen3.8-27b")
    assert wait == 0.0, f"Expected key to be ready (wait=0.0s), got wait={wait}"


@pytest.mark.asyncio
async def test_challenge_5_3_is_available_ignores_sliding_tpm_preventing_pool_rotation():
    """
    Empirical test demonstrating remediation:
    Key 1 has saturated its sliding-window TPM (8,500 tokens >= 8,000 TPM limit).
    Key 2 is completely idle (0 tokens).
    GroqKey.is_available() checks sliding window TPM/RPM and returns False when wait > 3s.
    As a result, GroqKeyPool.acquire_next_key() rotates to Key 2,
    preventing any 60s blocking sleep while Key 2 is idle.
    """
    pool = GroqKeyPool(keys=["gsk_saturated_key", "gsk_idle_key"])
    k1, k2 = pool.get_keys()
    model = "qwen/qwen3.8-27b"

    # Saturate Key 1 sliding-window TPM with 8,500 tokens
    k1.record_token_usage(model, 8500)

    # Key 1 wait is ~60s
    wait_k1 = k1.get_seconds_until_ready(model=model)
    assert wait_k1 >= 55.0

    # Key 2 wait is 0s
    wait_k2 = k2.get_seconds_until_ready(model=model)
    assert wait_k2 == 0.0

    # Key 1 is_available() must be False when wait > 3s
    k1_is_avail = k1.is_available(model=model)
    assert k1_is_avail is False, "Remediation verified: is_available() returns False on 8,500 tokens"

    # Pool round-robin skips saturated Key 1 and selects idle Key 2
    selected = await pool.acquire_next_key(model=model)
    assert selected is not None
    assert selected.key == "gsk_idle_key", (
        "Remediation verified: acquire_next_key rotated to idle sibling key!"
    )



# ============================================================================
# 6. High RPD Allowance Model (14,400 RPD) Stress Testing (Requirement 1)
# ============================================================================

def test_challenge_6_1_high_rpd_allowance_permits_requests_over_1000():
    """
    Verify high RPD models (e.g. llama-3.1-8b-instant with 14,400 RPD limit)
    permit requests > 1000 without false lockout.
    """
    key = GroqKey("gsk_high_rpd_key")
    model = "llama-3.1-8b-instant"

    req = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    # Simulate response from Groq returning 14,400 limit and 13,399 remaining (1001 requests used)
    resp = httpx.Response(
        200,
        request=req,
        headers={
            "x-ratelimit-limit-requests": "14400",
            "x-ratelimit-remaining-requests": "13399",
            "x-ratelimit-limit-tokens": "500000",
            "x-ratelimit-remaining-tokens": "495000",
        },
        json={"choices": [{"message": {"content": "ok"}}]},
    )
    key.update_from_response(resp, model=model)

    assert key._model_daily_requests[model] == 1001
    assert key._model_headers[model]["limit_requests"] == 14400

    # Key MUST remain available despite > 1000 requests used
    assert key.is_available(model=model) is True
    assert key.get_seconds_until_ready(model=model) == 0.0

    # Test at 5,000 requests used
    key._model_daily_requests[model] = 5000
    assert key.is_available(model=model) is True
    assert key.get_seconds_until_ready(model=model) == 0.0

    # Test at 14,399 requests used (boundary)
    key._model_daily_requests[model] = 14399
    assert key.is_available(model=model) is True
    assert key.get_seconds_until_ready(model=model) == 0.0


def test_challenge_6_2_high_rpd_boundary_at_limit_14400():
    """
    Verify that when high RPD model strictly reaches its 14,400 allowance,
    it is correctly marked exhausted and returns non-zero wait.
    """
    key = GroqKey("gsk_rpd_limit_key")
    model = "llama-3.1-8b-instant"

    req = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    resp = httpx.Response(
        200,
        request=req,
        headers={
            "x-ratelimit-limit-requests": "14400",
            "x-ratelimit-remaining-requests": "0",
            "x-ratelimit-reset-requests": "3600s",
        },
        json={"choices": [{"message": {"content": "ok"}}]},
    )
    key.update_from_response(resp, model=model)

    assert key._model_daily_requests[model] == 14400
    # Must now be exhausted
    assert key.is_available(model=model) is False
    wait = key.get_seconds_until_ready(model=model)
    assert wait >= 3590.0


def test_challenge_6_3_high_rpd_multi_model_isolation_on_same_key():
    """
    Verify that Model A with 14,400 RPD allowance and Model B with 1,000 RPD allowance
    are isolated on the same key:
    Model A having used 2,000 requests is ACTIVE.
    Model B having used 1,000 requests is EXHAUSTED.
    """
    key = GroqKey("gsk_multi_model_rpd_key")
    model_a = "llama-3.1-8b-instant"
    model_b = "qwen/qwen3.8-27b"

    # Configure Model A headers
    key._model_headers[model_a] = {"limit_requests": 14400}
    key._model_daily_requests[model_a] = 2000

    # Configure Model B headers (default 1000 limit)
    key._model_headers[model_b] = {"limit_requests": 1000}
    key._model_daily_requests[model_b] = 1000

    # Model A: 2000 < 14400 -> available
    assert key.is_available(model=model_a) is True
    assert key.get_seconds_until_ready(model=model_a) == 0.0

    # Model B: 1000 >= 1000 -> unavailable
    assert key.is_available(model=model_b) is False
    assert key.get_seconds_until_ready(model=model_b) > 0.0


@pytest.mark.asyncio
async def test_challenge_6_4_high_rpd_end_to_end_provider_generate_text_past_1000_requests():
    """
    Verify end-to-end generate_text call succeeds when a 14,400 RPD model
    has already consumed 1,500 requests on the active key.
    """
    pool = GroqKeyPool(keys=["gsk_high_rpd_e2e"])
    provider = GroqProvider(key_pool=pool, default_model="llama-3.1-8b-instant")
    key = pool.get_keys()[0]

    # Pre-populate 1,500 used requests under a 14,400 limit
    key._model_headers["llama-3.1-8b-instant"] = {"limit_requests": 14400}
    key._model_daily_requests["llama-3.1-8b-instant"] = 1500

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.is_closed = False

    async def mock_post(url, json=None, headers=None):
        req = httpx.Request("POST", url)
        return httpx.Response(
            200,
            request=req,
            headers={
                "x-ratelimit-limit-requests": "14400",
                "x-ratelimit-remaining-requests": "12899",
            },
            json={"choices": [{"message": {"content": "high_rpd_success"}}]},
        )

    mock_client.post = AsyncMock(side_effect=mock_post)
    provider._client = mock_client

    result = await provider.generate_text("test prompt", model="llama-3.1-8b-instant")
    assert result == "high_rpd_success"
    assert mock_client.post.call_count == 1


# ============================================================================
# 7. Jitter on Short-Wait Sleep Stress Testing (Requirement 2)
# ============================================================================

@pytest.mark.asyncio
async def test_challenge_7_1_jitter_varies_sleep_times_across_concurrent_calls():
    """
    Verify that when multiple concurrent tasks hit a short cooldown (3.0s < best_wait <= 12.0s),
    the calculated retry sleep times incorporate random jitter (0.05s to 0.15s),
    yielding distinct sleep times and preventing thundering herd lockouts.
    """
    pool = GroqKeyPool(keys=["gsk_jitter_key"])
    provider = GroqProvider(key_pool=pool, default_model="qwen/qwen3.8-27b", max_retries=2)

    key = pool.get_keys()[0]
    # Set a 5.0s cooldown (> 3.0s so acquire_next_key returns None, triggering _execute_with_key_rotation sleep)
    key.mark_rate_limited(is_daily=False, retry_delay_sec=5.0, model="qwen/qwen3.8-27b")

    recorded_sleep_durations = []

    original_sleep = asyncio.sleep

    async def tracking_sleep(delay, *args, **kwargs):
        recorded_sleep_durations.append(delay)
        # Once all 20 tasks have entered retry sleep, clear cooldown so retry attempt 1 succeeds
        if len(recorded_sleep_durations) >= 20:
            key._model_cooldowns.pop("qwen/qwen3.8-27b", None)
        return await original_sleep(0.001)

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.is_closed = False

    async def mock_post(url, json=None, headers=None):
        req = httpx.Request("POST", url)
        return httpx.Response(
            200,
            request=req,
            json={"choices": [{"message": {"content": "ok_jitter"}}]},
        )

    mock_client.post = AsyncMock(side_effect=mock_post)
    provider._client = mock_client

    with patch("asyncio.sleep", side_effect=tracking_sleep):
        tasks = [
            provider.generate_text(f"query_{i}", model="qwen/qwen3.8-27b")
            for i in range(20)
        ]
        results = await asyncio.gather(*tasks)

    assert all(r == "ok_jitter" for r in results)
    assert len(recorded_sleep_durations) >= 20

    # Verify jitter variance: all values should be within [5.0 + 0.05, 5.0 + 0.15] approximately
    for d in recorded_sleep_durations:
        assert 4.90 <= d <= 5.20, f"Sleep duration {d:.4f}s outside expected jitter band around 5.1s"

    # Verify sleep times are not all identical (variance across 20 calls)
    unique_durations = set(round(d, 4) for d in recorded_sleep_durations)
    assert len(unique_durations) >= 10, (
        f"Expected at least 10 unique sleep durations across 20 concurrent calls, "
        f"got {len(unique_durations)}: {unique_durations}"
    )


@pytest.mark.asyncio
async def test_challenge_7_2_short_wait_boundary_12s_jitter_clamp():
    """
    Verify that when best_wait is near 12.0s (e.g. 11.95s),
    the sleep time with jitter is strictly clamped to min(best_wait + jitter, 12.0) <= 12.0s.
    """
    pool = GroqKeyPool(keys=["gsk_clamp_key"])
    provider = GroqProvider(key_pool=pool, default_model="qwen/qwen3.8-27b", max_retries=1)

    key = pool.get_keys()[0]
    key.mark_rate_limited(is_daily=False, retry_delay_sec=11.95, model="qwen/qwen3.8-27b")

    recorded_sleeps = []

    async def tracking_sleep(delay, *args, **kwargs):
        recorded_sleeps.append(delay)
        return

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.is_closed = False
    provider._client = mock_client

    with patch("asyncio.sleep", side_effect=tracking_sleep):
        with pytest.raises(RuntimeError):
            await provider.generate_text("test", model="qwen/qwen3.8-27b")

    assert len(recorded_sleeps) >= 1
    # Sleep duration must be <= 12.0s despite 11.95 + jitter (which would be 12.00-12.10)
    assert recorded_sleeps[0] <= 12.0, f"Sleep duration {recorded_sleeps[0]}s exceeded 12.0s ceiling!"
    assert recorded_sleeps[0] >= 11.90


@pytest.mark.asyncio
async def test_challenge_7_3_wait_over_12s_rejects_immediately_without_sleep():
    """
    Verify that when best_wait > 12.0s (e.g. 15.0s), provider immediately raises 429
    without sleeping.
    """
    pool = GroqKeyPool(keys=["gsk_over12_key"])
    provider = GroqProvider(key_pool=pool, default_model="qwen/qwen3.8-27b")

    key = pool.get_keys()[0]
    key.mark_rate_limited(is_daily=False, retry_delay_sec=15.0, model="qwen/qwen3.8-27b")

    sleep_called = False

    async def tracking_sleep(delay, *args, **kwargs):
        nonlocal sleep_called
        sleep_called = True

    with patch("asyncio.sleep", side_effect=tracking_sleep):
        with pytest.raises(RuntimeError) as exc_info:
            await provider.generate_text("test", model="qwen/qwen3.8-27b")

    assert "429 Rate limit reached" in str(exc_info.value)
    assert sleep_called is False, "asyncio.sleep should NOT be called when wait > 12.0s"


# ============================================================================
# 8. Key Cooldown Reason Retention Under Model Cooldowns (Requirement 3)
# ============================================================================

def test_challenge_8_1_key_level_reason_preserved_when_model_cooldown_applied():
    """
    Verify that setting a model-specific cooldown does NOT overwrite
    the key-level cooldown reason or key status.
    """
    key = GroqKey("gsk_reason_retention_key")

    # Step 1: Set global key-level daily exhaustion reason
    key.mark_rate_limited(
        is_daily=True,
        retry_delay_sec=3600.0,
        reason="Organization Daily Quota Exhausted across org_main",
        model=None,
    )
    assert key.status == KeyStatus.DAILY_EXHAUSTED
    assert key.cooldown_reason == "Organization Daily Quota Exhausted across org_main"

    # Step 2: Apply a model-specific rate limit
    key.mark_rate_limited(
        is_daily=False,
        retry_delay_sec=5.0,
        reason="Model RPM limit reached on qwen/qwen3.8-27b",
        model="qwen/qwen3.8-27b",
    )

    # Key-level reason must be PRESERVED
    assert key.cooldown_reason == "Organization Daily Quota Exhausted across org_main", (
        f"Key cooldown_reason was overwritten: '{key.cooldown_reason}'"
    )
    # Model-specific reason must be stored in model dictionary
    assert key._model_cooldown_reasons["qwen/qwen3.8-27b"] == "Model RPM limit reached on qwen/qwen3.8-27b"


def test_challenge_8_2_multiple_model_reasons_isolated_on_same_key():
    """
    Verify multiple models rate-limited on the same key maintain distinct,
    independent reasons and telemetry outputs.
    """
    key = GroqKey("gsk_multi_reason_key")

    key.mark_rate_limited(
        is_daily=False,
        retry_delay_sec=10.0,
        reason="Reason A: Qwen 30 RPM limit",
        model="qwen/qwen3.8-27b",
    )
    key.mark_rate_limited(
        is_daily=True,
        retry_delay_sec=1800.0,
        reason="Reason B: GPT-OSS 120b daily TPD 200k limit",
        model="openai/gpt-oss-120b",
    )

    # Verify both reasons are intact
    assert key._model_cooldown_reasons["qwen/qwen3.8-27b"] == "Reason A: Qwen 30 RPM limit"
    assert key._model_cooldown_reasons["openai/gpt-oss-120b"] == "Reason B: GPT-OSS 120b daily TPD 200k limit"

    # Verify telemetry representation exposes both reasons accurately
    telemetry = key.to_dict()
    assert "qwen/qwen3.8-27b" in telemetry["model_cooldowns"]
    assert "openai/gpt-oss-120b" in telemetry["model_cooldowns"]
    assert telemetry["model_cooldowns"]["qwen/qwen3.8-27b"]["reason"] == "Reason A: Qwen 30 RPM limit"
    assert telemetry["model_cooldowns"]["openai/gpt-oss-120b"]["reason"] == "Reason B: GPT-OSS 120b daily TPD 200k limit"


def test_challenge_8_3_http_429_sequence_preserves_reasons():
    """
    Verify sequence of HTTP 429 responses via update_from_response preserves
    reasons and does not clobber key-level reasons.
    """
    key = GroqKey("gsk_http_reason_seq")
    key.cooldown_reason = "Base key reason"

    req = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    resp_429 = httpx.Response(
        429,
        request=req,
        headers={"retry-after": "5"},
        json={"error": {"message": "Rate limit on qwen"}},
    )

    key.update_from_response(resp_429, model="qwen/qwen3.8-27b")

    # Key cooldown reason remains unchanged
    assert key.cooldown_reason == "Base key reason"
    # Model reason reflects 429
    assert "qwen/qwen3.8-27b" in key._model_cooldown_reasons
