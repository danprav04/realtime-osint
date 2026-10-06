import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Union
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.db.models import Event, Source, Theme
from backend.app.db.session import get_session_factory
from backend.app.plugins.ais_plugin import AISSourcePlugin
from backend.app.plugins.base import BaseSourcePlugin, NormalizedEvent
from backend.app.plugins.newsapi_plugin import NewsAPISourcePlugin
from backend.app.plugins.reddit_plugin import RedditSourcePlugin
from backend.app.plugins.rss_plugin import RSSSourcePlugin
from backend.app.plugins.telegram_plugin import TelegramSourcePlugin
from backend.app.services.redis_bus import (
    CHANNEL_CONFIG_RELOAD,
    CHANNEL_EVENTS_NEW,
    RedisBus,
    get_redis_bus,
)

logger = logging.getLogger("osint.supervisor")


def create_plugin_instance(
    source_type: str,
    source_id: Union[int, str],
    theme_id: Union[int, str],
    name: str,
    config: Optional[Dict[str, Any]] = None,
    poll_interval: int = 300,
    is_enabled: bool = True,
) -> BaseSourcePlugin:
    """Factory creating appropriate plugin subclass from source type string."""
    st = str(source_type).lower().strip()
    cfg = config or {}

    if st == "rss":
        return RSSSourcePlugin(source_id, theme_id, name, cfg, poll_interval, is_enabled)
    elif st == "telegram":
        return TelegramSourcePlugin(source_id, theme_id, name, cfg, poll_interval, is_enabled)
    elif st in ("newsapi", "news_api"):
        return NewsAPISourcePlugin(source_id, theme_id, name, cfg, poll_interval, is_enabled)
    elif st == "reddit":
        return RedditSourcePlugin(source_id, theme_id, name, cfg, poll_interval, is_enabled)
    elif st in ("ais", "maritime_ais"):
        return AISSourcePlugin(source_id, theme_id, name, cfg, poll_interval, is_enabled)
    else:
        logger.warning(f"Unknown source type '{source_type}', falling back to RSS plugin.")
        return RSSSourcePlugin(source_id, theme_id, name, cfg, poll_interval, is_enabled)


