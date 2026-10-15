"""
backend/app/services/summary_scheduler.py - 30-Minute Automated AI Summary Scheduler
=====================================================================================
Asynchronous background scheduler that automatically synthesizes a fresh AI Situational
Summary (SitRep) every 30 minutes if new intelligence reports have arrived since the
last briefing.
If a user manually generates a briefing from behind the VPN, the 30-minute timer
resets automatically.
"""

import asyncio
from datetime import datetime, timedelta, timezone
import logging
from typing import Optional
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.db.models import Event, SystemSettings, Theme, utc_now
from backend.app.db.session import get_session_factory
from backend.app.services.socket_server import broadcast_summary_update
from backend.app.services.summary_service import (
    SUMMARY_CACHE_SETTINGS_KEY,
    SituationSummaryService,
    get_summary_service,
)

logger = logging.getLogger("osint.summary_scheduler")


class SummaryScheduler:
    """
    Background worker periodically checking whether a fresh AI Situational Summary
    needs to be synthesized based on elapsed time and new incoming intelligence.
    """

    def __init__(
        self,
        session_factory: Optional[async_sessionmaker[AsyncSession]] = None,
        summary_service: Optional[SituationSummaryService] = None,
        interval_seconds: int = 1800,  # 30 minutes
        check_interval_seconds: int = 30,
    ):
        self._session_factory = session_factory
        self._summary_service = summary_service
        self.interval_seconds = interval_seconds
        self.check_interval_seconds = check_interval_seconds
        self._task: Optional[asyncio.Task] = None
        self._running: bool = False

    @property
    def is_running(self) -> bool:
        return self._running

    def _get_session_factory(self) -> async_sessionmaker[AsyncSession]:
        if self._session_factory is None:
            self._session_factory = get_session_factory()
        return self._session_factory

    def _get_summary_service(self) -> SituationSummaryService:
        if self._summary_service is None:
            self._summary_service = get_summary_service()
        return self._summary_service

    async def start(self) -> None:
        """Start the background scheduler task."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop(), name="osint-summary-scheduler")
        logger.info(
            f"Automated AI Summary Scheduler started (interval: {self.interval_seconds}s / {self.interval_seconds // 60}m)."
        )

    async def stop(self) -> None:
        """Cancel and await termination of the background scheduler task."""
        if not self._running:
            return
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        logger.info("Automated AI Summary Scheduler stopped.")

    async def _init_last_generation_from_db(self, session: AsyncSession) -> None:
        """Seed scheduler state from the persisted cache in SystemSettings if uninitialized."""
        service = self._get_summary_service()
        last_gen_time, _, _ = service.get_last_generation_metadata()
        if last_gen_time is not None:
            return

        try:
            stmt = select(SystemSettings).where(
                SystemSettings.key == SUMMARY_CACHE_SETTINGS_KEY
            )
            record = (await session.execute(stmt)).scalars().first()
            if record and isinstance(record.value_json, dict) and record.value_json:
                latest_entry = max(
                    record.value_json.values(),
                    key=lambda e: e.get("generated_at", ""),
                    default=None,
                )
                if latest_entry and isinstance(latest_entry, dict):
                    gen_at_str = latest_entry.get("generated_at")
                    if gen_at_str:
                        gen_at = datetime.fromisoformat(gen_at_str)
                        if gen_at.tzinfo is None:
                            gen_at = gen_at.replace(tzinfo=timezone.utc)
                        service.record_generation(
                            gen_at,
                            latest_entry.get("cached_total_events", 0),
                            latest_entry.get("latest_event_ingested_at"),
                        )
                        logger.info(
                            f"SummaryScheduler initialized from DB cache: last generated at {gen_at.isoformat()}"
                        )
                        return
        except Exception as exc:
            logger.debug(f"Failed to load cached summary timestamp: {exc}")

        # If no previous summary in DB, set timestamp to now so initial generation triggers after warmup
        service.record_generation(utc_now() - timedelta(seconds=self.interval_seconds - 60), 0, None)

    async def _run_loop(self) -> None:
        """Periodic evaluation loop."""
        # Warmup delay
        await asyncio.sleep(5)
        service = self._get_summary_service()

        while self._running:
            try:
                factory = self._get_session_factory()
                async with factory() as session:
                    await self._init_last_generation_from_db(session)
                    last_gen_time, last_count, last_ingested = service.get_last_generation_metadata()

                    if last_gen_time:
                        if last_gen_time.tzinfo is None:
                            last_gen_time = last_gen_time.replace(tzinfo=timezone.utc)
                        elapsed = (utc_now() - last_gen_time).total_seconds()
                    else:
                        elapsed = self.interval_seconds

                    if elapsed >= self.interval_seconds:
                        # 1. Check if there are new events in DB
                        current_count = (
                            await session.execute(select(func.count(Event.id)))
                        ).scalar_one_or_none() or 0

                        max_ingested = (
                            await session.execute(select(func.max(Event.ingested_at)))
                        ).scalar_one_or_none()
                        max_ingested_str = max_ingested.isoformat() if max_ingested else None

                        has_new_events = (
                            current_count > last_count
                            or (max_ingested_str and max_ingested_str != last_ingested)
                        )

                        if has_new_events and current_count > 0:
                            logger.info(
                                f"SummaryScheduler: {elapsed:.0f}s elapsed (>= {self.interval_seconds}s) "
                                f"and new events detected ({current_count} vs {last_count}). "
                                f"Synthesizing scheduled AI situational summary..."
                            )
                            summary = await service.generate_or_get_summary(
                                session=session,
                                theme_id=None,
                                time_range="all",
                                force_refresh=True,
                            )
                            await broadcast_summary_update(summary)
                            logger.info(
                                f"SummaryScheduler: Successfully synthesized scheduled summary "
                                f"(Theme: {summary.get('theme_name')}, Threat: {summary.get('threat_trajectory')})."
                            )
                        else:
                            logger.debug(
                                f"SummaryScheduler: {elapsed:.0f}s elapsed, but zero new events detected "
                                f"({current_count} events). Skipping synthesis to conserve LLM quota."
                            )
                            # Advance the clock so we don't query repeatedly every check_interval
                            service.record_generation(utc_now(), current_count, max_ingested_str)

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"Error in SummaryScheduler loop: {exc}", exc_info=True)

            try:
                await asyncio.sleep(self.check_interval_seconds)
            except asyncio.CancelledError:
                break


_summary_scheduler: Optional[SummaryScheduler] = None


def get_summary_scheduler() -> SummaryScheduler:
    """Return singleton SummaryScheduler instance."""
    global _summary_scheduler
    if _summary_scheduler is None:
        _summary_scheduler = SummaryScheduler()
    return _summary_scheduler
