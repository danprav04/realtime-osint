"""
backend/app/pipeline/providers/base.py - Base AI Provider Interface & Resilient Retry Engine
"""

import abc
import asyncio
from collections import deque
import logging
import random
import re
from typing import Any, Callable, Coroutine, Deque, Dict, Optional, Tuple
import httpx

logger = logging.getLogger("osint.ai.provider")


def parse_duration_seconds(raw_val: Optional[str]) -> Optional[float]:
    """
    Parse duration headers/fields such as '22', '24s', '18.5s', '1m12s', '2h5m', '450ms'
    into float seconds.
    """
    if raw_val is None:
        return None
    s = str(raw_val).strip().lower()
    if not s:
        return None
    try:
        val = float(s)
        return val if val >= 0 else None
    except ValueError:
        pass

    total = 0.0
    matched = False
    for amount, unit in re.findall(
        r"(\d+(?:\.\d+)?)\s*(ms|s|sec|secs|second|seconds|m|min|mins|minute|minutes|h|hr|hrs|hour|hours)",
        s,
    ):
        val = float(amount)
        matched = True
        if unit == "ms":
            total += val / 1000.0
        elif unit in ("s", "sec", "secs", "second", "seconds"):
            total += val
        elif unit in ("m", "min", "mins", "minute", "minutes"):
            total += val * 60.0
        elif unit in ("h", "hr", "hrs", "hour", "hours"):
            total += val * 3600.0
    return total if matched else None


