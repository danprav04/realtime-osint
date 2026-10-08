"""
backend/app/pipeline/providers/gemini.py - Google Gemini HTTP REST Provider
"""

import json
import logging
from typing import Any, Dict, Optional
import httpx

from backend.app.pipeline.providers.base import BaseAIProvider

logger = logging.getLogger("osint.ai.gemini")

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"


class GeminiProvider(BaseAIProvider):
    """
    Google Gemini async REST provider via httpx.
    Supports gemini-3.5-flash-lite, gemini-3.1-flash-lite, gemini-2.5-flash with zero external SDK bloat.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        default_model: str = "gemini-3.5-flash-lite",
        timeout_seconds: float = 25.0,
        max_retries: int = 3,
        base_backoff_sec: float = 1.0,
        jitter_sec: float = 0.5,
    ):
        super().__init__(
            api_key=api_key,
            default_model=default_model,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            base_backoff_sec=base_backoff_sec,
            jitter_sec=jitter_sec,
        )
        # Safe pacing for Gemini free tier (15 RPM limit = 4.0s minimum gap)
        self.min_interval_sec = 3.0

    @property
    def provider_name(self) -> str:
        return "gemini"

    async def generate_text(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        model: Optional[str] = None,
        temperature: float = 0.2,
        max_tokens: int = 1024,
        json_mode: bool = False,
    ) -> str:
        model_name = model or self.default_model or "gemini-3.5-flash-lite"
        client = await self.get_client()
        url = f"{GEMINI_BASE_URL}/{model_name}:generateContent"

        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": self.api_key,
        }

        payload: Dict[str, Any] = {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": prompt}],
                }
            ],
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
            },
        }

        if system_prompt:
            payload["systemInstruction"] = {
                "parts": [{"text": system_prompt}]
            }

        if json_mode:
            payload["generationConfig"]["responseMimeType"] = "application/json"

        async def _call() -> httpx.Response:
            return await client.post(url, json=payload, headers=headers)

        response = await self._execute_with_retry(_call)
        res_data = response.json()

        self.call_history.append({
            "provider": "gemini",
            "model": model_name,
            "prompt": prompt,
            "status_code": response.status_code,
        })

        candidates = res_data.get("candidates", [])
        if not candidates:
            block = res_data.get("promptFeedback", {}).get("blockReason")
            raise ValueError(f"Gemini response empty or blocked (reason: {block})")

        parts = candidates[0].get("content", {}).get("parts", [])
        return parts[0].get("text", "") if parts else ""

    async def process_stage(
        self,
        stage: str,
        input_text: str,
        context: Optional[Dict[str, Any]] = None,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Generic stage processing invoking generate_text with json_mode=True.
        """
        from backend.app.pipeline.parser import extract_and_parse_json
        from backend.app.pipeline.models import (
            Stage1Result,
            Stage2Result,
            Stage3Result,
            Stage4Result,
        )

        ctx = context or {}
        model_name = model or self.default_model or "gemini-3.5-flash-lite"
        temp = temperature if temperature is not None else 0.2

        if stage in ("extraction", "extraction_translation"):
            prompt = (
                f"Extract structured entities, detect language, and translate to English.\n"
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
                f"Score this event 1-10 according to criteria: {scoring_prompt}\n"
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
            prompt = f"Summarize this event with a headline and concise summary:\n{input_text}"
            raw = await self.generate_text(
                prompt=prompt,
                model=model_name,
                temperature=temp,
                json_mode=True,
            )
            return json.loads(raw) if raw.startswith("{") else {"summary": raw, "headline": input_text[:70]}

        elif stage in ("visuals", "visual_generation"):
            prompt = f"Categorize trend and generate cluster tags for:\n{input_text}"
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
