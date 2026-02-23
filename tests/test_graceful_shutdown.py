"""Tests for graceful shutdown of the orchestrator (BE-007)."""
from __future__ import annotations

import asyncio

from unittest.mock import AsyncMock, MagicMock

from taskbrew.main import Orchestrator
from taskbrew.orchestrator.database import Database


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _make_orchestrator(db=None, agent_tasks=None, agent_loops=None, worktree_manager=None):
    """Build a minimal Orchestrator with mocked components."""
    db = db or MagicMock(spec=Database)
    db.close = AsyncMock()

    task_board = MagicMock()
    event_bus = MagicMock()
    artifact_store = MagicMock()
    instance_manager = MagicMock()
    roles = {}
    team_config = MagicMock()
    project_dir = "/tmp/test"
    wt = worktree_manager or MagicMock()
    wt.cleanup_all = AsyncMock()

    orch = Orchestrator(
        db=db,
        task_board=task_board,
        event_bus=event_bus,
        artifact_store=artifact_store,
        instance_manager=instance_manager,
        roles=roles,
        team_config=team_config,
        project_dir=project_dir,
        worktree_manager=wt,
    )

    if agent_loops:
        orch._agent_loops = agent_loops
    if agent_tasks:
        orch.agent_tasks = agent_tasks

    return orch


# ------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------


class TestGracefulShutdown:
    """Tests for Orchestrator.shutdown() graceful shutdown logic."""

    async def test_shutdown_sets_shutting_down_flag(self):
        """Shutdown should set _shutting_down to True."""
        orch = _make_orchestrator()
        assert orch.shutting_down is False

        await orch.shutdown()

        assert orch.shutting_down is True

    async def test_shutdown_calls_stop_on_agent_loops(self):
        """Every registered AgentLoop should have stop() called."""
        loop1 = MagicMock()
        loop2 = MagicMock()
        orch = _make_orchestrator(agent_loops=[loop1, loop2])

        await orch.shutdown()

        loop1.stop.assert_called_once()
        loop2.stop.assert_called_once()

    async def test_shutdown_waits_for_tasks_to_complete(self):
        """Agent tasks that finish before the timeout should not be cancelled."""
        completed = asyncio.Event()

        async def quick_task():
            completed.set()

        task = asyncio.create_task(quick_task())
        orch = _make_orchestrator(agent_tasks=[task])

        await orch.shutdown(timeout=5.0)

        assert completed.is_set()
        assert task.done()

    async def test_shutdown_force_cancels_after_timeout(self):
        """Tasks that exceed the timeout should be force-cancelled."""
        started = asyncio.Event()

        async def slow_task():
            started.set()
            await asyncio.sleep(300)  # Will be cancelled

        task = asyncio.create_task(slow_task())
        # Let it start
        await started.wait()

        orch = _make_orchestrator(agent_tasks=[task])

        await orch.shutdown(timeout=0.1)

        assert task.done()
        assert task.cancelled()

    async def test_shutdown_closes_database_in_finally(self):
        """db.close() must be called even if an earlier phase raises."""
        db = MagicMock(spec=Database)
        db.close = AsyncMock()

        wt = MagicMock()
        wt.cleanup_all = AsyncMock(side_effect=RuntimeError("boom"))

        orch = _make_orchestrator(db=db, worktree_manager=wt)

        # Should not raise despite worktree error
        await orch.shutdown()

        db.close.assert_awaited_once()

    async def test_shutdown_closes_database_even_when_tasks_fail(self):
        """db.close() must still be called if agent task gathering raises."""
        db = MagicMock(spec=Database)
        db.close = AsyncMock()

        async def failing_task():
            raise RuntimeError("agent exploded")

        task = asyncio.create_task(failing_task())
        # Give it a moment to fail
        await asyncio.sleep(0)

        orch = _make_orchestrator(db=db, agent_tasks=[task])
        await orch.shutdown()

        db.close.assert_awaited_once()

    async def test_shutdown_cleans_worktrees(self):
        """Worktree cleanup should be called during shutdown."""
        wt = MagicMock()
        wt.cleanup_all = AsyncMock()
        orch = _make_orchestrator(worktree_manager=wt)

        await orch.shutdown()

        wt.cleanup_all.assert_awaited_once()

    async def test_shutdown_skips_worktree_cleanup_when_none(self):
        """If worktree_manager is None, shutdown should not fail."""
        orch = _make_orchestrator(worktree_manager=None)
        # Manually set to None since helper always provides a mock
        orch.worktree_manager = None

        # Should not raise
        await orch.shutdown()

    async def test_shutdown_is_idempotent(self):
        """Calling shutdown twice should be a no-op the second time."""
        db = MagicMock(spec=Database)
        db.close = AsyncMock()
        orch = _make_orchestrator(db=db)

        await orch.shutdown()
        await orch.shutdown()

        # db.close should only be called once (second call returns early)
        db.close.assert_awaited_once()

    async def test_shutdown_signals_escalation_monitor(self):
        """If escalation monitor is running, it should be signalled to stop."""
        orch = _make_orchestrator()

        stop_event = asyncio.Event()
        orch._escalation_stop = stop_event

        async def mock_escalation():
            await stop_event.wait()

        orch._escalation_task = asyncio.create_task(mock_escalation())
        orch.agent_tasks.append(orch._escalation_task)

        await orch.shutdown(timeout=5.0)

        assert stop_event.is_set()
        assert orch._escalation_task.done()

    async def test_shutdown_with_no_agent_tasks(self):
        """Shutdown with empty agent_tasks should complete cleanly."""
        db = MagicMock(spec=Database)
        db.close = AsyncMock()
        orch = _make_orchestrator(db=db, agent_tasks=[])

        await orch.shutdown()

        assert orch.shutting_down is True
        db.close.assert_awaited_once()

    async def test_shutdown_logs_phases(self, caplog):
        """Shutdown should log progress at each phase."""
        import logging
        with caplog.at_level(logging.INFO, logger="taskbrew.main"):
            orch = _make_orchestrator()
            await orch.shutdown()

        log_text = caplog.text
        assert "Graceful shutdown initiated" in log_text
        assert "Phase 1" in log_text
        assert "Closing database connection" in log_text

    async def test_shutting_down_property(self):
        """The shutting_down property should reflect _shutting_down state."""
        orch = _make_orchestrator()
        assert orch.shutting_down is False

        orch._shutting_down = True
        assert orch.shutting_down is True

    async def test_shutdown_handles_db_close_error(self):
        """If db.close() raises, it should be logged but not re-raised."""
        db = MagicMock(spec=Database)
        db.close = AsyncMock(side_effect=RuntimeError("db close failed"))
        orch = _make_orchestrator(db=db)

        # Should not raise
        await orch.shutdown()

        assert orch.shutting_down is True
        db.close.assert_awaited_once()

    async def test_shutdown_timeout_parameter(self):
        """Custom timeout should be respected."""
        started = asyncio.Event()

        async def slow_task():
            started.set()
            await asyncio.sleep(300)

        task = asyncio.create_task(slow_task())
        await started.wait()

        orch = _make_orchestrator(agent_tasks=[task])

        # Very short timeout — task should be force-cancelled
        await orch.shutdown(timeout=0.05)

        assert task.done()
        assert task.cancelled()
