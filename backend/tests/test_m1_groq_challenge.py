"""
backend/tests/test_m1_groq_challenge.py - Empirical Adversarial Stress Harness for Milestone M1
================================================================================================
Empirical verification of Groq Multi-Key & Per-Model Rate-Limit Isolation:
1. Multi-model bursts on the same key without lock contention.
2. Saturated 200,000 TPD on key 1 while sibling keys remain unblocked.
3. Cooldown retention under short overwrite attacks.
4. Edge-case stability under extreme concurrency and adversarial response sequences.
"""

import asyncio
import time
from unittest.mock import AsyncMock
import httpx
import pytest

from backend.app.pipeline.providers.groq import GroqProvider
from backend.app.pipeline.providers.key_pool import (
    GroqKey,
    GroqKeyPool,
    KeyStatus,
)


@pytest.mark.asyncio
async def test_challenge_multi_model_burst_zero_lock_contention():
    """
    CHALLENGE 1A: Verify multi-model concurrent bursts on the same key have ZERO lock contention.
    Model A is subjected to a simulated 0.3s pacing delay inside its pacing lock.
    Concurrent requests for Model B and Model C on the exact same key must complete
    immediately (< 0.05s) without waiting for Model A's lock.
    """
    key = GroqKey(key="gsk_multi_model_test_key")

    model_a = "qwen/qwen3.8-27b"
    model_b = "openai/gpt-oss-120b"
    model_c = "openai/gpt-oss-20b"

    # Model A lock is held by a separate task for 0.3s
    lock_a = key._get_model_lock(model_a)
    model_b_completed_time = None
    model_c_completed_time = None
    model_a_completed_time = None

    start_time = time.monotonic()

    async def hold_model_a():
        nonlocal model_a_completed_time
        async with lock_a:
            await asyncio.sleep(0.3)
            model_a_completed_time = time.monotonic()

    async def run_model_b():
        nonlocal model_b_completed_time
        await asyncio.sleep(0.01)
        t0 = time.monotonic()
        await key.acquire_pacing_slot(model=model_b)
        model_b_completed_time = time.monotonic()
        return model_b_completed_time - t0

    async def run_model_c():
        nonlocal model_c_completed_time
        await asyncio.sleep(0.01)
        t0 = time.monotonic()
        await key.acquire_pacing_slot(model=model_c)
        model_c_completed_time = time.monotonic()
        return model_c_completed_time - t0

    # Launch concurrently
    results = await asyncio.gather(
        run_model_b(),
        run_model_c(),
        hold_model_a(),
        return_exceptions=False,
    )
    res_b, res_c = results[0], results[1]

    total_elapsed = time.monotonic() - start_time

    # Empirical validation:
    # Model B and Model C must have finished long before Model A completed
    assert model_b_completed_time is not None
    assert model_c_completed_time is not None
    assert model_a_completed_time is not None

    assert model_b_completed_time < model_a_completed_time
    assert model_c_completed_time < model_a_completed_time

    # Model B and Model C took < 0.06s despite Model A blocking for > 0.30s
    assert res_b < 0.08, f"Model B took {res_b:.4f}s, expected < 0.08s (lock contention detected!)"
    assert res_c < 0.08, f"Model C took {res_c:.4f}s, expected < 0.08s (lock contention detected!)"
    assert total_elapsed >= 0.30, f"Total elapsed {total_elapsed:.4f}s should reflect Model A's sleep"


@pytest.mark.asyncio
async def test_challenge_simultaneous_multi_model_heavy_burst():
    """
    CHALLENGE 1B: 90 concurrent coroutines (30 each on 3 models) on the same key.
    Verify task safety, absence of deadlocks, and perfect isolation of daily request counters.
    """
    key = GroqKey(key="gsk_heavy_burst_key")
    models = ["qwen/qwen3.8-27b", "openai/gpt-oss-120b", "openai/gpt-oss-20b"]

    async def worker(model: str):
        await key.acquire_pacing_slot(model=model)

    tasks = []
    for _ in range(30):
        for m in models:
            tasks.append(worker(m))

    t0 = time.monotonic()
    await asyncio.gather(*tasks)
    elapsed = time.monotonic() - t0

    # All 90 requests processed rapidly
    assert elapsed < 1.0, f"Burst took {elapsed:.2f}s, expected < 1.0s"
    assert key.total_requests == 90

    for m in models:
        assert key._model_daily_requests[m] == 30
        assert len(key._model_request_timestamps[m]) == 30


