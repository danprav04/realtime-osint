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
    },
    "scoring": {
        "provider": "gemini",
        "model_name": "gemini-3.5-flash-lite",
        "temperature": 0.1,
    },
    "summarization": {
        "provider": "groq",
        "model_name": "openai/gpt-oss-120b",
        "temperature": 0.2,
    },
    "visuals": {
        "provider": "groq",
        "model_name": "openai/gpt-oss-20b",
        "temperature": 0.2,
    },
}


def _normalize_stage_dict(raw: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(DEFAULT_AI_STAGES)
    for k, v in raw.items():
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
        out[norm_k] = cfg
    return out


@router.get("", dependencies=[Depends(require_api_key)])
async def get_system_settings(
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """Retrieve global system settings with masked API credentials."""
    res = await session.execute(
        select(SystemSettings).where(
            SystemSettings.key.in_(["api_keys", "notification_threshold", "polling_intervals"])
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

    return {
        "api_keys": masked_keys,
        "notification_threshold": rows.get("notification_threshold", {}).get("threshold", 7),
        "polling_intervals": rows.get("polling_intervals", {}),
    }


@router.put("", dependencies=[Depends(require_api_key)])
async def update_system_settings(
    payload: Dict[str, Any],
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """Update system settings (API keys, threshold, intervals) and notify workers."""
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
    current = setting.value_json if setting else dict(DEFAULT_AI_STAGES)

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
        setting.value_json = normalized
    else:
        session.add(SystemSettings(key="ai_pipeline_stages", value_json=normalized))

    await session.commit()

    # Hot-reload in orchestrator singleton immediately
    orchestrator = get_pipeline_orchestrator()
    for s_id, s_cfg in normalized.items():
        from backend.app.pipeline.models import AIStageConfig
        orchestrator.set_stage_config(
            s_id,
            AIStageConfig(
                stage_id=s_id,  # type: ignore
                provider=s_cfg.get("provider", "gemini"),
                model_name=s_cfg.get("model_name") or s_cfg.get("model", "gemini-3.5-flash-lite"),
                temperature=s_cfg.get("temperature", 0.2),
                api_key_override=s_cfg.get("api_key_override"),
            ),
        )

    # Publish reload event to Redis bus
    bus = get_redis_bus()
    await bus.publish(CHANNEL_CONFIG_RELOAD, {"action": "ai_config_update"})

    return normalized
