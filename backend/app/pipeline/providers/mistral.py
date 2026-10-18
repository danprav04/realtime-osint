"""
backend/app/pipeline/providers/mistral.py - Mistral AI Provider with Multi-Key Pool & Rate Limit System
========================================================================================================
High-throughput async REST provider supporting Mistral's Free Experiment plan ($10 monthly allowance per key).
Features:
  - Round-robin load balancing across multiple Mistral API keys.
  - Tailored rate-limiting per model based on official Mistral Limits (RPS and TPM).
  - Dynamic 429 failover to next healthy key upon RPS/TPM limit or monthly allowance exhaustion.
  - Automatic 401/403 unauthorized key deactivation.
  - Pacing slot enforcement respecting per-model RPS limits.
"""

import asyncio
import json
import logging
import os
import random
import time
from typing import Any, Dict, List, Optional, Union
import httpx

from backend.app.config import get_settings
from backend.app.pipeline.providers.base import BaseAIProvider
from backend.app.pipeline.providers.mistral_key_pool import (
    KeyStatus,
    MistralKey,
    MistralKeyPool,
    get_mistral_model_limits,
    parse_key_list,
)
from backend.app.pipeline.usage_tracker import get_usage_tracker

logger = logging.getLogger("osint.ai.mistral")

DEFAULT_MISTRAL_BASE_URL = "https://api.mistral.ai/v1"
DEFAULT_MISTRAL_MODEL = "ministral-3b-2512"


