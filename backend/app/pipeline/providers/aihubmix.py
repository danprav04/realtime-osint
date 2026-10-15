"""
backend/app/pipeline/providers/aihubmix.py - AI Hub Mix Free Tier Provider
===========================================================================
High-capacity, token-heavy async REST provider via httpx.
Strictly enforced constraints:
  - Free models only (e.g. coding-glm-5.3-free, coding-minimax-m3-free, nemotron-3-super-120b-a12b-free).
  - Global daily quota sentinel: 100 RPD and 1M TPD across all models.
  - Safe pacing: 10 RPM (6.0s spacing).
"""

import asyncio
from datetime import datetime, timezone
import json
import logging
from typing import Any, Dict, Optional, Set
import httpx

from backend.app.config import get_settings
from backend.app.pipeline.providers.base import BaseAIProvider, parse_duration_seconds

logger = logging.getLogger("osint.ai.aihubmix")

DEFAULT_AIHUBMIX_BASE_URL = "https://aihubmix.com/v1"

# Flagship free models tailored for token-heavy analysis
FLAGSHIP_FREE_MODELS: Set[str] = {
    "coding-glm-5.3-free",
    "coding-glm-5.3-flash-free",
    "coding-minimax-m3-free",
    "nemotron-3-super-120b-a12b-free",
    "nemotron-3-ultra-550b-a55b-free",
    "coding-glm-5.2-free",
    "coding-glm-5.1-free",
    "qwen3.8-flash-free",
    "qwen3.8-max-free",
    "xiaomi-mimo-v2.6-pro-free",
    "xiaomi-mimo-v2.5-pro-free",
    "dots-3-note-preview-free",
}


class AIHubMixQuotaTracker:
    """
    Global thread-safe in-memory daily quota tracker enforcing 100 RPD and 1M TPD.
    Automatically resets at 00:00 UTC.
    """

    def __init__(self, max_rpd: int = 100, max_tpd: int = 1_000_000):
        self.max_rpd = max_rpd
        self.max_tpd = max_tpd
        self._current_date_utc: str = self._get_current_utc_date()
        self._requests_today: int = 0
        self._tokens_today: int = 0
        self._lock = asyncio.Lock()

    @staticmethod
    def _get_current_utc_date() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    async def _check_and_rollover(self) -> None:
        today = self._get_current_utc_date()
        if today != self._current_date_utc:
            self._current_date_utc = today
            self._requests_today = 0
            self._tokens_today = 0
            logger.info(f"[AIHubMix Quota] New UTC day {today} - reset daily RPD and TPD counters to 0.")

    async def record_request(self) -> None:
        async with self._lock:
            await self._check_and_rollover()
            if self._requests_today >= self.max_rpd:
                raise RuntimeError(
                    f"AIHubMix ResourceExhausted: 429 Daily request quota exhausted "
                    f"({self._requests_today}/{self.max_rpd} RPD limit reached across free models)"
                )
            if self._tokens_today >= self.max_tpd:
                raise RuntimeError(
                    f"AIHubMix ResourceExhausted: 429 Daily token quota exhausted "
                    f"({self._tokens_today}/{self.max_tpd} TPD limit reached across free models)"
                )
            self._requests_today += 1

    async def record_tokens(self, tokens_used: int) -> None:
        async with self._lock:
            await self._check_and_rollover()
            self._tokens_today += max(0, tokens_used)
            if self._tokens_today >= self.max_tpd:
                logger.warning(
                    f"[AIHubMix Quota] Token budget reached ({self._tokens_today}/{self.max_tpd} TPD). "
                    f"Subsequent calls will failover."
                )

    async def get_usage_stats(self) -> Dict[str, Any]:
        async with self._lock:
            await self._check_and_rollover()
            return {
                "date_utc": self._current_date_utc,
                "requests_today": self._requests_today,
                "max_rpd": self.max_rpd,
                "remaining_requests": max(0, self.max_rpd - self._requests_today),
                "tokens_today": self._tokens_today,
                "max_tpd": self.max_tpd,
                "remaining_tokens": max(0, self.max_tpd - self._tokens_today),
            }

    def reset_for_testing(self) -> None:
        self._requests_today = 0
        self._tokens_today = 0


# Global singleton quota tracker
_global_quota_tracker = AIHubMixQuotaTracker()


def get_aihubmix_quota_tracker() -> AIHubMixQuotaTracker:
    return _global_quota_tracker


