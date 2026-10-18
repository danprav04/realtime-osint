"""
backend/tests/test_groq_key_pool.py - Unit & Integration Tests for Groq Key Pool & Rotation
=============================================================================================
Tests:
  - Key list parsing and deduplication
  - Round-robin load balancing distribution
  - Per-key independent sliding-window pacing
  - Automatic 429 minute-limit failover
  - Automatic 429 daily-quota failover
  - Automatic 401 unauthorized key deactivation
  - Pool exhaustion behavior
  - Live telemetry reporting
  - GroqProvider multi-key execution & failover
"""

import asyncio
from unittest.mock import AsyncMock, patch
import httpx
import pytest

from backend.app.pipeline.providers.factory import resolve_api_key, resolve_api_keys, get_shared_groq_pool
from backend.app.pipeline.providers.groq import GroqProvider
from backend.app.pipeline.providers.key_pool import (
    GroqKey,
    GroqKeyPool,
    KeyStatus,
    mask_key,
    parse_key_list,
)


def test_mask_key():
    assert mask_key("") == ""
    assert mask_key("12345") == "***"
    assert mask_key("gsk_mock_test_key_for_unit_tests_abcdef1234567890abcdef12e4P") == "gsk_...2e4P"


def test_parse_key_list():
    raw_single = "gsk_key1"
    assert parse_key_list(raw_single) == ["gsk_key1"]

    raw_csv = "gsk_key1, gsk_key2, gsk_key3 , gsk_key1"
    assert parse_key_list(raw_csv) == ["gsk_key1", "gsk_key2", "gsk_key3"]

    raw_lines = "gsk_keyA\ngsk_keyB\r\ngsk_keyC"
    assert parse_key_list(raw_lines) == ["gsk_keyA", "gsk_keyB", "gsk_keyC"]

    # Ignores masked placeholders
    raw_with_masked = "gsk_real_key, ***12e4, ***"
    assert parse_key_list(raw_with_masked) == ["gsk_real_key"]


@pytest.mark.asyncio
async def test_round_robin_selection():
    keys = ["gsk_key1", "gsk_key2", "gsk_key3", "gsk_key4"]
    pool = GroqKeyPool(keys=keys)
    assert pool.total_keys == 4

    acquired = []
    for _ in range(8):
        slot = await pool.acquire_next_key()
        assert slot is not None
        acquired.append(slot.key)

    assert acquired == [
        "gsk_key1",
        "gsk_key2",
        "gsk_key3",
        "gsk_key4",
        "gsk_key1",
        "gsk_key2",
        "gsk_key3",
        "gsk_key4",
    ]


@pytest.mark.asyncio
async def test_independent_per_key_pacing():
    keys = ["gsk_keyA", "gsk_keyB"]
    pool = GroqKeyPool(keys=keys)

    slot_a = await pool.acquire_next_key()
    slot_b = await pool.acquire_next_key()
    assert slot_a.key == "gsk_keyA"
    assert slot_b.key == "gsk_keyB"

    # Acquiring pacing slot on Key A does not impede Key B
    await slot_a.acquire_pacing_slot()
    assert slot_a.total_requests == 1
    assert slot_b.total_requests == 0

    await slot_b.acquire_pacing_slot()
    assert slot_b.total_requests == 1


@pytest.mark.asyncio
async def test_429_minute_cooldown_failover():
    keys = ["gsk_key1", "gsk_key2", "gsk_key3"]
    pool = GroqKeyPool(keys=keys)

    slot1 = await pool.acquire_next_key()
    assert slot1.key == "gsk_key1"

    # Simulate Key 1 hitting minute rate limit (20s reset)
    slot1.mark_rate_limited(is_daily=False, retry_delay_sec=20.0, reason="RPM limit 30")
    assert slot1.status == KeyStatus.MINUTE_COOLDOWN
    assert slot1.minute_rate_limits == 1

    # Next selections should skip Key 1 and alternate between Key 2 and Key 3
    s2 = await pool.acquire_next_key()
    s3 = await pool.acquire_next_key()
    s4 = await pool.acquire_next_key()

    assert s2.key == "gsk_key2"
    assert s3.key == "gsk_key3"
    assert s4.key == "gsk_key2"


