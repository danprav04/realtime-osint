"""
backend/app/api/settings.py - System Settings & AI Pipeline Configuration Endpoints
"""

import logging
from typing import Any, Dict, Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.auth import require_api_key
from backend.app.db.models import SystemSettings
from backend.app.db.session import get_db_session
from backend.app.pipeline.orchestrator import get_pipeline_orchestrator
from backend.app.services.redis_bus import CHANNEL_CONFIG_RELOAD, get_redis_bus

logger = logging.getLogger("osint.api.settings")
router = APIRouter(prefix="/api/settings", tags=["Settings"])

DEFAULT_AI_STAGES = {
    "extraction": {
        "provider": "groq",
        "model_name": "qwen/qwen3.8-27b",
        "temperature": 0.2,
        "fallbacks": [
            {"provider": "gemini", "model_name": "gemini-3.5-flash-lite", "temperature": 0.2},
            {"provider": "gemini", "model_name": "gemini-3.1-flash-lite", "temperature": 0.2},
            {"provider": "groq", "model_name": "openai/gpt-oss-120b", "temperature": 0.2},
        ],
    },
    "scoring": {
        "provider": "gemini",
        "model_name": "gemini-3.5-flash-lite",
        "temperature": 0.1,
        "fallbacks": [
            {"provider": "gemini", "model_name": "gemini-3.1-flash-lite", "temperature": 0.1},
            {"provider": "groq", "model_name": "openai/gpt-oss-120b", "temperature": 0.1},
            {"provider": "groq", "model_name": "qwen/qwen3.8-27b", "temperature": 0.1},
        ],
    },
    "summarization": {
        "provider": "groq",
        "model_name": "openai/gpt-oss-120b",
        "temperature": 0.2,
        "fallbacks": [
            {"provider": "groq", "model_name": "qwen/qwen3.8-27b", "temperature": 0.2},
            {"provider": "gemini", "model_name": "gemini-3.5-flash-lite", "temperature": 0.2},
            {"provider": "gemini", "model_name": "gemini-3.1-flash-lite", "temperature": 0.2},
            {"provider": "groq", "model_name": "openai/gpt-oss-20b", "temperature": 0.2},
        ],
    },
    "visuals": {
        "provider": "groq",
        "model_name": "openai/gpt-oss-20b",
        "temperature": 0.2,
        "fallbacks": [
            {"provider": "groq", "model_name": "qwen/qwen3.8-27b", "temperature": 0.2},
            {"provider": "gemini", "model_name": "gemini-3.5-flash-lite", "temperature": 0.2},
            {"provider": "gemini", "model_name": "gemini-3.1-flash-lite", "temperature": 0.2},
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
        norm_k = k.lower().strip()
        if "extract" in norm_k:
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
        if "fallbacks" in cfg and isinstance(cfg["fallbacks"], list):
            norm_fb = []
            for fb in cfg["fallbacks"]:
                if isinstance(fb, dict):
                    fb_copy = dict(fb)
                    if "model" in fb_copy and "model_name" not in fb_copy:
                        fb_copy["model_name"] = fb_copy["model"]
                    norm_fb.append(fb_copy)
                else:
                    norm_fb.append(fb)
            cfg["fallbacks"] = norm_fb
        elif "fallbacks" not in cfg and norm_k in DEFAULT_AI_STAGES:
            cfg["fallbacks"] = copy.deepcopy(DEFAULT_AI_STAGES[norm_k].get("fallbacks", []))
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
                "pizza_meter_enabled",
            ])
        )
    )
    rows = {r.key: r.value_json for r in res.scalars().all()}

    raw_keys = rows.get("api_keys", {})
    masked_keys = {}
    for k, v in raw_keys.items():
        if v and len(str(v)) > 4:
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

    pizza_meter_enabled = True
    if "pizza_meter_enabled" in rows:
        val = rows["pizza_meter_enabled"]
        if isinstance(val, dict):
            pizza_meter_enabled = bool(val.get("enabled", True))
        elif isinstance(val, bool):
            pizza_meter_enabled = val

    return {
        "api_keys": masked_keys,
        "notification_threshold": rows.get("notification_threshold", {}).get("threshold", 7),
        "polling_intervals": rows.get("polling_intervals", {}),
        "max_lookback_hours": configured_lookbacks,
        "pizza_meter_enabled": pizza_meter_enabled,
    }


@router.put("", dependencies=[Depends(require_api_key)])
async def update_system_settings(
    payload: Dict[str, Any],
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """Update system settings (API keys, threshold, intervals, lookback, pizza meter) and notify workers."""
    if "api_keys" in payload:
        res = await session.execute(
            select(SystemSettings).where(SystemSettings.key == "api_keys")
        )
        existing = res.scalars().first()
        current_keys = existing.value_json if existing else {}
        for k, v in payload["api_keys"].items():
            if v and not str(v).startswith("***"):
                current_keys[k] = v
        if existing:
            existing.value_json = current_keys
        else:
            session.add(SystemSettings(key="api_keys", value_json=current_keys))

    if "notification_threshold" in payload:
        res = await session.execute(
            select(SystemSettings).where(SystemSettings.key == "notification_threshold")
        )
        existing = res.scalars().first()
        val = {"threshold": int(payload["notification_threshold"])}
        if existing:
            existing.value_json = val
        else:
            session.add(SystemSettings(key="notification_threshold", value_json=val))

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

    if "pizza_meter_enabled" in payload:
        res = await session.execute(
            select(SystemSettings).where(SystemSettings.key == "pizza_meter_enabled")
        )
        existing = res.scalars().first()
        val = {"enabled": bool(payload["pizza_meter_enabled"])}
        if existing:
            existing.value_json = val
        else:
            session.add(SystemSettings(key="pizza_meter_enabled", value_json=val))

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
        return _normalize_stage_dict(setting.value_json)
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
        norm_id = stage_id.lower().strip()
        if "extract" in norm_id:
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

    if setting:
        setting.value_json = copy.deepcopy(normalized)
        flag_modified(setting, "value_json")
    else:
        session.add(SystemSettings(key="ai_pipeline_stages", value_json=copy.deepcopy(normalized)))

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
