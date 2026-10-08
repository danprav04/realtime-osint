"""
Pentagon Pizza Meter REST API endpoints.
Provides real-time tracking of pizza delivery/order volume and foot traffic
around the Pentagon and Arlington, VA defense corridor as an unconventional
OSINT indicator for potential late-night crisis activations.
"""

from datetime import datetime, timedelta, timezone
import logging
from typing import Any, Dict, List, Literal, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from backend.app.auth import require_api_key
from backend.app.db.models import Event, utc_now
from backend.app.db.session import get_db_session

logger = logging.getLogger("osint.api.pizza_meter")

router = APIRouter(prefix="/api/pizza-meter", tags=["PizzaMeter"])

# In-memory simulation override (for testing, demos, and adversarial evaluation)
_simulated_override: Optional[Dict[str, Any]] = None


class PizzaLocation(BaseModel):
    name: str
    address: str
    busyness: int = Field(ge=0, le=100)
    status: Literal["Normal", "Elevated", "Spike"]
    distance: str


class PizzaMeterResponse(BaseModel):
    status: Literal["NOMINAL", "ELEVATED", "HIGH", "CRITICAL"]
    level: int = Field(ge=1, le=4)
    score: int = Field(ge=0, le=100)
    trend: Literal["stable", "increasing", "decreasing"]
    baseline_multiplier: float
    description: str
    monitored_region: str
    last_updated: str
    locations: List[PizzaLocation]
    history: List[Dict[str, Any]]


class SimulateRequest(BaseModel):
    level: Optional[int] = Field(None, ge=1, le=4)
    status: Optional[Literal["NOMINAL", "ELEVATED", "HIGH", "CRITICAL"]] = None
    score: Optional[int] = Field(None, ge=0, le=100)
    trend: Optional[Literal["stable", "increasing", "decreasing"]] = None
    reset: Optional[bool] = False


def _get_base_locations(score: int) -> List[Dict[str, Any]]:
    """Generate monitored location activity based on overall busyness score."""
    # Slight variances across the 3 key Arlington Pentagon-area outlets
    dominos_busy = max(5, min(100, int(score * 0.95 + 2)))
    papajohns_busy = max(5, min(100, int(score * 1.05 - 1)))
    pizzahut_busy = max(5, min(100, int(score * 0.90 + 1)))

    def _loc_status(b: int) -> str:
        if b >= 75:
            return "Spike"
        elif b >= 45:
            return "Elevated"
        return "Normal"

    return [
        {
            "name": "Domino's Pizza (Pentagon City)",
            "address": "1100 S Joyce St, Arlington, VA",
            "busyness": dominos_busy,
            "status": _loc_status(dominos_busy),
            "distance": "0.8 mi from Pentagon",
        },
        {
            "name": "Papa John's (Columbia Pike)",
            "address": "2901 Columbia Pike, Arlington, VA",
            "busyness": papajohns_busy,
            "status": _loc_status(papajohns_busy),
            "distance": "1.2 mi from Pentagon",
        },
        {
            "name": "Pizza Hut (Arlington)",
            "address": "3100 Clarendon Blvd, Arlington, VA",
            "busyness": pizzahut_busy,
            "status": _loc_status(pizzahut_busy),
            "distance": "1.5 mi from Pentagon",
        },
    ]


