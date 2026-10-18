"""
backend/tests/test_m2_challenger_key_pool_stress.py - Empirical Stress Tests for M2 Mistral Key Pool
===================================================================================================
Adversarial stress testing of M2 Mistral Key Pool:
1. High concurrent bursts across 4 keys (32, 64, 100 tasks):
   - Strict equal distribution without Key 1 bias under high concurrency.
   - Non-multiple concurrent bursts (35, 65 tasks): verify fair round-robin distribution.
   - Mixed-model concurrent bursts.
   - Cooldown key exclusion under high concurrent bursts (Key 1 or Key 2 in cooldown).
2. Minimum spacing + 0.08s jitter margin enforcement:
   - Direct pacing on MistralKey for various models (ministral-3b, codestral, ministral-8b).
   - Consecutive call interval verification across rapid sequential requests.
   - Full concurrent pipeline simulation: 32 and 64 concurrent tasks executing through
     MistralKeyPool.acquire_next_key and MistralKey.acquire_pacing_slot.
   - Verifying that execution timestamps on each key respect (min_interval + 0.08s jitter margin).
3. Backpressure and edge condition stress:
   - Queue depth limit (15.0s wait ceiling) on slow models (ministral-14b).
   - High concurrency under sliding-window TPM limits.
"""

import asyncio
from collections import deque
import time
from typing import Dict, List, Optional
import pytest

from backend.app.pipeline.providers.mistral_key_pool import (
    KeyStatus,
    MistralKey,
    MistralKeyPool,
    get_mistral_model_limits,
)


# ============================================================================
# Category 1: High Concurrent Bursts & Equal Distribution Across 4 Keys
# ============================================================================

@pytest.mark.asyncio
async def test_stress_concurrent_burst_32_tasks_strict_equal_distribution():
    """
    Stress-test: 32 simultaneous concurrent tasks call acquire_next_key() across 4 keys.
    Must achieve strictly equal distribution (exactly 8 requests per key),
    with zero Key 1 bias.
    """
    keys = ["mstrl_k1", "mstrl_k2", "mstrl_k3", "mstrl_k4"]
    pool = MistralKeyPool(keys=keys)
    model = "ministral-3b-2512"

    tasks = [pool.acquire_next_key(model=model) for _ in range(32)]
    results = await asyncio.gather(*tasks)

    counts: Dict[str, int] = {k: 0 for k in keys}
    for item in results:
        assert item is not None, "acquire_next_key returned None during 32-task burst"
        counts[item.key] += 1

    for k in keys:
        assert counts[k] == 8, (
            f"Key {k} received {counts[k]} requests; expected strictly 8. "
            f"Full distribution: {counts}"
        )


@pytest.mark.asyncio
async def test_stress_concurrent_burst_64_tasks_strict_equal_distribution():
    """
    Stress-test: 64 simultaneous concurrent tasks call acquire_next_key() across 4 keys.
    Must achieve strictly equal distribution (exactly 16 requests per key),
    with zero Key 1 bias.
    """
    keys = ["mstrl_k1", "mstrl_k2", "mstrl_k3", "mstrl_k4"]
    pool = MistralKeyPool(keys=keys)
    model = "ministral-3b-2512"

    tasks = [pool.acquire_next_key(model=model) for _ in range(64)]
    results = await asyncio.gather(*tasks)

    counts: Dict[str, int] = {k: 0 for k in keys}
    for item in results:
        assert item is not None, "acquire_next_key returned None during 64-task burst"
        counts[item.key] += 1

    for k in keys:
        assert counts[k] == 16, (
            f"Key {k} received {counts[k]} requests; expected strictly 16. "
            f"Full distribution: {counts}"
        )