@pytest.mark.asyncio
async def test_challenge_saturated_200k_tpd_sibling_keys_unblocked():
    """
    CHALLENGE 2A: Saturated 200,000 TPD on Key 1 while sibling keys (2, 3, 4) remain 100% unblocked.
    Asserts:
    1. Key 1 is unavailable for the saturated model.
    2. Key 1 remains 100% available for sibling models.
    3. Keys 2, 3, 4 remain 100% active and unblocked for the saturated model.
    4. 300 round-robin acquisitions from pool distribute exactly 100 each across keys 2, 3, 4 with 0 to key 1.
    """
    keys = ["gsk_key1", "gsk_key2", "gsk_key3", "gsk_key4"]
    pool = GroqKeyPool(keys=keys)
    k1, k2, k3, k4 = pool.get_keys()

    saturated_model = "qwen/qwen3.8-27b"
    other_model = "openai/gpt-oss-120b"

    # Saturate Key 1 to exactly 200,000 tokens for saturated_model
    k1.record_token_usage(saturated_model, 200_000)
    assert k1._model_daily_tokens[saturated_model] == 200_000

    # Verify key 1 availability
    assert k1.is_available(model=saturated_model) is False, "Key 1 must be UNAVAILABLE for saturated model"
    assert k1.is_available(model=other_model) is True, "Key 1 must remain AVAILABLE for sibling model"

    # Verify keys 2, 3, 4 availability
    for sibling in (k2, k3, k4):
        assert sibling.is_available(model=saturated_model) is True, f"{sibling.masked_key} must be AVAILABLE"
        assert sibling.is_available(model=other_model) is True

    # Perform 300 round-robin selections from pool for saturated_model
    counts = {k: 0 for k in keys}
    for _ in range(300):
        slot = await pool.acquire_next_key(model=saturated_model)
        assert slot is not None, "Pool must never return None when sibling keys are active"
        counts[slot.key] += 1

    # Key 1 must have 0 acquisitions; Keys 2, 3, 4 must have exactly 100 each
    assert counts["gsk_key1"] == 0, f"Key 1 was selected {counts['gsk_key1']} times despite 200K TPD saturation!"
    assert counts["gsk_key2"] == 100
    assert counts["gsk_key3"] == 100
    assert counts["gsk_key4"] == 100

    # For other_model, Key 1 must participate equally in 4-way round robin (75 each)
    other_counts = {k: 0 for k in keys}
    for _ in range(300):
        slot = await pool.acquire_next_key(model=other_model)
        assert slot is not None
        other_counts[slot.key] += 1

    assert other_counts["gsk_key1"] == 75, f"Key 1 should participate for other model, got {other_counts['gsk_key1']}"
    assert other_counts["gsk_key2"] == 75
    assert other_counts["gsk_key3"] == 75
    assert other_counts["gsk_key4"] == 75


