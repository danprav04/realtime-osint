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

        # Live rate limit headers returned by Groq
        self.remaining_requests: Optional[int] = None
        self.remaining_tokens: Optional[int] = None
        self.limit_requests: Optional[int] = None
        self.limit_tokens: Optional[int] = None
        self.reset_requests_sec: Optional[float] = None
        self.reset_tokens_sec: Optional[float] = None
        self.retry_after_sec: Optional[float] = None

    def is_available(self, now: Optional[float] = None) -> bool:
        """Check if key is available to accept requests."""
        if self.status == KeyStatus.UNAUTHORIZED:
            return False

        current_time = now if now is not None else _get_loop_time()
        if self.status in (KeyStatus.MINUTE_COOLDOWN, KeyStatus.DAILY_EXHAUSTED):
            if current_time >= self.cooldown_until:
                # Cooldown expired, restore to active
                logger.info(
                    f"[GroqKey:{self.masked_key}] Cooldown expired. Restoring to ACTIVE status."
                )
                self.status = KeyStatus.ACTIVE
                self.cooldown_until = 0.0
                self.cooldown_reason = ""
                return True
            return False

        return self.status == KeyStatus.ACTIVE

    def get_seconds_until_ready(self, now: Optional[float] = None) -> float:
        """Estimate how many seconds until this key can issue its next request."""
        current_time = now if now is not None else _get_loop_time()
        if self.status == KeyStatus.UNAUTHORIZED:
            return float("inf")

        if self.status in (KeyStatus.MINUTE_COOLDOWN, KeyStatus.DAILY_EXHAUSTED):
            if current_time < self.cooldown_until:
                return max(0.0, self.cooldown_until - current_time)

        # Sliding window check (only if max_rpm explicitly set > 0)
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

    async def acquire_pacing_slot(self) -> None:
        """
        Enforce pacing and cooldown under the key's private lock.
        Requests on this key do not block requests on other keys in the pool.
        """
        async with self._pacing_lock:
            loop = asyncio.get_event_loop()
            now = loop.time()

            # 1. Honor active cooldown if any (set strictly by what Groq returned)
            if now < self.cooldown_until:
                wait_rl = self.cooldown_until - now
                logger.info(
                    f"[GroqKey:{self.masked_key}] Waiting {wait_rl:.1f}s for cooldown reset..."
                )
                await asyncio.sleep(wait_rl)
                now = loop.time()

            # 2. Enforce 60-second sliding-window RPM cap (if configured)
            if self.max_rpm > 0:
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

            # 3. Enforce minimum spacing between consecutive requests on this key (if configured)
            if self.min_interval_sec > 0 and self._last_request_time > 0:
                elapsed = now - self._last_request_time
                if elapsed < self.min_interval_sec:
                    await asyncio.sleep(self.min_interval_sec - elapsed)
                    now = loop.time()

            self._last_request_time = now
            if self.max_rpm > 0:
                self._request_timestamps.append(now)

            self.total_requests += 1
            self.last_used_at = datetime.now(timezone.utc).isoformat()

    def update_from_response(self, response: httpx.Response) -> None:
        """
        Dynamically update key status and rate-limit state using the exact HTTP headers
        returned by Groq (retry-after and x-ratelimit-*), eliminating arbitrary numbers.
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

        # 2. HTTP 429: Rate Limit Reached
        if response.status_code == 429:
            lock_delay: Optional[float] = None
            reason: str = ""
            is_daily = False

            # Priority 1: retry-after header returned by Groq on 429
            if retry_after is not None and retry_after > 0:
                lock_delay = retry_after
                if rem_requests == 0 and (rem_tokens is None or rem_tokens > 0):
                    is_daily = lock_delay > 3600.0
                    reason = f"daily requests exhausted (retry-after {retry_after:g}s)"
                else:
                    is_daily = False
                    reason = f"rate limit (retry-after {retry_after:g}s)"

            # Priority 2: tokens exhausted (TPM reset)
            elif rem_tokens == 0 and reset_tokens_sec is not None and reset_tokens_sec > 0:
                lock_delay = reset_tokens_sec
                is_daily = False
                reason = f"tokens exhausted (reset in {reset_tokens_sec:g}s)"

            # Priority 3: requests exhausted (RPD reset)
            elif rem_requests == 0 and reset_requests_sec is not None and reset_requests_sec > 0:
                lock_delay = reset_requests_sec
                is_daily = reset_requests_sec > 3600.0
                reason = f"requests exhausted (reset in {reset_requests_sec:g}s)"

            # Priority 4: any reset header present
            elif reset_tokens_sec is not None and reset_tokens_sec > 0:
                lock_delay = reset_tokens_sec
                is_daily = False
                reason = f"rate limit (reset-tokens {reset_tokens_sec:g}s)"
            elif reset_requests_sec is not None and reset_requests_sec > 0:
                lock_delay = reset_requests_sec
                is_daily = reset_requests_sec > 3600.0
                reason = f"rate limit (reset-requests {reset_requests_sec:g}s)"

            # Priority 5: Fallback to error message body parser
            else:
                is_daily_cls, fallback_delay, fallback_reason = classify_rate_limit_429(response, "groq")
                is_daily = is_daily_cls
                lock_delay = fallback_delay if (fallback_delay and fallback_delay > 0) else 1.0
                reason = fallback_reason or "HTTP 429 Rate Limit"

            self.mark_rate_limited(
                is_daily=is_daily,
                retry_delay_sec=lock_delay,
                reason=reason,
            )
            return

        # 3. HTTP 200 (Success):
        if response.status_code == 200:
            self.mark_success()
            # If Groq indicated that remaining tokens reached 0 on this request:
            if rem_tokens == 0 and reset_tokens_sec is not None and reset_tokens_sec > 0:
                self.status = KeyStatus.MINUTE_COOLDOWN
                self.cooldown_until = now + reset_tokens_sec
                self.cooldown_reason = f"tokens depleted (reset in {reset_tokens_sec:g}s)"
                logger.info(
                    f"[GroqKey:{self.masked_key}] 200 OK with tokens=0. "
                    f"Cooldown until reset in {reset_tokens_sec:g}s."
                )
            # If Groq indicated that remaining daily requests reached 0 on this request:
            elif rem_requests == 0 and reset_requests_sec is not None and reset_requests_sec > 0:
                self.status = KeyStatus.DAILY_EXHAUSTED if reset_requests_sec > 3600.0 else KeyStatus.MINUTE_COOLDOWN
                self.cooldown_until = now + reset_requests_sec
                self.cooldown_reason = f"requests depleted (reset in {reset_requests_sec:g}s)"
                logger.info(
                    f"[GroqKey:{self.masked_key}] 200 OK with requests=0. "
                    f"Cooldown until reset in {reset_requests_sec:g}s."
                )
            else:
                # Key is active and has available capacity
                self.status = KeyStatus.ACTIVE
                self.cooldown_until = 0.0
                self.cooldown_reason = ""

    def mark_success(self) -> None:
        """Record successful call on this key."""
        self.success_requests += 1
        if self.status != KeyStatus.ACTIVE:
            self.status = KeyStatus.ACTIVE
            self.cooldown_until = 0.0
            self.cooldown_reason = ""

    def mark_rate_limited(
        self,
        is_daily: bool,
        retry_delay_sec: Optional[float] = None,
        reason: str = "",
    ) -> None:
        """
        Mark key in cooldown following HTTP 429 using the exact duration returned by Groq.
        Locks key strictly using the returned delay, avoiding arbitrary multi-hour numbers.
        """
        now = _get_loop_time()
        self.cooldown_reason = reason or "HTTP 429 Rate Limit"

        # Lock for the exact delay returned by the provider (never arbitrary numbers)
        delay = float(retry_delay_sec) if (retry_delay_sec is not None and retry_delay_sec > 0) else (60.0 if is_daily else 1.0)

        if is_daily:
            self.status = KeyStatus.DAILY_EXHAUSTED
            self.daily_quota_exhaustions += 1
            self.cooldown_until = now + delay
            logger.warning(
                f"[GroqKey:{self.masked_key}] Daily quota exhausted ({reason}). "
                f"Cooldown applied for {delay:.2f}s as returned by provider."
            )
        else:
            self.status = KeyStatus.MINUTE_COOLDOWN
            self.minute_rate_limits += 1
            self.cooldown_until = now + delay
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

        return {
            "masked_key": self.masked_key,
            "status": self.status.value,
            "is_available": self.is_available(now),
            "cooldown_remaining_sec": round(cooldown_remaining, 1),
            "cooldown_reason": self.cooldown_reason,
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

    async def acquire_next_key(self) -> Optional[GroqKey]:
        """
        Select next available key using round-robin distribution with cooldown avoidance.
        Returns None if all keys are unavailable.
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
                if candidate.is_available(now):
                    self._round_robin_idx = (idx + 1) % n
                    return candidate

            # 2. If no key is immediately active, inspect if any minute-cooldown key is closest to ready
            best_key: Optional[GroqKey] = None
            best_wait = float("inf")
            for candidate in self._keys:
                if candidate.status == KeyStatus.UNAUTHORIZED:
                    continue
                wait = candidate.get_seconds_until_ready(now)
                if wait < best_wait:
                    best_wait = wait
                    best_key = candidate

            # If the closest key will be ready within 60 seconds (e.g. 1-minute window reset), return it
            if best_key and best_wait <= 60.0:
                return best_key

            return None

    def get_telemetry(self) -> Dict[str, Any]:
        """Return comprehensive live status for all keys in the pool."""
        now = _get_loop_time()

        key_stats = [k.to_dict() for k in self._keys]
        active_count = sum(1 for k in self._keys if k.is_available(now))
        total_requests = sum(k.total_requests for k in self._keys)
        total_success = sum(k.success_requests for k in self._keys)

        return {
            "total_keys": len(self._keys),
            "active_keys": active_count,
            "keys_in_cooldown": len(self._keys) - active_count,
            "total_requests": total_requests,
            "total_success": total_success,
            "keys": key_stats,
        }
