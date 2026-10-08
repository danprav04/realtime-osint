"""
Theme Engine REST API endpoints.
Provides theme management, custom scoring prompts, threshold adjustments,
and zero-code dynamic switching between intelligence monitoring topics.
"""

from datetime import datetime, timezone
import logging
import re
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from backend.app.auth import require_api_key
from backend.app.db.models import Theme
from backend.app.db.session import get_db_session
from backend.app.services.redis_bus import (
    CHANNEL_CONFIG_RELOAD,
    get_redis_bus,
)

logger = logging.getLogger("osint.api.themes")

router = APIRouter(prefix="/api/themes", tags=["Themes"])


class ThemeCreatePayload(BaseModel):
    name: str = Field(..., min_length=2, max_length=150)
    id: Optional[str] = None
    slug: Optional[str] = None
    description: Optional[str] = None
    keywords: List[str] = Field(default_factory=list)
    entities: List[str] = Field(default_factory=list)
    scoring_prompt: Optional[str] = "Score event on 1-10 importance scale based on geopolitical impact."
    is_active: bool = True
    alert_threshold: Optional[int] = None
    importance_threshold: Optional[int] = None
    notification_frequency_limit_minutes: Optional[int] = 5
    source_presets: Optional[Dict[str, Any]] = Field(default_factory=dict)


