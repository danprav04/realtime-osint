"""
backend/app/api/public.py - Public Read-Only Intelligence Endpoints & Password Authentication
=============================================================================================
Provides gated, read-only intelligence endpoints for public viewers.
Requires valid viewer session token obtained via password authentication.
Guarantees zero mutation and zero automatic LLM generation on public endpoints.
"""

from datetime import datetime, timezone
import logging
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.events import serialize_event
from backend.app.auth import (
    auth_rate_limiter,
    generate_viewer_token,
    require_viewer_token,
    verify_public_password,
)
from backend.app.db.models import Event, Source, Theme, utc_now
from backend.app.db.session import get_db_session
from backend.app.services.embedding_service import get_embedding_service
from backend.app.services.summary_service import get_summary_service

logger = logging.getLogger("osint.api.public")

router = APIRouter(prefix="/api/public", tags=["Public Read-Only Display"])


class PublicAuthRequest(BaseModel):
    password: str = Field(..., description="Access password for the public intelligence display")


def _get_client_ip(request: Request) -> str:
    """Extract client IP handling Cloudflare CF-Connecting-IP and standard reverse proxy headers."""
    cf_ip = request.headers.get("CF-Connecting-IP")
    if cf_ip:
        return cf_ip.strip()
    x_real_ip = request.headers.get("X-Real-IP")
    if x_real_ip:
        return x_real_ip.strip()
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@router.post("/auth")
async def authenticate_public_viewer(
    payload: PublicAuthRequest,
    request: Request,
) -> Dict[str, Any]:
    """
    Authenticate public display visitor with password.
    Enforces strict rate-limiting (max 5 failed attempts per 5 minutes per IP).
    """
    client_ip = _get_client_ip(request)

    # 1. Check rate limit
    is_limited, retry_after = auth_rate_limiter.is_limited(client_ip)
    if is_limited:
        logger.warning(f"Rate limited public auth attempt from IP: {client_ip}")
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many failed password attempts. Please wait {retry_after} seconds before trying again.",
            headers={"Retry-After": str(retry_after)},
        )

    # 2. Verify password
    if not verify_public_password(payload.password):
        auth_rate_limiter.record_attempt(client_ip)
        logger.warning(f"Failed public display login attempt from IP: {client_ip}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect password. Access to intelligence display denied.",
        )

    # 3. Successful login - clear failed attempts and return signed viewer token
    auth_rate_limiter.reset(client_ip)
    viewer_token = generate_viewer_token(expires_in_seconds=86400)
    logger.info(f"Successful public display authentication from IP: {client_ip}")
    return {
        "authenticated": True,
        "token": viewer_token,
        "expires_in": 86400,
        "role": "public_viewer",
    }


@router.get("/verify")
async def verify_viewer_auth(
    token: str = Depends(require_viewer_token),
) -> Dict[str, Any]:
    """Verify whether a stored viewer token is valid."""
    return {
        "authenticated": True,
        "role": "public_viewer",
    }


@router.get("/summary")
async def get_public_summary(
    theme_id: Optional[str] = Query(None, description="Theme ID, slug, or 'active'"),
    time_range: str = Query("all", description="Time window: 6h, 24h, 3d, 7d, all"),
    session: AsyncSession = Depends(get_db_session),
    token: str = Depends(require_viewer_token),
) -> Dict[str, Any]:
    """
    Retrieve the latest persisted AI Situational Summary (SitRep).
    Guarantees ZERO LLM generation or pipeline stage invocation.
    """
    service = get_summary_service()
    try:
        return await service.get_latest_persisted_summary(
            session=session,
            theme_id=theme_id,
            time_range=time_range,
        )
    except Exception as exc:
        logger.error(f"Error fetching public summary: {exc}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to load summary: {str(exc)}",
        )


