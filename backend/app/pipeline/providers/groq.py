"""
backend/app/pipeline/providers/groq.py - Groq Cloud HTTP REST Provider
"""

import json
import logging
from typing import Any, Dict, Optional
import httpx

from backend.app.pipeline.providers.base import BaseAIProvider

logger = logging.getLogger("osint.ai.groq")

GROQ_BASE_URL = "https://api.groq.com/openai/v1/chat/completions"


class GroqProvider(BaseAIProvider):
    """
    Groq Cloud async REST provider via httpx.
    Supports llama-3.3-70b-versatile, mixtral-8x7b-32768, llama-3.1-8b-instant.
    """

    @property
    def provider_name(self) -> str:
        return "groq"

    async def generate_text(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        model: Optional[str] = None,
        temperature: float = 0.2,
        max_tokens: int = 1024,
        json_mode: bool = False,
    ) -> str:
        model_name = model or self.default_model or "llama-3.3-70b-versatile"
        client = await self.get_client()

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})

        user_content = prompt
        # Groq requires the word 'json' in prompt when json_object mode is requested
        if json_mode and "json" not in user_content.lower() and (not system_prompt or "json" not in system_prompt.lower()):
            user_content += "\nProvide output as valid JSON."

        messages.append({"role": "user", "content": user_content})

        payload: Dict[str, Any] = {
            "model": model_name,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        async def _call() -> httpx.Response:
            return await client.post(GROQ_BASE_URL, json=payload, headers=headers)

        response = await self._execute_with_retry(_call)
        res_data = response.json()

        self.call_history.append({
            "provider": "groq",
            "model": model_name,
            "prompt": prompt,
            "status_code": response.status_code,
        })

        choices = res_data.get("choices", [])
        if not choices:
            raise ValueError(f"Groq response empty: {res_data}")

        return choices[0].get("message", {}).get("content", "")

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
        ctx = context or {}
        model_name = model or self.default_model or "llama-3.3-70b-versatile"
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
