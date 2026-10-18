"""
backend/app/api/settings.py - System Settings & AI Pipeline Configuration Endpoints
"""

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.auth import require_api_key
from backend.app.config import get_settings
from backend.app.db.models import SystemSettings
from backend.app.db.session import get_db_session
from backend.app.pipeline.orchestrator import get_pipeline_orchestrator
from backend.app.services.redis_bus import CHANNEL_CONFIG_RELOAD, get_redis_bus

logger = logging.getLogger("osint.api.settings")
router = APIRouter(prefix="/api/settings", tags=["Settings"])

DEFAULT_AI_STAGES = {
    "gatekeeper": {
        "provider": "mistral",
        "model_name": "ministral-3b-2512",
        "temperature": 0.0,
        "fallbacks": [
            {"provider": "gemini", "model_name": "gemma-4-26b-a4b-it", "temperature": 0.0},
            {"provider": "groq", "model_name": "openai/gpt-oss-20b", "temperature": 0.0},
            {"provider": "cloudflare", "model_name": "@cf/meta/llama-3.2-1b-instruct", "temperature": 0.0},
            {"provider": "gemini", "model_name": "gemma-4-31b-it", "temperature": 0.0},
            {"provider": "zai", "model_name": "glm-4.7-flash", "temperature": 0.0},
        ],
    },
    "extraction": {
        "provider": "groq",
        "model_name": "qwen/qwen3.8-27b",
        "temperature": 0.2,
        "fallbacks": [
            {"provider": "mistral", "model_name": "ministral-14b-2512", "temperature": 0.2},
            {"provider": "gemini", "model_name": "gemini-3.5-flash-lite", "temperature": 0.2},
            {"provider": "gemini", "model_name": "gemma-4-26b-a4b-it", "temperature": 0.2},
            {"provider": "gemini", "model_name": "gemini-3.1-flash-lite", "temperature": 0.2},
            {"provider": "groq", "model_name": "openai/gpt-oss-120b", "temperature": 0.2},
            {"provider": "siliconflow", "model_name": "Qwen/Qwen2.5-7B-Instruct", "temperature": 0.2},
        ],
    },
    "scoring": {
        "provider": "gemini",
        "model_name": "gemma-4-26b-a4b-it",
        "temperature": 0.1,
        "fallbacks": [
            {"provider": "mistral", "model_name": "ministral-14b-2512", "temperature": 0.1},
            {"provider": "mistral", "model_name": "codestral-2508", "temperature": 0.1},
            {"provider": "gemini", "model_name": "gemini-3.5-flash-lite", "temperature": 0.1},
            {"provider": "groq", "model_name": "openai/gpt-oss-120b", "temperature": 0.1},
            {"provider": "gemini", "model_name": "gemma-4-31b-it", "temperature": 0.1},
            {"provider": "siliconflow", "model_name": "Qwen/Qwen2.5-7B-Instruct", "temperature": 0.1},
        ],
    },
    "summarization": {
        "provider": "gemini",
        "model_name": "gemini-3.5-flash-lite",
        "temperature": 0.2,
        "fallbacks": [
            {"provider": "gemini", "model_name": "gemma-4-31b-it", "temperature": 0.2},
            {"provider": "gemini", "model_name": "gemma-4-26b-a4b-it", "temperature": 0.2},
            {"provider": "groq", "model_name": "openai/gpt-oss-120b", "temperature": 0.2},
            {"provider": "groq", "model_name": "openai/gpt-oss-20b", "temperature": 0.2},
            {"provider": "aihubmix", "model_name": "coding-glm-5.3-flash-free", "temperature": 0.2},
            {"provider": "siliconflow", "model_name": "THUDM/glm-4-9b-chat", "temperature": 0.2},
            {"provider": "mistral", "model_name": "ministral-14b-2512", "temperature": 0.2},
            {"provider": "mistral", "model_name": "codestral-2508", "temperature": 0.2},
        ],
    },
    "visuals": {
        "provider": "gemini",
        "model_name": "gemini-3.1-flash-lite",
        "temperature": 0.2,
        "fallbacks": [
            {"provider": "gemini", "model_name": "gemma-4-26b-a4b-it", "temperature": 0.2},
            {"provider": "mistral", "model_name": "codestral-2508", "temperature": 0.2},
            {"provider": "groq", "model_name": "openai/gpt-oss-20b", "temperature": 0.2},
            {"provider": "mistral", "model_name": "ministral-8b-2512", "temperature": 0.2},
            {"provider": "cloudflare", "model_name": "@cf/meta/llama-3.2-3b-instruct", "temperature": 0.2},
            {"provider": "gemini", "model_name": "gemini-3.5-flash-lite", "temperature": 0.2},
        ],
    },
    "situation_summary": {
        "provider": "gemini",
        "model_name": "gemini-3.5-flash-lite",
        "temperature": 0.2,
        "fallbacks": [
            {"provider": "mistral", "model_name": "ministral-14b-2512", "temperature": 0.2},
            {"provider": "groq", "model_name": "openai/gpt-oss-120b", "temperature": 0.2},
            {"provider": "aihubmix", "model_name": "coding-glm-5.3-free", "temperature": 0.2},
            {"provider": "mistral", "model_name": "codestral-2508", "temperature": 0.2},
            {"provider": "gemini", "model_name": "gemma-4-31b-it", "temperature": 0.2},
        ],
    },
}


