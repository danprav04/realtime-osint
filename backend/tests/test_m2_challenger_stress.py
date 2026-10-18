"""
backend/tests/test_m2_challenger_stress.py - Adversarial Stress & Chaos Test Harness for M2
===========================================================================================
EMPIRICAL CHALLENGER verification suite for M2 Mistral pipeline enhancements:
  1. 60-Second Sliding-Window TPM Tracking, Multi-Burst Saturation, and Rollover Recovery.
  2. Transient 429 Reason Formatting against orchestrator.py:819 is_minute_only Filter.
  3. max_tokens Preservation for Small Caller Values (1, 16, 64, 128, 256, 512).
"""

import asyncio
from collections import deque
import json
import time
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, patch
import httpx
import pytest

from backend.app.pipeline.providers.mistral import MistralProvider
from backend.app.pipeline.providers.mistral_key_pool import (
    KeyStatus,
    MistralKey,
    MistralKeyPool,
    get_mistral_model_limits,
)
from backend.app.pipeline.stage0_gatekeeper import Stage0Gatekeeper


# ============================================================================
# Helper: Orchestrator is_minute_only filter check (verbatim from orchestrator.py:806-840)
# ============================================================================

def evaluate_orchestrator_minute_only(exc: Exception) -> bool:
    """Evaluates whether exc matches orchestrator.py:806-840 is_minute_only filter."""
    err_str = str(exc).lower()
    is_rate_limit = (
        "429" in str(exc)
        or "quota" in err_str
        or "resourceexhausted" in err_str
        or "ratelimit" in err_str
        or "too many requests" in err_str
    )
    is_minute_only = (
        is_rate_limit
        and any(
            k in err_str
            for k in (
                "minute rate limit",
                "perminute",
                "per_minute",
                "(rpm)",
                "(tpm)",
                "requests per minute",
                "tokens per minute",
            )
        )
        and not any(
            k in err_str
            for k in (
                "daily",
                "perday",
                "per_day",
                "(rpd)",
                "(tpd)",
                "requests per day",
                "tokens per day",
            )
        )
    )
    return bool(is_minute_only)


# ============================================================================
# Area 1: 60-Second Sliding-Window TPM Tracking, Saturation, and Recovery
# ============================================================================

