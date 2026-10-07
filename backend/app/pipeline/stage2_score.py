"""
backend/app/pipeline/stage2_score.py - Stage 2: Importance Scoring with Dynamic Theme Steering
"""

import logging
import re
from typing import Any, Dict, List, Optional

from backend.app.pipeline.models import Stage2Result
from backend.app.pipeline.parser import extract_and_parse_json
from backend.app.pipeline.providers.base import BaseAIProvider

logger = logging.getLogger("osint.pipeline.stage2")


def build_stage2_system_prompt(theme_context: Dict[str, Any]) -> str:
    theme_name = theme_context.get("name", "US-Iran conflict")
    theme_desc = theme_context.get("description", "Monitoring US-Iran regional geopolitical developments")
    keywords = theme_context.get("keywords", [])
    entities = theme_context.get("entities", [])
    threshold = theme_context.get("alert_threshold", theme_context.get("importance_threshold", 7))
    scoring_prompt = theme_context.get(
        "scoring_prompt",
        "Score on a 1-10 scale. Critical military strikes 9-10, proxy/sanctions 7-8, diplomatic 4-6, background 1-3."
    )

    return f"""You are a senior geopolitical intelligence watch officer and threat prioritization analyst.
Your mission is to evaluate the following intelligence event and assign an objective importance score from 1 to 10 based strictly on the user's active theme configuration.

THEME CONFIGURATION:
- Theme Name: {theme_name}
- Theme Description: {theme_desc}
- Tracked Keywords: {', '.join(keywords) if isinstance(keywords, list) else keywords}
- Tracked Entities: {', '.join(entities) if isinstance(entities, list) else entities}
- Notification Threshold: {threshold}

SCORING INSTRUCTIONS (USER CONFIGURATION):
{scoring_prompt}

GENERAL SCALE DEFINITIONS (OVERRIDDEN BY SPECIFIC USER SCORING INSTRUCTIONS):
- Critical (9-10): Direct kinetic strikes, attacks on critical infrastructure/naval/nuclear sites, declared war or breakout.
- High (7-8): Significant proxy attacks, major sanctions, troop/naval deployments, blockades, weapons tests.
- Medium (4-6): Routine diplomatic rhetoric, bilateral talks, non-escalatory military drills, standard policy statements.
- Low (1-3): Background editorial commentary, unrelated noise, minor administrative reports.

RULES:
1. The user's SCORING INSTRUCTIONS take absolute precedence. If the scoring prompt states to score specific events higher or lower, strictly obey that instruction.
2. The `importance_score` MUST be an integer between 1 and 10.
3. Priority level mapping: 1-3 = "low", 4-6 = "medium", 7-8 = "high", 9-10 = "critical".
4. Set `should_alert` to true IF AND ONLY IF `importance_score` >= {threshold}.
5. Provide a 1-2 sentence `score_rationale` explaining the decision.
6. Check which tracked keywords and entities are present and populate `matched_keywords` and `matched_entities`.

You MUST respond strictly with a valid JSON object matching the requested schema. No markdown backticks."""


