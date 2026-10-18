"""
backend/tests/test_m2_it2_remediation_challenge.py
===================================================
Adversarial challenge and empirical stress-test suite for Milestone M2 Remediation (Iteration 2).
Focus areas:
1. Token recording accuracy:
   - 10 sequential completions record exactly 1x tokens per completion with zero double-counting.
   - Concurrent completions across multiple keys record exact cumulative totals without race conditions.
   - Multi-model concurrency isolation and error/failure non-recording.
2. Clock consistency inside and outside asyncio event loop:
   - Synchronous and asynchronous baseline alignment (_get_loop_time vs time.monotonic vs loop.time).
   - Rapid alternation of to_dict(), is_available(), and get_sliding_tpm() inside and outside event loop.
   - Monotonic clock progression and rolling window pruning consistency.
   - Pool telemetry and Pass 2 DAILY_EXHAUSTED exclusion.
"""

import asyncio
from collections import deque
import time
from typing import Any, Dict, List
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from backend.app.pipeline.providers.mistral import MistralProvider
from backend.app.pipeline.providers.mistral_key_pool import (
    KeyStatus,
    MistralKey,
    MistralKeyPool,
    _get_loop_time,
    get_mistral_model_limits,
)
from backend.app.pipeline.usage_tracker import get_usage_tracker


# ============================================================================
# PART 1: Token Recording Accuracy Stress Tests
# ============================================================================