def classify_rate_limit_429(
    response: httpx.Response, provider_name: str = ""
) -> Tuple[bool, Optional[float], str]:
    """
    Inspect an HTTP 429 response from Gemini or Groq to distinguish between:
      - Daily Quota Exhaustion (RPD / TPD): returns (True, retry_delay, reason) -> fail fast to fallback model.
      - Minute Rate Limit (RPM / TPM): returns (False, retry_delay, reason) -> wait for minute window reset & retry.
    """
    headers = response.headers
    retry_after_hdr = parse_duration_seconds(headers.get("retry-after"))
    rem_requests = headers.get("x-ratelimit-remaining-requests")
    rem_tokens = headers.get("x-ratelimit-remaining-tokens")
    reset_requests_sec = parse_duration_seconds(headers.get("x-ratelimit-reset-requests"))
    reset_tokens_sec = parse_duration_seconds(headers.get("x-ratelimit-reset-tokens"))

    body_text = ""
    try:
        body_text = response.text or ""
    except Exception:
        body_text = ""
    body_lower = body_text.lower()

    quota_ids: list[str] = []
    retry_info_sec: Optional[float] = None
    error_message = ""

    try:
        data = response.json()
        if isinstance(data, dict):
            err_obj = data.get("error", {})
            if isinstance(err_obj, dict):
                error_message = str(err_obj.get("message", ""))
                details = err_obj.get("details", [])
                if isinstance(details, list):
                    for d in details:
                        if not isinstance(d, dict):
                            continue
                        if "retryDelay" in d:
                            retry_info_sec = parse_duration_seconds(str(d.get("retryDelay")))
                        for v in d.get("violations", []) or []:
                            if isinstance(v, dict):
                                q_id = str(v.get("quotaId", ""))
                                q_metric = str(v.get("quotaMetric", ""))
                                if q_id:
                                    quota_ids.append(q_id)
                                if q_metric:
                                    quota_ids.append(q_metric)
    except Exception:
        pass

    quota_joined = " ".join(quota_ids).lower()
    combined_text = f"{quota_joined} {error_message.lower()} {body_lower}"

    # Extract retry delay mentioned in error message (e.g. "Please retry in 24.5s" or "try again in 12.3s")
    msg_retry_sec: Optional[float] = None
    m_retry = re.search(
        r"(?:retry|try again)\s+in\s+([0-9.]+(?:\s*(?:ms|s|sec|secs|m|min|mins|h|hr|hrs))+)",
        error_message or body_text,
        re.IGNORECASE,
    )
    if m_retry:
        msg_retry_sec = parse_duration_seconds(m_retry.group(1))

    # Check explicit minute vs daily indicators
    has_minute_indicator = any(
        tok in combined_text
        for tok in (
            "perminute",
            "per_minute",
            "requests per minute",
            "tokens per minute",
            "(rpm)",
            "(tpm)",
            "inputtokenspermodelperminute",
            "generaterequestsperminute",
        )
    ) or (rem_tokens is not None and str(rem_tokens).strip() == "0")

    has_daily_indicator = any(
        tok in combined_text
        for tok in (
            "perday",
            "per_day",
            "requests per day",
            "tokens per day",
            "(rpd)",
            "(tpd)",
            "daily quota",
            "daily limit",
            "generaterequestsperday",
            "requestsperday",
            "tokensperday",
            "free model quota",
            "reached the limit of the free model",
            "switch to a paid model",
            "unlimited concurrency",
        )
    ) or (
        rem_requests is not None
        and str(rem_requests).strip() == "0"
        and not has_minute_indicator
    )

    # Provider-specific header classification for Groq: lock strictly by returned headers
    if provider_name == "groq":
        if retry_after_hdr is not None and retry_after_hdr > 0:
            is_daily = bool(rem_requests is not None and str(rem_requests).strip() == "0" and reset_requests_sec is not None and reset_requests_sec > 3600.0)
            return (
                is_daily,
                retry_after_hdr,
                f"{'daily' if is_daily else 'minute'} rate limit (retry-after {retry_after_hdr:g}s)",
            )
        if rem_tokens is not None and str(rem_tokens).strip() == "0" and reset_tokens_sec is not None and reset_tokens_sec > 0:
            return (
                False,
                reset_tokens_sec,
                f"minute rate limit (tokens exhausted, wait {reset_tokens_sec:g}s)",
            )
        if rem_requests is not None and str(rem_requests).strip() == "0" and reset_requests_sec is not None and reset_requests_sec > 0:
            is_daily = reset_requests_sec > 3600.0
            return (
                is_daily,
                reset_requests_sec,
                f"{'daily' if is_daily else 'minute'} rate limit (requests exhausted, wait {reset_requests_sec:g}s)",
            )
        if reset_tokens_sec is not None and reset_tokens_sec > 0:
            return (
                False,
                reset_tokens_sec,
                f"minute rate limit (reset-tokens {reset_tokens_sec:g}s)",
            )
        if reset_requests_sec is not None and reset_requests_sec > 0:
            is_daily = reset_requests_sec > 3600.0
            return (
                is_daily,
                reset_requests_sec,
                f"{'daily' if is_daily else 'minute'} rate limit (reset-requests {reset_requests_sec:g}s)",
            )

    # Determine best wait duration
    retry_delay: Optional[float] = None
    for candidate in (retry_info_sec, msg_retry_sec, retry_after_hdr):
        if candidate is not None and candidate > 0:
            retry_delay = candidate
            break

    if retry_delay is None:
        if has_minute_indicator and reset_tokens_sec is not None:
            retry_delay = reset_tokens_sec
        elif reset_tokens_sec is not None and (reset_requests_sec is None or reset_tokens_sec <= 90.0):
            retry_delay = reset_tokens_sec
        elif reset_requests_sec is not None:
            retry_delay = reset_requests_sec

    # Classify whether this is a Daily Quota lockout vs a Minute Rate Limit
    if has_daily_indicator and not has_minute_indicator:
        is_daily = True
        reason = f"daily quota exhausted ({', '.join(quota_ids) or error_message[:80] or 'RPD/TPD'})"
    elif retry_delay is not None and retry_delay > 95.0 and not has_minute_indicator:
        is_daily = True
        reason = f"long quota lockout ({retry_delay:.0f}s)"
    else:
        is_daily = False
        reason = (
            f"minute rate limit ({', '.join(quota_ids) or 'RPM/TPM'}"
            f"{f', wait {retry_delay:.1f}s' if retry_delay else ''})"
        )

    return is_daily, retry_delay, reason