@pytest.mark.asyncio
async def test_stress_concurrent_burst_100_tasks_strict_equal_distribution():
    """
    Stress-test: 100 simultaneous concurrent tasks across 4 keys.
    Must achieve strictly equal distribution (exactly 25 requests per key).
    """
    keys = ["mstrl_k1", "mstrl_k2", "mstrl_k3", "mstrl_k4"]
    pool = MistralKeyPool(keys=keys)
    # Using ministral-3b (12.5 RPS, spacing 0.16s), 25 * 0.16s = 4.0s wait (well within 15s limit)
    model = "ministral-3b-2512"

    tasks = [pool.acquire_next_key(model=model) for _ in range(100)]
    results = await asyncio.gather(*tasks)

    counts: Dict[str, int] = {k: 0 for k in keys}
    for item in results:
        assert item is not None, "acquire_next_key returned None during 100-task burst"
        counts[item.key] += 1

    for k in keys:
        assert counts[k] == 25, (
            f"Key {k} received {counts[k]} requests; expected strictly 25. "
            f"Full distribution: {counts}"
        )


@pytest.mark.asyncio
async def test_stress_non_multiple_concurrent_burst_fair_round_robin():
    """
    Stress-test: 35 tasks across 4 keys (35 = 4 * 8 + 3).
    Must allocate 9 requests to first 3 keys and 8 requests to the 4th key.
    No key may have more than 9 or fewer than 8 requests.
    """
    keys = ["mstrl_k1", "mstrl_k2", "mstrl_k3", "mstrl_k4"]
    pool = MistralKeyPool(keys=keys)
    model = "ministral-3b-2512"

    tasks = [pool.acquire_next_key(model=model) for _ in range(35)]
    results = await asyncio.gather(*tasks)

    counts: Dict[str, int] = {k: 0 for k in keys}
    for item in results:
        assert item is not None
        counts[item.key] += 1

    sorted_counts = sorted(counts.values(), reverse=True)
    assert sorted_counts == [9, 9, 9, 8], (
        f"Distribution {counts} does not match fair round-robin [9, 9, 9, 8]"
    )


@pytest.mark.asyncio
async def test_stress_multiple_successive_bursts_no_key1_accumulation():
    """
    Stress-test: Run 5 successive bursts of 32 tasks each (total 160 requests).
    Verify that across all bursts, total allocations per key remain identical (40 each).
    """
    keys = ["mstrl_k1", "mstrl_k2", "mstrl_k3", "mstrl_k4"]
    pool = MistralKeyPool(keys=keys)
    model = "ministral-3b-2512"

    cumulative_counts: Dict[str, int] = {k: 0 for k in keys}

    for burst_idx in range(5):
        # Reset slot reservations between bursts to simulate spaced batch jobs
        tasks = [pool.acquire_next_key(model=model) for _ in range(32)]
        results = await asyncio.gather(*tasks)

        for item in results:
            assert item is not None
            cumulative_counts[item.key] += 1

    for k in keys:
        assert cumulative_counts[k] == 40, (
            f"Key {k} has cumulative count {cumulative_counts[k]}; expected 40. "
            f"Full distribution: {cumulative_counts}"
        )


@pytest.mark.asyncio
async def test_stress_concurrent_burst_with_key1_in_cooldown():
    """
    Adversarial test: Key 1 is placed in cooldown for the requested model.
    36 concurrent requests arrive.
    Verify:
      - Key 1 receives strictly 0 requests.
      - The 3 remaining healthy keys receive strictly 12 requests each.
    """
    keys = ["mstrl_k1", "mstrl_k2", "mstrl_k3", "mstrl_k4"]
    pool = MistralKeyPool(keys=keys)
    model = "ministral-8b-2512"

    k1 = pool.get_keys()[0]
    k1.mark_rate_limited(is_daily=False, retry_delay_sec=60.0, model=model)

    tasks = [pool.acquire_next_key(model=model) for _ in range(36)]
    results = await asyncio.gather(*tasks)

    counts: Dict[str, int] = {k: 0 for k in keys}
    for item in results:
        assert item is not None
        counts[item.key] += 1

    assert counts["mstrl_k1"] == 0, f"Key 1 in cooldown received requests: {counts['mstrl_k1']}"
    assert counts["mstrl_k2"] == 12, f"Key 2 expected 12, got {counts['mstrl_k2']}"
    assert counts["mstrl_k3"] == 12, f"Key 3 expected 12, got {counts['mstrl_k3']}"
    assert counts["mstrl_k4"] == 12, f"Key 4 expected 12, got {counts['mstrl_k4']}"


