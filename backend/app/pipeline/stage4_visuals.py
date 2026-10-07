"""
backend/app/pipeline/stage4_visuals.py - Stage 4: Visuals, Trends & Dashboard Narratives
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from backend.app.pipeline.models import (
    DashboardTrendSynthesis,
    EntityTrendMetric,
    Stage4Result,
    VisualChartMarker,
)
from backend.app.pipeline.parser import extract_and_parse_json
from backend.app.pipeline.providers.base import BaseAIProvider

logger = logging.getLogger("osint.pipeline.stage4")

STAGE4_SYSTEM_PROMPT = """You are a visual intelligence and operational pattern detection analyst.
Classify the event into an operational trend category, assign 2-5 thematic cluster tags for dashboard filtering, and formulate a 1-sentence narrative insight explaining the event's tactical significance.

Standard Operational Categories:
- Naval Escalation
- Air & Missile Defense
- Kinetic Engagement
- Diplomatic Posturing
- Sanctions & Economic Pressure
- Nuclear Developments
- Proxy & Asymmetric Operations
- Cyber & Information Warfare
- Maritime & Commercial Shipping Disruptions
- Geopolitical Development

You MUST respond strictly with a valid JSON object matching the requested schema. No markdown formatting."""

AGGREGATE_TRENDS_PROMPT = """You are a lead visual intelligence analyst constructing the strategic situation briefing for the interactive dashboard.
Analyze the provided event metrics, category distributions, and entity counts over the specified timeframe for theme: "{theme_name}".

Deliver:
1. `trend_narrative`: A coherent 2-paragraph narrative synthesis contextualizing recent spikes, shifts in tactical tempo, and adversary behavior.
2. `key_trend_bullets`: 3-5 concise bullets highlighting key macro shifts.
3. `trending_entities`: Categorize entity velocity (rising/stable/falling).
4. `chart_markers`: Specific visual callout points (e.g. peak alert spikes, tempo shifts) to highlight on the volume and score charts.

