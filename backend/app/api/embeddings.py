"""
backend/app/api/embeddings.py - Relevance Embeddings Map & Trustworthiness API
=============================================================================
Provides REST endpoints for fetching 2D relevance map data, triggering
batch backfills, recomputing PCA projections, and monitoring daily Gemini/HF quota.
"""

import logging
import os
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.auth import require_api_key
from backend.app.db.session import get_db_session
from backend.app.services.embedding_service import get_embedding_service

logger = logging.getLogger("osint.api.embeddings")

router = APIRouter(prefix="/api/embeddings", tags=["Embeddings & Relevance Map"])


@router.get("/map", dependencies=[Depends(require_api_key)])
async def get_embeddings_map(
    theme_id: Optional[str] = Query(None, description="Theme ID or slug filter"),
    time_range: Optional[str] = Query(None, description="Time range: 6h, 24h, 3d, 7d, all"),
    min_score: Optional[int] = Query(None, ge=1, le=10, description="Minimum importance score"),
    limit: int = Query(150, ge=10, le=500, description="Maximum events to project on map"),
    force_reproject: bool = Query(False, description="Force recomputing PCA 2D coordinates"),
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """
    Retrieve full 2D relevance map payload with spatial coordinates,
    trustworthiness scores, multi-source corroboration breakdown, and topical clusters.
    """
    hours_map = {
        "6h": 6,
        "24h": 24,
        "3d": 72,
        "7d": 168,
    }
    time_range_hours = hours_map.get(time_range) if time_range else None

    service = get_embedding_service()
    try:
        data = await service.get_relevance_map_data(
            session=session,
            theme_id=theme_id,
            time_range_hours=time_range_hours,
            min_score=min_score,
            limit=limit,
            force_reproject=force_reproject,
        )
        return data
    except Exception as exc:
        logger.error(f"Failed to generate embeddings map: {exc}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error generating relevance map: {str(exc)}",
        )


@router.post("/backfill", dependencies=[Depends(require_api_key)])
async def trigger_backfill(
    limit: int = Query(100, ge=1, le=300, description="Max un-embedded records to process"),
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """
    Trigger batch embedding of all pending un-embedded events in the database.
    Utilizes Gemini batchEmbedContents with deduplication to protect daily quotas.
    """
    service = get_embedding_service()
    try:
        result = await service.backfill_all_events(session=session, limit=limit)
        return result
    except Exception as exc:
        logger.error(f"Error during embedding backfill: {exc}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Backfill failed: {str(exc)}",
        )


@router.post("/reproject", dependencies=[Depends(require_api_key)])
async def reproject_map(
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """
    Force re-computation of 2D PCA spatial coordinates and multi-source corroboration metrics.
    """
    service = get_embedding_service()
    try:
        data = await service.get_relevance_map_data(session=session, force_reproject=True)
        return {
            "status": "success",
            "total_nodes": len(data.get("nodes", [])),
            "total_clusters": len(data.get("clusters", [])),
        }
    except Exception as exc:
        logger.error(f"Error during re-projection: {exc}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Re-projection failed: {str(exc)}",
        )


@router.get("/status", dependencies=[Depends(require_api_key)])
async def get_embedding_status(
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """
    Return overall embedding coverage, active model, and daily Gemini quota counters.
    """
    service = get_embedding_service()
    from sqlalchemy import func
    from backend.app.db.models import Event, EventEmbedding

    total_events = (await session.execute(select(func.count(Event.id)))).scalar() or 0
    embedded_events = (await session.execute(select(func.count(EventEmbedding.id)))).scalar() or 0
    coverage_pct = round((embedded_events / total_events * 100), 1) if total_events > 0 else 100.0

    quota = await service.get_quota_tracker(session)

    return {
        "total_events": total_events,
        "embedded_events": embedded_events,
        "pending_events": max(0, total_events - embedded_events),
        "coverage_percentage": coverage_pct,
        "primary_model": service.default_model,
        "fallback_model": service.fallback_model,
        "hf_space_configured": bool(service.api_key and os.getenv("HF_EMBEDDING_URL")),
        "quota_tracker": quota,
    }