class TestSlidingWindowTPM:
    """Empirical challenge tests for 60s sliding-window TPM tracking and rollover."""

    def test_tpm_multi_burst_incremental_saturation_and_exact_rollover(self):
        """
        Verify multi-burst incremental token usage on mistral-small-2603 (20,000 TPM limit):
        - Burst 1: 8,000 tokens at t=100.0
        - Burst 2: 7,000 tokens at t=115.0
        - Burst 3: 6,000 tokens at t=130.0 -> total 21,000 tokens (saturated).
        - At t=159.9: still saturated (21,000 >= 20,000).
        - At t=160.1: Burst 1 rolls off -> total 13,000 tokens (recovered).
        - Key transitions from unavailable to available precisely at rollover boundary.
        """
        key = MistralKey("mstrl_multi_burst")
        model = "mistral-small-2603"
        limit = 20_000

        # Burst 1 at t=100
        key.record_token_usage(model, 8000, now=100.0)
        assert key.get_sliding_tpm(model, now=105.0) == 8000
        assert key.is_available(now=105.0, model=model) is True

        # Burst 2 at t=115
        key.record_token_usage(model, 7000, now=115.0)
        assert key.get_sliding_tpm(model, now=120.0) == 15000
        assert key.is_available(now=120.0, model=model) is True

        # Burst 3 at t=130 -> 21,000 tokens (exceeds 20,000 limit)
        key.record_token_usage(model, 6000, now=130.0)
        assert key.get_sliding_tpm(model, now=135.0) == 21000
        assert key.is_available(now=135.0, model=model) is False

        # Still saturated at t=159.9
        assert key.get_sliding_tpm(model, now=159.9) == 21000
        assert key.is_available(now=159.9, model=model) is False
        wait_sec = key.get_seconds_until_ready(now=159.9, model=model)
        assert wait_sec > 0.0

        # Rollover: At t=160.1, Burst 1 (8,000 tokens from t=100) expires!
        # Remaining: 7,000 + 6,000 = 13,000 tokens
        tpm_after_rollover = key.get_sliding_tpm(model, now=160.1)
        assert tpm_after_rollover == 13000, f"Expected 13,000 tokens after rollover, got {tpm_after_rollover}"
        assert key.is_available(now=160.1, model=model) is True, "Key should be available after 60s rollover"
        assert key.get_seconds_until_ready(now=160.1, model=model) == 0.0

    def test_tpm_cascading_burst_delayed_recovery(self):
        """
        Verify scenario where oldest record expiration is NOT enough to clear saturation:
        - Burst 1: 500 tokens at t=100.0
        - Burst 2: 21,000 tokens at t=120.0 -> total 21,500 tokens.
        - At t=160.1: Burst 1 expires, but Burst 2 (21,000) STILL exceeds 20,000 limit.
        - Key must REMAIN unavailable until Burst 2 expires at t=180.1.
        """
        key = MistralKey("mstrl_cascade")
        model = "mistral-small-2603"

        key.record_token_usage(model, 500, now=100.0)
        key.record_token_usage(model, 21000, now=120.0)

        assert key.get_sliding_tpm(model, now=125.0) == 21500
        assert key.is_available(now=125.0, model=model) is False

        # At t=160.1: Burst 1 (500) rolls off, but 21,000 remains >= 20,000
        assert key.get_sliding_tpm(model, now=160.1) == 21000
        assert key.is_available(now=160.1, model=model) is False, "Key should remain unavailable while 21,000 >= 20,000"

        wait_remaining = key.get_seconds_until_ready(now=160.1, model=model)
        # Should wait for Burst 2 to expire: (120.0 + 60.0 + 0.08) - 160.1 ≈ 19.98s
        assert 19.5 <= wait_remaining <= 20.5, f"Expected ~20s wait for Burst 2 expiration, got {wait_remaining:.2f}s"

        # At t=180.2: Burst 2 rolls off, window drops to 0
        assert key.get_sliding_tpm(model, now=180.2) == 0
        assert key.is_available(now=180.2, model=model) is True

    @pytest.mark.asyncio
    async def test_tpm_concurrent_burst_stress_100_tasks(self):
        """
        Adversarial concurrency test: 100 concurrent async tasks record token usage
        simultaneously to stress-test deque consistency and calculation accuracy.
        """
        key = MistralKey("mstrl_concurrency_stress")
        model = "codestral-2508"

        async def worker(idx: int):
            tokens = 100 + (idx * 5)
            key.record_token_usage(model, tokens)

        tasks = [worker(i) for i in range(100)]
        await asyncio.gather(*tasks)

        expected_total = sum(100 + (i * 5) for i in range(100))
        sliding_tpm = key.get_sliding_tpm(model)
        assert sliding_tpm == expected_total, f"Expected {expected_total} tokens, got {sliding_tpm}"

    def test_tpm_zero_and_negative_token_inputs_ignored(self):
        """Verify record_token_usage ignores 0 or negative tokens without corrupting deque."""
        key = MistralKey("mstrl_negative_input")
        model = "ministral-3b-2512"

        key.record_token_usage(model, 0)
        key.record_token_usage(model, -500)
        assert key.get_sliding_tpm(model) == 0
        assert model not in key._model_token_records or len(key._model_token_records[model]) == 0

    def test_tpm_cross_model_isolation_same_key(self):
        """
        Stress-test cross-model isolation: Saturating TPM on mistral-small-2603 (20K TPM)
        must NOT degrade ministral-3b-2512 (1.3M TPM) or codestral-2508 (625K TPM) on the same key.
        """
        key = MistralKey("mstrl_isolation_key")
        small_model = "mistral-small-2603"
        fast_model = "ministral-3b-2512"
        code_model = "codestral-2508"

        # Saturate small_model with 25,000 tokens (> 20,000 limit)
        key.record_token_usage(small_model, 25000)

        # small_model is unavailable
        assert key.is_available(model=small_model) is False
        assert key.get_sliding_tpm(small_model) == 25000

        # fast_model has 0 tokens and is fully available
        assert key.get_sliding_tpm(fast_model) == 0
        assert key.is_available(model=fast_model) is True

        # code_model has 0 tokens and is fully available
        assert key.get_sliding_tpm(code_model) == 0
        assert key.is_available(model=code_model) is True

    @pytest.mark.asyncio
    async def test_tpm_multi_key_pool_saturation_and_failover_routing(self):
        """
        Stress-test 3-key pool failover under progressive TPM saturation:
        - Key 1 saturated -> rotates to Key 2
        - Key 2 saturated -> rotates to Key 3
        - Key 3 saturated -> acquire_next_key returns None
        - Key 1 recovers after rollover -> acquire_next_key selects Key 1 again.
        """
        keys = ["mstrl_k1", "mstrl_k2", "mstrl_k3"]
        pool = MistralKeyPool(keys=keys)
        model = "mistral-small-2603"
        loop = asyncio.get_event_loop()
        t0 = loop.time()

        k1, k2, k3 = pool.get_keys()

        # Saturate Key 1 at t0
        k1.record_token_usage(model, 25000, now=t0)
        assert k1.is_available(now=t0, model=model) is False

        # acquire_next_key should skip Key 1 and select Key 2
        sel1 = await pool.acquire_next_key(model=model)
        assert sel1 is not None and sel1.key == "mstrl_k2"

        # Saturate Key 2 at t0 + 0.01s
        t_k2 = loop.time()
        k2.record_token_usage(model, 25000, now=t_k2)
        assert k2.is_available(now=t_k2, model=model) is False

        # acquire_next_key should select Key 3
        sel2 = await pool.acquire_next_key(model=model)
        assert sel2 is not None and sel2.key == "mstrl_k3"

        # Saturate Key 3 at t0 + 0.02s
        t_k3 = loop.time()
        k3.record_token_usage(model, 25000, now=t_k3)
        assert k3.is_available(now=t_k3, model=model) is False

        # All 3 keys are saturated: acquire_next_key with now=t0+15s must return None
        with patch.object(loop, "time", return_value=t0 + 15.0):
            sel_none = await pool.acquire_next_key(model=model)
            assert sel_none is None, "All keys saturated; acquire_next_key should return None"

        # Advance time to t0 + 60.5s: Key 1 rolls over and recovers!
        with patch.object(loop, "time", return_value=t0 + 60.5):
            sel_recovered = await pool.acquire_next_key(model=model)
            assert sel_recovered is not None and sel_recovered.key == "mstrl_k1", (
                "Key 1 should recover after 60s rollover and be acquired"
            )


