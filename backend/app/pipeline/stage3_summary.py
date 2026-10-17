"""
backend/app/pipeline/stage3_summary.py - Stage 3: Summarization & Periodic Digests
"""

import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Union

from backend.app.pipeline.models import DigestEventItem, PeriodicDigestResult, Stage3Result
from backend.app.pipeline.parser import extract_and_parse_json
from backend.app.pipeline.providers.base import BaseAIProvider

logger = logging.getLogger("osint.pipeline.stage3")

STAGE3_SYSTEM_PROMPT = """You are an executive intelligence briefer for strategic and tactical commanders.
Synthesize the provided intelligence event into:
1. A punchy, informative English headline (4 to 12 words, maximum 80 characters) describing the concrete event action and location, optimized for instant live feed timeline cards and mobile notifications.
2. A concise 2-3 sentence executive intelligence summary following the Bottom Line Up Front (BLUF) standard in English: state what happened, who is involved, and immediate operational implications. Eliminate editorial filler.

CRITICAL RULES & NEGATIVE CONSTRAINTS:
- The headline and summary MUST ALWAYS BE WRITTEN IN 100% FLUENT ENGLISH.
- NEVER output single-word ratings, confidence labels, or priority classifications (such as "High", "Critical", "Medium", "Low", "Urgent", "Disputed", "Alert") as the headline. The headline MUST describe the specific event action (e.g. who did what where).
- NEVER output priority or confidence assessments as the summary. The summary MUST be 2-3 complete sentences detailing incident facts and operational implications.

You MUST respond strictly with a valid JSON object matching this exact schema:
{
  "headline": "Informative event headline in English describing specific action (4-12 words, max 80 chars, NEVER a rating word)",
  "summary": "BLUF: 2-3 sentence executive intelligence summary in English stating facts, actors, and operational implications"
}
No markdown backticks, no preamble."""

DIGEST_SYSTEM_PROMPT = """You are a chief intelligence watch officer authoring a periodic situational intelligence digest.
Analyze the provided collection of recent events ingested under the theme: "{theme_name}".

Tasks:
1. Executive Overview: Synthesize a 1-2 paragraph briefing on overall operational tempo and escalation dynamics.
2. Threat Trajectory: Classify trajectory as one of: "Escalating", "Elevated", "Stable", "De-escalating", "Volatile".
3. Key Developments: Extract the 3 to 7 most critical developments with a 1-sentence analytical bullet for each.
4. Critical Alerts Count: Count of events scoring above threshold {alert_threshold}.
5. Watchlist Indicators: Provide 3-5 specific operational triggers to track over the next 12-24 hours.

You MUST respond strictly with a valid JSON object matching the requested schema."""


def _heuristic_stage3_fallback(intelligence_text: str, title: str = "") -> Stage3Result:
    """
    Deterministic offline heuristic summary fallback.
    Ensures headlines and summaries are always fluent English even when the original
    source title was in Arabic, Hebrew, or Persian.
    """
    clean_text = intelligence_text.replace("English translation:", "").strip()
    
    # Check if title has non-Latin characters (Arabic, Hebrew, Persian)
    has_non_latin = bool(re.search(r"[\u0600-\u06FF\u0590-\u05FF\uFB50-\uFDFF\uFE70-\uFEFF]", title))
    if has_non_latin and clean_text:
        # Use first English sentence/clause from translated clean_text as headline
        first_clause = clean_text.split(".")[0].strip()
        headline = first_clause
    else:
        headline = (title or (clean_text.split(".")[0].strip() if clean_text else "")).strip()

    if len(headline) > 75:
        headline = headline[:72] + "..."
    if not headline:
        headline = "Regional Intelligence Situation Update"

    first_sentences = ". ".join([s.strip() for s in clean_text.split(".") if s.strip()][:2])
    if first_sentences:
        summary = f"BLUF: {first_sentences}. Operational readiness and surveillance remain active."
    else:
        summary = "BLUF: Intelligence forces continue monitoring strategic developments across the operational theater."

    return Stage3Result(headline=headline, summary=summary)


def _heuristic_digest_fallback(
    events: List[Any],
    theme_name: str = "US-Iran conflict",
    alert_threshold: int = 7,
) -> PeriodicDigestResult:
    """
    Deterministic offline periodic digest fallback.
    """
    total = len(events)
    critical_count = 0
    developments: List[DigestEventItem] = []

    for ev in events:
        score = getattr(ev, "importance_score", None) or (ev.get("importance_score", 5) if isinstance(ev, dict) else 5)
        if score >= alert_threshold:
            critical_count += 1

        ev_id = str(getattr(ev, "id", None) or (ev.get("id", "evt") if isinstance(ev, dict) else "evt"))
        hl = getattr(ev, "headline", None) or (ev.get("headline") if isinstance(ev, dict) else "") or "Event Summary"
        src = getattr(ev, "source_name", None) or (ev.get("source_name") if isinstance(ev, dict) else "OSINT")
        summary = getattr(ev, "summary", None) or (ev.get("summary") if isinstance(ev, dict) else "") or hl

        if len(developments) < 5:
            developments.append(DigestEventItem(
                event_id=ev_id,
                headline=hl[:75],
                importance_score=score,
                source_name=src,
                bullet_summary=summary[:150],
            ))

    threat_trajectory = "Elevated" if critical_count > 0 else "Stable"
    overview = (
        f"Situational intelligence assessment for theme '{theme_name}' covering {total} ingested events. "
        f"{critical_count} high-priority critical alerts were recorded during the monitoring cycle. "
        f"Overall operational trajectory is assessed as {threat_trajectory} with heightened maritime and defensive vigilance."
    )

    return PeriodicDigestResult(
        theme_name=theme_name,
        time_window_start=datetime.now(timezone.utc),
        time_window_end=datetime.now(timezone.utc),
        total_events_analyzed=total,
        critical_alerts_count=critical_count,
        threat_trajectory=threat_trajectory,
        executive_overview=overview,
        key_developments=developments,
        watchlist_indicators=[
            "Monitor vessel transits and escort operations in Strait of Hormuz.",
            "Track air defense radar activations and ballistic test notices.",
            "Verify proxy statements and diplomatic consultations across regional capitals."
        ],
    )


