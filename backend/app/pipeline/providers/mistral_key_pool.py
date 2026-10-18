"""
backend/app/pipeline/providers/mistral_key_pool.py - Mistral Multi-Key Rotation & Limits Pool
=============================================================================================
Manages an asynchronous pool of Mistral API keys tailored to Mistral's limit system:
  - Monthly allowance tracking ($10 / month Free experiment allowance per key, resetting 1st of month).
  - Per-model RPS (requests per second) and TPM (tokens per minute) limits enforcement.
  - Round-robin load balancing across independent Mistral keys.
  - Dynamic 429 rate limit failover (per-model temporary cooldown vs monthly quota exhaustion).
  - Automatic 401/403 unauthorized key deactivation.
  - Live observability telemetry for monitoring and settings dashboard.
"""

import asyncio
from collections import deque
from datetime import datetime, timezone
from enum import Enum
import logging
import random
from typing import Any, Deque, Dict, List, Optional, Tuple
import httpx

from backend.app.pipeline.providers.base import parse_duration_seconds
from backend.app.pipeline.providers.key_pool import KeyStatus, mask_key, parse_key_list

logger = logging.getLogger("osint.ai.mistral.pool")


def _get_loop_time() -> float:
    try:
        return asyncio.get_running_loop().time()
    except RuntimeError:
        import time
        return time.time()


def seconds_until_next_month_utc() -> float:
    """Return seconds remaining until the 1st of the next calendar month (00:00 UTC)."""
    now = datetime.now(timezone.utc)
    year = now.year + (1 if now.month == 12 else 0)
    month = 1 if now.month == 12 else now.month + 1
    next_month = datetime(year, month, 1, 0, 0, 0, tzinfo=timezone.utc)
    return max(60.0, (next_month - now).total_seconds())


# Official Mistral Limits per Model (Platform Free Plan)
MISTRAL_MODEL_LIMITS: Dict[str, Dict[str, Any]] = {
    "codestral-2508": {"tpm": 625_000, "rps": 2.08},
    "codestral-embed": {"tpm": 50_000, "rps": 1.00},
    "glm-5-2": {"tpm": None, "rps": 1.00},
    "labs-leanstral-1-5-1": {"tpm": 5_000_000, "rps": 0.63},
    "ministral-14b-2512": {"tpm": 937_500, "rps": 0.50},
    "ministral-3b-2512": {"tpm": 1_300_000, "rps": 12.50},
    "ministral-8b-2512": {"tpm": 625_000, "rps": 3.13},
    "mistral-embed-2312": {"tpm": 20_000_000, "rps": 1.00},
    "mistral-large-2512": {"tpm": 250_000, "rps": 1.00},
    "mistral-large-4": {"tpm": 20_000, "rps": 0.83},
    "mistral-medium-latest": {"tpm": 20_000, "rps": 1.00},
    "mistral-moderation-2603": {"tpm": 50_000, "rps": 1.67},
    "mistral-small-2603": {"tpm": 20_000, "rps": 1.00},
    "voxtral-mini-2602": {"tpm": 50_000, "rps": 1.00},
    "voxtral-mini-transcribe-realtime-2602": {"tpm": 50_000, "rps": 1.00},
    "voxtral-mini-tts-2603": {"tpm": 50_000, "rps": 1.00},
    "voxtral-small-2507": {"tpm": 50_000, "rps": 1.00},
    "zai-glm-5-3": {"tpm": 20_000, "rps": 1.00},
}


