"""
Stats REST API endpoints.
Provides aggregated dashboard metrics, score distribution analytics, source breakdowns,
and hourly volume statistics.
"""

from collections import defaultdict
from datetime import datetime, timedelta, timezone
import logging
from typing import Any, Dict
from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from backend.app.auth import require_api_key
from backend.app.db.models import Event, Source, Theme, utc_now
from backend.app.db.session import get_db_session

logger = logging.getLogger("osint.api.stats")

router = APIRouter(prefix="/api/stats", tags=["Stats"])


@router.get("", dependencies=[Depends(require_api_key)])
async def get_dashboard_stats(
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """Retrieve aggregated dashboard metrics and visual analytics."""
    # 1. Determine alert threshold from active theme
    active_theme = (await session.execute(
        select(Theme).where(Theme.is_active == True).limit(1)
    )).scalars().first()
    threshold = active_theme.alert_threshold if (active_theme and active_theme.alert_threshold) else 7

    # 2. Total events count
    total_events = (await session.execute(
        select(func.count(Event.id))
    )).scalar_one_or_none() or 0

    # 3. High priority events count (score >= threshold)
    high_priority_events = (await session.execute(
        select(func.count(Event.id)).where(Event.importance_score >= threshold)
    )).scalar_one_or_none() or 0

    # 4. Active sources count
    active_sources = (await session.execute(
        select(func.count(Source.id)).where(Source.is_enabled == True)
    )).scalar_one_or_none() or 0

    # 5. Latest update timestamp
    latest_event_time = (await session.execute(
        select(func.max(Event.ingested_at))
    )).scalar_one_or_none()

    latest_heartbeat = latest_event_time
    try:
        from backend.app.plugins.supervisor import get_ingestion_supervisor
        sup = get_ingestion_supervisor()
        if sup and getattr(sup, "last_ingested_at", None):
            if not latest_heartbeat or sup.last_ingested_at > latest_heartbeat:
                latest_heartbeat = sup.last_ingested_at
    except Exception:
        pass

    now_iso = utc_now().isoformat()
    last_updated_str = latest_heartbeat.isoformat() if latest_heartbeat else now_iso

    # 6. Score distribution (1-10)
    score_rows = (await session.execute(
        select(Event.importance_score, func.count(Event.id))
        .where(Event.importance_score.isnot(None))
        .group_by(Event.importance_score)
    )).all()

    scores_dict: Dict[int, int] = {i: 0 for i in range(1, 11)}
    for score_val, count_val in score_rows:
        if score_val and 1 <= score_val <= 10:
            scores_dict[score_val] = count_val

    b_1_3 = sum(scores_dict[i] for i in (1, 2, 3))
    b_4_6 = sum(scores_dict[i] for i in (4, 5, 6))
    b_7_8 = sum(scores_dict[i] for i in (7, 8))
    b_9_10 = sum(scores_dict[i] for i in (9, 10))

    score_distribution = {
        "1-3": b_1_3,
        "4-6": b_4_6,
        "7-8": b_7_8,
        "9-10": b_9_10,
        **{str(i): scores_dict[i] for i in range(1, 11)},
    }

    # 7. Source breakdown
    source_rows = (await session.execute(
        select(Event.source_type, func.count(Event.id))
        .group_by(Event.source_type)
    )).all()

    source_breakdown = {st: count for st, count in source_rows if st}
    # Ensure standard types present
    for st in ("rss", "telegram", "newsapi", "reddit", "ais"):
        source_breakdown.setdefault(st, 0)

    # 8. Hourly volume (last 24 hours)
    now = utc_now()
    since_24h = now - timedelta(hours=24)
    hourly_rows = (await session.execute(
        select(Event.published_at)
        .where(Event.published_at >= since_24h)
        .order_by(Event.published_at.asc())
    )).scalars().all()

    hourly_buckets: Dict[str, int] = defaultdict(int)
    for i in range(24):
        slot_time = (since_24h + timedelta(hours=i)).replace(minute=0, second=0, microsecond=0)
        hourly_buckets[slot_time.strftime("%Y-%m-%dT%H:00:00Z")] = 0

    for pub_at in hourly_rows:
        if pub_at:
            bucket_key = pub_at.replace(minute=0, second=0, microsecond=0).strftime("%Y-%m-%dT%H:00:00Z")
            hourly_buckets[bucket_key] += 1

    hourly_volume = [{"hour": k, "count": v} for k, v in sorted(hourly_buckets.items())]

    return {
        "total_events": total_events,
        "high_priority_events": high_priority_events,
        "active_sources": active_sources,
        "last_updated": last_updated_str,
        "last_update_timestamp": last_updated_str,
        "score_distribution": score_distribution,
        "source_breakdown": source_breakdown,
        "hourly_volume": hourly_volume,
    }


@router.get("/sources/credibility", dependencies=[Depends(require_api_key)])
async def get_source_credibility_leaderboard(
    theme_id: Optional[int] = None,
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """
    Retrieve dynamic source credibility leaderboard based on historical corroboration and reporting.
    """
    from backend.app.services.memory_rollup_service import get_memory_rollup_service
    service = get_memory_rollup_service()
    try:
        leaderboard = await service.get_dynamic_source_credibility(session=session, theme_id=theme_id)
        return {
            "total_sources_evaluated": len(leaderboard),
            "leaderboard": leaderboard,
        }
    except Exception as exc:
        logger.error(f"Error computing source credibility: {exc}", exc_info=True)
        return {
            "total_sources_evaluated": 0,
            "leaderboard": [],
        }

