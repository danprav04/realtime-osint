"""
backend/app/services/memory_rollup_service.py - Tiered Memory & Historical Rollup Service
========================================================================================
Manages compaction of aged intelligence events (>7 days) into high-signal semantic rollups,
calculates longitudinal entity/location baselines, prunes dense high-dimensional vectors for
routine events to save RAM and storage on constrained hosts, and provides historical precedent
matching for real-time analysis stages.
"""

import asyncio
import logging
from collections import Counter
from datetime import datetime, date, time, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple, Union

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models import Event, EventEmbedding, HistoricalRollup, Theme, utc_now
from backend.app.services.embedding_service import (
    compute_recency_weight,
    generate_deterministic_mock_embedding,
    get_embedding_service,
)

logger = logging.getLogger("osint.services.memory_rollup")


class MemoryRollupService:
    """
    Tiered memory and compaction engine.
    - Aggregates aged raw events into semantic HistoricalRollup rows with summary vectors and entity/location baselines.
    - Prunes memory-heavy 3072d vectors for routine events (<7) older than age_threshold_days while keeping raw Event rows intact.
    - Provides historical precedent search and baseline entity velocity metrics.
    """

    def __init__(self):
        self._compaction_lock = asyncio.Lock()

    async def compact_aged_events(
        self,
        session: AsyncSession,
        theme_id: Optional[int] = None,
        age_threshold_days: int = 7,
        clear_routine_vectors: bool = True,
        use_mock_embedding: bool = False,
    ) -> Dict[str, Any]:
        """
        Scan for analyzed events older than age_threshold_days and compact them into
        daily HistoricalRollup epochs. Reclaims vector memory for routine events (< 7).
        """
        async with self._compaction_lock:
            now = utc_now()
            cutoff_date = (now - timedelta(days=age_threshold_days)).replace(
                hour=23, minute=59, second=59, microsecond=999999
            )

            # Query all eligible analyzed events older than cutoff
            query = (
                select(Event)
                .where((Event.is_analyzed == True) & (Event.published_at <= cutoff_date))
                .order_by(Event.published_at.asc())
            )
            if theme_id is not None:
                query = query.where(Event.theme_id == theme_id)

            events = list((await session.execute(query)).scalars().all())
            if not events:
                logger.info("[memory-rollup] No eligible aged events found for compaction.")
                return {
                    "rollups_created": 0,
                    "rollups_updated": 0,
                    "vectors_pruned": 0,
                    "events_processed": 0,
                    "cutoff_date": cutoff_date.isoformat(),
                }

            # Group events by (theme_id, calendar_date)
            grouped_by_epoch: Dict[Tuple[int, date], List[Event]] = {}
            for ev in events:
                if not ev.published_at:
                    continue
                ev_date = ev.published_at.date()
                key = (ev.theme_id, ev_date)
                grouped_by_epoch.setdefault(key, []).append(ev)

            rollups_created = 0
            rollups_updated = 0
            routine_event_ids_to_prune: List[str] = []

            emb_service = get_embedding_service()

            for (th_id, epoch_d), epoch_events in grouped_by_epoch.items():
                date_str = epoch_d.strftime("%Y-%m-%d")
                epoch_key = f"daily_{th_id}_{date_str}"

                # Check if rollup exists
                stmt_chk = select(HistoricalRollup).where(
                    (HistoricalRollup.theme_id == th_id) & (HistoricalRollup.epoch_key == epoch_key)
                )
                existing_rollup = (await session.execute(stmt_chk)).scalars().first()

                # Calculate aggregation statistics
                total_ev = len(epoch_events)
                critical_events = [e for e in epoch_events if (e.importance_score or 0) >= 7]
                critical_count = len(critical_events)
                max_score = max((e.importance_score or 5) for e in epoch_events)
                avg_score = round(sum((e.importance_score or 5) for e in epoch_events) / max(1, total_ev), 2)

                # Entity frequencies
                entity_counter: Counter = Counter()
                for e in epoch_events:
                    for ent in (e.entities_json or []):
                        ent_str = str(ent).strip()
                        if ent_str:
                            entity_counter[ent_str] += 1

                # Location frequencies
                loc_counter: Counter = Counter()
                for e in epoch_events:
                    loc_val = ""
                    if isinstance(e.location_json, dict):
                        loc_val = e.location_json.get("name") or e.location_json.get("country") or ""
                    elif isinstance(e.location_json, str):
                        loc_val = e.location_json
                    loc_val = loc_val.strip()
                    if loc_val:
                        loc_counter[loc_val] += 1

                # Category frequencies
                cat_counter: Counter = Counter()
                for e in epoch_events:
                    cat_val = (getattr(e, "category", None) or (e.metadata_json or {}).get("trend_category") or "General Intelligence").strip()
                    cat_counter[cat_val] += 1

                # Source statistics
                src_counter: Counter = Counter()
                src_score_sums: Dict[str, float] = {}
                for e in epoch_events:
                    s_name = (e.source_name or "Unknown").strip()
                    src_counter[s_name] += 1
                    src_score_sums[s_name] = src_score_sums.get(s_name, 0.0) + float(e.importance_score or 5)

                source_stats: Dict[str, Any] = {}
                for s_name, count in src_counter.items():
                    source_stats[s_name] = {
                        "count": count,
                        "avg_importance": round(src_score_sums[s_name] / max(1, count), 1),
                    }

                # Notable events summary (top 8 highest-importance events)
                sorted_events = sorted(epoch_events, key=lambda x: (x.importance_score or 0), reverse=True)
                notable_list = [
                    {
                        "event_id": e.id,
                        "headline": e.headline or e.title,
                        "source_name": e.source_name,
                        "importance_score": e.importance_score or 5,
                        "published_at": e.published_at.isoformat() if e.published_at else None,
                        "location": e.location_json,
                    }
                    for e in sorted_events[:8]
                ]

                # Synthesize executive summary text
                top_entities_list = [f"{k} ({v})" for k, v in entity_counter.most_common(5)]
                top_locs_list = [f"{k} ({v})" for k, v in loc_counter.most_common(4)]
                key_headlines = [f"• {e.headline or e.title}" for e in sorted_events[:4] if (e.headline or e.title)]

                exec_summary_parts = [
                    f"Operational epoch {date_str}: {total_ev} total events recorded "
                    f"({critical_count} critical alerts, peak importance {max_score}/10, average {avg_score}/10).",
                ]
                if top_entities_list:
                    exec_summary_parts.append(f"Primary focal actors: {', '.join(top_entities_list)}.")
                if top_locs_list:
                    exec_summary_parts.append(f"Active geographic sectors: {', '.join(top_locs_list)}.")
                if key_headlines:
                    exec_summary_parts.append("Key developments:\n" + "\n".join(key_headlines))

                executive_summary = "\n".join(exec_summary_parts)

                # Generate summary embedding vector
                try:
                    summary_emb_text = f"Epoch {date_str} Summary\n{executive_summary}"
                    vectors, model_name = await emb_service.generate_embeddings_batch(
                        texts=[summary_emb_text],
                        use_mock=use_mock_embedding,
                        session=session,
                    )
                    summary_vec = vectors[0] if vectors else generate_deterministic_mock_embedding(summary_emb_text)
                except Exception as e:
                    logger.warning(f"[memory-rollup] Embedding generation failed for {epoch_key}, using mock: {e}")
                    summary_vec = generate_deterministic_mock_embedding(executive_summary)
                    model_name = "mock-summary-3072"

                p_start = datetime.combine(epoch_d, time.min, tzinfo=timezone.utc)
                p_end = datetime.combine(epoch_d, time.max, tzinfo=timezone.utc)

                if existing_rollup:
                    existing_rollup.total_events = total_ev
                    existing_rollup.critical_events_count = critical_count
                    existing_rollup.max_importance_score = max_score
                    existing_rollup.avg_importance_score = avg_score
                    existing_rollup.executive_summary = executive_summary
                    existing_rollup.entity_counts_json = dict(entity_counter.most_common(30))
                    existing_rollup.location_counts_json = dict(loc_counter.most_common(20))
                    existing_rollup.category_counts_json = dict(cat_counter)
                    existing_rollup.source_stats_json = source_stats
                    existing_rollup.notable_events_json = notable_list
                    existing_rollup.embedding_vector = summary_vec
                    existing_rollup.embedding_model_name = model_name
                    existing_rollup.is_compacted = True
                    rollups_updated += 1
                else:
                    new_rollup = HistoricalRollup(
                        theme_id=th_id,
                        period_start=p_start,
                        period_end=p_end,
                        epoch_key=epoch_key,
                        total_events=total_ev,
                        critical_events_count=critical_count,
                        max_importance_score=max_score,
                        avg_importance_score=avg_score,
                        executive_summary=executive_summary,
                        storylines_json=[],
                        entity_counts_json=dict(entity_counter.most_common(30)),
                        location_counts_json=dict(loc_counter.most_common(20)),
                        category_counts_json=dict(cat_counter),
                        source_stats_json=source_stats,
                        notable_events_json=notable_list,
                        embedding_vector=summary_vec,
                        embedding_model_name=model_name,
                        is_compacted=True,
                    )
                    session.add(new_rollup)
                    rollups_created += 1

                # Gather routine event IDs for vector memory reclamation
                for e in epoch_events:
                    score = e.importance_score or 5
                    if score < 7:
                        routine_event_ids_to_prune.append(e.id)

            # Vector pruning: Clear embedding_vector = [] for routine events older than cutoff
            vectors_pruned = 0
            if clear_routine_vectors and routine_event_ids_to_prune:
                batch_size = 500
                for i in range(0, len(routine_event_ids_to_prune), batch_size):
                    batch_ids = routine_event_ids_to_prune[i : i + batch_size]
                    stmt_prune = (
                        update(EventEmbedding)
                        .where(
                            (EventEmbedding.event_id.in_(batch_ids))
                            & (EventEmbedding.embedding_vector != [])
                        )
                        .values(embedding_vector=[])
                    )
                    res = await session.execute(stmt_prune)
                    vectors_pruned += res.rowcount or 0

            await session.commit()
            logger.info(
                f"[memory-rollup] Compaction complete: {rollups_created} created, {rollups_updated} updated, "
                f"{vectors_pruned} routine vectors pruned, {len(events)} events processed."
            )
            return {
                "status": "ok",
                "rollups_created": rollups_created,
                "rollups_updated": rollups_updated,
                "vectors_pruned": vectors_pruned,
                "events_processed": len(events),
                "cutoff_date": cutoff_date.isoformat(),
            }

    async def get_historical_context_and_baselines(
        self,
        session: AsyncSession,
        theme_id: Union[int, str],
        candidate_entities: List[str],
        candidate_location: str = "",
        limit: int = 3,
    ) -> Dict[str, Any]:
        """
        Query historical rollups for baseline entity velocity and precedents matching
        the candidate's entities and location.
        """
        th_id: Optional[int] = None
        if isinstance(theme_id, int):
            th_id = theme_id
        elif isinstance(theme_id, str) and theme_id.isdigit():
            th_id = int(theme_id)
        else:
            stmt_t = select(Theme.id).where(Theme.id == theme_id)
            th_id = (await session.execute(stmt_t)).scalar_one_or_none()

        if th_id is None:
            return {"precedents": [], "entity_baselines": {}}

        # Fetch up to 30 most recent daily rollups
        stmt = (
            select(HistoricalRollup)
            .where(HistoricalRollup.theme_id == th_id)
            .order_by(HistoricalRollup.period_start.desc())
            .limit(30)
        )
        rollups = list((await session.execute(stmt)).scalars().all())
        if not rollups:
            return {"precedents": [], "entity_baselines": {}}

        # 1. Compute baseline entity velocity
        norm_candidates = [e.strip().lower() for e in candidate_entities if e.strip()]
        entity_baselines: Dict[str, Any] = {}
        total_epochs = len(rollups)

        for ent_raw in candidate_entities:
            ent_key = ent_raw.strip()
            if not ent_key:
                continue
            ent_lower = ent_key.lower()

            counts_per_epoch: List[int] = []
            for r in rollups:
                epoch_counts = r.entity_counts_json or {}
                # Match entity case-insensitively
                match_val = sum(v for k, v in epoch_counts.items() if k.lower() == ent_lower)
                counts_per_epoch.append(match_val)

            avg_mentions = sum(counts_per_epoch) / max(1, total_epochs)
            max_mentions = max(counts_per_epoch) if counts_per_epoch else 0
            active_days = sum(1 for c in counts_per_epoch if c > 0)

            entity_baselines[ent_key] = {
                "daily_baseline_average": round(avg_mentions, 2),
                "peak_daily_mentions": max_mentions,
                "days_active_in_history": active_days,
                "total_epochs_observed": total_epochs,
            }

        # 2. Score rollups for relevance to candidate
        scored_rollups: List[Tuple[HistoricalRollup, float]] = []
        cand_loc_lower = (candidate_location or "").strip().lower()

        for r in rollups:
            ent_match_score = 0.0
            epoch_ents = r.entity_counts_json or {}
            for k, v in epoch_ents.items():
                if k.lower() in norm_candidates:
                    ent_match_score += float(v)

            loc_match_score = 0.0
            if cand_loc_lower:
                epoch_locs = r.location_counts_json or {}
                for lk, lv in epoch_locs.items():
                    if cand_loc_lower in lk.lower() or lk.lower() in cand_loc_lower:
                        loc_match_score += float(lv) * 2.0

            total_match = ent_match_score + loc_match_score
            if total_match > 0:
                scored_rollups.append((r, total_match))

        scored_rollups.sort(key=lambda x: x[1], reverse=True)

        precedents = []
        for r, score in scored_rollups[:limit]:
            precedents.append({
                "epoch_key": r.epoch_key,
                "period_start": r.period_start.strftime("%Y-%m-%d"),
                "total_events": r.total_events,
                "critical_events_count": r.critical_events_count,
                "max_importance_score": r.max_importance_score,
                "executive_summary": r.executive_summary[:280] + "..." if len(r.executive_summary) > 280 else r.executive_summary,
                "notable_events": (r.notable_events_json or [])[:3],
                "relevance_score": round(score, 1),
            })

        return {
            "precedents": precedents,
            "entity_baselines": entity_baselines,
        }

    async def get_rollups(
        self,
        session: AsyncSession,
        theme_id: Optional[Union[int, str]] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """Query historical rollups formatted as dicts."""
        stmt = select(HistoricalRollup).order_by(HistoricalRollup.period_start.desc())

        if theme_id is not None and str(theme_id).lower() != "all":
            if isinstance(theme_id, int) or str(theme_id).isdigit():
                stmt = stmt.where(HistoricalRollup.theme_id == int(theme_id))

        if start_date:
            stmt = stmt.where(HistoricalRollup.period_start >= start_date)
        if end_date:
            stmt = stmt.where(HistoricalRollup.period_end <= end_date)

        stmt = stmt.limit(limit)
        rollups = list((await session.execute(stmt)).scalars().all())
        return [r.to_dict() for r in rollups]

    async def get_dynamic_source_credibility(
        self,
        session: AsyncSession,
        theme_id: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        Calculate dynamic source credibility leaderboard based on historical reporting
        and corroboration performance.
        """
        now = utc_now()
        stmt = (
            select(
                Event.source_name,
                Event.source_type,
                func.count(Event.id).label("total_reports"),
                func.avg(Event.importance_score).label("avg_score"),
                func.max(Event.published_at).label("latest_report"),
            )
            .where(Event.is_analyzed == True)
            .group_by(Event.source_name, Event.source_type)
        )
        if theme_id is not None:
            stmt = stmt.where(Event.theme_id == theme_id)

        rows = (await session.execute(stmt)).all()
        if not rows:
            return []

        # Get corroboration and contradiction metrics from event embeddings
        emb_query = (
            select(
                Event.source_name,
                func.avg(EventEmbedding.trustworthiness_score).label("avg_trust"),
                func.sum(EventEmbedding.corroboration_count).label("total_corrob"),
            )
            .join(EventEmbedding, Event.id == EventEmbedding.event_id)
            .group_by(Event.source_name)
        )
        if theme_id is not None:
            emb_query = emb_query.where(Event.theme_id == theme_id)

        emb_rows = {row[0]: row for row in (await session.execute(emb_query)).all()}

        leaderboard = []
        for row in rows:
            s_name = row.source_name
            s_type = row.source_type
            total_reports = int(row.total_reports or 0)
            avg_imp = round(float(row.avg_score or 5.0), 1)
            latest_pub = row.latest_report

            emb_data = emb_rows.get(s_name)
            avg_trust = round(float(emb_data.avg_trust), 1) if emb_data and emb_data.avg_trust else 50.0
            total_corrob = int(emb_data.total_corrob) if emb_data and emb_data.total_corrob else 0

            # Calculate dynamic credibility score [20-100]
            # Rewards multi-source corroboration, volume, and consistency
            corrob_rate = total_corrob / max(1, total_reports)
            credibility_score = int(round(avg_trust * 0.55 + min(25, corrob_rate * 12) + min(20, total_reports * 0.5)))
            credibility_score = max(20, min(99, credibility_score))

            if credibility_score >= 80:
                tier = "Tier 1 - Verified Anchor"
                badge_color = "emerald"
            elif credibility_score >= 65:
                tier = "Tier 2 - Reliable Cross-Source"
                badge_color = "blue"
            elif credibility_score >= 45:
                tier = "Tier 3 - Developing / Mixed"
                badge_color = "amber"
            else:
                tier = "Tier 4 - High Rumor / Contested"
                badge_color = "rose"

            recency_w = compute_recency_weight(latest_pub, now=now, half_life_hours=24.0)

            leaderboard.append({
                "source_name": s_name,
                "source_type": s_type,
                "credibility_score": credibility_score,
                "tier": tier,
                "badge_color": badge_color,
                "total_reports": total_reports,
                "corroboration_rate": round(corrob_rate, 2),
                "avg_importance_score": avg_imp,
                "latest_activity": latest_pub.isoformat() if latest_pub else None,
                "recency_weight": round(recency_w, 2),
            })

        leaderboard.sort(key=lambda x: x["credibility_score"], reverse=True)
        return leaderboard


# Global singleton
_memory_rollup_service: Optional[MemoryRollupService] = None


def get_memory_rollup_service() -> MemoryRollupService:
    global _memory_rollup_service
    if _memory_rollup_service is None:
        _memory_rollup_service = MemoryRollupService()
    return _memory_rollup_service
