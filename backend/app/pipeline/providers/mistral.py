"""
backend/app/pipeline/providers/mistral.py - Mistral AI Provider (La Plateforme)
==============================================================================
High-throughput async REST provider supporting Mistral's Free Experiment plan ($10 monthly allowance).
High-concurrency model targets:
  - ministral-3b-2512 (12.50 Requests/sec | 1,300,000 TPM)
  - ministral-8b-2512 (3.13 Requests/sec | 625,000 TPM)
  - open-mistral-nemo (12B) / mistral-small-2603
"""

import asyncio
import json
import logging
import os
import time
from typing import Any, Dict, Optional
import httpx

from backend.app.config import get_settings
from backend.app.pipeline.providers.base import BaseAIProvider
from backend.app.pipeline.usage_tracker import get_usage_tracker

logger = logging.getLogger("osint.ai.mistral")

DEFAULT_MISTRAL_BASE_URL = "https://api.mistral.ai/v1"
DEFAULT_MISTRAL_MODEL = "ministral-3b-2512"


class MistralProvider(BaseAIProvider):
    """
    Mistral AI async REST provider via OpenAI-compatible chat completions.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        default_model: str = DEFAULT_MISTRAL_MODEL,
        base_url: Optional[str] = None,
        timeout_seconds: float = 25.0,
        max_retries: int = 3,
        base_backoff_sec: float = 1.0,
        jitter_sec: float = 0.5,
    ):
        st = get_settings()
        resolved_key = (
            api_key
            or getattr(st, "MISTRAL_API_KEY", None)
            or os.environ.get("MISTRAL_API_KEY", "")
        )
        super().__init__(
            api_key=resolved_key,
            default_model=default_model,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            base_backoff_sec=base_backoff_sec,
            jitter_sec=jitter_sec,
        )
        # Fast pacing (ministral-3b supports up to 12.5 req/s; 0.2s spacing allows ~300 RPM)
        self.min_interval_sec = 0.2
        self.max_rpm = 300
        self.max_minute_wait_sec = 65.0
        self.base_url = (base_url or getattr(st, "MISTRAL_BASE_URL", None) or DEFAULT_MISTRAL_BASE_URL).rstrip("/")

    @property
    def provider_name(self) -> str:
        return "mistral"

    async def generate_text(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        model: Optional[str] = None,
        temperature: float = 0.2,
        max_tokens: int = 2048,
        json_mode: bool = False,
    ) -> str:
        model_name = (model or self.default_model or DEFAULT_MISTRAL_MODEL).strip()
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

        # Tier-aware pacing based on selected Mistral model
        low_m = model_name.lower()
        if "14b" in low_m or "large" in low_m or "small" in low_m or "medium" in low_m:
            # ministral-14b has a 30 RPM cap (2.05s spacing)
            self.min_interval_sec = 2.05
            self.max_rpm = 29
        elif "code" in low_m:
            # codestral-2508 allows 125 RPM
            self.min_interval_sec = 0.50
            self.max_rpm = 120
        elif "8b" in low_m or "nemo" in low_m:
            # ministral-8b allows 188 RPM
            self.min_interval_sec = 0.35
            self.max_rpm = 175
        else:
            # ministral-3b allows up to 750 RPM
            self.min_interval_sec = 0.12
            self.max_rpm = 500

        t0 = time.monotonic()
        try:
            response = await self._execute_with_retry(_call)
        except httpx.HTTPStatusError as http_err:
            if json_mode and http_err.response is not None and http_err.response.status_code == 400:
                # If strict response_format was rejected by model, retry without it
                logger.info(f"[mistral:{model_name}] Retrying without strict response_format...")
                fallback_payload = dict(payload)
                fallback_payload.pop("response_format", None)

                async def _call_no_rf() -> httpx.Response:
                    return await client.post(api_endpoint, json=fallback_payload, headers=headers)

                try:
                    response = await self._execute_with_retry(_call_no_rf)
                except Exception as retry_err:
                    duration_ms = (time.monotonic() - t0) * 1000.0
                    sc = getattr(getattr(retry_err, "response", None), "status_code", 400)
                    try:
                        get_usage_tracker().record_call(
                            provider="mistral",
                            model=model_name,
                            key=self.api_key,
                            key_field="mistral_api_key",
                            duration_ms=duration_ms,
                            success=False,
                            status_code=sc,
                        )
                    except Exception:
                        pass
                    raise
            else:
                duration_ms = (time.monotonic() - t0) * 1000.0
                sc = getattr(getattr(http_err, "response", None), "status_code", 500)
                try:
                    get_usage_tracker().record_call(
                        provider="mistral",
                        model=model_name,
                        key=self.api_key,
                        key_field="mistral_api_key",
                        duration_ms=duration_ms,
                        success=False,
                        status_code=sc,
                    )
                except Exception:
                    pass
                raise
        except Exception as gen_err:
            duration_ms = (time.monotonic() - t0) * 1000.0
            sc = 429 if "429" in str(gen_err) else (403 if "403" in str(gen_err) else 500)
            try:
                get_usage_tracker().record_call(
                    provider="mistral",
                    model=model_name,
                    key=self.api_key,
                    key_field="mistral_api_key",
                    duration_ms=duration_ms,
                    success=False,
                    status_code=sc,
                )
            except Exception:
                pass
            raise

        duration_ms = (time.monotonic() - t0) * 1000.0
        res_data = response.json()
        usage = res_data.get("usage", {})
        total_tokens = usage.get("total_tokens", 0)
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)

        try:
            get_usage_tracker().record_call(
                provider="mistral",
                model=model_name,
                key=self.api_key,
                key_field="mistral_api_key",
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
            "provider": "mistral",
            "model": model_name,
            "prompt": prompt[:100],
            "tokens": total_tokens,
            "status_code": response.status_code,
        })

        choices = res_data.get("choices", [])
        if not choices:
            raise ValueError(f"Mistral response empty: {res_data}")

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
        """Structured stage processing for Mistral."""
        ctx = context or {}
        model_name = (model or self.default_model or DEFAULT_MISTRAL_MODEL).strip()
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
