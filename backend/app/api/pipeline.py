"""
backend/app/api/pipeline.py - AI Pipeline Execution API Endpoints
"""

import logging
from typing import Any, Dict
from fastapi import APIRouter, Depends
from backend.app.auth import require_api_key
from backend.app.pipeline.orchestrator import get_pipeline_orchestrator
from backend.app.pipeline.models import AnalyzedEvent

logger = logging.getLogger("osint.api.pipeline")
router = APIRouter(prefix="/api/pipeline", tags=["Pipeline"])


@router.post("/process", dependencies=[Depends(require_api_key)], response_model=AnalyzedEvent)
async def process_event(event_payload: Dict[str, Any]) -> AnalyzedEvent:
    """
    Executes the 4-stage AI processing pipeline on an incoming event:
    1. Extraction & Translation (Farsi/Arabic to English)
    2. Importance Scoring (1-10 with active Theme prompt)
    3. Executive Summarization & Headline
    4. Operational Trend Categorization & Visual Tags
    """
    orchestrator = get_pipeline_orchestrator()
    analyzed = await orchestrator.process_event(event_payload)
    return analyzed