@pytest.mark.asyncio
async def test_429_daily_quota_failover():
    keys = ["gsk_key1", "gsk_key2"]
    pool = GroqKeyPool(keys=keys)

    slot1 = await pool.acquire_next_key()
    assert slot1.key == "gsk_key1"

    # Simulate Key 1 hitting daily quota exhaustion (RPD)
    slot1.mark_rate_limited(is_daily=True, retry_delay_sec=14400.0, reason="RPD quota 1000 exceeded")
    assert slot1.status == KeyStatus.DAILY_EXHAUSTED
    assert slot1.daily_quota_exhaustions == 1

    # Key 1 is locked out for 4 hours; only Key 2 accepts traffic
    for _ in range(5):
        s = await pool.acquire_next_key()
        assert s.key == "gsk_key2"


@pytest.mark.asyncio
async def test_unauthorized_401_deactivation():
    keys = ["gsk_invalid", "gsk_valid"]
    pool = GroqKeyPool(keys=keys)

    slot_inv = await pool.acquire_next_key()
    assert slot_inv.key == "gsk_invalid"

    # Mark 401 Unauthorized
    slot_inv.mark_unauthorized()
    assert slot_inv.status == KeyStatus.UNAUTHORIZED

    # Only valid key is returned subsequently
    for _ in range(3):
        s = await pool.acquire_next_key()
        assert s.key == "gsk_valid"


@pytest.mark.asyncio
async def test_all_keys_exhausted():
    keys = ["gsk_k1", "gsk_k2"]
    pool = GroqKeyPool(keys=keys)

    for k in pool.get_keys():
        k.mark_rate_limited(is_daily=True, retry_delay_sec=3600.0, reason="Daily limit")

    slot = await pool.acquire_next_key()
    assert slot is None


def test_telemetry_reporting():
    keys = ["gsk_key1", "gsk_key2", "gsk_key3"]
    pool = GroqKeyPool(keys=keys)

    pool.get_keys()[0].mark_success()
    pool.get_keys()[1].mark_rate_limited(is_daily=False, retry_delay_sec=15.0, reason="TPM limit")

    tel = pool.get_telemetry()
    assert tel["total_keys"] == 3
    assert tel["active_keys"] == 2
    assert tel["keys_in_cooldown"] == 1
    assert tel["total_success"] == 1
    assert len(tel["keys"]) == 3


@pytest.mark.asyncio
async def test_groq_provider_rotation_on_429():
    """Verify GroqProvider catches 429 on Key 1, fails over to Key 2, and succeeds."""
    keys = ["gsk_key1", "gsk_key2"]
    pool = GroqKeyPool(keys=keys)
    provider = GroqProvider(key_pool=pool, default_model="qwen/qwen3.8-27b")

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.is_closed = False

    calls = []

    async def mock_post(url, json=None, headers=None):
        auth_hdr = headers.get("Authorization", "")
        calls.append(auth_hdr)
        req = httpx.Request("POST", url)
        if "gsk_key1" in auth_hdr:
            # First key returns 429 minute limit
            return httpx.Response(
                429,
                request=req,
                headers={"retry-after": "10", "x-ratelimit-remaining-requests": "0"},
                json={"error": {"message": "Rate limit reached for requests per minute"}},
            )
        else:
            # Second key returns 200 OK
            return httpx.Response(
                200,
                request=req,
                json={"choices": [{"message": {"content": '{"translated_text": "Hello World"}'}}]},
            )

    mock_client.post = mock_post
    provider._client = mock_client

    result = await provider.process_stage("extraction", "Bonjour le monde")
    assert result == {"translated_text": "Hello World"}

    # Verified that both Key 1 and Key 2 were invoked
    assert len(calls) == 2
    assert "Bearer gsk_key1" in calls[0]
    assert "Bearer gsk_key2" in calls[1]

    # Key 1 is throttled for qwen/qwen3.8-27b but remains ACTIVE for other models; Key 2 marked success
    assert pool.get_keys()[0].status == KeyStatus.ACTIVE
    assert pool.get_keys()[0].is_available(model="qwen/qwen3.8-27b") is False
    assert pool.get_keys()[0].is_available(model="llama-3.1-8b-instant") is True
    assert pool.get_keys()[0].get_model_cooldown_remaining("qwen/qwen3.8-27b") > 0
    assert pool.get_keys()[1].status == KeyStatus.ACTIVE
    assert pool.get_keys()[1].success_requests == 1


@pytest.mark.asyncio
async def test_groq_provider_missing_key_error():
    provider = GroqProvider(api_key="", key_pool=GroqKeyPool(keys=[]))
    with pytest.raises(PermissionError) as exc_info:
        await provider.generate_text("Test prompt")
    assert "Groq API key is invalid or unauthorized" in str(exc_info.value)


