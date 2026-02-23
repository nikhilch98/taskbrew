"""Lightweight performance and stress tests.

Verifies that core operations complete within acceptable time bounds
and that the system handles moderate load without degradation.
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

import pytest

from taskbrew.orchestrator.database import Database
from taskbrew.orchestrator.event_bus import EventBus
from taskbrew.orchestrator.task_board import TaskBoard
from taskbrew.agents.instance_manager import InstanceManager


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture
async def db(tmp_path: Path):
    """Create and initialise a file-backed test database."""
    database = Database(str(tmp_path / "perf.db"))
    await database.initialize()
    yield database
    await database.close()


@pytest.fixture
async def task_board(db: Database) -> TaskBoard:
    """Create a TaskBoard backed by the test database."""
    board = TaskBoard(db, group_prefixes={"pm": "FEAT"})
    await board.register_prefixes({
        "pm": "PM",
        "architect": "AR",
        "coder": "CD",
        "verifier": "VR",
        "tester": "TS",
    })
    return board


@pytest.fixture
async def instance_mgr(db: Database) -> InstanceManager:
    """Create an InstanceManager backed by the test database."""
    return InstanceManager(db)


@pytest.fixture
async def event_bus() -> EventBus:
    """Create a fresh EventBus."""
    return EventBus()


# ------------------------------------------------------------------
# Database bulk operations
# ------------------------------------------------------------------


class TestDatabasePerformance:
    """Tests verifying database operation throughput."""

    async def test_bulk_inserts_within_time_limit(self, db: Database):
        """1000 bulk inserts complete within 5 seconds."""
        start = time.monotonic()

        for i in range(1000):
            await db.execute(
                "INSERT INTO notifications (type, title, message, severity, created_at) "
                "VALUES (?, ?, ?, ?, datetime('now'))",
                (f"type_{i}", f"Title {i}", f"Message {i}", "info"),
            )

        elapsed = time.monotonic() - start
        assert elapsed < 5.0, f"1000 inserts took {elapsed:.2f}s (limit: 5s)"

    async def test_bulk_reads_after_inserts(self, db: Database):
        """Reading 1000 rows after bulk insert completes within 2 seconds."""
        for i in range(1000):
            await db.execute(
                "INSERT INTO notifications (type, title, message, severity, created_at) "
                "VALUES (?, ?, ?, ?, datetime('now'))",
                (f"type_{i}", f"Title {i}", f"Message {i}", "info"),
            )

        start = time.monotonic()
        rows = await db.execute_fetchall(
            "SELECT * FROM notifications ORDER BY id"
        )
        elapsed = time.monotonic() - start

        assert len(rows) == 1000
        assert elapsed < 2.0, f"Reading 1000 rows took {elapsed:.2f}s (limit: 2s)"

    async def test_database_connection_cleanup(self, tmp_path: Path):
        """Opening and closing many database connections does not leak resources."""
        for i in range(20):
            db = Database(str(tmp_path / f"cleanup_{i}.db"))
            await db.initialize()
            await db.execute(
                "INSERT INTO notifications (type, title, severity, created_at) "
                "VALUES ('test', 'cleanup', 'info', datetime('now'))",
            )
            rows = await db.execute_fetchall("SELECT * FROM notifications")
            assert len(rows) == 1
            await db.close()

        # If we get here without error, connections are properly cleaned up

    async def test_migration_applies_cleanly_on_fresh_database(self, tmp_path: Path):
        """Migration applies cleanly on a fresh database within 3 seconds."""
        start = time.monotonic()

        db = Database(str(tmp_path / "fresh_migration.db"))
        await db.initialize()

        from taskbrew.orchestrator.migration import MigrationManager

        mm = MigrationManager(db)
        current_version = await mm.get_current_version()

        elapsed = time.monotonic() - start
        await db.close()

        # All migrations should have been applied during initialize()
        assert current_version > 0
        assert elapsed < 3.0, f"Fresh DB + migrations took {elapsed:.2f}s (limit: 3s)"


# ------------------------------------------------------------------
# Task board operations at scale
# ------------------------------------------------------------------


class TestTaskBoardPerformance:
    """Tests verifying task board handles moderate load."""

    async def test_board_handles_100_plus_tasks(
        self, task_board: TaskBoard
    ):
        """Task board handles 100+ tasks without degradation."""
        group = await task_board.create_group(title="Big Feature", created_by="pm")

        start = time.monotonic()
        for i in range(120):
            await task_board.create_task(
                group_id=group["id"],
                title=f"Task #{i:03d}",
                task_type="implementation",
                assigned_to="coder",
                priority="medium",
            )
        create_elapsed = time.monotonic() - start

        # Creating 120 tasks should complete within 10 seconds
        assert create_elapsed < 10.0, (
            f"Creating 120 tasks took {create_elapsed:.2f}s (limit: 10s)"
        )

        # Board query should still be fast
        start = time.monotonic()
        board = await task_board.get_board(assigned_to="coder")
        query_elapsed = time.monotonic() - start

        total_tasks = sum(len(v) for v in board.values())
        assert total_tasks == 120
        assert query_elapsed < 2.0, (
            f"Querying board with 120 tasks took {query_elapsed:.2f}s (limit: 2s)"
        )

    async def test_concurrent_task_claims(
        self, task_board: TaskBoard, instance_mgr: InstanceManager
    ):
        """10 sequential rapid claim attempts resolve without errors or duplicates.

        SQLite uses a single connection, so true concurrent transactions are
        not possible.  This test verifies that rapid sequential claims still
        produce unique assignments (no double-claims).
        """
        from taskbrew.config_loader import RoleConfig

        role_cfg = RoleConfig(
            role="coder",
            display_name="Coder",
            prefix="CD",
            color="#00ff00",
            emoji="",
            system_prompt="coder",
        )

        group = await task_board.create_group(title="Concurrency Test", created_by="pm")

        # Create 10 tasks
        for i in range(10):
            await task_board.create_task(
                group_id=group["id"],
                title=f"Concurrent task {i}",
                task_type="implementation",
                assigned_to="coder",
            )

        # Register 10 agent instances
        for i in range(10):
            await instance_mgr.register_instance(f"coder-{i}", role_cfg)

        # Rapidly claim tasks sequentially (simulates contention)
        start = time.monotonic()
        results = []
        for i in range(10):
            result = await task_board.claim_task("coder", f"coder-{i}")
            results.append(result)
        elapsed = time.monotonic() - start

        # Filter out None results (queue exhausted)
        claimed = [r for r in results if r is not None]

        # All 10 tasks should be claimed (one per agent)
        assert len(claimed) == 10

        # Verify no duplicate claims: each claimed task ID should be unique
        claimed_ids = [c["id"] for c in claimed]
        assert len(claimed_ids) == len(set(claimed_ids)), (
            f"Duplicate claims detected: {claimed_ids}"
        )

        # Should complete within 5 seconds
        assert elapsed < 5.0, f"10 rapid claims took {elapsed:.2f}s (limit: 5s)"

    async def test_large_query_results_paginate(self, task_board: TaskBoard):
        """search_tasks pagination works correctly with many results."""
        group = await task_board.create_group(title="Pagination Test", created_by="pm")

        # Create 50 tasks with a common keyword
        for i in range(50):
            await task_board.create_task(
                group_id=group["id"],
                title=f"Searchable widget task {i}",
                task_type="implementation",
                assigned_to="coder",
            )

        # Page 1: first 10
        page1 = await task_board.search_tasks("widget", limit=10, offset=0)
        assert page1["total"] == 50
        assert len(page1["tasks"]) == 10
        assert page1["limit"] == 10
        assert page1["offset"] == 0

        # Page 2: next 10
        page2 = await task_board.search_tasks("widget", limit=10, offset=10)
        assert page2["total"] == 50
        assert len(page2["tasks"]) == 10

        # Verify page 1 and page 2 have different tasks
        page1_ids = {t["id"] for t in page1["tasks"]}
        page2_ids = {t["id"] for t in page2["tasks"]}
        assert page1_ids.isdisjoint(page2_ids), "Pages should not overlap"

        # Last page
        page_last = await task_board.search_tasks("widget", limit=10, offset=40)
        assert len(page_last["tasks"]) == 10

        # Beyond last page
        page_empty = await task_board.search_tasks("widget", limit=10, offset=50)
        assert len(page_empty["tasks"]) == 0

    async def test_batch_operations_do_not_block(self, task_board: TaskBoard):
        """Batch cancel on 50 tasks completes without blocking the event loop."""
        group = await task_board.create_group(title="Batch Test", created_by="pm")

        task_ids = []
        for i in range(50):
            t = await task_board.create_task(
                group_id=group["id"],
                title=f"Batch task {i}",
                task_type="implementation",
                assigned_to="coder",
            )
            task_ids.append(t["id"])

        # Run batch cancel alongside a trivial coroutine to verify
        # the event loop isn't blocked
        flag = False

        async def set_flag():
            nonlocal flag
            await asyncio.sleep(0)
            flag = True

        start = time.monotonic()
        batch_result, _ = await asyncio.gather(
            task_board.batch_update_tasks(task_ids, "cancel", {"reason": "perf test"}),
            set_flag(),
        )
        elapsed = time.monotonic() - start

        assert flag is True, "Event loop was blocked during batch operation"
        assert batch_result["updated"] == 50
        assert elapsed < 10.0, f"Batch cancel of 50 tasks took {elapsed:.2f}s (limit: 10s)"


# ------------------------------------------------------------------
# Memory usage
# ------------------------------------------------------------------


class TestMemoryUsage:
    """Tests verifying memory does not grow unbounded."""

    async def test_memory_bounded_on_large_result_sets(self, db: Database):
        """Large query results do not consume unbounded memory."""
        # Insert 500 rows
        for i in range(500):
            await db.execute(
                "INSERT INTO notifications (type, title, message, severity, created_at) "
                "VALUES (?, ?, ?, ?, datetime('now'))",
                ("test", f"Title {i}", f"Message body for notification {i}", "info"),
            )

        # Measure memory of result set
        rows = await db.execute_fetchall("SELECT * FROM notifications")
        assert len(rows) == 500

        result_size = sys.getsizeof(rows)
        # Each dict row plus the list overhead should be bounded.
        # 500 rows should be well under 1MB.
        assert result_size < 1_000_000, (
            f"Result set size {result_size} bytes exceeds 1MB limit"
        )

    async def test_event_bus_history_bounded(self, event_bus: EventBus):
        """EventBus history does not grow beyond MAX_HISTORY."""
        for i in range(EventBus.MAX_HISTORY + 500):
            await event_bus.emit("perf.test", {"index": i})

        history = event_bus.get_history()
        assert len(history) <= EventBus.MAX_HISTORY
        # Verify we have the most recent events, not the oldest
        last_event = history[-1]
        assert last_event["index"] == EventBus.MAX_HISTORY + 499


# ------------------------------------------------------------------
# WebSocket / EventBus performance
# ------------------------------------------------------------------


class TestEventBusPerformance:
    """Tests for event system throughput."""

    async def test_websocket_connect_disconnect_fast(self):
        """ConnectionManager connect/disconnect cycle is fast."""
        from taskbrew.dashboard.app import ConnectionManager

        mgr = ConnectionManager()

        # Simulate lightweight connect/disconnect with mocks
        from unittest.mock import AsyncMock

        connections = []
        for _ in range(50):
            ws = AsyncMock()
            ws.accept = AsyncMock()
            ws.send_text = AsyncMock()
            connections.append(ws)

        start = time.monotonic()
        for ws in connections:
            await mgr.connect(ws)
        for ws in connections:
            mgr.disconnect(ws)
        elapsed = time.monotonic() - start

        assert len(mgr.active) == 0
        assert elapsed < 1.0, (
            f"50 connect/disconnect cycles took {elapsed:.2f}s (limit: 1s)"
        )

    async def test_event_bus_high_throughput(self, event_bus: EventBus):
        """EventBus can emit 1000 events in under 2 seconds."""
        received = []

        async def handler(event):
            received.append(event)

        event_bus.subscribe("perf.test", handler)

        start = time.monotonic()
        for i in range(1000):
            await event_bus.emit("perf.test", {"i": i})
        # Allow tasks to complete
        await asyncio.sleep(0.5)
        elapsed = time.monotonic() - start

        assert elapsed < 2.5, f"1000 events took {elapsed:.2f}s (limit: 2.5s)"
        # Handler should have received most events (async tasks may be in flight)
        assert len(received) > 900, (
            f"Only {len(received)} of 1000 events received"
        )


# ------------------------------------------------------------------
# API endpoint response time (via ASGI transport)
# ------------------------------------------------------------------


class TestAPIPerformance:
    """Tests verifying API endpoints respond within time bounds."""

    @pytest.fixture
    async def client(self, tmp_path: Path):
        """Create an AsyncClient for API timing tests."""
        from httpx import AsyncClient, ASGITransport
        from taskbrew.orchestrator.migration import MigrationManager

        db = Database(str(tmp_path / "api_perf.db"))
        await db.initialize()

        mm = MigrationManager(db)
        await mm.apply_pending()

        board = TaskBoard(db, group_prefixes={"pm": "FEAT"})
        await board.register_prefixes({"pm": "PM", "coder": "CD", "architect": "AR"})
        event_bus = EventBus()
        instance_mgr = InstanceManager(db)

        from taskbrew.intelligence.quality import QualityManager
        from taskbrew.intelligence.memory import MemoryManager
        from taskbrew.intelligence.collaboration import CollaborationManager
        from taskbrew.intelligence.specialization import SpecializationManager
        from taskbrew.intelligence.planning import PlanningManager
        from taskbrew.intelligence.preflight import PreflightChecker
        from taskbrew.intelligence.impact import ImpactAnalyzer
        from taskbrew.intelligence.escalation import EscalationManager
        from taskbrew.intelligence.checkpoints import CheckpointManager
        from taskbrew.intelligence.messaging import MessagingManager
        from taskbrew.intelligence.knowledge_graph import KnowledgeGraphBuilder
        from taskbrew.intelligence.review_learning import ReviewLearningManager
        from taskbrew.intelligence.tool_router import ToolRouter
        from taskbrew.intelligence.context_providers import ContextProviderRegistry

        from taskbrew.intelligence.autonomous import AutonomousManager
        from taskbrew.intelligence.code_intel import CodeIntelligenceManager
        from taskbrew.intelligence.learning import LearningManager
        from taskbrew.intelligence.coordination import CoordinationManager
        from taskbrew.intelligence.testing_quality import TestingQualityManager
        from taskbrew.intelligence.security_intel import SecurityIntelManager
        from taskbrew.intelligence.observability import ObservabilityManager
        from taskbrew.intelligence.advanced_planning import AdvancedPlanningManager

        memory_manager = MemoryManager(db)
        context_registry = ContextProviderRegistry(db, project_dir=str(tmp_path))

        class _Orch:
            pass

        orch = _Orch()
        orch.db = db
        orch.task_board = board
        orch.event_bus = event_bus
        orch.instance_manager = instance_mgr
        orch.roles = {}
        orch.team_config = None
        orch.project_dir = str(tmp_path)
        orch.memory_manager = memory_manager
        orch.context_registry = context_registry
        orch.quality_manager = QualityManager(db, memory_manager=memory_manager)
        orch.collaboration_manager = CollaborationManager(db, task_board=board, event_bus=event_bus)
        orch.specialization_manager = SpecializationManager(db)
        orch.planning_manager = PlanningManager(db, task_board=board)
        orch.preflight_checker = PreflightChecker(db)
        orch.impact_analyzer = ImpactAnalyzer(db, project_dir=str(tmp_path))
        orch.escalation_manager = EscalationManager(db, task_board=board, event_bus=event_bus)
        orch.checkpoint_manager = CheckpointManager(db, event_bus=event_bus)
        orch.messaging_manager = MessagingManager(db, event_bus=event_bus)
        orch.knowledge_graph = KnowledgeGraphBuilder(db, project_dir=str(tmp_path))
        orch.review_learning = ReviewLearningManager(db)
        orch.tool_router = ToolRouter(db)
        orch.autonomous_manager = AutonomousManager(db, task_board=board, memory_manager=memory_manager)
        orch.code_intel_manager = CodeIntelligenceManager(db, project_dir=str(tmp_path))
        orch.learning_manager = LearningManager(db, memory_manager=memory_manager)
        orch.coordination_manager = CoordinationManager(
            db, task_board=board, event_bus=event_bus, instance_manager=instance_mgr
        )
        orch.testing_quality_manager = TestingQualityManager(db, project_dir=str(tmp_path))
        orch.security_intel_manager = SecurityIntelManager(db, project_dir=str(tmp_path))
        orch.observability_manager = ObservabilityManager(db, event_bus=event_bus)
        orch.advanced_planning_manager = AdvancedPlanningManager(db)

        from taskbrew.dashboard.app import create_app
        from taskbrew.dashboard.routers._deps import set_orchestrator

        app = create_app(
            event_bus=event_bus,
            task_board=board,
            instance_manager=instance_mgr,
        )
        set_orchestrator(orch)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c
        await db.close()

    async def test_health_endpoint_responds_fast(self, client):
        """GET /api/health responds within 200ms."""
        start = time.monotonic()
        resp = await client.get("/api/health")
        elapsed = time.monotonic() - start

        assert resp.status_code == 200
        assert elapsed < 0.2, f"Health check took {elapsed:.3f}s (limit: 0.2s)"

    async def test_intelligence_v2_endpoint_responds_within_500ms(self, client):
        """Intelligence v2 endpoint responds within 500ms."""
        endpoints = [
            "/api/v2/autonomous/discoveries",
            "/api/v2/code-intel/patterns",
            "/api/v2/learning/conventions",
            "/api/v2/coordination/standups",
        ]

        for endpoint in endpoints:
            start = time.monotonic()
            resp = await client.get(endpoint)
            elapsed = time.monotonic() - start

            assert resp.status_code == 200, f"{endpoint} returned {resp.status_code}"
            assert elapsed < 0.5, (
                f"{endpoint} took {elapsed:.3f}s (limit: 0.5s)"
            )