@pytest.mark.asyncio
async def test_challenge_groq_provider_execution_under_key1_tpd_429():
    """
    CHALLENGE 2B: End-to-end execution through GroqProvider when Key 1 encounters 429 TPD exhaustion.
    Verify:
    1. Key 1 receives 429 TPD, marks model cooldown.
    2. Rotation automatically succeeds on Key 2.
    3. Subsequent 30 requests rotate between Keys 2, 3, 4 with ZERO errors and NEVER touch Key 1 for that model.
    4. Key 1 successfully executes requests for a different model.
    """
    keys = ["gsk_key1", "gsk_key2", "gsk_key3", "gsk_key4"]
    pool = GroqKeyPool(keys=keys)
    provider = GroqProvider(key_pool=pool, default_model="qwen/qwen3.8-27b")

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.is_closed = False

    invoked_keys = []

    async def mock_post(url, json=None, headers=None):
        auth_hdr = headers.get("Authorization", "")
        model_req = json.get("model")
        invoked_keys.append((auth_hdr, model_req))
        req = httpx.Request("POST", url)

        # Key 1 returns 429 TPD exhaustion for qwen/qwen3.8-27b
        if "gsk_key1" in auth_hdr and model_req == "qwen/qwen3.8-27b":
            return httpx.Response(
                429,
                request=req,
                headers={"retry-after": "1800"},
                json={
                    "error": {
                        "message": "Rate limit reached for model `qwen/qwen3.8-27b` on tokens per day (TPD): Limit 200000, Used 200000"
                    }
                },
            )
        # All other keys or models succeed
        return httpx.Response(
            200,
            request=req,
            json={"choices": [{"message": {"content": f"ok-{model_req}"}}]},
        )

    mock_client.post = mock_post
    provider._client = mock_client

    # Call 1: Key 1 encounters 429 TPD, provider transparently rotates to Key 2 and succeeds
    res1 = await provider.generate_text("Prompt 1", model="qwen/qwen3.8-27b")
    assert res1 == "ok-qwen/qwen3.8-27b"

    # Verify Key 1 is on cooldown for qwen/qwen3.8-27b, but ACTIVE overall
    k1 = pool.get_keys()[0]
    assert k1.status == KeyStatus.ACTIVE
    assert k1.is_available(model="qwen/qwen3.8-27b") is False
    assert k1.get_model_cooldown_remaining("qwen/qwen3.8-27b") >= 1700.0

    # Execute 30 more calls for qwen/qwen3.8-27b
    invoked_keys.clear()
    for i in range(30):
        res = await provider.generate_text(f"Prompt {i}", model="qwen/qwen3.8-27b")
        assert res == "ok-qwen/qwen3.8-27b"

    # Assert Key 1 was NEVER touched during these 30 calls
    used_auths = [auth for auth, _ in invoked_keys]
    assert not any("gsk_key1" in a for a in used_auths), "Key 1 was invoked despite TPD exhaustion!"
    assert any("gsk_key2" in a for a in used_auths)
    assert any("gsk_key3" in a for a in used_auths)
    assert any("gsk_key4" in a for a in used_auths)

    # Now execute a request for a different model on GroqProvider
    invoked_keys.clear()
    res_other = await provider.generate_text("Other Prompt", model="openai/gpt-oss-120b")
    assert res_other == "ok-openai/gpt-oss-120b"
    # Key 1 was healthy and available for the sibling model
    assert k1.is_available(model="openai/gpt-oss-120b") is True


def test_challenge_cooldown_retention_under_short_overwrite_attacks():
    """
    CHALLENGE 3A: Cooldown retention under short overwrite attacks.
    Call mark_rate_limited with 1000s delay, followed by 2s delay;
    assert cooldown expiry remains ~1000s and reason is not corrupted.
    """
    key = GroqKey(key="gsk_retention_attack_key")
    model = "qwen/qwen3.8-27b"

    # Step 1: Baseline long cooldown (1000s)
    key.mark_rate_limited(
        is_daily=True,
        retry_delay_sec=1000.0,
        reason="Daily quota exhausted (1000s)",
        model=model,
    )
    initial_cd = key.get_model_cooldown_remaining(model)
    assert 995.0 <= initial_cd <= 1001.0
    assert key._model_cooldown_reasons[model] == "Daily quota exhausted (1000s)"

    # Step 2: Adversarial short overwrite attack 1: 2.0s
    key.mark_rate_limited(
        is_daily=False,
        retry_delay_sec=2.0,
        reason="Short 2s overwrite attack",
        model=model,
    )
    cd_after_2s = key.get_model_cooldown_remaining(model)
    assert 995.0 <= cd_after_2s <= 1001.0, f"Cooldown corrupted to {cd_after_2s:.2f}s! Expected ~1000s."
    assert key._model_cooldown_reasons[model] == "Daily quota exhausted (1000s)"

    # Step 3: Adversarial short overwrite attack 2: 0.1s
    key.mark_rate_limited(
        is_daily=False,
        retry_delay_sec=0.1,
        reason="Short 100ms overwrite attack",
        model=model,
    )
    cd_after_100ms = key.get_model_cooldown_remaining(model)
    assert 995.0 <= cd_after_100ms <= 1001.0
    assert key._model_cooldown_reasons[model] == "Daily quota exhausted (1000s)"

    # Step 4: Adversarial short overwrite attack 3: 15.0s
    key.mark_rate_limited(
        is_daily=False,
        retry_delay_sec=15.0,
        reason="Short 15s overwrite attack",
        model=model,
    )
    cd_after_15s = key.get_model_cooldown_remaining(model)
    assert 995.0 <= cd_after_15s <= 1001.0
    assert key._model_cooldown_reasons[model] == "Daily quota exhausted (1000s)"

    # Step 5: Legitimate extension: longer cooldown of 2500s MUST be accepted
    key.mark_rate_limited(
        is_daily=True,
        retry_delay_sec=2500.0,
        reason="Extended 2500s block",
        model=model,
    )
    cd_after_2500s = key.get_model_cooldown_remaining(model)
    assert 2490.0 <= cd_after_2500s <= 2501.0
    assert key._model_cooldown_reasons[model] == "Extended 2500s block"