def test_factory_multi_key_resolution():
    # Test comma-separated parsing
    db_keys = {
        "groq_api_key": "gsk_db_k1, gsk_db_k2, gsk_db_k3",
    }
    resolved = resolve_api_keys("groq", db_keys=db_keys)
    assert resolved == ["gsk_db_k1", "gsk_db_k2", "gsk_db_k3"]

    primary = resolve_api_key("groq", db_keys=db_keys)
    assert primary == "gsk_db_k1"


def test_groq_key_not_assumed_locked_initially():
    """Verify GroqKey is never assumed locked initially; pacing caps are not imposed by default."""
    key = GroqKey(key="gsk_fresh_test_key")
    assert key.status == KeyStatus.ACTIVE
    assert key.cooldown_until == 0.0
    assert key.is_available() is True
    assert key.min_interval_sec == 0.0
    assert key.max_rpm == 0
    assert key.get_seconds_until_ready() == 0.0


def test_groq_key_locks_using_retry_after_header():
    """Verify HTTP 429 locks key for the exact retry-after seconds returned by Groq, not arbitrary numbers."""
    key = GroqKey(key="gsk_test_retry_after")
    req = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    resp_429 = httpx.Response(
        429,
        request=req,
        headers={
            "retry-after": "2",
            "x-ratelimit-remaining-requests": "14370",
            "x-ratelimit-remaining-tokens": "17997",
            "x-ratelimit-limit-requests": "14400",
            "x-ratelimit-limit-tokens": "18000",
        },
        json={"error": {"message": "Rate limit reached"}},
    )

    key.update_from_response(resp_429)

    assert key.retry_after_sec == 2.0
    assert key.status == KeyStatus.MINUTE_COOLDOWN
    assert key.minute_rate_limits == 1
    assert "retry-after 2s" in key.cooldown_reason
    assert key.is_available() is False
    # Cooldown should be locked by ~2.0 seconds (not 25s, not 65s, not 14400s)
    remaining = key.get_seconds_until_ready()
    assert 1.0 <= remaining <= 2.1


def test_groq_key_locks_using_reset_tokens_header():
    """Verify HTTP 429 with TPM reset locks for exact x-ratelimit-reset-tokens duration (e.g. 7.66s)."""
    key = GroqKey(key="gsk_test_reset_tokens")
    req = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    resp_429 = httpx.Response(
        429,
        request=req,
        headers={
            "x-ratelimit-remaining-tokens": "0",
            "x-ratelimit-reset-tokens": "7.66s",
            "x-ratelimit-remaining-requests": "14370",
            "x-ratelimit-limit-tokens": "18000",
        },
        json={"error": {"message": "Rate limit reached for tokens per minute"}},
    )

    key.update_from_response(resp_429)

    assert key.reset_tokens_sec == 7.66
    assert key.status == KeyStatus.MINUTE_COOLDOWN
    assert key.is_available() is False
    remaining = key.get_seconds_until_ready()
    assert 6.0 <= remaining <= 7.7


def test_groq_key_locks_using_reset_requests_header_not_4_hours():
    """Verify HTTP 429 with RPD reset (2m59.56s) locks for 179.56s, NEVER arbitrary 14400s (4 hours)."""
    key = GroqKey(key="gsk_test_reset_requests")
    req = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    resp_429 = httpx.Response(
        429,
        request=req,
        headers={
            "x-ratelimit-remaining-requests": "0",
            "x-ratelimit-reset-requests": "2m59.56s",
            "x-ratelimit-remaining-tokens": "17997",
            "x-ratelimit-limit-requests": "14400",
        },
        json={"error": {"message": "Rate limit reached for requests per day"}},
    )

    key.update_from_response(resp_429)

    assert key.reset_requests_sec == 179.56
    # 2m59.56s is <= 3600s, so it is treated as a short requests cooldown, not 14400s (4 hours)
    remaining = key.get_seconds_until_ready()
    assert 170.0 <= remaining <= 180.0
    assert remaining < 300.0  # Crucial: NOT 14400s!


def test_groq_key_200_ok_updates_quota_and_keeps_active():
    """Verify 200 OK responses update capacity and confirm key is active and ready."""
    key = GroqKey(key="gsk_test_200_ok")
    req = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    resp_200 = httpx.Response(
        200,
        request=req,
        headers={
            "x-ratelimit-remaining-requests": "14370",
            "x-ratelimit-remaining-tokens": "17997",
            "x-ratelimit-limit-requests": "14400",
            "x-ratelimit-limit-tokens": "18000",
            "x-ratelimit-reset-requests": "2m59.56s",
            "x-ratelimit-reset-tokens": "7.66s",
        },
        json={"choices": [{"message": {"content": "ok"}}]},
    )

    key.update_from_response(resp_200)

    assert key.status == KeyStatus.ACTIVE
    assert key.is_available() is True
    assert key.remaining_requests == 14370
    assert key.remaining_tokens == 17997
    assert key.limit_requests == 14400
    assert key.limit_tokens == 18000
    assert key.reset_requests_sec == 179.56
    assert key.reset_tokens_sec == 7.66
    assert key.cooldown_until == 0.0