class BaseAIProvider(abc.ABC):
    """
    Abstract Base Class for AI Providers (Google Gemini & Groq Cloud).
    Enforces unified async generation, connection pooling, per-minute sliding-window
    rate limiting, and intelligent RPM vs. RPD error handling.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        default_model: str = "",
        timeout_seconds: float = 25.0,
        max_retries: int = 3,
        base_backoff_sec: float = 1.0,
        jitter_sec: float = 0.5,
    ):
        self.api_key = api_key or ""
        self.default_model = default_model
        self.timeout = httpx.Timeout(connect=5.0, read=timeout_seconds, write=5.0, pool=5.0)
        self.max_retries = max_retries
        self.base_backoff = base_backoff_sec
        self.jitter = jitter_sec
        self.min_interval_sec: float = 1.0
        self.max_rpm: int = 0
        self.max_tpm: int = 0
        self.max_minute_wait_sec: float = 65.0
        self._pacing_lock = asyncio.Lock()
        self._last_request_time: float = 0.0
        self._rate_limit_until: float = 0.0
        self._request_timestamps: Deque[float] = deque()
        self._token_records: Deque[Tuple[float, int]] = deque()
        self._client: Optional[httpx.AsyncClient] = None
        self.call_history: list[Dict[str, Any]] = []

    async def get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=self.timeout,
                limits=httpx.Limits(max_connections=15, max_keepalive_connections=5),
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    @property
    @abc.abstractmethod
    def provider_name(self) -> str:
        """Name of provider: 'gemini' or 'groq'."""
        pass

    @abc.abstractmethod
    async def generate_text(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        model: Optional[str] = None,
        temperature: float = 0.2,
        max_tokens: int = 1024,
        json_mode: bool = False,
    ) -> str:
        """Asynchronously call provider API and return completion text."""
        pass

    @abc.abstractmethod
    async def process_stage(
        self,
        stage: str,
        input_text: str,
        context: Optional[Dict[str, Any]] = None,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Process structured stage execution returning parsed dictionary."""
        pass

    async def _acquire_pacing_slot(self, estimated_tokens: int = 0) -> None:
        """
        Enforce per-minute sliding-window RPM and TPM limits, active 429 minute-window pauses,
        and minimum inter-request spacing under a shared async lock.
        """
        if self.min_interval_sec <= 0 and self.max_rpm <= 0 and self.max_tpm <= 0 and self._rate_limit_until <= 0:
            return

        async with self._pacing_lock:
            loop = asyncio.get_event_loop()
            now = loop.time()

            # 1. Honor any active 429 minute-window reset deadline
            if now < self._rate_limit_until:
                wait_rl = self._rate_limit_until - now
                logger.info(
                    f"[{self.provider_name}:{self.default_model}] Waiting {wait_rl:.1f}s for active minute rate-limit window reset..."
                )
                await asyncio.sleep(wait_rl)
                now = loop.time()

            # 2. Enforce 60-second sliding-window RPM cap so we never exceed minute rate limits
            if self.max_rpm > 0:
                while self._request_timestamps and (now - self._request_timestamps[0]) >= 60.0:
                    self._request_timestamps.popleft()

                if len(self._request_timestamps) >= self.max_rpm:
                    window_wait = (self._request_timestamps[0] + 60.2) - now
                    if window_wait > 0:
                        logger.info(
                            f"[{self.provider_name}:{self.default_model}] Reached {self.max_rpm} RPM sliding-window cap; "
                            f"pacing {window_wait:.1f}s for minute window rollover..."
                        )
                        await asyncio.sleep(window_wait)
                        now = loop.time()
                    while self._request_timestamps and (now - self._request_timestamps[0]) >= 60.0:
                        self._request_timestamps.popleft()

            # 3. Enforce 60-second sliding-window TPM cap so we never exceed minute token limits (e.g. 16K TPM for Gemma)
            if self.max_tpm > 0 and estimated_tokens > 0:
                while self._token_records and (now - self._token_records[0][0]) >= 60.0:
                    self._token_records.popleft()

                current_tokens = sum(tok for _, tok in self._token_records)
                if (current_tokens + estimated_tokens) > self.max_tpm:
                    # Calculate wait time for enough tokens to roll off the 60s sliding window
                    tokens_needed = (current_tokens + estimated_tokens) - self.max_tpm
                    freed = 0
                    tpm_wait = 0.0
                    for ts, tok in self._token_records:
                        freed += tok
                        tpm_wait = max(tpm_wait, (ts + 60.2) - now)
                        if freed >= tokens_needed:
                            break
                    if tpm_wait > 0:
                        logger.warning(
                            f"[{self.provider_name}:{self.default_model}] Approaching {self.max_tpm} TPM limit "
                            f"({current_tokens} + {estimated_tokens} = {current_tokens + estimated_tokens} tokens / 60s); "
                            f"pacing {tpm_wait:.1f}s for token bucket rollover..."
                        )
                        await asyncio.sleep(tpm_wait)
                        now = loop.time()
                    while self._token_records and (now - self._token_records[0][0]) >= 60.0:
                        self._token_records.popleft()

            # 4. Enforce minimum spacing between consecutive requests
            if self.min_interval_sec > 0 and self._last_request_time > 0:
                elapsed = now - self._last_request_time
                if elapsed < self.min_interval_sec:
                    await asyncio.sleep(self.min_interval_sec - elapsed)
                    now = loop.time()

            self._last_request_time = now
            if self.max_rpm > 0:
                self._request_timestamps.append(now)
            if self.max_tpm > 0 and estimated_tokens > 0:
                self._token_records.append((now, estimated_tokens))

    async def _execute_with_retry(
        self,
        call_fn: Callable[[], Coroutine[Any, Any, httpx.Response]],
        estimated_tokens: int = 0,
    ) -> httpx.Response:
        """
        Executes HTTP requests with sliding-window RPM/TPM pacing, minute-limit recovery,
        and daily-quota failover classification.
        """
        if not self.api_key or not self.api_key.strip():
            raise PermissionError(
                f"{self.provider_name.capitalize()} API key is invalid or unauthorized (HTTP 401)"
            )

        last_error: Optional[Exception] = None
        for attempt in range(self.max_retries + 1):
            try:
                await self._acquire_pacing_slot(estimated_tokens=estimated_tokens)

                response = await call_fn()

                # Status 429: Distinguish Minute Rate Limit (RPM/TPM) vs Daily Quota (RPD/TPD)
                if response.status_code == 429:
                    is_daily_quota, retry_delay_sec, reason = classify_rate_limit_429(
                        response, self.provider_name
                    )

                    # If daily quota (RPD/TPD) is exhausted, fail fast immediately to trigger fallback model
                    if is_daily_quota:
                        logger.warning(
                            f"[{self.provider_name}:{self.default_model}] Daily quota exhausted ({reason}). "
                            f"Failing over immediately."
                        )
                        if self.provider_name.lower() == "gemini":
                            raise RuntimeError(
                                f"Gemini ResourceExhausted: 429 Quota Exceeded ({reason})"
                            )
                        elif self.provider_name.lower() == "aihubmix":
                            raise RuntimeError(
                                f"AIHubMix ResourceExhausted: 429 Quota Exceeded ({reason})"
                            )
                        else:
                            raise RuntimeError(
                                f"Groq RateLimitError: 429 Rate limit reached ({reason})"
                            )

                    # Otherwise, this is only a per-minute rate limit (RPM/TPM)
                    if attempt == self.max_retries:
                        if self.provider_name.lower() == "gemini":
                            raise RuntimeError(
                                f"Gemini ResourceExhausted: 429 Quota Exceeded ({reason})"
                            )
                        elif self.provider_name.lower() == "aihubmix":
                            raise RuntimeError(
                                f"AIHubMix ResourceExhausted: 429 Quota Exceeded ({reason})"
                            )
                        else:
                            raise RuntimeError(
                                f"Groq RateLimitError: 429 Rate limit reached ({reason})"
                            )

                    # If the rate limit requires a long wait (>30s), fail fast to let orchestrator switch models
                    if retry_delay_sec is not None and retry_delay_sec > 30.0:
                        logger.warning(
                            f"[{self.provider_name}:{self.default_model}] 429 requires long wait ({retry_delay_sec:.1f}s > 30s) - failing over immediately"
                        )
                        if self.provider_name.lower() == "gemini":
                            raise RuntimeError(
                                f"Gemini ResourceExhausted: 429 Minute rate limit requires long wait ({retry_delay_sec:.1f}s > 30s) ({reason})"
                            )
                        elif self.provider_name.lower() == "aihubmix":
                            raise RuntimeError(
                                f"AIHubMix ResourceExhausted: 429 Minute rate limit requires long wait ({retry_delay_sec:.1f}s > 30s) ({reason})"
                            )
                        else:
                            raise RuntimeError(
                                f"Groq RateLimitError: 429 Minute rate limit requires long wait ({retry_delay_sec:.1f}s > 30s) ({reason})"
                            )

                    now = asyncio.get_event_loop().time()
                    if retry_delay_sec is not None and retry_delay_sec > 0:
                        delay = min(
                            self.max_minute_wait_sec,
                            retry_delay_sec + random.uniform(0.4, 1.0),
                        )
                    elif self.max_rpm > 0 and self._request_timestamps:
                        oldest_wait = max(3.0, (self._request_timestamps[0] + 60.5) - now)
                        delay = min(
                            self.max_minute_wait_sec,
                            max(oldest_wait, self.base_backoff * (2 ** attempt)),
                        )
                    else:
                        delay = min(
                            self.max_minute_wait_sec,
                            self.base_backoff * (2 ** attempt) + random.uniform(0.1, self.jitter),
                        )

                    # Coordinate across concurrent workers so all callers wait for the minute window to clear
                    self._rate_limit_until = max(self._rate_limit_until, now + delay)

                    logger.warning(
                        f"[{self.provider_name}:{self.default_model}] 429 {reason}. "
                        f"Waiting {delay:.1f}s for minute window reset instead of switching models "
                        f"(attempt {attempt + 1}/{self.max_retries})"
                    )
                    await asyncio.sleep(delay)
                    continue

                # Status 503: Service Unavailable
                if response.status_code == 503:
                    if attempt == self.max_retries:
                        raise RuntimeError(f"{self.provider_name.capitalize()} Service Unavailable (HTTP 503)")
                    delay = self.base_backoff * (2 ** attempt) + random.uniform(0.1, self.jitter)
                    await asyncio.sleep(delay)
                    continue

                # Status 500: Internal Server Error (Gemma frequently throws 500 when TPM quota is saturated)
                if response.status_code == 500:
                    if attempt == self.max_retries:
                        response.raise_for_status()
                    delay = max(3.5, self.base_backoff * (2 ** attempt)) + random.uniform(0.5, 1.5)
                    logger.warning(
                        f"[{self.provider_name}:{self.default_model}] HTTP 500 Internal Error (TPM saturation/transient cluster error). "
                        f"Waiting {delay:.1f}s before retry (attempt {attempt + 1}/{self.max_retries})..."
                    )
                    now_loop = asyncio.get_event_loop().time()
                    self._rate_limit_until = max(self._rate_limit_until, now_loop + delay)
                    await asyncio.sleep(delay)
                    continue

                # Status 401 / 403: Invalid / Unauthorized Key (Non-retryable)
                if response.status_code in (401, 403):
                    raise PermissionError(
                        f"{self.provider_name.capitalize()} API key is invalid or unauthorized (HTTP {response.status_code})"
                    )

                # Proactively pace if Groq reports minute token bucket is nearly empty
                rem_tok = response.headers.get("x-ratelimit-remaining-tokens")
                reset_tok = response.headers.get("x-ratelimit-reset-tokens")
                if rem_tok is not None and reset_tok is not None:
                    try:
                        if int(rem_tok) < 400:
                            rst_sec = parse_duration_seconds(reset_tok)
                            if rst_sec and 0 < rst_sec <= 30.0:
                                now_t = asyncio.get_event_loop().time()
                                self._rate_limit_until = max(self._rate_limit_until, now_t + rst_sec)
                    except ValueError:
                        pass

                response.raise_for_status()
                return response

            except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ConnectError) as net_err:
                last_error = net_err
                err_label = "ReadTimeout" if isinstance(net_err, httpx.ReadTimeout) else str(net_err)
                if isinstance(net_err, httpx.ReadTimeout) or attempt == self.max_retries:
                    raise RuntimeError(
                        f"[{self.provider_name}] Network error after {attempt + 1} attempt(s): {err_label}"
                    )
                delay = self.base_backoff * (2 ** attempt) + random.uniform(0.1, self.jitter)
                await asyncio.sleep(delay)

        raise RuntimeError(f"[{self.provider_name}] Failed: {last_error}")
