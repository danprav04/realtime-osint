"""
backend/app/services/summary_service.py - Full-Spectrum AI Situational Summary Engine
=====================================================================================
Synthesizes incoming report summaries, high-dimensional vector embeddings, semantic
storyline clusters, multi-source corroboration/trustworthiness scores, and live OSINT
system telemetry (velocity, entities, locations, alerts) into
an executive operational briefing (SitRep).
"""

import asyncio
import logging
import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple, Union

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models import Alert, Event, EventEmbedding, SystemSettings, Theme, utc_now
from backend.app.pipeline.models import ClusterSynthesisLLMItem, SituationBriefingLLMOutput
from backend.app.pipeline.orchestrator import get_pipeline_orchestrator
from backend.app.pipeline.parser import extract_and_parse_json
from backend.app.pipeline.providers.base import BaseAIProvider
from backend.app.services.embedding_service import get_embedding_service

logger = logging.getLogger("osint.services.summary")

SUMMARY_CACHE_SETTINGS_KEY = "situation_summary_cache"
CACHE_TTL_SECONDS = 900  # 15 minutes

SITUATION_SUMMARY_SYSTEM_PROMPT = """You are the Chief Intelligence Watch Officer authoring a full-spectrum Operational Situation Summary (SitRep).
You are provided with:
1. Active Theme Monitoring Context & Alert Thresholds.
2. Live OSINT Telemetry: Reporting velocity (6h vs 24h), source breakdown, and critical alert counts.
3. Semantic Embedding Storyline Clusters: Incoming intelligence reports grouped by high-dimensional vector embeddings (cosine similarity + KMeans clustering), annotated with multi-source corroboration counts and trustworthiness scores ("Highly Corroborated", "Developing / Verified", "Single-Source Unverified", "Isolated Outlier").
4. Unverified / Isolated Outlier Reports: High-visibility claims that lack independent cross-source corroboration in embedding space.

CRITICAL INSTRUCTIONS:
- Synthesize all available intelligence into a cohesive, authoritative, 100% fluent English situational briefing.
- Weight your conclusions by EMBEDDING CORROBORATION: treat "Highly Corroborated" and "Developing / Verified" clusters as confirmed operational developments, while explicitly caveats "Single-Source Unverified" and "Isolated Outlier" claims as unconfirmed reporting requiring verification.
- Provide an overall `threat_trajectory` ("Escalating", "Elevated", "Stable", "De-escalating", "Volatile") and `threat_level_score` (1-10).
- Write a 2-3 paragraph `executive_bluf` (Bottom Line Up Front) synthesizing the primary operational developments, actor posture, and immediate implications.
- Write a 1-2 paragraph `strategic_assessment` analyzing cross-cluster linkages, reporting tempo/velocity surges, and source diversity.
- For each provided semantic cluster (`cluster_id`), return a matching item in `cluster_assessments` with a punchy operational `title`, a 2-3 sentence `executive_summary` synthesizing the reports in that cluster and their source consensus, a 1-sentence `operational_implication`, and `threat_level` ("low", "medium", "high", "critical").
- Provide 4-6 `key_developments` bullets, an `unverified_outlier_assessment` summarizing notable uncorroborated claims, and 4-5 actionable `watchlist_indicators` for the next 12-24 hours.

Respond strictly with a valid JSON object matching the SituationBriefingLLMOutput schema. Do not wrap in markdown backticks."""


def _hours_for_time_range(time_range: Optional[str]) -> Optional[int]:
    mapping = {
        "6h": 6,
        "24h": 24,
        "3d": 72,
        "7d": 168,
        "all": None,
    }
    if not time_range:
        return None
    return mapping.get(time_range.lower().strip(), None)


def _derive_cluster_threat_level(max_score: int, avg_score: float) -> str:
    if max_score >= 9 or avg_score >= 8.0:
        return "critical"
    if max_score >= 7 or avg_score >= 6.5:
        return "high"
    if max_score >= 4 or avg_score >= 4.0:
        return "medium"
    return "low"


def _derive_trust_label_from_score(avg_trust: float) -> str:
    if avg_trust >= 80:
        return "Highly Corroborated"
    if avg_trust >= 65:
        return "Developing / Verified"
    if avg_trust >= 35:
        return "Single-Source Unverified"
    return "Isolated Outlier"


