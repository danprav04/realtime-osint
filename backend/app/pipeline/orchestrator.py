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
from backend.app.db.models import Alert, Event, EventEmbedding, SystemSettings, Theme, utc_now
from backend.app.db.session import get_session_factory
from backend.app.pipeline.arabic_normalizer import classify_kinetic_signal
from backend.app.pipeline.models import (
    AIStageConfig,
    AIFallbackConfig,
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
from backend.app.pipeline.stage2_score import (
    _heuristic_stage2_fallback,
    classify_source_reliability,
    run_stage2_score,
)
from backend.app.pipeline.stage3_summary import _heuristic_stage3_fallback, run_stage3_summary
from backend.app.pipeline.stage4_visuals import _heuristic_stage4_fallback, run_stage4_visuals
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
        self._model_cooldowns: Dict[str, float] = {}
        from backend.app.pipeline.stage0_gatekeeper import Stage0Gatekeeper
        self._gatekeeper = Stage0Gatekeeper(batch_size=12)

        # Seed initial default stage configs
        self._init_default_configs()

    @property
    def is_running(self) -> bool:
        return self._running

    def _init_default_configs(self) -> None:
        self._stage_configs = {
            "gatekeeper": AIStageConfig(
                stage_id="gatekeeper",
                provider="gemini",
                model_name="gemma-4-26b-a4b-it",
                temperature=0.0,
                fallbacks=[
                    AIFallbackConfig(provider="gemini", model_name="gemma-4-31b-it", temperature=0.0),
                    AIFallbackConfig(provider="groq", model_name="openai/gpt-oss-20b", temperature=0.0),
                    AIFallbackConfig(provider="groq", model_name="qwen/qwen3.8-27b", temperature=0.0),
                ],
            ),
            "extraction": AIStageConfig(
                stage_id="extraction",
                provider="groq",
                model_name="qwen/qwen3.8-27b",
                temperature=0.2,
                fallbacks=[
                    AIFallbackConfig(provider="gemini", model_name="gemini-3.5-flash-lite", temperature=0.2),
                    AIFallbackConfig(provider="gemini", model_name="gemini-3.1-flash-lite", temperature=0.2),
                    AIFallbackConfig(provider="groq", model_name="openai/gpt-oss-120b", temperature=0.2),
                ],
            ),
            "scoring": AIStageConfig(
                stage_id="scoring",
                provider="gemini",
                model_name="gemini-3.5-flash-lite",
                temperature=0.1,
                fallbacks=[
                    AIFallbackConfig(provider="gemini", model_name="gemini-3.1-flash-lite", temperature=0.1),
                    AIFallbackConfig(provider="groq", model_name="openai/gpt-oss-120b", temperature=0.1),
                    AIFallbackConfig(provider="groq", model_name="qwen/qwen3.8-27b", temperature=0.1),
                ],
            ),
            "summarization": AIStageConfig(
                stage_id="summarization",
                provider="groq",
                model_name="openai/gpt-oss-120b",
                temperature=0.2,
                fallbacks=[
                    AIFallbackConfig(provider="groq", model_name="qwen/qwen3.8-27b", temperature=0.2),
                    AIFallbackConfig(provider="gemini", model_name="gemini-3.5-flash-lite", temperature=0.2),
                    AIFallbackConfig(provider="gemini", model_name="gemini-3.1-flash-lite", temperature=0.2),
                    AIFallbackConfig(provider="groq", model_name="openai/gpt-oss-20b", temperature=0.2),
                ],
            ),
            "visuals": AIStageConfig(
                stage_id="visuals",
                provider="groq",
                model_name="openai/gpt-oss-20b",
                temperature=0.2,
                fallbacks=[
                    AIFallbackConfig(provider="groq", model_name="qwen/qwen3.8-27b", temperature=0.2),
                    AIFallbackConfig(provider="gemini", model_name="gemini-3.5-flash-lite", temperature=0.2),
                    AIFallbackConfig(provider="gemini", model_name="gemini-3.1-flash-lite", temperature=0.2),
                ],
            ),
        }

    def is_model_in_cooldown(self, provider: str, model_name: str) -> bool:
        """Check if a provider's model is currently in a 429 rate-limit lockout cooldown."""
        key = f"{provider.lower().strip()}:{model_name.strip()}"
        exp = self._model_cooldowns.get(key)
        if exp is None:
            return False
        try:
            now = asyncio.get_event_loop().time()
        except RuntimeError:
            import time
            now = time.time()
        if now < exp:
            return True
        self._model_cooldowns.pop(key, None)
        return False

    def mark_model_cooldown(self, provider: str, model_name: str, duration_sec: float = 60.0) -> None:
        """Place a model into temporary cooldown lockout after hitting quota/rate limit."""
        key = f"{provider.lower().strip()}:{model_name.strip()}"
        try:
            now = asyncio.get_event_loop().time()
        except RuntimeError:
            import time
            now = time.time()
        self._model_cooldowns[key] = now + duration_sec
        logger.warning(f"[Pipeline Cooldown] Model '{key}' entered rate-limit cooldown for {duration_sec:.1f}s")

    def clear_cooldowns(self) -> None:
        """Clear all active model cooldowns."""
        self._model_cooldowns.clear()

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
        if s in ("gatekeeper", "stage0", "pre_filter", "triage", "pre_pipeline"):
            return "gatekeeper"
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

    def get_gatekeeper_stats(self) -> Dict[str, Any]:
        """Return live Stage 0 Pre-Pipeline Gatekeeper telemetry."""
        return self._gatekeeper.get_stats()

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
                        canonical_keys = {"gatekeeper", "extraction", "scoring", "summarization", "visuals"}
                        sorted_items = sorted(
                            setting.value_json.items(),
                            key=lambda item: 1 if str(item[0]).lower().strip() in canonical_keys else 0,
                        )
                        for stage_id, cfg_data in sorted_items:
                            norm_id = self._normalize_stage_id(stage_id)
                            raw_fallbacks = cfg_data.get("fallbacks")
                            if raw_fallbacks is None and norm_id in self._stage_configs:
                                parsed_fallbacks = list(self._stage_configs[norm_id].fallbacks)
                            else:
                                parsed_fallbacks = []
                                for fb in (raw_fallbacks or []):
                                    if isinstance(fb, dict):
                                        parsed_fallbacks.append(AIFallbackConfig(
                                            provider=fb.get("provider", "gemini"),
                                            model_name=fb.get("model_name") or fb.get("model", "gemini-3.1-flash-lite"),
                                            temperature=fb.get("temperature"),
                                            api_key_override=fb.get("api_key_override"),
                                        ))
                                    elif isinstance(fb, AIFallbackConfig):
                                        parsed_fallbacks.append(fb)
                            self._stage_configs[norm_id] = AIStageConfig(
                                stage_id=norm_id,  # type: ignore
                                provider=cfg_data.get("provider", "gemini"),
                                model_name=cfg_data.get("model_name") or cfg_data.get("model", "gemini-3.5-flash-lite"),
                                temperature=cfg_data.get("temperature", 0.2),
                                api_key_override=cfg_data.get("api_key_override"),
                                enabled=bool(cfg_data.get("enabled", True)),
                                fallbacks=parsed_fallbacks,
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

    async def _execute_stage_with_fallbacks(
        self,
        stage_id: str,
        runner_fn: Any,
        fallback_heuristic_fn: Any,
    ) -> Any:
        """
        Executes a pipeline stage across its primary model and configured fallback chain.
        Automatically catches 429 quota exhaustion, timeouts, and errors, and fails over sequentially.
        Minute-only rate limits (RPM/TPM) wait for the minute window reset on the same model
        rather than locking out the primary model or prematurely switching to fallback models.
        """
        stage_cfg = self.get_stage_config(stage_id)
        candidates = [
            {
                "provider": stage_cfg.provider,
                "model_name": stage_cfg.model_name,
                "temperature": stage_cfg.temperature,
                "api_key_override": stage_cfg.api_key_override,
            }
        ]
        for fb in stage_cfg.fallbacks:
            candidates.append({
                "provider": fb.provider,
                "model_name": fb.model_name,
                "temperature": fb.temperature if fb.temperature is not None else stage_cfg.temperature,
                "api_key_override": fb.api_key_override or stage_cfg.api_key_override,
            })

        for idx, cand in enumerate(candidates):
            p_name = cand["provider"]
            m_name = cand["model_name"]
            t_val = cand["temperature"]
            k_override = cand["api_key_override"]

            # If in active cooldown and we have a next candidate, skip directly to save latency
            if self.is_model_in_cooldown(p_name, m_name) and idx < len(candidates) - 1:
                next_cand = candidates[idx + 1]
                logger.warning(
                    f"[Pipeline Fallback] Stage '{stage_id}': Model '{p_name}:{m_name}' is in active rate-limit cooldown. "
                    f"Skipping directly to #{idx + 1} ('{next_cand['provider']}:{next_cand['model_name']}')."
                )
                continue

            api_key = resolve_api_key(p_name, k_override, self._db_api_keys)
            try:
                provider_inst = self._registry.get_provider(
                    provider=p_name,
                    model_name=m_name,
                    api_key=api_key,
                    temperature=t_val,
                )
            except Exception as inst_err:
                logger.warning(f"Could not instantiate provider for '{p_name}:{m_name}': {inst_err}")
                continue

            try:
                result = await runner_fn(
                    provider=provider_inst,
                    model=m_name,
                    temperature=t_val,
                    raise_on_error=True,
                )
                if idx > 0:
                    logger.info(
                        f"[Pipeline Fallback] Stage '{stage_id}': Succeeded using fallback #{idx} "
                        f"('{p_name}:{m_name}')."
                    )
                return result
            except PermissionError:
                raise
            except Exception as exc:
                err_str = str(exc).lower()
                is_rate_limit = (
                    "429" in str(exc)
                    or "quota" in err_str
                    or "resourceexhausted" in err_str
                    or "ratelimit" in err_str
                    or "too many requests" in err_str
                )
                is_minute_only = (
                    is_rate_limit
                    and any(
                        k in err_str
                        for k in (
                            "minute rate limit",
                            "perminute",
                            "per_minute",
                            "(rpm)",
                            "(tpm)",
                            "requests per minute",
                            "tokens per minute",
                        )
                    )
                    and not any(
                        k in err_str
                        for k in (
                            "daily",
                            "perday",
                            "per_day",
                            "(rpd)",
                            "(tpd)",
                            "requests per day",
                            "tokens per day",
                        )
                    )
                )

                # If the model only hit a per-minute rate limit (RPM/TPM), wait for the minute
                # window reset and retry the same model before falling back or locking it out.
                if is_minute_only and not self.use_mock:
                    try:
                        now_loop = asyncio.get_event_loop().time()
                        rl_until = getattr(provider_inst, "_rate_limit_until", 0.0)
                        recovery_wait = max(4.5, min(30.0, rl_until - now_loop)) if rl_until > now_loop else 5.0
                        logger.info(
                            f"[Pipeline Minute Rate Limit] Stage '{stage_id}': Model '{p_name}:{m_name}' "
                            f"reached minute rate limit ({exc}). Waiting {recovery_wait:.1f}s to retry '{p_name}:{m_name}'..."
                        )
                        await asyncio.sleep(recovery_wait)
                        result = await runner_fn(
                            provider=provider_inst,
                            model=m_name,
                            temperature=t_val,
                            raise_on_error=True,
                        )
                        logger.info(
                            f"[Pipeline Minute Rate Limit] Stage '{stage_id}': Model '{p_name}:{m_name}' "
                            f"succeeded after minute rate-limit window reset."
                        )
                        return result
                    except Exception as retry_exc:
                        exc = retry_exc
                        err_str = str(exc).lower()

                if is_rate_limit and not is_minute_only:
                    self.mark_model_cooldown(p_name, m_name, duration_sec=60.0)

                if idx < len(candidates) - 1:
                    next_cand = candidates[idx + 1]
                    logger.warning(
                        f"[Pipeline Fallback] Stage '{stage_id}': Model '{p_name}:{m_name}' reached limit or failed "
                        f"({exc}). Failing over to #{idx + 1} ('{next_cand['provider']}:{next_cand['model_name']}')..."
                    )
                else:
                    logger.warning(
                        f"[Pipeline Fallback] Stage '{stage_id}': All {len(candidates)} candidate models exhausted. "
                        f"Last error: {exc}. Invoking deterministic offline heuristics."
                    )

        return fallback_heuristic_fn()

    async def filter_events_batch(
        self,
        events: List[NormalizedEvent],
        force_llm: bool = False,
    ) -> List[NormalizedEvent]:
        """
        Execute Stage 0 Pre-Pipeline Gatekeeper across a batch of newly polled events
        BEFORE database insertion and BEFORE Stages 1-4:
          1. Layer 1 Deterministic Triage (siren/sensor fast-pass, deleted/spam/echo fast-drop).
          2. Layer 2 Micro-Batched High-RPD LLM Triage (up to 12 items/call via gemma-4-26b-a4b-it -> gemma-4-31b-it).
        Returns the filtered list of relevant NormalizedEvents in original order.
        """
        if not events:
            return []

        from backend.app.pipeline.stage0_gatekeeper import _extract_event_text_and_meta, _heuristic_keep_decision

        first_ev = events[0]
        theme_id = getattr(first_ev, "theme_id", None) or (first_ev.get("theme_id") if isinstance(first_ev, dict) else "1")
        theme_ctx = await self._resolve_theme_context(theme_id)

        kept_map: Dict[int, bool] = {}
        llm_candidates: List[tuple[int, NormalizedEvent]] = []

        for idx, ev in enumerate(events):
            self._gatekeeper.evaluated_count += 1
            decision = self._gatekeeper.triage_layer1(ev, theme_ctx)
            if decision == "fast_pass":
                kept_map[idx] = True
                self._gatekeeper.passed_count += 1
                self._gatekeeper.fast_passed_count += 1
            elif decision == "fast_drop":
                kept_map[idx] = False
                self._gatekeeper.dropped_deterministic_count += 1
            else:
                llm_candidates.append((idx, ev))

        self._gatekeeper.last_evaluated_at = datetime.now(timezone.utc).isoformat()

        if llm_candidates:
            gk_cfg = self.get_stage_config("gatekeeper")
            has_any_key = bool(
                self.use_mock
                or force_llm
                or getattr(self._registry, "use_mock", False)
                or (
                    self._running
                    and (
                        resolve_api_key("gemini", gk_cfg.api_key_override, self._db_api_keys)
                        or resolve_api_key("groq", gk_cfg.api_key_override, self._db_api_keys)
                    )
                )
            )
            b_size = self._gatekeeper.batch_size
            for start in range(0, len(llm_candidates), b_size):
                sub_slice = llm_candidates[start : start + b_size]
                chunk_events = [item[1] for item in sub_slice]

                def chunk_heuristic_fallback(chunk_ref=chunk_events) -> List[int]:
                    kept_local: List[int] = []
                    for local_i, c_ev in enumerate(chunk_ref):
                        c_text, s_type, _, m = _extract_event_text_and_meta(c_ev)
                        eff_type = "reddit_comment" if m.get("is_comment") else s_type
                        if _heuristic_keep_decision(c_text, eff_type, theme_ctx):
                            kept_local.append(local_i)
                    return kept_local

                if gk_cfg.enabled and has_any_key:
                    kept_local_indices: List[int] = await self._execute_stage_with_fallbacks(
                        stage_id="gatekeeper",
                        runner_fn=lambda chunk_ref=chunk_events, **kw: self._gatekeeper.evaluate_llm_chunk(
                            chunk=chunk_ref,
                            theme_context=theme_ctx,
                            **kw,
                        ),
                        fallback_heuristic_fn=chunk_heuristic_fallback,
                    )
                else:
                    kept_local_indices = chunk_heuristic_fallback()

                kept_local_set = set(kept_local_indices)
                for local_i, (orig_idx, c_ev) in enumerate(sub_slice):
                    if local_i in kept_local_set:
                        kept_map[orig_idx] = True
                        self._gatekeeper.passed_count += 1
                        c_text, _, _, _ = _extract_event_text_and_meta(c_ev)
                        if c_text:
                            self._gatekeeper._recent_texts.append(c_text)
                    else:
                        kept_map[orig_idx] = False
                        self._gatekeeper.dropped_llm_count += 1

        return [ev for idx, ev in enumerate(events) if kept_map.get(idx, False)]

    async def _build_scoring_context(
        self,
        event: Union[NormalizedEvent, Dict[str, Any]],
        res_s1: Stage1Result,
        theme_ctx: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Build the pre-Stage 2 Situational & Semantic Embedding Context:
        1. Classifies source reliability tier and frontline kinetic vs. acoustic signals.
        2. Queries recent events and embeddings in the active theme to compute vector similarity,
           cross-source corroboration, same-source echo detection, and 6h/24h theater velocity.
        """
        ev_id = str(getattr(event, "id", None) or (event.get("id") if isinstance(event, dict) else "evt"))
        src_name = getattr(event, "source_name", "OSINT") or (
            event.get("source_name", "OSINT") if isinstance(event, dict) else "OSINT"
        )
        src_type = getattr(event, "source_type", "rss") or (
            event.get("source_type", "rss") if isinstance(event, dict) else "rss"
        )
        title_val = getattr(event, "title", "") or (event.get("title", "") if isinstance(event, dict) else "")
        raw_text_val = getattr(event, "raw_text", "") or (event.get("raw_text", "") if isinstance(event, dict) else "")
        location_val = getattr(event, "location", None) or (
            event.get("location") if isinstance(event, dict) else None
        )
        pub_at = getattr(event, "published_at", None) or (
            event.get("published_at") if isinstance(event, dict) else datetime.now(timezone.utc)
        )

        tier_id, tier_label = classify_source_reliability(src_name, src_type)
        kinetic_sig = classify_kinetic_signal(f"{title_val}\n{raw_text_val}")
        threshold = int(theme_ctx.get("alert_threshold", theme_ctx.get("importance_threshold", 7)))

        ctx: Dict[str, Any] = {
            "source_reliability_tier": tier_id,
            "source_reliability_label": tier_label,
            "kinetic_signal": kinetic_sig,
            "related_reports": [],
            "corroboration_count": 0,
            "corroborating_sources": [],
            "corroborating_event_ids": [],
            "same_source_echo_count": 0,
            "is_same_source_echo": False,
            "max_similarity": 0.0,
            "corroboration_status": "First Report / Unverified",
            "velocity_status": "Normal",
            "events_last_6h": 0,
            "critical_alerts_last_6h": 0,
            "avg_score_24h": 5.0,
            "active_storylines": [],
            "embedding_vector": None,
            "embedding_model_name": None,
        }

        # Skip DB lookup only in pure memory mock runs where no session_factory was provided
        if self._session_factory is None and self.use_mock:
            return ctx

        try:
            from backend.app.services.embedding_service import get_embedding_service
            factory = self._get_session_factory()
            raw_tid = theme_ctx.get("id", 1)
            numeric_theme_id = int(raw_tid) if str(raw_tid).isdigit() else 1
            eng_title = res_s1.translated_title or title_val
            eng_text = res_s1.translated_text or raw_text_val

            async with factory() as session:
                emb_service = get_embedding_service()
                neighborhood = await emb_service.compute_live_scoring_neighborhood(
                    session=session,
                    candidate_event_id=ev_id,
                    theme_id=numeric_theme_id,
                    title=eng_title,
                    intelligence_text=eng_text,
                    extracted_entities=res_s1.extracted_entities,
                    source_name=src_name,
                    source_type=src_type,
                    published_at=pub_at,
                    location_json=location_val,
                    alert_threshold=threshold,
                    use_mock=self.use_mock,
                )
                ctx.update(neighborhood)
        except Exception as exc:
            logger.debug(f"Pre-Stage 2 neighborhood lookup skipped: {exc}")

        return ctx

    async def process_event(
        self,
        event: Union[NormalizedEvent, Dict[str, Any]],
        parallel_stages: bool = True,
        force_reanalyze: bool = False,
    ) -> AnalyzedEvent:
        """
        Executes the multi-stage AI reasoning pipeline for an event:
        1. Stage 1 (Sequential): Extraction & Translation (with multi-model fallback).
        2. Pre-Score Context & Embedding Lookup: Computes vector similarity, corroboration, echo status, and theater tempo.
        3. Stages 2, 3, 4 (Concurrent): Context-Aware Scoring, Summarization, Visual Trends.
        4. Persists enriched fields, embeddings, and retroactive corroboration upgrades to database and publishes alerts.
        """
        theme_id = getattr(event, "theme_id", None) or (event.get("theme_id") if isinstance(event, dict) else "1")
        theme_ctx = await self._resolve_theme_context(theme_id)

        raw_text_val = getattr(event, "raw_text", "") or (event.get("raw_text", "") if isinstance(event, dict) else "")
        title_val = getattr(event, "title", "") or (event.get("title", "") if isinstance(event, dict) else "")

        # Stage 1: Extraction & Translation (Must complete first)
        res_s1: Stage1Result = await self._execute_stage_with_fallbacks(
            stage_id="extraction",
            runner_fn=lambda **kw: run_stage1_extract(event=event, **kw),
            fallback_heuristic_fn=lambda: _heuristic_stage1_fallback(raw_text=raw_text_val, title=title_val),
        )

        intelligence_text = res_s1.translated_text or raw_text_val

        # Pre-Stage 2: Inline Semantic Embedding & Situational Context Lookup
        scoring_ctx = await self._build_scoring_context(event=event, res_s1=res_s1, theme_ctx=theme_ctx)

        # Stage 2, 3, 4 with fallback chains
        def stage2_fallback():
            return Stage2Result(
                importance_score=5,
                score_rationale="Fallback assessment due to stage exception",
                priority_level="medium",
                should_alert=False,
            )

        task_s2 = self._execute_stage_with_fallbacks(
            stage_id="scoring",
            runner_fn=lambda **kw: run_stage2_score(
                event=event,
                intelligence_text=intelligence_text,
                extracted_entities=res_s1.extracted_entities,
                theme_context=theme_ctx,
                stage1_result=res_s1,
                scoring_context=scoring_ctx,
                **kw,
            ),
            fallback_heuristic_fn=stage2_fallback,
        )

        task_s3 = self._execute_stage_with_fallbacks(
            stage_id="summarization",
            runner_fn=lambda **kw: run_stage3_summary(
                event=event,
                intelligence_text=intelligence_text,
                **kw,
            ),
            fallback_heuristic_fn=lambda: Stage3Result(
                summary="Intelligence update logged and monitored.",
                headline=title_val[:75] or "Event Update",
            ),
        )

        task_s4 = self._execute_stage_with_fallbacks(
            stage_id="visuals",
            runner_fn=lambda **kw: run_stage4_visuals(
                event=event,
                intelligence_text=intelligence_text,
                extracted_entities=res_s1.extracted_entities,
                **kw,
            ),
            fallback_heuristic_fn=lambda: Stage4Result(
                trend_category="Geopolitical Development",
                cluster_tags=["Intelligence"],
                narrative_insight="Event logged.",
            ),
        )

        if parallel_stages:
            results = await asyncio.gather(task_s2, task_s3, task_s4, return_exceptions=True)
            res_s2: Stage2Result = results[0] if not isinstance(results[0], Exception) else stage2_fallback()
            res_s3: Stage3Result = results[1] if not isinstance(results[1], Exception) else Stage3Result(
                summary="Intelligence update logged and monitored.",
                headline=title_val[:75] or "Event Update",
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
        should_alert = bool(res_s2.importance_score >= threshold and not scoring_ctx.get("is_same_source_echo"))

        ev_id = str(getattr(event, "id", None) or (event.get("id") if isinstance(event, dict) else "evt"))
        src_name = getattr(event, "source_name", "OSINT") or (event.get("source_name", "OSINT") if isinstance(event, dict) else "OSINT")
        src_type = getattr(event, "source_type", "rss") or (event.get("source_type", "rss") if isinstance(event, dict) else "rss")
        src_url = getattr(event, "source_url", None) or (event.get("source_url") if isinstance(event, dict) else None)
        ext_id = str(getattr(event, "external_id", ev_id) or (event.get("external_id", ev_id) if isinstance(event, dict) else ev_id))
        title = getattr(event, "title", "Event") or (event.get("title", "Event") if isinstance(event, dict) else "Event")
        raw_text = getattr(event, "raw_text", "") or (event.get("raw_text", "") if isinstance(event, dict) else "")
        author = getattr(event, "author", None) or (event.get("author") if isinstance(event, dict) else None)
        location = getattr(event, "location", None) or (event.get("location") if isinstance(event, dict) else None)
        raw_meta = getattr(event, "metadata", {}) or (event.get("metadata", {}) if isinstance(event, dict) else {})
        metadata = dict(raw_meta) if isinstance(raw_meta, dict) else {}
        metadata["scoring_breakdown"] = res_s2.to_breakdown_dict(scoring_ctx)
        if res_s4.trend_category:
            metadata["trend_category"] = res_s4.trend_category

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

        # Update DB persistence (including inline EventEmbedding and retroactive corroboration upgrades)
        try:
            await self._persist_analyzed_event(
                analyzed=analyzed,
                should_alert=should_alert,
                scoring_ctx=scoring_ctx,
                alert_threshold=threshold,
            )
        except Exception as db_err:
            logger.warning(f"Database persistence error in pipeline: {db_err}")

        # Broadcast via Redis Bus
        try:
            await self._publish_to_bus(analyzed, should_alert)
        except Exception as bus_err:
            logger.warning(f"Redis bus broadcast error in pipeline: {bus_err}")

        return analyzed

    async def _persist_analyzed_event(
        self,
        analyzed: AnalyzedEvent,
        should_alert: bool,
        scoring_ctx: Optional[Dict[str, Any]] = None,
        alert_threshold: int = 7,
    ) -> None:
        """
        Persist enriched fields, scoring_breakdown metadata, inline EventEmbedding,
        and retroactive corroboration upgrades for earlier matched events.
        """
        ctx = scoring_ctx or {}
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

                meta = dict(db_event.metadata_json or {})
                if analyzed.metadata:
                    meta.update(analyzed.metadata)
                if analyzed.trend_category:
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

                # Persist inline EventEmbedding if already computed during pre-Stage 2 context lookup
                emb_vec = ctx.get("embedding_vector")
                if emb_vec:
                    emb_res = await session.execute(
                        select(EventEmbedding).where(EventEmbedding.event_id == db_event.id)
                    )
                    existing_emb = emb_res.scalars().first()
                    corrob_cnt = int(ctx.get("corroboration_count", 0))
                    trust_est = (
                        85 if corrob_cnt >= 2 else (72 if corrob_cnt == 1 else 48)
                    )
                    if existing_emb:
                        existing_emb.embedding_vector = emb_vec
                        existing_emb.corroboration_count = corrob_cnt
                        existing_emb.trustworthiness_score = trust_est
                    else:
                        session.add(
                            EventEmbedding(
                                event_id=db_event.id,
                                embedding_vector=emb_vec,
                                model_name=ctx.get("embedding_model_name") or "gemini-embedding-001",
                                corroboration_count=corrob_cnt,
                                trustworthiness_score=trust_est,
                                embedded_at=utc_now(),
                            )
                        )

                # Retroactively upgrade earlier unverified reports when corroborated by an independent source
                corrob_ids = ctx.get("corroborating_event_ids") or []
                if corrob_ids:
                    await self._retroactive_corroboration_upgrade(
                        session=session,
                        analyzed=analyzed,
                        corroborating_event_ids=corrob_ids,
                        alert_threshold=alert_threshold,
                    )

                await session.commit()

    async def _retroactive_corroboration_upgrade(
        self,
        session: AsyncSession,
        analyzed: AnalyzedEvent,
        corroborating_event_ids: List[str],
        alert_threshold: int = 7,
    ) -> None:
        """
        When a new report from an independent source corroborates earlier reports in embedding space:
        1. Increments corroboration_count and trustworthiness_score on the earlier EventEmbedding rows.
        2. If an earlier report was scored while uncorroborated (e.g., First Report / Unverified or
           capped just below the alert threshold), upgrades its importance_score (+1), updates its
           scoring_breakdown & score_rationale, and dispatches an Alert if it newly crosses threshold.
        """
        stmt_peers = select(Event).where(Event.id.in_(corroborating_event_ids))
        peer_events = list((await session.execute(stmt_peers)).scalars().all())
        if not peer_events:
            return

        stmt_peer_embs = select(EventEmbedding).where(EventEmbedding.event_id.in_(corroborating_event_ids))
        peer_embs = {
            e.event_id: e for e in (await session.execute(stmt_peer_embs)).scalars().all()
        }

        for peer in peer_events:
            if (peer.source_name or "").strip().lower() == (analyzed.source_name or "").strip().lower():
                continue

            peer_meta = dict(peer.metadata_json or {})
            peer_bd = dict(peer_meta.get("scoring_breakdown") or {})
            prev_sources = list(peer_bd.get("corroborating_sources") or [])
            if analyzed.source_name and analyzed.source_name not in prev_sources:
                prev_sources.append(analyzed.source_name)
            new_corrob_count = max(len(prev_sources), int(peer_bd.get("corroboration_count", 0)) + 1)

            # Update peer EventEmbedding metrics
            p_emb = peer_embs.get(peer.id)
            if p_emb:
                p_emb.corroboration_count = max(int(p_emb.corroboration_count or 0), new_corrob_count)
                p_emb.trustworthiness_score = max(
                    int(p_emb.trustworthiness_score or 50),
                    85 if new_corrob_count >= 2 else 72,
                )

            was_unverified = (
                peer_bd.get("corroboration_count", 0) == 0
                or peer_bd.get("verification_confidence") == "low"
                or peer_bd.get("corroboration_status") in (None, "First Report / Unverified", "Single-Source Unverified")
            )
            already_upgraded = bool(peer_bd.get("retroactively_corroborated", False))

            peer_bd["corroboration_count"] = new_corrob_count
            peer_bd["corroborating_sources"] = prev_sources
            peer_bd["corroboration_status"] = (
                "Highly Corroborated" if new_corrob_count >= 2 else "Developing / Verified"
            )
            peer_bd["verification_confidence"] = "high"
            peer_bd["confidence_score"] = max(int(peer_bd.get("confidence_score") or 7), 8 if new_corrob_count == 1 else 9)

            prev_score = int(peer.importance_score or 5)
            if was_unverified and not already_upgraded and prev_score >= 4:
                new_score = min(10, prev_score + 1)
                peer.importance_score = new_score
                peer_bd["final_score"] = new_score
                peer_bd["retroactively_corroborated"] = True
                notes = list(peer_bd.get("calibration_notes") or [])
                upgrade_note = (
                    f"Retroactively corroborated ({prev_score} -> {new_score}) by independent source {analyzed.source_name}"
                )
                if upgrade_note not in notes:
                    notes.append(upgrade_note)
                peer_bd["calibration_notes"] = notes

                cur_rat = (peer.score_rationale or "").strip()
                if "retroactively corroborated" not in cur_rat.lower():
                    peer.score_rationale = f"{cur_rat} [{upgrade_note}]".strip()

                # If the upgraded score now meets/exceeds alert_threshold, create Alert if none exists
                if new_score >= alert_threshold:
                    alt_check = await session.execute(select(Alert).where(Alert.event_id == peer.id))
                    if not alt_check.scalars().first():
                        session.add(
                            Alert(
                                event_id=peer.id,
                                importance_score=new_score,
                                channel="telegram",
                                status="sent",
                            )
                        )

            peer_meta["scoring_breakdown"] = peer_bd
            peer.metadata_json = peer_meta

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

        # Non-blocking background embedding generation
        try:
            asyncio.create_task(self._trigger_embedding(analyzed))
        except Exception as e:
            logger.debug(f"Could not spawn background embedding task: {e}")

    async def _trigger_embedding(self, analyzed: AnalyzedEvent) -> None:
        """Asynchronously embed newly analyzed event in the background."""
        try:
            from backend.app.services.embedding_service import get_embedding_service
            from backend.app.services.redis_bus import CHANNEL_MAP_UPDATE
            factory = self._get_session_factory()
            async with factory() as session:
                res = await session.execute(select(Event).where(Event.id == analyzed.id))
                ev = res.scalars().first()
                if ev:
                    service = get_embedding_service()
                    await service.embed_events([ev], session, use_mock=self.use_mock)
                    bus = self._get_redis_bus()
                    await bus.publish(CHANNEL_MAP_UPDATE, {"event_id": ev.id, "theme_id": str(ev.theme_id)})
        except Exception as exc:
            logger.debug(f"Background embedding skipped or deferred: {exc}")


_orchestrator_instance: Optional[PipelineOrchestrator] = None


def get_pipeline_orchestrator(use_mock: bool = False) -> PipelineOrchestrator:
    global _orchestrator_instance
    if _orchestrator_instance is None:
        _orchestrator_instance = PipelineOrchestrator(use_mock=use_mock)
    return _orchestrator_instance