def test_groq_key_200_ok_locks_when_tokens_depleted():
    """Verify 200 OK that depletes tokens to 0 locks key for exact reset-tokens duration."""
    key = GroqKey(key="gsk_test_200_depleted")
    req = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    resp_200 = httpx.Response(
        200,
        request=req,
        headers={
            "x-ratelimit-remaining-requests": "14000",
            "x-ratelimit-remaining-tokens": "0",
            "x-ratelimit-reset-tokens": "4.2s",
        },
        json={"choices": [{"message": {"content": "ok"}}]},
    )

    key.update_from_response(resp_200)

    assert key.status == KeyStatus.MINUTE_COOLDOWN
    assert key.is_available() is False
    assert "tokens depleted (reset in 4.2s)" in key.cooldown_reason
    remaining = key.get_seconds_until_ready()
    assert 3.0 <= remaining <= 4.3


def test_groq_provider_does_not_assume_pacing_delay():
    """Verify GroqProvider initializes with min_interval_sec=0 and max_rpm=0."""
    pool = GroqKeyPool(keys=["gsk_pacing_test"])
    provider = GroqProvider(key_pool=pool)
    assert provider.min_interval_sec == 0.0
    assert provider.max_rpm == 0


def test_groq_key_model_specific_cooldown_keeps_key_available_for_other_models():
    """Verify that a model 429 locks strictly that model and keeps the key active for others."""
    key = GroqKey(key="gsk_model_test_key")
    req = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    resp_429 = httpx.Response(
        429,
        request=req,
        headers={"retry-after": "307"},
        json={"error": {"message": "Rate limit reached for model llama-3.3-70b-versatile"}},
    )

    key.update_from_response(resp_429, model="llama-3.3-70b-versatile")

    # Key status must remain ACTIVE!
    assert key.status == KeyStatus.ACTIVE
    assert key.is_available() is True
    # The specific model is throttled
    assert key.is_available(model="llama-3.3-70b-versatile") is False
    # Other models are completely available
    assert key.is_available(model="llama-3.1-8b-instant") is True
    assert key.is_available(model="qwen/qwen3.8-27b") is True

    # Cooldown remaining for throttled model is ~307s
    model_rem = key.get_model_cooldown_remaining("llama-3.3-70b-versatile")
    assert 300.0 <= model_rem <= 308.0

    # Key dict reports active status and throttled models
    d = key.to_dict()
    assert d["status"] == "active"
    assert d["cooldown_remaining_sec"] == 0.0
    assert "llama-3.3-70b-versatile" in d["throttled_models"]


@pytest.mark.asyncio
async def test_pool_rotates_and_isolates_by_model():
    """Verify GroqKeyPool isolates key rotation strictly by model."""
    keys = ["gsk_keyA", "gsk_keyB"]
    pool = GroqKeyPool(keys=keys)

    # Key A gets throttled for llama-3.3-70b-versatile
    key_a = pool.get_keys()[0]
    key_a.mark_rate_limited(is_daily=False, retry_delay_sec=120.0, reason="TPM limit", model="llama-3.3-70b-versatile")

    # When acquiring for llama-3.3-70b-versatile, key A is skipped, key B is returned
    slot_llama = await pool.acquire_next_key(model="llama-3.3-70b-versatile")
    assert slot_llama is not None
    assert slot_llama.key == "gsk_keyB"

    # When acquiring for qwen/qwen3.8-27b, key A IS AVAILABLE and returned!
    slot_qwen = await pool.acquire_next_key(model="qwen/qwen3.8-27b")
    assert slot_qwen is not None
    assert slot_qwen.key == "gsk_keyA"


