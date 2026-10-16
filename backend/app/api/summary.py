"""
backend/app/api/summary.py - AI Situational Summary REST API Endpoints
======================================================================
Provides endpoints for retrieving and regenerating the full-spectrum AI
Situational Summary (SitRep) fusing incoming report summaries, high-dimensional
embeddings, semantic clusters, trustworthiness metrics, and live OSINT telemetry.
"""

import logging
from typing import Any, Dict, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.auth import require_api_key
from backend.app.db.session import get_db_session
from backend.app.services.summary_service import get_summary_service

logger = logging.getLogger("osint.api.summary")

router = APIRouter(prefix="/api/summary", tags=["AI Situational Summary"])


@router.get("", dependencies=[Depends(require_api_key)])
async def get_situation_summary(
    theme_id: Optional[str] = Query(None, description="Theme ID, slug, 'active', or 'all'"),
    time_range: str = Query("all", description="Time window: 6h, 24h, 3d, 7d, all"),
    force_refresh: bool = Query(False, description="Bypass cache and regenerate AI briefing"),
    limit: int = Query(150, ge=10, le=500, description="Max events to analyze"),
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """
    Retrieve the current AI Situational Summary (SitRep).
    Returns cached briefing if fresh (<15m and no new events ingested), or synthesizes
    a new briefing across all available report summaries, embeddings, and telemetry.
    """
    service = get_summary_service()
    try:
        return await service.generate_or_get_summary(
            session=session,
            theme_id=theme_id,
            time_range=time_range,
            force_refresh=force_refresh,
            limit=limit,
        )
    except Exception as exc:
        logger.error(f"Error generating situational summary: {exc}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate situational summary: {str(exc)}",
        )


@router.post("/generate", dependencies=[Depends(require_api_key)])
async def regenerate_situation_summary(
    theme_id: Optional[str] = Query(None, description="Theme ID, slug, 'active', or 'all'"),
    time_range: str = Query("all", description="Time window: 6h, 24h, 3d, 7d, all"),
    limit: int = Query(150, ge=10, le=500, description="Max events to analyze"),
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """
    Explicitly trigger a fresh AI Situational Summary synthesis, bypassing cache.
    """
    service = get_summary_service()
    try:
        return await service.generate_or_get_summary(
            session=session,
            theme_id=theme_id,
            time_range=time_range,
            force_refresh=True,
            limit=limit,
        )
    except Exception as exc:
        logger.error(f"Error regenerating situational summary: {exc}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to regenerate situational summary: {str(exc)}",
        )


@router.get("/rollups", dependencies=[Depends(require_api_key)])
async def get_historical_rollups(
    theme_id: Optional[str] = Query(None, description="Theme ID, slug, or 'all'"),
    limit: int = Query(50, ge=1, le=200, description="Max historical epochs to return"),
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """
    Retrieve historical tiered memory rollups (>7d summaries and entity/location baselines).
    """
    from backend.app.services.memory_rollup_service import get_memory_rollup_service
    service = get_memory_rollup_service()
    try:
        rollups = await service.get_rollups(session=session, theme_id=theme_id, limit=limit)
        return {
            "total_rollups": len(rollups),
            "rollups": rollups,
        }
    except Exception as exc:
        logger.error(f"Error fetching historical rollups: {exc}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch historical rollups: {str(exc)}",
        )


@router.post("/rollups/compact", dependencies=[Depends(require_api_key)])
async def trigger_memory_compaction(
    theme_id: Optional[int] = Query(None, description="Optional theme ID to compact"),
    age_threshold_days: int = Query(7, ge=1, le=365, description="Age threshold in days for rollups"),
    clear_routine_vectors: bool = Query(True, description="Prune vectors for routine events (<7) to save RAM"),
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """
    Manually trigger compaction of aged events into HistoricalRollup epochs.
    """
    from backend.app.services.memory_rollup_service import get_memory_rollup_service
    service = get_memory_rollup_service()
    try:
        res = await service.compact_aged_events(
            session=session,
            theme_id=theme_id,
            age_threshold_days=age_threshold_days,
            clear_routine_vectors=clear_routine_vectors,
        )
        return res
    except Exception as exc:
        logger.error(f"Error during memory compaction: {exc}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to run memory compaction: {str(exc)}",
        )