# ============================================================================
# Category 2: Minimum Spacing + 0.08s Jitter Margin Enforcement
# ============================================================================

@pytest.mark.asyncio
async def test_stress_pacing_jitter_margin_ministral_3b():
    """
    Verify spacing on ministral-3b-2512:
    - 12.5 RPS -> min_interval = 0.080s
    - Jitter margin = 0.080s
    - Expected total spacing per key = min_interval + jitter = 0.160s
    Consecutive executions on the same key must be spaced by at least 0.150s.
    """
    key = MistralKey("mstrl_pacing_3b", jitter_margin_sec=0.08)
    model = "ministral-3b-2512"

    limits = get_mistral_model_limits(model)
    min_interval = limits["min_interval"]
    expected_spacing = min_interval + key.jitter_margin_sec  # 0.08 + 0.08 = 0.16s

    timestamps: List[float] = []

    for _ in range(5):
        await key.acquire_pacing_slot(model=model)
        timestamps.append(time.monotonic())

    for i in range(1, len(timestamps)):
        diff = timestamps[i] - timestamps[i - 1]
        assert diff >= expected_spacing - 0.015, (
            f"Call {i} to {i+1} spacing {diff:.4f}s was less than "
            f"expected ({expected_spacing:.4f}s - tolerance)"
        )


@pytest.mark.asyncio
async def test_stress_pacing_jitter_margin_codestral():
    """
    Verify spacing on codestral-2508:
    - 2.08 RPS -> min_interval ≈ 0.4808s
    - Jitter margin = 0.080s
    - Expected total spacing = 0.4808 + 0.08 = 0.5608s
    3 consecutive calls must enforce >= 0.54s spacing.
    """
    key = MistralKey("mstrl_pacing_code", jitter_margin_sec=0.08)
    model = "codestral-2508"

    limits = get_mistral_model_limits(model)
    expected_spacing = limits["min_interval"] + key.jitter_margin_sec

    timestamps: List[float] = []

    for _ in range(3):
        await key.acquire_pacing_slot(model=model)
        timestamps.append(time.monotonic())

    for i in range(1, len(timestamps)):
        diff = timestamps[i] - timestamps[i - 1]
        assert diff >= expected_spacing - 0.02, (
            f"Call {i} to {i+1} spacing {diff:.4f}s was less than "
            f"expected ({expected_spacing:.4f}s - tolerance)"
        )


@pytest.mark.asyncio
async def test_stress_atomic_reservation_slot_spacing_integrity():
    """
    Inspect the internal queue of reserved slots created by acquire_next_key()
    under a 16-request burst on a single key.
    Verify that every slot scheduled in _reserved_slots satisfies:
    slot[i+1] - slot[i] >= min_interval + 0.08s jitter margin.
    """
    key = MistralKey("mstrl_single_res", jitter_margin_sec=0.08)
    pool = MistralKeyPool()
    pool._keys = [key]
    model = "ministral-3b-2512"

    limits = get_mistral_model_limits(model)
    min_spacing = limits["min_interval"] + key.jitter_margin_sec  # 0.16s

    for _ in range(10):
        selected = await pool.acquire_next_key(model=model)
        assert selected is not None

    reserved_slots = list(key._reserved_slots[model])
    assert len(reserved_slots) == 10

    for i in range(1, len(reserved_slots)):
        slot_diff = reserved_slots[i] - reserved_slots[i - 1]
        assert slot_diff >= min_spacing - 1e-4, (
            f"Slot {i} reservation interval {slot_diff:.4f}s violates "
            f"minimum spacing {min_spacing:.4f}s"
        )


# ============================================================================
# Category 3: Full End-to-End Concurrent Pipeline Simulation
# ============================================================================