@pytest.mark.asyncio
async def test_groq_provider_fallback_model_succeeds_when_primary_model_exhausts_keys():
    """Verify that when a primary model exhausts pool keys, a fallback model on Groq can run and succeed."""
    keys = ["gsk_single_key"]
    pool = GroqKeyPool(keys=keys)
    provider = GroqProvider(key_pool=pool, default_model="llama-3.3-70b-versatile")

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.is_closed = False

    async def mock_post(url, json=None, headers=None):
        req = httpx.Request("POST", url)
        model_req = json.get("model")
        if model_req == "llama-3.3-70b-versatile":
            return httpx.Response(
                429,
                request=req,
                headers={"retry-after": "300"},
                json={"error": {"message": "Rate limit reached for model llama-3.3-70b-versatile"}},
            )
        else:
            return httpx.Response(
                200,
                request=req,
                json={"choices": [{"message": {"content": '{"summary": "Fallback Success"}'}}]},
            )

    mock_client.post = mock_post
    provider._client = mock_client

    # 1. Primary model fails with 429 and exhausts key
    with pytest.raises(RuntimeError) as exc_info:
        await provider.generate_text("test prompt", model="llama-3.3-70b-versatile")
    assert "Rate limit reached for model 'llama-3.3-70b-versatile'" in str(exc_info.value)

    # 2. Key remains ACTIVE in pool
    assert pool.get_keys()[0].status == KeyStatus.ACTIVE

    # 3. Fallback model immediately runs on the same key and succeeds!
    fallback_res = await provider.process_stage("summarization", "breaking event", model="qwen/qwen3.8-27b")
    assert fallback_res == {"summary": "Fallback Success"}


@pytest.mark.asyncio
async def test_groq_key_429_sub_millisecond_reset_tokens_enforces_minimum_cooldown():
    """Verify that a 429 with sub-millisecond reset-tokens enforces the minimum cooldown floor (>= 3.0s)."""
    key = GroqKey("gsk_test_key")
    req = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    # Simulate Groq 429 with 1ms reset tokens header
    resp = httpx.Response(
        429,
        request=req,
        headers={
            "x-ratelimit-reset-tokens": "1ms",
            "x-ratelimit-remaining-tokens": "0",
            "x-ratelimit-limit-tokens": "8000",
        },
        json={"error": {"message": "Rate limit reached. Please try again in 4.5s."}},
    )

    key.update_from_response(resp, model="qwen/qwen3.8-27b")
    cd = key.get_model_cooldown_remaining("qwen/qwen3.8-27b")
    # Must be at least 3.0s (or 4.5s parsed from body), never 0.001s!
    assert cd >= 3.0


@pytest.mark.asyncio
async def test_groq_key_pool_broadcast_model_cooldown():
    """Verify that broadcast_model_cooldown puts all keys in the pool on cooldown for that model."""
    pool = GroqKeyPool(keys=["gsk_key1", "gsk_key2", "gsk_key3"])
    pool.broadcast_model_cooldown(model="qwen/qwen3.8-27b", cooldown_sec=10.0, reason="TPM limit reached")

    for k in pool.get_keys():
        assert k.get_model_cooldown_remaining("qwen/qwen3.8-27b") >= 9.0
        # Keys remain active for other models
        assert k.is_available(model="openai/gpt-oss-20b")





@pytest.mark.asyncio
async def test_twelve_independent_buckets_across_four_keys_and_three_models():
    """Verify complete isolation across 4 keys x 3 models = 12 independent buckets."""
    keys = ["gsk_key1", "gsk_key2", "gsk_key3", "gsk_key4"]
    models = ["qwen/qwen3.8-27b", "openai/gpt-oss-120b", "openai/gpt-oss-20b"]
    pool = GroqKeyPool(keys=keys)

    # 1. Acquire pacing slots across all 12 buckets to establish state
    for k in pool.get_keys():
        for m in models:
            await k.acquire_pacing_slot(model=m)

    # Verify each key has independent model locks, request timestamps, and daily request counters
    for k in pool.get_keys():
        for m in models:
            assert m in k._model_pacing_locks
            assert len(k._model_request_timestamps[m]) == 1
            assert k._model_daily_requests[m] == 1

    # 2. Put bucket (Key 1, qwen/qwen3.8-27b) on a 100s cooldown
    k1 = pool.get_keys()[0]
    k1.mark_rate_limited(is_daily=False, retry_delay_sec=100.0, reason="TPM limit", model="qwen/qwen3.8-27b")

    # (Key 1, qwen/qwen3.8-27b) is throttled
    assert k1.is_available(model="qwen/qwen3.8-27b") is False
    # But Key 1's other two buckets are 100% available
    assert k1.is_available(model="openai/gpt-oss-120b") is True
    assert k1.is_available(model="openai/gpt-oss-20b") is True

    # Keys 2, 3, 4 are available for all 3 models
    for k in pool.get_keys()[1:]:
        for m in models:
            assert k.is_available(model=m) is True

    # 3. Simulate token consumption strictly in bucket (Key 2, openai/gpt-oss-120b)
    k2 = pool.get_keys()[1]
    k2.record_token_usage("openai/gpt-oss-120b", 4500)
    assert sum(tok for _, tok in k2._model_token_records["openai/gpt-oss-120b"]) == 4500
    assert k2._model_daily_tokens["openai/gpt-oss-120b"] == 4500

    # Ensure other 11 buckets have 0 token records
    for k in pool.get_keys():
        for m in models:
            if k == k2 and m == "openai/gpt-oss-120b":
                continue
            assert sum(tok for _, tok in k._model_token_records.get(m, [])) == 0

    # 4. Key rotation for qwen/qwen3.8-27b skips Key 1 (on cooldown) and picks Key 2
    next_qwen = await pool.acquire_next_key(model="qwen/qwen3.8-27b")
    assert next_qwen is not None
    assert next_qwen.key == "gsk_key2"

    # Pool round-robin index continues to Key 3, then Key 4, and rotates back to Key 1 for openai/gpt-oss-120b
    next_120b_1 = await pool.acquire_next_key(model="openai/gpt-oss-120b")
    assert next_120b_1.key == "gsk_key3"
    next_120b_2 = await pool.acquire_next_key(model="openai/gpt-oss-120b")
    assert next_120b_2.key == "gsk_key4"
    next_120b_3 = await pool.acquire_next_key(model="openai/gpt-oss-120b")
    assert next_120b_3.key == "gsk_key1"


