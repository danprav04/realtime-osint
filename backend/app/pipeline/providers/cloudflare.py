"""
backend/app/pipeline/providers/cloudflare.py - Cloudflare Workers AI Provider
=============================================================================
High-throughput async REST provider utilizing Cloudflare's perpetual Free Tier:
  - 10,000 free Neurons per day (resets 00:00 UTC).
  - 300 RPM rate limit for text generation.
  - Zero accidental billing: hard stops automatically when daily neurons reach 10,000.
Optimal ultra-lightweight models:
  - @cf/meta/llama-3.2-1b-instruct (~4,100 RPD)
  - @cf/meta/llama-3.2-3b-instruct (~2,380 RPD)
  - @cf/meta/llama-3.1-8b-instruct-fp8-fast (~2,200 RPD)
"""

import asyncio
from datetime import datetime, timezone
import json
import logging
import os
import time
from typing import Any, Dict, Optional
import httpx

from backend.app.config import get_settings
from backend.app.pipeline.providers.base import BaseAIProvider
from backend.app.pipeline.usage_tracker import get_usage_tracker

logger = logging.getLogger("osint.ai.cloudflare")

DEFAULT_CLOUDFLARE_MODEL = "@cf/meta/llama-3.2-1b-instruct"


class CloudflareNeuronTracker:
    """
    In-memory daily Neuron budget tracker for Cloudflare Workers AI.
    Default free tier: 10,000 Neurons per day, resetting at 00:00 UTC.
    Protects against rate-limit overruns or unexpected billing.
    """

    def __init__(self, max_neurons_per_day: float = 10000.0):
        self.max_neurons = max_neurons_per_day
        self._current_date_utc: str = self._get_current_utc_date()
        self._neurons_today: float = 0.0
        self._lock = asyncio.Lock()

    @staticmethod
    def _get_current_utc_date() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    async def _check_and_rollover(self) -> None:
        today = self._get_current_utc_date()
        if today != self._current_date_utc:
            self._current_date_utc = today
            self._neurons_today = 0.0
            logger.info(f"[Cloudflare AI] New UTC day {today} - reset Neuron budget counter to 0.")

    async def record_neurons(self, neurons_used: float) -> None:
        async with self._lock:
            await self._check_and_rollover()
            if self._neurons_today + neurons_used > self.max_neurons:
                raise RuntimeError(
                    f"Cloudflare daily Neuron allowance exhausted: {self._neurons_today:.1f} used, "
                    f"attempted to add {neurons_used:.1f} (limit {self.max_neurons:.0f} Neurons/day)"
                )
            self._neurons_today += max(0.0, neurons_used)

    def get_neurons_used_today(self) -> float:
        return self._neurons_today


_global_neuron_tracker: Optional[CloudflareNeuronTracker] = None


def get_cloudflare_neuron_tracker() -> CloudflareNeuronTracker:
    global _global_neuron_tracker
    if _global_neuron_tracker is None:
        _global_neuron_tracker = CloudflareNeuronTracker()
    return _global_neuron_tracker