class AIHubMixProvider(BaseAIProvider):
    """
    AI Hub Mix async REST provider for high-context, token-heavy workflows.
    Calls OpenAI-compatible endpoint at https://aihubmix.com/v1/chat/completions.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        default_model: str = "coding-glm-5.3-flash-free",
        base_url: Optional[str] = None,
        timeout_seconds: float = 35.0,
        max_retries: int = 3,
        base_backoff_sec: float = 2.0,
        jitter_sec: float = 0.5,
        quota_tracker: Optional[AIHubMixQuotaTracker] = None,
    ):
        st = get_settings()
        resolved_key = (
            api_key
            or getattr(st, "AIHUBMIX_API_KEY", None)
            or os.environ.get("AIHUBMIX_API_KEY", "")
        )
        super().__init__(
            api_key=resolved_key,
            default_model=default_model,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            base_backoff_sec=base_backoff_sec,
            jitter_sec=jitter_sec,
        )
        # Strict 10 RPM pacing = 6.0s spacing
        self.min_interval_sec = 6.0
        self.max_rpm = 10
        self.max_minute_wait_sec = 65.0

        self.base_url = (base_url or getattr(st, "AIHUBMIX_BASE_URL", None) or DEFAULT_AIHUBMIX_BASE_URL).rstrip("/")
        self.quota_tracker = quota_tracker or _global_quota_tracker

        # Ensure default model is valid free model
        self.validate_free_model(self.default_model)

    @property
    def provider_name(self) -> str:
        return "aihubmix"

    @classmethod
    def is_free_model(cls, model_name: str) -> bool:
        """Verify whether a model identifier represents an AI Hub Mix free model."""
        if not model_name:
            return False
        m_clean = model_name.strip().lower()
        return m_clean.endswith("-free") or m_clean.endswith(":free") or "-free" in m_clean or ":free" in m_clean

    @classmethod
    def validate_free_model(cls, model_name: str) -> str:
        """Enforce strict free-model requirement to prevent balance consumption."""
        clean = (model_name or "").strip()
        if not cls.is_free_model(clean):
            raise ValueError(
                f"AI Hub Mix free tier integration only permits free models. "
                f"'{model_name}' is not a free model (must end with '-free' or ':free')."
            )
        return clean

    async def generate_text(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        model: Optional[str] = None,
        temperature: float = 0.2,
        max_tokens: int = 2048,
        json_mode: bool = False,
    ) -> str:
        model_name = self.validate_free_model(model or self.default_model or "coding-glm-5.3-flash-free")
        client = await self.get_client()

        # Check and record request in daily quota tracker
        await self.quota_tracker.record_request()

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})

        user_content = prompt
        if json_mode and "json" not in user_content.lower() and (not system_prompt or "json" not in system_prompt.lower()):
            user_content += "\nProvide output strictly in valid JSON format."

        messages.append({"role": "user", "content": user_content})

        # Reasoning models (e.g. coding-glm-5.3, coding-minimax-m3, nemotron) consume completion tokens
        # for internal reasoning_content before emitting final content.
        effective_max_tokens = max(max_tokens, 2048 if json_mode else 1024)

        payload: Dict[str, Any] = {
            "model": model_name,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": effective_max_tokens,
        }

        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        api_endpoint = f"{self.base_url}/chat/completions"

        async def _call() -> httpx.Response:
            return await client.post(api_endpoint, json=payload, headers=headers)

        try:
            response = await self._execute_with_retry(_call)
        except httpx.HTTPStatusError as http_err:
            if json_mode and http_err.response is not None and http_err.response.status_code == 400:
                # If strict response_format was rejected by the underlying model, retry without it
                logger.info(f"[aihubmix:{model_name}] Retrying without strict json_object wrapper...")
                fallback_payload = dict(payload)
                fallback_payload.pop("response_format", None)

                async def _call_no_rf() -> httpx.Response:
                    return await client.post(api_endpoint, json=fallback_payload, headers=headers)

                response = await self._execute_with_retry(_call_no_rf)
            else:
                raise

        res_data = response.json()

        # Track token consumption in daily tracker
        usage = res_data.get("usage", {})
        total_tokens = usage.get("total_tokens", 0)
        if total_tokens > 0:
            await self.quota_tracker.record_tokens(total_tokens)

        self.call_history.append({
            "provider": "aihubmix",
            "model": model_name,
            "prompt": prompt[:100],
            "tokens": total_tokens,
            "status_code": response.status_code,
        })

        choices = res_data.get("choices", [])
        if not choices:
            raise ValueError(f"AI Hub Mix response empty: {res_data}")

        msg_obj = choices[0].get("message", {})
        return msg_obj.get("content") or msg_obj.get("reasoning_content") or ""

    async def process_stage(
        self,
        stage: str,
        input_text: str,
        context: Optional[Dict[str, Any]] = None,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Structured stage processing for AI Hub Mix."""
        ctx = context or {}
        model_name = self.validate_free_model(model or self.default_model or "coding-glm-5.3-flash-free")
        temp = temperature if temperature is not None else 0.2

        if stage in ("extraction", "extraction_translation"):
            prompt = (
                f"Extract structured entities, detect language, and translate to English in valid JSON.\n"
                f"Input text:\n{input_text}"
            )
            raw = await self.generate_text(
                prompt=prompt,
                model=model_name,
                temperature=temp,
                json_mode=True,
            )
            return json.loads(raw) if raw.startswith("{") else {"translated_text": raw}

        elif stage in ("scoring", "importance_scoring"):
            scoring_prompt = ctx.get("scoring_prompt", "")
            prompt = (
                f"Score this event 1-10 in valid JSON according to criteria: {scoring_prompt}\n"
                f"Text:\n{input_text}"
            )
            raw = await self.generate_text(
                prompt=prompt,
                model=model_name,
                temperature=temp,
                json_mode=True,
            )
            return json.loads(raw) if raw.startswith("{") else {"importance_score": 5, "score_rationale": raw}

        elif stage in ("summarization",):
            prompt = f"Summarize this event with a headline and concise summary in valid JSON:\n{input_text}"
            raw = await self.generate_text(
                prompt=prompt,
                model=model_name,
                temperature=temp,
                max_tokens=3072,
                json_mode=True,
            )
            return json.loads(raw) if raw.startswith("{") else {"summary": raw, "headline": input_text[:70]}

        elif stage in ("visuals", "visual_generation"):
            prompt = f"Categorize trend and generate cluster tags in valid JSON for:\n{input_text}"
            raw = await self.generate_text(
                prompt=prompt,
                model=model_name,
                temperature=temp,
                json_mode=True,
            )
            return json.loads(raw) if raw.startswith("{") else {"trend_category": "General", "cluster_tags": []}

        else:
            raw = await self.generate_text(prompt=input_text, model=model_name, temperature=temp)
            return {"result": raw}