def test_groq_key_mark_rate_limited_cooldown_retention():
    """Verify mark_rate_limited retains longer cooldowns and does not overwrite with shorter ones."""
    key = GroqKey("gsk_retention_test")
    model = "qwen/qwen3.8-27b"

    # 1. Establish initial long daily cooldown (14,400s)
    key.mark_rate_limited(
        is_daily=True,
        retry_delay_sec=14400.0,
        reason="Daily TPD 200k exhausted",
        model=model,
    )
    rem_initial = key.get_model_cooldown_remaining(model)
    assert 14390.0 <= rem_initial <= 14401.0
    assert key._model_cooldown_reasons[model] == "Daily TPD 200k exhausted"
    assert key.daily_quota_exhaustions == 1

    # 2. Attempt to apply a shorter minute cooldown (15s)
    key.mark_rate_limited(
        is_daily=False,
        retry_delay_sec=15.0,
        reason="Minute RPM limit",
        model=model,
    )
    # Expiry must NOT have regressed to 15s; it must remain ~14400s!
    rem_after_short = key.get_model_cooldown_remaining(model)
    assert 14390.0 <= rem_after_short <= 14401.0
    # Reason must not be downgraded
    assert key._model_cooldown_reasons[model] == "Daily TPD 200k exhausted"
    assert key.minute_rate_limits == 1

    # 3. Apply an even longer cooldown (20,000s) -> should update
    key.mark_rate_limited(
        is_daily=True,
        retry_delay_sec=20000.0,
        reason="Extended 24h block",
        model=model,
    )
    rem_longer = key.get_model_cooldown_remaining(model)
    assert 19990.0 <= rem_longer <= 20001.0
    assert key._model_cooldown_reasons[model] == "Extended 24h block"


def test_tpd_exhaustion_classified_as_daily_with_remaining_requests_positive():
    """Verify HTTP 429 for 24h TPD exhaustion is classified as daily even when remaining requests > 0."""
    key = GroqKey("gsk_tpd_test")
    req = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    # Groq response: 985 requests left today, but 200K TPD reached (retry-after 1042s)
    resp = httpx.Response(
        429,
        request=req,
        headers={
            "retry-after": "1042",
            "x-ratelimit-remaining-requests": "985",
            "x-ratelimit-remaining-tokens": "7500",
            "x-ratelimit-limit-requests": "1000",
            "x-ratelimit-limit-tokens": "8000",
        },
        json={
            "error": {
                "message": (
                    "Rate limit reached for model `qwen/qwen3.8-27b` in organization `org_test` "
                    "on tokens per day (TPD): Limit 200000, Used 200000. Please try again in 17m22s."
                )
            }
        },
    )

    key.update_from_response(resp, model="qwen/qwen3.8-27b")

    # Must be classified as DAILY exhaustion, not minute rate limit
    assert key.daily_quota_exhaustions == 1
    assert key.minute_rate_limits == 0
    cd = key.get_model_cooldown_remaining("qwen/qwen3.8-27b")
    assert 1030.0 <= cd <= 1043.0
    assert key.is_available(model="qwen/qwen3.8-27b") is False
    # Key itself remains ACTIVE for other models
    assert key.status == KeyStatus.ACTIVE
    assert key.is_available(model="openai/gpt-oss-120b") is True


