"""
Sources REST API endpoints.
Provides source ingestion configuration, CRUD operations, enable/disable toggling,
and manual polling triggers with automatic hot-reload coordination.
"""

from datetime import datetime, timezone
import logging
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from backend.app.auth import require_api_key
from backend.app.db.models import Source, SystemSettings, Theme, utc_now
from backend.app.db.session import get_db_session
from backend.app.services.redis_bus import (
    CHANNEL_CONFIG_RELOAD,
    get_redis_bus,
)

logger = logging.getLogger("osint.api.sources")

router = APIRouter(prefix="/api/sources", tags=["Sources"])


def serialize_source(source: Source) -> Dict[str, Any]:
    """Serialize a Source DB model into expected API schema."""
    cfg = dict(source.config_json) if isinstance(source.config_json, dict) else {}
    target = cfg.get("target") or cfg.get("url")
    if not target and cfg.get("channels") and isinstance(cfg["channels"], list):
        target = cfg["channels"][0]
    elif not target and cfg.get("subreddits") and isinstance(cfg["subreddits"], list):
        target = cfg["subreddits"][0]

    status_str = cfg.get("status")
    if not status_str:
        status_str = "active" if source.is_enabled else "paused"

    theme_slug = None
    if "theme" in source.__dict__ and source.theme is not None and getattr(source.theme, "slug", None):
        theme_slug = source.theme.slug
    if not theme_slug:
        theme_slug = str(source.theme_id)

    max_lookback_hours = float(cfg.get("max_lookback_hours", 1.0))

    return {
        "id": str(source.id),
        "theme_id": theme_slug,
        "name": source.name,
        "source_type": source.source_type,
        "target": target or "",
        "config_json": cfg,
        "enabled": source.is_enabled,
        "is_enabled": source.is_enabled,
        "interval_seconds": source.polling_interval_sec,
        "polling_interval_sec": source.polling_interval_sec,
        "max_lookback_hours": max_lookback_hours,
        "status": status_str,
        "error_message": cfg.get("error_message"),
        "last_polled_at": source.last_polled_at.isoformat() if source.last_polled_at else None,
        "created_at": source.created_at.isoformat() if source.created_at else None,
    }