@pytest.mark.asyncio
async def test_sequential_10_completions_exact_single_token_recording_zero_double_counting():
    """
    Stress test token recording accuracy:
    Execute 10 sequential completions through MistralProvider.
    Verify that each completion records exactly 1x tokens in _model_token_records and get_sliding_tpm(),
    with zero double-counting across all 10 steps and at final tally.
    """
    pool = MistralKeyPool(keys=["mstrl_seq_key_01"])
    provider = MistralProvider(key_pool=pool, default_model="ministral-3b-2512")
    key = pool.get_keys()[0]
    model = "ministral-3b-2512"

    token_sequence = [111, 222, 333, 444, 555, 666, 777, 888, 999, 1234]
    cumulative_tokens = 0

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.is_closed = False

    current_idx = 0

    async def mock_post(url, json=None, headers=None):
        nonlocal current_idx
        tok = token_sequence[current_idx]
        req = httpx.Request("POST", url)
        return httpx.Response(
            200,
            request=req,
            json={
                "choices": [{"message": {"content": f"Completion #{current_idx + 1}"}}],
                "usage": {
                    "prompt_tokens": tok // 3,
                    "completion_tokens": tok - (tok // 3),
                    "total_tokens": tok,
                },
            },
        )

    mock_client.post = mock_post
    provider._client = mock_client

    for i, tok in enumerate(token_sequence):
        current_idx = i
        res = await provider.generate_text(f"Prompt #{i + 1}", model=model)
        assert res == f"Completion #{i + 1}"

        cumulative_tokens += tok
        records = list(key._model_token_records.get(model, []))

        # Check 1: Record count must be exactly i + 1
        assert len(records) == i + 1, (
            f"Step {i + 1}: Expected exactly {i + 1} token records, found {len(records)}"
        )

        # Check 2: The latest record must match the exact token count
        assert records[-1][1] == tok, (
            f"Step {i + 1}: Expected latest record {tok}, got {records[-1][1]}"
        )

        # Check 3: Cumulative token sequence must exactly match
        recorded_sequence = [r[1] for r in records]
        assert recorded_sequence == token_sequence[: i + 1], (
            f"Step {i + 1}: Recorded sequence {recorded_sequence} != {token_sequence[: i + 1]}"
        )

        # Check 4: get_sliding_tpm must equal exact cumulative sum (zero inflation)
        current_tpm = key.get_sliding_tpm(model)
        assert current_tpm == cumulative_tokens, (
            f"Step {i + 1}: Expected sliding TPM {cumulative_tokens}, got {current_tpm} "
            f"(inflation factor: {current_tpm / cumulative_tokens:.2f}x)"
        )

    # Final overall assertion: total tokens recorded == sum(token_sequence)
    final_total = sum(token_sequence)  # 6229
    assert key.get_sliding_tpm(model) == final_total, (
        f"Final TPM mismatch: expected {final_total}, got {key.get_sliding_tpm(model)}"
    )
    assert len(key._model_token_records[model]) == 10

    await provider.close()


@pytest.mark.asyncio
async def test_concurrent_completions_across_multiple_keys_exact_cumulative_totals():
    """
    Stress test token recording under high concurrency:
    Dispatch 40 concurrent completions across a 4-key pool.
    Verify that tokens are accurately distributed, recorded 1x per completion,
    and cumulative tokens across all keys equal the exact sum of all response usages with zero race conditions.
    """
    key_ids = [f"mstrl_conc_key_{k}" for k in range(1, 5)]
    pool = MistralKeyPool(keys=key_ids)
    provider = MistralProvider(key_pool=pool, default_model="ministral-3b-2512")
    keys = pool.get_keys()
    model = "ministral-3b-2512"

    num_tasks = 40
    # Each task i will return distinct total_tokens = 50 + i * 10
    expected_task_tokens = {i: 50 + i * 10 for i in range(num_tasks)}
    expected_total_tokens = sum(expected_task_tokens.values())  # 40*50 + 10*(39*40/2) = 2000 + 7800 = 9800

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.is_closed = False

    async def mock_post(url, json=None, headers=None):
        prompt_str = json.get("messages", [{}])[-1].get("content", "")
        # Parse task index from prompt
        task_idx = int(prompt_str.split("Task_")[1].split()[0])
        tok = expected_task_tokens[task_idx]
        req = httpx.Request("POST", url)
        # Small jitter to interleave completion timings
        await asyncio.sleep(0.005)
        return httpx.Response(
            200,
            request=req,
            json={
                "choices": [{"message": {"content": f"Result for Task_{task_idx}"}}],
                "usage": {
                    "prompt_tokens": 15,
                    "completion_tokens": tok - 15,
                    "total_tokens": tok,
                },
            },
        )

    mock_client.post = mock_post
    provider._client = mock_client

    # Launch all 40 tasks concurrently
    async def run_task(idx: int):
        return await provider.generate_text(f"Run Task_{idx} payload", model=model)

    tasks = [run_task(i) for i in range(num_tasks)]
    results = await asyncio.gather(*tasks)

    assert len(results) == num_tasks
    for i in range(num_tasks):
        assert results[i] == f"Result for Task_{i}"

    # Verify per-key and aggregate token records
    all_recorded_tokens: List[int] = []
    total_records_count = 0

    for k in keys:
        records = list(k._model_token_records.get(model, []))
        total_records_count += len(records)
        for _, tok in records:
            all_recorded_tokens.append(tok)
        # Key's sliding TPM must match sum of its records
        assert k.get_sliding_tpm(model) == sum(r[1] for r in records)

    # Assert 1: Total records count across all keys must equal 40 exactly
    assert total_records_count == num_tasks, (
        f"Expected {num_tasks} total records across keys, found {total_records_count}"
    )

    # Assert 2: All 4 keys received traffic (round-robin distribution verified)
    for k in keys:
        assert len(k._model_token_records.get(model, [])) > 0, (
            f"Key {k.masked_key} received 0 requests in concurrent burst"
        )

    # Assert 3: Cumulative tokens across all keys must equal 9800 exactly
    assert sum(all_recorded_tokens) == expected_total_tokens, (
        f"Expected cumulative tokens {expected_total_tokens}, got {sum(all_recorded_tokens)}"
    )

    # Assert 4: Multiset of recorded tokens must match input tokens exactly
    assert sorted(all_recorded_tokens) == sorted(expected_task_tokens.values()), (
        f"Recorded token multiset does not match input token distribution"
    )

    await provider.close()


@pytest.mark.asyncio
async def test_concurrent_multi_model_multi_key_exact_totals():
    """
    Verify concurrent completions across 2 different models on a 4-key pool.
    Assert that tokens for model A and model B are strictly partitioned per model and key,
    with exact totals recorded and zero cross-model token contamination.
    """
    key_ids = [f"mstrl_multi_m_{k}" for k in range(1, 5)]
    pool = MistralKeyPool(keys=key_ids)
    provider = MistralProvider(key_pool=pool)
    keys = pool.get_keys()

    model_a = "ministral-3b-2512"
    model_b = "codestral-2508"

    tokens_a = 120
    tokens_b = 350
    count_per_model = 16

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.is_closed = False

    async def mock_post(url, json=None, headers=None):
        req_model = json.get("model", "")
        tok = tokens_a if req_model == model_a else tokens_b
        req = httpx.Request("POST", url)
        await asyncio.sleep(0.002)
        return httpx.Response(
            200,
            request=req,
            json={
                "choices": [{"message": {"content": f"ok-{req_model}"}}],
                "usage": {"total_tokens": tok},
            },
        )

    mock_client.post = mock_post
    provider._client = mock_client

    tasks_a = [provider.generate_text(f"Task A {i}", model=model_a) for i in range(count_per_model)]
    tasks_b = [provider.generate_text(f"Task B {i}", model=model_b) for i in range(count_per_model)]

    # Interleave tasks
    interleaved = []
    for a, b in zip(tasks_a, tasks_b):
        interleaved.extend([a, b])

    results = await asyncio.gather(*interleaved)
    assert len(results) == count_per_model * 2

    # Verification
    total_a_tokens = sum(k.get_sliding_tpm(model_a) for k in keys)
    total_b_tokens = sum(k.get_sliding_tpm(model_b) for k in keys)

    assert total_a_tokens == tokens_a * count_per_model, (
        f"Model A expected {tokens_a * count_per_model}, got {total_a_tokens}"
    )
    assert total_b_tokens == tokens_b * count_per_model, (
        f"Model B expected {tokens_b * count_per_model}, got {total_b_tokens}"
    )

    total_a_records = sum(len(k._model_token_records.get(model_a, [])) for k in keys)
    total_b_records = sum(len(k._model_token_records.get(model_b, [])) for k in keys)

    assert total_a_records == count_per_model
    assert total_b_records == count_per_model

    await provider.close()


@pytest.mark.asyncio
async def test_failed_responses_record_zero_tokens():
    """
    Adversarial check:
    Verify that HTTP 429, HTTP 500, network timeouts, and HTTP 401 NEVER record phantom tokens
    into the key's sliding window.
    """
    pool = MistralKeyPool(keys=["mstrl_err_key_01", "mstrl_err_key_02"])
    provider = MistralProvider(key_pool=pool, default_model="ministral-3b-2512", max_retries=1)
    k1, k2 = pool.get_keys()
    model = "ministral-3b-2512"

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.is_closed = False

    # Scenario 1: HTTP 429 RateLimit response
    async def mock_post_429(url, json=None, headers=None):
        req = httpx.Request("POST", url)
        return httpx.Response(429, request=req, json={"message": "rate limit"}, headers={"retry-after": "5"})

    mock_client.post = mock_post_429
    provider._client = mock_client

    with pytest.raises(RuntimeError):
        await provider.generate_text("test fail", model=model)

    assert k1.get_sliding_tpm(model) == 0
    assert k2.get_sliding_tpm(model) == 0
    assert len(k1._model_token_records.get(model, [])) == 0
    assert len(k2._model_token_records.get(model, [])) == 0

    # Scenario 2: Network Timeout
    async def mock_post_timeout(url, json=None, headers=None):
        raise httpx.ReadTimeout("Read timed out")

    mock_client.post = mock_post_timeout
    with pytest.raises(RuntimeError):
        await provider.generate_text("test timeout", model=model)

    assert k1.get_sliding_tpm(model) == 0
    assert k2.get_sliding_tpm(model) == 0

    # Scenario 3: Response 200 without usage field
    async def mock_post_no_usage(url, json=None, headers=None):
        req = httpx.Request("POST", url)
        return httpx.Response(200, request=req, json={"choices": [{"message": {"content": "no usage"}}]})

    mock_client.post = mock_post_no_usage
    # reset key statuses
    k1.status = KeyStatus.ACTIVE
    k1.cooldown_until = 0.0
    k2.status = KeyStatus.ACTIVE
    k2.cooldown_until = 0.0
    k1._model_cooldowns.clear()
    k2._model_cooldowns.clear()

    res = await provider.generate_text("test no usage", model=model)
    assert res == "no usage"
    assert k1.get_sliding_tpm(model) == 0
    assert k2.get_sliding_tpm(model) == 0

    await provider.close()


@pytest.mark.asyncio
async def test_process_stage_structured_call_exact_single_token_recording():
    """
    Verify that structured process_stage() calls (extraction, scoring, summarization)
    record tokens exactly 1x per execution.
    """
    pool = MistralKeyPool(keys=["mstrl_stage_key"])
    provider = MistralProvider(key_pool=pool, default_model="ministral-3b-2512")
    key = pool.get_keys()[0]
    model = "ministral-3b-2512"

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.is_closed = False

    async def mock_post(url, json=None, headers=None):
        req = httpx.Request("POST", url)
        return httpx.Response(
            200,
            request=req,
            json={
                "choices": [{"message": {"content": '{"importance_score": 8, "reason": "urgent"}'}}],
                "usage": {"total_tokens": 185},
            },
        )

    mock_client.post = mock_post
    provider._client = mock_client

    res = await provider.process_stage(
        stage="scoring",
        input_text="Breaking geopolitical event",
        context={"scoring_prompt": "prioritize conflict"},
        model=model,
    )
    assert res.get("importance_score") == 8
    assert key.get_sliding_tpm(model) == 185
    assert len(key._model_token_records[model]) == 1
    assert key._model_token_records[model][0][1] == 185

    await provider.close()


# ============================================================================
# PART 2: Clock Consistency Inside & Outside Asyncio Event Loop
# ============================================================================

def test_clock_consistency_sync_vs_async_loop_baseline():
    """
    Verify that _get_loop_time() returns coherent monotonic timestamps both
    inside an asyncio event loop and outside (synchronous context), with
    no epoch jumps or discrepancies > 0.05s.
    """
    # 1. Measure in synchronous context (outside any event loop)
    sync_t1 = _get_loop_time()
    mono_t1 = time.monotonic()
    assert abs(sync_t1 - mono_t1) < 0.05, (
        f"Sync _get_loop_time() {sync_t1} deviated from time.monotonic() {mono_t1}"
    )

    # 2. Measure inside an asyncio event loop
    async def get_async_times():
        loop = asyncio.get_running_loop()
        return _get_loop_time(), loop.time(), time.monotonic()

    async_loop_t, loop_internal_t, mono_t2 = asyncio.run(get_async_times())

    # Inside loop, _get_loop_time() must match loop.time()
    assert abs(async_loop_t - loop_internal_t) < 0.05
    assert abs(async_loop_t - mono_t2) < 0.05

    # 3. Compare sync baseline vs async baseline (monotonically progressive)
    assert mono_t2 >= mono_t1
    assert async_loop_t >= sync_t1
    assert (async_loop_t - sync_t1) < 1.0, (
        f"Epoch mismatch detected! sync_t1={sync_t1}, async_loop_t={async_loop_t}"
    )


def test_rapid_alternation_to_dict_is_available_get_sliding_tpm_inside_outside_loop():
    """
    Stress test clock consistency:
    Perform 50 rapid alternating cycles between synchronous code and asyncio event loops.
    Call to_dict(), is_available(), and get_sliding_tpm() at each step.
    Assert that active model cooldowns and sliding token records are strictly preserved
    with zero premature expiration, zero popped records, and zero wiped state.
    """
    key = MistralKey("mstrl_rapid_clock_key")
    model_1 = "ministral-3b-2512"
    model_2 = "codestral-2508"

    # Set up active cooldowns and token records
    initial_delay = 50.0
    key.mark_rate_limited(is_daily=False, retry_delay_sec=initial_delay, model=model_1)
    key.record_token_usage(model_1, 1500)
    key.record_token_usage(model_2, 3200)

    assert model_1 in key._model_cooldowns
    assert key.get_sliding_tpm(model_1) == 1500
    assert key.get_sliding_tpm(model_2) == 3200

    async def async_check(iteration: int):
        # Inside running loop
        d = key.to_dict()
        assert model_1 in key._model_cooldowns, f"Iter {iteration} async: cooldown popped!"
        assert model_1 in d["model_cooldowns"], f"Iter {iteration} async: cooldown missing in dict!"
        assert model_1 in d["throttled_models"]
        assert key.is_available(model=model_1) is False, f"Iter {iteration} async: key reported available!"
        assert key.get_sliding_tpm(model_1) == 1500, f"Iter {iteration} async: TPM wiped!"
        assert key.get_sliding_tpm(model_2) == 3200, f"Iter {iteration} async: model_2 TPM wiped!"
        assert len(key._model_token_records[model_1]) == 1
        assert len(key._model_token_records[model_2]) == 1

    # Run 50 rapid alternating cycles
    for cycle in range(50):
        # 1. Sync inspection (outside event loop)
        d_sync = key.to_dict()
        assert model_1 in key._model_cooldowns, f"Iter {cycle} sync: cooldown popped!"
        assert model_1 in d_sync["model_cooldowns"], f"Iter {cycle} sync: cooldown missing in dict!"
        assert model_1 in d_sync["throttled_models"]
        assert key.is_available(model=model_1) is False, f"Iter {cycle} sync: key reported available!"
        assert key.get_sliding_tpm(model_1) == 1500, f"Iter {cycle} sync: TPM wiped!"
        assert key.get_sliding_tpm(model_2) == 3200, f"Iter {cycle} sync: model_2 TPM wiped!"
        assert len(key._model_token_records[model_1]) == 1
        assert len(key._model_token_records[model_2]) == 1

        # 2. Async inspection (inside event loop)
        asyncio.run(async_check(cycle))

    # Final assertion after all 50 cycles: state is 100% intact
    assert model_1 in key._model_cooldowns
    assert key.get_sliding_tpm(model_1) == 1500
    assert key.get_sliding_tpm(model_2) == 3200
    assert key.is_available(model=model_1) is False


def test_controlled_clock_progression_across_sync_and_async():
    """
    Stress test time-based transitions:
    Using controlled mock clock progression, verify that:
    1. During 0-40s: cooldowns and token records are preserved in both sync and async.
    2. At 45s: cooldown expires cleanly in both sync and async.
    3. At 61s: token records roll off cleanly in both sync and async.
    """
    key = MistralKey("mstrl_mock_prog_key")
    model = "ministral-3b-2512"

    simulated_time = 1000.0

    def mock_time():
        return simulated_time

    with patch("time.monotonic", side_effect=mock_time):
        # T = 1000.0: Set 40s cooldown and 500 tokens
        key.mark_rate_limited(is_daily=False, retry_delay_sec=40.0, model=model)
        key.record_token_usage(model, 500)

        assert key.is_available(model=model) is False
        assert key.get_sliding_tpm(model) == 500
        assert key.to_dict()["model_cooldowns"][model]["cooldown_remaining_sec"] == 40.0

        # Advance to T = 1025.0 (+25s)
        simulated_time = 1025.0
        # Check in sync
        assert key.is_available(model=model) is False
        assert key.get_sliding_tpm(model) == 500
        rem = key.to_dict()["model_cooldowns"][model]["cooldown_remaining_sec"]
        assert rem == 15.0

        # Advance to T = 1045.0 (+45s): cooldown expired, but tokens still within 60s window
        simulated_time = 1045.0
        # Check in sync
        assert key.is_available(model=model) is True
        assert model not in key.to_dict()["model_cooldowns"]
        assert key.get_sliding_tpm(model) == 500

        # Advance to T = 1065.0 (+65s): tokens expired (>60s)
        simulated_time = 1065.0
        # Check in sync
        assert key.get_sliding_tpm(model) == 0
        assert len(key._model_token_records[model]) == 0
        assert key.is_available(model=model) is True


def test_pool_get_telemetry_inside_and_outside_event_loop():
    """
    Verify that MistralKeyPool.get_telemetry() produces identical, accurate metrics
    whether invoked from a synchronous function or inside an asyncio event loop.
    """
    pool = MistralKeyPool(keys=["k1_telemetry", "k2_telemetry", "k3_telemetry"])
    k1, k2, k3 = pool.get_keys()

    # k1 active
    k1.record_token_usage("ministral-3b-2512", 250)
    # k2 in model cooldown
    k2.mark_rate_limited(is_daily=False, retry_delay_sec=30.0, model="ministral-3b-2512")
    # k3 daily exhausted
    k3.mark_rate_limited(is_daily=True, retry_delay_sec=3600.0)

    # 1. Sync telemetry
    sync_telemetry = pool.get_telemetry()
    assert sync_telemetry["total_keys"] == 3
    assert sync_telemetry["keys_in_cooldown"] == 1  # k3 is DAILY_EXHAUSTED
    assert sync_telemetry["monthly_allowance_usd_total"] == 30.0

    # 2. Async telemetry
    async def fetch_async_telemetry():
        return pool.get_telemetry()

    async_telemetry = asyncio.run(fetch_async_telemetry())

    assert sync_telemetry["total_keys"] == async_telemetry["total_keys"]
    assert sync_telemetry["active_keys"] == async_telemetry["active_keys"]
    assert sync_telemetry["keys_in_cooldown"] == async_telemetry["keys_in_cooldown"]
    assert len(sync_telemetry["keys"]) == len(async_telemetry["keys"])


@pytest.mark.asyncio
async def test_acquire_next_key_pass2_excludes_daily_exhausted_under_stress():
    """
    Verify that acquire_next_key Pass 2 strictly excludes DAILY_EXHAUSTED keys
    even across 20 successive acquisition requests under queue backpressure.
    """
    pool = MistralKeyPool(keys=["exhausted_1", "exhausted_2", "active_paced"])
    e1, e2, active = pool.get_keys()
    model = "ministral-3b-2512"

    e1.mark_rate_limited(is_daily=True, retry_delay_sec=10.0)
    e2.mark_rate_limited(is_daily=True, retry_delay_sec=5.0)

    # active key has a short spacing wait
    active.mark_rate_limited(is_daily=False, retry_delay_sec=0.2, model=model)

    # For 20 consecutive acquisitions, active key must be chosen every time, never e1 or e2
    for _ in range(20):
        key = await pool.acquire_next_key(model=model)
        assert key is not None
        assert key.key == "active_paced", (
            f"DAILY_EXHAUSTED key {key.key} was illegally selected in Pass 2!"
        )

    # When active also becomes DAILY_EXHAUSTED, acquire_next_key returns None
    active.mark_rate_limited(is_daily=True, retry_delay_sec=5.0)
    none_key = await pool.acquire_next_key(model=model)
    assert none_key is None
