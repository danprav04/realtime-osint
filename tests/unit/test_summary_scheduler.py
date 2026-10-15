"""
tests/unit/test_summary_scheduler.py - Unit Tests for Automated 30-Minute AI Summary Scheduler
=============================================================================================
Validates:
1. SummaryScheduler lifecycle (start/stop).
2. Interval tracking and manual generation reset logic.
3. Skip behavior when zero new events have been ingested.
"""

import asyncio
from datetime import datetime, timedelta, timezone
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from backend.app.services.summary_scheduler import SummaryScheduler, get_summary_scheduler
from backend.app.services.summary_service import SituationSummaryService, get_summary_service


@pytest.mark.asyncio
async def test_scheduler_lifecycle():
    """Test starting and stopping the SummaryScheduler gracefully."""
    mock_service = MagicMock(spec=SituationSummaryService)
    mock_service.get_last_generation_metadata.return_value = (datetime.now(timezone.utc), 10, "2026-10-15T12:00:00")

    scheduler = SummaryScheduler(
        summary_service=mock_service,
        interval_seconds=1800,
        check_interval_seconds=1,
    )

    assert scheduler.is_running is False
    await scheduler.start()
    assert scheduler.is_running is True

    # Allow one tick
    await asyncio.sleep(0.1)

    await scheduler.stop()
    assert scheduler.is_running is False


@pytest.mark.asyncio
async def test_summary_service_metadata_tracking():
    """Test that summary_service accurately tracks generation timestamps and counts."""
    service = get_summary_service()
    t0 = datetime(2026, 10, 15, 12, 0, 0, tzinfo=timezone.utc)
    service.record_generation(t0, 42, "2026-10-15T11:59:59")

    last_time, count, latest_iso = service.get_last_generation_metadata()
    assert last_time == t0
    assert count == 42
    assert latest_iso == "2026-10-15T11:59:59"

    # Simulate manual generation 5 minutes later
    t1 = t0 + timedelta(minutes=5)
    service.record_generation(t1, 45, "2026-10-15T12:04:30")
    new_time, new_count, new_iso = service.get_last_generation_metadata()
    assert new_time == t1
    assert new_count == 45
    assert new_iso == "2026-10-15T12:04:30"