async def calculate_pizza_meter_status(session: Optional[AsyncSession] = None) -> Dict[str, Any]:
    """
    Calculate dynamic pizza meter status based on Arlington local time
    and correlation with high-priority OSINT events.
    """
    global _simulated_override
    if _simulated_override is not None:
        return _simulated_override

    now_utc = utc_now()
    # Arlington, VA is in US Eastern Time (UTC-4 during EDT)
    arlington_hour = (now_utc.hour - 4) % 24

    # Baseline calculation based on time of day in Arlington
    is_late_night = arlington_hour >= 21 or arlington_hour < 4
    base_score = 16 if not is_late_night else 24

    # Correlate with recent high-importance events (last 3 hours)
    recent_high_priority_count = 0
    if session is not None:
        try:
            since_3h = now_utc - timedelta(hours=3)
            query = select(func.count(Event.id)).where(
                Event.ingested_at >= since_3h,
                Event.importance_score >= 8,
            )
            result = await session.execute(query)
            recent_high_priority_count = result.scalar_one_or_none() or 0
        except Exception as exc:
            logger.warning(f"Error checking recent events for pizza meter: {exc}")

    # Elevate score if high-priority events are occurring
    if recent_high_priority_count >= 5:
        score = min(95, base_score + 65)
        trend = "increasing"
    elif recent_high_priority_count >= 2:
        score = min(75, base_score + 40)
        trend = "increasing"
    elif recent_high_priority_count == 1:
        score = min(50, base_score + 20)
        trend = "increasing"
    else:
        score = base_score
        trend = "stable"

    if score >= 80:
        level = 4
        status_label = "CRITICAL"
        multiplier = 3.2
        description = "DEFCON Pizza: Unusually high late-night delivery surge across Pentagon corridor."
    elif score >= 55:
        level = 3
        status_label = "HIGH"
        multiplier = 2.1
        description = "High order volume detected in Arlington defense corridor. Elevated activity."
    elif score >= 30:
        level = 2
        status_label = "ELEVATED"
        multiplier = 1.4
        description = "Moderate elevation above typical baseline order patterns."
    else:
        level = 1
        status_label = "NOMINAL"
        multiplier = 1.05
        description = "Baseline order activity in Pentagon & Arlington DoD corridor."

    # Generate synthetic 6-hour trend points for sparkline/history
    history = []
    for i in range(4, 0, -1):
        hist_time = (now_utc - timedelta(hours=i * 2)).replace(minute=0, second=0, microsecond=0)
        hist_score = max(10, score - (i * 3))
        history.append({"timestamp": hist_time.isoformat(), "score": hist_score})
    history.append({"timestamp": now_utc.isoformat(), "score": score})

    return {
        "status": status_label,
        "level": level,
        "score": score,
        "trend": trend,
        "baseline_multiplier": multiplier,
        "description": description,
        "monitored_region": "Arlington / Pentagon DoD Corridor, VA",
        "last_updated": now_utc.isoformat(),
        "locations": _get_base_locations(score),
        "history": history,
    }


@router.get("", response_model=PizzaMeterResponse, dependencies=[Depends(require_api_key)])
async def get_pizza_meter(
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """Retrieve current Pentagon Pizza Meter telemetry and status."""
    return await calculate_pizza_meter_status(session)


@router.post("/simulate", response_model=PizzaMeterResponse, dependencies=[Depends(require_api_key)])
async def simulate_pizza_meter(
    payload: SimulateRequest,
) -> Dict[str, Any]:
    """Override or reset pizza meter state for testing and demonstration."""
    global _simulated_override

    if payload.reset:
        _simulated_override = None
        return await calculate_pizza_meter_status()

    level_map = {
        1: ("NOMINAL", 18, 1.05, "Baseline order activity in Pentagon & Arlington DoD corridor."),
        2: ("ELEVATED", 42, 1.4, "Moderate elevation above typical baseline order patterns."),
        3: ("HIGH", 68, 2.1, "High order volume detected in Arlington defense corridor."),
        4: ("CRITICAL", 88, 3.2, "DEFCON Pizza: Unusually high late-night delivery surge across Pentagon corridor."),
    }

    target_level = payload.level or 1
    if payload.status:
        status_to_level = {"NOMINAL": 1, "ELEVATED": 2, "HIGH": 3, "CRITICAL": 4}
        target_level = status_to_level.get(payload.status, target_level)

    status_label, default_score, multiplier, desc = level_map[target_level]
    score = payload.score if payload.score is not None else default_score
    trend = payload.trend or ("increasing" if target_level >= 3 else "stable")

    now_utc = utc_now()
    history = [
        {"timestamp": (now_utc - timedelta(hours=6)).isoformat(), "score": max(10, score - 20)},
        {"timestamp": (now_utc - timedelta(hours=4)).isoformat(), "score": max(10, score - 10)},
        {"timestamp": (now_utc - timedelta(hours=2)).isoformat(), "score": max(10, score - 5)},
        {"timestamp": now_utc.isoformat(), "score": score},
    ]

    _simulated_override = {
        "status": status_label,
        "level": target_level,
        "score": score,
        "trend": trend,
        "baseline_multiplier": multiplier,
        "description": desc,
        "monitored_region": "Arlington / Pentagon DoD Corridor, VA",
        "last_updated": now_utc.isoformat(),
        "locations": _get_base_locations(score),
        "history": history,
    }

    return _simulated_override