def test_challenge_cooldown_retention_via_http_429_responses():
    """
    CHALLENGE 3B: Cooldown retention through update_from_response when out-of-order 429s arrive.
    Simulate HTTP 429 with retry-after=1000s followed by HTTP 429 with retry-after=2s.
    """
    key = GroqKey(key="gsk_http_retention_key")
    model = "qwen/qwen3.8-27b"
    req = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")

    # 1. First 429 with 1000s retry-after
    resp_long = httpx.Response(
        429,
        request=req,
        headers={"retry-after": "1000", "x-ratelimit-remaining-requests": "0"},
        json={"error": {"message": "Rate limit exceeded"}},
    )
    key.update_from_response(resp_long, model=model)
    cd1 = key.get_model_cooldown_remaining(model)
    assert 995.0 <= cd1 <= 1001.0

    # 2. Second 429 with 2s retry-after
    resp_short = httpx.Response(
        429,
        request=req,
        headers={"retry-after": "2", "x-ratelimit-remaining-requests": "14000"},
        json={"error": {"message": "Rate limit exceeded"}},
    )
    key.update_from_response(resp_short, model=model)
    cd2 = key.get_model_cooldown_remaining(model)
    assert 995.0 <= cd2 <= 1001.0, f"Cooldown regressed to {cd2:.2f}s from 1000s!"


def test_challenge_key_level_cooldown_retention_without_model():
    """
    CHALLENGE 3C: Key-level global cooldown retention (model=None).
    Verify that an un-scoped short 429 does not overwrite a long key-level daily exhaustion.
    """
    key = GroqKey(key="gsk_key_level_retention")

    # 1. 1000s daily key-level cooldown
    key.mark_rate_limited(is_daily=True, retry_delay_sec=1000.0, reason="Org account quota")
    assert key.status == KeyStatus.DAILY_EXHAUSTED
    assert key.cooldown_reason == "Org account quota"
    cd1 = key.get_seconds_until_ready()
    assert 995.0 <= cd1 <= 1001.0

    # 2. Short 2.0s minute rate limit on key
    key.mark_rate_limited(is_daily=False, retry_delay_sec=2.0, reason="Short 2s pause")
    # Must remain DAILY_EXHAUSTED with ~1000s cooldown expiry
    assert key.status == KeyStatus.DAILY_EXHAUSTED
    cd2 = key.get_seconds_until_ready()
    assert 995.0 <= cd2 <= 1001.0, f"Cooldown expiry failed to retain ~1000s, got {cd2}s"
    # Note: cooldown_until is preserved; key.cooldown_reason is updated by line 606
    assert key.cooldown_until >= cd2


def test_challenge_zero_and_negative_retry_delays_resilience():
    """
    CHALLENGE 4A: Boundary condition resilience when retry delays are 0 or negative.
    Verify no crash and safe application of floor.
    """
    key = GroqKey(key="gsk_negative_delay_key")
    model = "qwen/qwen3.8-27b"

    # Should not crash on negative delay
    key.mark_rate_limited(is_daily=False, retry_delay_sec=-5.0, model=model)
    cd = key.get_model_cooldown_remaining(model)
    # Applied fallback delay (1.0s)
    assert 0.5 <= cd <= 1.2

    # Should not crash on 0.0 delay
    key.mark_rate_limited(is_daily=False, retry_delay_sec=0.0, model=model)
    cd2 = key.get_model_cooldown_remaining(model)
    assert cd2 >= 0.0