@router.get("/events")
async def get_public_events(
    theme_id: Optional[str] = Query(None, description="Filter by theme slug or numeric ID"),
    source_type: Optional[str] = Query(None, description="Filter by source type"),
    min_score: Optional[int] = Query(None, ge=1, le=10, description="Minimum importance score"),
    max_score: Optional[int] = Query(None, ge=1, le=10, description="Maximum importance score"),
    search: Optional[str] = Query(None, description="Search text in title, summary, or raw text"),
    sort_by: Optional[str] = Query("published_desc", description="Sort order"),
    limit: int = Query(50, ge=1, le=150, description="Page size limit"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    session: AsyncSession = Depends(get_db_session),
    token: str = Depends(require_viewer_token),
) -> List[Dict[str, Any]]:
    """Retrieve intelligence timeline events for public display."""
    query = select(Event)

    # Sort order
    if sort_by == "score_desc":
        query = query.order_by(Event.importance_score.desc().nullslast(), Event.published_at.desc())
    elif sort_by == "score_asc":
        query = query.order_by(Event.importance_score.asc().nullslast(), Event.published_at.desc())
    elif sort_by == "ingested_desc":
        query = query.order_by(Event.ingested_at.desc())
    elif sort_by == "published_asc":
        query = query.order_by(Event.published_at.asc())
    else:
        query = query.order_by(Event.published_at.desc())

    # Theme filter
    if theme_id:
        if theme_id.isdigit():
            query = query.where(Event.theme_id == int(theme_id))
        else:
            theme_subq = select(Theme.id).where(Theme.slug == theme_id).scalar_subquery()
            query = query.where(Event.theme_id == theme_subq)

    # Source type filter
    if source_type and source_type.lower() != "all":
        query = query.where(Event.source_type == source_type.lower())

    # Importance score filters
    if min_score is not None:
        query = query.where(Event.importance_score >= min_score)
    if max_score is not None:
        query = query.where(Event.importance_score <= max_score)

    # Search filter
    if search and search.strip():
        term = f"%{search.strip().lower()}%"
        query = query.where(
            or_(
                func.lower(Event.title).like(term),
                func.lower(Event.summary).like(term),
                func.lower(Event.raw_text).like(term),
            )
        )

    results = await session.execute(query.limit(limit).offset(offset))
    events = results.scalars().all()
    return [serialize_event(e) for e in events]


@router.get("/map")
async def get_public_map(
    theme_id: Optional[str] = Query(None, description="Theme ID or slug filter"),
    time_range: Optional[str] = Query(None, description="Time range: 6h, 24h, 3d, 7d, all"),
    min_score: Optional[int] = Query(None, ge=1, le=10, description="Minimum importance score"),
    limit: int = Query(150, ge=10, le=200, description="Maximum events to project on map"),
    session: AsyncSession = Depends(get_db_session),
    token: str = Depends(require_viewer_token),
) -> Dict[str, Any]:
    """Retrieve 2D relevance map data for public display."""
    hours_map = {
        "6h": 6,
        "24h": 24,
        "3d": 72,
        "7d": 168,
    }
    time_range_hours = hours_map.get(time_range) if time_range else None
    service = get_embedding_service()
    try:
        return await service.get_relevance_map_data(
            session=session,
            theme_id=theme_id,
            time_range_hours=time_range_hours,
            min_score=min_score,
            limit=limit,
            force_reproject=False,
        )
    except Exception as exc:
        logger.error(f"Failed to generate public embeddings map: {exc}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error retrieving relevance map",
        )


@router.get("/stats")
async def get_public_stats(
    session: AsyncSession = Depends(get_db_session),
    token: str = Depends(require_viewer_token),
) -> Dict[str, Any]:
    """Retrieve summary stat counters for public display cards."""
    active_theme = (
        await session.execute(select(Theme).where(Theme.is_active == True).limit(1))
    ).scalars().first()
    threshold = (
        active_theme.alert_threshold if (active_theme and active_theme.alert_threshold) else 7
    )

    total_events = (
        await session.execute(select(func.count(Event.id)))
    ).scalar_one_or_none() or 0

    high_priority_events = (
        await session.execute(
            select(func.count(Event.id)).where(Event.importance_score >= threshold)
        )
    ).scalar_one_or_none() or 0

    latest_event_time = (
        await session.execute(select(func.max(Event.ingested_at)))
    ).scalar_one_or_none()

    latest_heartbeat = latest_event_time
    try:
        from backend.app.plugins.supervisor import get_ingestion_supervisor
        sup = get_ingestion_supervisor()
        if sup and getattr(sup, "last_ingested_at", None):
            if not latest_heartbeat or sup.last_ingested_at > latest_heartbeat:
                latest_heartbeat = sup.last_ingested_at
    except Exception:
        pass

    return {
        "total_events": total_events,
        "high_priority_events": high_priority_events,
        "active_sources": 0,
        "last_updated": latest_heartbeat.isoformat() if latest_heartbeat else utc_now().isoformat(),
        "last_update_timestamp": latest_heartbeat.isoformat() if latest_heartbeat else utc_now().isoformat(),
    }


@router.get("/theme")
async def get_public_active_theme(
    session: AsyncSession = Depends(get_db_session),
    token: str = Depends(require_viewer_token),
) -> Dict[str, Any]:
    """Retrieve active monitoring theme metadata for display header."""
    active_theme = (
        await session.execute(select(Theme).where(Theme.is_active == True).limit(1))
    ).scalars().first()

    if not active_theme:
        return {
            "id": "default",
            "name": "US-Iran conflict",
            "slug": "us-iran",
            "description": "Real-time geopolitical intelligence monitoring",
            "alert_threshold": 7,
        }

    return {
        "id": str(active_theme.id),
        "name": active_theme.name,
        "slug": active_theme.slug or str(active_theme.id),
        "description": active_theme.description,
        "alert_threshold": active_theme.alert_threshold or 7,
    }