@pytest.mark.asyncio
async def test_groq_rotation_does_not_broadcast_model_cooldown_across_keys():
    """Verify that a 429 on one key does not broadcast cooldown to other keys during execution."""
    keys = ["gsk_key1", "gsk_key2"]
    pool = GroqKeyPool(keys=keys)
    provider = GroqProvider(key_pool=pool, default_model="qwen/qwen3.8-27b")

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.is_closed = False

    async def mock_post(url, json=None, headers=None):
        auth_hdr = headers.get("Authorization", "")
        req = httpx.Request("POST", url)
        if "gsk_key1" in auth_hdr:
            # Key 1 hits 429 TPD exhaustion
            return httpx.Response(
                429,
                request=req,
                headers={"retry-after": "500"},
                json={"error": {"message": "Rate limit reached on tokens per day (TPD)"}},
            )
        else:
            # Key 2 succeeds
            return httpx.Response(
                200,
                request=req,
                json={"choices": [{"message": {"content": "ok"}}]},
            )

    mock_client.post = mock_post
    provider._client = mock_client

    result = await provider.generate_text("test", model="qwen/qwen3.8-27b")
    assert result == "ok"

    # Key 1 has cooldown for qwen/qwen3.8-27b
    k1, k2 = pool.get_keys()
    assert k1.get_model_cooldown_remaining("qwen/qwen3.8-27b") > 400.0
    # Crucial: Key 2 was NOT broadcast locked! Key 2 remains completely ready!
    assert k2.get_model_cooldown_remaining("qwen/qwen3.8-27b") == 0.0
    assert k2.is_available(model="qwen/qwen3.8-27b") is True


@pytest.mark.asyncio
async def test_execute_with_key_rotation_short_wait_retry_sleep():
    """Verify that when all keys have best_wait <= 12.0s, rotation sleeps and retries rather than failing."""
    keys = ["gsk_single_key"]
    pool = GroqKeyPool(keys=keys)
    provider = GroqProvider(key_pool=pool, default_model="qwen/qwen3.8-27b")

    # Put single key on a short 0.15s cooldown
    k1 = pool.get_keys()[0]
    k1.mark_rate_limited(is_daily=False, retry_delay_sec=0.15, reason="Transient short pause", model="qwen/qwen3.8-27b")

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.is_closed = False

    async def mock_post(url, json=None, headers=None):
        req = httpx.Request("POST", url)
        return httpx.Response(
            200,
            request=req,
            json={"choices": [{"message": {"content": "Recovered After Short Wait"}}]},
        )

    mock_client.post = mock_post
    provider._client = mock_client

    # Should NOT raise 429; should sleep ~0.15s + 0.08s and succeed
    result = await provider.generate_text("prompt", model="qwen/qwen3.8-27b")
    assert result == "Recovered After Short Wait"


def test_proactive_tpm_rotation_on_http_200():
    """Verify HTTP 200 with remaining tokens < 1200 proactively sets cooldown for that model."""
    key = GroqKey("gsk_proactive_test")
    req = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    resp_200 = httpx.Response(
        200,
        request=req,
        headers={
            "x-ratelimit-remaining-requests": "990",
            "x-ratelimit-remaining-tokens": "850",  # < 1200 tokens remaining
            "x-ratelimit-reset-tokens": "5.5s",
        },
        json={"choices": [{"message": {"content": "ok"}}]},
    )

    key.update_from_response(resp_200, model="qwen/qwen3.8-27b")

    # Proactive cooldown set for model
    assert key.is_available(model="qwen/qwen3.8-27b") is False
    cd = key.get_model_cooldown_remaining("qwen/qwen3.8-27b")
    assert 4.5 <= cd <= 5.6
    # Key remains active for other models
    assert key.status == KeyStatus.ACTIVE
    assert key.is_available(model="openai/gpt-oss-120b") is True


@pytest.mark.asyncio
async def test_exact_one_time_token_recording_per_call():
    """Verify that a single generate_text request records token usage exactly once (not duplicated)."""
    pool = GroqKeyPool(keys=["gsk_dedup_test"])
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
                "x-ratelimit-remaining-tokens": "7850",
            },
            json={
                "choices": [{"message": {"content": "ok"}}],
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 50,
                    "total_tokens": 150,
                },
            },
        )

    mock_client.post = mock_post
    provider._client = mock_client

    key = pool.get_keys()[0]
    await provider.generate_text("test prompt", model="qwen/qwen3.8-27b")

    # Tokens must be recorded exactly once: 150 total, not 300
    records = list(key._model_token_records["qwen/qwen3.8-27b"])
    assert len(records) == 1, f"Expected exactly 1 record, got {len(records)}"
    assert records[0][1] == 150
    assert sum(tok for _, tok in records) == 150
    assert key._model_daily_tokens["qwen/qwen3.8-27b"] == 150