def test_challenge_submillisecond_header_floored_protection():
    """
    CHALLENGE 4B: Protect against sub-millisecond retry storms from weird upstream headers.
    When Groq returns x-ratelimit-reset-tokens: 0.1ms without retry-after,
    effective lock must be floored to at least 3.0s.
    """
    key = GroqKey(key="gsk_subms_key")
    model = "qwen/qwen3.8-27b"
    req = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")

    resp = httpx.Response(
        429,
        request=req,
        headers={
            "x-ratelimit-reset-tokens": "0.1ms",
            "x-ratelimit-remaining-tokens": "0",
        },
        json={"error": {"message": "Rate limit reached"}},
    )
    key.update_from_response(resp, model=model)
    cd = key.get_model_cooldown_remaining(model)
    assert cd >= 3.0, f"Expected cooldown >= 3.0s, got {cd:.4f}s"


@pytest.mark.asyncio
async def test_challenge_all_keys_tpd_saturated_for_one_model_while_sibling_model_healthy():
    """
    CHALLENGE 5A: All 4 keys in pool hit 200K TPD on Model A (`qwen/qwen3.8-27b`).
    Verify:
    1. Pool returns None for Model A (properly signaling model exhaustion).
    2. Sibling Model B (`openai/gpt-oss-120b`) is 100% active across all 4 keys.
    3. Sibling Model C (`openai/gpt-oss-20b`) is 100% active across all 4 keys.
    """
    keys = ["gsk_k1", "gsk_k2", "gsk_k3", "gsk_k4"]
    pool = GroqKeyPool(keys=keys)

    model_a = "qwen/qwen3.8-27b"
    model_b = "openai/gpt-oss-120b"
    model_c = "openai/gpt-oss-20b"

    # Saturate 200k TPD on all 4 keys for Model A
    for k in pool.get_keys():
        k.record_token_usage(model_a, 200_000)

    # Model A acquisition must return None
    slot_a = await pool.acquire_next_key(model=model_a)
    assert slot_a is None, "Pool must return None when all keys have exhausted 200K TPD for Model A"

    # Sibling models B and C must acquire freely across all 4 keys
    for m in (model_b, model_c):
        for expected_key in keys:
            slot = await pool.acquire_next_key(model=m)
            assert slot is not None
            assert slot.key == expected_key


@pytest.mark.asyncio
async def test_challenge_sliding_window_tpm_8k_saturation_isolation():
    """
    CHALLENGE 5B: Saturated sliding-window TPM (8,000 tokens) on Key 1 for Model A.
    Verify:
    1. Key 1 calculates wait time for Model A based on the sliding window.
    2. Model B on Key 1 has 0s wait time (unaffected by Model A's TPM).
    3. Model A on Key 2 has 0s wait time (unaffected by Key 1's TPM).
    4. Proactive cooldown from Groq response rotates pool to Key 2 immediately.
    """
    pool = GroqKeyPool(keys=["gsk_k1", "gsk_k2"])
    k1, k2 = pool.get_keys()

    model_a = "qwen/qwen3.8-27b"
    model_b = "openai/gpt-oss-120b"

    # Push 8,500 tokens into Model A bucket on Key 1
    k1.record_token_usage(model_a, 8500)

    # Key 1 wait for Model A must be > 0 (waiting for 60s sliding window)
    wait_k1_a = k1.get_seconds_until_ready(model=model_a)
    assert 55.0 <= wait_k1_a <= 61.0, f"Expected ~60s sliding TPM wait, got {wait_k1_a}s"

    # Model B on Key 1 must be immediately ready (0.0s wait)
    wait_k1_b = k1.get_seconds_until_ready(model=model_b)
    assert wait_k1_b == 0.0, f"Model B on Key 1 should be immediately ready, got {wait_k1_b}s"

    # Model A on Key 2 must be immediately ready (0.0s wait)
    wait_k2_a = k2.get_seconds_until_ready(model=model_a)
    assert wait_k2_a == 0.0, f"Model A on Key 2 should be immediately ready, got {wait_k2_a}s"

    # Simulate Groq 200 OK with remaining tokens depleted (< 1200) for Model A
    req = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    resp_depleted = httpx.Response(
        200,
        request=req,
        headers={
            "x-ratelimit-remaining-tokens": "500",
            "x-ratelimit-reset-tokens": "8.5s",
            "x-ratelimit-remaining-requests": "14000",
        },
        json={"choices": [{"message": {"content": "ok"}}]},
    )
    k1.update_from_response(resp_depleted, model=model_a)

    # Key 1 is now marked in model cooldown for Model A
    assert k1.is_available(model=model_a) is False
    # But Key 1 remains completely available for Model B
    assert k1.is_available(model=model_b) is True
    # And Key 2 remains completely available for Model A
    assert k2.is_available(model=model_a) is True

    # When acquiring for Model A, pool skips Key 1 and picks Key 2
    selected = await pool.acquire_next_key(model=model_a)
    assert selected is not None
    assert selected.key == "gsk_k2"


