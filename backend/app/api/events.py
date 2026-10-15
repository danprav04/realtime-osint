"""
Events REST API endpoints.
Provides timeline querying, multi-field filtering, pagination, and direct ingestion
with WebSocket broadcasting via Redis Pub/Sub.
"""

from datetime import datetime, timezone
import logging
from typing import Any, Dict, List, Optional
import uuid
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from backend.app.auth import require_api_key
from backend.app.db.models import Event, Theme, utc_now
from backend.app.db.session import get_db_session
from backend.app.services.redis_bus import (
    CHANNEL_EVENTS_ALERT,
    CHANNEL_EVENTS_NEW,
    get_redis_bus,
)

logger = logging.getLogger("osint.api.events")

router = APIRouter(prefix="/api/events", tags=["Events"])


def serialize_event(event: Event) -> Dict[str, Any]:
    """Serialize an Event model into standardized timeline output."""
    evt_dict = event.to_dict()
    if "theme" in event.__dict__ and event.theme is not None and getattr(event.theme, "slug", None):
        evt_dict["theme_id"] = event.theme.slug
    else:
        evt_dict["theme_id"] = str(event.theme_id)

    # Ensure source_url and common attributes are top-level
    evt_dict["source_url"] = getattr(event, "source_url", None)
    return evt_dict