async def broadcast_source_reload(source_id: str) -> None:
    """Publish source configuration update to Redis Pub/Sub."""
    try:
        bus = get_redis_bus()
        payload = {
            "action": "source_update",
            "source_id": str(source_id),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        await bus.publish(CHANNEL_CONFIG_RELOAD, payload)
    except Exception as exc:
        logger.warning(f"Failed to publish source reload message: {exc}")

    try:
        from backend.app.plugins.supervisor import get_ingestion_supervisor
        supervisor = get_ingestion_supervisor()
        await supervisor.reconcile_sources()
    except Exception as exc:
        logger.debug(f"Supervisor reconcile notification: {exc}")


async def find_source(session: AsyncSession, source_id: str) -> Optional[Source]:
    """Retrieve source by numeric ID or string identifier."""
    if source_id.isdigit():
        stmt = select(Source).where(Source.id == int(source_id))
        return (await session.execute(stmt)).scalars().first()
    
    # Try finding by name if not numeric
    stmt = select(Source).where(Source.name == source_id)
    return (await session.execute(stmt)).scalars().first()


@router.get("", dependencies=[Depends(require_api_key)])
async def list_sources(
    theme_id: Optional[str] = Query(None, description="Filter sources by theme slug or numeric ID"),
    source_type: Optional[str] = Query(None, description="Filter sources by plugin type"),
    session: AsyncSession = Depends(get_db_session),
) -> List[Dict[str, Any]]:
    """List all configured ingestion sources with optional filtering."""
    query = select(Source).order_by(Source.id.asc())

    if theme_id:
        if theme_id.isdigit():
            query = query.where(Source.theme_id == int(theme_id))
        else:
            # Resolve theme slug
            theme_stmt = select(Theme).where(
                (Theme.slug == theme_id) | (Theme.name.ilike(theme_id.replace("-", " ")))
            )
            theme_obj = (await session.execute(theme_stmt)).scalars().first()
            if theme_obj:
                query = query.where(Source.theme_id == theme_obj.id)
            else:
                return []

    if source_type:
        # Support aliases like 'ais' -> 'maritime_ais' or direct match
        if source_type == "ais":
            query = query.where((Source.source_type == "ais") | (Source.source_type == "maritime_ais"))
        else:
            query = query.where(Source.source_type == source_type)

    sources = (await session.execute(query)).scalars().all()
    return [serialize_source(s) for s in sources]


@router.post("", status_code=status.HTTP_200_OK, dependencies=[Depends(require_api_key)])
async def create_source(
    payload: Dict[str, Any],
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """Add a new intelligence data source."""
    name = payload.get("name", "New Source")
    source_type = payload.get("source_type", "rss")
    
    # Resolve theme_id
    raw_theme_id = payload.get("theme_id")
    theme_id_val = None
    if raw_theme_id:
        if str(raw_theme_id).isdigit():
            theme_id_val = int(raw_theme_id)
        else:
            t_stmt = select(Theme).where(
                (Theme.slug == str(raw_theme_id)) | (Theme.name.ilike(str(raw_theme_id).replace("-", " ")))
            )
            t_obj = (await session.execute(t_stmt)).scalars().first()
            if t_obj:
                theme_id_val = t_obj.id

    if theme_id_val is None:
        # Fall back to active theme
        active_t = (await session.execute(select(Theme).where(Theme.is_active == True).limit(1))).scalars().first()
        theme_id_val = active_t.id if active_t else 1

    config = dict(payload.get("config_json", {})) if isinstance(payload.get("config_json"), dict) else {}
    target = payload.get("target")
    if target:
        config["target"] = target
        if source_type == "rss" and "url" not in config:
            config["url"] = target
        elif source_type == "telegram" and "channels" not in config:
            config["channels"] = [target]
        elif source_type == "reddit" and "subreddits" not in config:
            config["subreddits"] = [target]

    if "status" in payload:
        config["status"] = payload["status"]
    if "error_message" in payload:
        config["error_message"] = payload["error_message"]

    raw_lb = payload.get("max_lookback_hours", config.get("max_lookback_hours"))
    if raw_lb is not None:
        config["max_lookback_hours"] = float(raw_lb)
    else:
        st_clean = "newsapi" if source_type in ("news_api", "newsapi") else ("ais" if source_type in ("maritime_ais", "ais") else source_type)
        sys_res = await session.execute(select(SystemSettings).where(SystemSettings.key == "max_lookback_hours"))
        sys_row = sys_res.scalars().first()
        lb_map = sys_row.value_json if sys_row and sys_row.value_json else {}
        config["max_lookback_hours"] = float(lb_map.get(st_clean, 1.0))

    enabled = payload.get("enabled", payload.get("is_enabled", True))
    interval = payload.get("interval_seconds", payload.get("polling_interval_sec", 300))

    source = Source(
        theme_id=theme_id_val,
        name=name,
        source_type=source_type,
        config_json=config,
        is_enabled=bool(enabled),
        polling_interval_sec=int(interval),
    )
    session.add(source)
    await session.commit()
    await session.refresh(source)

    await broadcast_source_reload(str(source.id))
    return serialize_source(source)


@router.get("/{source_id}", dependencies=[Depends(require_api_key)])
async def get_source(
    source_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """Retrieve details of a specific source."""
    source = await find_source(session, source_id)
    if not source:
        raise HTTPException(status_code=404, detail=f"Source '{source_id}' not found")
    return serialize_source(source)


@router.put("/{source_id}", dependencies=[Depends(require_api_key)])
async def update_source(
    source_id: str,
    payload: Dict[str, Any],
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """Update source parameters, enable/disable toggle, or polling interval."""
    source = await find_source(session, source_id)
    if not source:
        raise HTTPException(status_code=404, detail=f"Source '{source_id}' not found")

    if "name" in payload and payload["name"] is not None:
        source.name = payload["name"]
    if "source_type" in payload and payload["source_type"] is not None:
        source.source_type = payload["source_type"]

    if "enabled" in payload:
        source.is_enabled = bool(payload["enabled"])
    elif "is_enabled" in payload:
        source.is_enabled = bool(payload["is_enabled"])

    if "interval_seconds" in payload:
        source.polling_interval_sec = int(payload["interval_seconds"])
    elif "polling_interval_sec" in payload:
        source.polling_interval_sec = int(payload["polling_interval_sec"])

    # Update config JSON
    current_cfg = dict(source.config_json) if isinstance(source.config_json, dict) else {}
    if "config_json" in payload and isinstance(payload["config_json"], dict):
        current_cfg.update(payload["config_json"])
    if "target" in payload:
        current_cfg["target"] = payload["target"]
        if source.source_type == "rss":
            current_cfg["url"] = payload["target"]
    if "status" in payload:
        current_cfg["status"] = payload["status"]
    if "error_message" in payload:
        current_cfg["error_message"] = payload["error_message"]
    if "max_lookback_hours" in payload and payload["max_lookback_hours"] is not None:
        current_cfg["max_lookback_hours"] = float(payload["max_lookback_hours"])

    source.config_json = current_cfg

    await session.commit()
    await session.refresh(source)

    await broadcast_source_reload(str(source.id))
    return serialize_source(source)


@router.delete("/{source_id}", dependencies=[Depends(require_api_key)])
async def delete_source(
    source_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """Delete source and stop active background polling task."""
    source = await find_source(session, source_id)
    if not source:
        raise HTTPException(status_code=404, detail=f"Source '{source_id}' not found")

    s_id_str = str(source.id)
    await session.delete(source)
    await session.commit()

    await broadcast_source_reload(s_id_str)
    return {"status": "deleted", "id": s_id_str}


@router.post("/{source_id}/trigger", dependencies=[Depends(require_api_key)])
async def trigger_source(
    source_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """
    Manually trigger an immediate poll cycle for a source.
    Returns 400 Bad Request if the source is currently disabled.
    """
    source = await find_source(session, source_id)
    if not source:
        raise HTTPException(status_code=404, detail=f"Source '{source_id}' not found")

    if not source.is_enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot trigger disabled source",
        )

    # Attempt manual poll via supervisor if running
    try:
        from backend.app.plugins.supervisor import get_ingestion_supervisor
        supervisor = get_ingestion_supervisor()
        await supervisor.poll_source_once(source.id)
    except Exception as exc:
        logger.debug(f"Manual poll trigger exception: {exc}")

    source.last_polled_at = utc_now()
    await session.commit()

    return {
        "status": "triggered",
        "source_id": str(source.id),
        "timestamp": source.last_polled_at.isoformat(),
    }