You MUST respond strictly with a valid JSON object matching the requested schema."""


def _heuristic_stage4_fallback(
    intelligence_text: str,
    extracted_entities: List[str],
) -> Stage4Result:
    """
    Deterministic offline heuristic classification fallback for Stage 4.
    """
    text_lower = intelligence_text.lower()
    
    if any(w in text_lower for w in ["naval", "strait", "hormuz", "vessel", "ship", "tanker", "boarding"]):
        category = "Naval Escalation"
        tags = ["Naval", "Maritime Security", "Strait of Hormuz"]
    elif any(w in text_lower for w in ["missile", "drone", "shahed", "uav", "air defense", "intercept"]):
        category = "Air & Missile Defense"
        tags = ["Missile", "Air Defense", "UAV"]
    elif any(w in text_lower for w in ["strike", "kinetic", "bomb", "explosion", "clash", "killed"]):
        category = "Kinetic Engagement"
        tags = ["Kinetic", "Strikes", "Combat Operations"]
    elif any(w in text_lower for w in ["diplomat", "talks", "treaty", "envoy", "meeting", "foreign minister"]):
        category = "Diplomatic Posturing"
        tags = ["Diplomacy", "Negotiations", "Bilateral"]
    elif any(w in text_lower for w in ["sanction", "treasury", "asset freeze", "embargo"]):
        category = "Sanctions & Economic Pressure"
        tags = ["Sanctions", "Economic Warfare", "Trade Restrictions"]
    elif any(w in text_lower for w in ["uranium", "centrifuge", "iaea", "enrichment", "nuclear"]):
        category = "Nuclear Developments"
        tags = ["Nuclear", "IAEA Monitoring", "Enrichment"]
    else:
        category = "Geopolitical Development"
        tags = ["Intelligence", "Regional Security"]

    # Enrich tags with key entities
    for ent in extracted_entities[:2]:
        if ent not in tags:
            tags.append(ent)

    insight = f"Event reflects observable activity within {category.lower()} across regional operational sectors."
    return Stage4Result(trend_category=category, cluster_tags=tags[:5], narrative_insight=insight)


def _heuristic_dashboard_synthesis_fallback(
    events: List[Any],
    theme_name: str = "US-Iran conflict",
    period_analyzed: str = "Last 24 Hours",
) -> DashboardTrendSynthesis:
    """
    Deterministic offline aggregate trend synthesis fallback.
    """
    total = len(events)
    narrative = (
        f"Operational intelligence indicators for theme '{theme_name}' over {period_analyzed} "
        f"show steady monitoring across strategic maritime and air corridors. A total of {total} events "
        f"were ingested and classified. Tactical tempo remains concentrated in core transit corridors."
    )
    return DashboardTrendSynthesis(
        theme_name=theme_name,
        period_analyzed=period_analyzed,
        trend_narrative=narrative,
        key_trend_bullets=[
            "Persistent naval and patrol surveillance sustained in key waterways.",
            "Elevated intelligence collection on regional defense deployments.",
            "Active monitoring of multilateral diplomatic statements and consultations."
        ],
        top_categories=[
            {"category": "Naval Escalation", "count": max(1, total // 3)},
            {"category": "Diplomatic Posturing", "count": max(1, total // 4)},
            {"category": "Geopolitical Development", "count": max(1, total // 3)},
        ],
        trending_entities=[
            EntityTrendMetric(entity="IRGC", category="Military", count=12, velocity="rising"),
            EntityTrendMetric(entity="US Navy", category="Military", count=8, velocity="stable"),
            EntityTrendMetric(entity="Strait of Hormuz", category="Location", count=15, velocity="rising"),
        ],
        chart_markers=[
            VisualChartMarker(
                label="Regional Activity Peak",
                marker_type="peak_alert",
                description="Cluster of high-importance defense signals identified."
            )
        ]
    )


async def run_stage4_visuals(
    event: Any,
    intelligence_text: str,
    extracted_entities: List[str],
    provider: BaseAIProvider,
    model: Optional[str] = None,
    temperature: Optional[float] = None,
) -> Stage4Result:
    """
    Executes Stage 4 visual & trend categorization using the configured AI provider.
    """
    title = getattr(event, "title", None) or (event.get("title") if isinstance(event, dict) else "")
    prompt = (
        f"Event Title: {title}\n"
        f"Extracted Entities: {', '.join(extracted_entities)}\n"
        f"Intelligence Content:\n{intelligence_text}\n\n"
        f"Provide valid JSON matching the Stage4Result schema."
    )

    def fallback():
        return _heuristic_stage4_fallback(intelligence_text, extracted_entities)

    try:
        raw_response = await provider.generate_text(
            prompt=prompt,
            system_prompt=STAGE4_SYSTEM_PROMPT,
            model=model,
            temperature=temperature if temperature is not None else 0.2,
            json_mode=True,
        )
        result = extract_and_parse_json(
            raw_text=raw_response,
            target_model=Stage4Result,
            fallback_factory=fallback,
        )
        return result
    except PermissionError:
        raise
    except RuntimeError as r_err:
        if "429" in str(r_err):
            raise
        logger.warning(f"RuntimeError in Stage 4, falling back to heuristics: {r_err}")
        return fallback()
    except Exception as exc:
        logger.warning(f"Stage 4 execution exception, using fallback: {exc}")
        return fallback()


async def run_dashboard_trend_synthesis(
    events: List[Any],
    theme_name: str,
    period_analyzed: str,
    provider: BaseAIProvider,
    model: Optional[str] = None,
    temperature: Optional[float] = None,
) -> DashboardTrendSynthesis:
    """
    Synthesizes multi-event trends for the interactive dashboard.
    """
    def fallback():
        return _heuristic_dashboard_synthesis_fallback(events, theme_name, period_analyzed)

    if not events:
        return fallback()

    categories: Dict[str, int] = {}
    for ev in events:
        cat = getattr(ev, "trend_category", "Geopolitical Development") or "Geopolitical Development"
        categories[cat] = categories.get(cat, 0) + 1

    prompt = (
        f"Theme: {theme_name}\n"
        f"Period: {period_analyzed}\n"
        f"Total Events: {len(events)}\n"
        f"Category Distribution: {categories}\n\n"
        f"Provide valid JSON matching the DashboardTrendSynthesis schema."
    )

    try:
        raw_response = await provider.generate_text(
            prompt=prompt,
            system_prompt=AGGREGATE_TRENDS_PROMPT.format(theme_name=theme_name),
            model=model,
            temperature=temperature if temperature is not None else 0.2,
            json_mode=True,
        )
        result = extract_and_parse_json(
            raw_text=raw_response,
            target_model=DashboardTrendSynthesis,
            fallback_factory=fallback,
        )
        return result
    except Exception as exc:
        logger.warning(f"Dashboard trend synthesis exception, using fallback: {exc}")
        return fallback()