def get_mistral_model_limits(model_name: Optional[str]) -> Dict[str, Any]:
    """Resolve model limits (TPM, RPS, min_interval) for a given Mistral model."""
    if not model_name:
        model_name = "ministral-3b-2512"
    norm = model_name.strip().lower()

    # Exact or stripped match
    if norm in MISTRAL_MODEL_LIMITS:
        item = MISTRAL_MODEL_LIMITS[norm]
        rps = float(item["rps"])
        return {
            "model": norm,
            "tpm": item["tpm"],
            "rps": rps,
            "min_interval": 1.0 / rps if rps > 0 else 0.1,
        }

    # Fuzzy match by prefix/family
    if "3b" in norm:
        rps = 12.50
        tpm = 1_300_000
    elif "8b" in norm or "nemo" in norm:
        rps = 3.13
        tpm = 625_000
    elif "14b" in norm:
        rps = 0.50
        tpm = 937_500
    elif "code" in norm:
        rps = 2.08
        tpm = 625_000
    elif "moderation" in norm:
        rps = 1.67
        tpm = 50_000
    elif "embed" in norm:
        rps = 1.00
        tpm = 20_000_000
    elif "large-4" in norm:
        rps = 0.83
        tpm = 20_000
    elif "leanstral" in norm:
        rps = 0.63
        tpm = 5_000_000
    elif "large" in norm:
        rps = 1.00
        tpm = 250_000
    elif "small" in norm or "medium" in norm:
        rps = 1.00
        tpm = 20_000
    elif "voxtral" in norm:
        rps = 1.00
        tpm = 50_000
    else:
        rps = 1.00
        tpm = 50_000

    return {
        "model": norm,
        "tpm": tpm,
        "rps": rps,
        "min_interval": 1.0 / rps if rps > 0 else 0.1,
    }


