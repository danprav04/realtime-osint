"""
backend/app/pipeline/providers/zai.py - Z.ai / Zhipu AI Provider
==============================================================
Zero-cost async REST provider utilizing Zhipu BigModel's permanent $0 Flash tier:
  - Models: glm-4.7-flash, glm-4.5-flash, glm-4-flash.
  - Quota: Unlimited RPD and Unlimited TPD directly on official platform.
  - Concurrency Guard: Enforces 3 concurrent slot boundary via asyncio.Semaphore
    to prevent concurrency throttling errors.
"""

import asyncio
import json
import logging
import os
from typing import Any, Dict, Optional
import httpx

from backend.app.config import get_settings
from backend.app.pipeline.providers.base import BaseAIProvider

logger = logging.getLogger("osint.ai.zai")

DEFAULT_ZAI_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"
DEFAULT_ZAI_MODEL = "glm-4.7-flash"

# Process-wide concurrency semaphore for Z.ai free tier
_zai_concurrency_semaphore = asyncio.Semaphore(3)


class ZaiProvider(BaseAIProvider):
    """
    Z.ai (Zhipu AI) async REST provider via OpenAI-compatible chat completions.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        default_model: str = DEFAULT_ZAI_MODEL,
        base_url: Optional[str] = None,
        timeout_seconds: float = 25.0,
        max_retries: int = 3,
        base_backoff_sec: float = 1.0,
        jitter_sec: float = 0.5,
    ):
        st = get_settings()
        resolved_key = (
            api_key
            or getattr(st, "ZAI_API_KEY", None)
            or os.environ.get("ZAI_API_KEY", "")
        )
        super().__init__(
            api_key=resolved_key,
            default_model=default_model,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            base_backoff_sec=base_backoff_sec,
            jitter_sec=jitter_sec,
        )
        self.min_interval_sec = 0.2
        self.max_rpm = 300
        self.max_minute_wait_sec = 65.0
        self.concurrency_limit = 3
        self.semaphore = _zai_concurrency_semaphore
        self.base_url = (base_url or getattr(st, "ZAI_BASE_URL", None) or DEFAULT_ZAI_BASE_URL).rstrip("/")

    @property
    def provider_name(self) -> str:
        return "zai"

    async def generate_text(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        model: Optional[str] = None,
        temperature: float = 0.2,
        max_tokens: int = 2048,
        json_mode: bool = False,
    ) -> str:
        model_name = (model or self.default_model or DEFAULT_ZAI_MODEL).strip()
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
            async with _zai_concurrency_semaphore:
                return await client.post(api_endpoint, json=payload, headers=headers)

        try:
            response = await self._execute_with_retry(_call)
        except httpx.HTTPStatusError as http_err:
            if json_mode and http_err.response is not None and http_err.response.status_code == 400:
                logger.info(f"[zai:{model_name}] Retrying without strict response_format...")
                fallback_payload = dict(payload)
                fallback_payload.pop("response_format", None)

                async def _call_no_rf() -> httpx.Response:
                    async with _zai_concurrency_semaphore:
                        return await client.post(api_endpoint, json=fallback_payload, headers=headers)

                response = await self._execute_with_retry(_call_no_rf)
            else:
                raise

        res_data = response.json()
        usage = res_data.get("usage", {})
        total_tokens = usage.get("total_tokens", 0)

        self.call_history.append({
            "provider": "zai",
            "model": model_name,
            "prompt": prompt[:100],
            "tokens": total_tokens,
            "status_code": response.status_code,
        })

        choices = res_data.get("choices", [])
        if not choices:
            raise ValueError(f"Z.ai response empty: {res_data}")

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
        """Structured stage processing for Z.ai."""
        ctx = context or {}
        model_name = (model or self.default_model or DEFAULT_ZAI_MODEL).strip()
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