def _heuristic_stage2_fallback(
    intelligence_text: str,
    extracted_entities: List[str],
    theme_context: Dict[str, Any],
) -> Stage2Result:
    """
    Deterministic offline heuristic scoring fallback.
    Honors active theme scoring prompt steering and alert threshold.
    """
    scoring_prompt = str(theme_context.get("scoring_prompt", "")).lower()
    threshold = int(theme_context.get("alert_threshold", theme_context.get("importance_threshold", 7)))
    text_lower = intelligence_text.lower()
    tracked_keywords = theme_context.get("keywords", [])
    tracked_entities = theme_context.get("entities", [])

    matched_kw = [k for k in tracked_keywords if str(k).lower() in text_lower]
    matched_ent = [e for e in tracked_entities if str(e).lower() in text_lower]

    # Check dynamic user prompt steering
    is_diplomatic = any(w in text_lower for w in ["diplomatic", "talks", "negotiat", "meeting", "envoy", "ambassador"])
    if "score diplomatic talks 10" in scoring_prompt or "score 10 for diplomatic" in scoring_prompt or "score diplomatic talks as 10" in scoring_prompt:
        if is_diplomatic:
            score = 10
            return Stage2Result(
                importance_score=score,
                score_rationale="User scoring prompt prioritizes diplomatic talks at critical importance (10).",
                priority_level="critical",
                should_alert=score >= threshold,
                matched_keywords=matched_kw,
                matched_entities=matched_ent,
            )

    if "score diplomatic talks 1" in scoring_prompt or "score 1 for diplomatic" in scoring_prompt or "score diplomatic talks as 1" in scoring_prompt:
        if is_diplomatic:
            score = 1
            return Stage2Result(
                importance_score=score,
                score_rationale="User scoring prompt minimizes diplomatic talks to lowest importance (1).",
                priority_level="low",
                should_alert=score >= threshold,
                matched_keywords=matched_kw,
                matched_entities=matched_ent,
            )

    # Heuristic scoring based on severity
    if any(w in text_lower for w in ["missile", "strike", "kinetic", "nuclear", "war", "attack", "casualt", "explosion"]):
        score = 9
        priority = "critical"
        rationale = "Direct kinetic engagement or critical strategic asset strike reported."
    elif any(w in text_lower for w in ["blockade", "seiz", "intercept", "drone", "patrol", "sanction", "threat", "strait"]):
        score = 7
        priority = "high"
        rationale = "Significant military escalation or naval disruption in strategic corridor."
    elif is_diplomatic:
        score = 4
        priority = "medium"
        rationale = "Routine diplomatic statements, multilateral consultations, and non-escalatory rhetoric."
    else:
        score = 3
        priority = "low"
        rationale = "General regional political commentary and background reporting."

    return Stage2Result(
        importance_score=score,
        score_rationale=rationale,
        priority_level=priority,
        should_alert=score >= threshold,
        matched_keywords=matched_kw,
        matched_entities=matched_ent,
    )


async def run_stage2_score(
    event: Any,
    intelligence_text: str,
    extracted_entities: List[str],
    theme_context: Dict[str, Any],
    provider: BaseAIProvider,
    model: Optional[str] = None,
    temperature: Optional[float] = None,
) -> Stage2Result:
    """
    Executes Stage 2 importance scoring using the configured AI provider.
    """
    title = getattr(event, "title", None) or (event.get("title") if isinstance(event, dict) else "")
    source_name = getattr(event, "source_name", None) or (event.get("source_name") if isinstance(event, dict) else "Unknown")
    source_type = getattr(event, "source_type", None) or (event.get("source_type") if isinstance(event, dict) else "rss")

    system_prompt = build_stage2_system_prompt(theme_context)
    prompt = (
        f"Event Title: {title}\n"
        f"Source: {source_name} ({source_type})\n"
        f"Extracted Entities: {', '.join(extracted_entities)}\n"
        f"Intelligence Content:\n{intelligence_text}\n\n"
        f"Provide valid JSON matching the Stage2Result schema."
    )

    def fallback():
        return _heuristic_stage2_fallback(intelligence_text, extracted_entities, theme_context)

    try:
        raw_response = await provider.generate_text(
            prompt=prompt,
            system_prompt=system_prompt,
            model=model,
            temperature=temperature if temperature is not None else 0.1,
            json_mode=True,
        )
        result = extract_and_parse_json(
            raw_text=raw_response,
            target_model=Stage2Result,
            fallback_factory=fallback,
        )
        return result
    except PermissionError:
        raise
    except RuntimeError as r_err:
        if "429" in str(r_err):
            raise
        logger.warning(f"RuntimeError in Stage 2, falling back to heuristics: {r_err}")
        return fallback()
    except Exception as exc:
        logger.warning(f"Stage 2 execution exception, using fallback: {exc}")
        return fallback()
