"""
backend/app/pipeline/providers/key_pool.py - Groq Multi-Key Rotation & Load Balancing Pool
==========================================================================================
Manages an asynchronous pool of Groq API keys with:
  - Round-robin load balancing across healthy keys.
  - Per-key independent 60-second sliding-window pacing (28 RPM / 2.1s gap per key).
  - Dynamic 429 rate limit failover (minute vs daily quota classification).
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

from backend.app.pipeline.providers.base import classify_rate_limit_429, parse_duration_seconds

logger = logging.getLogger("osint.ai.groq.pool")


def _get_loop_time() -> float:
    try:
        return asyncio.get_running_loop().time()
    except RuntimeError:
        import time
        return time.time()


class KeyStatus(str, Enum):
    ACTIVE = "active"
    MINUTE_COOLDOWN = "minute_cooldown"
    DAILY_EXHAUSTED = "daily_exhausted"
    UNAUTHORIZED = "unauthorized"


def mask_key(k: Optional[str]) -> str:
    """Safely mask key for logs and UI display, preserving suffix."""
    if not k:
        return ""
    s = str(k).strip()
    if len(s) <= 8:
        return "***"
    return f"{s[:4]}...{s[-4:]}"


def parse_key_list(raw_keys: Any) -> List[str]:
    """Parse comma, newline, or whitespace delimited string or list of API keys."""
    if not raw_keys:
        return []
    if isinstance(raw_keys, (list, tuple, set)):
        items = list(raw_keys)
    elif isinstance(raw_keys, str):
        # Support comma and newline separated formats
        raw = raw_keys.replace("\r", "\n").replace(",", "\n")
        items = raw.split("\n")
    else:
        items = [str(raw_keys)]

    parsed: List[str] = []
    seen = set()
    for item in items:
        clean = str(item).strip().strip("'\"")
        if clean and not clean.startswith("***") and clean not in seen:
            seen.add(clean)
            parsed.append(clean)
    return parsed


class GroqKey:
    """
    State, pacing, and rate limit telemetry for a single Groq API key.
    Locks dynamically based on what Groq returns (retry-after and x-ratelimit-* headers),
    without assuming the key is locked or enforcing arbitrary client-side lockouts.
    """

    def __init__(
        self,
        key: str,
        min_interval_sec: float = 0.0,
        max_rpm: int = 0,
        max_minute_wait_sec: float = 65.0,
    ):
        self.key: str = key.strip()
        self.masked_key: str = mask_key(self.key)
        self.status: KeyStatus = KeyStatus.ACTIVE
        self.cooldown_until: float = 0.0
        self.cooldown_reason: str = ""
        self.min_interval_sec: float = min_interval_sec
        self.max_rpm: int = max_rpm
        self.max_minute_wait_sec: float = max_minute_wait_sec

        self._pacing_lock = asyncio.Lock()
        self._last_request_time: float = 0.0
        self._request_timestamps: Deque[float] = deque()

        # Telemetry metrics
        self.total_requests: int = 0
        self.success_requests: int = 0
        self.minute_rate_limits: int = 0
        self.daily_quota_exhaustions: int = 0
        self.auth_errors: int = 0
        self.last_used_at: Optional[str] = None

        # Per-(key, model) bucket isolation (locks, headers, sliding RPM/TPM, daily counters)
        self._model_pacing_locks: Dict[str, asyncio.Lock] = {}
        self._model_headers: Dict[str, Dict[str, Any]] = {}
        self._model_request_timestamps: Dict[str, Deque[float]] = {}
        self._model_token_records: Dict[str, Deque[Tuple[float, int]]] = {}
        self._model_daily_tokens: Dict[str, int] = {}
        self._model_daily_requests: Dict[str, int] = {}
        self._model_last_request_time: Dict[str, float] = {}
        self._current_date_utc: str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self._created_loop_time: float = _get_loop_time()
        self._model_daily_last_reset: Dict[str, float] = {}
        self._model_daily_reset_at: Dict[str, float] = {}

        # Per-model cooldown tracking
        self._model_cooldowns: Dict[str, float] = {}  # model_name.lower() -> cooldown_until loop timestamp
        self._model_cooldown_reasons: Dict[str, str] = {}
        self._model_rate_limits: Dict[str, int] = {}

        # Live rate limit headers returned by Groq
        self.remaining_requests: Optional[int] = None
        self.remaining_tokens: Optional[int] = None
        self.limit_requests: Optional[int] = None
        self.limit_tokens: Optional[int] = None
        self.reset_requests_sec: Optional[float] = None
        self.reset_tokens_sec: Optional[float] = None
        self.retry_after_sec: Optional[float] = None

    def _get_model_lock(self, norm_model: str) -> asyncio.Lock:
        """Get or initialize the private pacing lock for a specific model bucket on this key."""
        if norm_model not in self._model_pacing_locks:
            self._model_pacing_locks[norm_model] = asyncio.Lock()
        return self._model_pacing_locks[norm_model]

    def _check_daily_rollover(self, current_time: Optional[float] = None, model: Optional[str] = None) -> None:
        """
        Reset daily token and request counters when:
        1. UTC calendar date changes.
        2. Simulated or elapsed loop time exceeds 24 hours (86,400s) since last reset.
        3. Provider reset header timestamp has elapsed.
        """
        now_t = current_time if current_time is not None else _get_loop_time()
        today_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        if today_utc != self._current_date_utc:
            self._current_date_utc = today_utc
            self._model_daily_tokens.clear()
            self._model_daily_requests.clear()
            self._model_daily_last_reset.clear()
            self._model_daily_reset_at.clear()
            return

        # Check per-model elapsed time or header reset timestamp
        models_to_check = [model.strip().lower()] if model else list(set(self._model_daily_tokens.keys()) | set(self._model_daily_requests.keys()))
        for norm_m in models_to_check:
            last_reset = self._model_daily_last_reset.setdefault(norm_m, self._created_loop_time)
            reset_at = self._model_daily_reset_at.get(norm_m, 0.0)
            if (reset_at > 0.0 and now_t >= reset_at) or (now_t - last_reset >= 86400.0):
                self._model_daily_tokens[norm_m] = 0
                self._model_daily_requests[norm_m] = 0
                self._model_daily_last_reset[norm_m] = now_t
                self._model_daily_reset_at.pop(norm_m, None)

    def record_token_usage(self, model: str, tokens: int) -> None:
        """Record token usage for sliding-window TPM (8,000) and daily TPD (200,000) tracking."""
        if not model or tokens <= 0:
            return
        norm_m = model.strip().lower()
        now = _get_loop_time()
        self._check_daily_rollover(current_time=now, model=norm_m)
        self._model_daily_last_reset.setdefault(norm_m, now)
        self._model_token_records.setdefault(norm_m, deque()).append((now, tokens))
        self._model_daily_tokens[norm_m] = self._model_daily_tokens.get(norm_m, 0) + tokens

    def is_available(self, now: Optional[float] = None, model: Optional[str] = None) -> bool:
        """
        Check if key is available to accept requests.
        If model is specified, checks whether this specific model is currently
        in a per-model rate limit cooldown or daily quota exhaustion on this key.
        """
        if self.status == KeyStatus.UNAUTHORIZED:
            return False

        current_time = now if now is not None else _get_loop_time()
        self._check_daily_rollover(current_time=current_time, model=model)

        # Check key-level global cooldown (e.g. account/org daily quota or 401/403)
        if self.status in (KeyStatus.MINUTE_COOLDOWN, KeyStatus.DAILY_EXHAUSTED):
            if current_time >= self.cooldown_until:
                # Cooldown expired, restore to active
                logger.info(
                    f"[GroqKey:{self.masked_key}] Global key cooldown expired. Restoring to ACTIVE status."
                )
                self.status = KeyStatus.ACTIVE
                self.cooldown_until = 0.0
                self.cooldown_reason = ""
            else:
                return False

        # If a specific model was requested, check per-model cooldown and daily limits
        if model:
            norm_model = model.strip().lower()
            model_expiry = self._model_cooldowns.get(norm_model, 0.0)
            if model_expiry == 0.0 and "/" in norm_model:
                model_expiry = self._model_cooldowns.get(norm_model.split("/")[-1], 0.0)
            elif model_expiry == 0.0 and "/" not in norm_model:
                for k_cd, exp in self._model_cooldowns.items():
                    if k_cd.endswith(f"/{norm_model}"):
                        model_expiry = exp
                        break
            if model_expiry > 0:
                if current_time >= model_expiry:
                    # Model cooldown expired on this key
                    self._model_cooldowns.pop(norm_model, None)
                    if "/" in norm_model:
                        self._model_cooldowns.pop(norm_model.split("/")[-1], None)
                    self._model_cooldown_reasons.pop(norm_model, None)
                else:
                    return False

            # Check daily bucket limits for this model (dynamic RPD or 200,000 TPD)
            limit_r = (
                self._model_headers.get(norm_model, {}).get("limit_requests")
                or self.limit_requests
                or 1000
            )
            if self._model_daily_requests.get(norm_model, 0) >= limit_r:
                return False
            if self._model_daily_tokens.get(norm_model, 0) >= 200000:
                return False

            # Check sliding TPM/RPM pacing wait time (> 3.0s means waiting on sliding window)
            if self.get_seconds_until_ready(current_time, model=norm_model) > 3.0:
                return False

            return True

        if self.get_seconds_until_ready(current_time) > 3.0:
            return False

        return self.status == KeyStatus.ACTIVE

    def get_model_cooldown_remaining(self, model: str, now: Optional[float] = None) -> float:
        """Return remaining seconds of cooldown for a specific model on this key."""
        if not model:
            return 0.0
        current_time = now if now is not None else _get_loop_time()
        norm_model = model.strip().lower()
        expiry = self._model_cooldowns.get(norm_model, 0.0)
        if expiry == 0.0 and "/" in norm_model:
            expiry = self._model_cooldowns.get(norm_model.split("/")[-1], 0.0)
        elif expiry == 0.0 and "/" not in norm_model:
            for k_cd, exp in self._model_cooldowns.items():
                if k_cd.endswith(f"/{norm_model}"):
                    expiry = exp
                    break
        if expiry > current_time:
            return max(0.0, expiry - current_time)
        return 0.0

    def get_seconds_until_ready(self, now: Optional[float] = None, model: Optional[str] = None) -> float:
        """Estimate how many seconds until this key can issue its next request (optionally for a model)."""
        current_time = now if now is not None else _get_loop_time()
        self._check_daily_rollover(current_time=current_time, model=model)
        if self.status == KeyStatus.UNAUTHORIZED:
            return float("inf")

        if self.status in (KeyStatus.MINUTE_COOLDOWN, KeyStatus.DAILY_EXHAUSTED):
            if current_time < self.cooldown_until:
                return max(0.0, self.cooldown_until - current_time)

        if model:
            norm_model = model.strip().lower()
            model_wait = self.get_model_cooldown_remaining(model, now=current_time)
            if model_wait > 0:
                return model_wait

            # Check daily quota limits (dynamic RPD or 200,000 TPD)
            limit_r = (
                self._model_headers.get(norm_model, {}).get("limit_requests")
                or self.limit_requests
                or 1000
            )
            if self._model_daily_requests.get(norm_model, 0) >= limit_r or self._model_daily_tokens.get(norm_model, 0) >= 200000:
                hdr = self._model_headers.get(norm_model, {})
                return hdr.get("reset_requests_sec") or 3600.0

            wait_time = 0.0
            # 60s sliding window RPM (30 RPM default)
            timestamps = self._model_request_timestamps.get(norm_model)
            rpm_limit = self.max_rpm if self.max_rpm > 0 else 30
            if timestamps:
                active_ts = [t for t in timestamps if (current_time - t) < 60.0]
                if len(active_ts) >= rpm_limit:
                    wait_time = max(wait_time, (active_ts[0] + 60.2) - current_time)

            # 60s sliding window TPM (8,000 TPM limit)
            token_records = self._model_token_records.get(norm_model)
            if token_records:
                active_toks = [(ts, tok) for ts, tok in token_records if (current_time - ts) < 60.0]
                if sum(tok for _, tok in active_toks) >= 8000:
                    wait_time = max(wait_time, (active_toks[0][0] + 60.2) - current_time)

            if self.min_interval_sec > 0 and self._model_last_request_time.get(norm_model, 0.0) > 0:
                elapsed = current_time - self._model_last_request_time[norm_model]
                if elapsed < self.min_interval_sec:
                    wait_time = max(wait_time, self.min_interval_sec - elapsed)

            return max(0.0, wait_time)

        # Sliding window check without model (only if max_rpm explicitly set > 0)
        wait_time = 0.0
        if self.max_rpm > 0 and self._request_timestamps and len(self._request_timestamps) >= self.max_rpm:
            earliest = self._request_timestamps[0]
            if (current_time - earliest) < 60.0:
                wait_time = max(wait_time, (earliest + 60.2) - current_time)

        if self.min_interval_sec > 0 and self._last_request_time > 0:
            elapsed = current_time - self._last_request_time
            if elapsed < self.min_interval_sec:
                wait_time = max(wait_time, self.min_interval_sec - elapsed)

        return max(0.0, wait_time)

    async def acquire_pacing_slot(self, model: Optional[str] = None) -> None:
        """
        Enforce pacing and cooldown under the key and model's private lock.
        Requests on this key and model do not block requests on other keys or models in the pool.
        """
        norm_model = model.strip().lower() if model else None
        lock = self._get_model_lock(norm_model) if norm_model else self._pacing_lock

        async with lock:
            loop = asyncio.get_event_loop()
            now = loop.time()
            self._check_daily_rollover(current_time=now, model=norm_model)
            if norm_model:
                self._model_daily_last_reset.setdefault(norm_model, now)

            # 1. Honor active key-level cooldown if any (set strictly by what Groq returned)
            if now < self.cooldown_until:
                wait_rl = self.cooldown_until - now
                logger.info(
                    f"[GroqKey:{self.masked_key}] Waiting {wait_rl:.1f}s for key cooldown reset..."
                )
                await asyncio.sleep(wait_rl)
                now = loop.time()

            # 2. Honor model-level cooldown if specified and pending
            if norm_model:
                m_exp = self._model_cooldowns.get(norm_model, 0.0)
                if now < m_exp:
                    wait_m = m_exp - now
                    logger.info(
                        f"[GroqKey:{self.masked_key}] Waiting {wait_m:.1f}s for model '{model}' cooldown reset..."
                    )
                    await asyncio.sleep(wait_m)
                    now = loop.time()

            # 3. Enforce 60-second sliding-window RPM cap
            if norm_model:
                timestamps = self._model_request_timestamps.setdefault(norm_model, deque())
                while timestamps and (now - timestamps[0]) >= 60.0:
                    timestamps.popleft()

                rpm_limit = self.max_rpm if self.max_rpm > 0 else 30
                if len(timestamps) >= rpm_limit:
                    window_wait = (timestamps[0] + 60.2) - now
                    if window_wait > 0:
                        logger.info(
                            f"[GroqKey:{self.masked_key}:{norm_model}] Reached {rpm_limit} RPM sliding-window cap; "
                            f"pacing {window_wait:.1f}s..."
                        )
                        await asyncio.sleep(window_wait)
                        now = loop.time()
                    while timestamps and (now - timestamps[0]) >= 60.0:
                        timestamps.popleft()

                # 4. Enforce 60-second sliding-window TPM cap (8,000 TPM)
                token_records = self._model_token_records.setdefault(norm_model, deque())
                while token_records and (now - token_records[0][0]) >= 60.0:
                    token_records.popleft()

                current_window_tokens = sum(tok for _, tok in token_records)
                if current_window_tokens >= 8000:
                    tpm_wait = (token_records[0][0] + 60.2) - now
                    if tpm_wait > 0:
                        logger.info(
                            f"[GroqKey:{self.masked_key}:{norm_model}] Reached 8,000 TPM sliding-window cap; "
                            f"pacing {tpm_wait:.1f}s..."
                        )
                        await asyncio.sleep(tpm_wait)
                        now = loop.time()
                    while token_records and (now - token_records[0][0]) >= 60.0:
                        token_records.popleft()

            elif self.max_rpm > 0:
                while self._request_timestamps and (now - self._request_timestamps[0]) >= 60.0:
                    self._request_timestamps.popleft()

                if len(self._request_timestamps) >= self.max_rpm:
                    window_wait = (self._request_timestamps[0] + 60.2) - now
                    if window_wait > 0:
                        logger.info(
                            f"[GroqKey:{self.masked_key}] Reached {self.max_rpm} RPM sliding-window cap; "
                            f"pacing {window_wait:.1f}s..."
                        )
                        await asyncio.sleep(window_wait)
                        now = loop.time()
                    while self._request_timestamps and (now - self._request_timestamps[0]) >= 60.0:
                        self._request_timestamps.popleft()

            # 5. Enforce minimum spacing between consecutive requests on this key (if configured)
            last_req = self._model_last_request_time.get(norm_model, 0.0) if norm_model else self._last_request_time
            if self.min_interval_sec > 0 and last_req > 0:
                elapsed = now - last_req
                if elapsed < self.min_interval_sec:
                    await asyncio.sleep(self.min_interval_sec - elapsed)
                    now = loop.time()

            if norm_model:
                self._model_last_request_time[norm_model] = now
                self._model_request_timestamps.setdefault(norm_model, deque()).append(now)
                self._model_daily_requests[norm_model] = self._model_daily_requests.get(norm_model, 0) + 1

            self._last_request_time = now
            if self.max_rpm > 0:
                self._request_timestamps.append(now)

            self.total_requests += 1
            self.last_used_at = datetime.now(timezone.utc).isoformat()

    def update_from_response(self, response: httpx.Response, model: Optional[str] = None) -> None:
        """
        Dynamically update key status and rate-limit state using the exact HTTP headers
        returned by Groq (retry-after and x-ratelimit-*), eliminating arbitrary numbers.
        If model is specified, rate limit cooldowns are isolated strictly to that model.
        """
        headers = response.headers

        # 1. Parse headers returned by Groq
        raw_retry_after = headers.get("retry-after")
        retry_after = parse_duration_seconds(raw_retry_after)

        raw_rem_req = headers.get("x-ratelimit-remaining-requests")
        raw_rem_tok = headers.get("x-ratelimit-remaining-tokens")
        raw_lim_req = headers.get("x-ratelimit-limit-requests")
        raw_lim_tok = headers.get("x-ratelimit-limit-tokens")
        raw_reset_req = headers.get("x-ratelimit-reset-requests")
        raw_reset_tok = headers.get("x-ratelimit-reset-tokens")

        rem_requests = int(raw_rem_req) if raw_rem_req is not None and str(raw_rem_req).strip().isdigit() else None
        rem_tokens = int(raw_rem_tok) if raw_rem_tok is not None and str(raw_rem_tok).strip().isdigit() else None
        lim_requests = int(raw_lim_req) if raw_lim_req is not None and str(raw_lim_req).strip().isdigit() else None
        lim_tokens = int(raw_lim_tok) if raw_lim_tok is not None and str(raw_lim_tok).strip().isdigit() else None

        reset_requests_sec = parse_duration_seconds(raw_reset_req)
        reset_tokens_sec = parse_duration_seconds(raw_reset_tok)

        self.retry_after_sec = retry_after
        self.remaining_requests = rem_requests
        self.remaining_tokens = rem_tokens
        self.limit_requests = lim_requests
        self.limit_tokens = lim_tokens
        self.reset_requests_sec = reset_requests_sec
        self.reset_tokens_sec = reset_tokens_sec

        now = _get_loop_time()

        if model:
            norm_model = model.strip().lower()
            self._model_headers[norm_model] = {
                "remaining_requests": rem_requests,
                "remaining_tokens": rem_tokens,
                "limit_requests": lim_requests,
                "limit_tokens": lim_tokens,
                "reset_requests_sec": reset_requests_sec,
                "reset_tokens_sec": reset_tokens_sec,
                "retry_after_sec": retry_after,
                "updated_at": now,
            }
            if lim_requests is not None and rem_requests is not None and lim_requests >= rem_requests:
                self._model_daily_requests[norm_model] = lim_requests - rem_requests
            if reset_requests_sec is not None and reset_requests_sec > 0:
                self._model_daily_reset_at[norm_model] = now + reset_requests_sec

        # 2. HTTP 429: Rate Limit Reached
        if response.status_code == 429:
            lock_delay: Optional[float] = None
            reason: str = ""
            is_daily = False

            # Check error body and fallback classifier first
            is_daily_cls, fallback_delay, fallback_reason = classify_rate_limit_429(response, "groq")

            err_body = ""
            try:
                err_body = response.text.lower()
            except Exception:
                pass

            is_tpd_exhaustion = (
                is_daily_cls
                or "tokens per day" in err_body
                or "tpd" in err_body
                or "tokens/day" in err_body
            )

            # Priority 1: retry-after header returned by Groq on 429
            if retry_after is not None and retry_after > 0:
                lock_delay = retry_after
                if rem_requests == 0 and (rem_tokens is None or rem_tokens > 0):
                    is_daily = True
                    reason = f"daily requests exhausted (retry-after {retry_after:g}s)"
                elif is_tpd_exhaustion:
                    is_daily = True
                    reason = f"daily tokens exhausted (TPD 200k, retry-after {retry_after:g}s)"
                elif retry_after > 90.0:
                    is_daily = True
                    reason = f"daily quota exhausted (rolling window, retry-after {retry_after:g}s)"
                else:
                    is_daily = False
                    reason = f"rate limit (retry-after {retry_after:g}s)"

            # Priority 2: TPD exhaustion detected without explicit retry_after header
            elif is_tpd_exhaustion:
                lock_delay = fallback_delay if (fallback_delay and fallback_delay > 0) else 60.0
                is_daily = True
                reason = fallback_reason or "daily tokens exhausted (TPD 200k)"

            # Priority 3: tokens exhausted (TPM reset) - require >= 1.0s to avoid sub-millisecond storms
            elif (rem_tokens == 0 or (rem_tokens is not None and rem_tokens < 1000)) and reset_tokens_sec is not None and reset_tokens_sec >= 1.0:
                lock_delay = reset_tokens_sec
                is_daily = False
                reason = f"tokens exhausted (reset in {reset_tokens_sec:g}s)"

            # Priority 4: requests exhausted (RPD reset - Groq requests are always RPD)
            elif rem_requests == 0 and reset_requests_sec is not None and reset_requests_sec > 0:
                lock_delay = reset_requests_sec
                is_daily = True
                reason = f"daily requests exhausted (reset in {reset_requests_sec:g}s)"

            # Priority 5: delay parsed from body (e.g. "Please try again in 4.2s")
            elif fallback_delay is not None and fallback_delay > 0:
                lock_delay = fallback_delay
                is_daily = is_daily_cls or (fallback_delay > 90.0)
                reason = fallback_reason or f"rate limit (body parsed {fallback_delay:g}s)"

            # Priority 6: any reset header present with at least 1.0s
            elif reset_tokens_sec is not None and reset_tokens_sec >= 1.0:
                lock_delay = reset_tokens_sec
                is_daily = False
                reason = f"rate limit (reset-tokens {reset_tokens_sec:g}s)"
            elif reset_requests_sec is not None and reset_requests_sec > 0:
                lock_delay = reset_requests_sec
                is_daily = True
                reason = f"rate limit (reset-requests {reset_requests_sec:g}s)"

            # Priority 7: Fallback floor
            else:
                is_daily = is_daily_cls
                lock_delay = fallback_delay if (fallback_delay and fallback_delay > 0) else 5.0

            # When retry_after is explicitly provided by the provider header, respect it directly.
            # Otherwise enforce a minimum floor of 3.0s (60s for daily) to avoid 0.001s retry storms.
            if retry_after is not None and retry_after > 0:
                effective_lock = retry_after
            elif is_daily:
                effective_lock = max(lock_delay or 0.0, 60.0)
            else:
                effective_lock = max(lock_delay or 0.0, 3.05)

            self.mark_rate_limited(
                is_daily=is_daily,
                retry_delay_sec=effective_lock,
                reason=reason,
                model=model,
            )
            return

        # 3. HTTP 200 (Success):
        if response.status_code == 200:
            # Proactive TPM rotation on HTTP 200:
            # When rem_tokens is not None and rem_tokens < 1200 and reset_tokens_sec > 0,
            # proactively set self._model_cooldowns[norm_m] = max(existing, now + min(reset_tokens_sec, 15.0))
            if (rem_tokens == 0 or (rem_tokens is not None and rem_tokens < 1200)) and reset_tokens_sec is not None and reset_tokens_sec > 0:
                proactive_cd = min(reset_tokens_sec, 15.0)
                dep_reason = (
                    f"tokens depleted (reset in {reset_tokens_sec:g}s)"
                    if rem_tokens == 0
                    else f"tokens near depletion ({rem_tokens} remaining; reset in {reset_tokens_sec:g}s)"
                )
                if model:
                    norm_m = model.strip().lower()
                    existing_cd = self._model_cooldowns.get(norm_m, 0.0)
                    self._model_cooldowns[norm_m] = max(existing_cd, now + proactive_cd)
                    self._model_cooldown_reasons[norm_m] = dep_reason
                    logger.info(
                        f"[GroqKey:{self.masked_key}] 200 OK with remaining tokens={rem_tokens} for model '{model}'. "
                        f"Proactive model cooldown until reset in {proactive_cd:g}s. Key remains ACTIVE."
                    )
                else:
                    self.status = KeyStatus.MINUTE_COOLDOWN
                    self.cooldown_until = max(self.cooldown_until, now + proactive_cd)
                    self.cooldown_reason = dep_reason
                    logger.info(
                        f"[GroqKey:{self.masked_key}] 200 OK with remaining tokens={rem_tokens}. "
                        f"Proactive cooldown until reset in {proactive_cd:g}s."
                    )
                self.success_requests += 1
            # If Groq indicated that remaining daily requests reached 0 on this request:
            elif rem_requests == 0 and reset_requests_sec is not None and reset_requests_sec > 0:
                if model:
                    norm_m = model.strip().lower()
                    existing_cd = self._model_cooldowns.get(norm_m, 0.0)
                    self._model_cooldowns[norm_m] = max(existing_cd, now + reset_requests_sec)
                    self._model_cooldown_reasons[norm_m] = f"requests depleted (reset in {reset_requests_sec:g}s)"
                    logger.info(
                        f"[GroqKey:{self.masked_key}] 200 OK with requests=0 for model '{model}'. "
                        f"Model cooldown until reset in {reset_requests_sec:g}s. Key remains ACTIVE."
                    )
                else:
                    self.status = KeyStatus.DAILY_EXHAUSTED
                    self.cooldown_until = max(self.cooldown_until, now + reset_requests_sec)
                    self.cooldown_reason = f"requests depleted (reset in {reset_requests_sec:g}s)"
                    logger.info(
                        f"[GroqKey:{self.masked_key}] 200 OK with requests=0. "
                        f"Daily exhausted cooldown until reset in {reset_requests_sec:g}s."
                    )
                self.success_requests += 1
            else:
                self.mark_success(model=model)

            # Record token usage if usage field is present in response
            if model:
                try:
                    data = response.json()
                    if isinstance(data, dict):
                        usage = data.get("usage", {})
                        tok = usage.get("total_tokens") or (
                            usage.get("prompt_tokens", 0) + usage.get("completion_tokens", 0)
                        )
                        if tok and tok > 0:
                            self.record_token_usage(model, tok)
                except Exception:
                    pass

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
        Mark rate limit following HTTP 429 using the exact duration returned by Groq.
        If model is provided, locks strictly that model on this key while keeping the key
        active and available for all other models.
        Retains longer cooldowns: self._model_cooldowns[norm_model] = max(existing, now + delay).
        """
        now = _get_loop_time()

        # Lock for the exact delay returned by the provider (never arbitrary numbers)
        delay = float(retry_delay_sec) if (retry_delay_sec is not None and retry_delay_sec > 0) else (60.0 if is_daily else 1.0)

        if model:
            norm_model = model.strip().lower()
            existing = self._model_cooldowns.get(norm_model, 0.0)
            target = now + delay
            if target >= existing:
                self._model_cooldowns[norm_model] = target
                self._model_cooldown_reasons[norm_model] = reason or f"Rate limit on {model}"
            else:
                self._model_cooldowns[norm_model] = existing

            self._model_rate_limits[norm_model] = self._model_rate_limits.get(norm_model, 0) + 1
            if is_daily:
                self.daily_quota_exhaustions += 1
            else:
                self.minute_rate_limits += 1
            logger.warning(
                f"[GroqKey:{self.masked_key}] Model '{model}' rate limited ({reason}). "
                f"Cooldown {delay:.2f}s applied strictly to model '{model}'. Key remains ACTIVE for other models."
            )
            # Ensure key itself is NOT locked
            if self.status != KeyStatus.UNAUTHORIZED:
                self.status = KeyStatus.ACTIVE
            return

        existing_key = self.cooldown_until
        target_key = now + delay
        if is_daily:
            self.status = KeyStatus.DAILY_EXHAUSTED
            self.daily_quota_exhaustions += 1
            if target_key >= existing_key:
                self.cooldown_until = target_key
                self.cooldown_reason = reason or "Daily quota exhausted"
            else:
                self.cooldown_until = existing_key
            logger.warning(
                f"[GroqKey:{self.masked_key}] Daily quota exhausted ({reason}). "
                f"Cooldown applied for {delay:.2f}s as returned by provider."
            )
        else:
            self.minute_rate_limits += 1
            if target_key >= existing_key:
                self.status = KeyStatus.MINUTE_COOLDOWN
                self.cooldown_until = target_key
                self.cooldown_reason = reason or "Rate limit reached"
            else:
                self.cooldown_until = existing_key
            logger.warning(
                f"[GroqKey:{self.masked_key}] Rate limit reached ({reason}). "
                f"Cooldown applied for {delay:.2f}s as returned by provider."
            )

    def mark_unauthorized(self) -> None:
        """Mark key permanently invalid following HTTP 401/403."""
        self.status = KeyStatus.UNAUTHORIZED
        self.auth_errors += 1
        logger.error(
            f"[GroqKey:{self.masked_key}] Permanently deactivated due to HTTP 401/403 Unauthorized."
        )

    def to_dict(self) -> Dict[str, Any]:
        """Telemetry representation for monitoring."""
        now = _get_loop_time()
        cooldown_remaining = max(0.0, self.cooldown_until - now) if self.cooldown_until > 0 else 0.0

        # Calculate active per-model cooldowns
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
            "total_requests": self.total_requests,
            "success_requests": self.success_requests,
            "minute_rate_limits": self.minute_rate_limits,
            "daily_quota_exhaustions": self.daily_quota_exhaustions,
            "auth_errors": self.auth_errors,
            "last_used_at": self.last_used_at,
            "remaining_requests": self.remaining_requests,
            "remaining_tokens": self.remaining_tokens,
            "limit_requests": self.limit_requests,
            "limit_tokens": self.limit_tokens,
            "reset_requests_sec": self.reset_requests_sec,
            "reset_tokens_sec": self.reset_tokens_sec,
            "retry_after_sec": self.retry_after_sec,
            "model_headers": {m: dict(h) for m, h in self._model_headers.items()},
            "model_daily_tokens": dict(self._model_daily_tokens),
            "model_daily_requests": dict(self._model_daily_requests),
        }