class MistralKey:
    """
    State, pacing, and rate limit telemetry for a single Mistral API key.
    Enforces independent per-key model RPS pacing and dynamic 429 failover.
    """

    def __init__(
        self,
        key: str,
        monthly_allowance_usd: float = 10.0,
    ):
        self.key: str = key.strip()
        self.masked_key: str = mask_key(self.key)
        self.status: KeyStatus = KeyStatus.ACTIVE
        self.cooldown_until: float = 0.0
        self.cooldown_reason: str = ""
        self.monthly_allowance_usd: float = monthly_allowance_usd

        self._pacing_lock = asyncio.Lock()
        self._last_request_time: float = 0.0
        self._last_request_by_model: Dict[str, float] = {}

        # Telemetry metrics
        self.total_requests: int = 0
        self.success_requests: int = 0
        self.minute_rate_limits: int = 0
        self.monthly_quota_exhaustions: int = 0
        self.auth_errors: int = 0
        self.last_used_at: Optional[str] = None

        # Per-model cooldown tracking
        self._model_cooldowns: Dict[str, float] = {}
        self._model_cooldown_reasons: Dict[str, str] = {}
        self._model_rate_limits: Dict[str, int] = {}

        # Live headers from Mistral
        self.remaining_requests: Optional[int] = None
        self.remaining_tokens: Optional[int] = None
        self.limit_requests: Optional[int] = None
        self.limit_tokens: Optional[int] = None
        self.reset_requests_sec: Optional[float] = None
        self.reset_tokens_sec: Optional[float] = None
        self.retry_after_sec: Optional[float] = None

    def is_available(self, now: Optional[float] = None, model: Optional[str] = None) -> bool:
        """Check if key is available to accept requests (optionally for a specific model)."""
        if self.status == KeyStatus.UNAUTHORIZED:
            return False

        current_time = now if now is not None else _get_loop_time()

        # Check global key cooldown (e.g. monthly quota or general lockout)
        if self.status in (KeyStatus.MINUTE_COOLDOWN, KeyStatus.DAILY_EXHAUSTED):
            if current_time >= self.cooldown_until:
                logger.info(
                    f"[MistralKey:{self.masked_key}] Global key cooldown expired. Restoring to ACTIVE status."
                )
                self.status = KeyStatus.ACTIVE
                self.cooldown_until = 0.0
                self.cooldown_reason = ""
            else:
                return False

        # If a specific model was requested, check per-model cooldown
        if model:
            norm_model = model.strip().lower()
            model_expiry = self._model_cooldowns.get(norm_model, 0.0)
            if model_expiry > 0:
                if current_time >= model_expiry:
                    self._model_cooldowns.pop(norm_model, None)
                    self._model_cooldown_reasons.pop(norm_model, None)
                    return True
                return False

        return self.status == KeyStatus.ACTIVE

    def get_model_cooldown_remaining(self, model: str, now: Optional[float] = None) -> float:
        """Return remaining seconds of cooldown for a specific model on this key."""
        if not model:
            return 0.0
        current_time = now if now is not None else _get_loop_time()
        norm_model = model.strip().lower()
        expiry = self._model_cooldowns.get(norm_model, 0.0)
        if expiry > current_time:
            return max(0.0, expiry - current_time)
        return 0.0

    def get_seconds_until_ready(self, now: Optional[float] = None, model: Optional[str] = None) -> float:
        """Estimate how many seconds until this key can issue its next request."""
        current_time = now if now is not None else _get_loop_time()
        if self.status == KeyStatus.UNAUTHORIZED:
            return float("inf")

        if self.status in (KeyStatus.MINUTE_COOLDOWN, KeyStatus.DAILY_EXHAUSTED):
            if current_time < self.cooldown_until:
                return max(0.0, self.cooldown_until - current_time)

        if model:
            model_wait = self.get_model_cooldown_remaining(model, now=current_time)
            if model_wait > 0:
                return model_wait

        # Model RPS spacing check
        limits = get_mistral_model_limits(model)
        min_interval = limits["min_interval"]
        norm_m = limits["model"]
        last_t = self._last_request_by_model.get(norm_m, 0.0)
        if last_t > 0:
            elapsed = current_time - last_t
            if elapsed < min_interval:
                return max(0.0, min_interval - elapsed)

        return 0.0

    async def acquire_pacing_slot(self, model: Optional[str] = None) -> None:
        """
        Enforce pacing and cooldown under the key's private lock according to Mistral's model limits.
        """
        async with self._pacing_lock:
            loop = asyncio.get_event_loop()
            now = loop.time()

            # 1. Honor key-level cooldown
            if now < self.cooldown_until:
                wait_rl = self.cooldown_until - now
                logger.info(
                    f"[MistralKey:{self.masked_key}] Waiting {wait_rl:.1f}s for key cooldown reset..."
                )
                await asyncio.sleep(wait_rl)
                now = loop.time()

            # 2. Honor model-level cooldown
            limits = get_mistral_model_limits(model)
            norm_model = limits["model"]
            m_exp = self._model_cooldowns.get(norm_model, 0.0)
            if now < m_exp:
                wait_m = m_exp - now
                logger.info(
                    f"[MistralKey:{self.masked_key}] Waiting {wait_m:.1f}s for model '{norm_model}' cooldown reset..."
                )
                await asyncio.sleep(wait_m)
                now = loop.time()

            # 3. Enforce model RPS spacing
            min_interval = limits["min_interval"]
            last_m_time = self._last_request_by_model.get(norm_model, 0.0)
            if last_m_time > 0:
                elapsed = now - last_m_time
                if elapsed < min_interval:
                    wait_spacing = min_interval - elapsed
                    await asyncio.sleep(wait_spacing)
                    now = loop.time()

            self._last_request_by_model[norm_model] = now
            self._last_request_time = now
            self.total_requests += 1
            self.last_used_at = datetime.now(timezone.utc).isoformat()

    def update_from_response(self, response: httpx.Response, model: Optional[str] = None) -> None:
        """
        Update key status and rate-limit state from HTTP response headers and status.
        Distinguishes between monthly allowance exhaustion ($10/mo limit) and per-model RPS/TPM limits.
        """
        headers = response.headers

        # Parse retry-after and ratelimit headers
        raw_retry = headers.get("retry-after")
        retry_after = parse_duration_seconds(raw_retry)

        raw_rem_req = (
            headers.get("x-ratelimit-remaining-req-minute")
            or headers.get("x-ratelimit-remaining-requests")
            or headers.get("ratelimit-remaining")
        )
        raw_rem_tok = (
            headers.get("x-ratelimit-remaining-tokens-minute")
            or headers.get("x-ratelimit-remaining-tokens")
        )
        raw_lim_req = (
            headers.get("x-ratelimit-limit-req-minute")
            or headers.get("x-ratelimit-limit-requests")
            or headers.get("ratelimit-limit")
        )
        raw_lim_tok = (
            headers.get("x-ratelimit-limit-tokens-minute")
            or headers.get("x-ratelimit-limit-tokens")
        )
        raw_reset_req = (
            headers.get("x-ratelimit-reset-req-minute")
            or headers.get("x-ratelimit-reset-requests")
            or headers.get("ratelimit-reset")
        )
        raw_reset_tok = (
            headers.get("x-ratelimit-reset-tokens-minute")
            or headers.get("x-ratelimit-reset-tokens")
        )

        rem_req = int(raw_rem_req) if raw_rem_req is not None and str(raw_rem_req).strip().isdigit() else None
        rem_tok = int(raw_rem_tok) if raw_rem_tok is not None and str(raw_rem_tok).strip().isdigit() else None
        lim_req = int(raw_lim_req) if raw_lim_req is not None and str(raw_lim_req).strip().isdigit() else None
        lim_tok = int(raw_lim_tok) if raw_lim_tok is not None and str(raw_lim_tok).strip().isdigit() else None

        reset_req_sec = parse_duration_seconds(raw_reset_req)
        reset_tok_sec = parse_duration_seconds(raw_reset_tok)

        self.retry_after_sec = retry_after
        self.remaining_requests = rem_req
        self.remaining_tokens = rem_tok
        self.limit_requests = lim_req
        self.limit_tokens = lim_tok
        self.reset_requests_sec = reset_req_sec
        self.reset_tokens_sec = reset_tok_sec

        now = _get_loop_time()

        # HTTP 429: Rate Limit or Monthly Quota
        if response.status_code == 429:
            err_body = ""
            try:
                err_body = response.text.lower()
            except Exception:
                pass

            is_monthly = any(
                w in err_body
                for w in (
                    "quota",
                    "monthly",
                    "allowance",
                    "credit",
                    "balance",
                    "insufficient_quota",
                    "payment",
                )
            )

            if is_monthly:
                # Key exhausted its $10 monthly allowance, cooldown until 1st of next month
                cooldown_sec = (
                    retry_after
                    if (retry_after is not None and retry_after > 3600.0)
                    else seconds_until_next_month_utc()
                )
                self.mark_rate_limited(
                    is_daily=True,
                    retry_delay_sec=cooldown_sec,
                    reason="Monthly allowance ($10) exhausted",
                    model=None,
                )
            else:
                # Minute TPM or model RPS rate limit: apply cooldown to model only!
                effective_sec = retry_after or reset_tok_sec or reset_req_sec or 3.0
                effective_sec = max(effective_sec, 2.0)
                reason = f"model rate limit (retry-after {effective_sec:g}s)"
                self.mark_rate_limited(
                    is_daily=False,
                    retry_delay_sec=effective_sec,
                    reason=reason,
                    model=model,
                )
            return

        # HTTP 401 / 403: Invalid or Unauthorized Key
        if response.status_code in (401, 403):
            self.mark_unauthorized()
            return

        # HTTP 200: Success
        if response.status_code == 200:
            self.mark_success(model=model)
            if rem_tok == 0 and reset_tok_sec is not None and reset_tok_sec > 0:
                if model:
                    norm_m = model.strip().lower()
                    self._model_cooldowns[norm_m] = now + reset_tok_sec
                    self._model_cooldown_reasons[norm_m] = f"tokens depleted (reset in {reset_tok_sec:g}s)"
                else:
                    self.status = KeyStatus.MINUTE_COOLDOWN
                    self.cooldown_until = now + reset_tok_sec
                    self.cooldown_reason = f"tokens depleted (reset in {reset_tok_sec:g}s)"

    def mark_success(self, model: Optional[str] = None) -> None:
        """Record successful call on this key."""
        self.success_requests += 1
        if model:
            norm_m = model.strip().lower()
            self._model_cooldowns.pop(norm_m, None)
            self._model_cooldown_reasons.pop(norm_m, None)
        if self.status != KeyStatus.ACTIVE and self.status != KeyStatus.UNAUTHORIZED:
            self.status = KeyStatus.ACTIVE
            self.cooldown_until = 0.0
            self.cooldown_reason = ""

    def mark_rate_limited(
        self,
        is_daily: bool,
        retry_delay_sec: Optional[float] = None,
        reason: str = "",
        model: Optional[str] = None,
    ) -> None:
        """
        Mark rate limit. If model is provided and not a global quota exhaustion,
        locks strictly that model on this key while keeping the key ACTIVE for other models.
        """
        now = _get_loop_time()
        self.cooldown_reason = reason or "HTTP 429 Rate Limit"
        delay = float(retry_delay_sec) if (retry_delay_sec is not None and retry_delay_sec > 0) else (86400.0 if is_daily else 3.0)

        if model and not is_daily:
            norm_model = model.strip().lower()
            self._model_cooldowns[norm_model] = now + delay
            self._model_cooldown_reasons[norm_model] = reason or f"Rate limit on {model}"
            self._model_rate_limits[norm_model] = self._model_rate_limits.get(norm_model, 0) + 1
            self.minute_rate_limits += 1
            logger.warning(
                f"[MistralKey:{self.masked_key}] Model '{model}' rate limited ({reason}). "
                f"Cooldown {delay:.2f}s applied strictly to model '{model}'. Key remains ACTIVE for other models."
            )
            if self.status != KeyStatus.UNAUTHORIZED:
                self.status = KeyStatus.ACTIVE
            return

        if is_daily:
            self.status = KeyStatus.DAILY_EXHAUSTED
            self.monthly_quota_exhaustions += 1
            self.cooldown_until = now + delay
            logger.warning(
                f"[MistralKey:{self.masked_key}] Monthly quota exhausted ({reason}). "
                f"Cooldown applied for {delay:.2f}s until monthly reset."
            )
        else:
            self.status = KeyStatus.MINUTE_COOLDOWN
            self.minute_rate_limits += 1
            self.cooldown_until = now + delay
            logger.warning(
                f"[MistralKey:{self.masked_key}] Rate limit reached ({reason}). "
                f"Cooldown applied for {delay:.2f}s."
            )

    def mark_unauthorized(self) -> None:
        """Mark key permanently invalid following HTTP 401/403."""
        self.status = KeyStatus.UNAUTHORIZED
        self.auth_errors += 1
        logger.error(
            f"[MistralKey:{self.masked_key}] Permanently deactivated due to HTTP 401/403 Unauthorized."
        )

    def to_dict(self) -> Dict[str, Any]:
        """Telemetry representation for monitoring."""
        now = _get_loop_time()
        cooldown_remaining = max(0.0, self.cooldown_until - now) if self.cooldown_until > 0 else 0.0

        active_model_cds: Dict[str, Dict[str, Any]] = {}
        throttled_models_list: List[str] = []
        for m_name, exp in list(self._model_cooldowns.items()):
            if exp > now:
                rem_m = round(max(0.0, exp - now), 1)
                active_model_cds[m_name] = {
                    "cooldown_remaining_sec": rem_m,
                    "reason": self._model_cooldown_reasons.get(m_name, "Rate limit"),
                }
                throttled_models_list.append(m_name)
            else:
                self._model_cooldowns.pop(m_name, None)
                self._model_cooldown_reasons.pop(m_name, None)

        return {
            "masked_key": self.masked_key,
            "status": self.status.value,
            "is_available": self.is_available(now),
            "cooldown_remaining_sec": round(cooldown_remaining, 1),
            "cooldown_reason": self.cooldown_reason,
            "model_cooldowns": active_model_cds,
            "throttled_models": throttled_models_list,
            "monthly_allowance_usd": self.monthly_allowance_usd,
            "total_requests": self.total_requests,
            "success_requests": self.success_requests,
            "minute_rate_limits": self.minute_rate_limits,
            "monthly_quota_exhaustions": self.monthly_quota_exhaustions,
            "auth_errors": self.auth_errors,
            "last_used_at": self.last_used_at,
            "remaining_requests": self.remaining_requests,
            "remaining_tokens": self.remaining_tokens,
            "limit_requests": self.limit_requests,
            "limit_tokens": self.limit_tokens,
            "reset_requests_sec": self.reset_requests_sec,
            "reset_tokens_sec": self.reset_tokens_sec,
            "retry_after_sec": self.retry_after_sec,
        }


