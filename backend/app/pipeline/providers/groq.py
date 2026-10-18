"""
backend/app/pipeline/providers/groq.py - Groq Cloud HTTP REST Provider with Multi-Key Pool
==========================================================================================
High-speed inference provider supporting:
  - Round-robin load balancing across multiple Groq API keys.
  - Per-key independent pacing sliding window (28 RPM / 2.1s gap per key).
  - Dynamic 429 failover to next healthy key upon minute or daily quota exhaustion.
  - Automatic 401/403 unauthorized key deactivation.
"""

import asyncio
import json
import logging
import random
import time
from typing import Any, Dict, List, Optional, Union
import httpx

from backend.app.pipeline.providers.base import BaseAIProvider, classify_rate_limit_429
from backend.app.pipeline.providers.key_pool import GroqKey, GroqKeyPool, KeyStatus, parse_key_list
from backend.app.pipeline.usage_tracker import get_usage_tracker

logger = logging.getLogger("osint.ai.groq")

GROQ_BASE_URL = "https://api.groq.com/openai/v1/chat/completions"


class GroqProvider(BaseAIProvider):
    """
    Groq Cloud async REST provider via httpx with multi-key load balancing and failover.
    Supports qwen/qwen3.8-27b, openai/gpt-oss-120b, openai/gpt-oss-20b.
    """

    def __init__(
        self,
        api_key: Optional[Union[str, List[str]]] = None,
        key_pool: Optional[GroqKeyPool] = None,
        default_model: str = "qwen/qwen3.8-27b",
        timeout_seconds: float = 25.0,
        max_retries: int = 4,
        base_backoff_sec: float = 1.0,
        jitter_sec: float = 0.5,
    ):
        # Extract primary key string for BaseAIProvider initialization
        primary_key = ""
        if key_pool and key_pool.get_keys():
            primary_key = key_pool.get_keys()[0].key
        elif api_key:
            parsed = parse_key_list(api_key)
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

        # Attach or initialize GroqKeyPool
        if key_pool is not None:
            self.key_pool = key_pool
            if api_key:
                for k in parse_key_list(api_key):
                    self.key_pool.add_key(k)
        else:
            initial_keys = parse_key_list(api_key) if api_key else []
            self.key_pool = GroqKeyPool(keys=initial_keys)

        # Keep primary api_key in sync
        if self.key_pool.get_keys() and not self.api_key:
            self.api_key = self.key_pool.get_keys()[0].key

        # Do not impose arbitrary client-side pacing caps; keys are locked dynamically
        # using provider-returned response headers (retry-after, x-ratelimit-*).
        self.min_interval_sec = 0.0
        self.max_rpm = 0

    @property
    def provider_name(self) -> str:
        return "groq"

    async def _execute_with_key_rotation(
        self,
        model_name: str,
        payload_builder: Any,
        json_mode: bool = False,
    ) -> httpx.Response:
        """
        Executes an API call using round-robin load balancing across healthy keys in the pool.
        Automatically rotates keys and retries if 429 rate limits or transient errors occur.
        """
        keys = self.key_pool.get_keys()
        if not keys:
            raise PermissionError(
                "Groq API key is invalid or unauthorized (HTTP 401)"
            )

        client = await self.get_client()
        last_error: Optional[Exception] = None
        last_429_reason: str = ""

        max_attempts = max(self.max_retries + 1, len(keys) * 2)

        for attempt in range(max_attempts):
            key_slot: Optional[GroqKey] = await self.key_pool.acquire_next_key(model=model_name)

            if not key_slot:
                # All keys in pool are either unauthorized or on cooldown for this specific model
                all_unauth = all(k.status == KeyStatus.UNAUTHORIZED for k in keys)
                if all_unauth:
                    raise PermissionError(
                        "Groq API key is invalid or unauthorized (HTTP 401)"
                    )

                # If all keys are temporarily waiting on a short minute window (best_wait <= 12.0s),
                # sleep and retry rather than immediately raising a 429 to the orchestrator
                _, best_wait = self.key_pool.get_best_wait_time(model=model_name)
                if best_wait <= 12.0 and attempt < max_attempts - 1:
                    sleep_time = min(best_wait + random.uniform(0.05, 0.15), 12.0)
                    logger.info(
                        f"[groq:{model_name}] All keys temporarily on short cooldown (best_wait={best_wait:.2f}s). "
                        f"Sleeping {sleep_time:.2f}s and retrying (attempt {attempt + 1}/{max_attempts})..."
                    )
                    await asyncio.sleep(sleep_time)
                    continue

                reason_detail = last_429_reason or f"All Groq API keys in pool are in cooldown for model '{model_name}'"
                raise RuntimeError(
                    f"Groq RateLimitError: 429 Rate limit reached for model '{model_name}' ({reason_detail})"
                )

            # Enforce pacing slot for this specific key and model
            await key_slot.acquire_pacing_slot(model=model_name)

            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {key_slot.key}",
            }
            payload = payload_builder()

            try:
                response = await client.post(GROQ_BASE_URL, json=payload, headers=headers)

                # Dynamically update key rate-limit status for this model from returned response headers
                key_slot.update_from_response(response, model=model_name)

                # HTTP 429: Rate Limit (Minute or Daily Quota)
                if response.status_code == 429:
                    last_429_reason = (
                        key_slot._model_cooldown_reasons.get(model_name.lower())
                        or key_slot.cooldown_reason
                        or "Groq Rate limit reached"
                    )
                    model_lock_sec = key_slot.get_model_cooldown_remaining(model_name) or (key_slot.retry_after_sec or 3.0)
                    is_daily = key_slot.status == KeyStatus.DAILY_EXHAUSTED
                    try:
                        get_usage_tracker().record_call(
                            provider="groq",
                            model=model_name,
                            key=key_slot.key,
                            key_field="groq_api_key",
                            success=False,
                            status_code=429,
                            error_message=last_429_reason,
                            is_rate_limit=True,
                            is_daily_quota=is_daily,
                            cooldown_sec=model_lock_sec,
                            is_key_level=False,
                        )
                    except Exception:
                        pass
                    logger.warning(
                        f"[groq:{model_name}] Key {key_slot.masked_key} received 429 ({last_429_reason}). "
                        f"Rotating to next available key in pool for model '{model_name}' (attempt {attempt + 1}/{max_attempts})..."
                    )
                    continue

                # HTTP 401 / 403: Invalid or Unauthorized Key
                if response.status_code in (401, 403):
                    key_slot.mark_unauthorized()
                    logger.warning(
                        f"[groq:{model_name}] Key {key_slot.masked_key} rejected with HTTP {response.status_code}. "
                        f"Deactivating key and rotating to next available key..."
                    )
                    continue

                # HTTP 400: Strict json_object validator rejection (json_validate_failed)
                if json_mode and response.status_code == 400:
                    err_body = response.text or ""
                    if "json_validate_failed" in err_body:
                        try:
                            failed_gen = response.json().get("error", {}).get("failed_generation", "")
                            if failed_gen and "{" in failed_gen and "}" in failed_gen:
                                setattr(response, "_used_key", key_slot.key)
                                setattr(response, "_used_key_slot", key_slot)
                                return response
                        except Exception:
                            pass

                        if key_slot.remaining_tokens is not None and key_slot.remaining_tokens < 1000:
                            logger.info(
                                f"[groq:{model_name}] Tokens depleted ({key_slot.remaining_tokens} remaining); "
                                f"skipping json_validate_failed retry to avoid 429."
                            )
                        else:
                            logger.info(
                                f"[groq:{model_name}] Retrying on key {key_slot.masked_key} without "
                                f"strict json_object wrapper after json_validate_failed..."
                            )
                            fallback_payload = dict(payload)
                            fallback_payload.pop("response_format", None)
                            fallback_payload["max_tokens"] = min(max(payload.get("max_tokens", 1024), 1024), 1536)
                            fallback_resp = await client.post(GROQ_BASE_URL, json=fallback_payload, headers=headers)
                            key_slot.update_from_response(fallback_resp, model=model_name)
                            if fallback_resp.status_code == 200:
                                setattr(fallback_resp, "_used_key", key_slot.key)
                                setattr(fallback_resp, "_used_key_slot", key_slot)
                                return fallback_resp
                            if fallback_resp.status_code == 429:
                                last_429_reason = (
                                    key_slot._model_cooldown_reasons.get(model_name.lower())
                                    or key_slot.cooldown_reason
                                    or "Groq Rate limit reached"
                                )
                                model_lock_sec = key_slot.get_model_cooldown_remaining(model_name) or (key_slot.retry_after_sec or 3.0)
                                is_daily = key_slot.status == KeyStatus.DAILY_EXHAUSTED
                                try:
                                    get_usage_tracker().record_call(
                                        provider="groq",
                                        model=model_name,
                                        key=key_slot.key,
                                        key_field="groq_api_key",
                                        success=False,
                                        status_code=429,
                                        error_message=last_429_reason,
                                        is_rate_limit=True,
                                        is_daily_quota=is_daily,
                                        cooldown_sec=model_lock_sec,
                                        is_key_level=False,
                                    )
                                except Exception:
                                    pass
                                logger.warning(
                                    f"[groq:{model_name}] Fallback retry received 429 ({last_429_reason}). "
                                    f"Rotating key..."
                                )
                                continue
                            response = fallback_resp

                # HTTP 503: Service Unavailable
                if response.status_code == 503:
                    logger.warning(
                        f"[groq:{model_name}] Groq Service Unavailable (503) on {key_slot.masked_key}. Rotating key..."
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
                    f"[groq:{model_name}] Network error on {key_slot.masked_key}: {net_err}. Retrying..."
                )
                delay = self.base_backoff * (2 ** min(attempt, 3)) + random.uniform(0.1, self.jitter)
                await asyncio.sleep(delay)
                continue

        if last_429_reason:
            raise RuntimeError(f"Groq RateLimitError: 429 Rate limit reached for model '{model_name}' ({last_429_reason})")
        raise RuntimeError(f"[groq:{model_name}] Request failed after {max_attempts} attempts: {last_error}")

    async def generate_text(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        model: Optional[str] = None,
        temperature: float = 0.2,
        max_tokens: int = 1024,
        json_mode: bool = False,
    ) -> str:
        model_name = model or self.default_model or "qwen/qwen3.8-27b"

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})

        user_content = prompt
        # Groq requires the word 'json' in prompt when json_object mode is requested
        if json_mode and "json" not in user_content.lower() and (not system_prompt or "json" not in system_prompt.lower()):
            user_content += "\nProvide output as valid JSON."

        messages.append({"role": "user", "content": user_content})

        # Reasoning models (e.g. openai/gpt-oss-120b, openai/gpt-oss-20b, qwen3.8-27b) consume tokens
        # for reasoning before emitting JSON. Clamping between 768 and 1536 ensures JSON validity
        # without blowing through Groq's 8,000 TPM limit in 2 calls.
        effective_max_tokens = max_tokens
        if json_mode and any(tag in model_name.lower() for tag in ("gpt-oss", "qwen", "deepseek")):
            effective_max_tokens = max(min(max_tokens, 1536), 768)

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
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)
        total_tokens = usage.get("total_tokens", prompt_tokens + completion_tokens)
        used_key = getattr(response, "_used_key", None) or self.api_key

        try:
            get_usage_tracker().record_call(
                provider="groq",
                model=model_name,
                key=used_key,
                key_field="groq_api_key",
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
            "provider": "groq",
            "model": model_name,
            "prompt": prompt,
            "status_code": response.status_code,
        })

        choices = res_data.get("choices", [])
        if not choices:
            # Handle possible failed_generation fallback
            failed_gen = res_data.get("error", {}).get("failed_generation")
            if failed_gen:
                return str(failed_gen)
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
        model_name = model or self.default_model or "qwen/qwen3.8-27b"
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