class IngestionSupervisor:
    """
    Asynchronous Ingestion Worker Supervisor.
    Runs inside the primary FastAPI process on the native asyncio event loop
    (preserving the <900MB RAM budget by avoiding heavy Celery/RabbitMQ multiprocessing).

    Key Capabilities:
    1. Orchestrates independent asyncio polling tasks for all active sources.
    2. Isolated Error Boundaries: exceptions in one plugin are logged and quarantined
       without impacting peer plugins or the web service.
    3. Zero-Code Hot Reloading: listens on Redis channel 'osint:config:reload' to
       dynamically add, update, pause, or remove plugins when configuration changes
       with zero service downtime.
    4. Persists raw intelligence events to PostgreSQL/SQLite and publishes
       to Redis bus channel 'osint:events:new'.
    """

    def __init__(
        self,
        session_factory: Optional[async_sessionmaker[AsyncSession]] = None,
        redis_bus: Optional[RedisBus] = None,
    ):
        self._session_factory = session_factory
        self._redis_bus = redis_bus
        self.plugins: Dict[str, BaseSourcePlugin] = {}
        self._tasks: Dict[str, asyncio.Task] = {}
        self._running: bool = False
        self._lock = asyncio.Lock()
        self._reload_subscribed: bool = False

    @property
    def is_running(self) -> bool:
        return self._running

    def _get_session_factory(self) -> async_sessionmaker[AsyncSession]:
        if self._session_factory is None:
            self._session_factory = get_session_factory()
        return self._session_factory

    def _get_redis_bus(self) -> RedisBus:
        if self._redis_bus is None:
            self._redis_bus = get_redis_bus()
        return self._redis_bus

    async def start(self) -> None:
        """Start the ingestion supervisor and reconcile active sources."""
        async with self._lock:
            if self._running:
                return
            self._running = True
            logger.info("Starting Ingestion Supervisor...")

            # Subscribe to Redis pub/sub channel for zero-code dynamic reload
            bus = self._get_redis_bus()
            if not self._reload_subscribed:
                bus.subscribe(CHANNEL_CONFIG_RELOAD, self.handle_config_reload)
                self._reload_subscribed = True

            # Initial source reconciliation
            await self._reconcile_sources_internal()
            logger.info(f"Ingestion Supervisor started with {len(self.plugins)} active plugins.")

    async def stop(self) -> None:
        """Cleanly cancel all active plugin tasks and stop the supervisor."""
        async with self._lock:
            if not self._running:
                return
            self._running = False
            logger.info("Stopping Ingestion Supervisor...")

            # Unsubscribe from reload bus
            bus = self._get_redis_bus()
            if self._reload_subscribed:
                bus.unsubscribe(CHANNEL_CONFIG_RELOAD, self.handle_config_reload)
                self._reload_subscribed = False

            # Cancel all worker tasks
            for source_id, task in list(self._tasks.items()):
                if not task.done():
                    task.cancel()

            # Wait for tasks to cancel
            if self._tasks:
                await asyncio.gather(*self._tasks.values(), return_exceptions=True)
                self._tasks.clear()

            # Stop all plugins
            for plugin in self.plugins.values():
                await plugin.stop()

            logger.info("Ingestion Supervisor stopped successfully.")

    async def handle_config_reload(self, channel: str, message: Any) -> None:
        """Redis pub/sub subscriber callback triggered on dynamic configuration updates."""
        logger.info(f"Received config reload message on '{channel}': {message}. Reconciling sources...")
        await self.reconcile_sources()

    async def reconcile_sources(self) -> None:
        """Public method to trigger atomic source reconciliation."""
        async with self._lock:
            if not self._running:
                return
            await self._reconcile_sources_internal()

    async def _reconcile_sources_internal(self) -> None:
        """Query database for active themes and sources, reconciling with running plugins."""
        try:
            factory = self._get_session_factory()
            async with factory() as session:
                # Find active themes
                themes_stmt = select(Theme).where(Theme.is_active == True)
                active_themes = (await session.execute(themes_stmt)).scalars().all()
                active_theme_ids = {t.id for t in active_themes}

                if not active_theme_ids:
                    logger.info("No active themes found in database. Pausing all plugins.")
                    for sid in list(self.plugins.keys()):
                        await self._remove_plugin_internal(sid)
                    return

                # Find sources for active themes
                sources_stmt = select(Source).where(Source.theme_id.in_(active_theme_ids))
                sources = (await session.execute(sources_stmt)).scalars().all()
                db_source_map = {str(s.id): s for s in sources}
        except Exception as exc:
            logger.error(f"Failed to query database for sources during reconciliation: {exc}", exc_info=True)
            return

        # 1. Add or update plugins from database
        for sid_str, source_obj in db_source_map.items():
            if sid_str not in self.plugins:
                # New source: instantiate and start task if enabled
                plugin = create_plugin_instance(
                    source_type=source_obj.source_type,
                    source_id=source_obj.id,
                    theme_id=source_obj.theme_id,
                    name=source_obj.name,
                    config=source_obj.config_json,
                    poll_interval=source_obj.polling_interval_sec,
                    is_enabled=source_obj.is_enabled,
                )
                self.plugins[sid_str] = plugin
                if plugin.is_enabled and self._running:
                    self._start_plugin_task(sid_str, plugin)
            else:
                # Existing plugin: check if interval, config, or enabled state changed
                plugin = self.plugins[sid_str]
                plugin.name = source_obj.name
                plugin.config = source_obj.config_json or {}
                plugin.poll_interval = max(1, int(source_obj.polling_interval_sec))

                if source_obj.is_enabled and not plugin.is_enabled:
                    plugin.is_enabled = True
                    if sid_str not in self._tasks or self._tasks[sid_str].done():
                        self._start_plugin_task(sid_str, plugin)
                elif not source_obj.is_enabled and plugin.is_enabled:
                    plugin.is_enabled = False
                    self._cancel_plugin_task(sid_str)

        # 2. Remove plugins no longer present in DB
        for existing_sid in list(self.plugins.keys()):
            if existing_sid not in db_source_map:
                await self._remove_plugin_internal(existing_sid)

        logger.info(
            f"Reconciliation complete: {len(self.plugins)} managed plugins, "
            f"{len([t for t in self._tasks.values() if not t.done()])} running tasks."
        )

    def _start_plugin_task(self, source_id: str, plugin: BaseSourcePlugin) -> None:
        """Spawn worker task for plugin on current asyncio event loop."""
        self._cancel_plugin_task(source_id)
        task = asyncio.create_task(
            self._run_plugin_worker(plugin),
            name=f"osint_worker_{plugin.name}_{source_id}",
        )
        self._tasks[source_id] = task

    def _cancel_plugin_task(self, source_id: str) -> None:
        """Cancel existing task if running."""
        if source_id in self._tasks:
            task = self._tasks[source_id]
            if not task.done():
                task.cancel()
            del self._tasks[source_id]

    async def _remove_plugin_internal(self, source_id: str) -> None:
        """Stop and remove a plugin instance."""
        self._cancel_plugin_task(source_id)
        if source_id in self.plugins:
            plugin = self.plugins.pop(source_id)
            await plugin.stop()

    async def add_plugin(self, plugin: BaseSourcePlugin) -> None:
        """Programmatically register and run a new plugin instance."""
        async with self._lock:
            sid = str(plugin.source_id)
            if sid in self.plugins:
                await self._remove_plugin_internal(sid)
            self.plugins[sid] = plugin
            if self._running and plugin.is_enabled:
                self._start_plugin_task(sid, plugin)

    async def remove_plugin(self, source_id: Union[int, str]) -> None:
        """Programmatically remove a plugin instance."""
        async with self._lock:
            await self._remove_plugin_internal(str(source_id))

    async def pause_plugin(self, source_id: Union[int, str]) -> None:
        """Temporarily pause polling for a source."""
        async with self._lock:
            sid = str(source_id)
            if sid in self.plugins:
                self.plugins[sid].is_enabled = False
                self._cancel_plugin_task(sid)

    async def resume_plugin(self, source_id: Union[int, str]) -> None:
        """Resume polling for a paused source."""
        async with self._lock:
            sid = str(source_id)
            if sid in self.plugins:
                plugin = self.plugins[sid]
                plugin.is_enabled = True
                if self._running:
                    self._start_plugin_task(sid, plugin)

    def get_plugin(self, source_id: Union[int, str]) -> Optional[BaseSourcePlugin]:
        """Retrieve plugin instance by source ID."""
        return self.plugins.get(str(source_id))

    def list_plugins(self) -> List[BaseSourcePlugin]:
        """List all managed plugin instances."""
        return list(self.plugins.values())

    async def poll_source_once(self, source_id: Union[int, str]) -> List[NormalizedEvent]:
        """Manually trigger a single poll execution for a given source."""
        plugin = self.get_plugin(source_id)
        if not plugin:
            raise KeyError(f"Source ID '{source_id}' not found.")
        events = await plugin.poll()
        for event in events:
            await self.dispatch_and_persist(event)
        return events

    async def _run_plugin_worker(self, plugin: BaseSourcePlugin) -> None:
        """
        Isolated worker loop for an individual source plugin.
        Any exception is quarantined and does not affect peer plugins.
        """
        await plugin.start()
        try:
            while self._running and plugin.is_enabled:
                try:
                    events = await plugin.poll()
                    for event in events:
                        await self.dispatch_and_persist(event)
                    plugin.last_polled_at = datetime.now(timezone.utc)
                    plugin.status = "active"
                    plugin.last_error = None
                except asyncio.CancelledError:
                    break
                except Exception as exc:
                    # Isolated error handling: quarantined failure
                    logger.error(
                        f"Isolated error in plugin '{plugin.name}' (id={plugin.source_id}): {exc}",
                        exc_info=True,
                    )
                    plugin.last_error = str(exc)
                    plugin.status = "error"

                # Sleep until next poll cycle or until cancelled
                try:
                    await asyncio.sleep(plugin.poll_interval)
                except asyncio.CancelledError:
                    break
        finally:
            await plugin.stop()

    async def dispatch_and_persist(self, event: NormalizedEvent) -> None:
        """
        1. Persist raw event to database (with deduplication).
        2. Publish event to Redis bus channel 'osint:events:new'.
        """
        # 1. Database persistence
        try:
            factory = self._get_session_factory()
            async with factory() as session:
                # Check for existing event by (theme_id, external_id)
                # Resolve numeric theme_id if theme_id is a string
                numeric_theme_id: int
                if event.theme_id.isdigit():
                    numeric_theme_id = int(event.theme_id)
                else:
                    # Look up theme by name
                    theme_stmt = select(Theme).where(
                        (Theme.name.ilike(f"%{event.theme_id}%")) | (Theme.is_active == True)
                    ).limit(1)
                    found_theme = (await session.execute(theme_stmt)).scalars().first()
                    numeric_theme_id = found_theme.id if found_theme else 1

                # Check unique constraint (theme_id, external_id)
                dup_check = select(Event).where(
                    (Event.theme_id == numeric_theme_id) & (Event.external_id == event.external_id)
                )
                existing = (await session.execute(dup_check)).scalars().first()

                if not existing:
                    # Resolve numeric source_id if available
                    source_id_val = None
                    plugin = self.get_plugin(event.source_name) or self.get_plugin(event.theme_id)
                    # Try finding source in DB if possible
                    src_stmt = select(Source).where(
                        (Source.theme_id == numeric_theme_id) & (Source.name == event.source_name)
                    ).limit(1)
                    src_obj = (await session.execute(src_stmt)).scalars().first()
                    if src_obj:
                        source_id_val = src_obj.id

                    db_event = Event(
                        id=event.id,
                        theme_id=numeric_theme_id,
                        source_id=source_id_val,
                        source_type=event.source_type,
                        source_name=event.source_name,
                        external_id=event.external_id,
                        title=event.title,
                        raw_text=event.raw_text,
                        language=event.language or "en",
                        author=event.author,
                        location_json=event.location.to_dict() if event.location else None,
                        metadata_json=event.metadata,
                        published_at=event.published_at,
                        ingested_at=event.ingested_at,
                    )
                    session.add(db_event)
                    await session.commit()
        except Exception as exc:
            logger.warning(f"Database persistence skipped/failed for event '{event.id}': {exc}")

        # 2. Publish to Redis bus
        try:
            bus = self._get_redis_bus()
            await bus.publish(CHANNEL_EVENTS_NEW, event.model_dump())
        except Exception as exc:
            logger.warning(f"Event bus publish failed for event '{event.id}': {exc}")

    def get_status(self) -> Dict[str, Any]:
        """Return supervisor health and status overview."""
        active_count = len([t for t in self._tasks.values() if not t.done()])
        return {
            "is_running": self._running,
            "total_plugins": len(self.plugins),
            "active_tasks": active_count,
            "plugins": {
                sid: {
                    "name": p.name,
                    "type": p.__class__.__name__,
                    "status": p.status,
                    "is_enabled": p.is_enabled,
                    "poll_interval": p.poll_interval,
                    "last_polled_at": p.last_polled_at.isoformat() if p.last_polled_at else None,
                    "events_count": p.events_count,
                    "last_error": p.last_error,
                }
                for sid, p in self.plugins.items()
            },
        }


# Global supervisor singleton
_supervisor_instance: Optional[IngestionSupervisor] = None


def get_ingestion_supervisor() -> IngestionSupervisor:
    """Return the global IngestionSupervisor singleton."""
    global _supervisor_instance
    if _supervisor_instance is None:
        _supervisor_instance = IngestionSupervisor()
    return _supervisor_instance
