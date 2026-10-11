"""
Pentagon Pizza Meter REST API endpoints.
Provides real-time tracking of pizza delivery/order volume and foot traffic
around the Pentagon and Arlington, VA defense corridor as an unconventional
OSINT indicator for potential late-night crisis activations.

Integrates live telemetry and Google Maps Popular Times baselines from
PizzINT (https://pizzint.watch/api/dashboard-data) across all 6 monitored
Pentagon corridor pizzerias, with a calibrated hourly baseline and strict
OSINT flash-crisis fallback when live store popularity is unavailable.
"""

from datetime import datetime, timedelta, timezone
import logging
import time
from typing import Any, Dict, List, Literal, Optional, Tuple
import httpx
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from backend.app.auth import require_api_key
from backend.app.db.models import Event, SystemSettings, utc_now
from backend.app.db.session import get_db_session

logger = logging.getLogger("osint.api.pizza_meter")

router = APIRouter(prefix="/api/pizza-meter", tags=["PizzaMeter"])

# In-memory simulation override (for testing, demos, and adversarial evaluation)
_simulated_override: Optional[Dict[str, Any]] = None

# In-memory cache for live pizzint.watch telemetry
PIZZINT_API_URL = "https://pizzint.watch/api/dashboard-data"
PIZZINT_CACHE_TTL_SECONDS = 180.0
_pizzint_cache_data: Optional[Dict[str, Any]] = None
_pizzint_cache_ts: float = 0.0

# All 6 real-world Pentagon Corridor pizzerias tracked by PizzINT
MONITORED_OUTLETS: List[Dict[str, Any]] = [
    {
        "name": "Domino's Pizza (Pentagon Closest)",
        "place_id": "ChIJI6ACK7q2t4kRFcPtFhUuYhU",
        "address": "2602 Columbia Pike, Arlington, VA",
        "distance": "1.7 mi from Pentagon",
        "variance_factor": 0.98,
        "variance_offset": 1,
    },
    {
        "name": "Papa John's (Columbia Pike)",
        "place_id": "ChIJo03BaX-3t4kRbyhPM0rTuqM",
        "address": "3205 Columbia Pike, Arlington, VA",
        "distance": "2.1 mi from Pentagon",
        "variance_factor": 1.03,
        "variance_offset": 0,
    },
    {
        "name": "Extreme Pizza (Pentagon City)",
        "place_id": "ChIJcYireCe3t4kR4d9trEbGYjc",
        "address": "1419 S Fern St, Arlington, VA",
        "distance": "0.8 mi from Pentagon",
        "variance_factor": 0.95,
        "variance_offset": 2,
    },
    {
        "name": "We, The Pizza (Crystal City)",
        "place_id": "ChIJS1rpOC-3t4kRsLyM6aftM8k",
        "address": "2100 Crystal Dr, Arlington, VA",
        "distance": "1.2 mi from Pentagon",
        "variance_factor": 1.00,
        "variance_offset": -1,
    },
    {
        "name": "District Pizza Palace",
        "place_id": "ChIJ42QeLXu3t4kRnArvcaz2o3A",
        "address": "2325 S Eads St, Arlington, VA",
        "distance": "1.3 mi from Pentagon",
        "variance_factor": 0.92,
        "variance_offset": 1,
    },
    {
        "name": "Pizzato Pizza",
        "place_id": "ChIJrbin_Qm3t4kRVSytw_2DM1g",
        "address": "3001 Washington Blvd, Arlington, VA",
        "distance": "1.9 mi from Pentagon",
        "variance_factor": 0.96,
        "variance_offset": 0,
    },
]

