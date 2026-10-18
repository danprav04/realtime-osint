"""
backend/app/pipeline/usage_tracker.py - Unified Provider, Key & Model Perceived Usage Tracker
=============================================================================================
Tracks live perceived requests, tokens, latencies, rate-limit hits, cooldowns, and quotas
for each API key and model across Google Gemini, Groq Cloud, AI Hub Mix, Hugging Face TEI,
and ingestion/alert source providers.
"""

import hashlib
import logging
import os
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

from backend.app.pipeline.providers.key_pool import mask_key, parse_key_list

logger = logging.getLogger("osint.ai.usage")


def _get_utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_loop_time() -> float:
    try:
        import asyncio
        return asyncio.get_running_loop().time()
    except RuntimeError:
        import time
        return time.time()


def _hash_key(key: str) -> str:
    """Generate short non-reversible fingerprint for key identification."""
    if not key:
        return "unconfigured"
    return hashlib.sha256(key.strip().encode("utf-8")).hexdigest()[:12]


class ProviderUsageTracker:
    """
    Centralized, thread-safe live usage tracker recording the app's perceived
    activity across all keys, providers, and models.
    """

    def __init__(self):
        self._lock = threading.Lock()
        # Keyed by (provider, model_name.lower())
        self._models: Dict[str, Dict[str, Any]] = {}
        # Keyed by key_fingerprint or (provider, key_field, key_index)
        self._keys: Dict[str, Dict[str, Any]] = {}
        # Cumulative provider level counters
        self._providers: Dict[str, Dict[str, Any]] = {}

    def record_call(
        self,
        provider: str,
        model: Optional[str] = None,
        key: Optional[str] = None,
        key_field: Optional[str] = None,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
        success: bool = True,
        status_code: int = 200,
        error_message: Optional[str] = None,
        is_rate_limit: bool = False,
        is_daily_quota: bool = False,
        duration_ms: float = 0.0,
        remaining_requests: Optional[int] = None,
        remaining_tokens: Optional[int] = None,
        limit_requests: Optional[int] = None,
        limit_tokens: Optional[int] = None,
        reset_requests_sec: Optional[float] = None,
        reset_tokens_sec: Optional[float] = None,
        cooldown_sec: Optional[float] = None,
        is_key_level: bool = False,
    ) -> None:
        """
        Record a perceived request outcome against provider, key, and model counters.
        """
        p_norm = (provider or "unknown").lower().strip()
        m_norm = (model or "default").strip()
        now_iso = _get_utc_now_iso()
        now_loop = _get_loop_time()

        calc_tokens = total_tokens if total_tokens > 0 else (prompt_tokens + completion_tokens)

        with self._lock:
            # 1. Update Provider Totals
            if p_norm not in self._providers:
                self._providers[p_norm] = {
                    "total_requests": 0,
                    "successful_requests": 0,
                    "failed_requests": 0,
                    "rate_limited_count": 0,
                    "total_tokens": 0,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "last_used_at": None,
                }
            p_stats = self._providers[p_norm]
            p_stats["total_requests"] += 1
            if success:
                p_stats["successful_requests"] += 1
            else:
                p_stats["failed_requests"] += 1
            if is_rate_limit:
                p_stats["rate_limited_count"] += 1
            p_stats["total_tokens"] += calc_tokens
            p_stats["prompt_tokens"] += prompt_tokens
            p_stats["completion_tokens"] += completion_tokens
            p_stats["last_used_at"] = now_iso

            # 2. Update Model Totals
            m_key = f"{p_norm}:{m_norm.lower()}"
            if m_key not in self._models:
                self._models[m_key] = {
                    "model_name": m_norm,
                    "provider": p_norm,
                    "total_requests": 0,
                    "successful_requests": 0,
                    "failed_requests": 0,
                    "rate_limited_count": 0,
                    "total_tokens": 0,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_duration_ms": 0.0,
                    "last_duration_ms": 0.0,
                    "last_used_at": None,
                    "last_status_code": status_code,
                    "last_error": None,
                    "cooldown_remaining_sec": 0.0,
                    "cooldown_until_ts": 0.0,
                }
            m_stats = self._models[m_key]
            m_stats["total_requests"] += 1
            if success:
                m_stats["successful_requests"] += 1
                m_stats["cooldown_remaining_sec"] = 0.0
                m_stats["cooldown_until_ts"] = 0.0
            else:
                m_stats["failed_requests"] += 1
                m_stats["last_error"] = str(error_message or f"HTTP {status_code}")
            if is_rate_limit:
                m_stats["rate_limited_count"] += 1
                if cooldown_sec and float(cooldown_sec) > 0:
                    m_stats["cooldown_remaining_sec"] = round(float(cooldown_sec), 1)
                    m_stats["cooldown_until_ts"] = now_loop + float(cooldown_sec)
            m_stats["total_tokens"] += calc_tokens
            m_stats["prompt_tokens"] += prompt_tokens
            m_stats["completion_tokens"] += completion_tokens
            m_stats["last_duration_ms"] = round(duration_ms, 1)
            m_stats["total_duration_ms"] += duration_ms
            m_stats["last_used_at"] = now_iso
            m_stats["last_status_code"] = status_code

            # 3. Update Key Totals (if key provided)
            if key and str(key).strip():
                clean_k = str(key).strip()
                k_fp = _hash_key(clean_k)
                k_id = f"{p_norm}:{k_fp}"
                if k_id not in self._keys:
                    self._keys[k_id] = {
                        "key_hash": k_fp,
                        "masked_key": mask_key(clean_k),
                        "key_field": key_field or f"{p_norm}_api_key",
                        "provider": p_norm,
                        "status": "active",
                        "total_requests": 0,
                        "successful_requests": 0,
                        "failed_requests": 0,
                        "rate_limited_count": 0,
                        "total_tokens": 0,
                        "prompt_tokens": 0,
                        "completion_tokens": 0,
                        "last_used_at": None,
                        "remaining_requests": None,
                        "remaining_tokens": None,
                        "limit_requests": None,
                        "limit_tokens": None,
                        "reset_requests_sec": None,
                        "reset_tokens_sec": None,
                        "cooldown_remaining_sec": 0.0,
                        "cooldown_reason": "",
                        "throttled_models": {},
                    }
                k_stats = self._keys[k_id]
                k_stats["total_requests"] += 1
                if success:
                    k_stats["successful_requests"] += 1
                    # Successful call on key: restore active status if not unauthorized
                    if k_stats["status"] != "unauthorized":
                        k_stats["status"] = "active"
                        k_stats["cooldown_remaining_sec"] = 0.0
                        k_stats["cooldown_reason"] = ""
                    if "throttled_models" in k_stats and m_norm in k_stats["throttled_models"]:
                        k_stats["throttled_models"].pop(m_norm, None)
                else:
                    k_stats["failed_requests"] += 1
                    if is_rate_limit:
                        k_stats["rate_limited_count"] += 1
                        if is_key_level or (is_daily_quota and not model):
                            # Entire key is locked out (e.g. account-wide daily quota exhaustion)
                            k_stats["status"] = "daily_exhausted" if is_daily_quota else "minute_cooldown"
                            k_stats["cooldown_reason"] = str(error_message or "Rate limit 429")
                            if cooldown_sec:
                                k_stats["cooldown_remaining_sec"] = round(float(cooldown_sec), 1)
                        else:
                            # Per-model rate limit: key remains ACTIVE for all other models!
                            if k_stats["status"] != "unauthorized":
                                k_stats["status"] = "active"
                            k_stats["cooldown_remaining_sec"] = 0.0
                            if cooldown_sec and m_norm:
                                th_dict = k_stats.setdefault("throttled_models", {})
                                th_dict[m_norm] = round(float(cooldown_sec), 1)

                k_stats["total_tokens"] += calc_tokens
                k_stats["prompt_tokens"] += prompt_tokens
                k_stats["completion_tokens"] += completion_tokens
                k_stats["last_used_at"] = now_iso

                # Update live headers if passed
                if remaining_requests is not None:
                    k_stats["remaining_requests"] = remaining_requests
                if remaining_tokens is not None:
                    k_stats["remaining_tokens"] = remaining_tokens
                if limit_requests is not None:
                    k_stats["limit_requests"] = limit_requests
                if limit_tokens is not None:
                    k_stats["limit_tokens"] = limit_tokens
                if reset_requests_sec is not None:
                    k_stats["reset_requests_sec"] = reset_requests_sec
                if reset_tokens_sec is not None:
                    k_stats["reset_tokens_sec"] = reset_tokens_sec

    def reset_for_testing(self) -> None:
        """Clear all in-memory metrics (used in test fixtures)."""
        with self._lock:
            self._models.clear()
            self._keys.clear()
            self._providers.clear()

    def get_telemetry(
        self,
        ai_settings: Optional[Dict[str, Any]] = None,
        db_keys: Optional[Dict[str, str]] = None,
        sources: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        Compile comprehensive snapshot of perceived usage for all keys and models.
        Incorporates live GroqKeyPool status, Orchestrator cooldowns, and active AI pipeline stages.
        """
        from backend.app.config import get_settings
        from backend.app.pipeline.providers.factory import get_shared_groq_pool, get_shared_mistral_pool, resolve_api_keys

        st = get_settings()
        effective_db_keys = dict(db_keys or {})

        # 1. Discover stage assignments from ai_settings
        stage_map: Dict[str, Set[str]] = {}
        model_provider_map: Dict[str, str] = {}
        primary_models: Set[str] = set()
        fallback_models: Set[str] = set()

        if ai_settings and isinstance(ai_settings, dict):
            for stage_name, stage_val in ai_settings.items():
                if isinstance(stage_val, dict):
                    p_model = stage_val.get("model_name") or stage_val.get("model")
                    p_prov = stage_val.get("provider")
                    if p_model:
                        clean_m = str(p_model).strip()
                        stage_map.setdefault(clean_m, set()).add(stage_name)
                        primary_models.add(clean_m)
                        if p_prov:
                            model_provider_map[clean_m] = str(p_prov).strip().lower()
                    for fb in stage_val.get("fallbacks") or []:
                        if isinstance(fb, dict):
                            fb_model = fb.get("model_name") or fb.get("model")
                            fb_prov = fb.get("provider")
                            if fb_model:
                                clean_fb = str(fb_model).strip()
                                stage_map.setdefault(clean_fb, set()).add(f"{stage_name} (fallback)")
                                fallback_models.add(clean_fb)
                                if fb_prov:
                                    model_provider_map[clean_fb] = str(fb_prov).strip().lower()

        # Map active embedding models to stage_map
        try:
            from backend.app.services.embedding_service import get_embedding_service
            emb_srv = get_embedding_service()
            if emb_srv.default_model:
                stage_map.setdefault(emb_srv.default_model, set()).add("embeddings")
                model_provider_map[emb_srv.default_model] = "gemini"
            if emb_srv.fallback_model:
                stage_map.setdefault(emb_srv.fallback_model, set()).add("embeddings (fallback)")
                model_provider_map[emb_srv.fallback_model] = "gemini"
        except Exception:
            pass

        # 2. Check live orchestrator model cooldowns
        orchestrator_cooldowns: Dict[str, float] = {}
        try:
            from backend.app.pipeline.orchestrator import get_pipeline_orchestrator
            orch = get_pipeline_orchestrator()
            try:
                import asyncio
                loop = asyncio.get_running_loop()
                now_loop = loop.time()
            except RuntimeError:
                import time
                now_loop = time.time()
            for k, expiry in orch._model_cooldowns.items():
                if expiry > now_loop:
                    orchestrator_cooldowns[k] = max(0.0, expiry - now_loop)
        except Exception:
            pass

        # 3. Pull live GroqKeyPool keys & model cooldowns
        groq_pool_keys: List[Dict[str, Any]] = []
        groq_model_cooldowns: Dict[str, float] = {}
        try:
            pool = get_shared_groq_pool()
            groq_tel = pool.get_telemetry()
            groq_pool_keys = groq_tel.get("keys", [])
            for pk in groq_pool_keys:
                for m_k, cd_info in pk.get("model_cooldowns", {}).items():
                    cd_val = cd_info.get("cooldown_remaining_sec", 0.0) if isinstance(cd_info, dict) else float(cd_info)
                    if cd_val > groq_model_cooldowns.get(m_k, 0.0):
                        groq_model_cooldowns[m_k] = cd_val
        except Exception:
            pass

        # 3b. Pull live MistralKeyPool keys & model cooldowns
        mistral_pool_keys: List[Dict[str, Any]] = []
        mistral_model_cooldowns: Dict[str, float] = {}
        try:
            m_pool = get_shared_mistral_pool()
            mistral_tel = m_pool.get_telemetry()
            mistral_pool_keys = mistral_tel.get("keys", [])
            for pk in mistral_pool_keys:
                for m_k, cd_info in pk.get("model_cooldowns", {}).items():
                    cd_val = cd_info.get("cooldown_remaining_sec", 0.0) if isinstance(cd_info, dict) else float(cd_info)
                    if cd_val > mistral_model_cooldowns.get(m_k, 0.0):
                        mistral_model_cooldowns[m_k] = cd_val
        except Exception:
            pass

        # 4. Pull live AIHubMix quota stats
        aihubmix_quota: Dict[str, Any] = {}
        try:
            from backend.app.pipeline.providers.aihubmix import get_aihubmix_quota_tracker
            # Use non-blocking or sync inspection
            hub_tracker = get_aihubmix_quota_tracker()
            aihubmix_quota = {
                "requests_today": hub_tracker._requests_today,
                "tokens_today": hub_tracker._tokens_today,
                "max_rpd": hub_tracker.max_rpd,
                "max_tpd": hub_tracker.max_tpd,
                "remaining_requests": max(0, hub_tracker.max_rpd - hub_tracker._requests_today),
                "remaining_tokens": max(0, hub_tracker.max_tpd - hub_tracker._tokens_today),
            }
        except Exception:
            pass

        # 5. Pull live Embedding Quota
        embedding_quota: Dict[str, Any] = {}
        try:
            from backend.app.services.embedding_service import get_embedding_service
            emb_srv = get_embedding_service()
            embedding_quota = {
                "primary_model": emb_srv.default_model,
                "fallback_model": emb_srv.fallback_model,
                "hf_space_configured": bool(emb_srv.hf_embedding_url),
                "hf_embedding_url": emb_srv.hf_embedding_url or "",
            }
        except Exception:
            pass

        now_loop = _get_loop_time()

        with self._lock:
            # Build models response
            models_list: List[Dict[str, Any]] = []
            known_models_seen: Set[str] = set()

            # First add models with recorded activity
            for m_key, m_stats in self._models.items():
                m_name = m_stats["model_name"]
                p_name = m_stats["provider"]
                known_models_seen.add(f"{p_name}:{m_name.lower()}")

                cooldown_key = f"{p_name}:{m_name.strip()}"
                orch_cd = orchestrator_cooldowns.get(cooldown_key, 0.0)
                pool_cd = (
                    groq_model_cooldowns.get(m_name.lower().strip(), 0.0) if p_name == "groq"
                    else (mistral_model_cooldowns.get(m_name.lower().strip(), 0.0) if p_name == "mistral" else 0.0)
                )
                recorded_cd = max(0.0, m_stats.get("cooldown_until_ts", 0.0) - now_loop)
                active_cd = max(orch_cd, pool_cd, recorded_cd)

                assigned_stages = sorted(list(stage_map.get(m_name, [])))
                avg_lat = (
                    round(m_stats["total_duration_ms"] / m_stats["total_requests"], 1)
                    if m_stats["total_requests"] > 0
                    else 0.0
                )
                success_pct = (
                    round((m_stats["successful_requests"] / m_stats["total_requests"]) * 100, 1)
                    if m_stats["total_requests"] > 0
                    else 100.0
                )

                status = "cooldown" if active_cd > 0 else ("active" if m_stats["total_requests"] > 0 else "ready")

                models_list.append({
                    "model_name": m_name,
                    "provider": p_name,
                    "stages": assigned_stages,
                    "is_primary": m_name in primary_models,
                    "is_fallback": m_name in fallback_models,
                    "status": status,
                    "cooldown_remaining_sec": round(active_cd, 1),
                    "total_requests": m_stats["total_requests"],
                    "successful_requests": m_stats["successful_requests"],
                    "failed_requests": m_stats["failed_requests"],
                    "rate_limited_count": m_stats["rate_limited_count"],
                    "success_rate_pct": success_pct,
                    "total_tokens": m_stats["total_tokens"],
                    "prompt_tokens": m_stats["prompt_tokens"],
                    "completion_tokens": m_stats["completion_tokens"],
                    "avg_latency_ms": avg_lat,
                    "last_latency_ms": m_stats["last_duration_ms"],
                    "last_used_at": m_stats["last_used_at"],
                    "last_error": m_stats["last_error"],
                })

            catalog_defaults = [
                ("gemini", "gemini-3.5-flash-lite"),
                ("gemini", "gemini-3.1-flash-lite"),
                ("gemini", "gemma-4-26b-a4b-it"),
                ("gemini", "gemma-4-31b-it"),
                ("gemini", "gemini-embedding-2"),
                ("gemini", "gemini-embedding-001"),
                ("groq", "qwen/qwen3.8-27b"),
                ("groq", "openai/gpt-oss-120b"),
                ("groq", "openai/gpt-oss-20b"),
                ("aihubmix", "coding-glm-5.3-free"),
                ("aihubmix", "coding-minimax-m3-free"),
                ("aihubmix", "nemotron-3-super-120b"),
                ("mistral", "ministral-3b-2512"),
                ("mistral", "ministral-14b-2512"),
                ("mistral", "codestral-2508"),
                ("mistral", "ministral-8b-2512"),
                ("mistral", "open-mistral-nemo"),
                ("cloudflare", "@cf/meta/llama-3.2-1b-instruct"),
                ("cloudflare", "@cf/meta/llama-3.2-3b-instruct"),
                ("cloudflare", "@cf/meta/llama-3.1-8b-instruct-fp8-fast"),
                ("zai", "glm-4.7-flash"),
                ("zai", "glm-4.5-flash"),
                ("siliconflow", "Qwen/Qwen2.5-7B-Instruct"),
                ("siliconflow", "THUDM/GLM-4-9B-0414"),
                ("huggingface", "sentence-transformers/all-MiniLM-L6-v2"),
            ]
            # Add any explicitly configured models from stage_map
            for m_name, stages in stage_map.items():
                m_lower = m_name.lower()
                if m_name in model_provider_map:
                    p_cand = model_provider_map[m_name]
                elif any(tag in m_lower for tag in ("ministral", "mistral", "codestral", "open-mistral")):
                    p_cand = "mistral"
                elif any(tag in m_lower for tag in ("@cf/", "cloudflare")):
                    p_cand = "cloudflare"
                elif any(tag in m_lower for tag in ("glm-4.7", "glm-4.5", "glm-4-flash", "zai")):
                    p_cand = "zai"
                elif any(tag in m_lower for tag in ("siliconflow", "qwen2.5", "glm-4-9b")):
                    p_cand = "siliconflow"
                elif any(tag in m_lower for tag in ("coding", "minimax", "nemotron")):
                    p_cand = "aihubmix"
                elif any(tag in m_lower for tag in ("qwen3.8", "gpt-oss", "groq", "llama")):
                    p_cand = "groq"
                elif any(tag in m_lower for tag in ("gemini", "gemma")):
                    p_cand = "gemini"
                else:
                    p_cand = "gemini"
                catalog_defaults.append((p_cand, m_name))

            for p_cand, m_name in catalog_defaults:
                k_check = f"{p_cand}:{m_name.lower()}"
                if k_check not in known_models_seen:
                    known_models_seen.add(k_check)
                    assigned_stages = sorted(list(stage_map.get(m_name, [])))
                    cooldown_key = f"{p_cand}:{m_name.strip()}"
                    orch_cd = orchestrator_cooldowns.get(cooldown_key, 0.0)
                    pool_cd = (
                        groq_model_cooldowns.get(m_name.lower().strip(), 0.0) if p_cand == "groq"
                        else (mistral_model_cooldowns.get(m_name.lower().strip(), 0.0) if p_cand == "mistral" else 0.0)
                    )
                    active_cd = max(orch_cd, pool_cd)
                    models_list.append({
                        "model_name": m_name,
                        "provider": p_cand,
                        "stages": assigned_stages,
                        "is_primary": m_name in primary_models,
                        "is_fallback": m_name in fallback_models,
                        "status": "cooldown" if active_cd > 0 else "ready",
                        "cooldown_remaining_sec": round(active_cd, 1),
                        "total_requests": 0,
                        "successful_requests": 0,
                        "failed_requests": 0,
                        "rate_limited_count": 0,
                        "success_rate_pct": 100.0,
                        "total_tokens": 0,
                        "prompt_tokens": 0,
                        "completion_tokens": 0,
                        "avg_latency_ms": 0.0,
                        "last_latency_ms": 0.0,
                        "last_used_at": None,
                        "last_error": None,
                    })

            # Sort models: active first by total_requests descending, then alphabetical
            models_list.sort(key=lambda x: (x["total_requests"], 1 if x["stages"] else 0), reverse=True)

            # Build keys response
            keys_list: List[Dict[str, Any]] = []
            seen_key_fingerprints: Set[str] = set()

            # First add recorded keys
            for k_id, k_val in self._keys.items():
                seen_key_fingerprints.add(k_val["key_hash"])
                success_pct = (
                    round((k_val["successful_requests"] / k_val["total_requests"]) * 100, 1)
                    if k_val["total_requests"] > 0
                    else 100.0
                )
                keys_list.append({
                    **k_val,
                    "success_rate_pct": success_pct,
                })

            # Synchronize with live GroqKeyPool keys
            groq_keys_raw = resolve_api_keys("groq", db_keys=effective_db_keys, settings=st)
            for idx, raw_k in enumerate(groq_keys_raw):
                k_fp = _hash_key(raw_k)
                pool_match = next((pk for pk in groq_pool_keys if pk.get("masked_key") == mask_key(raw_k)), None)
                if k_fp not in seen_key_fingerprints:
                    seen_key_fingerprints.add(k_fp)
                    total_req = pool_match.get("total_requests", 0) if pool_match else 0
                    succ_req = pool_match.get("success_requests", 0) if pool_match else 0
                    rem_req = pool_match.get("remaining_requests") if pool_match else None
                    rem_tok = pool_match.get("remaining_tokens") if pool_match else None
                    lim_req = pool_match.get("limit_requests") if pool_match else None
                    lim_tok = pool_match.get("limit_tokens") if pool_match else None
                    rst_req = pool_match.get("reset_requests_sec") if pool_match else None
                    rst_tok = pool_match.get("reset_tokens_sec") if pool_match else None
                    cd_sec = pool_match.get("cooldown_remaining_sec", 0.0) if pool_match else 0.0
                    cd_rsn = pool_match.get("cooldown_reason", "") if pool_match else ""
                    last_used = pool_match.get("last_used_at") if pool_match else None
                    st_val = pool_match.get("status", "active") if pool_match else "active"

                    keys_list.append({
                        "key_hash": k_fp,
                        "masked_key": mask_key(raw_k),
                        "key_field": "groq_api_key",
                        "key_index": idx,
                        "provider": "groq",
                        "status": st_val,
                        "total_requests": total_req,
                        "successful_requests": succ_req,
                        "failed_requests": max(0, total_req - succ_req),
                        "rate_limited_count": pool_match.get("minute_rate_limits", 0) if pool_match else 0,
                        "success_rate_pct": round((succ_req / total_req * 100), 1) if total_req > 0 else 100.0,
                        "total_tokens": 0,
                        "prompt_tokens": 0,
                        "completion_tokens": 0,
                        "last_used_at": last_used,
                        "remaining_requests": rem_req,
                        "remaining_tokens": rem_tok,
                        "limit_requests": lim_req,
                        "limit_tokens": lim_tok,
                        "reset_requests_sec": rst_req,
                        "reset_tokens_sec": rst_tok,
                        "cooldown_remaining_sec": cd_sec,
                        "cooldown_reason": cd_rsn,
                        "throttled_models": pool_match.get("throttled_models", []) if pool_match else [],
                        "model_cooldowns": pool_match.get("model_cooldowns", {}) if pool_match else {},
                    })
                elif pool_match:
                    # Update existing key with live pool headers
                    for k in keys_list:
                        if k["key_hash"] == k_fp:
                            if pool_match.get("remaining_requests") is not None:
                                k["remaining_requests"] = pool_match["remaining_requests"]
                            if pool_match.get("remaining_tokens") is not None:
                                k["remaining_tokens"] = pool_match["remaining_tokens"]
                            if pool_match.get("limit_requests") is not None:
                                k["limit_requests"] = pool_match["limit_requests"]
                            if pool_match.get("limit_tokens") is not None:
                                k["limit_tokens"] = pool_match["limit_tokens"]
                            if pool_match.get("reset_requests_sec") is not None:
                                k["reset_requests_sec"] = pool_match["reset_requests_sec"]
                            if pool_match.get("reset_tokens_sec") is not None:
                                k["reset_tokens_sec"] = pool_match["reset_tokens_sec"]
                            if pool_match.get("cooldown_remaining_sec", 0.0) > 0:
                                k["cooldown_remaining_sec"] = pool_match["cooldown_remaining_sec"]
                                k["cooldown_reason"] = pool_match.get("cooldown_reason", "")
                                k["status"] = pool_match.get("status", "minute_cooldown")
                            if "throttled_models" in pool_match:
                                k["throttled_models"] = pool_match["throttled_models"]
                            if "model_cooldowns" in pool_match:
                                k["model_cooldowns"] = pool_match["model_cooldowns"]

            # Check other providers configured in db_keys or environment
            single_key_providers = [
                ("gemini", "gemini_api_key", effective_db_keys.get("gemini_api_key") or getattr(st, "GEMINI_API_KEY", None) or os.environ.get("GEMINI_API_KEY")),
                ("aihubmix", "aihubmix_api_key", effective_db_keys.get("aihubmix_api_key") or getattr(st, "AIHUBMIX_API_KEY", None) or os.environ.get("AIHUBMIX_API_KEY")),
                ("mistral", "mistral_api_key", effective_db_keys.get("mistral_api_key") or getattr(st, "MISTRAL_API_KEY", None) or os.environ.get("MISTRAL_API_KEY")),
                ("cloudflare", "cloudflare_api_token", effective_db_keys.get("cloudflare_api_token") or getattr(st, "CLOUDFLARE_API_TOKEN", None) or os.environ.get("CLOUDFLARE_API_TOKEN") or getattr(st, "CLOUDFLARE_API_KEY", None) or os.environ.get("CLOUDFLARE_API_KEY")),
                ("zai", "zai_api_key", effective_db_keys.get("zai_api_key") or getattr(st, "ZAI_API_KEY", None) or os.environ.get("ZAI_API_KEY")),
                ("siliconflow", "siliconflow_api_key", effective_db_keys.get("siliconflow_api_key") or getattr(st, "SILICONFLOW_API_KEY", None) or os.environ.get("SILICONFLOW_API_KEY")),
                ("huggingface", "hf_api_key", effective_db_keys.get("hf_api_key") or getattr(st, "HF_API_KEY", None) or os.environ.get("HF_API_KEY")),
                ("telegram", "telegram_bot_token", effective_db_keys.get("telegram_bot_token") or getattr(st, "TELEGRAM_BOT_TOKEN", None) or os.environ.get("TELEGRAM_BOT_TOKEN")),
                ("reddit", "reddit_client_id", effective_db_keys.get("reddit_client_id") or getattr(st, "REDDIT_CLIENT_ID", None) or os.environ.get("REDDIT_CLIENT_ID")),
                ("newsapi", "newsapi_key", effective_db_keys.get("newsapi_key") or getattr(st, "NEWSAPI_KEY", None) or os.environ.get("NEWSAPI_KEY")),
            ]

            for p_name, field_name, raw_val in single_key_providers:
                if raw_val and str(raw_val).strip() and not str(raw_val).startswith("***"):
                    clean_v = str(raw_val).strip()
                    k_fp = _hash_key(clean_v)
                    if k_fp not in seen_key_fingerprints:
                        seen_key_fingerprints.add(k_fp)
                        rem_req = None
                        rem_tok = None
                        lim_req = None
                        lim_tok = None
                        if p_name == "aihubmix" and aihubmix_quota:
                            rem_req = aihubmix_quota.get("remaining_requests")
                            rem_tok = aihubmix_quota.get("remaining_tokens")
                            lim_req = aihubmix_quota.get("max_rpd")
                            lim_tok = aihubmix_quota.get("max_tpd")

                        keys_list.append({
                            "key_hash": k_fp,
                            "masked_key": mask_key(clean_v),
                            "key_field": field_name,
                            "key_index": 0,
                            "provider": p_name,
                            "status": "active",
                            "total_requests": 0,
                            "successful_requests": 0,
                            "failed_requests": 0,
                            "rate_limited_count": 0,
                            "success_rate_pct": 100.0,
                            "total_tokens": 0,
                            "prompt_tokens": 0,
                            "completion_tokens": 0,
                            "last_used_at": None,
                            "remaining_requests": rem_req,
                            "remaining_tokens": rem_tok,
                            "limit_requests": lim_req,
                            "limit_tokens": lim_tok,
                            "reset_requests_sec": None,
                            "reset_tokens_sec": None,
                            "cooldown_remaining_sec": 0.0,
                            "cooldown_reason": "",
                        })

            # Assemble per-provider telemetry objects
            provider_names = [
                "gemini",
                "groq",
                "aihubmix",
                "mistral",
                "cloudflare",
                "zai",
                "siliconflow",
                "huggingface",
                "telegram",
                "reddit",
                "newsapi",
            ]
            provider_display_names = {
                "gemini": "Google Gemini",
                "groq": "Groq Cloud",
                "aihubmix": "AI Hub Mix",
                "mistral": "Mistral AI",
                "cloudflare": "Cloudflare Workers AI",
                "zai": "Z.ai (Zhipu)",
                "siliconflow": "SiliconFlow",
                "huggingface": "Hugging Face TEI",
                "telegram": "Telegram API",
                "reddit": "Reddit Data API",
                "newsapi": "NewsAPI / Currents",
            }

            providers_payload: Dict[str, Any] = {}
            total_app_requests = sum(p.get("total_requests", 0) for p in self._providers.values())
            total_app_tokens = sum(p.get("total_tokens", 0) for p in self._providers.values())

            for p_id in provider_names:
                p_prov = self._providers.get(p_id, {
                    "total_requests": 0,
                    "successful_requests": 0,
                    "failed_requests": 0,
                    "rate_limited_count": 0,
                    "total_tokens": 0,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "last_used_at": None,
                })

                p_keys = [k for k in keys_list if k["provider"] == p_id]
                p_models = [m for m in models_list if m["provider"] == p_id]

                active_keys = sum(1 for k in p_keys if k["status"] == "active")
                cooldown_keys = sum(1 for k in p_keys if k["cooldown_remaining_sec"] > 0)
                cooldown_models = sum(1 for m in p_models if m["cooldown_remaining_sec"] > 0)

                # Total provider requests includes sum from keys if higher
                p_total_req = max(p_prov["total_requests"], sum(k["total_requests"] for k in p_keys))
                p_succ_req = max(p_prov["successful_requests"], sum(k["successful_requests"] for k in p_keys))

                is_connected = len(p_keys) > 0 or any(m["stages"] for m in p_models)
                if p_id == "huggingface" and embedding_quota.get("hf_space_configured"):
                    is_connected = True

                providers_payload[p_id] = {
                    "id": p_id,
                    "name": provider_display_names.get(p_id, p_id.capitalize()),
                    "is_connected": is_connected,
                    "total_requests": p_total_req,
                    "successful_requests": p_succ_req,
                    "failed_requests": max(0, p_total_req - p_succ_req),
                    "rate_limited_count": p_prov["rate_limited_count"],
                    "total_tokens": p_prov["total_tokens"],
                    "prompt_tokens": p_prov["prompt_tokens"],
                    "completion_tokens": p_prov["completion_tokens"],
                    "active_keys_count": active_keys,
                    "cooldown_keys_count": cooldown_keys,
                    "cooldown_models_count": cooldown_models,
                    "last_used_at": p_prov["last_used_at"] or (p_keys[0]["last_used_at"] if p_keys else None),
                    "keys": p_keys,
                    "models": p_models,
                }

            return {
                "updated_at": _get_utc_now_iso(),
                "summary": {
                    "total_requests": max(total_app_requests, sum(k["total_requests"] for k in keys_list)),
                    "total_tokens": total_app_tokens,
                    "active_keys": sum(1 for k in keys_list if k["status"] == "active"),
                    "keys_in_cooldown": sum(1 for k in keys_list if k["cooldown_remaining_sec"] > 0),
                    "total_models": len(models_list),
                    "models_in_cooldown": sum(1 for m in models_list if m["cooldown_remaining_sec"] > 0),
                },
                "providers": providers_payload,
                "all_models": models_list,
                "all_keys": keys_list,
            }


# Process-wide singleton
_usage_tracker_instance = ProviderUsageTracker()


def get_usage_tracker() -> ProviderUsageTracker:
    """Return process-wide ProviderUsageTracker singleton."""
    return _usage_tracker_instance