def _normalize_stage_dict(raw: Dict[str, Any]) -> Dict[str, Any]:
    import copy
    out = copy.deepcopy(DEFAULT_AI_STAGES)
    # Sort so exact canonical keys in DEFAULT_AI_STAGES take precedence over aliased keys
    sorted_items = sorted(
        raw.items(),
        key=lambda item: 1 if item[0].lower().strip() in DEFAULT_AI_STAGES else 0,
    )
    for k, v in sorted_items:
        if k.startswith("_"):
            continue
        norm_k = k.lower().strip()
        if "gate" in norm_k or "stage0" in norm_k:
            norm_k = "gatekeeper"
        elif "situation" in norm_k or "sitrep" in norm_k:
            norm_k = "situation_summary"
        elif "extract" in norm_k:
            norm_k = "extraction"
        elif "score" in norm_k:
            norm_k = "scoring"
        elif "summar" in norm_k:
            norm_k = "summarization"
        elif "vis" in norm_k:
            norm_k = "visuals"

        cfg = dict(v)
        if "model" in cfg and "model_name" not in cfg:
            cfg["model_name"] = cfg["model"]

        # 1. Non-token-heavy stages: AI Hub Mix must NOT be used (100 RPD global limit)
        if norm_k not in ("summarization", "situation_summary"):
            if cfg.get("provider") == "aihubmix":
                cfg["provider"] = "gemini" if norm_k == "scoring" else "groq"
                cfg["model_name"] = "gemini-3.5-flash-lite" if norm_k == "scoring" else "qwen/qwen3.8-27b"
            if "fallbacks" in cfg and isinstance(cfg["fallbacks"], list):
                cfg["fallbacks"] = [
                    (dict(fb, model_name=fb.get("model_name") or fb.get("model")) if isinstance(fb, dict) else fb)
                    for fb in cfg["fallbacks"]
                    if not (isinstance(fb, dict) and fb.get("provider") == "aihubmix")
                ]
            elif "fallbacks" not in cfg and norm_k in DEFAULT_AI_STAGES:
                cfg["fallbacks"] = copy.deepcopy(DEFAULT_AI_STAGES[norm_k].get("fallbacks", []))
        else:
            # 2. Token-heavy Stage 3 (Summarization) and Situational Summary:
            # Use single strongest model (coding-glm-5.3-flash-free or coding-glm-5.3-free).
            # No duplicate intra-provider AI Hub Mix fallbacks since rate limits are shared globally.
            default_stage_key = norm_k if norm_k in DEFAULT_AI_STAGES else "situation_summary"
            if cfg.get("provider") == "aihubmix":
                cfg["model_name"] = "coding-glm-5.3-flash-free" if "flash" in str(cfg.get("model_name") or cfg.get("model", "")) else "coding-glm-5.3-free"
                # Strip any aihubmix fallbacks if primary is already aihubmix
                if "fallbacks" in cfg and isinstance(cfg["fallbacks"], list):
                    cfg["fallbacks"] = [
                        (dict(fb, model_name=fb.get("model_name") or fb.get("model")) if isinstance(fb, dict) else fb)
                        for fb in cfg["fallbacks"]
                        if not (isinstance(fb, dict) and fb.get("provider") == "aihubmix")
                    ]
                elif "fallbacks" not in cfg:
                    cfg["fallbacks"] = copy.deepcopy(DEFAULT_AI_STAGES[default_stage_key]["fallbacks"])
            else:
                # Primary is not aihubmix: allow at most ONE aihubmix fallback using coding-glm-5.3-flash-free or coding-glm-5.3-free
                if "fallbacks" in cfg and isinstance(cfg["fallbacks"], list):
                    filtered_fb = []
                    has_aihubmix = False
                    for fb in cfg["fallbacks"]:
                        if isinstance(fb, dict):
                            fb_copy = dict(fb)
                            if "model" in fb_copy and "model_name" not in fb_copy:
                                fb_copy["model_name"] = fb_copy["model"]
                            if fb_copy.get("provider") == "aihubmix":
                                if not has_aihubmix:
                                    if not fb_copy.get("model_name"):
                                        fb_copy["model_name"] = "coding-glm-5.3-free" if norm_k == "situation_summary" else "coding-glm-5.3-flash-free"
                                    filtered_fb.append(fb_copy)
                                    has_aihubmix = True
                                # Drop subsequent aihubmix models: shared quota makes them redundant
                            else:
                                filtered_fb.append(fb_copy)
                        else:
                            filtered_fb.append(fb)
                    cfg["fallbacks"] = filtered_fb
                elif "fallbacks" not in cfg:
                    cfg["fallbacks"] = copy.deepcopy(DEFAULT_AI_STAGES[default_stage_key]["fallbacks"])

        out[norm_k] = cfg
    return out