# Calibrated 24-hour normal busyness baseline (0:00 to 23:00 Arlington, VA local time / US Eastern)
# Derived from Google Maps Popular Times profiles across the 6 monitored Pentagon corridor outlets.
# Hours: 00  01  02  03  04  05  06  07  08  09  10  11  12  13  14  15  16  17  18  19  20  21  22  23
HOURLY_NORMAL_BUSYNESS: Dict[str, List[int]] = {
    "Domino's Pizza (Pentagon Closest)": [
        14, 9, 6, 5, 5, 5, 5, 5, 5, 12, 32, 46, 62, 58, 42, 38, 44, 52, 74, 82, 68, 52, 38, 24
    ],
    "Domino's Pizza (Pentagon City)": [
        12, 8, 5, 5, 5, 5, 5, 5, 5, 10, 25, 45, 68, 62, 35, 25, 28, 52, 75, 82, 68, 48, 30, 18
    ],
    "Papa John's (Columbia Pike)": [
        12, 7, 5, 5, 5, 5, 5, 5, 5, 10, 28, 42, 58, 54, 42, 46, 52, 58, 72, 78, 64, 48, 34, 22
    ],
    "Extreme Pizza (Pentagon City)": [
        6, 5, 5, 5, 5, 5, 5, 5, 5, 8, 18, 36, 44, 46, 44, 48, 54, 60, 68, 76, 62, 48, 22, 10
    ],
    "We, The Pizza (Crystal City)": [
        6, 5, 5, 5, 5, 5, 5, 5, 5, 12, 42, 62, 78, 76, 66, 60, 64, 76, 82, 80, 62, 42, 15, 8
    ],
    "District Pizza Palace": [
        12, 6, 5, 5, 5, 5, 5, 5, 5, 8, 18, 32, 38, 36, 36, 34, 38, 46, 56, 66, 60, 54, 46, 34
    ],
    "Pizzato Pizza": [
        24, 18, 10, 8, 5, 5, 5, 5, 5, 8, 16, 42, 50, 50, 52, 48, 52, 58, 64, 68, 58, 52, 50, 44
    ],
    "Pizza Hut (Arlington)": [
        8, 5, 5, 5, 5, 5, 5, 5, 5, 8, 18, 38, 58, 52, 28, 20, 22, 45, 68, 74, 56, 36, 22, 12
    ],
}


def get_arlington_time_and_day(dt: Optional[datetime] = None) -> Tuple[int, int]:
    """
    Return (arlington_hour, google_day_of_week) for Arlington, VA (US Eastern Time).
    google_day_of_week matches pizzint.watch / JS Date.getDay(): 0=Sunday, 1=Monday, ..., 6=Saturday.
    """
    if dt is None:
        try:
            from zoneinfo import ZoneInfo
            local_dt = datetime.now(ZoneInfo("America/New_York"))
        except Exception:
            local_dt = utc_now() - timedelta(hours=4)
    else:
        try:
            from zoneinfo import ZoneInfo
            local_dt = dt.astimezone(ZoneInfo("America/New_York"))
        except Exception:
            local_dt = dt - timedelta(hours=4)

    hour = local_dt.hour
    # Python weekday(): Monday=0..Sunday=6 -> convert to JS getDay(): Sunday=0..Saturday=6
    js_day = (local_dt.weekday() + 1) % 7
    return hour, js_day


def get_arlington_hour(dt: Optional[datetime] = None) -> int:
    """Get local Arlington, VA hour (US Eastern Time with DST awareness)."""
    hour, _ = get_arlington_time_and_day(dt)
    return hour


def get_hourly_normal(
    location_name: str,
    hour: int,
    pizzint_baseline_times: Optional[Dict[str, List[Dict[str, Any]]]] = None,
    js_day: Optional[int] = None,
) -> int:
    """
    Retrieve normal business baseline percentage for a location at given hour.
    Uses live Google Maps day-of-week Popular Times baseline when available and positive,
    falling back to the calibrated 24-hour profile.
    """
    h = hour % 24
    if pizzint_baseline_times and js_day is not None:
        # PizzINT uses previous day's late-night schedule for hours < 4 AM
        lookup_day = str((js_day + 6) % 7 if h < 4 else js_day)
        day_entries = pizzint_baseline_times.get(lookup_day) or []
        for entry in day_entries:
            if isinstance(entry, dict) and entry.get("hour") == h:
                pop = entry.get("popularity")
                if isinstance(pop, (int, float)) and pop > 0:
                    return int(max(5, min(100, round(pop))))

    profile = HOURLY_NORMAL_BUSYNESS.get(location_name)
    if not profile:
        # Fuzzy match by brand keyword
        lower_name = location_name.lower()
        for k, v in HOURLY_NORMAL_BUSYNESS.items():
            if k.lower().split("(")[0].strip() in lower_name:
                profile = v
                break
    if not profile:
        profile = [10, 6, 5, 5, 5, 5, 5, 5, 5, 10, 20, 40, 60, 55, 30, 22, 25, 50, 70, 75, 60, 42, 26, 15]
    return profile[h]


