"""
backend/tests/test_m2_it2_challenger.py - Iteration 2 Adversarial Challenge Suite
================================================================================
Empirical challenge harness for:
  1. Pass 2 candidate selection under edge cases:
     - 3 keys DAILY_EXHAUSTED, 1 key in short pacing -> strictly returns short-pacing key,
       never returns a DAILY_EXHAUSTED key.
     - All 4 keys DAILY_EXHAUSTED -> strictly returns None.
     - Exhaustive index permutations, short-wait DAILY_EXHAUSTED traps, and queue overflow.
  2. Sliding TPM exact boundary conditions:
     - tpm_limit - 1 (available, Pass 1 immediate selection, no TPM sleep).
     - tpm_limit (unavailable, Pass 1 skip, failover to available key, TPM sleep).
     - tpm_limit + 1 (unavailable, Pass 1 skip, failover to available key, TPM sleep).
     - Multi-chunk cumulative threshold crossing and 60s rollover recovery.
     - Models with tpm = None (unbounded TPM).
"""

import asyncio
from collections import deque
import time
from typing import Dict, List, Optional
from unittest.mock import patch
import pytest

from backend.app.pipeline.providers.mistral_key_pool import (
    KeyStatus,
    MistralKey,
    MistralKeyPool,
    get_mistral_model_limits,
    MISTRAL_MODEL_LIMITS,
    _get_loop_time,
)


# ============================================================================
# Section 1: Pass 2 Candidate Selection Adversarial Tests
# ============================================================================