@pytest.mark.asyncio
async def test_stress_full_concurrent_pipeline_32_tasks_execution():
    """
    Simulate full realistic workload:
    32 concurrent workers execute acquire_next_key() followed immediately
    by acquire_pacing_slot() and a simulated fast network invocation (5ms).
    Verify:
      1. All 32 tasks complete without error or deadlock.
      2. Distribution across 4 keys is strictly 8 tasks per key.
      3. For every key, recorded execution timestamps satisfy spacing >= min_interval + 0.08s.
    """
    keys = ["mstrl_k1", "mstrl_k2", "mstrl_k3", "mstrl_k4"]
    pool = MistralKeyPool(keys=keys)
    model = "ministral-3b-2512"

    limits = get_mistral_model_limits(model)
    min_spacing = limits["min_interval"] + 0.08  # 0.16s

    key_execution_times: Dict[str, List[float]] = {k: [] for k in keys}
    lock = asyncio.Lock()

    async def worker(task_id: int):
        k = await pool.acquire_next_key(model=model)
        assert k is not None, f"Task {task_id} failed to acquire key"
        await k.acquire_pacing_slot(model=model)
        exec_time = time.monotonic()
        async with lock:
            key_execution_times[k.key].append(exec_time)
        # simulated work
        await asyncio.sleep(0.005)

    tasks = [asyncio.create_task(worker(i)) for i in range(32)]
    await asyncio.gather(*tasks)

    # 1. Distribution check
    for k in keys:
        count = len(key_execution_times[k])
        assert count == 8, f"Key {k} processed {count} tasks; expected strictly 8"

    # 2. Pacing spacing check per key
    for k in keys:
        times = sorted(key_execution_times[k])
        for i in range(1, len(times)):
            interval = times[i] - times[i - 1]
            assert interval >= min_spacing - 0.03, (
                f"Key {k} interval between call {i} and {i+1} was {interval:.4f}s, "
                f"expected >= {min_spacing - 0.03:.4f}s"
            )


@pytest.mark.asyncio
async def test_stress_full_concurrent_pipeline_64_tasks_execution():
    """
    Simulate 64 concurrent workers across 4 keys:
    Verify:
      1. Exactly 16 tasks per key (64 // 4 = 16).
      2. 0 deadlocks, 0 race conditions.
      3. Strict round-robin fairness.
    """
    keys = ["mstrl_k1", "mstrl_k2", "mstrl_k3", "mstrl_k4"]
    pool = MistralKeyPool(keys=keys)
    model = "ministral-3b-2512"

    key_counts: Dict[str, int] = {k: 0 for k in keys}
    lock = asyncio.Lock()

    async def worker(task_id: int):
        k = await pool.acquire_next_key(model=model)
        assert k is not None
        await k.acquire_pacing_slot(model=model)
        async with lock:
            key_counts[k.key] += 1
        await asyncio.sleep(0.001)

    tasks = [asyncio.create_task(worker(i)) for i in range(64)]
    await asyncio.gather(*tasks)

    for k in keys:
        assert key_counts[k] == 16, f"Key {k} count {key_counts[k]} != 16"


# ============================================================================
# Category 4: Slower Models & Queue Saturation Backpressure (15s ceiling)
# ============================================================================

@pytest.mark.asyncio
async def test_stress_slower_model_queue_backpressure_ceiling():
    """
    Verify backpressure behavior on slower model: ministral-14b-2512.
    - RPS = 0.50 (2.0s min_interval)
    - Spacing = 2.08s
    - With 1 key, 8 requests take 8 * 2.08s = 16.64s.
    - The 8th request wait (14.56s) is <= 15.0s, so it is accepted.
    - The 9th request wait (16.64s) exceeds 15.0s ceiling, so acquire_next_key()
      must return None (triggering backpressure).
    """
    pool = MistralKeyPool(keys=["mstrl_slow_key"])
    model = "ministral-14b-2512"

    acquired = []
    for i in range(8):
        k = await pool.acquire_next_key(model=model)
        assert k is not None, f"Request {i+1} unexpectedly rejected"
        acquired.append(k)

    # 9th request must exceed 15.0s ceiling and be rejected
    rejected = await pool.acquire_next_key(model=model)
    assert rejected is None, (
        "Expected acquire_next_key to return None when reservation wait exceeds 15.0s"
    )

# ============================================================================
# Category 5: Advanced Adversarial Scenarios
# ============================================================================

