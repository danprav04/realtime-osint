"""
backend/app/pipeline/stage2_score.py - Stage 2: Context-Aware & Embedding-Calibrated Importance Scoring
"""

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from backend.app.pipeline.arabic_normalizer import classify_kinetic_signal
from backend.app.pipeline.models import Stage2Result
from backend.app.pipeline.parser import extract_and_parse_json
from backend.app.pipeline.providers.base import BaseAIProvider

logger = logging.getLogger("osint.pipeline.stage2")

TIER1_WIRE_AND_OFFICIAL_KEYWORDS = (
    "reuters",
    "associated press",
    "ap news",
    "afp",
    "bbc",
    "al jazeera",
    "centcom",
    "iaea",
    "us navy",
    "pentagon",
    "idf",
    "defense ministry",
    "state department",
    "bloomberg",
    "financial times",
    "maritime ais",
)

TIER3_UNVERIFIED_SOCIAL_KEYWORDS = (
    "reddit",
    "rumor",
    "unverified",
    "anon",
    "chatter",
    "osint defender",
    "forum",
    "citizen",
)


def classify_source_reliability(source_name: str, source_type: str) -> Tuple[str, str]:
    """
    Classify an ingestion source into a 3-tier intelligence reliability rating:
      - tier_1_wire_official: Established global wires, official military/nuclear bodies, or direct AIS/Seismic/UKMTO sensors
      - tier_2_regional_osint: Standard regional news outlets, RSS feeds, Bluesky/Mastodon OSINT trackers, and monitored channels
      - tier_3_social_unverified: Social forums (Reddit comments/posts) and unverified rumor channels
    """
    s_name = (source_name or "").strip().lower()
    s_type = (source_type or "").strip().lower()

    if s_type in ("ais", "maritime_ais", "ukmto", "seismic", "emsc", "geophysical", "oref", "pikud_haoref", "homefront", "hfc"):
        return "tier_1_wire_official", f"Tier 1 — Direct Sensor / Civil Defense Alert ({s_type.upper()})"

    if any(k in s_name for k in TIER1_WIRE_AND_OFFICIAL_KEYWORDS) or s_name == "ap" or "ukmto" in s_name or "emsc" in s_name or "oref" in s_name or "פיקוד העורף" in s_name:
        return "tier_1_wire_official", "Tier 1 — Major Wire / Official Feed"

    if s_type == "reddit" or any(k in s_name for k in TIER3_UNVERIFIED_SOCIAL_KEYWORDS):
        return "tier_3_social_unverified", "Tier 3 — Social / Unverified Feed"

    return "tier_2_regional_osint", "Tier 2 — Regional OSINT / Media Feed"



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
Your mission is to evaluate the following intelligence event and assign an objective importance score from 1 to 10 based on the user's active theme configuration, multi-dimensional signal analysis, general theater context, and semantic embedding corroboration.

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
- Medium (4-6): Routine diplomatic rhetoric, bilateral talks, non-escalatory military drills, standard policy statements, or uncorroborated acoustic anomalies ("explosions heard").
- Low (1-3): Background editorial commentary, unrelated noise, repetitive same-source echoes, minor administrative reports.

MULTI-DIMENSIONAL EVALUATION RUBRIC:
1. `severity_score` (1-10): Intrinsic tactical or strategic severity of the reported event.
2. `novelty_score` (1-10): Escalation novelty (9-10 = new breaking escalation; 5-7 = developing follow-up with new facts; 1-3 = repetitive echo or routine status quo).
3. `verification_confidence` ("low", "medium", "high") & `confidence_score` (1-10):
   - "high" (8-10): Corroborated across independent sources in embedding space, or reported by a Tier-1 wire/sensor with verified kinetic facts.
   - "medium" (5-7): First report from a standard regional OSINT source consistent with theater context.
   - "low" (1-4): Uncorroborated acoustic chatter ("sound of explosion heard" without impact proof) or single-source Tier-3 social rumor.

