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
    State, pacing sliding-window, and error telemetry for a single Groq API key.
    """

    def __init__(
        self,
        key: str,
        min_interval_sec: float = 2.1,
        max_rpm: int = 28,
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

        # Sliding window check
        wait_time = 0.0
        if self._request_timestamps and len(self._request_timestamps) >= self.max_rpm:
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
        Enforce sliding-window pacing and minimum spacing under the key's private lock.
        Requests on this key do not block requests on other keys in the pool.
        """
        async with self._pacing_lock:
            loop = asyncio.get_event_loop()
            now = loop.time()

            # 1. Honor active cooldown if any
            if now < self.cooldown_until:
                wait_rl = self.cooldown_until - now
                logger.info(
                    f"[GroqKey:{self.masked_key}] Waiting {wait_rl:.1f}s for cooldown reset..."
                )
                await asyncio.sleep(wait_rl)
                now = loop.time()

            # 2. Enforce 60-second sliding-window RPM cap
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

            # 3. Enforce minimum spacing between consecutive requests on this key
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

    def mark_success(self) -> None:
        """Record successful call on this key."""
        self.success_requests += 1
        if self.status != KeyStatus.ACTIVE:
            self.status = KeyStatus.ACTIVE
            self.cooldown_until = 0.0

    def mark_rate_limited(
        self,
        is_daily: bool,
        retry_delay_sec: Optional[float] = None,
        reason: str = "",
    ) -> None:
        """Mark key in cooldown following HTTP 429."""
        now = _get_loop_time()
        self.cooldown_reason = reason or "HTTP 429 Rate Limit"

        if is_daily:
            self.status = KeyStatus.DAILY_EXHAUSTED
            self.daily_quota_exhaustions += 1
            # Cooldown until tomorrow 00:00 UTC (or at least 4 hours)
            delay = retry_delay_sec if (retry_delay_sec and retry_delay_sec > 300) else 14400.0
            self.cooldown_until = now + delay
            logger.warning(
                f"[GroqKey:{self.masked_key}] Daily quota exhausted ({reason}). "
                f"Cooldown applied for {delay:.0f}s."
            )
        else:
            self.status = KeyStatus.MINUTE_COOLDOWN
            self.minute_rate_limits += 1
            delay = min(self.max_minute_wait_sec, (retry_delay_sec or 25.0) + random.uniform(0.5, 1.5))
            self.cooldown_until = now + delay
            logger.warning(
                f"[GroqKey:{self.masked_key}] Minute rate limit reached ({reason}). "
                f"Cooldown applied for {delay:.1f}s."
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

            # If the closest key will be ready within 35 seconds, return it
            if best_key and best_wait <= 35.0:
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