def evaluate_location_status(busyness: int, normal_busyness: int) -> Literal["Normal", "Elevated", "Spike"]:
    """
    Calibrate location alert level against normal business for this hour.
    - Spike: current volume surges abnormally above normal (>=1.75x ratio and +25% delta, or +40% absolute delta).
    - Elevated: moderate elevation above normal (>=1.3x ratio and +12% delta, or +20% absolute delta).
    - Normal: busyness aligns with expected hourly baseline (e.g. 78% during dinner rush is Normal).
    """
    delta = busyness - normal_busyness
    floor = max(normal_busyness, 8)
    ratio = busyness / floor

    if (ratio >= 1.75 and delta >= 25) or delta >= 40:
        return "Spike"
    elif (ratio >= 1.30 and delta >= 12) or delta >= 20:
        return "Elevated"
    return "Normal"


async def fetch_pizzint_dashboard_data(force: bool = False) -> Optional[Dict[str, Any]]:
    """
    Fetch real-time Pentagon Pizza Index telemetry from pizzint.watch/api/dashboard-data
    with a short timeout and in-memory TTL cache.
    """
    global _pizzint_cache_data, _pizzint_cache_ts
    now_mono = time.monotonic()
    if not force and _pizzint_cache_data is not None and (now_mono - _pizzint_cache_ts) < PIZZINT_CACHE_TTL_SECONDS:
        return _pizzint_cache_data

    try:
        async with httpx.AsyncClient(timeout=3.0, follow_redirects=True) as client:
            resp = await client.get(
                PIZZINT_API_URL,
                headers={"User-Agent": "RealtimeOSINT-PizzaMeter/1.0"},
            )
            if resp.status_code == 200:
                payload = resp.json()
                if isinstance(payload, dict) and isinstance(payload.get("data"), list):
                    _pizzint_cache_data = payload
                    _pizzint_cache_ts = now_mono
                    return payload
    except Exception as exc:
        logger.debug(f"Live PizzINT fetch skipped/unavailable (using calibrated baseline): {exc}")

    return _pizzint_cache_data


class PizzaLocation(BaseModel):
    name: str
    address: str
    busyness: int = Field(ge=0, le=100)
    normal_busyness: int = Field(default=20, ge=0, le=100)
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
    enabled: bool = True
    hourly_calibrated: bool = True
    arlington_hour: Optional[int] = None


class SimulateRequest(BaseModel):
    level: Optional[int] = Field(None, ge=1, le=4)
    status: Optional[Literal["NOMINAL", "ELEVATED", "HIGH", "CRITICAL"]] = None
    score: Optional[int] = Field(None, ge=0, le=100)
    trend: Optional[Literal["stable", "increasing", "decreasing"]] = None
    hour: Optional[int] = Field(None, ge=0, le=23)
    reset: Optional[bool] = False


class TogglePizzaMeterRequest(BaseModel):
    enabled: bool