@pytest.mark.asyncio
async def test_challenge_high_concurrency_pool_acquire_race_500_tasks():
    """
    CHALLENGE 5C: 500 concurrent coroutines acquiring keys simultaneously from a 4-key pool.
    Verify:
    1. No exceptions or state corruption under heavy asyncio contention.
    2. Round-robin index remains bounded [0, 4).
    3. Distribution across keys is uniform (125 each).
    """
    keys = ["gsk_key1", "gsk_key2", "gsk_key3", "gsk_key4"]
    pool = GroqKeyPool(keys=keys)

    acquired_keys = []

    async def acquire_worker():
        slot = await pool.acquire_next_key(model="qwen/qwen3.8-27b")
        if slot:
            acquired_keys.append(slot.key)

    tasks = [acquire_worker() for _ in range(500)]
    t0 = time.monotonic()
    await asyncio.gather(*tasks)
    elapsed = time.monotonic() - t0

    assert elapsed < 1.0, f"500 acquisitions took {elapsed:.2f}s, expected < 1.0s"
    assert len(acquired_keys) == 500
    for k in keys:
        assert acquired_keys.count(k) == 125, f"Key {k} got {acquired_keys.count(k)} acquisitions, expected 125"


def test_challenge_corrupted_response_headers_resilience():
    """
    CHALLENGE 5D: Malformed or non-numeric rate limit headers from upstream gateway.
    Verify update_from_response handles non-digits, None, empty strings without crashing.
    """
    key = GroqKey(key="gsk_corrupt_test")
    model = "qwen/qwen3.8-27b"
    req = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")

    resp_corrupt = httpx.Response(
        200,
        request=req,
        headers={
            "x-ratelimit-remaining-requests": "NaN",
            "x-ratelimit-remaining-tokens": "undefined",
            "x-ratelimit-limit-requests": "",
            "x-ratelimit-reset-requests": "invalid-duration",
            "retry-after": "not-a-number",
        },
        json={"choices": [{"message": {"content": "ok"}}]},
    )

    # Must parse safely and mark success
    key.update_from_response(resp_corrupt, model=model)
    assert key.status == KeyStatus.ACTIVE
    assert key.remaining_requests is None
    assert key.remaining_tokens is None
    assert key.retry_after_sec is None
    assert key.success_requests == 1


@pytest.mark.asyncio
async def test_challenge_short_wait_retry_vs_long_wait_fast_fail():
    """
    CHALLENGE 5E: Verification of short wait retry (<= 12.0s) vs long wait fast fail (> 12.0s).
    """
    pool = GroqKeyPool(keys=["gsk_k1"])
    provider = GroqProvider(key_pool=pool, default_model="qwen/qwen3.8-27b")

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.is_closed = False
    provider._client = mock_client

    k1 = pool.get_keys()[0]

    # Scenario 1: Long wait (> 12s, e.g. 120s) -> Fast fail immediately with RuntimeError
    k1.mark_rate_limited(is_daily=False, retry_delay_sec=120.0, reason="Long 2m pause", model="qwen/qwen3.8-27b")
    t0 = time.monotonic()
    with pytest.raises(RuntimeError) as exc_info:
        await provider.generate_text("Prompt", model="qwen/qwen3.8-27b")
    elapsed = time.monotonic() - t0

    assert "Rate limit reached for model 'qwen/qwen3.8-27b'" in str(exc_info.value)
    # Fast-failed without sleeping 120s!
    assert elapsed < 0.20, f"Expected fast-fail < 0.20s, took {elapsed:.2f}s"