class GroqKeyPool:
    """
    Thread-safe, round-robin load-balancing pool for multiple Groq API keys.
    """

    def __init__(self, keys: Optional[List[str]] = None):
        self._keys: List[GroqKey] = []
        self._round_robin_idx: int = 0
        self._pool_lock = asyncio.Lock()
        if keys:
            self.set_keys(keys)

    @property
    def total_keys(self) -> int:
        return len(self._keys)

    @property
    def keys(self) -> List[str]:
        """Return the list of raw API keys in this pool."""
        return [k.key for k in self._keys]

    def set_keys(self, raw_keys: Any) -> None:
        """Set or update the active list of keys, preserving metrics for existing keys."""
        parsed = parse_key_list(raw_keys)
        if not parsed:
            self._keys = []
            return

        existing_map = {k.key: k for k in self._keys}
        new_list: List[GroqKey] = []
        for raw in parsed:
            if raw in existing_map:
                new_list.append(existing_map[raw])
            else:
                new_list.append(GroqKey(key=raw))

        self._keys = new_list
        logger.info(
            f"[GroqKeyPool] Initialized with {len(self._keys)} key(s): "
            f"{', '.join(k.masked_key for k in self._keys)}"
        )

    def add_key(self, key: str) -> None:
        """Add a single key to the pool if not already present."""
        clean = key.strip()
        if not clean or any(k.key == clean for k in self._keys):
            return
        self._keys.append(GroqKey(key=clean))

    def get_keys(self) -> List[GroqKey]:
        return list(self._keys)

    def has_available_key_for_model(self, model: str) -> bool:
        """Check if at least one key in the pool is currently available for the specified model."""
        now = _get_loop_time()
        return any(k.is_available(now, model=model) for k in self._keys)

    def broadcast_model_cooldown(
        self,
        model: str,
        cooldown_sec: float,
        reason: str,
        is_daily: bool = False,
    ) -> None:
        """
        Groq rate limits (RPM, TPM, RPD) apply at the Organization level across all API keys.
        When a model hits a rate limit on one key in the organization, broadcast the model
        cooldown to all sibling keys to prevent redundant 429 hammering.
        """
        if not model or not self._keys:
            return
        effective_delay = max(cooldown_sec, 60.0 if is_daily else 3.0)
        for k in self._keys:
            k.mark_rate_limited(
                is_daily=is_daily,
                retry_delay_sec=effective_delay,
                reason=f"[org-wide] {reason}",
                model=model,
            )

    async def acquire_next_key(self, model: Optional[str] = None) -> Optional[GroqKey]:
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

            # 1. Round-robin scan for an immediately available key (respecting model if given)
            for offset in range(n):
                idx = (self._round_robin_idx + offset) % n
                candidate = self._keys[idx]
                if candidate.is_available(now, model=model):
                    self._round_robin_idx = (idx + 1) % n
                    return candidate

            # 2. If no key is immediately active for this model, inspect if any key is closest to ready (within 3s)
            best_key, best_wait = self.get_best_wait_time(model=model, now=now)

            # If the closest key will be ready within 3.0 seconds (short jitter / window rollover), return it
            if best_key and best_wait <= 3.0:
                return best_key

            return None

    def get_best_wait_time(
        self,
        model: Optional[str] = None,
        now: Optional[float] = None,
    ) -> Tuple[Optional[GroqKey], float]:
        """Find the key closest to being ready for a given model and return (key, seconds_until_ready)."""
        current_time = now if now is not None else _get_loop_time()
        best_key: Optional[GroqKey] = None
        best_wait = float("inf")
        for candidate in self._keys:
            if candidate.status == KeyStatus.UNAUTHORIZED:
                continue
            wait = candidate.get_seconds_until_ready(current_time, model=model)
            if wait < best_wait:
                best_wait = wait
                best_key = candidate
        return best_key, best_wait

    def get_telemetry(self) -> Dict[str, Any]:
        """Return comprehensive live status for all keys in the pool."""
        now = _get_loop_time()

        key_stats = [k.to_dict() for k in self._keys]
        active_count = sum(1 for k in self._keys if k.is_available(now))
        # Global key cooldown is only when status is MINUTE_COOLDOWN/DAILY_EXHAUSTED and cooldown_until > now
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
            "keys": key_stats,
        }


# Re-export Mistral pool components for unified imports
from backend.app.pipeline.providers.mistral_key_pool import (
    MISTRAL_MODEL_LIMITS,
    MistralKey,
    MistralKeyPool,
    get_mistral_model_limits,
)
