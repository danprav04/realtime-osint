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

    # Key 1 is marked in cooldown; Key 2 marked success
    assert pool.get_keys()[0].status == KeyStatus.MINUTE_COOLDOWN
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