RULES:
1. THEME RELEVANCE GATE (MANDATORY): First verify whether the event is directly or indirectly connected to "{theme_name}" ({theme_desc}). If the event concerns an entirely separate geopolitical conflict or region with no link to "{theme_name}" (e.g., domestic unrelated politics or a separate war not involving the theme's actors), you MUST assign an `importance_score` of 1 or 2 ("low") regardless of severity.
2. The user's SCORING INSTRUCTIONS take precedence for all on-theme events. If the scoring prompt states to score specific events higher or lower, strictly obey that instruction.
3. CONTEXT & EMBEDDING CALIBRATION:
   - If the Semantic Embedding Context shows this report is a **Same-Source Echo** (repetitive post from the same outlet with no new escalation), lower `novelty_score` (1-3) and reduce `importance_score` below {threshold}.
   - If the report is **Acoustic Chatter Only** ("explosions heard" / sonic boom) with 0 corroborating sources, keep `importance_score` <= {max(1, int(threshold) - 1)}.
   - If the report is **Corroborated across multiple independent sources** in embedding space, reflect higher `verification_confidence` ("high") and confirm its operational importance.
4. The `importance_score` MUST be an integer between 1 and 10.
5. Priority level mapping: 1-3 = "low", 4-6 = "medium", 7-8 = "high", 9-10 = "critical".
6. Set `should_alert` to true IF AND ONLY IF `importance_score` >= {threshold} AND it is not a repetitive same-source echo.
7. Provide a 1-2 sentence `score_rationale` explaining the decision (mentioning corroboration, novelty, or signal confidence when relevant).
8. Check which tracked keywords and entities are present and populate `matched_keywords` and `matched_entities`.

You MUST respond strictly with a valid JSON object matching the requested schema. No markdown backticks."""


def build_stage2_context_block(
    stage1_result: Optional[Any] = None,
    scoring_context: Optional[Dict[str, Any]] = None,
) -> str:
    """Format Stage 1 structured signals and pre-computed embedding/theater context for Stage 2 prompt."""
    lines: List[str] = []

    if stage1_result is not None:
        key_facts = getattr(stage1_result, "key_facts", None) or (
            stage1_result.get("key_facts") if isinstance(stage1_result, dict) else []
        )
        breakdown = getattr(stage1_result, "entity_breakdown", None) or (
            stage1_result.get("entity_breakdown") if isinstance(stage1_result, dict) else {}
        )
        if key_facts:
            lines.append("Stage 1 Extracted Key Facts:")
            for kf in key_facts[:5]:
                lines.append(f"  - {kf}")
        if isinstance(breakdown, dict) and any(breakdown.values()):
            bd_parts = [f"{k}: {', '.join(v)}" for k, v in breakdown.items() if v]
            if bd_parts:
                lines.append(f"Entity Taxonomy: {' | '.join(bd_parts)}")

    if scoring_context:
        lines.append("\nSITUATIONAL & SEMANTIC EMBEDDING CONTEXT:")
        src_label = scoring_context.get("source_reliability_label", "Standard OSINT Feed")
        src_tier = scoring_context.get("source_reliability_tier", "tier_2_regional_osint")
        lines.append(f"- Source Reliability Tier: {src_label} ({src_tier})")

        kin = scoring_context.get("kinetic_signal") or {}
        if kin:
            sig_type = kin.get("signal_type", "general_intelligence")
            ver_status = kin.get("verification_status", "contextual_update")
            lines.append(f"- Frontline Signal Triangulation: {sig_type} ({ver_status})")
            if kin.get("is_sonic_boom_only"):
                lines.append(
                    "- Sonic Boom Differentiation: Sonic boom tokens detected WITHOUT smoke/impact evidence "
                    "(de-escalate from explosive impact to aircraft overflight)."
                )
            if kin.get("term_burst_active"):
                b_cnt = kin.get("burst_count", 0)
                b_tok = ", ".join(kin.get("burst_tokens") or [])
                lines.append(
                    f"- Sliding-Window Term Burst Active: {b_cnt} matching ground reports in 60s window "
                    f"[{b_tok}]"
                )

        is_contradiction = bool(scoring_context.get("is_contradiction", False))
        contra_sources = scoring_context.get("contradicting_sources") or []
        contra_details = scoring_context.get("contradiction_details") or []
        if is_contradiction:
            lines.append(
                f"- CONTRADICTION / DISPUTE ALERT: Report is actively denied or disputed by "
                f"{', '.join(contra_sources[:3])}."
            )
            for cd in contra_details[:2]:
                lines.append(f"  * {cd}")

        recency_corrob = scoring_context.get("recency_weighted_corroboration", 0.0)
        lines.append(
            f"- Continuous Recency-Weighted Corroboration: {recency_corrob:.2f} "
            f"(recent cross-source reports weighted with exponential decay)"
        )

        corrob_status = scoring_context.get("corroboration_status", "First Report / Unverified")
        corrob_count = scoring_context.get("corroboration_count", 0)
        corrob_sources = scoring_context.get("corroborating_sources", [])
        echo_count = scoring_context.get("same_source_echo_count", 0)
        max_sim = scoring_context.get("max_similarity", 0.0)

        corrob_sources_str = f" [{', '.join(corrob_sources[:4])}]" if corrob_sources else ""
        lines.append(
            f"- Embedding Corroboration Status: {corrob_status} "
            f"(Independent Corroborating Sources: {corrob_count}"
            f"{corrob_sources_str}, "
            f"Same-Source Recent Echoes: {echo_count}, Max Semantic Similarity: {max_sim:.2f})"
        )

        related = scoring_context.get("related_reports") or []
        if related:
            lines.append("- Top Semantically Related Recent Reports (Vector Embedding Matches):")
            for r in related[:4]:
                lines.append(
                    f"  * [{r.get('source_name')} | Sim {r.get('similarity', 0.0):.2f} | Score {r.get('importance_score', 5)}/10] "
                    f"{r.get('headline')}"
                )

        vel = scoring_context.get("velocity_status", "Normal")
        ev_6h = scoring_context.get("events_last_6h", 0)
        crit_6h = scoring_context.get("critical_alerts_last_6h", 0)
        avg_24h = scoring_context.get("avg_score_24h", 5.0)
        lines.append(
            f"- General Theater Tempo: {vel} ({ev_6h} events & {crit_6h} critical alerts in last 6h; "
            f"24h avg score: {avg_24h}/10)"
        )

        storylines = scoring_context.get("active_storylines") or []
        if storylines:
            lines.append(f"- Active High-Priority Theater Storylines: {' ; '.join(storylines[:3])}")

        baselines = scoring_context.get("entity_baselines") or {}
        if baselines:
            lines.append("- Longitudinal Entity Baselines (Tiered Historical Memory):")
            for ent, b_info in list(baselines.items())[:3]:
                lines.append(
                    f"  * {ent}: {b_info.get('daily_baseline_average', 0)} avg/day "
                    f"(peak {b_info.get('peak_daily_mentions', 0)}/day, {b_info.get('days_active_in_history', 0)} active epochs)"
                )

        precedents = scoring_context.get("historical_precedents") or []
        if precedents:
            lines.append("- Historical Precedents (from Memory Rollups):")
            for prec in precedents[:2]:
                lines.append(
                    f"  * [{prec.get('period_start')} | Peak {prec.get('max_importance_score')}/10] {prec.get('executive_summary')}"
                )

    return "\n".join(lines)


def _has_explicit_user_prompt_override(scoring_prompt: str, intelligence_text: str) -> Optional[int]:
    """Check if the user's custom scoring_prompt explicitly mandates a 10 or 1 override for this event."""
    sp_lower = (scoring_prompt or "").lower()
    text_lower = (intelligence_text or "").lower()
    is_diplomatic = any(
        w in text_lower for w in ["diplomatic", "talks", "negotiat", "meeting", "envoy", "ambassador"]
    )
    if is_diplomatic:
        if (
            "score diplomatic talks 10" in sp_lower
            or "score 10 for diplomatic" in sp_lower
            or "score diplomatic talks as 10" in sp_lower
        ):
            return 10
        if (
            "score diplomatic talks 1" in sp_lower
            or "score 1 for diplomatic" in sp_lower
            or "score diplomatic talks as 1" in sp_lower
        ):
            return 1
    return None


def calibrate_stage2_result(
    result: Stage2Result,
    theme_context: Dict[str, Any],
    scoring_context: Optional[Dict[str, Any]] = None,
    intelligence_text: str = "",
) -> Stage2Result:
    """
    Apply deterministic intelligence tradecraft calibration to Stage 2 output:
    1. Populates multi-dimensional sub-scores (`severity_score`, `novelty_score`, `verification_confidence`, `confidence_score`).
    2. Enforces Same-Source Echo dampening & duplicate alert suppression.
    3. Caps uncorroborated frontline acoustic chatter ("explosions heard") below the alert threshold,
       and de-escalates Sonic Boom overflights (without smoke/impact) to <= 5.
    4. Moderates uncorroborated Tier-3 social media claims.
    5. Rewards multi-source semantic embedding corroboration or 60s vernacular term bursts.
    """
    ctx = scoring_context or {}
    threshold = int(theme_context.get("alert_threshold", theme_context.get("importance_threshold", 7)))
    scoring_prompt = str(theme_context.get("scoring_prompt", ""))

    kin = ctx.get("kinetic_signal") or {}
    sig_type = kin.get("signal_type", "general_intelligence")
    is_kinetic = bool(kin.get("is_kinetic", False))
    is_sonic_boom_only = bool(kin.get("is_sonic_boom_only", False))
    term_burst_active = bool(kin.get("term_burst_active", False))
    burst_count = int(kin.get("burst_count", 0))
    corrob_count = int(ctx.get("corroboration_count", 0))
    corrob_sources = list(ctx.get("corroborating_sources", []))
    is_same_source_echo = bool(ctx.get("is_same_source_echo", False))
    src_tier = ctx.get("source_reliability_tier", "tier_2_regional_osint")
    related_reports = ctx.get("related_reports") or []

    score = int(result.importance_score)
    sev_score = int(result.severity_score) if result.severity_score is not None else score
    notes: List[str] = list(result.calibration_notes or [])

    # Check if user's custom scoring prompt explicitly overrode this event
    explicit_override = _has_explicit_user_prompt_override(scoring_prompt, intelligence_text)
    if explicit_override is not None:
        score = explicit_override
        sev_score = explicit_override
        nov_score = result.novelty_score if result.novelty_score is not None else 7
        conf_level = result.verification_confidence or "high"
        conf_score = result.confidence_score if result.confidence_score is not None else 8
    else:
        # Derive novelty sub-score
        if is_same_source_echo:
            nov_score = min(result.novelty_score or 2, 3)
        elif result.novelty_score is not None:
            nov_score = int(result.novelty_score)
        elif corrob_count == 0 and sev_score >= 7:
            nov_score = 9
        elif corrob_count >= 1:
            nov_score = 7
        else:
            nov_score = 5

        # Derive baseline verification confidence
        if corrob_count >= 2:
            conf_level = "high"
            conf_score = max(result.confidence_score or 9, 9)
        elif corrob_count == 1 or (src_tier == "tier_1_wire_official" and sig_type != "acoustic_chatter"):
            conf_level = "high"
            conf_score = max(result.confidence_score or 8, 8)
        elif sig_type == "acoustic_chatter" or src_tier == "tier_3_social_unverified":
            conf_level = "medium" if (term_burst_active and not is_sonic_boom_only) else "low"
            conf_score = 6 if (term_burst_active and not is_sonic_boom_only) else min(result.confidence_score or 4, 4)
        else:
            conf_level = result.verification_confidence or "medium"
            conf_score = result.confidence_score if result.confidence_score is not None else 7

        # --- Calibration Guardrail 1: Same-Source Echo Dampening ---
        if is_same_source_echo:
            old_score = score
            score = max(1, min(score - 2, threshold - 1))
            note = f"Same-source echo dampened ({old_score} -> {score}): repetitive update from same feed"
            if note not in notes:
                notes.append(note)

        # --- Calibration Guardrail 2a: Sonic Boom Overflight De-Escalation ---
        elif is_sonic_boom_only and not is_kinetic:
            cap = min(5, max(1, threshold - 2))
            if score > cap:
                old_score = score
                score = cap
                note = (
                    f"Sonic boom de-escalated ({old_score} -> {score}): supersonic overflight tokens "
                    f"reported without smoke plume or kinetic impact evidence"
                )
                if note not in notes:
                    notes.append(note)
            conf_level = "low"
            conf_score = min(conf_score, 4)

        # --- Calibration Guardrail 2b: Uncorroborated Acoustic Chatter Cap ---
        elif sig_type == "acoustic_chatter" and not is_kinetic and corrob_count == 0 and not term_burst_active:
            cap = max(1, min(6, threshold - 1))
            if score > cap:
                old_score = score
                score = cap
                note = (
                    f"Acoustic anomaly cap ({old_score} -> {score}): uncorroborated sound/siren "
                    f"report without confirmed kinetic impact"
                )
                if note not in notes:
                    notes.append(note)
            conf_level = "low"
            conf_score = min(conf_score, 4)

        # --- Calibration Guardrail 3: Single-Source Tier-3 Unverified Moderation ---
        elif src_tier == "tier_3_social_unverified" and corrob_count == 0 and not term_burst_active and score >= threshold:
            old_score = score
            score = max(1, score - 1)
            note = (
                f"Single-source social feed moderation ({old_score} -> {score}): "
                f"pending independent cross-source corroboration"
            )
            if note not in notes:
                notes.append(note)
            conf_level = "low"
            conf_score = min(conf_score, 4)

        # --- Calibration Guardrail 4: Multi-Source Embedding Corroboration & Contradiction Handling ---
        is_contradiction = bool(ctx.get("is_contradiction", False))
        contra_sources = list(ctx.get("contradicting_sources", []))

        if is_contradiction:
            old_score = score
            if score >= threshold and src_tier != "tier_1_wire_official":
                score = max(1, min(score, threshold - 1))
            src_str = f" from {', '.join(contra_sources[:2])}" if contra_sources else ""
            note = f"Dispute / Contradiction flagged: conflicting claims or official denial{src_str}"
            if note not in notes:
                notes.append(note)
            conf_level = "low"
            conf_score = min(conf_score, 4)
        elif not is_same_source_echo and corrob_count >= 2 and score >= 4:
            old_score = score
            score = min(10, score + 1)
            src_str = ", ".join(corrob_sources[:3])
            note = (
                f"Multi-source corroboration boost ({old_score} -> {score}): "
                f"verified across {corrob_count + 1} independent outlets ({src_str})"
            )
            if note not in notes:
                notes.append(note)
            conf_level = "high"
            conf_score = max(conf_score, 9)
        elif not is_same_source_echo and corrob_count == 1 and score >= 4:
            src_str = ", ".join(corrob_sources[:2])
            note = f"Cross-source corroborated by {src_str}"
            if note not in notes:
                notes.append(note)
            conf_level = "high"
            conf_score = max(conf_score, 8)
        elif not is_same_source_echo and term_burst_active and not is_sonic_boom_only and score >= 4:
            old_score = score
            if is_kinetic and score < 10:
                score = min(10, score + 1)
            note = f"Vernacular term burst detected ({burst_count} reports in 60s window)"
            if note not in notes:
                notes.append(note)
            conf_level = "high" if is_kinetic else "medium"
            conf_score = max(conf_score, 8 if is_kinetic else 6)

    # Map final calibrated score to priority level
    if score >= 9:
        priority = "critical"
    elif score >= 7:
        priority = "high"
    elif score >= 4:
        priority = "medium"
    else:
        priority = "low"

    should_alert = bool(score >= threshold and not is_same_source_echo and not is_contradiction)

    rationale = (result.score_rationale or "Standard intelligence assessment.").strip()
    if notes:
        primary_note = notes[0]
        if primary_note.lower() not in rationale.lower():
            rationale = f"{rationale} [{primary_note}]"

    return Stage2Result(
        importance_score=score,
        score_rationale=rationale,
        priority_level=priority,  # type: ignore
        should_alert=should_alert,
        matched_keywords=list(result.matched_keywords or []),
        matched_entities=list(result.matched_entities or []),
        severity_score=sev_score,
        novelty_score=nov_score,
        verification_confidence=conf_level,  # type: ignore
        confidence_score=conf_score,
        corroboration_status=ctx.get("corroboration_status") or result.corroboration_status or "First Report / Unverified",
        corroborating_sources=corrob_sources or list(result.corroborating_sources or []),
        related_event_ids=[r.get("event_id") for r in related_reports if r.get("event_id")] or list(result.related_event_ids or []),
        is_contradiction=is_contradiction,
        contradicting_sources=contra_sources or list(getattr(result, "contradicting_sources", []) or []),
        contradiction_details=list(ctx.get("contradiction_details") or []),
        recency_weighted_corroboration=ctx.get("recency_weighted_corroboration"),
        historical_precedents=list(ctx.get("historical_precedents") or []),
        calibration_notes=notes,
    )


def _heuristic_stage2_fallback(
    intelligence_text: str,
    extracted_entities: List[str],
    theme_context: Dict[str, Any],
    scoring_context: Optional[Dict[str, Any]] = None,
) -> Stage2Result:
    """
    Deterministic offline heuristic scoring fallback.
    Honors active theme scoring prompt steering, alert threshold, and contextual calibration.
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
            base_res = Stage2Result(
                importance_score=score,
                score_rationale="User scoring prompt prioritizes diplomatic talks at critical importance (10).",
                priority_level="critical",
                should_alert=score >= threshold,
                matched_keywords=matched_kw,
                matched_entities=matched_ent,
                severity_score=10,
                novelty_score=8,
                verification_confidence="high",
                confidence_score=9,
            )
            return calibrate_stage2_result(base_res, theme_context, scoring_context, intelligence_text)

    if "score diplomatic talks 1" in scoring_prompt or "score 1 for diplomatic" in scoring_prompt or "score diplomatic talks as 1" in scoring_prompt:
        if is_diplomatic:
            score = 1
            base_res = Stage2Result(
                importance_score=score,
                score_rationale="User scoring prompt minimizes diplomatic talks to lowest importance (1).",
                priority_level="low",
                should_alert=score >= threshold,
                matched_keywords=matched_kw,
                matched_entities=matched_ent,
                severity_score=1,
                novelty_score=2,
                verification_confidence="medium",
                confidence_score=7,
            )
            return calibrate_stage2_result(base_res, theme_context, scoring_context, intelligence_text)

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

    base_res = Stage2Result(
        importance_score=score,
        score_rationale=rationale,
        priority_level=priority,
        should_alert=score >= threshold,
        matched_keywords=matched_kw,
        matched_entities=matched_ent,
        severity_score=score,
    )
    return calibrate_stage2_result(base_res, theme_context, scoring_context, intelligence_text)


async def run_stage2_score(
    event: Any,
    intelligence_text: str,
    extracted_entities: List[str],
    theme_context: Dict[str, Any],
    provider: BaseAIProvider,
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    raise_on_error: bool = False,
    stage1_result: Optional[Any] = None,
    scoring_context: Optional[Dict[str, Any]] = None,
) -> Stage2Result:
    """
    Executes Stage 2 importance scoring using the configured AI provider,
    enriched with Stage 1 structured signals, general theater context,
    and vector embedding corroboration.
    """
    title = getattr(event, "title", None) or (event.get("title") if isinstance(event, dict) else "")
    raw_text = getattr(event, "raw_text", None) or (event.get("raw_text") if isinstance(event, dict) else "")
    source_name = getattr(event, "source_name", None) or (event.get("source_name") if isinstance(event, dict) else "Unknown")
    source_type = getattr(event, "source_type", None) or (event.get("source_type") if isinstance(event, dict) else "rss")

    # Ensure baseline signal & source reliability context is always populated even if called standalone
    effective_context: Dict[str, Any] = dict(scoring_context or {})
    if "source_reliability_tier" not in effective_context:
        tier_id, tier_label = classify_source_reliability(source_name, source_type)
        effective_context["source_reliability_tier"] = tier_id
        effective_context["source_reliability_label"] = tier_label
    if "kinetic_signal" not in effective_context:
        effective_context["kinetic_signal"] = classify_kinetic_signal(f"{title}\n{raw_text}")

    system_prompt = build_stage2_system_prompt(theme_context)
    context_block = build_stage2_context_block(stage1_result=stage1_result, scoring_context=effective_context)

    context_block_section = f"{context_block}\n" if context_block else ""
    prompt = (
        f"Event Title: {title}\n"
        f"Source: {source_name} ({source_type})\n"
        f"Extracted Entities: {', '.join(extracted_entities)}\n"
        f"Intelligence Content:\n{intelligence_text}\n"
        f"{context_block_section}\n"
        f"Provide valid JSON matching the Stage2Result schema (including 'importance_score', 'score_rationale', "
        f"'priority_level', 'should_alert', 'severity_score', 'novelty_score', 'verification_confidence', "
        f"'confidence_score', 'matched_keywords', 'matched_entities')."
    )

    def fallback():
        return _heuristic_stage2_fallback(intelligence_text, extracted_entities, theme_context, effective_context)

    try:
        raw_response = await provider.generate_text(
            prompt=prompt,
            system_prompt=system_prompt,
            model=model,
            temperature=temperature if temperature is not None else 0.1,
            json_mode=True,
        )
        parsed = extract_and_parse_json(
            raw_text=raw_response,
            target_model=Stage2Result,
            fallback_factory=fallback,
        )
        return calibrate_stage2_result(parsed, theme_context, effective_context, intelligence_text)
    except PermissionError:
        raise
    except RuntimeError as r_err:
        if raise_on_error or "429" in str(r_err):
            raise
        logger.warning(f"RuntimeError in Stage 2 ({r_err}), gracefully falling back to heuristics.")
        return fallback()
    except Exception as exc:
        if raise_on_error:
            raise
        logger.warning(f"Stage 2 execution exception ({exc}), using fallback.")
        return fallback()