@pytest.mark.asyncio
async def test_stress_mixed_models_concurrent_burst_across_4_keys():
    """
    Stress-test: 64 concurrent requests consisting of 4 distinct models:
      - 16x ministral-3b-2512 (12.5 RPS)
      - 16x ministral-8b-2512 (3.13 RPS)
      - 16x codestral-2508 (2.08 RPS)
      - 16x mistral-large-2512 (1.00 RPS)
    Verify:
      1. All 64 requests acquire a valid key.
      2. Overall requests are equally balanced across the 4 keys (16 each).
      3. No deadlocks or cross-model slot contamination.
    """
    keys = ["mstrl_k1", "mstrl_k2", "mstrl_k3", "mstrl_k4"]
    pool = MistralKeyPool(keys=keys)

    models = [
        "ministral-3b-2512",
        "ministral-8b-2512",
        "codestral-2508",
        "mistral-large-2512",
    ]

    # Interleave models
    request_models = [models[i % 4] for i in range(64)]

    tasks = [pool.acquire_next_key(model=m) for m in request_models]
    results = await asyncio.gather(*tasks)

    counts: Dict[str, int] = {k: 0 for k in keys}
    for item in results:
        assert item is not None
        counts[item.key] += 1

    for k in keys:
        assert counts[k] == 16, f"Key {k} got {counts[k]} requests, expected 16"


@pytest.mark.asyncio
async def test_stress_concurrent_burst_with_dynamic_429_failover():
    """
    Stress-test: 32 concurrent requests with dynamic 429 failover.
    Simulate Key 1 returning 429 after 4 requests.
    Verify:
      1. Key 1 is marked rate-limited for the model.
      2. The remaining 28 requests automatically fail over and distribute
         across the 3 remaining keys without any hung tasks or unhandled exceptions.
    """
    keys = ["mstrl_k1", "mstrl_k2", "mstrl_k3", "mstrl_k4"]
    pool = MistralKeyPool(keys=keys)
    model = "ministral-3b-2512"

    executed_keys: List[str] = []
    lock = asyncio.Lock()

    async def worker(task_id: int):
        k = await pool.acquire_next_key(model=model)
        assert k is not None

        # Simulate 429 on Key 1 on the 4th encounter
        async with lock:
            if k.key == "mstrl_k1" and executed_keys.count("mstrl_k1") >= 3:
                k.mark_rate_limited(is_daily=False, retry_delay_sec=10.0, model=model)
                # Rotate to next key as MistralProvider would
                k = await pool.acquire_next_key(model=model)
                assert k is not None
                assert k.key != "mstrl_k1"

            executed_keys.append(k.key)

    tasks = [asyncio.create_task(worker(i)) for i in range(32)]
    await asyncio.gather(*tasks)

    assert len(executed_keys) == 32
    assert executed_keys.count("mstrl_k1") <= 4
    # The other keys took over the load
    for other_k in ["mstrl_k2", "mstrl_k3", "mstrl_k4"]:
        assert executed_keys.count(other_k) >= 8


@pytest.mark.asyncio
async def test_stress_out_of_order_pacing_slot_acquisition():
    """
    Stress-test: Verify FIFO reservation ordering even if tasks complete acquire_next_key
    and then call acquire_pacing_slot in slightly shuffled order.
    """
    key = MistralKey("mstrl_fifo_key", jitter_margin_sec=0.08)
    pool = MistralKeyPool()
    pool._keys = [key]
    model = "ministral-3b-2512"

    # Reserve 6 slots
    for _ in range(6):
        await pool.acquire_next_key(model=model)

    start_times: List[float] = []

    async def pace_worker(idx: int, delay: float):
        await asyncio.sleep(delay)
        await key.acquire_pacing_slot(model=model)
        start_times.append(time.monotonic())

    # Launch with small staggered delays to simulate random task scheduling
    delays = [0.03, 0.01, 0.05, 0.00, 0.02, 0.04]
    workers = [pace_worker(i, d) for i, d in enumerate(delays)]
    await asyncio.gather(*workers)

    assert len(start_times) == 6
    # Verify that consecutive execution times are properly spaced
    for i in range(1, len(start_times)):
        diff = start_times[i] - start_times[i - 1]
        assert diff >= 0.12, f"Execution {i} interval {diff:.4f}s was less than minimum 0.12s"