def _build_deterministic_summary(
    theme_name: str,
    alert_threshold: int,
    clusters_meta: List[Dict[str, Any]],
    outlier_events: List[Dict[str, Any]],
    telemetry: Dict[str, Any],
) -> SituationBriefingLLMOutput:
    """
    Deterministic, data-driven synthesis fallback when LLMs are in mock mode,
    offline, or rate-limited. Uses the actual cluster data, report summaries,
    embedding corroboration scores, entities, and telemetry.
    """
    total_events = telemetry.get("total_events", 0)
    critical_count = telemetry.get("critical_alerts_count", 0)
    corrob_rate = telemetry.get("corroboration_rate_pct", 0)
    edges_count = telemetry.get("cross_source_edges_count", 0)
    max_score = telemetry.get("max_importance_score", 5)
    avg_score = telemetry.get("avg_importance_score", 5.0)
    velocity_status = telemetry.get("velocity_status", "Normal")
    top_entities = [e["name"] for e in telemetry.get("top_entities", [])[:6]]
    top_locations = [l["name"] for l in telemetry.get("top_locations", [])[:4]]

    if total_events == 0:
        return SituationBriefingLLMOutput(
            threat_trajectory="Stable",
            threat_level_score=2,
            executive_bluf=(
                f"BLUF: No intelligence events are currently recorded within the selected time window for "
                f"theme '{theme_name}'. Automated ingestion supervisors and multi-stage analysis pipelines "
                f"remain active and monitoring configured OSINT feeds."
            ),
            strategic_assessment=(
                "Baseline monitoring posture is in effect. As incoming reports are ingested from RSS, Telegram, "
                "NewsAPI, Reddit, and Maritime AIS sensors, high-dimensional vector embeddings and semantic "
                "clusters will automatically populate this situational briefing."
            ),
            cluster_assessments=[],
            key_developments=[],
            unverified_outlier_assessment="No uncorroborated or isolated outlier reports detected in the current window.",
            watchlist_indicators=[
                f"Monitor incoming ingestion streams for initial '{theme_name}' activity.",
                "Verify source polling intervals and API credentials in Settings.",
                "Track cross-source corroboration links as new events are embedded.",
            ],
        )

    # Determine threat trajectory & score
    if critical_count >= 4 or max_score >= 9 or (critical_count >= 2 and velocity_status == "Surge"):
        trajectory = "Escalating"
        threat_score = min(10, max(8, max_score))
    elif critical_count >= 2 or max_score >= 8:
        trajectory = "Elevated"
        threat_score = max(7, min(9, round(avg_score + 1.5)))
    elif velocity_status == "Surge" or len(outlier_events) > total_events * 0.5:
        trajectory = "Volatile"
        threat_score = max(6, min(8, max_score))
    elif max_score >= 5:
        trajectory = "Elevated" if critical_count >= 1 else "Stable"
        threat_score = max(4, min(7, round(avg_score)))
    else:
        trajectory = "De-escalating" if velocity_status == "Quiet" else "Stable"
        threat_score = max(2, min(5, round(avg_score)))

    # Build per-cluster assessments
    cluster_items: List[ClusterSynthesisLLMItem] = []
    for c in clusters_meta:
        cid = c["cluster_id"]
        c_events = c.get("events", [])
        rep_count = c["report_count"]
        ind_sources = c.get("independent_sources", [])
        trust_label = c.get("trustworthiness_label", "Developing / Verified")
        avg_trust = c.get("avg_trustworthiness_score", 60)
        c_max_score = c.get("max_importance_score", 5)
        c_avg_score = c.get("avg_importance_score", 5.0)
        c_entities = c.get("top_entities", [])[:4]

        # Synthesize top 2 report summaries in the cluster
        top_summaries = []
        for ev in c_events[:2]:
            s_text = (ev.get("summary") or ev.get("headline") or ev.get("title") or "").replace("BLUF:", "").strip()
            if s_text:
                top_summaries.append(s_text)

        combined_narrative = " ".join(top_summaries)
        if len(combined_narrative) > 360:
            combined_narrative = combined_narrative[:357] + "..."

        src_desc = (
            f"Corroborated across {len(ind_sources)} outlets ({', '.join(ind_sources[:3])})"
            if len(ind_sources) > 1
            else f"Reported via {ind_sources[0] if ind_sources else 'monitored feed'}"
        )
        exec_summary = (
            f"{src_desc} with {avg_trust}% embedding trustworthiness ({trust_label}). "
            f"{combined_narrative}"
        ).strip()

        ent_str = f" involving {', '.join(c_entities)}" if c_entities else ""
        implication = (
            f"High-priority operational storyline (peak score {c_max_score}/10){ent_str}; "
            f"{'confirmed by multi-source semantic convergence.' if avg_trust >= 65 else 'requires independent secondary confirmation.'}"
        )

        cluster_items.append(
            ClusterSynthesisLLMItem(
                cluster_id=cid,
                title=c["label"],
                executive_summary=exec_summary,
                operational_implication=implication,
                threat_level=_derive_cluster_threat_level(c_max_score, c_avg_score),
            )
        )

    # Build Executive BLUF
    lead_cluster_names = [c["label"] for c in clusters_meta[:3]]
    lead_clusters_str = (
        f"Primary semantic storylines center on {'; '.join(lead_cluster_names)}."
        if lead_cluster_names
        else "Incoming intelligence is distributed across individual flash reports."
    )
    entities_str = (
        f"Key actors and assets driving current reporting include {', '.join(top_entities)}."
        if top_entities
        else ""
    )
    locations_str = (
        f"Geographic operational focus is concentrated around {', '.join(top_locations)}."
        if top_locations
        else ""
    )

    p1 = (
        f"BLUF: Situational posture for '{theme_name}' is assessed as {trajectory.upper()} "
        f"(Threat Level {threat_score}/10) across {total_events} analyzed intelligence reports "
        f"and {len(clusters_meta)} semantic embedding clusters. {critical_count} critical flash events "
        f"exceeded the alert threshold (≥{alert_threshold}/10), while vector similarity analysis "
        f"established {edges_count} cross-source corroboration links ({corrob_rate}% multi-source verification rate)."
    )
    p2 = f"{lead_clusters_str} {entities_str} {locations_str}".strip()

    # Build Strategic Assessment
    strategic_assessment = (
        f"Cross-cluster embedding analysis indicates a {velocity_status.lower()} reporting tempo "
        f"({telemetry.get('events_last_6h', 0)} events in the last 6 hours vs. "
        f"{telemetry.get('events_prev_18h', 0)} in the preceding 18 hours). "
        f"High-dimensional vector clustering separated {sum(c['report_count'] for c in clusters_meta)} "
        f"clustered reports from {len(outlier_events)} unverified/outlier claims, allowing watch officers "
        f"to prioritize multi-source consensus over single-feed noise."
    )

    # Build Key Developments
    all_sorted_events = []
    for c in clusters_meta:
        all_sorted_events.extend(c.get("events", []))
    all_sorted_events.extend(outlier_events)
    all_sorted_events.sort(
        key=lambda x: (x.get("importance_score", 5), x.get("trustworthiness_score", 50)),
        reverse=True,
    )

    key_devs: List[str] = []
    seen_headlines = set()
    for ev in all_sorted_events:
        hl = (ev.get("headline") or ev.get("title") or "").strip()
        if not hl or hl.lower() in seen_headlines:
            continue
        seen_headlines.add(hl.lower())
        src = ev.get("source_name", "OSINT")
        sc = ev.get("importance_score", 5)
        tl = ev.get("trustworthiness_label", "Verified")
        sum_snippet = (ev.get("summary") or "").replace("BLUF:", "").strip()
        first_sent = sum_snippet.split(".")[0].strip() if sum_snippet else hl
        key_devs.append(f"[{src} | Score {sc}/10 | {tl}] {hl} — {first_sent}.")
        if len(key_devs) >= 5:
            break

    # Build Unverified Outlier Assessment
    if outlier_events:
        top_outlier = outlier_events[0]
        outlier_assessment = (
            f"Embedding distance analysis flagged {len(outlier_events)} report(s) as Single-Source Unverified "
            f"or Isolated Outliers lacking independent cross-outlet confirmation. Most notable uncorroborated claim: "
            f"\"{top_outlier.get('headline') or top_outlier.get('title')}\" via {top_outlier.get('source_name')} "
            f"(Score {top_outlier.get('importance_score', 5)}/10, Trust {top_outlier.get('trustworthiness_score', 25)}%). "
            f"Treat these items as tactical indicators pending secondary sensor corroboration."
        )
    else:
        outlier_assessment = (
            "All active intelligence reports within the window align with established semantic clusters "
            "or multi-source corroboration thresholds; zero high-risk isolated outliers detected."
        )

    # Watchlist indicators
    watchlist = [
        f"Monitor cross-source corroboration for emerging reports around {', '.join(top_locations[:2]) if top_locations else theme_name}.",
        f"Track operational posture and communications involving {', '.join(top_entities[:3]) if top_entities else 'primary regional actors'}.",
        "Watch for secondary confirmation of single-source flash claims in embedding outlier space.",
        "Evaluate 6-hour ingestion velocity spikes across Telegram and Maritime AIS feeds for early escalation signals.",
    ]

    return SituationBriefingLLMOutput(
        threat_trajectory=trajectory,  # type: ignore
        threat_level_score=threat_score,
        executive_bluf=f"{p1}\n\n{p2}",
        strategic_assessment=strategic_assessment,
        cluster_assessments=cluster_items,
        key_developments=key_devs,
        unverified_outlier_assessment=outlier_assessment,
        watchlist_indicators=watchlist,
    )


