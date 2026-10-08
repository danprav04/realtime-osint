"""
backend/app/pipeline/orchestrator.py - Asynchronous Multi-Stage AI Pipeline Orchestrator
"""

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Union

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.config import Settings, get_settings
from backend.app.db.models import Alert, Event, SystemSettings, Theme
from backend.app.db.session import get_session_factory
from backend.app.pipeline.models import (
    AIStageConfig,
    AnalyzedEvent,
    Stage1Result,
    Stage2Result,
    Stage3Result,
    Stage4Result,
)
from backend.app.pipeline.providers.factory import (
    ProviderRegistry,
    get_provider_registry,
    resolve_api_key,
)
from backend.app.pipeline.providers.base import BaseAIProvider
from backend.app.pipeline.stage1_extract import _heuristic_stage1_fallback, run_stage1_extract
from backend.app.pipeline.stage2_score import run_stage2_score
from backend.app.pipeline.stage3_summary import run_stage3_summary
from backend.app.pipeline.stage4_visuals import run_stage4_visuals
from backend.app.plugins.base import NormalizedEvent
from backend.app.services.redis_bus import (
    CHANNEL_CONFIG_RELOAD,
    CHANNEL_EVENTS_ALERT,
    CHANNEL_EVENTS_NEW,
    RedisBus,
    get_redis_bus,
)

logger = logging.getLogger("osint.pipeline.orchestrator")