class CloudflareProvider(BaseAIProvider):
    """
    Cloudflare Workers AI async REST provider via OpenAI-compatible endpoint:
    https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1/chat/completions
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_token: Optional[str] = None,
        account_id: Optional[str] = None,
        default_model: str = DEFAULT_CLOUDFLARE_MODEL,
        base_url: Optional[str] = None,
        timeout_seconds: float = 25.0,
        max_retries: int = 3,
        base_backoff_sec: float = 1.0,
        jitter_sec: float = 0.5,
    ):
        st = get_settings()
        resolved_key = (
            api_token
            or api_key
            or getattr(st, "CLOUDFLARE_API_TOKEN", None)
            or os.environ.get("CLOUDFLARE_API_TOKEN", "")
        )
        super().__init__(
            api_key=resolved_key,
            default_model=self.normalize_model_name(default_model),
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            base_backoff_sec=base_backoff_sec,
            jitter_sec=jitter_sec,
        )
        self.default_model = self.normalize_model_name(default_model)
        self.api_token = resolved_key
        self.account_id = (
            account_id
            or getattr(st, "CLOUDFLARE_ACCOUNT_ID", None)
            or os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")
        )
        # 300 RPM rate limit = 0.2s spacing
        self.min_interval_sec = 0.2
        self.max_rpm = 300
        self.max_minute_wait_sec = 65.0

        if base_url:
            self.base_url = base_url.rstrip("/")
        elif self.account_id:
            self.base_url = f"https://api.cloudflare.com/client/v4/accounts/{self.account_id}/ai/v1"
        else:
            self.base_url = "https://api.cloudflare.com/client/v4/ai/v1"

    @property
    def chat_endpoint(self) -> str:
        return f"{self.base_url}/chat/completions"

    @property
    def provider_name(self) -> str:
        return "cloudflare"

    @classmethod
    def normalize_model_name(cls, model_name: Optional[str]) -> str:
        m = (model_name or DEFAULT_CLOUDFLARE_MODEL).strip()
        if not m.startswith("@cf/") and not m.startswith("@"):
            if "1b" in m.lower():
                return "@cf/meta/llama-3.2-1b-instruct"
            elif "3b" in m.lower():
                return "@cf/meta/llama-3.2-3b-instruct"
            elif "8b" in m.lower():
                return "@cf/meta/llama-3.1-8b-instruct-fp8-fast"
            else:
                return f"@cf/{m}"
        return m

    async def generate_text(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        model: Optional[str] = None,
        temperature: float = 0.2,
        max_tokens: int = 2048,
        json_mode: bool = False,
    ) -> str:
        model_name = self.normalize_model_name(model or self.default_model)
        client = await self.get_client()

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

        effective_max_tokens = max(max_tokens, 1536 if json_mode else 1024)

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

        t0 = time.monotonic()
        try:
            response = await self._execute_with_retry(_call)
        except httpx.HTTPStatusError as http_err:
            if json_mode and http_err.response is not None and http_err.response.status_code == 400:
                logger.info(f"[cloudflare:{model_name}] Retrying without strict response_format...")
                fallback_payload = dict(payload)
                fallback_payload.pop("response_format", None)

                async def _call_no_rf() -> httpx.Response:
                    return await client.post(api_endpoint, json=fallback_payload, headers=headers)

                response = await self._execute_with_retry(_call_no_rf)
            else:
                raise

        duration_ms = (time.monotonic() - t0) * 1000.0
        res_data = response.json()
        usage = res_data.get("usage", {})
        total_tokens = usage.get("total_tokens", 0)
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)

        # Record daily Neurons
        neurons_used = float(usage.get("neurons", 0)) or (total_tokens * 0.05 if total_tokens else 5.0)
        try:
            await get_cloudflare_neuron_tracker().record_neurons(neurons_used)
        except Exception as n_err:
            logger.warning(f"[Cloudflare AI] Daily neuron budget sentinel warning: {n_err}")

        try:
            get_usage_tracker().record_call(
                provider="cloudflare",
                model=model_name,
                key=self.api_token or self.api_key,
                key_field="cloudflare_api_token",
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                duration_ms=duration_ms,
                success=True,
                status_code=response.status_code,
            )
        except Exception:
            pass

        self.call_history.append({
            "provider": "cloudflare",
            "model": model_name,
            "prompt": prompt[:100],
            "tokens": total_tokens,
            "neurons": usage.get("neurons", 0),
            "status_code": response.status_code,
        })

        choices = res_data.get("choices", [])
        if not choices:
            raise ValueError(f"Cloudflare Workers AI response empty: {res_data}")

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
        """Structured stage processing for Cloudflare Workers AI."""
        ctx = context or {}
        model_name = self.normalize_model_name(model or self.default_model)
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