class ThemeUpdatePayload(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    keywords: Optional[List[str]] = None
    entities: Optional[List[str]] = None
    scoring_prompt: Optional[str] = None
    is_active: Optional[bool] = None
    alert_threshold: Optional[int] = None
    importance_threshold: Optional[int] = None
    notification_frequency_limit_minutes: Optional[int] = None
    source_presets: Optional[Dict[str, Any]] = None


def serialize_theme(theme: Theme) -> Dict[str, Any]:
    """Serialize a Theme DB model into standard API response schema."""
    slug_val = theme.slug or (theme.name.lower().replace(" ", "-") if theme.name else str(theme.id))
    theme_dict = theme.to_dict()
    theme_dict["id"] = slug_val
    theme_dict["slug"] = slug_val
    theme_dict["db_id"] = theme.id
    theme_dict["importance_threshold"] = theme.alert_threshold
    theme_dict["alert_threshold"] = theme.alert_threshold
    theme_dict.setdefault("notification_frequency_limit_minutes", 5)
    theme_dict.setdefault("source_presets", {})
    return theme_dict


async def find_theme(session: AsyncSession, id_or_slug: str) -> Optional[Theme]:
    """
    Look up a Theme by slug, numeric ID, name, or 'active' keyword (case-insensitive & hyphen-tolerant).
    Auto-populates slug if matched and slug was previously NULL.
    """
    if not id_or_slug:
        return None

    target = str(id_or_slug).strip()

    # 0. Keyword 'active' -> return currently active theme
    if target.lower() == "active":
        stmt = select(Theme).where(Theme.is_active == True).limit(1)
        theme = (await session.execute(stmt)).scalars().first()
        if theme:
            return theme

    # 1. Numeric ID match if digits
    if target.isdigit():
        stmt = select(Theme).where(Theme.id == int(target))
        theme = (await session.execute(stmt)).scalars().first()
        if theme:
            return theme

    # 2. Direct slug match (case-insensitive)
    stmt = select(Theme).where(func.lower(Theme.slug) == target.lower())
    theme = (await session.execute(stmt)).scalars().first()
    if theme:
        return theme

    # 3. Direct exact name match (case-insensitive)
    stmt = select(Theme).where(Theme.name.ilike(target))
    theme = (await session.execute(stmt)).scalars().first()
    if theme:
        return theme

    # 4. Slugified name match (spaces replaced by hyphens, e.g. 'US-Iran conflict' -> 'us-iran-conflict')
    stmt = select(Theme).where(func.lower(func.replace(Theme.name, " ", "-")) == target.lower())
    theme = (await session.execute(stmt)).scalars().first()
    if theme:
        if not theme.slug:
            theme.slug = target.lower()
            session.add(theme)
            await session.commit()
            await session.refresh(theme)
        return theme

    # 5. Reverse: target hyphens converted to spaces
    target_spaces = target.replace("-", " ")
    stmt = select(Theme).where(Theme.name.ilike(target_spaces))
    theme = (await session.execute(stmt)).scalars().first()
    if theme:
        if not theme.slug:
            theme.slug = target.lower()
            session.add(theme)
            await session.commit()
            await session.refresh(theme)
        return theme

    # 6. Fallback: normalize all non-alphanumeric characters across all stored themes
    res = await session.execute(select(Theme))
    all_themes = res.scalars().all()
    clean_target = re.sub(r"[^a-z0-9]", "", target.lower())
    if clean_target:
        for t in all_themes:
            clean_name = re.sub(r"[^a-z0-9]", "", t.name.lower()) if t.name else ""
            clean_slug = re.sub(r"[^a-z0-9]", "", t.slug.lower()) if t.slug else ""
            if clean_target == clean_slug or clean_target == clean_name:
                if not t.slug:
                    t.slug = target.lower()
                    session.add(t)
                    await session.commit()
                    await session.refresh(t)
                return t

        for t in all_themes:
            clean_name = re.sub(r"[^a-z0-9]", "", t.name.lower()) if t.name else ""
            if clean_target in clean_name or clean_name in clean_target:
                return t

    return None



async def broadcast_theme_reload(theme_identifier: str) -> None:
    """Notify system components via Redis Pub/Sub of a theme reconfiguration."""
    try:
        bus = get_redis_bus()
        payload = {
            "action": "theme_update",
            "theme_id": str(theme_identifier),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        await bus.publish(CHANNEL_CONFIG_RELOAD, payload)
    except Exception as exc:
        logger.warning(f"Failed to publish theme reload message: {exc}")

    # Synchronously refresh active supervisor and pipeline instances if loaded
    try:
        from backend.app.plugins.supervisor import get_ingestion_supervisor
        supervisor = get_ingestion_supervisor()
        await supervisor.reconcile_sources()
    except Exception as exc:
        logger.debug(f"Supervisor reconcile notification: {exc}")

    try:
        from backend.app.pipeline.orchestrator import get_pipeline_orchestrator
        orchestrator = get_pipeline_orchestrator()
        await orchestrator._reload_configs_from_db()
    except Exception as exc:
        logger.debug(f"Pipeline reload notification: {exc}")


@router.get("", dependencies=[Depends(require_api_key)])
async def list_themes(
    is_active: Optional[bool] = Query(None, description="Filter by active status"),
    session: AsyncSession = Depends(get_db_session),
) -> List[Dict[str, Any]]:
    """List all available intelligence themes."""
    query = select(Theme).order_by(Theme.id.asc())
    if is_active is not None:
        query = query.where(Theme.is_active == is_active)
    result = await session.execute(query)
    themes = result.scalars().all()
    return [serialize_theme(t) for t in themes]


@router.post("", status_code=status.HTTP_200_OK, dependencies=[Depends(require_api_key)])
async def create_theme(
    payload: Dict[str, Any],
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """Create a new intelligence monitoring theme."""
    name = payload.get("name", "Untitled Theme")
    slug = (
        payload.get("slug")
        or payload.get("id")
        or name.lower().replace(" ", "-")
    )
    threshold = (
        payload.get("alert_threshold")
        or payload.get("importance_threshold")
        or 7
    )

    theme = Theme(
        slug=slug,
        name=name,
        description=payload.get("description", ""),
        keywords=payload.get("keywords", []),
        entities=payload.get("entities", []),
        scoring_prompt=payload.get("scoring_prompt", "Score 1-10 on geopolitical impact."),
        is_active=payload.get("is_active", True),
        alert_threshold=int(threshold),
    )
    session.add(theme)
    await session.commit()
    await session.refresh(theme)

    await broadcast_theme_reload(theme.slug or str(theme.id))
    return serialize_theme(theme)


@router.get("/active", dependencies=[Depends(require_api_key)])
async def get_active_theme(
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """Retrieve the currently active theme."""
    stmt = select(Theme).where(Theme.is_active == True).limit(1)
    theme = (await session.execute(stmt)).scalars().first()
    if not theme:
        raise HTTPException(status_code=404, detail="No active theme found")
    return serialize_theme(theme)


@router.get("/{id_or_slug}", dependencies=[Depends(require_api_key)])
async def get_theme(
    id_or_slug: str,
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """Retrieve theme details by ID or slug string."""
    theme = await find_theme(session, id_or_slug)
    if not theme:
        raise HTTPException(status_code=404, detail=f"Theme '{id_or_slug}' not found")
    return serialize_theme(theme)


@router.put("/{id_or_slug}", dependencies=[Depends(require_api_key)])
async def update_theme(
    id_or_slug: str,
    payload: Dict[str, Any],
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """Update theme configuration, keywords, or scoring guidelines."""
    theme = await find_theme(session, id_or_slug)
    if not theme:
        raise HTTPException(status_code=404, detail=f"Theme '{id_or_slug}' not found")

    if "name" in payload and payload["name"] is not None:
        theme.name = payload["name"]
    if "description" in payload:
        theme.description = payload["description"]
    if "keywords" in payload and payload["keywords"] is not None:
        theme.keywords = payload["keywords"]
    if "entities" in payload and payload["entities"] is not None:
        theme.entities = payload["entities"]
    if "scoring_prompt" in payload and payload["scoring_prompt"] is not None:
        theme.scoring_prompt = payload["scoring_prompt"]
    if "is_active" in payload and payload["is_active"] is not None:
        theme.is_active = bool(payload["is_active"])
    
    threshold = payload.get("alert_threshold") or payload.get("importance_threshold")
    if threshold is not None:
        theme.alert_threshold = int(threshold)

    await session.commit()
    await session.refresh(theme)

    await broadcast_theme_reload(theme.slug or str(theme.id))
    return serialize_theme(theme)


@router.post("/{id_or_slug}/activate", dependencies=[Depends(require_api_key)])
async def activate_theme(
    id_or_slug: str,
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """
    Atomically switch active monitoring theme. Deactivates all other themes
    and broadcasts zero-code hot-reload signal across the pipeline.
    """
    target_theme = await find_theme(session, id_or_slug)
    if not target_theme:
        raise HTTPException(status_code=404, detail=f"Theme '{id_or_slug}' not found")

    # Set all other themes to inactive
    await session.execute(update(Theme).values(is_active=False))
    target_theme.is_active = True
    await session.commit()
    await session.refresh(target_theme)

    theme_identifier = target_theme.slug or str(target_theme.id)
    await broadcast_theme_reload(theme_identifier)

    return {
        "status": "activated",
        "active_theme_id": theme_identifier,
    }


@router.delete("/{id_or_slug}", dependencies=[Depends(require_api_key)])
async def delete_theme(
    id_or_slug: str,
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """Delete a theme and its cascading sources and events."""
    theme = await find_theme(session, id_or_slug)
    if not theme:
        raise HTTPException(status_code=404, detail=f"Theme '{id_or_slug}' not found")

    deleted_id = theme.slug or str(theme.id)
    await session.delete(theme)
    await session.commit()

    await broadcast_theme_reload(deleted_id)
    return {"status": "deleted", "id": deleted_id}