class PipelineOrchestrator:
    """
    Asynchronous Multi-Stage AI Pipeline Orchestrator.
    Coordinates sequential execution of Stage 1 (Extraction & Translation),
    followed by concurrent execution of Stages 2, 3, and 4 via asyncio.gather().
    Enriches database Event records, persists Alerts, publishes to Redis bus,
    and supports dynamic zero-code hot-reload via Redis Pub/Sub.
    """

    def __init__(
        self,
        session_factory: Optional[async_sessionmaker[AsyncSession]] = None,
        redis_bus: Optional[RedisBus] = None,
        provider_registry: Optional[ProviderRegistry] = None,
        queue_maxsize: int = 500,
        num_workers: int = 2,
        use_mock: bool = False,
    ):
        self._session_factory = session_factory
        self._redis_bus = redis_bus
        self.use_mock = use_mock
        if provider_registry is not None:
            self._registry = provider_registry
        elif use_mock:
            self._registry = ProviderRegistry(use_mock=True)
        else:
            self._registry = get_provider_registry(use_mock=False)
        self._stage_configs: Dict[str, AIStageConfig] = {}
        self._theme_cache: Dict[str, Dict[str, Any]] = {}
        self._db_api_keys: Dict[str, str] = {}
        self._queue: asyncio.Queue[NormalizedEvent] = asyncio.Queue(maxsize=queue_maxsize)
        self._worker_tasks: List[asyncio.Task] = []
        self._num_workers = num_workers
        self._running: bool = False
        self._lock = asyncio.Lock()
        self._reload_subscribed: bool = False

        # Seed initial default stage configs
        self._init_default_configs()

    @property
    def is_running(self) -> bool:
        return self._running

    def _init_default_configs(self) -> None:
        self._stage_configs = {
            "extraction": AIStageConfig(
                stage_id="extraction",
                provider="gemini",
                model_name="gemini-3.5-flash-lite",
                temperature=0.2,
            ),
            "scoring": AIStageConfig(
                stage_id="scoring",
                provider="gemini",
                model_name="gemini-3.5-flash-lite",
                temperature=0.1,
            ),
            "summarization": AIStageConfig(
                stage_id="summarization",
                provider="gemini",
                model_name="gemini-3.5-flash-lite",
                temperature=0.2,
            ),
            "visuals": AIStageConfig(
                stage_id="visuals",
                provider="groq",
                model_name="qwen/qwen3.8-27b",
                temperature=0.2,
            ),
        }

    def _get_session_factory(self) -> async_sessionmaker[AsyncSession]:
        if self._session_factory is None:
            self._session_factory = get_session_factory()
        return self._session_factory

    def _get_redis_bus(self) -> RedisBus:
        if self._redis_bus is None:
            self._redis_bus = get_redis_bus()
        return self._redis_bus

    def _normalize_stage_id(self, stage_id: str) -> str:
        s = str(stage_id).lower().strip()
        if "extract" in s:
            return "extraction"
        if "score" in s:
            return "scoring"
        if "summar" in s:
            return "summarization"
        if "vis" in s:
            return "visuals"
        return s

    def get_stage_config(self, stage_id: str) -> AIStageConfig:
        norm = self._normalize_stage_id(stage_id)
        if norm not in self._stage_configs:
            self._stage_configs[norm] = AIStageConfig(stage_id=norm)  # type: ignore
        return self._stage_configs[norm]

    def set_stage_config(self, stage_id: str, config: AIStageConfig) -> None:
        norm = self._normalize_stage_id(stage_id)
        self._stage_configs[norm] = config

    async def start(self) -> None:
        """Start background queue consumers and subscribe to config reload."""
        async with self._lock:
            if self._running:
                return
            self._running = True
            logger.info("Starting Pipeline Orchestrator...")

            # Load initial configurations from DB if available
            try:
                await self._reload_configs_from_db()
            except Exception as exc:
                logger.warning(f"Could not load initial DB configs: {exc}")

            # Subscribe to Redis config reload channel
            try:
                bus = self._get_redis_bus()
                if not self._reload_subscribed:
                    bus.subscribe(CHANNEL_CONFIG_RELOAD, self.handle_config_reload)
                    self._reload_subscribed = True
            except Exception as bus_err:
                logger.warning(f"Failed to subscribe to Redis config reload: {bus_err}")

            # Start worker tasks
            for i in range(self._num_workers):
                task = asyncio.create_task(
                    self._queue_worker_loop(i),
                    name=f"pipeline_worker_{i}",
                )
                self._worker_tasks.append(task)

            # Backfill any existing unanalyzed events from database
            asyncio.create_task(self._backfill_unanalyzed_events())

            logger.info(f"Pipeline Orchestrator started with {self._num_workers} workers.")

    async def stop(self) -> None:
        """Cleanly stop worker tasks and unsubscribe."""
        async with self._lock:
            if not self._running:
                return
            self._running = False
            logger.info("Stopping Pipeline Orchestrator...")

            try:
                bus = self._get_redis_bus()
                if self._reload_subscribed:
                    bus.unsubscribe(CHANNEL_CONFIG_RELOAD, self.handle_config_reload)
                    self._reload_subscribed = False
            except Exception:
                pass

            for task in self._worker_tasks:
                task.cancel()
            if self._worker_tasks:
                await asyncio.gather(*self._worker_tasks, return_exceptions=True)
                self._worker_tasks.clear()

            await self._registry.close_all()
            logger.info("Pipeline Orchestrator stopped.")

    async def enqueue_event(self, event: NormalizedEvent) -> bool:
        """Put an incoming NormalizedEvent into the bounded processing queue."""
        try:
            self._queue.put_nowait(event)
            return True
        except asyncio.QueueFull:
            logger.warning(f"Pipeline queue full ({self._queue.maxsize} events). Dropping event {event.id}.")
            return False

    async def _queue_worker_loop(self, worker_id: int) -> None:
        """Worker loop continuously pulling and processing events from bounded queue."""
        logger.info(f"Pipeline worker {worker_id} started.")
        while self._running:
            try:
                event = await self._queue.get()
                try:
                    await self.process_event(event)
                except Exception as exc:
                    logger.error(f"Worker {worker_id} failed processing event {event.id}: {exc}", exc_info=True)
                finally:
                    self._queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as err:
                logger.error(f"Worker {worker_id} loop unexpected error: {err}")
                await asyncio.sleep(0.5)

    def _get_fallback_provider(self, primary_provider_name: str, stage_id: str) -> Optional[BaseAIProvider]:
        """
        If the primary provider hits 429 or fails, select an alternate healthy provider
        (e.g., failover Groq -> Gemini, or Gemini -> Groq) if its API key is configured.
        """
        if self.use_mock:
            return None
        p_norm = primary_provider_name.lower().strip()
        alt_provider = "gemini" if p_norm == "groq" else "groq"
        alt_key = resolve_api_key(alt_provider, None, self._db_api_keys)
        if alt_key:
            alt_model = "gemini-3.5-flash-lite" if alt_provider == "gemini" else "qwen/qwen3.8-27b"
            try:
                return self._registry.get_provider(
                    provider=alt_provider,
                    model_name=alt_model,
                    api_key=alt_key,
                )
            except Exception as e:
                logger.debug(f"Could not initialize fallback provider {alt_provider}: {e}")
        return None

    async def _backfill_unanalyzed_events(self) -> None:
        """Query DB for unanalyzed events and enqueue them for processing."""
        try:
            await asyncio.sleep(2)
            factory = self._get_session_factory()
            async with factory() as session:
                stmt = (
                    select(Event)
                    .where(Event.is_analyzed == False)
                    .order_by(Event.published_at.desc())
                    .limit(100)
                )
                events = (await session.execute(stmt)).scalars().all()
                if events:
                    logger.info(f"Backfilling {len(events)} unanalyzed events into pipeline queue...")
                    for ev in events:
                        norm = NormalizedEvent(
                            id=ev.id,
                            theme_id=str(ev.theme_id),
                            source_type=ev.source_type,
                            source_name=ev.source_name,
                            source_url=ev.source_url,
                            external_id=ev.external_id,
                            title=ev.title,
                            raw_text=ev.raw_text,
                            language=ev.language,
                            author=ev.author,
                            published_at=ev.published_at,
                            ingested_at=ev.ingested_at,
                        )
                        await self.enqueue_event(norm)
        except Exception as exc:
            logger.warning(f"Backfill unanalyzed events encountered error: {exc}")

    async def handle_config_reload(self, channel: str, message: Any) -> None:
        """Dynamic hot-reload subscriber callback."""
        logger.info(f"Pipeline received reload signal on '{channel}': {message}")
        await self._reload_configs_from_db()

    async def _reload_configs_from_db(self) -> None:
        """Atomically reload AI stage configs and active theme from DB."""
        try:
            factory = self._get_session_factory()
            async with factory() as session:
                # 1. Reload stage configs and API keys
                res = await session.execute(
                    select(SystemSettings).where(
                        SystemSettings.key.in_(["ai_pipeline_stages", "api_keys"])
                    )
                )
                settings_list = res.scalars().all()
                for setting in settings_list:
                    if setting.key == "ai_pipeline_stages" and setting.value_json:
                        for stage_id, cfg_data in setting.value_json.items():
                            norm_id = self._normalize_stage_id(stage_id)
                            self._stage_configs[norm_id] = AIStageConfig(
                                stage_id=norm_id,  # type: ignore
                                provider=cfg_data.get("provider", "gemini"),
                                model_name=cfg_data.get("model_name") or cfg_data.get("model", "gemini-3.5-flash-lite"),
                                temperature=cfg_data.get("temperature", 0.2),
                                api_key_override=cfg_data.get("api_key_override"),
                            )
                    elif setting.key == "api_keys" and setting.value_json:
                        self._db_api_keys = dict(setting.value_json)

                # 2. Reload active themes into cache
                theme_res = await session.execute(
                    select(Theme).where(Theme.is_active == True)
                )
                themes = theme_res.scalars().all()
                for t in themes:
                    t_dict = t.to_dict()
                    self._theme_cache[str(t.id)] = t_dict
                    self._theme_cache[t.name.lower()] = t_dict

            logger.info("Pipeline Orchestrator configurations reloaded successfully.")
        except Exception as exc:
            logger.error(f"Error reloading pipeline configurations from DB: {exc}")

    async def _resolve_theme_context(self, theme_id_or_name: Union[str, int]) -> Dict[str, Any]:
        """Resolve theme context (scoring prompt, keywords, entities, alert threshold)."""
        key = str(theme_id_or_name).lower().strip()
        if key in self._theme_cache:
            return self._theme_cache[key]

        # Try fetching from DB
        try:
            factory = self._get_session_factory()
            async with factory() as session:
                if str(theme_id_or_name).isdigit():
                    t_res = await session.execute(
                        select(Theme).where(Theme.id == int(theme_id_or_name))
                    )
                else:
                    t_res = await session.execute(
                        select(Theme).where(Theme.name.ilike(f"%{theme_id_or_name}%"))
                    )
                theme = t_res.scalars().first()
                if theme:
                    t_dict = theme.to_dict()
                    self._theme_cache[key] = t_dict
                    self._theme_cache[str(theme.id)] = t_dict
                    return t_dict
        except Exception as exc:
            logger.warning(f"Could not load theme '{theme_id_or_name}' from DB: {exc}")

        # Default fallback theme context (Pre-configured US-Iran theme)
        return {
            "id": 1,
            "name": "US-Iran conflict",
            "description": "Monitoring US-Iran military, geopolitical, and economic developments",
            "keywords": [
                "Strait of Hormuz", "IRGC", "CENTCOM", "uranium", "sanctions",
                "ballistic", "proxy", "Persian Gulf", "Bab el-Mandeb", "Shahed",
                "Houthis", "Hezbollah", "enrichment"
            ],
            "entities": ["United States", "Iran", "Israel", "IRGC", "US Navy", "CENTCOM", "Khamenei", "Pezeshkian"],
            "scoring_prompt": (
                "You are a senior geopolitical intelligence analyst. Score the following event on a 1-10 "
                "importance scale for the US-Iran conflict theme. Guidelines: Score 9-10 for direct kinetic military engagements, "
                "strikes on naval/nuclear assets, or declared nuclear breakout. Score 7-8 for proxy attacks, major sanctions, troop "
                "deployments, or heightened naval blockades. Score 4-6 for standard diplomatic rhetoric, non-escalatory drills, or "
                "minor statements. Score 1-3 for background political commentary or irrelevant noise. Provide an integer score and "
                "a 1-sentence rationale."
            ),
            "alert_threshold": 7,
            "importance_threshold": 7,
        }

    async def process_event(
        self,
        event: Union[NormalizedEvent, Dict[str, Any]],
        parallel_stages: bool = True,
        force_reanalyze: bool = False,
    ) -> AnalyzedEvent:
        """
        Executes the multi-stage AI reasoning pipeline for an event:
        1. Stage 1 (Sequential): Extraction & Translation.
        2. Stages 2, 3, 4 (Concurrent): Scoring, Summarization, Visual Trends.
        3. Persists enriched fields to database and publishes alerts.
        """
        theme_id = getattr(event, "theme_id", None) or (event.get("theme_id") if isinstance(event, dict) else "1")
        theme_ctx = await self._resolve_theme_context(theme_id)

        # Stage 1: Extraction & Translation (Must complete first)
        stage1_cfg = self.get_stage_config("extraction")
        provider_s1 = self._registry.get_provider(
            provider=stage1_cfg.provider,
            model_name=stage1_cfg.model_name,
            api_key=resolve_api_key(stage1_cfg.provider, stage1_cfg.api_key_override, self._db_api_keys),
            temperature=stage1_cfg.temperature,
        )
        try:
            res_s1: Stage1Result = await run_stage1_extract(
                event=event,
                provider=provider_s1,
                model=stage1_cfg.model_name,
                temperature=stage1_cfg.temperature,
            )
        except Exception as exc:
            logger.warning(f"Stage 1 primary provider '{stage1_cfg.provider}' failed ({exc}), attempting failover...")
            fallback_s1 = self._get_fallback_provider(stage1_cfg.provider, "extraction")
            if fallback_s1:
                try:
                    res_s1 = await run_stage1_extract(
                        event=event,
                        provider=fallback_s1,
                        model=fallback_s1.default_model,
                        temperature=stage1_cfg.temperature,
                    )
                except Exception as fb_exc:
                    logger.warning(f"Stage 1 fallback provider also failed ({fb_exc}). Using heuristics.")
                    res_s1 = _heuristic_stage1_fallback(
                        raw_text=getattr(event, "raw_text", "") or (event.get("raw_text", "") if isinstance(event, dict) else ""),
                        title=getattr(event, "title", "") or (event.get("title", "") if isinstance(event, dict) else ""),
                    )
            else:
                res_s1 = _heuristic_stage1_fallback(
                    raw_text=getattr(event, "raw_text", "") or (event.get("raw_text", "") if isinstance(event, dict) else ""),
                    title=getattr(event, "title", "") or (event.get("title", "") if isinstance(event, dict) else ""),
                )

        intelligence_text = res_s1.translated_text or (
            getattr(event, "raw_text", "") or (event.get("raw_text", "") if isinstance(event, dict) else "")
        )

        # Stage 2, 3, 4 Providers
        stage2_cfg = self.get_stage_config("scoring")
        stage3_cfg = self.get_stage_config("summarization")
        stage4_cfg = self.get_stage_config("visuals")

        provider_s2 = self._registry.get_provider(
            provider=stage2_cfg.provider,
            model_name=stage2_cfg.model_name,
            api_key=resolve_api_key(stage2_cfg.provider, stage2_cfg.api_key_override, self._db_api_keys),
            temperature=stage2_cfg.temperature,
        )
        provider_s3 = self._registry.get_provider(
            provider=stage3_cfg.provider,
            model_name=stage3_cfg.model_name,
            api_key=resolve_api_key(stage3_cfg.provider, stage3_cfg.api_key_override, self._db_api_keys),
            temperature=stage3_cfg.temperature,
        )
        provider_s4 = self._registry.get_provider(
            provider=stage4_cfg.provider,
            model_name=stage4_cfg.model_name,
            api_key=resolve_api_key(stage4_cfg.provider, stage4_cfg.api_key_override, self._db_api_keys),
            temperature=stage4_cfg.temperature,
        )

        task_s2 = run_stage2_score(
            event=event,
            intelligence_text=intelligence_text,
            extracted_entities=res_s1.extracted_entities,
            theme_context=theme_ctx,
            provider=provider_s2,
            model=stage2_cfg.model_name,
            temperature=stage2_cfg.temperature,
        )
        task_s3 = run_stage3_summary(
            event=event,
            intelligence_text=intelligence_text,
            provider=provider_s3,
            model=stage3_cfg.model_name,
            temperature=stage3_cfg.temperature,
        )
        task_s4 = run_stage4_visuals(
            event=event,
            intelligence_text=intelligence_text,
            extracted_entities=res_s1.extracted_entities,
            provider=provider_s4,
            model=stage4_cfg.model_name,
            temperature=stage4_cfg.temperature,
        )

        if parallel_stages:
            results = await asyncio.gather(task_s2, task_s3, task_s4, return_exceptions=True)
            res_s2: Stage2Result = results[0] if not isinstance(results[0], Exception) else Stage2Result(
                importance_score=5,
                score_rationale="Fallback assessment due to stage exception",
                priority_level="medium",
                should_alert=False,
            )
            res_s3: Stage3Result = results[1] if not isinstance(results[1], Exception) else Stage3Result(
                summary="Intelligence update logged and monitored.",
                headline=getattr(event, "title", "Event Update")[:75],
            )
            res_s4: Stage4Result = results[2] if not isinstance(results[2], Exception) else Stage4Result(
                trend_category="Geopolitical Development",
                cluster_tags=["Intelligence"],
                narrative_insight="Event logged.",
            )
        else:
            res_s2 = await task_s2
            res_s3 = await task_s3
            res_s4 = await task_s4

        threshold = int(theme_ctx.get("alert_threshold", theme_ctx.get("importance_threshold", 7)))
        should_alert = bool(res_s2.importance_score >= threshold)

        ev_id = str(getattr(event, "id", None) or (event.get("id") if isinstance(event, dict) else "evt"))
        src_name = getattr(event, "source_name", "OSINT") or (event.get("source_name", "OSINT") if isinstance(event, dict) else "OSINT")
        src_type = getattr(event, "source_type", "rss") or (event.get("source_type", "rss") if isinstance(event, dict) else "rss")
        src_url = getattr(event, "source_url", None) or (event.get("source_url") if isinstance(event, dict) else None)
        ext_id = str(getattr(event, "external_id", ev_id) or (event.get("external_id", ev_id) if isinstance(event, dict) else ev_id))
        title = getattr(event, "title", "Event") or (event.get("title", "Event") if isinstance(event, dict) else "Event")
        raw_text = getattr(event, "raw_text", "") or (event.get("raw_text", "") if isinstance(event, dict) else "")
        author = getattr(event, "author", None) or (event.get("author") if isinstance(event, dict) else None)
        location = getattr(event, "location", None) or (event.get("location") if isinstance(event, dict) else None)
        metadata = getattr(event, "metadata", {}) or (event.get("metadata", {}) if isinstance(event, dict) else {})
        pub_at = getattr(event, "published_at", None) or (event.get("published_at") if isinstance(event, dict) else datetime.now(timezone.utc))
        if isinstance(pub_at, str):
            try:
                pub_at = datetime.fromisoformat(pub_at)
            except Exception:
                pub_at = datetime.now(timezone.utc)
        ing_at = getattr(event, "ingested_at", None) or (event.get("ingested_at") if isinstance(event, dict) else datetime.now(timezone.utc))
        if isinstance(ing_at, str):
            try:
                ing_at = datetime.fromisoformat(ing_at)
            except Exception:
                ing_at = datetime.now(timezone.utc)

        # Determine English headline
        raw_hl = getattr(res_s3, "headline", "") or ""
        has_non_latin = bool(re.search(r"[\u0600-\u06FF\u0590-\u05FF\uFB50-\uFDFF\uFE70-\uFEFF]", raw_hl)) if raw_hl else True
        if has_non_latin or not raw_hl:
            if getattr(res_s1, "translated_title", None):
                final_headline = res_s1.translated_title
            elif res_s1.translated_text:
                clean_t = res_s1.translated_text.replace("English translation:", "").strip()
                final_headline = clean_t.split(".")[0].strip()[:75]
            else:
                final_headline = title[:75]
        else:
            final_headline = raw_hl

        # Construct AnalyzedEvent
        analyzed = AnalyzedEvent(
            id=ev_id,
            theme_id=theme_id,
            source_type=src_type,
            source_name=src_name,
            source_url=src_url,
            external_id=ext_id,
            title=title,
            raw_text=raw_text,
            language=res_s1.detected_language,
            author=author,
            location=location,
            metadata=metadata,
            published_at=pub_at,
            ingested_at=ing_at,
            # Enrichments
            translated_text=res_s1.translated_text,
            importance_score=res_s2.importance_score,
            score_rationale=res_s2.score_rationale,
            summary=res_s3.summary,
            headline=final_headline,
            entities=res_s1.extracted_entities,
            entities_json=res_s1.extracted_entities,
            trend_category=res_s4.trend_category,
            notification_dispatched=should_alert,
            notification_dispatched_at=datetime.now(timezone.utc) if should_alert else None,
            is_analyzed=True,
            processed_at=datetime.now(timezone.utc),
        )

        # Update DB persistence
        try:
            await self._persist_analyzed_event(analyzed, should_alert)
        except Exception as db_err:
            logger.warning(f"Database persistence error in pipeline: {db_err}")

        # Broadcast via Redis Bus
        try:
            await self._publish_to_bus(analyzed, should_alert)
        except Exception as bus_err:
            logger.warning(f"Redis bus broadcast error in pipeline: {bus_err}")

        return analyzed

    async def _persist_analyzed_event(self, analyzed: AnalyzedEvent, should_alert: bool) -> None:
        """Persist enriched fields to database and insert Alert row if score >= threshold."""
        factory = self._get_session_factory()
        async with factory() as session:
            # Find matching event by id or external_id
            ev_res = await session.execute(
                select(Event).where((Event.id == analyzed.id) | (Event.external_id == analyzed.external_id))
            )
            db_event = ev_res.scalars().first()
            if db_event:
                db_event.translated_text = analyzed.translated_text
                db_event.importance_score = analyzed.importance_score
                db_event.score_rationale = analyzed.score_rationale
                db_event.summary = analyzed.summary
                db_event.headline = analyzed.headline
                db_event.entities_json = analyzed.entities
                db_event.is_analyzed = True
                db_event.processed_at = analyzed.processed_at or datetime.now(timezone.utc)
                if analyzed.trend_category:
                    meta = dict(db_event.metadata_json or {})
                    meta["trend_category"] = analyzed.trend_category
                    db_event.metadata_json = meta

                if should_alert:
                    # Check if alert already exists for this event
                    alt_res = await session.execute(
                        select(Alert).where(Alert.event_id == db_event.id)
                    )
                    if not alt_res.scalars().first():
                        alert = Alert(
                            event_id=db_event.id,
                            importance_score=analyzed.importance_score,
                            channel="telegram",
                            status="sent",
                        )
                        session.add(alert)

                await session.commit()

    async def _publish_to_bus(self, analyzed: AnalyzedEvent, should_alert: bool) -> None:
        """Publish analyzed event and critical alert to Redis channels."""
        bus = self._get_redis_bus()
        payload = analyzed.model_dump(mode="json")
        await bus.publish(CHANNEL_EVENTS_NEW, payload)

        if should_alert:
            alert_payload = {
                "event_id": analyzed.id,
                "title": analyzed.headline or analyzed.title,
                "headline": analyzed.headline,
                "summary": analyzed.summary,
                "importance_score": analyzed.importance_score,
                "source_name": analyzed.source_name,
                "published_at": analyzed.published_at.isoformat() if analyzed.published_at else None,
                "source_url": analyzed.source_url,
            }
            await bus.publish(CHANNEL_EVENTS_ALERT, alert_payload)


_orchestrator_instance: Optional[PipelineOrchestrator] = None


def get_pipeline_orchestrator(use_mock: bool = False) -> PipelineOrchestrator:
    global _orchestrator_instance
    if _orchestrator_instance is None:
        _orchestrator_instance = PipelineOrchestrator(use_mock=use_mock)
    return _orchestrator_instance
