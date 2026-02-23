"""Tests for the escalation_monitor background task."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock


from taskbrew.intelligence.monitors import escalation_monitor


# ------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------


async def test_monitor_calls_check_stuck_tasks_periodically():
    """The monitor should call check_stuck_tasks() on each iteration."""
    manager = AsyncMock()
    call_count = 0
    call_count_target = 3

    stop = asyncio.Event()

    async def _counting_check(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count >= call_count_target:
            stop.set()
        return []

    manager.check_stuck_tasks.side_effect = _counting_check

    task = asyncio.create_task(
        escalation_monitor(manager, check_interval=0, stop_event=stop)
    )

    await asyncio.wait_for(task, timeout=5.0)

    assert call_count >= call_count_target


async def test_monitor_stops_on_stop_event():
    """The monitor should exit promptly when stop_event is set."""
    manager = AsyncMock()
    manager.check_stuck_tasks.return_value = []

    stop = asyncio.Event()
    task = asyncio.create_task(
        escalation_monitor(manager, check_interval=60, stop_event=stop)
    )

    # Let the first check run
    await asyncio.sleep(0.05)
    stop.set()

    # The task should finish quickly despite the 60s interval
    await asyncio.wait_for(task, timeout=2.0)
    assert task.done()


async def test_monitor_handles_check_exception_gracefully():
    """The monitor should keep running when check_stuck_tasks() raises."""
    manager = AsyncMock()
    call_count = 0

    async def _flaky_check(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("Database unavailable")
        return []

    manager.check_stuck_tasks.side_effect = _flaky_check

    stop = asyncio.Event()

    async def _stop_after_recovery():
        # Wait until we've had at least 2 calls (one failure + one success)
        while call_count < 2:
            await asyncio.sleep(0.01)
        stop.set()

    stopper = asyncio.create_task(_stop_after_recovery())
    task = asyncio.create_task(
        escalation_monitor(manager, check_interval=0, stop_event=stop)
    )

    await asyncio.wait_for(asyncio.gather(task, stopper), timeout=5.0)

    # The monitor survived the exception and continued
    assert call_count >= 2


async def test_monitor_escalates_stuck_tasks():
    """When stuck tasks are found, the monitor should escalate each one."""
    manager = AsyncMock()
    stuck_tasks = [
        {"id": "TSK-001", "title": "Stuck task 1", "claimed_by": "coder-1",
         "started_at": "2025-01-01T00:00:00", "last_heartbeat": None},
        {"id": "TSK-002", "title": "Stuck task 2", "claimed_by": "tester-1",
         "started_at": "2025-01-01T00:00:00", "last_heartbeat": None},
    ]
    manager.check_stuck_tasks.return_value = stuck_tasks
    manager.escalate.return_value = {"status": "open"}

    stop = asyncio.Event()

    async def _stop_after_first():
        # Wait until escalate has been called for both tasks
        while manager.escalate.call_count < 2:
            await asyncio.sleep(0.01)
        stop.set()

    stopper = asyncio.create_task(_stop_after_first())
    task = asyncio.create_task(
        escalation_monitor(manager, check_interval=0, stop_event=stop)
    )

    await asyncio.wait_for(asyncio.gather(task, stopper), timeout=5.0)

    assert manager.escalate.call_count >= 2
    # Verify the calls were made with correct task IDs
    call_task_ids = [
        call.kwargs.get("task_id") or call.args[0]
        for call in manager.escalate.call_args_list
    ]
    assert "TSK-001" in call_task_ids
    assert "TSK-002" in call_task_ids


async def test_monitor_handles_escalate_exception():
    """If escalate() fails for one task, the monitor should continue with others."""
    manager = AsyncMock()
    stuck_tasks = [
        {"id": "TSK-BAD", "title": "Bad task", "claimed_by": "coder-1",
         "started_at": "2025-01-01T00:00:00", "last_heartbeat": None},
        {"id": "TSK-GOOD", "title": "Good task", "claimed_by": "coder-2",
         "started_at": "2025-01-01T00:00:00", "last_heartbeat": None},
    ]

    call_count = 0

    async def _check_once(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return stuck_tasks
        return []

    manager.check_stuck_tasks.side_effect = _check_once

    escalate_calls = []

    async def _flaky_escalate(**kwargs):
        task_id = kwargs.get("task_id")
        escalate_calls.append(task_id)
        if task_id == "TSK-BAD":
            raise RuntimeError("Escalation DB error")
        return {"status": "open"}

    manager.escalate.side_effect = _flaky_escalate

    stop = asyncio.Event()

    async def _stop_later():
        while call_count < 2:
            await asyncio.sleep(0.01)
        stop.set()

    stopper = asyncio.create_task(_stop_later())
    task = asyncio.create_task(
        escalation_monitor(manager, check_interval=0, stop_event=stop)
    )

    await asyncio.wait_for(asyncio.gather(task, stopper), timeout=5.0)

    # Both tasks should have been attempted
    assert "TSK-BAD" in escalate_calls
    assert "TSK-GOOD" in escalate_calls


async def test_monitor_with_default_stop_event():
    """The monitor creates its own stop_event when none is provided."""
    manager = AsyncMock()
    manager.check_stuck_tasks.return_value = []

    task = asyncio.create_task(
        escalation_monitor(manager, check_interval=0)
    )

    # Let it run briefly
    await asyncio.sleep(0.05)

    # Cancel the task since we can't signal the internal stop_event
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert manager.check_stuck_tasks.call_count >= 1