def _get_base_locations(
    score: int,
    hour: Optional[int] = None,
    raw_location_busyness: Optional[Dict[str, int]] = None,
    pizzint_stores_by_place_id: Optional[Dict[str, Dict[str, Any]]] = None,
    js_day: Optional[int] = None,
    surge_delta: int = 0,
) -> List[Dict[str, Any]]:
    """
    Generate monitored location activity across all 6 Pentagon corridor outlets,
    calibrated against normal business per hour and live PizzINT store telemetry.
    """
    if hour is None:
        hour, inferred_day = get_arlington_time_and_day()
        if js_day is None:
            js_day = inferred_day

    locations = []
    now_utc = utc_now()

    for outlet in MONITORED_OUTLETS:
        name = outlet["name"]
        place_id = outlet.get("place_id", "")
        live_store = (pizzint_stores_by_place_id or {}).get(place_id)

        # 1. Determine normal hourly baseline for this outlet
        bpt = live_store.get("baseline_popular_times") if isinstance(live_store, dict) else None
        norm = get_hourly_normal(name, hour, pizzint_baseline_times=bpt, js_day=js_day)

        # 2. Determine current busyness for this outlet
        live_pop: Optional[int] = None
        if isinstance(live_store, dict):
            cur_pop = live_store.get("current_popularity")
            if isinstance(cur_pop, (int, float)) and cur_pop > 0:
                live_pop = int(round(cur_pop))
            else:
                # Check if sparkline_24h has a fresh reading within the last 75 minutes
                sparkline = live_store.get("sparkline_24h") or []
                for pt in reversed(sparkline):
                    pt_pop = pt.get("current_popularity")
                    pt_rec = pt.get("recorded_at")
                    if isinstance(pt_pop, (int, float)) and pt_pop > 0 and pt_rec:
                        try:
                            rec_dt = datetime.fromisoformat(str(pt_rec).replace("Z", "+00:00"))
                            if (now_utc - rec_dt).total_seconds() <= 4500:
                                live_pop = int(round(pt_pop))
                            break
                        except Exception:
                            break

        if raw_location_busyness and name in raw_location_busyness:
            busy = max(5, min(100, raw_location_busyness[name]))
        elif live_pop is not None:
            busy = max(5, min(100, live_pop))
        elif pizzint_stores_by_place_id is not None:
            # When using live/hybrid mode without a current live_pop reading for this store,
            # anchor to this specific store's normal baseline + any calibrated crisis surge_delta
            busy = max(5, min(100, int(round(norm + surge_delta))))
        else:
            # Deterministic score-based calculation (used by simulation and unit tests)
            vf = outlet.get("variance_factor", 1.0)
            vo = outlet.get("variance_offset", 0)
            busy = max(5, min(100, int(round(score * vf + vo))))

        loc_status = evaluate_location_status(busy, norm)
        if isinstance(live_store, dict) and live_store.get("is_spike") is True:
            loc_status = "Spike"

        locations.append({
            "name": name,
            "address": outlet["address"],
            "busyness": busy,
            "normal_busyness": norm,
            "status": loc_status,
            "distance": outlet["distance"],
        })
    return locations


