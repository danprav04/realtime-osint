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



