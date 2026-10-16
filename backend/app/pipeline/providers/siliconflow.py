"""
backend/app/pipeline/providers/siliconflow.py - SiliconFlow Provider
===================================================================
High-capacity async REST provider utilizing SiliconFlow's permanent $0 open-weight catalog.
Key models:
  - Qwen/Qwen2.5-7B-Instruct (~1,000 RPM, independent bucket)
  - Qwen/Qwen3-8B (~1,000 RPM, independent bucket)
  - THUDM/glm-4-9b-chat (~1,000 RPM, independent bucket)
"""

import asyncio
import json
import logging
import os
from typing import Any, Dict, Optional
import httpx

from backend.app.config import get_settings
from backend.app.pipeline.providers.base import BaseAIProvider

logger = logging.getLogger("osint.ai.siliconflow")

DEFAULT_SILICONFLOW_BASE_URL = "https://api.siliconflow.com/v1"
DEFAULT_SILICONFLOW_MODEL = "Qwen/Qwen2.5-7B-Instruct"


class SiliconFlowProvider(BaseAIProvider):
    """
    SiliconFlow async REST provider via OpenAI-compatible chat completions.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        default_model: str = DEFAULT_SILICONFLOW_MODEL,
        base_url: Optional[str] = None,
        timeout_seconds: float = 25.0,
        max_retries: int = 3,
        base_backoff_sec: float = 1.0,
        jitter_sec: float = 0.5,
    ):
        st = get_settings()
        resolved_key = (
            api_key
            or getattr(st, "SILICONFLOW_API_KEY", None)
            or os.environ.get("SILICONFLOW_API_KEY", "")
        )
        norm_model = self.normalize_model_name(default_model)
        super().__init__(
            api_key=resolved_key,
            default_model=norm_model,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            base_backoff_sec=base_backoff_sec,
            jitter_sec=jitter_sec,
        )
        self.default_model = norm_model
        # High RPM capacity (~1,000 RPM per model = 0.1s spacing)
        self.min_interval_sec = 0.1
        self.max_rpm = 600
        self.max_minute_wait_sec = 65.0
        self.base_url = (base_url or getattr(st, "SILICONFLOW_BASE_URL", None) or DEFAULT_SILICONFLOW_BASE_URL).rstrip("/")

    @property
    def provider_name(self) -> str:
        return "siliconflow"

    @classmethod
    def normalize_model_name(cls, model_name: Optional[str]) -> str:
        m = (model_name or DEFAULT_SILICONFLOW_MODEL).strip()
        m_lower = m.lower()
        if m_lower in ("qwen2.5-7b", "qwen-7b", "qwen2.5-7b-instruct"):
            return "Qwen/Qwen2.5-7B-Instruct"
        if m_lower in ("qwen3-8b", "qwen3.8b", "qwen3-8b-instruct"):
            return "Qwen/Qwen3-8B"
        if m_lower in ("glm-4-9b", "glm-4-9b-chat", "thudm/glm-4-9b"):
            return "THUDM/glm-4-9b-chat"
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

        try:
            response = await self._execute_with_retry(_call)
        except httpx.HTTPStatusError as http_err:
            if json_mode and http_err.response is not None and http_err.response.status_code == 400:
                logger.info(f"[siliconflow:{model_name}] Retrying without strict response_format...")
                fallback_payload = dict(payload)
                fallback_payload.pop("response_format", None)

                async def _call_no_rf() -> httpx.Response:
                    return await client.post(api_endpoint, json=fallback_payload, headers=headers)

                response = await self._execute_with_retry(_call_no_rf)
            else:
                raise

        res_data = response.json()
        usage = res_data.get("usage", {})
        total_tokens = usage.get("total_tokens", 0)

        self.call_history.append({
            "provider": "siliconflow",
            "model": model_name,
            "prompt": prompt[:100],
            "tokens": total_tokens,
            "status_code": response.status_code,
        })

        choices = res_data.get("choices", [])
        if not choices:
            raise ValueError(f"SiliconFlow response empty: {res_data}")

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
        """Structured stage processing for SiliconFlow."""
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