class SituationSummaryService:
    """
    Service responsible for gathering all OSINT events, report summaries,
    high-dimensional embeddings, semantic clusters, trustworthiness metrics,
    and system telemetry to generate and cache the AI Situational Summary.
    """

    def __init__(self):
        self.last_generation_time: Optional[datetime] = None
        self.last_generation_event_count: int = 0
        self.last_generation_latest_ingested: Optional[str] = None

    def record_generation(
        self, gen_time: datetime, count: int, latest_iso: Optional[str]
    ) -> None:
        """Record the timestamp and event counts of a summary generation."""
        self.last_generation_time = gen_time
        self.last_generation_event_count = count
        self.last_generation_latest_ingested = latest_iso

    def get_last_generation_metadata(
        self,
    ) -> Tuple[Optional[datetime], int, Optional[str]]:
        """Return (last_generation_time, last_generation_event_count, last_generation_latest_ingested)."""
        return (
            self.last_generation_time,
            self.last_generation_event_count,
            self.last_generation_latest_ingested,
        )

    async def get_latest_persisted_summary(
        self,
        session: AsyncSession,
        theme_id: Optional[str] = None,
        time_range: str = "all",
    ) -> Dict[str, Any]:
        """
        Retrieve the latest persisted situational summary from SystemSettings
        without triggering ANY LLM synthesis, pipeline stages, or heavy computation.
        If no persisted summary exists in cache, returns a deterministic fallback.
        """
        theme_obj, theme_key, theme_name, alert_threshold = await self._resolve_theme(
            session, theme_id
        )
        norm_time_range = (time_range or "all").lower().strip()
        cache_key = f"{theme_key}:{norm_time_range}"

        # 1. Attempt lookup in SystemSettings
        try:
            stmt = select(SystemSettings).where(
                SystemSettings.key == SUMMARY_CACHE_SETTINGS_KEY
            )
            record = (await session.execute(stmt)).scalars().first()
            if record and isinstance(record.value_json, dict):
                cache_map = record.value_json
                entry = cache_map.get(cache_key)
                if not entry:
                    entry = cache_map.get(f"{theme_key}:all")
                if not entry and cache_map:
                    entry = max(
                        cache_map.values(),
                        key=lambda e: e.get("generated_at", ""),
                        default=None,
                    )
                if entry and isinstance(entry, dict) and "payload" in entry:
                    payload = dict(entry["payload"])
                    payload["cached"] = True
                    return payload
        except Exception as exc:
            logger.warning(f"Error reading persisted summary: {exc}")

        # 2. If nothing persisted, return safe deterministic fallback with zero LLM generation
        telemetry = {
            "total_events": 0,
            "embedded_events": 0,
            "embedding_coverage_pct": 100.0,
            "corroborated_reports_count": 0,
            "corroboration_rate_pct": 0,
            "cross_source_edges_count": 0,
            "total_clusters": 0,
            "isolated_outliers_count": 0,
            "critical_alerts_count": 0,
            "avg_importance_score": 0.0,
            "max_importance_score": 0,
            "velocity_status": "Quiet",
            "events_last_6h": 0,
            "events_prev_18h": 0,
            "source_breakdown": {},
            "top_entities": [],
            "top_locations": [],
            "top_categories": [],
        }
        fallback_llm = _build_deterministic_summary(
            theme_name=theme_name,
            alert_threshold=alert_threshold,
            clusters_meta=[],
            outlier_events=[],
            telemetry=telemetry,
        )
        return {
            "theme_id": theme_key,
            "theme_name": theme_name,
            "generated_at": utc_now().isoformat(),
            "time_range": norm_time_range,
            "cached": True,
            "active_ai_model": "deterministic-fusion-engine",
            "active_embedding_model": "gemini-embedding-001",
            "threat_trajectory": fallback_llm.threat_trajectory,
            "threat_level_score": fallback_llm.threat_level_score,
            "executive_bluf": fallback_llm.executive_bluf,
            "strategic_assessment": fallback_llm.strategic_assessment,
            "key_developments": [],
            "clusters": [],
            "unverified_outliers": {
                "count": 0,
                "assessment": fallback_llm.unverified_outlier_assessment,
                "events": [],
            },
            "watchlist_indicators": fallback_llm.watchlist_indicators,
            "telemetry": telemetry,
        }

    async def _resolve_theme(
        self, session: AsyncSession, theme_id: Optional[str]
    ) -> Tuple[Optional[Theme], str, str, int]:
        """
        Return (Theme_or_None, theme_key_str, theme_display_name, alert_threshold).
        If theme_id is 'all', returns (None, 'all', 'All Monitored Themes', 7).
        If theme_id is None or 'active', resolves the active Theme.
        """
        if theme_id and str(theme_id).lower().strip() == "all":
            return None, "all", "All Monitored Themes", 7

        if theme_id and str(theme_id).lower().strip() != "active":
            if str(theme_id).isdigit():
                stmt = select(Theme).where(Theme.id == int(theme_id))
            else:
                stmt = select(Theme).where(
                    (Theme.slug == theme_id) | (Theme.name.ilike(f"%{theme_id}%"))
                )
            theme = (await session.execute(stmt)).scalars().first()
            if theme:
                return (
                    theme,
                    str(theme.slug or theme.id),
                    theme.name,
                    theme.alert_threshold or 7,
                )

        # Default: active theme
        stmt_active = select(Theme).where(Theme.is_active == True).limit(1)
        active_theme = (await session.execute(stmt_active)).scalars().first()
        if active_theme:
            return (
                active_theme,
                str(active_theme.slug or active_theme.id),
                active_theme.name,
                active_theme.alert_threshold or 7,
            )

        return None, "default", "US-Iran conflict", 7

    async def _get_cached_summary(
        self,
        session: AsyncSession,
        cache_key: str,
        latest_ingested_iso: Optional[str],
        total_events_count: int,
    ) -> Optional[Dict[str, Any]]:
        """Check SystemSettings for a fresh cached situational summary."""
        try:
            stmt = select(SystemSettings).where(
                SystemSettings.key == SUMMARY_CACHE_SETTINGS_KEY
            )
            record = (await session.execute(stmt)).scalars().first()
            if not record or not isinstance(record.value_json, dict):
                return None

            entry = record.value_json.get(cache_key)
            if not isinstance(entry, dict):
                return None

            gen_at_str = entry.get("generated_at")
            if not gen_at_str:
                return None

            gen_at = datetime.fromisoformat(gen_at_str)
            if gen_at.tzinfo is None:
                gen_at = gen_at.replace(tzinfo=timezone.utc)

            age_sec = (utc_now() - gen_at).total_seconds()
            if age_sec > CACHE_TTL_SECONDS:
                return None

            # Invalidate if event count or latest event timestamp changed
            if entry.get("cached_total_events") != total_events_count:
                return None
            if latest_ingested_iso and entry.get("latest_event_ingested_at") != latest_ingested_iso:
                return None

            payload = dict(entry.get("payload", {}))
            if payload:
                # If the cached summary was generated by a fallback model while the primary
                # configured model was temporarily unavailable, invalidate once primary is healthy
                cached_model = payload.get("active_ai_model")
                orchestrator = get_pipeline_orchestrator()
                if not orchestrator.use_mock and total_events_count > 0:
                    sum_cfg = orchestrator.get_stage_config("summarization")
                    primary_key = f"{sum_cfg.provider}:{sum_cfg.model_name}"
                    if (
                        cached_model
                        and cached_model != primary_key
                        and orchestrator.get_cooldown_remaining(primary_key) <= 0.0
                    ):
                        logger.info(
                            f"Cached summary used fallback model ({cached_model}); "
                            f"primary ({primary_key}) is ready — refreshing summary."
                        )
                        return None

                payload["cached"] = True
                return payload
        except Exception as exc:
            logger.debug(f"Summary cache lookup skipped: {exc}")
        return None

    async def _save_cached_summary(
        self,
        session: AsyncSession,
        cache_key: str,
        latest_ingested_iso: Optional[str],
        total_events_count: int,
        payload: Dict[str, Any],
    ) -> None:
        """Persist generated summary in SystemSettings cache."""
        try:
            stmt = select(SystemSettings).where(
                SystemSettings.key == SUMMARY_CACHE_SETTINGS_KEY
            )
            record = (await session.execute(stmt)).scalars().first()
            cache_map = (
                dict(record.value_json)
                if (record and isinstance(record.value_json, dict))
                else {}
            )

            # Keep max 12 cache keys to avoid unbounded JSON growth
            if len(cache_map) >= 12 and cache_key not in cache_map:
                oldest_key = next(iter(cache_map.keys()))
                cache_map.pop(oldest_key, None)

            cache_map[cache_key] = {
                "generated_at": payload.get("generated_at"),
                "latest_event_ingested_at": latest_ingested_iso,
                "cached_total_events": total_events_count,
                "payload": payload,
            }

            if record:
                record.value_json = cache_map
            else:
                session.add(
                    SystemSettings(
                        key=SUMMARY_CACHE_SETTINGS_KEY,
                        value_json=cache_map,
                    )
                )
            await session.commit()
        except Exception as exc:
            logger.warning(f"Failed to save situational summary cache: {exc}")

    async def generate_or_get_summary(
        self,
        session: AsyncSession,
        theme_id: Optional[str] = None,
        time_range: str = "all",
        force_refresh: bool = False,
        limit: int = 150,
    ) -> Dict[str, Any]:
        """
        Synthesize or retrieve the full-spectrum AI Situational Summary.
        """
        theme_obj, theme_key, theme_name, alert_threshold = await self._resolve_theme(
            session, theme_id
        )
        norm_time_range = (time_range or "all").lower().strip()
        if norm_time_range not in ("6h", "24h", "3d", "7d", "all"):
            norm_time_range = "all"

        hours = _hours_for_time_range(norm_time_range)
        now = utc_now()

        # Build query to check current events in window
        base_query = select(Event).order_by(Event.published_at.desc())
        if theme_obj is not None:
            base_query = base_query.where(Event.theme_id == theme_obj.id)
        if hours is not None:
            cutoff = now - timedelta(hours=hours)
            base_query = base_query.where(Event.published_at >= cutoff)

        events_in_window: List[Event] = list(
            (await session.execute(base_query.limit(limit))).scalars().all()
        )

        # Fallback: if time_range filter yielded 0 events (e.g. seeded historical events),
        # check if 'all' has events when user didn't explicitly restrict or if we want accurate state
        latest_ingested_iso = (
            max((e.ingested_at for e in events_in_window if e.ingested_at), default=None)
        )
        latest_ingested_str = latest_ingested_iso.isoformat() if latest_ingested_iso else None
        cache_key = f"{theme_key}:{norm_time_range}"

        if not force_refresh:
            cached = await self._get_cached_summary(
                session=session,
                cache_key=cache_key,
                latest_ingested_iso=latest_ingested_str,
                total_events_count=len(events_in_window),
            )
            if cached is not None:
                if self.last_generation_time is None and cached.get("generated_at"):
                    try:
                        self.last_generation_time = datetime.fromisoformat(cached["generated_at"])
                        self.last_generation_event_count = len(events_in_window)
                        self.last_generation_latest_ingested = latest_ingested_str
                    except Exception:
                        pass
                return cached

        # 1. Obtain full Embedding Relevance Map data (auto-embeds any un-embedded events,
        # computes KMeans semantic clusters, pairwise cosine similarity edges, and trustworthiness)
        emb_service = get_embedding_service()
        map_data = await emb_service.get_relevance_map_data(
            session=session,
            theme_id=theme_obj.id if theme_obj is not None else None,
            time_range_hours=hours,
            limit=limit,
            force_reproject=False,
        )

        nodes: List[Dict[str, Any]] = map_data.get("nodes", [])
        edges: List[Dict[str, Any]] = map_data.get("edges", [])
        raw_clusters: List[Dict[str, Any]] = map_data.get("clusters", [])
        map_stats: Dict[str, Any] = map_data.get("stats", {})
        active_embedding_model = map_stats.get("active_model", emb_service.default_model)

        event_by_id: Dict[str, Event] = {e.id: e for e in events_in_window}
        node_by_id: Dict[str, Dict[str, Any]] = {n["event_id"]: n for n in nodes}

        # 2. Build enriched event citation objects combining DB Event + Embedding Node metrics
        citation_by_id: Dict[str, Dict[str, Any]] = {}
        entity_counter: Counter = Counter()
        location_counter: Counter = Counter()
        category_counter: Counter = Counter()
        source_breakdown: Counter = Counter({"rss": 0, "telegram": 0, "newsapi": 0, "reddit": 0, "ais": 0})

        since_6h = now - timedelta(hours=6)
        since_24h = now - timedelta(hours=24)
        events_last_6h = 0
        events_prev_18h = 0
        critical_alerts_count = 0
        scores_list: List[int] = []

        for ev in events_in_window:
            n_info = node_by_id.get(ev.id, {})
            trust_info = n_info.get("trustworthiness", {})
            t_score = int(trust_info.get("score", 50))
            t_label = str(trust_info.get("label", "Developing / Verified"))
            corrob_count = int(trust_info.get("corroboration_count", 0))
            corrob_sources = trust_info.get("corroborating_sources", [ev.source_name])

            imp_score = int(ev.importance_score or 5)
            scores_list.append(imp_score)
            if imp_score >= alert_threshold:
                critical_alerts_count += 1

            if ev.source_type:
                source_breakdown[ev.source_type] += 1

            pub_dt = ev.published_at
            if pub_dt:
                if pub_dt.tzinfo is None:
                    pub_dt = pub_dt.replace(tzinfo=timezone.utc)
                if pub_dt >= since_6h:
                    events_last_6h += 1
                elif pub_dt >= since_24h:
                    events_prev_18h += 1

            for ent in (ev.entities_json or []):
                clean_ent = str(ent).strip()
                if clean_ent:
                    entity_counter[clean_ent] += 1

            if isinstance(ev.location_json, dict) and ev.location_json:
                loc_name = ev.location_json.get("name") or ev.location_json.get("country")
                if loc_name:
                    location_counter[str(loc_name).strip()] += 1
            elif isinstance(ev.location_json, str) and ev.location_json.strip():
                location_counter[ev.location_json.strip()] += 1

            trend_cat = (ev.metadata_json or {}).get("trend_category")
            if trend_cat:
                category_counter[str(trend_cat).strip()] += 1

            citation_by_id[ev.id] = {
                "id": ev.id,
                "title": ev.title,
                "headline": ev.headline or ev.title,
                "summary": ev.summary or ev.translated_text or ev.raw_text[:280],
                "raw_text": ev.raw_text,
                "translated_text": ev.translated_text,
                "source_name": ev.source_name,
                "source_type": ev.source_type,
                "source_url": ev.source_url,
                "importance_score": imp_score,
                "score_rationale": ev.score_rationale,
                "published_at": ev.published_at.isoformat() if ev.published_at else now.isoformat(),
                "ingested_at": ev.ingested_at.isoformat() if ev.ingested_at else now.isoformat(),
                "entities_json": ev.entities_json or [],
                "location_json": ev.location_json,
                "cluster_id": n_info.get("cluster_id"),
                "cluster_label": n_info.get("cluster_label"),
                "trustworthiness_score": t_score,
                "trustworthiness_label": t_label,
                "corroboration_count": corrob_count,
                "corroborating_sources": corrob_sources,
            }

        # 3. Group into Semantic Storyline Clusters (cluster_id > 0) and Unverified/Isolated Outliers
        clusters_meta: List[Dict[str, Any]] = []
        outlier_events: List[Dict[str, Any]] = []

        for c in raw_clusters:
            cid = int(c.get("id", 0))
            c_event_ids = c.get("event_ids", [])
            c_events = [citation_by_id[eid] for eid in c_event_ids if eid in citation_by_id]
            if not c_events:
                continue

            c_events.sort(
                key=lambda x: (x["importance_score"], x["trustworthiness_score"]),
                reverse=True,
            )

            if cid == 0:
                outlier_events.extend(c_events)
                continue

            c_scores = [x["importance_score"] for x in c_events]
            c_trusts = [x["trustworthiness_score"] for x in c_events]
            avg_imp = round(sum(c_scores) / len(c_scores), 1)
            max_imp = max(c_scores)
            avg_trust = round(sum(c_trusts) / len(c_trusts))

            ind_sources = list(dict.fromkeys(x["source_name"] for x in c_events))
            src_types = list(dict.fromkeys(x["source_type"] for x in c_events))

            c_ent_counter: Counter = Counter()
            for x in c_events:
                for ent in (x.get("entities_json") or []):
                    c_ent_counter[ent] += 1
            c_top_ents = [name for name, _ in c_ent_counter.most_common(6)]

            clusters_meta.append({
                "cluster_id": cid,
                "label": c.get("label") or c_events[0]["headline"][:55],
                "report_count": len(c_events),
                "avg_importance_score": avg_imp,
                "max_importance_score": max_imp,
                "avg_trustworthiness_score": avg_trust,
                "trustworthiness_label": _derive_trust_label_from_score(avg_trust),
                "independent_sources": ind_sources,
                "source_types": src_types,
                "top_entities": c_top_ents,
                "center_x": c.get("center_x", 0.0),
                "center_y": c.get("center_y", 0.0),
                "events": c_events,
            })

            # Also surface high-importance single-source unverified reports in the outlier watch list
            for x in c_events:
                if (
                    x["trustworthiness_label"] in ("Single-Source Unverified", "Isolated Outlier")
                    and x["importance_score"] >= 7
                    and x not in outlier_events
                ):
                    outlier_events.append(x)

        # Sort clusters by max importance score and report count
        clusters_meta.sort(
            key=lambda x: (x["max_importance_score"], x["avg_trustworthiness_score"], x["report_count"]),
            reverse=True,
        )
        outlier_events.sort(key=lambda x: x["importance_score"], reverse=True)

        # 4. Compute Fused Telemetry Snapshot
        total_events = len(events_in_window)
        embedded_events = len(nodes)
        coverage_pct = round((embedded_events / total_events * 100), 1) if total_events > 0 else 100.0
        corroborated_count = sum(
            1 for c_ev in citation_by_id.values() if c_ev["trustworthiness_score"] >= 60
        )
        corroboration_rate_pct = (
            round((corroborated_count / total_events) * 100) if total_events > 0 else 0
        )

        # Velocity calculation: compare 6h hourly rate vs previous 18h hourly rate
        rate_6h = events_last_6h / 6.0
        rate_prev_18h = max(0.2, events_prev_18h / 18.0)
        if events_last_6h >= 6 and rate_6h >= rate_prev_18h * 1.6:
            velocity_status = "Surge"
        elif events_last_6h >= 3 and rate_6h >= rate_prev_18h * 1.15:
            velocity_status = "Elevated"
        elif total_events == 0 or (events_last_6h == 0 and events_prev_18h == 0):
            velocity_status = "Quiet"
        else:
            velocity_status = "Normal"

        telemetry = {
            "total_events": total_events,
            "embedded_events": embedded_events,
            "embedding_coverage_pct": coverage_pct,
            "corroborated_reports_count": corroborated_count,
            "corroboration_rate_pct": corroboration_rate_pct,
            "cross_source_edges_count": len(edges),
            "total_clusters": len(clusters_meta),
            "isolated_outliers_count": len(outlier_events),
            "critical_alerts_count": critical_alerts_count,
            "avg_importance_score": round(sum(scores_list) / len(scores_list), 1) if scores_list else 0.0,
            "max_importance_score": max(scores_list) if scores_list else 0,
            "velocity_status": velocity_status,
            "events_last_6h": events_last_6h,
            "events_prev_18h": events_prev_18h,
            "source_breakdown": dict(source_breakdown),
            "top_entities": [{"name": k, "count": v} for k, v in entity_counter.most_common(12)],
            "top_locations": [{"name": k, "count": v} for k, v in location_counter.most_common(8)],
            "top_categories": [{"name": k, "count": v} for k, v in category_counter.most_common(6)],
        }

        # 5. Execute AI Synthesis via PipelineOrchestrator fallback chain
        orchestrator = get_pipeline_orchestrator()
        sum_stage_cfg = orchestrator.get_stage_config("summarization")
        active_ai_model = f"{sum_stage_cfg.provider}:{sum_stage_cfg.model_name}"

        def fallback_fn() -> SituationBriefingLLMOutput:
            return _build_deterministic_summary(
                theme_name=theme_name,
                alert_threshold=alert_threshold,
                clusters_meta=clusters_meta,
                outlier_events=outlier_events,
                telemetry=telemetry,
            )

        if total_events == 0 or orchestrator.use_mock:
            llm_output = fallback_fn()
            if orchestrator.use_mock:
                active_ai_model = "deterministic-fusion-engine"
        else:
            prompt_text = self._build_llm_prompt(
                theme_name=theme_name,
                theme_obj=theme_obj,
                alert_threshold=alert_threshold,
                time_range=norm_time_range,
                clusters_meta=clusters_meta,
                outlier_events=outlier_events,
                telemetry=telemetry,
            )

            async def _runner(
                provider: BaseAIProvider,
                model: Optional[str] = None,
                temperature: Optional[float] = None,
                raise_on_error: bool = True,
            ) -> SituationBriefingLLMOutput:
                nonlocal active_ai_model
                active_ai_model = f"{provider.provider_name}:{model or sum_stage_cfg.model_name}"
                raw_resp = await provider.generate_text(
                    prompt=prompt_text,
                    system_prompt=SITUATION_SUMMARY_SYSTEM_PROMPT,
                    model=model,
                    temperature=temperature if temperature is not None else 0.2,
                    max_tokens=3072,
                    json_mode=True,
                )
                return extract_and_parse_json(
                    raw_text=raw_resp,
                    target_model=SituationBriefingLLMOutput,
                    fallback_factory=fallback_fn,
                )

            try:
                llm_output: SituationBriefingLLMOutput = await asyncio.wait_for(
                    orchestrator._execute_stage_with_fallbacks(
                        stage_id="summarization",
                        runner_fn=_runner,
                        fallback_heuristic_fn=fallback_fn,
                    ),
                    timeout=18.0,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "Situation summary LLM synthesis exceeded 18s budget — cleanly failing over to deterministic engine."
                )
                llm_output = fallback_fn()
                active_ai_model = "deterministic-fusion-engine (timeout fallback)"
            except Exception as exc:
                logger.warning(f"Situation summary LLM synthesis fell back to deterministic engine: {exc}")
                llm_output = fallback_fn()

        # 6. Merge LLM cluster narratives with rich cluster metadata
        deterministic_ref = _build_deterministic_summary(
            theme_name=theme_name,
            alert_threshold=alert_threshold,
            clusters_meta=clusters_meta,
            outlier_events=outlier_events,
            telemetry=telemetry,
        )
        det_cluster_by_id = {c.cluster_id: c for c in deterministic_ref.cluster_assessments}
        llm_cluster_by_id = {c.cluster_id: c for c in (llm_output.cluster_assessments or [])}

        final_clusters: List[Dict[str, Any]] = []
        for idx, c_meta in enumerate(clusters_meta):
            cid = c_meta["cluster_id"]
            llm_c = llm_cluster_by_id.get(cid)
            if llm_c is None and idx < len(llm_output.cluster_assessments or []):
                llm_c = llm_output.cluster_assessments[idx]
            det_c = det_cluster_by_id.get(cid)

            title_val = (
                (llm_c.title if llm_c and llm_c.title else None)
                or (det_c.title if det_c else None)
                or c_meta["label"]
            )
            exec_sum_val = (
                (llm_c.executive_summary if llm_c and llm_c.executive_summary else None)
                or (det_c.executive_summary if det_c else "")
            )
            imp_val = (
                (llm_c.operational_implication if llm_c and llm_c.operational_implication else None)
                or (det_c.operational_implication if det_c else "")
            )
            t_level = (
                (llm_c.threat_level if llm_c and llm_c.threat_level else None)
                or _derive_cluster_threat_level(
                    c_meta["max_importance_score"], c_meta["avg_importance_score"]
                )
            )

            final_clusters.append({
                "cluster_id": cid,
                "label": c_meta["label"],
                "title": title_val,
                "executive_summary": exec_sum_val,
                "operational_implication": imp_val,
                "threat_level": t_level,
                "report_count": c_meta["report_count"],
                "avg_importance_score": c_meta["avg_importance_score"],
                "max_importance_score": c_meta["max_importance_score"],
                "avg_trustworthiness_score": c_meta["avg_trustworthiness_score"],
                "trustworthiness_label": c_meta["trustworthiness_label"],
                "independent_sources": c_meta["independent_sources"],
                "source_types": c_meta["source_types"],
                "top_entities": c_meta["top_entities"],
                "center_x": c_meta["center_x"],
                "center_y": c_meta["center_y"],
                "events": c_meta["events"],
            })

        # Pair key developments bullets with top events for interactive inspection
        all_top_events = sorted(
            list(citation_by_id.values()),
            key=lambda x: (x["importance_score"], x["trustworthiness_score"]),
            reverse=True,
        )
        raw_dev_bullets = llm_output.key_developments or deterministic_ref.key_developments
        structured_developments: List[Dict[str, Any]] = []
        for idx, bullet in enumerate(raw_dev_bullets[:6]):
            matched_ev = all_top_events[idx] if idx < len(all_top_events) else None
            structured_developments.append({
                "bullet": bullet,
                "event_id": matched_ev["id"] if matched_ev else None,
                "headline": matched_ev["headline"] if matched_ev else None,
                "importance_score": matched_ev["importance_score"] if matched_ev else None,
                "source_name": matched_ev["source_name"] if matched_ev else None,
                "trustworthiness_label": matched_ev["trustworthiness_label"] if matched_ev else None,
                "trustworthiness_score": matched_ev["trustworthiness_score"] if matched_ev else None,
            })

        synth_parts = active_ai_model.split(":", 1) if ":" in active_ai_model else [
            "system" if "deterministic" in active_ai_model.lower() else "gemini",
            active_ai_model,
        ]
        synth_prov = {"provider": synth_parts[0], "model": synth_parts[1]}

        emb_parts = active_embedding_model.split(":", 1) if ":" in active_embedding_model else [
            "huggingface" if ("all-MiniLM" in active_embedding_model or "/" in active_embedding_model) else "gemini",
            active_embedding_model,
        ]
        emb_prov = {"provider": emb_parts[0], "model": emb_parts[1]}

        result_payload: Dict[str, Any] = {
            "id": str(uuid.uuid4()),
            "generated_at": utc_now().isoformat(),
            "cached": False,
            "theme_id": theme_key,
            "theme_name": theme_name,
            "time_range": norm_time_range,
            "active_ai_model": active_ai_model,
            "active_embedding_model": active_embedding_model,
            "ai_provenance": {
                "synthesizer": synth_prov,
                "embedding": emb_prov,
            },
            "threat_trajectory": llm_output.threat_trajectory or deterministic_ref.threat_trajectory,
            "threat_level_score": llm_output.threat_level_score or deterministic_ref.threat_level_score,
            "executive_bluf": llm_output.executive_bluf or deterministic_ref.executive_bluf,
            "strategic_assessment": llm_output.strategic_assessment or deterministic_ref.strategic_assessment,
            "key_developments": structured_developments,
            "clusters": final_clusters,
            "unverified_outliers": {
                "assessment": (
                    llm_output.unverified_outlier_assessment
                    or deterministic_ref.unverified_outlier_assessment
                ),
                "events": outlier_events[:10],
            },
            "watchlist_indicators": (
                llm_output.watchlist_indicators or deterministic_ref.watchlist_indicators
            ),
            "telemetry": telemetry,
        }

        await self._save_cached_summary(
            session=session,
            cache_key=cache_key,
            latest_ingested_iso=latest_ingested_str,
            total_events_count=total_events,
            payload=result_payload,
        )
        self.record_generation(utc_now(), total_events, latest_ingested_str)

        return result_payload

    def _build_llm_prompt(
        self,
        theme_name: str,
        theme_obj: Optional[Theme],
        alert_threshold: int,
        time_range: str,
        clusters_meta: List[Dict[str, Any]],
        outlier_events: List[Dict[str, Any]],
        telemetry: Dict[str, Any],
    ) -> str:
        """
        Build a compact, token-efficient, high-signal prompt incorporating
        theme context, telemetry, semantic embedding clusters, and outlier reports.
        """
        lines = [
            f"=== SITUATIONAL BRIEFING CONTEXT ===",
            f"Monitored Theme: {theme_name}",
            f"Time Window: {time_range}",
            f"Alert Threshold: {alert_threshold}/10",
        ]
        if theme_obj and theme_obj.description:
            lines.append(f"Theme Focus: {theme_obj.description}")
        if theme_obj and theme_obj.keywords:
            lines.append(f"Tracked Keywords: {', '.join(theme_obj.keywords[:12])}")

        lines.extend([
            "",
            "=== FUSED SYSTEM TELEMETRY & EMBEDDING METRICS ===",
            f"- Total Reports Analyzed: {telemetry['total_events']} (Embedding Coverage: {telemetry['embedding_coverage_pct']}%)",
            f"- Multi-Source Corroboration Rate: {telemetry['corroboration_rate_pct']}% ({telemetry['corroborated_reports_count']} corroborated reports, {telemetry['cross_source_edges_count']} cross-source similarity edges)",
            f"- Critical Flash Alerts (Score >= {alert_threshold}): {telemetry['critical_alerts_count']} (Max Score: {telemetry['max_importance_score']}/10, Avg Score: {telemetry['avg_importance_score']}/10)",
            f"- Ingestion Velocity: {telemetry['velocity_status']} ({telemetry['events_last_6h']} events in last 6h vs {telemetry['events_prev_18h']} in prior 18h)",
            f"- Source Mix: {telemetry['source_breakdown']}",
            f"- Top Entities: {', '.join(str(e['name']) + ' (' + str(e['count']) + ')' for e in telemetry['top_entities'][:10])}",
            f"- Top Locations: {', '.join(str(l['name']) + ' (' + str(l['count']) + ')' for l in telemetry['top_locations'][:6])}",
        ])

        lines.append("")
        lines.append(f"=== SEMANTIC EMBEDDING STORYLINE CLUSTERS ({len(clusters_meta)} clusters) ===")
        for c in clusters_meta[:8]:
            lines.append(
                f"\n[Cluster ID: {c['cluster_id']}] Label: \"{c['label']}\" | "
                f"Reports: {c['report_count']} | Max Score: {c['max_importance_score']}/10 | "
                f"Avg Embedding Trust: {c['avg_trustworthiness_score']}% ({c['trustworthiness_label']}) | "
                f"Sources: {', '.join(c['independent_sources'][:5])}"
            )
            for ev in c["events"][:5]:
                sum_text = (ev.get("summary") or ev.get("headline") or "").replace("\n", " ").strip()
                if len(sum_text) > 220:
                    sum_text = sum_text[:217] + "..."
                lines.append(
                    f"  * ({ev['source_name']} | Score {ev['importance_score']}/10 | Trust {ev['trustworthiness_score']}% {ev['trustworthiness_label']}) "
                    f"{ev['headline']}: {sum_text}"
                )

        lines.append("")
        lines.append(f"=== UNVERIFIED / ISOLATED OUTLIER REPORTS ({len(outlier_events)} items) ===")
        if outlier_events:
            for o_ev in outlier_events[:6]:
                sum_text = (o_ev.get("summary") or o_ev.get("headline") or "").replace("\n", " ").strip()
                if len(sum_text) > 180:
                    sum_text = sum_text[:177] + "..."
                lines.append(
                    f"  * [OUTLIER - {o_ev['trustworthiness_label']}] ({o_ev['source_name']} | Score {o_ev['importance_score']}/10) "
                    f"{o_ev['headline']}: {sum_text}"
                )
        else:
            lines.append("  * None.")

        lines.extend([
            "",
            "Provide valid JSON matching SituationBriefingLLMOutput with keys:",
            "threat_trajectory, threat_level_score, executive_bluf, strategic_assessment, "
            "cluster_assessments (list of {cluster_id, title, executive_summary, operational_implication, threat_level}), "
            "key_developments (list of strings), unverified_outlier_assessment, watchlist_indicators (list of strings).",
        ])
        return "\n".join(lines)


_summary_service: Optional[SituationSummaryService] = None


def get_summary_service() -> SituationSummaryService:
    global _summary_service
    if _summary_service is None:
        _summary_service = SituationSummaryService()
    return _summary_service