async def calculate_pizza_meter_status(
    session: Optional[AsyncSession] = None,
    hour_override: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Calculate dynamic pizza meter status calibrated against Arlington local time
    hourly baseline, live PizzINT telemetry, and correlation with breaking flash OSINT events.
    """
    global _simulated_override
    if _simulated_override is not None:
        return _simulated_override

    now_utc = utc_now()
    arlington_hour, js_day = get_arlington_time_and_day()
    if hour_override is not None:
        arlington_hour = hour_override

    # Read toggle setting if database session is active
    is_enabled = True
    if session is not None:
        try:
            res = await session.execute(
                select(SystemSettings).where(SystemSettings.key == "pizza_meter_enabled")
            )
            row = res.scalars().first()
            if row and row.value_json:
                is_enabled = (
                    row.value_json.get("enabled", True)
                    if isinstance(row.value_json, dict)
                    else bool(row.value_json)
                )
        except Exception as exc:
            logger.warning(f"Error checking pizza_meter_enabled setting: {exc}")

    # 1. Fetch live PizzINT dashboard telemetry (when not using a manual hour_override)
    pizzint_payload = await fetch_pizzint_dashboard_data() if hour_override is None else None
    pizzint_stores_by_place_id: Optional[Dict[str, Dict[str, Any]]] = None
    pizzint_quiet = False
    pizzint_active_spikes = 0

    if isinstance(pizzint_payload, dict) and isinstance(pizzint_payload.get("data"), list):
        pizzint_stores_by_place_id = {
            str(item.get("place_id")): item
            for item in pizzint_payload["data"]
            if isinstance(item, dict) and item.get("place_id")
        }
        pizzint_active_spikes = int(pizzint_payload.get("active_spikes") or 0)
        defcon_lvl = int(pizzint_payload.get("defcon_level") or 5)
        if defcon_lvl >= 5 and pizzint_active_spikes == 0:
            pizzint_quiet = True

    # 2. Check for genuine breaking flash crisis events in the last 90 minutes
    # Uses BOTH published_at and ingested_at within 90 minutes and importance_score >= 9
    # so historical articles scraped during feed backfills never trigger false alerts.
    breaking_flash_count = 0
    if session is not None:
        try:
            since_90m = now_utc - timedelta(minutes=90)
            query = select(func.count(Event.id)).where(
                Event.published_at >= since_90m,
                Event.ingested_at >= since_90m,
                Event.importance_score >= 9,
            )
            result = await session.execute(query)
            breaking_flash_count = result.scalar_one_or_none() or 0
        except Exception as exc:
            logger.warning(f"Error checking breaking events for pizza meter: {exc}")

    # 3. Calibrate surge_delta above hourly normal baseline
    if pizzint_quiet and breaking_flash_count < 3:
        # Live PizzINT confirms DOUGHCON 5 (quiet, 0 spikes) and no major kinetic outbreak
        surge_delta = 0
        trend: Literal["stable", "increasing", "decreasing"] = "stable"
    elif breaking_flash_count >= 5:
        surge_delta = 38
        trend = "increasing"
    elif breaking_flash_count >= 3:
        surge_delta = 24
        trend = "increasing"
    elif breaking_flash_count >= 1 and not pizzint_quiet:
        surge_delta = 12
        trend = "increasing"
    else:
        surge_delta = 0
        trend = "stable"

    # Calculate expected normal baseline across all 6 outlets for this hour
    temp_norms = [
        get_hourly_normal(
            outlet["name"],
            arlington_hour,
            pizzint_baseline_times=(
                pizzint_stores_by_place_id.get(outlet["place_id"], {}).get("baseline_popular_times")
                if pizzint_stores_by_place_id
                else None
            ),
            js_day=js_day if hour_override is None else None,
        )
        for outlet in MONITORED_OUTLETS
    ]
    avg_normal = sum(temp_norms) / len(temp_norms)
    target_score = min(98, max(5, int(round(avg_normal + surge_delta))))

    # 4. Compute calibrated per-location activity across all 6 outlets
    locations = _get_base_locations(
        score=target_score,
        hour=arlington_hour,
        pizzint_stores_by_place_id=pizzint_stores_by_place_id,
        js_day=js_day if hour_override is None else None,
        surge_delta=surge_delta,
    )

    avg_current = sum(l["busyness"] for l in locations) / len(locations)
    avg_norm = sum(l["normal_busyness"] for l in locations) / len(locations)
    current_score = max(5, min(100, int(round(avg_current))))

    floor = max(avg_norm, 8.0)
    # True mathematical ratio of current activity vs. normal hourly baseline (never artificially inflated)
    multiplier = round(max(1.0 if surge_delta == 0 and abs(avg_current - avg_norm) < 2 else 0.5, avg_current / floor), 2)
    calibrated_delta = avg_current - avg_norm
    spike_count = sum(1 for l in locations if l["status"] == "Spike")

    if spike_count >= 2 or calibrated_delta >= 15:
        trend = "increasing"
    elif calibrated_delta <= -12:
        trend = "decreasing"

    # 5. Calibrate alert thresholds based on true multiplier and spike breadth
    is_late_night = arlington_hour >= 21 or arlington_hour < 5
    if (
        multiplier >= 2.20
        or (is_late_night and multiplier >= 1.85 and calibrated_delta >= 25)
        or (spike_count >= 3 and multiplier >= 1.85)
    ):
        level = 4
        status_label = "CRITICAL"
        description = (
            f"DEFCON Pizza: Unusually high late-night delivery surge across Pentagon corridor "
            f"({multiplier:.1f}x above normal {arlington_hour:02d}:00 EDT baseline)."
            if is_late_night
            else f"DEFCON Pizza: Unprecedented delivery surge across Pentagon corridor ({multiplier:.1f}x normal baseline)."
        )
    elif multiplier >= 1.60 or (calibrated_delta >= 28 and multiplier >= 1.45) or spike_count >= 2:
        level = 3
        status_label = "HIGH"
        description = (
            f"High order volume detected in Arlington defense corridor "
            f"({multiplier:.1f}x above typical {arlington_hour:02d}:00 EDT baseline)."
        )
    elif multiplier >= 1.25 or calibrated_delta >= 12 or spike_count >= 1:
        level = 2
        status_label = "ELEVATED"
        description = (
            f"Moderate elevation ({multiplier:.1f}x) above typical {arlington_hour:02d}:00 EDT "
            f"baseline order patterns."
        )
    else:
        level = 1
        status_label = "NOMINAL"
        description = (
            f"Baseline order activity across 6 Pentagon & Arlington DoD corridor pizzerias "
            f"(in line with normal {arlington_hour:02d}:00 EDT baseline)."
        )

    # 6. Build 6-hour trend history points calibrated to previous hourly periods
    history = []
    for i in range(4, 0, -1):
        hist_time = (now_utc - timedelta(hours=i * 2)).replace(minute=0, second=0, microsecond=0)
        hist_hour = (arlington_hour - (i * 2)) % 24
        hist_norms = [
            get_hourly_normal(
                outlet["name"],
                hist_hour,
                pizzint_baseline_times=(
                    pizzint_stores_by_place_id.get(outlet["place_id"], {}).get("baseline_popular_times")
                    if pizzint_stores_by_place_id
                    else None
                ),
                js_day=js_day if hour_override is None else None,
            )
            for outlet in MONITORED_OUTLETS
        ]
        hist_norm_avg = sum(hist_norms) / len(hist_norms)
        hist_score = max(5, min(100, int(round(hist_norm_avg + max(0, surge_delta - (i * 8))))))
        history.append({"timestamp": hist_time.isoformat(), "score": hist_score})
    history.append({"timestamp": now_utc.isoformat(), "score": current_score})

    return {
        "status": status_label,
        "level": level,
        "score": current_score,
        "trend": trend,
        "baseline_multiplier": multiplier,
        "description": description,
        "monitored_region": "Arlington / Pentagon DoD Corridor, VA",
        "last_updated": now_utc.isoformat(),
        "locations": locations,
        "history": history,
        "enabled": is_enabled,
        "hourly_calibrated": True,
        "arlington_hour": arlington_hour,
    }


@router.get("", response_model=PizzaMeterResponse, dependencies=[Depends(require_api_key)])
async def get_pizza_meter(
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """Retrieve current Pentagon Pizza Meter telemetry calibrated against hourly baseline."""
    return await calculate_pizza_meter_status(session)


@router.put("/toggle", dependencies=[Depends(require_api_key)])
async def toggle_pizza_meter(
    payload: TogglePizzaMeterRequest,
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """Toggle the Pentagon Pizza Meter feature on or off."""
    res = await session.execute(
        select(SystemSettings).where(SystemSettings.key == "pizza_meter_enabled")
    )
    existing = res.scalars().first()
    val = {"enabled": payload.enabled}
    if existing:
        existing.value_json = val
    else:
        session.add(SystemSettings(key="pizza_meter_enabled", value_json=val))

    await session.commit()
    logger.info(f"Pentagon Pizza Meter toggled: enabled={payload.enabled}")
    return {"status": "ok", "enabled": payload.enabled}


@router.post("/simulate", response_model=PizzaMeterResponse, dependencies=[Depends(require_api_key)])
async def simulate_pizza_meter(
    payload: SimulateRequest,
) -> Dict[str, Any]:
    """Override or reset pizza meter state for testing, demos, and calibration verification."""
    global _simulated_override

    if payload.reset:
        _simulated_override = None
        return await calculate_pizza_meter_status()

    sim_hour = (
        payload.hour
        if payload.hour is not None
        else (2 if (payload.level == 4 or payload.status == "CRITICAL") else get_arlington_hour())
    )

    level_map = {
        1: (
            "NOMINAL",
            18,
            1.05,
            "Baseline order activity across 6 Pentagon & Arlington DoD corridor pizzerias (in line with normal hourly baseline).",
        ),
        2: (
            "ELEVATED",
            42,
            1.4,
            "Moderate elevation (1.4x) above typical hourly baseline order patterns.",
        ),
        3: (
            "HIGH",
            68,
            2.1,
            "High order volume detected in Arlington defense corridor (2.1x above typical hourly baseline).",
        ),
        4: (
            "CRITICAL",
            88,
            3.2,
            "DEFCON Pizza: Unusually high late-night delivery surge across Pentagon corridor (3.2x above normal hourly baseline).",
        ),
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

    if target_level == 4 and multiplier < 3.0:
        multiplier = 3.2

    locations = _get_base_locations(score, hour=sim_hour)

    _simulated_override = {
        "status": status_label,
        "level": target_level,
        "score": score,
        "trend": trend,
        "baseline_multiplier": multiplier,
        "description": desc,
        "monitored_region": "Arlington / Pentagon DoD Corridor, VA",
        "last_updated": now_utc.isoformat(),
        "locations": locations,
        "history": history,
        "enabled": True,
        "hourly_calibrated": True,
        "arlington_hour": sim_hour,
    }

    return _simulated_override