@router.get("", dependencies=[Depends(require_api_key)])
async def get_system_settings(
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """Retrieve global system settings with masked API credentials."""
    res = await session.execute(
        select(SystemSettings).where(
            SystemSettings.key.in_([
                "api_keys",
                "notification_threshold",
                "polling_intervals",
                "max_lookback_hours",
            ])
        )
    )
    rows = {r.key: r.value_json for r in res.scalars().all()}

    raw_keys = rows.get("api_keys", {})
    app_settings = get_settings()
    env_keys = {
        "gemini_api_key": getattr(app_settings, "GEMINI_API_KEY", None) or os.environ.get("GEMINI_API_KEY"),
        "groq_api_key": getattr(app_settings, "GROQ_API_KEY", None) or os.environ.get("GROQ_API_KEY"),
        "aihubmix_api_key": getattr(app_settings, "AIHUBMIX_API_KEY", None) or os.environ.get("AIHUBMIX_API_KEY"),
        "mistral_api_key": getattr(app_settings, "MISTRAL_API_KEY", None) or os.environ.get("MISTRAL_API_KEY"),
        "cloudflare_api_token": getattr(app_settings, "CLOUDFLARE_API_TOKEN", None) or os.environ.get("CLOUDFLARE_API_TOKEN") or getattr(app_settings, "CLOUDFLARE_API_KEY", None) or os.environ.get("CLOUDFLARE_API_KEY"),
        "cloudflare_api_key": getattr(app_settings, "CLOUDFLARE_API_KEY", None) or os.environ.get("CLOUDFLARE_API_KEY") or getattr(app_settings, "CLOUDFLARE_API_TOKEN", None) or os.environ.get("CLOUDFLARE_API_TOKEN"),
        "cloudflare_account_id": getattr(app_settings, "CLOUDFLARE_ACCOUNT_ID", None) or os.environ.get("CLOUDFLARE_ACCOUNT_ID"),
        "zai_api_key": getattr(app_settings, "ZAI_API_KEY", None) or os.environ.get("ZAI_API_KEY"),
        "siliconflow_api_key": getattr(app_settings, "SILICONFLOW_API_KEY", None) or os.environ.get("SILICONFLOW_API_KEY"),
        "telegram_bot_token": getattr(app_settings, "TELEGRAM_BOT_TOKEN", None) or os.environ.get("TELEGRAM_BOT_TOKEN"),
        "telegram_chat_id": getattr(app_settings, "TELEGRAM_CHAT_ID", None) or os.environ.get("TELEGRAM_CHAT_ID"),
        "telegram_api_id": os.environ.get("TELEGRAM_API_ID"),
        "telegram_api_hash": os.environ.get("TELEGRAM_API_HASH"),
        "newsapi_key": os.environ.get("NEWSAPI_KEY"),
        "reddit_client_id": os.environ.get("REDDIT_CLIENT_ID"),
        "reddit_client_secret": os.environ.get("REDDIT_CLIENT_SECRET"),
        "hf_embedding_url": getattr(app_settings, "HF_EMBEDDING_URL", None) or os.environ.get("HF_EMBEDDING_URL"),
        "hf_api_key": getattr(app_settings, "HF_API_KEY", None) or os.environ.get("HF_API_KEY"),
    }
    effective_keys = {k: "" for k in env_keys.keys()}
    for k, env_v in env_keys.items():
        if env_v:
            effective_keys[k] = env_v
    if isinstance(raw_keys, dict):
        for k, db_v in raw_keys.items():
            if db_v or k not in effective_keys:
                effective_keys[k] = db_v

    masked_keys = {}
    for k, v in effective_keys.items():
        if not v:
            masked_keys[k] = ""
        elif k == "hf_embedding_url":
            masked_keys[k] = str(v)
        elif k in ("groq_api_key", "mistral_api_key") and ("," in str(v) or "\n" in str(v)):
            sub_keys = [sk.strip() for sk in str(v).replace("\r", "\n").replace(",", "\n").split("\n") if sk.strip()]
            masked_subs = []
            for sk in sub_keys:
                if len(sk) > 4:
                    masked_subs.append(f"***{sk[-4:]}")
                else:
                    masked_subs.append("***")
            masked_keys[k] = ", ".join(masked_subs)
        elif len(str(v)) > 4:
            masked_keys[k] = f"***{str(v)[-4:]}"
        else:
            masked_keys[k] = "***"

    default_lookbacks = {
        "rss": 1.0,
        "telegram": 1.0,
        "newsapi": 1.0,
        "reddit": 1.0,
        "ais": 1.0,
    }
    configured_lookbacks = dict(default_lookbacks)
    if "max_lookback_hours" in rows and isinstance(rows["max_lookback_hours"], dict):
        configured_lookbacks.update({k: float(v) for k, v in rows["max_lookback_hours"].items()})

    return {
        "api_keys": masked_keys,
        "notification_threshold": rows.get("notification_threshold", {}).get("threshold", 7),
        "polling_intervals": rows.get("polling_intervals", {}),
        "max_lookback_hours": configured_lookbacks,
        "gatekeeper_stats": get_pipeline_orchestrator().get_gatekeeper_stats(),
    }


@router.get("/gatekeeper-stats", dependencies=[Depends(require_api_key)])
async def get_gatekeeper_stats() -> Dict[str, Any]:
    """Return live Pre-Pipeline Stage 0 High-RPD Gatekeeper telemetry counters."""
    return get_pipeline_orchestrator().get_gatekeeper_stats()


@router.get("/groq-pool", dependencies=[Depends(require_api_key)])
async def get_groq_pool_status() -> Dict[str, Any]:
    """Return live Groq API key rotation pool telemetry, status, and health."""
    from backend.app.pipeline.providers.factory import get_shared_groq_pool
    return get_shared_groq_pool().get_telemetry()


@router.get("/mistral-pool", dependencies=[Depends(require_api_key)])
async def get_mistral_pool_status() -> Dict[str, Any]:
    """Return live Mistral AI key rotation pool telemetry, status, and health."""
    from backend.app.pipeline.providers.factory import get_shared_mistral_pool
    return get_shared_mistral_pool().get_telemetry()


@router.get("/provider-usage", dependencies=[Depends(require_api_key)])
async def get_provider_usage(
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """Return live perceived usage of each key and model across all providers."""
    from backend.app.pipeline.usage_tracker import get_usage_tracker
    orch = get_pipeline_orchestrator()
    ai_settings = {
        s: orch.get_stage_config(s).model_dump()
        for s in ("gatekeeper", "extraction", "scoring", "summarization", "visuals", "situation_summary")
    }

    res = await session.execute(
        select(SystemSettings).where(SystemSettings.key.in_(["api_keys", "ai_pipeline"]))
    )
    db_rows = {row.key: row.value_json for row in res.scalars().all()}
    db_keys = db_rows.get("api_keys", {})

    return get_usage_tracker().get_telemetry(ai_settings=ai_settings, db_keys=db_keys)


@router.put("", dependencies=[Depends(require_api_key)])
async def update_system_settings(
    payload: Dict[str, Any],
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """Update system settings (API keys, threshold, intervals, lookback) and notify workers."""
    if "api_keys" in payload:
        from sqlalchemy.orm.attributes import flag_modified
        res = await session.execute(
            select(SystemSettings).where(SystemSettings.key == "api_keys")
        )
        existing = res.scalars().first()
        current_keys = dict(existing.value_json) if existing and existing.value_json else {}
        env_var_map = {
            "gemini_api_key": "GEMINI_API_KEY",
            "groq_api_key": "GROQ_API_KEY",
            "aihubmix_api_key": "AIHUBMIX_API_KEY",
            "mistral_api_key": "MISTRAL_API_KEY",
            "cloudflare_api_token": "CLOUDFLARE_API_TOKEN",
            "cloudflare_api_key": "CLOUDFLARE_API_KEY",
            "cloudflare_account_id": "CLOUDFLARE_ACCOUNT_ID",
            "zai_api_key": "ZAI_API_KEY",
            "siliconflow_api_key": "SILICONFLOW_API_KEY",
            "hf_embedding_url": "HF_EMBEDDING_URL",
            "hf_api_key": "HF_API_KEY",
            "telegram_bot_token": "TELEGRAM_BOT_TOKEN",
            "telegram_chat_id": "TELEGRAM_CHAT_ID",
            "telegram_api_id": "TELEGRAM_API_ID",
            "telegram_api_hash": "TELEGRAM_API_HASH",
            "newsapi_key": "NEWSAPI_KEY",
            "reddit_client_id": "REDDIT_CLIENT_ID",
            "reddit_client_secret": "REDDIT_CLIENT_SECRET",
        }
        for k, v in payload["api_keys"].items():
            if k == "hf_embedding_url" and v == "":
                current_keys[k] = ""
                os.environ.pop("HF_EMBEDDING_URL", None)
            elif v and not str(v).startswith("***"):
                clean_v = str(v).strip()
                current_keys[k] = clean_v
                if k in env_var_map:
                    os.environ[env_var_map[k]] = clean_v
                if k in ("cloudflare_api_token", "cloudflare_api_key"):
                    os.environ["CLOUDFLARE_API_TOKEN"] = clean_v
                    os.environ["CLOUDFLARE_API_KEY"] = clean_v
                elif k == "groq_api_key":
                    os.environ["GROQ_API_KEYS"] = clean_v
                    from backend.app.pipeline.providers.factory import get_shared_groq_pool
                    from backend.app.pipeline.providers.key_pool import parse_key_list
                    get_shared_groq_pool().set_keys(parse_key_list(clean_v))
                elif k == "mistral_api_key":
                    os.environ["MISTRAL_API_KEYS"] = clean_v
                    from backend.app.pipeline.providers.factory import get_shared_mistral_pool
                    from backend.app.pipeline.providers.key_pool import parse_key_list
                    get_shared_mistral_pool().set_keys(parse_key_list(clean_v))
                elif k == "telegram_bot_token":
                    from backend.app.services.telegram_bot import get_telegram_bot
                    get_telegram_bot().bot_token = clean_v
                elif k == "telegram_chat_id":
                    from backend.app.services.telegram_bot import get_telegram_bot
                    get_telegram_bot().chat_id = clean_v
        if existing:
            existing.value_json = current_keys
            flag_modified(existing, "value_json")
        else:
            session.add(SystemSettings(key="api_keys", value_json=current_keys))

    if "notification_threshold" in payload:
        res = await session.execute(
            select(SystemSettings).where(SystemSettings.key == "notification_threshold")
        )
        existing = res.scalars().first()
        thresh_int = int(payload["notification_threshold"])
        val = {"threshold": thresh_int}
        if existing:
            existing.value_json = val
        else:
            session.add(SystemSettings(key="notification_threshold", value_json=val))
        # Also sync active theme's alert_threshold so the pipeline orchestrator uses it
        from backend.app.db.models import Theme
        active_themes = (
            await session.execute(select(Theme).where(Theme.is_active == True))
        ).scalars().all()
        for t in active_themes:
            t.alert_threshold = thresh_int

    if "polling_intervals" in payload:
        res = await session.execute(
            select(SystemSettings).where(SystemSettings.key == "polling_intervals")
        )
        existing = res.scalars().first()
        current_ints = existing.value_json if existing else {}
        current_ints.update(payload["polling_intervals"])
        if existing:
            existing.value_json = current_ints
        else:
            session.add(SystemSettings(key="polling_intervals", value_json=current_ints))

    if "max_lookback_hours" in payload:
        res = await session.execute(
            select(SystemSettings).where(SystemSettings.key == "max_lookback_hours")
        )
        existing = res.scalars().first()
        current_lb = existing.value_json if existing else {}
        current_lb.update({k: float(v) for k, v in payload["max_lookback_hours"].items()})
        if existing:
            existing.value_json = current_lb
        else:
            session.add(SystemSettings(key="max_lookback_hours", value_json=current_lb))

    await session.commit()

    # Publish reload notification to Redis bus
    bus = get_redis_bus()
    await bus.publish(CHANNEL_CONFIG_RELOAD, {"action": "reload_all"})

    return {"status": "updated", "message": "Settings updated successfully"}


@router.get("/ai-pipeline", dependencies=[Depends(require_api_key)])
async def get_ai_pipeline_stages(
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """Get active AI pipeline configuration for all 4 stages."""
    res = await session.execute(
        select(SystemSettings).where(SystemSettings.key == "ai_pipeline_stages")
    )
    setting = res.scalars().first()
    if setting and setting.value_json:
        clean = {k: v for k, v in setting.value_json.items() if not k.startswith("_")}
        return _normalize_stage_dict(clean)
    return DEFAULT_AI_STAGES


@router.put("/ai-pipeline", dependencies=[Depends(require_api_key)])
async def update_ai_pipeline_stages(
    payload: Dict[str, Any],
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """Update AI pipeline stage models/providers and broadcast hot-reload event."""
    res = await session.execute(
        select(SystemSettings).where(SystemSettings.key == "ai_pipeline_stages")
    )
    setting = res.scalars().first()
    import copy
    from sqlalchemy.orm.attributes import flag_modified

    current = copy.deepcopy(setting.value_json) if setting and setting.value_json else copy.deepcopy(DEFAULT_AI_STAGES)

    for stage_id, cfg in payload.items():
        if stage_id.startswith("_"):
            continue
        norm_id = stage_id.lower().strip()
        if "gate" in norm_id or "stage0" in norm_id:
            norm_id = "gatekeeper"
        elif "situation" in norm_id or "sitrep" in norm_id:
            norm_id = "situation_summary"
        elif "extract" in norm_id:
            norm_id = "extraction"
        elif "score" in norm_id:
            norm_id = "scoring"
        elif "summar" in norm_id:
            norm_id = "summarization"
        elif "vis" in norm_id:
            norm_id = "visuals"

        if norm_id not in current:
            current[norm_id] = {}
        current[norm_id].update(cfg)
        if "model" in cfg and "model_name" not in cfg:
            current[norm_id]["model_name"] = cfg["model"]

    normalized = _normalize_stage_dict(current)
    persisted = copy.deepcopy(normalized)
    persisted["_customized"] = True

    if setting:
        setting.value_json = persisted
        flag_modified(setting, "value_json")
    else:
        session.add(SystemSettings(key="ai_pipeline_stages", value_json=persisted))

    await session.commit()

    # Hot-reload in orchestrator singleton immediately
    orchestrator = get_pipeline_orchestrator()
    for s_id, s_cfg in normalized.items():
        from backend.app.pipeline.models import AIStageConfig, AIFallbackConfig
        raw_fb = s_cfg.get("fallbacks", [])
        parsed_fb = []
        for fb in raw_fb:
            if isinstance(fb, dict):
                parsed_fb.append(AIFallbackConfig(
                    provider=fb.get("provider", "gemini"),
                    model_name=fb.get("model_name") or fb.get("model", "gemini-3.1-flash-lite"),
                    temperature=fb.get("temperature"),
                    api_key_override=fb.get("api_key_override"),
                ))
            elif isinstance(fb, AIFallbackConfig):
                parsed_fb.append(fb)

        orchestrator.set_stage_config(
            s_id,
            AIStageConfig(
                stage_id=s_id,  # type: ignore
                provider=s_cfg.get("provider", "gemini"),
                model_name=s_cfg.get("model_name") or s_cfg.get("model", "gemini-3.5-flash-lite"),
                temperature=s_cfg.get("temperature", 0.2),
                api_key_override=s_cfg.get("api_key_override"),
                fallbacks=parsed_fb,
            ),
        )
    orchestrator.clear_cooldowns()

    # Publish reload event to Redis bus
    bus = get_redis_bus()
    await bus.publish(CHANNEL_CONFIG_RELOAD, {"action": "ai_config_update"})

    return normalized


@router.post("/ai-pipeline/reset", dependencies=[Depends(require_api_key)])
async def reset_ai_pipeline_stages(
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """Reset AI pipeline stages back to code defaults (DEFAULT_AI_STAGES)."""
    import copy
    from sqlalchemy.orm.attributes import flag_modified

    res = await session.execute(
        select(SystemSettings).where(SystemSettings.key == "ai_pipeline_stages")
    )
    setting = res.scalars().first()
    defaults = copy.deepcopy(DEFAULT_AI_STAGES)

    if setting:
        setting.value_json = copy.deepcopy(defaults)
        flag_modified(setting, "value_json")
    else:
        session.add(SystemSettings(key="ai_pipeline_stages", value_json=copy.deepcopy(defaults)))

    await session.commit()

    orchestrator = get_pipeline_orchestrator()
    for s_id, s_cfg in defaults.items():
        from backend.app.pipeline.models import AIStageConfig, AIFallbackConfig
        raw_fb = s_cfg.get("fallbacks", [])
        parsed_fb = [
            AIFallbackConfig(
                provider=fb.get("provider", "gemini"),
                model_name=fb.get("model_name") or fb.get("model", "gemini-3.1-flash-lite"),
                temperature=fb.get("temperature"),
                api_key_override=fb.get("api_key_override"),
            )
            for fb in raw_fb
            if isinstance(fb, dict)
        ]
        orchestrator.set_stage_config(
            s_id,
            AIStageConfig(
                stage_id=s_id,  # type: ignore
                provider=s_cfg.get("provider", "gemini"),
                model_name=s_cfg.get("model_name") or s_cfg.get("model", "gemini-3.5-flash-lite"),
                temperature=s_cfg.get("temperature", 0.2),
                api_key_override=s_cfg.get("api_key_override"),
                fallbacks=parsed_fb,
            ),
        )
    orchestrator.clear_cooldowns()

    bus = get_redis_bus()
    await bus.publish(CHANNEL_CONFIG_RELOAD, {"action": "ai_config_reset"})

    return defaults


# ---------------------------------------------------------------------------
# Server Logs & Runtime Diagnostics Endpoints
# ---------------------------------------------------------------------------

@router.get("/logs", dependencies=[Depends(require_api_key)])
async def get_server_logs(
    limit: int = Query(100, ge=1, le=1000, description="Maximum number of log entries to return"),
    level: Optional[str] = Query(None, description="Filter by level (DEBUG, INFO, WARNING, ERROR, CRITICAL) or comma-separated list"),
    logger: Optional[str] = Query(None, description="Filter by logger name prefix/substring"),
    q: Optional[str] = Query(None, description="Search query string in log message or trace ID"),
    since_id: Optional[int] = Query(None, description="Return only logs with id strictly greater than since_id"),
) -> Dict[str, Any]:
    """Retrieve filtered server runtime logs with statistical distribution and telemetry."""
    from backend.app.services.server_logs import get_server_log_buffer

    buf = get_server_log_buffer()
    logs = buf.get_logs(
        level=level,
        logger_name=logger,
        search=q,
        limit=limit,
        since_id=since_id,
        reverse=True,
    )
    stats = buf.get_stats()

    return {
        "logs": logs,
        "total": len(logs),
        "returned": len(logs),
        "stats": stats,
    }


@router.get("/logs/stats", dependencies=[Depends(require_api_key)])
async def get_server_logs_stats() -> Dict[str, Any]:
    """Retrieve statistical distribution, memory capacity, and health metrics for server logs."""
    from backend.app.services.server_logs import get_server_log_buffer

    buf = get_server_log_buffer()
    return buf.get_stats()


@router.delete("/logs", dependencies=[Depends(require_api_key)])
async def clear_server_logs() -> Dict[str, Any]:
    """Clear all buffered server logs from the in-memory circular ring buffer."""
    from backend.app.services.server_logs import get_server_log_buffer

    buf = get_server_log_buffer()
    buf.clear()
    logger.info("Server log buffer cleared via settings API.")
    return {
        "status": "cleared",
        "message": "Server log buffer has been cleared successfully",
    }


@router.get("/logs/export", dependencies=[Depends(require_api_key)])
async def export_server_logs(
    format: str = Query("text", pattern="^(text|json)$", description="Export format: text or json"),
    limit: int = Query(1000, ge=1, le=2000, description="Maximum number of lines to export"),
) -> Response:
    """Export runtime server logs as a downloadable file (plaintext log or structured JSON)."""
    import json
    from backend.app.services.server_logs import get_server_log_buffer

    buf = get_server_log_buffer()
    now_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    if format == "json":
        logs = buf.get_logs(limit=limit, reverse=False)
        content = json.dumps({"exported_at": datetime.now(timezone.utc).isoformat(), "logs": logs}, indent=2, default=str)
        filename = f"osint_server_logs_{now_str}.json"
        return Response(
            content=content,
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    else:
        text_content = buf.export_text(limit=limit)
        filename = f"osint_server_logs_{now_str}.log"
        return PlainTextResponse(
            content=text_content,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )


@router.post("/logs/test", dependencies=[Depends(require_api_key)])
async def emit_test_server_log(
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    """Emit a test server log entry to verify log capture and UI connectivity."""
    from backend.app.services.server_logs import get_server_log_buffer

    test_level = str(payload.get("level", "INFO")).upper()
    test_msg = str(payload.get("message", "Operator test log emitted from settings panel."))

    buf = get_server_log_buffer()
    entry = buf.append_manual(test_level, "osint.test", test_msg)
    return {
        "status": "emitted",
        "message": "Test log entry created successfully",
        "entry": entry,
    }


# =========================================================================
# World Theater & Geopolitical Context Endpoints
# =========================================================================

@router.get("/world-theater", dependencies=[Depends(require_api_key)])
async def get_world_theater_context() -> Dict[str, Any]:
    """Retrieve current World Theater context, leadership roster, active flashpoints, and sync status."""
    from backend.app.services.world_theater_service import get_world_theater_service
    svc = get_world_theater_service()
    return svc.get_context()


@router.put("/world-theater", dependencies=[Depends(require_api_key)])
async def update_world_theater_context(
    payload: Dict[str, Any],
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """Update custom World Theater context facts, leaders, or operational rules."""
    from backend.app.services.world_theater_service import get_world_theater_service
    svc = get_world_theater_service(session_factory=lambda: session)
    
    current = svc.get_context()
    current.update(payload)
    current["last_synced_at"] = datetime.now(timezone.utc).isoformat()
    current["sync_source"] = "operator_manual_update"
    svc.set_context(current)

    # Persist to DB
    res = await session.execute(
        select(SystemSettings).where(SystemSettings.key == "world_theater_context")
    )
    existing = res.scalars().first()
    if existing:
        existing.value_json = current
        flag_modified(existing, "value_json")
    else:
        session.add(SystemSettings(key="world_theater_context", value_json=current))
    await session.commit()

    # Broadcast reload
    try:
        bus = get_redis_bus()
        await bus.publish(CHANNEL_CONFIG_RELOAD, {"type": "world_theater_updated"})
    except Exception as exc:
        logger.debug(f"Could not broadcast world theater reload: {exc}")

    return {
        "status": "updated",
        "message": "World Theater context updated and broadcast to all workers",
        "context": current,
    }


@router.post("/world-theater/refresh", dependencies=[Depends(require_api_key)])
async def refresh_world_theater_from_api(
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """Force an active resynchronization of World Theater leadership from live Wikidata APIs."""
    from backend.app.services.world_theater_service import get_world_theater_service
    svc = get_world_theater_service(session_factory=lambda: session)
    updated = await svc.sync_from_live_apis(timeout=8.0)
    return {
        "status": "synchronized",
        "message": "Synchronized World Theater leadership from live external APIs",
        "live_api_status": updated.get("live_api_status"),
        "sync_source": updated.get("sync_source"),
        "last_synced_at": updated.get("last_synced_at"),
        "context": updated,
    }