async def run_stage3_summary(
    event: Any,
    intelligence_text: str,
    provider: BaseAIProvider,
    scoring_context: Optional[Dict[str, Any]] = None,
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    raise_on_error: bool = False,
) -> Stage3Result:
    """
    Executes Stage 3 event summarization using the configured AI provider.
    Incorporate cross-source corroboration and denial stance if available.
    """
    title = getattr(event, "title", None) or (event.get("title") if isinstance(event, dict) else "")
    prompt_parts = [
        f"Event Title: {title}",
        f"Intelligence Content:\n{intelligence_text}",
    ]
    if scoring_context:
        if scoring_context.get("is_contradiction"):
            prompt_parts.append(
                "DISPUTE CONTEXT: This event claim is actively denied or disputed by other sources. "
                "Ensure the headline or summary notes this disputed/conflicting stance objectively."
            )
        elif scoring_context.get("corroboration_count", 0) >= 2:
            prompt_parts.append(
                "VERIFICATION CONTEXT: Verified by multiple independent sources."
            )
    prompt_parts.append(
        "Respond strictly with valid JSON matching this schema:\n"
        "{\n"
        '  "headline": "Informative event headline in English (4-12 words, max 80 chars, NEVER a rating word)",\n'
        '  "summary": "BLUF: 2-3 sentence executive intelligence summary in English stating facts, actors, and implications"\n'
        "}"
    )
    prompt = "\n\n".join(prompt_parts)

    def fallback():
        res = _heuristic_stage3_fallback(intelligence_text, title)
        if scoring_context and scoring_context.get("is_contradiction") and not res.headline.lower().startswith("disputed"):
            res.headline = f"Disputed: {res.headline}"[:78]
        return res


    try:
        raw_response = await provider.generate_text(
            prompt=prompt,
            system_prompt=STAGE3_SYSTEM_PROMPT,
            model=model,
            temperature=temperature if temperature is not None else 0.2,
            json_mode=True,
        )
        result = extract_and_parse_json(
            raw_text=raw_response,
            target_model=Stage3Result,
            fallback_factory=fallback,
        )
        if not result.headline or not result.summary:
            fb = fallback()
            if not result.headline:
                result.headline = fb.headline
            if not result.summary:
                result.summary = fb.summary
        return result

    except PermissionError:
        raise
    except RuntimeError as r_err:
        if raise_on_error:
            raise
        logger.warning(f"RuntimeError in Stage 3 ({r_err}), gracefully falling back to heuristics.")
        return fallback()
    except Exception as exc:
        if raise_on_error:
            raise
        logger.warning(f"Stage 3 execution exception ({exc}), using fallback.")
        return fallback()


async def run_periodic_digest(
    events: List[Any],
    theme_name: str,
    alert_threshold: int,
    provider: BaseAIProvider,
    model: Optional[str] = None,
    temperature: Optional[float] = None,
) -> PeriodicDigestResult:
    """
    Synthesizes a periodic situational intelligence digest over a collection of events.
    """
    def fallback():
        return _heuristic_digest_fallback(events, theme_name, alert_threshold)

    if not events:
        return fallback()

    # Build concise compact digest event summaries to preserve token budget
    event_lines = []
    for ev in events[:20]:
        title = getattr(ev, "headline", None) or getattr(ev, "title", "")
        score = getattr(ev, "importance_score", 5)
        src = getattr(ev, "source_name", "OSINT")
        event_lines.append(f"- [{src}] (Score {score}) {title}")

    prompt = (
        f"Theme: {theme_name}\n"
        f"Alert Threshold: {alert_threshold}\n"
        f"Events Summary ({len(events)} events):\n"
        + "\n".join(event_lines) + "\n\n"
        f"Provide valid JSON matching the PeriodicDigestResult schema."
    )

    try:
        raw_response = await provider.generate_text(
            prompt=prompt,
            system_prompt=DIGEST_SYSTEM_PROMPT.format(theme_name=theme_name, alert_threshold=alert_threshold),
            model=model,
            temperature=temperature if temperature is not None else 0.2,
            json_mode=True,
        )
        result = extract_and_parse_json(
            raw_text=raw_response,
            target_model=PeriodicDigestResult,
            fallback_factory=fallback,
        )
        return result
    except Exception as exc:
        logger.warning(f"Periodic digest generation exception, using fallback: {exc}")
        return fallback()
