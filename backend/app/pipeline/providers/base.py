"""
backend/app/pipeline/providers/base.py - Base AI Provider Interface & Resilient Retry Engine
"""

import abc
import asyncio
import logging
import random
from typing import Any, Callable, Coroutine, Dict, Optional
import httpx

logger = logging.getLogger("osint.ai.provider")


class BaseAIProvider(abc.ABC):
    """
    Abstract Base Class for AI Providers (Google Gemini & Groq Cloud).
    Enforces unified async generation, connection pooling, and error classification.
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

    async def _execute_with_retry(
        self,
        call_fn: Callable[[], Coroutine[Any, Any, httpx.Response]],
    ) -> httpx.Response:
        """
        Executes HTTP requests with exponential backoff, jitter, and error normalization.
        """
        if not self.api_key or not self.api_key.strip():
            raise PermissionError(
                f"{self.provider_name.capitalize()} API key is invalid or unauthorized (HTTP 401)"
            )

        last_error: Optional[Exception] = None
        for attempt in range(self.max_retries + 1):
            try:
                response = await call_fn()

                # Status 429: Rate Limit / Quota Exceeded
                if response.status_code == 429:
                    if attempt == self.max_retries:
                        if self.provider_name.lower() == "gemini":
                            raise RuntimeError("Gemini ResourceExhausted: 429 Quota Exceeded")
                        else:
                            raise RuntimeError("Groq RateLimitError: 429 Rate limit reached")

                    retry_header = response.headers.get("retry-after")
                    delay = self.base_backoff * (2 ** attempt) + random.uniform(0.1, self.jitter)
                    if retry_header:
                        try:
                            delay = max(float(retry_header), delay)
                        except ValueError:
                            pass
                    logger.warning(
                        f"[{self.provider_name}] 429 Rate Limit encountered. Retrying in {delay:.2f}s "
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

                # Status 401 / 403: Invalid / Unauthorized Key (Non-retryable)
                if response.status_code in (401, 403):
                    raise PermissionError(
                        f"{self.provider_name.capitalize()} API key is invalid or unauthorized (HTTP {response.status_code})"
                    )

                response.raise_for_status()
                return response

            except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ConnectError) as net_err:
                last_error = net_err
                if attempt == self.max_retries:
                    raise RuntimeError(
                        f"[{self.provider_name}] Network error after {self.max_retries} retries: {net_err}"
                    )
                delay = self.base_backoff * (2 ** attempt) + random.uniform(0.1, self.jitter)
                await asyncio.sleep(delay)

        raise RuntimeError(f"[{self.provider_name}] Failed: {last_error}")