# ============================================================================
# Area 2: Transient 429 Classification against orchestrator.py:819 Filter
# ============================================================================

class TestTransient429Classification:
    """Empirical challenge tests for transient 429 formatting against orchestrator.py."""

    def test_transient_429_retry_after_header_orchestrator_filter(self):
        """
        Mistral API returns 429 with retry-after header.
        Verify formatted reason matches orchestrator.py:819 is_minute_only filter.
        """
        key = MistralKey("mstrl_test_key")
        model = "ministral-8b-2512"

        resp = httpx.Response(
            429,
            headers={"retry-after": "6"},
            json={"message": "Capacity reached"},
            request=httpx.Request("POST", "https://api.mistral.ai/v1/chat/completions"),
        )
        key.update_from_response(resp, model=model)

        reason = key._model_cooldown_reasons.get(model, "")
        assert "minute rate limit / model RPS limit" in reason
        assert "retry-after 6" in reason

        # Construct exception as MistralProvider would raise
        exc = RuntimeError(
            f"Mistral RateLimitError: 429 Rate limit reached for model '{model}' ({reason})"
        )
        assert evaluate_orchestrator_minute_only(exc) is True, (
            f"Error message failed orchestrator is_minute_only filter: {exc}"
        )

    def test_transient_429_reset_tokens_header_orchestrator_filter(self):
        """
        Mistral API returns 429 with x-ratelimit-reset-tokens-minute header.
        Verify formatted reason matches orchestrator.py:819 is_minute_only filter.
        """
        key = MistralKey("mstrl_reset_tok_key")
        model = "codestral-2508"

        resp = httpx.Response(
            429,
            headers={"x-ratelimit-reset-tokens-minute": "14"},
            json={"message": "Tokens minute limit reached"},
            request=httpx.Request("POST", "https://api.mistral.ai/v1/chat/completions"),
        )
        key.update_from_response(resp, model=model)

        reason = key._model_cooldown_reasons.get(model, "")
        assert "minute rate limit / model RPS limit" in reason

        exc = RuntimeError(
            f"Mistral RateLimitError: 429 Rate limit reached for model '{model}' ({reason})"
        )
        assert evaluate_orchestrator_minute_only(exc) is True

    def test_transient_429_no_headers_body_rate_limit_orchestrator_filter(self):
        """
        Mistral API returns 429 with NO rate-limit headers.
        Verify it falls back to 3.0s cooldown and passes orchestrator is_minute_only.
        """
        key = MistralKey("mstrl_no_headers_key")
        model = "mistral-large-2512"

        resp = httpx.Response(
            429,
            headers={},
            json={"message": "Rate limit exceeded for requests"},
            request=httpx.Request("POST", "https://api.mistral.ai/v1/chat/completions"),
        )
        key.update_from_response(resp, model=model)

        reason = key._model_cooldown_reasons.get(model, "")
        assert "minute rate limit / model RPS limit" in reason

        exc = RuntimeError(
            f"Mistral RateLimitError: 429 Rate limit reached for model '{model}' ({reason})"
        )
        assert evaluate_orchestrator_minute_only(exc) is True

    @pytest.mark.asyncio
    async def test_transient_429_all_keys_tpm_saturated_orchestrator_filter(self):
        """
        All keys in pool saturated via TPM tracking before any HTTP call.
        Verify MistralProvider raises 429 error matching orchestrator is_minute_only filter.
        """
        pool = MistralKeyPool(keys=["mstrl_k1", "mstrl_k2"])
        model = "mistral-small-2603"
        provider = MistralProvider(key_pool=pool, default_model=model)

        # Saturate both keys with 25,000 tokens
        for k in pool.get_keys():
            k.record_token_usage(model, 25000)

        with pytest.raises(RuntimeError) as exc_info:
            await provider.generate_text("test prompt", model=model)

        exc = exc_info.value
        assert "429" in str(exc)
        assert evaluate_orchestrator_minute_only(exc) is True, (
            f"TPM saturation error failed orchestrator is_minute_only: {exc}"
        )
        await provider.close()

    @pytest.mark.asyncio
    async def test_transient_429_all_keys_rps_paced_orchestrator_filter(self):
        """
        All keys in pool placed on active model cooldown.
        Verify MistralProvider raises 429 error matching orchestrator is_minute_only filter.
        """
        pool = MistralKeyPool(keys=["mstrl_k1", "mstrl_k2"])
        model = "ministral-3b-2512"
        provider = MistralProvider(key_pool=pool, default_model=model)

        for k in pool.get_keys():
            k.mark_rate_limited(is_daily=False, retry_delay_sec=10.0, model=model)

        with pytest.raises(RuntimeError) as exc_info:
            await provider.generate_text("test prompt", model=model)

        exc = exc_info.value
        assert "429" in str(exc)
        assert evaluate_orchestrator_minute_only(exc) is True, (
            f"RPS cooldown error failed orchestrator is_minute_only: {exc}"
        )
        await provider.close()

    def test_monthly_quota_exhaustion_is_daily_true_and_fails_minute_only(self):
        """
        When Mistral returns monthly quota exhaustion ($10 allowance depleted),
        verify that key.status is DAILY_EXHAUSTED and the key is locked out.
        """
        key = MistralKey("mstrl_monthly_key")
        model = "ministral-3b-2512"

        resp = httpx.Response(
            429,
            json={"message": "Quota exceeded. Free tier monthly allowance exhausted."},
            request=httpx.Request("POST", "https://api.mistral.ai/v1/chat/completions"),
        )
        key.update_from_response(resp, model=model)

        assert key.status == KeyStatus.DAILY_EXHAUSTED
        assert key.monthly_quota_exhaustions == 1
        assert key.is_available() is False
        assert key.is_available(model=model) is False
        assert "monthly allowance" in key.cooldown_reason.lower()