class TestPass2CandidateSelectionAdversarial:
    """
    Adversarially challenge Pass 2 candidate selection in acquire_next_key:
    - 3 keys DAILY_EXHAUSTED and 1 key in short pacing.
    - All 4 keys DAILY_EXHAUSTED.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize("short_idx", [0, 1, 2, 3])
    @pytest.mark.parametrize("start_rr_idx", [0, 1, 2, 3])
    async def test_3_daily_exhausted_1_short_pacing_all_permutations(
        self, short_idx: int, start_rr_idx: int
    ):
        """
        Permute over which key is in short pacing (0, 1, 2, 3) and all initial
        round-robin indices (0, 1, 2, 3).
        Verify that acquire_next_key:
          1. Strictly returns the short-pacing key.
          2. NEVER returns any of the 3 DAILY_EXHAUSTED keys.
        """
        key_names = ["mstrl_k0", "mstrl_k1", "mstrl_k2", "mstrl_k3"]
        pool = MistralKeyPool(keys=key_names)
        pool._round_robin_idx = start_rr_idx
        model = "ministral-3b-2512"

        keys = pool.get_keys()
        for i, k in enumerate(keys):
            if i == short_idx:
                # Key in short pacing (model cooldown of 1.2s, so wait > 0.05s)
                k.mark_rate_limited(is_daily=False, retry_delay_sec=1.2, model=model)
                assert k.status == KeyStatus.ACTIVE
            else:
                # Key is DAILY_EXHAUSTED
                k.mark_rate_limited(is_daily=True, retry_delay_sec=86400.0)
                assert k.status == KeyStatus.DAILY_EXHAUSTED

        selected = await pool.acquire_next_key(model=model)

        assert selected is not None, f"Expected key {short_idx} to be selected, but got None"
        assert selected.key == key_names[short_idx], (
            f"Expected short-pacing key {key_names[short_idx]}, but got {selected.key}!"
        )
        assert selected.status != KeyStatus.DAILY_EXHAUSTED, (
            f"Selected key has status {selected.status}, which is DAILY_EXHAUSTED!"
        )

    @pytest.mark.asyncio
    async def test_daily_exhausted_with_shorter_cooldown_than_short_pacing_key(self):
        """
        Adversarial trap: 3 keys are DAILY_EXHAUSTED but have very short cooldowns (0.1s, 0.2s, 0.3s).
        The 1 active key is in short pacing with a longer wait (1.8s).
        In Pass 2, a naive comparator looking only at min(wait) would choose the DAILY_EXHAUSTED key.
        Verify acquire_next_key strictly skips all DAILY_EXHAUSTED keys and selects the short-pacing key.
        """
        pool = MistralKeyPool(keys=["mstrl_trap_k0", "mstrl_trap_k1", "mstrl_trap_k2", "mstrl_active_k3"])
        model = "codestral-2508"
        keys = pool.get_keys()

        # DAILY_EXHAUSTED keys with short cooldowns
        keys[0].mark_rate_limited(is_daily=True, retry_delay_sec=0.1)
        keys[1].mark_rate_limited(is_daily=True, retry_delay_sec=0.2)
        keys[2].mark_rate_limited(is_daily=True, retry_delay_sec=0.3)

        # Active key with longer wait (1.8s)
        keys[3].mark_rate_limited(is_daily=False, retry_delay_sec=1.8, model=model)

        # Test across all 4 starting round-robin positions
        for start_idx in range(4):
            pool._round_robin_idx = start_idx
            selected = await pool.acquire_next_key(model=model)
            assert selected is not None
            assert selected.key == "mstrl_active_k3", (
                f"At start_idx={start_idx}, selected {selected.key} instead of mstrl_active_k3!"
            )
            assert selected.status == KeyStatus.ACTIVE

    @pytest.mark.asyncio
    @pytest.mark.parametrize("pacing_type", ["model_cooldown", "rps_spacing", "slot_reservation"])
    async def test_various_short_pacing_types_with_3_daily_exhausted(self, pacing_type: str):
        """
        Verify that acquire_next_key reliably selects the short-pacing key regardless of
        how the pacing delay was introduced:
          - 'model_cooldown': transient 429 cooldown (e.g. 1.5s).
          - 'rps_spacing': key recently executed and enforces min_interval (e.g. ministral-14b 2.0s).
          - 'slot_reservation': key already has an atomic reservation slot 0.8s in future.
        """
        pool = MistralKeyPool(keys=["mstrl_k0", "mstrl_k1", "mstrl_k2", "mstrl_k3"])
        model = "ministral-14b-2512"  # 0.5 RPS = 2.0s interval
        keys = pool.get_keys()

        # Keys 0, 1, 2 are DAILY_EXHAUSTED
        for i in range(3):
            keys[i].mark_rate_limited(is_daily=True, retry_delay_sec=3600.0)

        # Key 3 is in short pacing via pacing_type
        loop = asyncio.get_event_loop()
        now = loop.time()

        if pacing_type == "model_cooldown":
            keys[3].mark_rate_limited(is_daily=False, retry_delay_sec=1.5, model=model)
        elif pacing_type == "rps_spacing":
            # Simulate a request executed 0.1s ago on 2.0s interval model
            keys[3]._last_request_by_model[model] = now - 0.1
            keys[3]._last_request_time = now - 0.1
        elif pacing_type == "slot_reservation":
            keys[3].reserve_slot(model, slot_time=now + 0.8, spacing=2.08)

        # Pass 1 must skip because wait > 0.05s
        wait_k3 = keys[3].get_seconds_until_ready(now, model=model)
        assert wait_k3 > 0.05, f"Expected wait > 0.05s for short pacing, got {wait_k3}"

        selected = await pool.acquire_next_key(model=model)
        assert selected is not None
        assert selected.key == "mstrl_k3"
        assert selected.status != KeyStatus.DAILY_EXHAUSTED

    @pytest.mark.asyncio
    @pytest.mark.parametrize("start_idx", [0, 1, 2, 3])
    async def test_all_4_keys_daily_exhausted_returns_none(self, start_idx: int):
        """
        When all 4 keys are DAILY_EXHAUSTED:
        Verify acquire_next_key strictly returns None across all start indices and models.
        """
        key_names = ["mstrl_d0", "mstrl_d1", "mstrl_d2", "mstrl_d3"]
        pool = MistralKeyPool(keys=key_names)
        pool._round_robin_idx = start_idx

        # Mark all 4 keys DAILY_EXHAUSTED with various cooldown lengths
        cooldowns = [1.0, 5.0, 100.0, 86400.0]
        for k, cd in zip(pool.get_keys(), cooldowns):
            k.mark_rate_limited(is_daily=True, retry_delay_sec=cd)
            assert k.status == KeyStatus.DAILY_EXHAUSTED

        # Test on various models
        for test_model in ["ministral-3b-2512", "codestral-2508", "mistral-large-2512", None]:
            result = await pool.acquire_next_key(model=test_model)
            assert result is None, (
                f"Expected None when all keys DAILY_EXHAUSTED on model '{test_model}', got {result}"
            )

    @pytest.mark.asyncio
    async def test_3_daily_exhausted_and_short_pacing_key_wait_exceeds_15s_returns_none(self):
        """
        Edge case: 3 keys are DAILY_EXHAUSTED. The 4th key has a wait > 15.0s (e.g. 20.0s).
        In Pass 2, best_wait will be 20.0s > 15.0s limit.
        Verify acquire_next_key returns None, and NEVER falls back to any DAILY_EXHAUSTED key.
        """
        pool = MistralKeyPool(keys=["mstrl_k0", "mstrl_k1", "mstrl_k2", "mstrl_k3"])
        model = "ministral-3b-2512"
        keys = pool.get_keys()

        # Keys 0, 1, 2 DAILY_EXHAUSTED
        for i in range(3):
            keys[i].mark_rate_limited(is_daily=True, retry_delay_sec=3600.0)

        # Key 3 has wait > 15s (e.g. 20s cooldown)
        keys[3].mark_rate_limited(is_daily=False, retry_delay_sec=20.0, model=model)

        selected = await pool.acquire_next_key(model=model)
        assert selected is None, f"Expected None when wait > 15.0s, but got {selected}"

    @pytest.mark.asyncio
    async def test_3_daily_exhausted_successive_queue_saturation(self):
        """
        Stress test: 3 keys are DAILY_EXHAUSTED.
        Call acquire_next_key multiple times in rapid succession.
        Key 3 accumulates reservations. Each call must return Key 3 until its wait
        exceeds 15.0s, at which point acquire_next_key must return None (never a DAILY_EXHAUSTED key).
        """
        pool = MistralKeyPool(keys=["mstrl_k0", "mstrl_k1", "mstrl_k2", "mstrl_k3"])
        model = "ministral-14b-2512"  # 0.5 RPS = 2.0s interval + 0.08s spacing ≈ 2.08s per call
        keys = pool.get_keys()

        for i in range(3):
            keys[i].mark_rate_limited(is_daily=True, retry_delay_sec=86400.0)

        acquired_keys = []
        for _ in range(10):
            res = await pool.acquire_next_key(model=model)
            if res is not None:
                acquired_keys.append(res)
            else:
                break

        # In 15.0s window with 2.08s spacing, at most ~8 calls can be scheduled:
        # Call 1: wait 0.0s
        # Call 2: wait 2.08s
        # Call 3: wait 4.16s
        # Call 4: wait 6.24s
        # Call 5: wait 8.32s
        # Call 6: wait 10.40s
        # Call 7: wait 12.48s
        # Call 8: wait 14.56s
        # Call 9: wait 16.64s > 15.0s -> returns None
        assert 6 <= len(acquired_keys) <= 8, f"Unexpected number of acquired slots: {len(acquired_keys)}"
        for ak in acquired_keys:
            assert ak.key == "mstrl_k3", f"Expected strictly mstrl_k3, got {ak.key}"


# ============================================================================
# Section 2: Sliding TPM Exact Boundary Enforcement Tests
# ============================================================================

class TestSlidingTPMBoundaryConditions:
    """
    Empirical challenge tests for sliding TPM boundary enforcement:
    - Exactly tpm_limit - 1 (available, not throttled)
    - Exactly tpm_limit (unavailable, throttled)
    - Exactly tpm_limit + 1 (unavailable, throttled)
    """

    @pytest.mark.parametrize(
        "model,tpm_limit",
        [
            ("mistral-small-2603", 20_000),
            ("mistral-large-2512", 250_000),
            ("codestral-2508", 625_000),
            ("ministral-8b-2512", 625_000),
            ("ministral-3b-2512", 1_300_000),
            ("codestral-embed", 50_000),
        ],
    )
    def test_exact_tpm_boundary_three_point_probe(self, model: str, tpm_limit: int):
        """
        Three-point probe at (tpm_limit - 1, tpm_limit, tpm_limit + 1) for every
        Mistral model with a defined TPM limit.
        """
        now = 1000.0

        # --- Probe 1: tpm_limit - 1 (Available) ---
        k_minus_1 = MistralKey("mstrl_probe_minus_1")
        k_minus_1.record_token_usage(model, tpm_limit - 1, now=now)
        assert k_minus_1.get_sliding_tpm(model, now=now) == tpm_limit - 1
        assert k_minus_1.is_available(now=now, model=model) is True, (
            f"Model {model} at tpm_limit - 1 ({tpm_limit - 1}) must be AVAILABLE, but is_available returned False!"
        )
        assert k_minus_1.get_seconds_until_ready(now=now, model=model) == 0.0

        # --- Probe 2: tpm_limit (Unavailable) ---
        k_exact = MistralKey("mstrl_probe_exact")
        k_exact.record_token_usage(model, tpm_limit, now=now)
        assert k_exact.get_sliding_tpm(model, now=now) == tpm_limit
        assert k_exact.is_available(now=now, model=model) is False, (
            f"Model {model} at tpm_limit ({tpm_limit}) must be UNAVAILABLE, but is_available returned True!"
        )
        wait_exact = k_exact.get_seconds_until_ready(now=now, model=model)
        assert wait_exact > 0.0, f"Expected positive wait at exact tpm_limit, got {wait_exact}"
        # Wait should be (now + 60.0 + 0.08) - now ≈ 60.08s
        assert abs(wait_exact - (60.0 + k_exact.jitter_margin_sec)) < 1e-3

        # --- Probe 3: tpm_limit + 1 (Unavailable) ---
        k_plus_1 = MistralKey("mstrl_probe_plus_1")
        k_plus_1.record_token_usage(model, tpm_limit + 1, now=now)
        assert k_plus_1.get_sliding_tpm(model, now=now) == tpm_limit + 1
        assert k_plus_1.is_available(now=now, model=model) is False, (
            f"Model {model} at tpm_limit + 1 ({tpm_limit + 1}) must be UNAVAILABLE, but is_available returned True!"
        )
        wait_plus_1 = k_plus_1.get_seconds_until_ready(now=now, model=model)
        assert wait_plus_1 > 0.0, f"Expected positive wait at tpm_limit + 1, got {wait_plus_1}"
        assert abs(wait_plus_1 - (60.0 + k_plus_1.jitter_margin_sec)) < 1e-3

    @pytest.mark.asyncio
    async def test_pool_acquire_behavior_at_exact_boundaries(self):
        """
        Verify pool acquire_next_key behavior across boundary states:
        - When Key 1 is at tpm_limit - 1: Pass 1 immediately selects Key 1.
        - When Key 1 is at tpm_limit: Pass 1 skips Key 1, selects Key 2.
        - When Key 1 is at tpm_limit + 1: Pass 1 skips Key 1, selects Key 2.
        """
        model = "mistral-small-2603"
        tpm_limit = 20_000
        now = _get_loop_time()

        # Case 1: Key 1 has 19,999 tokens (tpm_limit - 1)
        pool1 = MistralKeyPool(keys=["mstrl_k1", "mstrl_k2"])
        k1, k2 = pool1.get_keys()
        k1.record_token_usage(model, tpm_limit - 1, now=now)

        sel1 = await pool1.acquire_next_key(model=model)
        assert sel1 is not None and sel1.key == "mstrl_k1", (
            f"Key 1 with {tpm_limit - 1} tokens should be selected in Pass 1, but got {sel1}"
        )

        # Case 2: Key 1 has 20,000 tokens (exact tpm_limit)
        pool2 = MistralKeyPool(keys=["mstrl_k1", "mstrl_k2"])
        k1_2, k2_2 = pool2.get_keys()
        k1_2.record_token_usage(model, tpm_limit, now=now)

        sel2 = await pool2.acquire_next_key(model=model)
        assert sel2 is not None and sel2.key == "mstrl_k2", (
            f"Key 1 with {tpm_limit} tokens must be skipped; Key 2 must be selected, got {sel2}"
        )

        # Case 3: Key 1 has 20,001 tokens (tpm_limit + 1)
        pool3 = MistralKeyPool(keys=["mstrl_k1", "mstrl_k2"])
        k1_3, k2_3 = pool3.get_keys()
        k1_3.record_token_usage(model, tpm_limit + 1, now=now)

        sel3 = await pool3.acquire_next_key(model=model)
        assert sel3 is not None and sel3.key == "mstrl_k2", (
            f"Key 1 with {tpm_limit + 1} tokens must be skipped; Key 2 must be selected, got {sel3}"
        )

    def test_multi_chunk_cumulative_threshold_crossing_and_recovery(self):
        """
        Verify cumulative multi-chunk token accumulation crossing boundaries:
        - Chunk 1: (limit - 2) tokens at t0 -> total = limit - 2 (Available)
        - Chunk 2: 1 token at t0 + 10s -> total = limit - 1 (Available)
        - Chunk 3: 1 token at t0 + 20s -> total = limit (Unavailable!)
        - Chunk 4: 1 token at t0 + 30s -> total = limit + 1 (Unavailable!)
        - At t0 + 60.1s: Chunk 1 expires -> total = 1 + 1 + 1 = 3 < limit (Recovered to Available!)
        """
        key = MistralKey("mstrl_multi_chunk")
        model = "mistral-small-2603"
        limit = 20_000
        t0 = 500.0

        # Chunk 1: 19,998 tokens at t=500
        key.record_token_usage(model, limit - 2, now=t0)
        assert key.get_sliding_tpm(model, now=t0) == limit - 2
        assert key.is_available(now=t0, model=model) is True

        # Chunk 2: 1 token at t=510 -> total = 19,999 (limit - 1)
        key.record_token_usage(model, 1, now=t0 + 10.0)
        assert key.get_sliding_tpm(model, now=t0 + 10.0) == limit - 1
        assert key.is_available(now=t0 + 10.0, model=model) is True

        # Chunk 3: 1 token at t=520 -> total = 20,000 (limit)
        key.record_token_usage(model, 1, now=t0 + 20.0)
        assert key.get_sliding_tpm(model, now=t0 + 20.0) == limit
        assert key.is_available(now=t0 + 20.0, model=model) is False

        # Chunk 4: 1 token at t=530 -> total = 20,001 (limit + 1)
        key.record_token_usage(model, 1, now=t0 + 30.0)
        assert key.get_sliding_tpm(model, now=t0 + 30.0) == limit + 1
        assert key.is_available(now=t0 + 30.0, model=model) is False

        # At t=560.1 (t0 + 60.1s): Chunk 1 rolls off!
        # Remaining: Chunk 2 (1) + Chunk 3 (1) + Chunk 4 (1) = 3 tokens
        recovered_tpm = key.get_sliding_tpm(model, now=t0 + 60.1)
        assert recovered_tpm == 3, f"Expected 3 tokens after Chunk 1 expiration, got {recovered_tpm}"
        assert key.is_available(now=t0 + 60.1, model=model) is True, (
            "Key failed to recover to available state after oldest chunk expired!"
        )
        assert key.get_seconds_until_ready(now=t0 + 60.1, model=model) == 0.0

    @pytest.mark.asyncio
    async def test_acquire_pacing_slot_sleep_behavior_at_boundaries(self):
        """
        Verify acquire_pacing_slot sleep behavior:
        - At tpm_limit - 1: does NOT sleep for TPM.
        - At tpm_limit: sleeps until oldest token expires.
        - At tpm_limit + 1: sleeps until oldest token expires.
        """
        model = "mistral-small-2603"
        limit = 20_000
        loop = asyncio.get_event_loop()
        now = loop.time()

        # 1. At tpm_limit - 1: no TPM sleep
        k_below = MistralKey("mstrl_slot_below")
        k_below._model_token_records[model] = deque([(now - 30.0, limit - 1)])

        sleeps_below: List[float] = []
        with patch("asyncio.sleep", side_effect=lambda d: sleeps_below.append(d)):
            await k_below.acquire_pacing_slot(model=model)

        # sleeps_below should NOT contain a ~30s TPM sleep
        assert not any(s > 5.0 for s in sleeps_below), (
            f"acquire_pacing_slot triggered unexpected TPM sleep at limit - 1: {sleeps_below}"
        )

        # 2. At tpm_limit: must sleep ~30.08s for oldest token
        k_at = MistralKey("mstrl_slot_at")
        k_at._model_token_records[model] = deque([(now - 30.0, limit)])

        sleeps_at: List[float] = []
        with patch("asyncio.sleep", side_effect=lambda d: sleeps_at.append(d)):
            await k_at.acquire_pacing_slot(model=model)

        expected_sleep = (now - 30.0 + 60.0 + 0.08) - now  # ≈ 30.08s
        assert any(abs(s - expected_sleep) < 0.5 for s in sleeps_at), (
            f"Expected ~{expected_sleep:.2f}s TPM sleep at exact limit, got {sleeps_at}"
        )

        # 3. At tpm_limit + 1: must sleep ~30.08s for oldest token
        k_above = MistralKey("mstrl_slot_above")
        k_above._model_token_records[model] = deque([(now - 30.0, limit + 1)])

        sleeps_above: List[float] = []
        with patch("asyncio.sleep", side_effect=lambda d: sleeps_above.append(d)):
            await k_above.acquire_pacing_slot(model=model)

        assert any(abs(s - expected_sleep) < 0.5 for s in sleeps_above), (
            f"Expected ~{expected_sleep:.2f}s TPM sleep at limit + 1, got {sleeps_above}"
        )

    def test_unbounded_tpm_models_never_throttle_on_tokens(self):
        """
        Verify models with tpm=None (e.g. glm-5-2) never throttle on token usage,
        even with 100,000,000 tokens recorded.
        """
        key = MistralKey("mstrl_unbounded")
        model = "glm-5-2"
        limits = get_mistral_model_limits(model)
        assert limits["tpm"] is None

        now = 1000.0
        key.record_token_usage(model, 100_000_000, now=now)
        assert key.get_sliding_tpm(model, now=now) == 100_000_000
        assert key.is_available(now=now, model=model) is True, (
            "Model with tpm=None should always be available regardless of token usage"
        )
        assert key.get_seconds_until_ready(now=now, model=model) == 0.0