@router.get("", dependencies=[Depends(require_api_key)])
async def list_events(
    theme_id: Optional[str] = Query(None, description="Filter by theme slug or numeric ID"),
    source_type: Optional[str] = Query(None, description="Filter by source type (rss, telegram, reddit, etc.)"),
    min_score: Optional[int] = Query(None, ge=1, le=10, description="Minimum importance score"),
    max_score: Optional[int] = Query(None, ge=1, le=10, description="Maximum importance score"),
    start_date: Optional[datetime] = Query(None, description="Filter events published after this timestamp"),
    end_date: Optional[datetime] = Query(None, description="Filter events published before this timestamp"),
    search: Optional[str] = Query(None, description="Case-insensitive text search in title, summary, or text"),
    sort_by: Optional[str] = Query("published_desc", description="Sort order: published_desc, published_asc, score_desc, score_asc, ingested_desc"),
    limit: int = Query(50, ge=1, le=500, description="Page size limit"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    session: AsyncSession = Depends(get_db_session),
) -> List[Dict[str, Any]]:
    """Retrieve intelligence events with timeline filtering, text search, and pagination."""
    query = select(Event)

    if sort_by == "score_desc":
        query = query.order_by(
            Event.importance_score.desc().nullslast(),
            Event.published_at.desc().nullslast(),
        )
    elif sort_by == "score_asc":
        query = query.order_by(
            Event.importance_score.asc().nullslast(),
            Event.published_at.desc().nullslast(),
        )
    elif sort_by == "published_asc":
        query = query.order_by(
            Event.published_at.asc().nullslast(),
            Event.ingested_at.asc().nullslast(),
        )
    elif sort_by == "ingested_desc":
        query = query.order_by(
            Event.ingested_at.desc().nullslast(),
            Event.published_at.desc().nullslast(),
        )
    else:
        # Default: published_desc
        query = query.order_by(
            Event.published_at.desc().nullslast(),
            Event.ingested_at.desc().nullslast(),
        )

    if theme_id:
        if theme_id.isdigit():
            query = query.where(Event.theme_id == int(theme_id))
        else:
            t_stmt = select(Theme).where(
                (Theme.slug == theme_id) | (Theme.name.ilike(theme_id.replace("-", " ")))
            )
            theme_obj = (await session.execute(t_stmt)).scalars().first()
            if theme_obj:
                query = query.where(Event.theme_id == theme_obj.id)
            else:
                return []

    if source_type:
        query = query.where(Event.source_type == source_type)

    if min_score is not None:
        query = query.where(Event.importance_score >= min_score)

    if max_score is not None:
        query = query.where(Event.importance_score <= max_score)

    if start_date is not None:
        query = query.where(Event.published_at >= start_date)

    if end_date is not None:
        query = query.where(Event.published_at <= end_date)

    if search:
        search_pattern = f"%{search}%"
        query = query.where(
            or_(
                Event.title.ilike(search_pattern),
                Event.summary.ilike(search_pattern),
                Event.raw_text.ilike(search_pattern),
            )
        )

    query = query.offset(offset).limit(limit)
    events = (await session.execute(query)).scalars().all()
    return [serialize_event(e) for e in events]


@router.post("", status_code=status.HTTP_200_OK, dependencies=[Depends(require_api_key)])
async def ingest_event(
    event_data: Dict[str, Any],
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """Ingest a normalized intelligence event and broadcast it via WebSockets."""
    event_id = event_data.get("id") or str(uuid.uuid4())
    raw_theme_id = event_data.get("theme_id")
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

    external_id = event_data.get("external_id") or f"ext_{event_id}"

    # Deduplication check by (theme_id, external_id)
    dup_stmt = select(Event).where(
        (Event.theme_id == theme_id_val) & (Event.external_id == external_id)
    ).limit(1)
    existing = (await session.execute(dup_stmt)).scalars().first()

    if existing:
        return serialize_event(existing)

    pub_at = event_data.get("published_at")
    if isinstance(pub_at, str):
        try:
            pub_date = datetime.fromisoformat(pub_at.replace("Z", "+00:00"))
        except Exception:
            pub_date = utc_now()
    elif isinstance(pub_at, datetime):
        pub_date = pub_at
    else:
        pub_date = utc_now()

    score = event_data.get("importance_score")
    if score is not None:
        try:
            score = int(score)
        except (ValueError, TypeError):
            score = None

    db_event = Event(
        id=event_id,
        theme_id=theme_id_val,
        source_id=event_data.get("source_id"),
        source_type=event_data.get("source_type", "manual"),
        source_name=event_data.get("source_name", "Direct API Ingest"),
        source_url=event_data.get("source_url"),
        external_id=external_id,
        title=event_data.get("title", "Untitled Intelligence Event"),
        raw_text=event_data.get("raw_text", ""),
        translated_text=event_data.get("translated_text"),
        language=event_data.get("language", "en"),
        author=event_data.get("author"),
        location_json=event_data.get("location") or event_data.get("location_json"),
        metadata_json=event_data.get("metadata") or event_data.get("metadata_json") or {},
        importance_score=score,
        score_rationale=event_data.get("score_rationale"),
        summary=event_data.get("summary"),
        headline=event_data.get("headline", event_data.get("title")),
        entities_json=event_data.get("entities") or event_data.get("entities_json") or [],
        is_analyzed=score is not None,
        published_at=pub_date,
        ingested_at=utc_now(),
    )
    session.add(db_event)
    await session.commit()
    await session.refresh(db_event)

    serialized = serialize_event(db_event)

    # Broadcast to Redis Pub/Sub
    try:
        bus = get_redis_bus()
        await bus.publish(CHANNEL_EVENTS_NEW, serialized)

        # Check alert threshold
        theme_threshold = 7
        t_row = (await session.execute(select(Theme).where(Theme.id == theme_id_val))).scalars().first()
        if t_row and t_row.alert_threshold:
            theme_threshold = t_row.alert_threshold

        if score is not None and score >= theme_threshold:
            alert_payload = {
                "event_id": serialized["id"],
                "id": serialized["id"],
                "title": serialized["title"],
                "headline": serialized.get("headline"),
                "summary": serialized.get("summary") or serialized["title"],
                "importance_score": score,
                "source_name": serialized["source_name"],
                "published_at": serialized["published_at"],
                "source_url": serialized.get("source_url"),
                "ai_provenance": serialized.get("ai_provenance"),
                "metadata_json": serialized.get("metadata_json"),
            }
            await bus.publish(CHANNEL_EVENTS_ALERT, alert_payload)
    except Exception as exc:
        logger.warning(f"Failed to publish ingested event to Redis bus: {exc}")

    return serialized


@router.get("/{event_id}", dependencies=[Depends(require_api_key)])
async def get_event(
    event_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """Retrieve an event by ID."""
    stmt = select(Event).where(Event.id == event_id)
    event = (await session.execute(stmt)).scalars().first()
    if not event:
        raise HTTPException(status_code=404, detail=f"Event '{event_id}' not found")
    return serialize_event(event)


@router.delete("/{event_id}", dependencies=[Depends(require_api_key)])
async def delete_event(
    event_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """Delete an event by ID."""
    stmt = select(Event).where(Event.id == event_id)
    event = (await session.execute(stmt)).scalars().first()
    if not event:
        raise HTTPException(status_code=404, detail=f"Event '{event_id}' not found")

    await session.delete(event)
    await session.commit()
    return {"status": "deleted", "id": event_id}