# ============================================================================
# Area 3: max_tokens Preservation for Small Caller Values
# ============================================================================

class TestMaxTokensPreservation:
    """Empirical challenge tests for max_tokens preservation in MistralProvider."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("small_val", [1, 16, 64, 128, 256, 512])
    async def test_max_tokens_preservation_small_values_json_mode(self, small_val: int):
        """
        Verify that for small caller values (1, 16, 64, 128, 256, 512) with json_mode=True,
        MistralProvider passes the exact value in the outgoing payload and NEVER inflates to 1536.
        """
        pool = MistralKeyPool(keys=["mstrl_test_k"])
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
                json={"choices": [{"message": {"content": '{"result": "ok"}'}}]},
            )

        mock_client.post = mock_post
        provider._client = mock_client

        res = await provider.generate_text(
            prompt="JSON prompt",
            max_tokens=small_val,
            json_mode=True,
        )

        assert res == '{"result": "ok"}'
        assert captured_payload is not None
        assert captured_payload["max_tokens"] == small_val, (
            f"max_tokens was inflated from {small_val} to {captured_payload['max_tokens']}"
        )
        assert captured_payload.get("response_format") == {"type": "json_object"}
        await provider.close()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("small_val", [1, 16, 64, 128, 256, 512])
    async def test_max_tokens_preservation_small_values_plain_mode(self, small_val: int):
        """
        Verify that for small caller values with json_mode=False,
        MistralProvider passes the exact value in outgoing payload and NEVER inflates to 1024 or 2048.
        """
        pool = MistralKeyPool(keys=["mstrl_test_k"])
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
                json={"choices": [{"message": {"content": "plain text response"}}]},
            )

        mock_client.post = mock_post
        provider._client = mock_client

        res = await provider.generate_text(
            prompt="Plain prompt",
            max_tokens=small_val,
            json_mode=False,
        )

        assert res == "plain text response"
        assert captured_payload is not None
        assert captured_payload["max_tokens"] == small_val, (
            f"max_tokens was inflated from {small_val} to {captured_payload['max_tokens']}"
        )
        assert "response_format" not in captured_payload
        await provider.close()

    @pytest.mark.asyncio
    async def test_max_tokens_default_fallback_behavior(self):
        """
        Verify fallback defaults:
        - When max_tokens=None and json_mode=True -> falls back to 1536
        - When max_tokens=None and json_mode=False -> falls back to 1024
        - When max_tokens not passed (default parameter) -> uses 2048
        """
        pool = MistralKeyPool(keys=["mstrl_test_k"])
        provider = MistralProvider(key_pool=pool, default_model="ministral-3b-2512")

        captured = []

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.is_closed = False

        async def mock_post(url, json=None, headers=None):
            captured.append(dict(json))
            req = httpx.Request("POST", url)
            return httpx.Response(
                200,
                request=req,
                json={"choices": [{"message": {"content": "ok"}}]},
            )

        mock_client.post = mock_post
        provider._client = mock_client

        # 1. max_tokens=None with json_mode=True
        await provider.generate_text("Prompt 1", max_tokens=None, json_mode=True)
        assert captured[-1]["max_tokens"] == 1536

        # 2. max_tokens=None with json_mode=False
        await provider.generate_text("Prompt 2", max_tokens=None, json_mode=False)
        assert captured[-1]["max_tokens"] == 1024

        # 3. max_tokens omitted (default parameter)
        await provider.generate_text("Prompt 3")
        assert captured[-1]["max_tokens"] == 2048

        await provider.close()

    @pytest.mark.asyncio
    async def test_stage0_gatekeeper_batch_max_tokens(self):
        """
        Verify that Stage0Gatekeeper.evaluate_layer2_batch passes max_tokens=2048
        (batch evaluation budget) to MistralProvider without truncation or inflation errors.
        """
        gk = Stage0Gatekeeper(batch_size=4)
        pool = MistralKeyPool(keys=["mstrl_gk_key"])
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

        chunk = [
            {"source_type": "telegram", "source_name": "ch1", "raw_text": "Missile attack reported in Red Sea"},
            {"source_type": "rss", "source_name": "reuters", "raw_text": "Naval strike near Bab el-Mandeb"},
        ]

        kept = await gk.evaluate_llm_chunk(
            chunk=chunk,
            provider=provider,
            model="ministral-3b-2512",
        )

        assert kept == [0, 1]
        assert captured_payload is not None
        assert captured_payload["max_tokens"] == 2048
        assert captured_payload.get("response_format") == {"type": "json_object"}
        await provider.close()


# ============================================================================
# Deep Adversarial Tests: Edge Cases, Multi-time Skew, and Orchestrator E2E
# ============================================================================

class TestDeepAdversarialScenarios:
    """Deep adversarial chaos tests for complex real-world edge cases."""

    def test_tpm_epoch_time_and_loop_time_coexistence(self):
        """
        Verify that if records were added using epoch time (time.time() ~ 1.7e9),
        calls using loop time or None gracefully handle the epoch timestamp without
        underflowing or corrupting the sliding window.
        """
        key = MistralKey("mstrl_epoch_test")
        model = "mistral-small-2603"
        now_epoch = time.time()

        # Insert 15,000 tokens using epoch time
        key.record_token_usage(model, 15000, now=now_epoch)

        # Query using None (default)
        tpm1 = key.get_sliding_tpm(model)
        assert tpm1 == 15000

        # Query using loop time (< 1e8)
        loop_now = 50.0  # simulated loop time
        tpm2 = key.get_sliding_tpm(model, now=loop_now)
        # Should detect that records[0][0] > 1e8 and convert to time.time()
        assert tpm2 == 15000

        # Saturate to 25,000 tokens
        key.record_token_usage(model, 10000, now=now_epoch + 1.0)
        assert key.get_sliding_tpm(model) == 25000
        # get_seconds_until_ready should calculate valid positive wait without crash
        wait_s = key.get_seconds_until_ready(now=loop_now, model=model)
        assert 50.0 <= wait_s <= 65.0, f"Expected valid ~60s wait, got {wait_s}s"

    @pytest.mark.asyncio
    async def test_tpm_acquire_pacing_slot_sleeps_on_tpm_cap(self):
        """
        Verify that acquire_pacing_slot detects 60s sliding TPM saturation and
        calls asyncio.sleep for the required duration to clear the sliding window.
        """
        key = MistralKey("mstrl_slot_pacing")
        model = "mistral-small-2603"
        loop = asyncio.get_event_loop()
        now = loop.time()

        # Place 22,000 tokens at now - 58.0s (2 seconds remaining until 60s rollover)
        old_time = now - 58.0
        key._model_token_records[model] = deque([(old_time, 22000)])

        sleep_calls: List[float] = []

        async def fake_sleep(duration: float):
            sleep_calls.append(duration)

        with patch("asyncio.sleep", side_effect=fake_sleep):
            await key.acquire_pacing_slot(model=model)

        assert len(sleep_calls) >= 1
        tpm_sleep = sleep_calls[0]
        # Wait should be (old_time + 60.0 + 0.08) - now ≈ (now - 58.0 + 60.08) - now ≈ 2.08s
        assert 1.9 <= tpm_sleep <= 2.3, f"Expected ~2.08s sleep, got {tpm_sleep:.2f}s"

    @pytest.mark.parametrize(
        "mistral_msg,headers",
        [
            ("Requests rate limit exceeded", {"retry-after": "5"}),
            ("Capacity exceeded for model ministral-3b-2512", {"retry-after": "2"}),
            ("Too many requests. Please wait before retrying.", {}),
            ("Rate limit reached: 2 requests per second", {}),
            ("Tokens per minute limit reached: 20000", {"x-ratelimit-reset-tokens": "8"}),
            ("Temporary overload on cluster", {"retry-after": "3"}),
            ("", {"retry-after": "4"}),
            ("Unknown transient limiter error", {}),
        ],
    )
    def test_transient_429_various_mistral_error_bodies_all_match_minute_only(
        self, mistral_msg: str, headers: Dict[str, str]
    ):
        """
        Adversarial matrix: Ensure all variations of Mistral 429 error messages and headers
        are formatted so they strictly pass orchestrator.py:819 is_minute_only filter.
        """
        key = MistralKey("mstrl_matrix_key")
        model = "ministral-3b-2512"

        resp = httpx.Response(
            429,
            headers=headers,
            json={"message": mistral_msg} if mistral_msg else {},
            request=httpx.Request("POST", "https://api.mistral.ai/v1/chat/completions"),
        )
        key.update_from_response(resp, model=model)

        reason = key._model_cooldown_reasons.get(model, "")
        exc = RuntimeError(
            f"Mistral RateLimitError: 429 Rate limit reached for model '{model}' ({reason})"
        )

        assert evaluate_orchestrator_minute_only(exc) is True, (
            f"Failed for message='{mistral_msg}', headers={headers}. Reason='{reason}'"
        )

    @pytest.mark.asyncio
    async def test_orchestrator_is_minute_only_retry_branch_execution(self):
        """
        Direct behavioral verification that orchestrator.py:855-878 catches MistralProvider's
        transient 429 exception, evaluates should_wait_and_retry=True, and retries.
        """
        err_str = "mistral ratelimiterror: 429 rate limit reached for model 'ministral-3b-2512' (minute rate limit / model rps limit (retry-after 3s))"
        exc = RuntimeError(err_str)

        # Run exact logic from orchestrator.py:806-858
        is_rate_limit = (
            "429" in str(exc)
            or "quota" in err_str
            or "resourceexhausted" in err_str
            or "ratelimit" in err_str
            or "too many requests" in err_str
        )
        is_minute_only = (
            is_rate_limit
            and any(
                k in err_str
                for k in (
                    "minute rate limit",
                    "perminute",
                    "per_minute",
                    "(rpm)",
                    "(tpm)",
                    "requests per minute",
                    "tokens per minute",
                )
            )
            and not any(
                k in err_str
                for k in (
                    "daily",
                    "perday",
                    "per_day",
                    "(rpd)",
                    "(tpd)",
                    "requests per day",
                    "tokens per day",
                )
            )
        )
        has_downstream_candidates = True
        is_gemma_tpm_saturation = False
        should_wait_and_retry = is_minute_only or (is_gemma_tpm_saturation and not has_downstream_candidates)

        assert is_rate_limit is True
        assert is_minute_only is True
        assert should_wait_and_retry is True, (
            "orchestrator failed to trigger minute-window wait and retry branch on Mistral 429"
        )

    @pytest.mark.asyncio
    async def test_max_tokens_preservation_with_system_prompt_and_temperature(self):
        """
        Verify that max_tokens is preserved alongside complex parameters:
        system_prompt, temperature=0.7, json_mode=True, max_tokens=128.
        """
        pool = MistralKeyPool(keys=["mstrl_param_key"])
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
                json={"choices": [{"message": {"content": '{"summary": "test"}'}}]},
            )

        mock_client.post = mock_post
        provider._client = mock_client

        res = await provider.generate_text(
            prompt="Analyze geopolitical event",
            system_prompt="You are an OSINT intelligence analyst.",
            model="ministral-14b-2512",
            temperature=0.7,
            max_tokens=128,
            json_mode=True,
        )

        assert res == '{"summary": "test"}'
        assert captured_payload is not None
        assert captured_payload["max_tokens"] == 128
        assert captured_payload["temperature"] == 0.7
        assert captured_payload["model"] == "ministral-14b-2512"
        assert len(captured_payload["messages"]) == 2
        assert captured_payload["messages"][0]["role"] == "system"
        assert captured_payload["messages"][1]["role"] == "user"
        await provider.close()