class MistralProvider(BaseAIProvider):
    """
    Mistral AI async REST provider via OpenAI-compatible chat completions with multi-key pool support.
    """

    def __init__(
        self,
        api_key: Optional[Union[str, List[str]]] = None,
        key_pool: Optional[MistralKeyPool] = None,
        default_model: str = DEFAULT_MISTRAL_MODEL,
        base_url: Optional[str] = None,
        timeout_seconds: float = 25.0,
        max_retries: int = 3,
        base_backoff_sec: float = 1.0,
        jitter_sec: float = 0.5,
    ):
        st = get_settings()

        # Extract primary key string for BaseAIProvider initialization
        primary_key = ""
        if key_pool and key_pool.get_keys():
            primary_key = key_pool.get_keys()[0].key
        elif api_key:
            parsed = parse_key_list(api_key)
            if parsed:
                primary_key = parsed[0]
        else:
            resolved_env = (
                getattr(st, "MISTRAL_API_KEYS", None)
                or os.environ.get("MISTRAL_API_KEYS")
                or getattr(st, "MISTRAL_API_KEY", None)
                or os.environ.get("MISTRAL_API_KEY", "")
            )
            parsed = parse_key_list(resolved_env)
            if parsed:
                primary_key = parsed[0]

        super().__init__(
            api_key=primary_key,
            default_model=default_model,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            base_backoff_sec=base_backoff_sec,
            jitter_sec=jitter_sec,
        )

        # Attach or initialize MistralKeyPool
        if key_pool is not None:
            self.key_pool = key_pool
            if api_key:
                for k in parse_key_list(api_key):
                    self.key_pool.add_key(k)
        else:
            initial_keys = parse_key_list(api_key) if api_key else []
            if not initial_keys:
                resolved_env = (
                    getattr(st, "MISTRAL_API_KEYS", None)
                    or os.environ.get("MISTRAL_API_KEYS")
                    or getattr(st, "MISTRAL_API_KEY", None)
                    or os.environ.get("MISTRAL_API_KEY", "")
                )
                initial_keys = parse_key_list(resolved_env)
            self.key_pool = MistralKeyPool(keys=initial_keys)

        # Keep primary api_key in sync
        if self.key_pool.get_keys() and not self.api_key:
            self.api_key = self.key_pool.get_keys()[0].key

        # Initial pacing parameters (preserved for backward compatibility with tests)
        self.min_interval_sec = 0.2
        self.max_rpm = 300
        self.max_minute_wait_sec = 65.0
        self.base_url = (base_url or getattr(st, "MISTRAL_BASE_URL", None) or DEFAULT_MISTRAL_BASE_URL).rstrip("/")

    @property
    def provider_name(self) -> str:
        return "mistral"

    async def _execute_with_key_rotation(
        self,
        model_name: str,
        payload_builder: Any,
        json_mode: bool = False,
    ) -> httpx.Response:
        """
        Executes an API call using round-robin load balancing across healthy keys in the pool.
        Rotates keys and retries if 429 rate limits, quota limits, or transient errors occur.
        """
        keys = self.key_pool.get_keys()
        if not keys:
            raise PermissionError("Mistral API key is invalid or unauthorized (HTTP 401)")

        client = await self.get_client()
        last_error: Optional[Exception] = None
        last_429_reason: str = ""

        max_attempts = max(self.max_retries + 1, len(keys) * 2)

        for attempt in range(max_attempts):
            key_slot: Optional[MistralKey] = await self.key_pool.acquire_next_key(model=model_name)

            if not key_slot:
                all_unauth = all(k.status == KeyStatus.UNAUTHORIZED for k in keys)
                if all_unauth:
                    raise PermissionError("Mistral API key is invalid or unauthorized (HTTP 401)")
                reason_detail = last_429_reason or f"All Mistral API keys in pool are in cooldown for model '{model_name}' (minute rate limit / model RPS limit)"
                if "minute rate limit" not in reason_detail.lower():
                    reason_detail = f"{reason_detail} (minute rate limit / model RPS limit)"
                raise RuntimeError(
                    f"Mistral RateLimitError: 429 Rate limit reached for model '{model_name}' ({reason_detail})"
                )

            # Enforce pacing slot for this specific key and model RPS limit
            await key_slot.acquire_pacing_slot(model=model_name)

            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {key_slot.key}",
            }
            payload = payload_builder()
            api_endpoint = f"{self.base_url}/chat/completions"

            try:
                response = await client.post(api_endpoint, json=payload, headers=headers)

                # Dynamically update key rate-limit status for this model from response
                key_slot.update_from_response(response, model=model_name)

                # HTTP 429: Rate Limit (RPS, TPM or Monthly Quota)
                if response.status_code == 429:
                    last_429_reason = (
                        key_slot._model_cooldown_reasons.get(model_name.lower())
                        or key_slot.cooldown_reason
                        or "Mistral Rate limit reached (minute rate limit / model RPS limit)"
                    )
                    model_lock_sec = key_slot.get_model_cooldown_remaining(model_name) or (key_slot.retry_after_sec or 3.0)
                    is_monthly = key_slot.status == KeyStatus.DAILY_EXHAUSTED
                    try:
                        get_usage_tracker().record_call(
                            provider="mistral",
                            model=model_name,
                            key=key_slot.key,
                            key_field="mistral_api_key",
                            success=False,
                            status_code=429,
                            error_message=last_429_reason,
                            is_rate_limit=True,
                            is_daily_quota=is_monthly,
                            cooldown_sec=model_lock_sec,
                            is_key_level=is_monthly,
                        )
                    except Exception:
                        pass
                    logger.warning(
                        f"[mistral:{model_name}] Key {key_slot.masked_key} received 429 ({last_429_reason}). "
                        f"Rotating to next available key in pool for model '{model_name}' (attempt {attempt + 1}/{max_attempts})..."
                    )
                    continue

                # HTTP 401 / 403: Invalid or Unauthorized Key
                if response.status_code in (401, 403):
                    key_slot.mark_unauthorized()
                    logger.warning(
                        f"[mistral:{model_name}] Key {key_slot.masked_key} rejected with HTTP {response.status_code}. "
                        f"Deactivating key and rotating to next available key..."
                    )
                    continue

                # HTTP 400: Strict JSON format rejection fallback
                if json_mode and response.status_code == 400:
                    logger.info(
                        f"[mistral:{model_name}] Retrying on key {key_slot.masked_key} without "
                        f"strict response_format after HTTP 400..."
                    )
                    fallback_payload = dict(payload)
                    fallback_payload.pop("response_format", None)
                    fallback_resp = await client.post(api_endpoint, json=fallback_payload, headers=headers)
                    key_slot.update_from_response(fallback_resp, model=model_name)
                    if fallback_resp.status_code == 200:
                        setattr(fallback_resp, "_used_key", key_slot.key)
                        setattr(fallback_resp, "_used_key_slot", key_slot)
                        return fallback_resp
                    if fallback_resp.status_code == 429:
                        last_429_reason = key_slot.cooldown_reason or "Mistral Rate limit reached (minute rate limit / model RPS limit)"
                        continue
                    response = fallback_resp

                # HTTP 503 / 500: Server errors
                if response.status_code in (500, 502, 503, 504):
                    logger.warning(
                        f"[mistral:{model_name}] Server error HTTP {response.status_code} on {key_slot.masked_key}. Rotating key..."
                    )
                    delay = self.base_backoff * (2 ** min(attempt, 3)) + random.uniform(0.1, self.jitter)
                    await asyncio.sleep(delay)
                    continue

                if response.is_error:
                    response.raise_for_status()

                setattr(response, "_used_key", key_slot.key)
                setattr(response, "_used_key_slot", key_slot)
                return response

            except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ConnectError) as net_err:
                last_error = net_err
                logger.warning(
                    f"[mistral:{model_name}] Network error on {key_slot.masked_key}: {net_err}. Retrying..."
                )
                delay = self.base_backoff * (2 ** min(attempt, 3)) + random.uniform(0.1, self.jitter)
                await asyncio.sleep(delay)
                continue

        if last_429_reason:
            if "minute rate limit" not in last_429_reason.lower():
                last_429_reason = f"{last_429_reason} (minute rate limit / model RPS limit)"
            raise RuntimeError(
                f"Mistral RateLimitError: 429 Rate limit reached for model '{model_name}' ({last_429_reason})"
            )
        raise RuntimeError(f"[mistral:{model_name}] Request failed after {max_attempts} attempts: {last_error}")

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

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})

        user_content = prompt
        if json_mode and "json" not in user_content.lower() and (not system_prompt or "json" not in system_prompt.lower()):
            user_content += "\nProvide output strictly in valid JSON format."

        messages.append({"role": "user", "content": user_content})

        # Preserve caller-specified max_tokens (e.g. Stage 0 max_tokens=256)
        effective_max_tokens = max_tokens if max_tokens is not None else (1536 if json_mode else 1024)

        def _build_payload() -> Dict[str, Any]:
            pl: Dict[str, Any] = {
                "model": model_name,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": effective_max_tokens,
            }
            if json_mode:
                pl["response_format"] = {"type": "json_object"}
            return pl

        t0 = time.monotonic()
        response = await self._execute_with_key_rotation(
            model_name=model_name,
            payload_builder=_build_payload,
            json_mode=json_mode,
        )
        duration_ms = (time.monotonic() - t0) * 1000.0

        res_data = response.json()
        usage = res_data.get("usage", {})
        total_tokens = usage.get("total_tokens", 0)
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)
        used_key = getattr(response, "_used_key", None) or self.api_key

        try:
            get_usage_tracker().record_call(
                provider="mistral",
                model=model_name,
                key=used_key,
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