def test_utc_date_rollover_resets_daily_counters():
    """Verify that daily counters reset when UTC date changes or after 24h simulated time."""
    key = GroqKey("gsk_rollover_test")
    model = "qwen/qwen3.8-27b"

    # Saturate key with 200,000 tokens
    key.record_token_usage(model, 200_000)
    assert key.is_available(model=model) is False

    # Scenario 1: Advance simulated time by 24 hours + 1 second (86,401s)
    last_reset = key._model_daily_last_reset[model]
    assert key.is_available(now=last_reset + 86401.0, model=model) is True
    assert key._model_daily_tokens[model] == 0

    # Scenario 2: Simulate UTC date change
    key2 = GroqKey("gsk_rollover_test2")
    key2._model_daily_tokens[model] = 200_000
    key2._model_daily_requests[model] = 1000
    assert key2.is_available(model=model) is False
    key2._current_date_utc = "2020-01-01"  # Different UTC date
    assert key2.is_available(model=model) is True
    assert key2._model_daily_tokens.get(model, 0) == 0
    assert key2._model_daily_requests.get(model, 0) == 0

    # Scenario 3: Simulate header reset time elapsed
    key3 = GroqKey("gsk_rollover_test3")
    key3._model_daily_tokens[model] = 200_000
    assert key3.is_available(model=model) is False
    key3._model_daily_reset_at[model] = 500.0
    assert key3.is_available(now=501.0, model=model) is True
    assert key3._model_daily_tokens[model] == 0


@pytest.mark.asyncio
async def test_sliding_tpm_saturation_rotates_to_idle_sibling_key():
    """Verify that when Key 1 reaches 8,000 sliding TPM, is_available returns False and pool selects Key 2."""
    pool = GroqKeyPool(keys=["gsk_saturated", "gsk_idle"])
    k1, k2 = pool.get_keys()
    model = "qwen/qwen3.8-27b"

    # Push 8,500 tokens into Key 1 within 60s sliding window
    k1.record_token_usage(model, 8500)

    # Key 1 has wait > 3.0s, so is_available must return False
    assert k1.get_seconds_until_ready(model=model) >= 55.0
    assert k1.is_available(model=model) is False

    # Key 2 is completely idle
    assert k2.get_seconds_until_ready(model=model) == 0.0
    assert k2.is_available(model=model) is True

    # acquire_next_key must rotate to Key 2
    selected = await pool.acquire_next_key(model=model)
    assert selected is not None
    assert selected.key == "gsk_idle"


def test_dynamic_rpd_limit_in_is_available():
    """Verify that models with higher limits (e.g. 14,400 RPD) are not blocked at 1,000 requests."""
    key = GroqKey("gsk_rpd_test")
    model = "llama-3.1-8b-instant"

    # Set model headers for 14,400 limit and 1,100 used
    key._model_headers[model] = {
        "limit_requests": 14400,
        "remaining_requests": 13300,
    }
    key._model_daily_requests[model] = 1100

    # Must remain AVAILABLE with 13,300 requests left (not blocked by hardcoded 1000)
    assert key.is_available(model=model) is True

    # Once 14,400 requests are exhausted, becomes unavailable
    key._model_daily_requests[model] = 14400
    assert key.is_available(model=model) is False


def test_cooldown_reason_preserved_from_model_clobbering():
    """Verify that model-level rate limits do not overwrite key-level cooldown_reason."""
    key = GroqKey("gsk_reason_test")

    # Set key-level daily exhaustion cooldown
    key.mark_rate_limited(is_daily=True, retry_delay_sec=1000.0, reason="Daily token limit reached", model=None)
    assert key.cooldown_reason == "Daily token limit reached"
    assert key.status == KeyStatus.DAILY_EXHAUSTED

    # Model-level rate limit must NOT clobber key.cooldown_reason
    key.mark_rate_limited(is_daily=False, retry_delay_sec=2.0, reason="Model TPM limit", model="qwen/qwen3.8-27b")
    assert key.cooldown_reason == "Daily token limit reached"
    assert key._model_cooldown_reasons["qwen/qwen3.8-27b"] == "Model TPM limit"

    # Shorter key-level cooldown must NOT overwrite longer reason or expiry
    key.mark_rate_limited(is_daily=False, retry_delay_sec=2.0, reason="Short pause", model=None)
    assert key.cooldown_reason == "Daily token limit reached"