class MistralKeyPool:
    """
    Thread-safe, round-robin load-balancing pool for multiple Mistral API keys.
    Each key has an independent Free tier allowance ($10/mo) and model RPS/TPM limits.
    """

    def __init__(self, keys: Optional[List[str]] = None):
        self._keys: List[MistralKey] = []
        self._round_robin_idx: int = 0
        self._pool_lock = asyncio.Lock()
        if keys:
            self.set_keys(keys)

    @property
    def total_keys(self) -> int:
        return len(self._keys)

    @property
    def keys(self) -> List[str]:
        return [k.key for k in self._keys]

    def set_keys(self, raw_keys: Any) -> None:
        """Set or update the active list of keys, preserving metrics for existing keys."""
        parsed = parse_key_list(raw_keys)
        if not parsed:
            self._keys = []
            return

        existing_map = {k.key: k for k in self._keys}
        new_list: List[MistralKey] = []
        for raw in parsed:
            if raw in existing_map:
                new_list.append(existing_map[raw])
            else:
                new_list.append(MistralKey(key=raw))

        self._keys = new_list
        logger.info(
            f"[MistralKeyPool] Initialized with {len(self._keys)} key(s): "
            f"{', '.join(k.masked_key for k in self._keys)}"
        )

    def add_key(self, key: str) -> None:
        """Add a single key to the pool if not already present."""
        clean = key.strip()
        if not clean or any(k.key == clean for k in self._keys):
            return
        self._keys.append(MistralKey(key=clean))

    def get_keys(self) -> List[MistralKey]:
        return list(self._keys)

    async def acquire_next_key(self, model: Optional[str] = None) -> Optional[MistralKey]:
        """
        Select next available key using round-robin distribution with cooldown avoidance.
        If model is specified, filters out keys where that specific model is in cooldown.
        Returns None if all keys are unavailable for that model.
        """
        async with self._pool_lock:
            if not self._keys:
                return None

            loop = asyncio.get_event_loop()
            now = loop.time()
            n = len(self._keys)

            # 1. Round-robin scan for an immediately available key
            for offset in range(n):
                idx = (self._round_robin_idx + offset) % n
                candidate = self._keys[idx]
                if candidate.is_available(now, model=model):
                    self._round_robin_idx = (idx + 1) % n
                    return candidate

            # 2. Inspect if any key will be ready shortly (within 2.0s jitter/window)
            best_key: Optional[MistralKey] = None
            best_wait = float("inf")
            for candidate in self._keys:
                if candidate.status == KeyStatus.UNAUTHORIZED:
                    continue
                wait = candidate.get_seconds_until_ready(now, model=model)
                if wait < best_wait:
                    best_wait = wait
                    best_key = candidate

            if best_key and best_wait <= 2.0:
                return best_key

            return None

    def get_telemetry(self) -> Dict[str, Any]:
        """Return comprehensive live status for all Mistral keys in the pool."""
        now = _get_loop_time()

        key_stats = [k.to_dict() for k in self._keys]
        active_count = sum(1 for k in self._keys if k.is_available(now))
        keys_in_cd = sum(
            1 for k in self._keys
            if k.status in (KeyStatus.MINUTE_COOLDOWN, KeyStatus.DAILY_EXHAUSTED) and k.cooldown_until > now
        )
        total_requests = sum(k.total_requests for k in self._keys)
        total_success = sum(k.success_requests for k in self._keys)

        return {
            "total_keys": len(self._keys),
            "active_keys": active_count,
            "keys_in_cooldown": keys_in_cd,
            "total_requests": total_requests,
            "total_success": total_success,
            "monthly_allowance_usd_total": len(self._keys) * 10.0,
            "keys": key_stats,
        }
