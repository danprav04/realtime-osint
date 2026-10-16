"""
backend/app/pipeline/providers/gemini.py - Google Gemini HTTP REST Provider
"""

import json
import logging
from typing import Any, Dict, Optional
import httpx

from backend.app.config import get_settings
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
        max_retries: int = 4,
        base_backoff_sec: float = 1.0,
        jitter_sec: float = 0.5,
    ):
        st = get_settings()
        resolved_key = (
            api_key
            or getattr(st, "GEMINI_API_KEY", None)
            or os.environ.get("GEMINI_API_KEY", "")
        )
        is_gemma = "gemma" in (default_model or "").lower()
        effective_timeout = max(timeout_seconds, 40.0) if is_gemma else timeout_seconds
        super().__init__(
            api_key=resolved_key,
            default_model=default_model,
            timeout_seconds=effective_timeout,
            max_retries=max_retries,
            base_backoff_sec=base_backoff_sec,
            jitter_sec=jitter_sec,
        )
        # Safe pacing for Gemini free tier (15 RPM limit = 4.0s minimum gap; 15 RPM & 11K TPM for gemma-4)
        if is_gemma:
            self.min_interval_sec = 4.0
            self.max_rpm = 15
            self.max_tpm = 11000  # Conservative 11K TPM limit (Google AI Studio has a 16K TPM limit)
        else:
            self.min_interval_sec = 4.1
            self.max_rpm = 14
            self.max_tpm = 0

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
        is_gemma = "gemma" in model_name.lower()
        client = await self.get_client()
        url = f"{GEMINI_BASE_URL}/{model_name}:generateContent"

        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": self.api_key,
        }

        # Gemma token budgeting & compatibility:
        # 1. Gemma 4 models on Google AI Studio crash with HTTP 500 ("Internal error encountered.")
        #    if `systemInstruction` or `responseMimeType="application/json"` is included in the payload.
        #    Fold system_prompt directly into the user prompt parts and request JSON explicitly.
        # 2. Gemma is an internal reasoning model that emits ~400-1450 thought tokens before emitting output.
        #    maxOutputTokens must be at least 2048 so thinking tokens don't hit the ceiling and abort with HTTP 500.
        # 3. Safe temperature: Gemma-4 sampling crashes with 500 if temperature == 0.0; clamp to min 0.1.
        if is_gemma:
            effective_max_tokens = max(2048, max_tokens)
            effective_temp = max(0.1, temperature)
            combined_prompt = f"{system_prompt}\n\n{prompt}" if system_prompt else prompt
            if json_mode and "json" not in combined_prompt.lower():
                combined_prompt += "\nRespond strictly with valid JSON."

            payload: Dict[str, Any] = {
                "contents": [
                    {
                        "role": "user",
                        "parts": [{"text": combined_prompt}],
                    }
                ],
                "generationConfig": {
                    "temperature": effective_temp,
                    "maxOutputTokens": effective_max_tokens,
                },
            }
        else:
            effective_max_tokens = max_tokens
            effective_temp = temperature
            payload: Dict[str, Any] = {
                "contents": [
                    {
                        "role": "user",
                        "parts": [{"text": prompt}],
                    }
                ],
                "generationConfig": {
                    "temperature": effective_temp,
                    "maxOutputTokens": effective_max_tokens,
                },
            }
            if system_prompt:
                payload["systemInstruction"] = {
                    "parts": [{"text": system_prompt}]
                }
            if json_mode:
                payload["generationConfig"]["responseMimeType"] = "application/json"

        prompt_chars = len(prompt) + (len(system_prompt) if system_prompt else 0)
        est_input_tokens = int(prompt_chars / 3.5) + 15
        total_est_tokens = est_input_tokens + effective_max_tokens

        async def _call() -> httpx.Response:
            return await client.post(url, json=payload, headers=headers)

        try:
            response = await self._execute_with_retry(_call, estimated_tokens=total_est_tokens)
        except httpx.HTTPStatusError as http_err:
            # Gemma models on Google AI Studio may reject systemInstruction or responseMimeType="application/json"
            # with HTTP 400 INVALID_ARGUMENT. Fold system_prompt into user prompt and retry on the same model.
            if http_err.response is not None and http_err.response.status_code == 400 and (system_prompt or json_mode):
                logger.info(
                    f"[gemini:{model_name}] HTTP 400 with systemInstruction/responseMimeType; "
                    f"retrying on same model with inline prompt instructions..."
                )
                combined_prompt = f"{system_prompt}\n\n{prompt}" if system_prompt else prompt
                if json_mode and "json" not in combined_prompt.lower():
                    combined_prompt += "\nRespond strictly with valid JSON."
                compat_payload: Dict[str, Any] = {
                    "contents": [
                        {
                            "role": "user",
                            "parts": [{"text": combined_prompt}],
                        }
                    ],
                    "generationConfig": {
                        "temperature": max(0.1, temperature),
                        "maxOutputTokens": effective_max_tokens,
                    },
                }

                async def _call_compat() -> httpx.Response:
                    return await client.post(url, json=compat_payload, headers=headers)

                response = await self._execute_with_retry(_call_compat, estimated_tokens=total_est_tokens)
            else:
                raise

        res_data = response.json()

        # Synchronize token records with actual totalTokenCount reported by Google
        usage = res_data.get("usageMetadata", {})
        actual_tokens = usage.get("totalTokenCount", 0)
        token_record = getattr(response, "_token_record", None)
        if actual_tokens > 0:
            self.update_last_token_record(actual_tokens, record_ref=token_record)

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
        if not parts:
            return ""

        # Extract non-thought content parts (Gemma reasoning models emit thought=True parts first)
        non_thought_parts = [p.get("text", "") for p in parts if not p.get("thought", False)]
        if non_thought_parts:
            return "".join(non_thought_parts)
        return parts[-1].get("text", "")

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
