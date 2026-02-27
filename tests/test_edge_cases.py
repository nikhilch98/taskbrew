"""Edge-case tests for the TaskBrew API.

Tests boundary conditions: empty inputs, zero values, missing data,
and other edge cases across coordination, observability, and
advanced-planning endpoints.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import AsyncClient, ASGITransport

from taskbrew.orchestrator.database import Database
from taskbrew.orchestrator.event_bus import EventBus
from taskbrew.orchestrator.task_board import TaskBoard
from taskbrew.agents.instance_manager import InstanceManager


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


async def _build_full_env(tmp_path: Path):
    """Build a fully-wired test environment with all intelligence managers."""
    db = Database(str(tmp_path / "test.db"))
    await db.initialize()

    from taskbrew.orchestrator.migration import MigrationManager

    mm = MigrationManager(db)
    await mm.apply_pending()

    board = TaskBoard(db, group_prefixes={"pm": "FEAT"})
    await board.register_prefixes(
        {"pm": "PM", "architect": "AR", "coder": "CD", "verifier": "VR"}
    )
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
    orch.collaboration_manager = CollaborationManager(
        db, task_board=board, event_bus=event_bus
    )
    orch.specialization_manager = SpecializationManager(db)
    orch.planning_manager = PlanningManager(db, task_board=board)
    orch.preflight_checker = PreflightChecker(db)
    orch.impact_analyzer = ImpactAnalyzer(db, project_dir=str(tmp_path))
    orch.escalation_manager = EscalationManager(
        db, task_board=board, event_bus=event_bus
    )
    orch.checkpoint_manager = CheckpointManager(db, event_bus=event_bus)
    orch.messaging_manager = MessagingManager(db, event_bus=event_bus)
    orch.knowledge_graph = KnowledgeGraphBuilder(db, project_dir=str(tmp_path))
    orch.review_learning = ReviewLearningManager(db)
    orch.tool_router = ToolRouter(db)

    orch.autonomous_manager = AutonomousManager(
        db, task_board=board, memory_manager=memory_manager
    )
    orch.code_intel_manager = CodeIntelligenceManager(
        db, project_dir=str(tmp_path)
    )
    orch.learning_manager = LearningManager(db, memory_manager=memory_manager)
    orch.coordination_manager = CoordinationManager(
        db, task_board=board, event_bus=event_bus, instance_manager=instance_mgr
    )
    orch.testing_quality_manager = TestingQualityManager(
        db, project_dir=str(tmp_path)
    )
    orch.security_intel_manager = SecurityIntelManager(
        db, project_dir=str(tmp_path)
    )
    orch.observability_manager = ObservabilityManager(db, event_bus=event_bus)
    orch.advanced_planning_manager = AdvancedPlanningManager(
        db, task_board=board
    )

    return orch, db


@pytest.fixture
async def client(tmp_path):
    """AsyncClient backed by a fully-wired FastAPI app."""
    orch, db = await _build_full_env(tmp_path)

    from taskbrew.dashboard.app import create_app
    from taskbrew.dashboard.routers._deps import set_orchestrator
    import taskbrew.dashboard.routers.intelligence_v2 as v2_mod

    app = create_app(
        event_bus=orch.event_bus,
        task_board=orch.task_board,
        instance_manager=orch.instance_manager,
    )
    set_orchestrator(orch)

    v2_mod._obs_tables_ensured = False
    v2_mod._planning_tables_ensured = False
    v2_mod._testing_tables_ensured = False
    v2_mod._security_tables_ensured = False

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    await db.close()


# ------------------------------------------------------------------
# Helper
# ------------------------------------------------------------------


async def _create_task(client: AsyncClient, title: str = "Test task") -> dict:
    """Create a goal and return the response containing group_id and task_id."""
    resp = await client.post("/api/goals", json={"title": title})
    assert resp.status_code == 200
    return resp.json()


# ===================================================================
# Empty / boundary schedule (1 test)
# ===================================================================


class TestScheduleBoundaries:
    """Schedule building with zero tasks or empty groups."""

    async def test_empty_group_schedule(self, client):
        """Build schedule for a group with no tasks returns empty list."""
        # Create a group (via goal) but do not add extra tasks --
        # the goal creates one task, so we use a fresh non-existent group id.
        resp = await client.get(
            "/api/v2/planning/schedule/NONEXISTENT-GRP"
        )
        assert resp.status_code == 200
        assert resp.json() == []


# ===================================================================
# Deadline estimation edge case (1 test)
# ===================================================================


class TestDeadlineEstimation:
    """Deadline estimation with no prior history."""

    async def test_deadline_estimate_no_history(self, client):
        """Estimate deadline with no completed tasks of the same type falls back to default."""
        goal = await _create_task(client, "Deadline no history test")
        task_id = goal["task_id"]

        resp = await client.post(
            f"/api/v2/planning/deadline/{task_id}"
        )
        assert resp.status_code == 200
        data = resp.json()
        # With no historical samples the manager defaults to 1.0 hour
        assert data["based_on_samples"] == 0
        assert data["estimated_hours"] == 1.0


# ===================================================================
# Standup with no tasks (1 test)
# ===================================================================


class TestStandupEdgeCases:
    """Standup generation when agent has no activity."""

    async def test_standup_no_tasks(self, client):
        """Generate standup for an agent with zero tasks returns empty lists."""
        resp = await client.post(
            "/api/v2/coordination/standups/nonexistent-agent-99"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["agent_id"] == "nonexistent-agent-99"
        assert data["completed_tasks"] == []
        assert data["in_progress_tasks"] == []
        assert data["blockers"] == []


# ===================================================================
# Heartbeat boundary values (2 tests)
# ===================================================================


class TestHeartbeatBoundaries:
    """Heartbeats at boundary progress percentages."""

    async def test_heartbeat_zero_progress(self, client):
        """Record heartbeat with 0% progress succeeds."""
        goal = await _create_task(client, "Heartbeat 0%")
        task_id = goal["task_id"]

        resp = await client.post(
            "/api/v2/coordination/heartbeats",
            json={
                "task_id": task_id,
                "agent_id": "coder-1",
                "progress_pct": 0.0,
                "status_message": "Just started",
            },
        )
        assert resp.status_code == 200

    async def test_heartbeat_100_progress(self, client):
        """Record heartbeat with 100% progress succeeds."""
        goal = await _create_task(client, "Heartbeat 100%")
        task_id = goal["task_id"]

        resp = await client.post(
            "/api/v2/coordination/heartbeats",
            json={
                "task_id": task_id,
                "agent_id": "coder-1",
                "progress_pct": 100.0,
                "status_message": "All done",
            },
        )
        assert resp.status_code == 200


# ===================================================================
# Anomaly detection with insufficient data (1 test)
# ===================================================================


class TestAnomalyDetection:
    """Anomaly detection requires at least 3 data points per metric."""

    async def test_anomaly_detection_insufficient_data(self, client):
        """Detect anomalies with fewer than 3 data points returns empty list."""
        # Record only 2 behavior metrics
        for val in [1.0, 2.0]:
            await client.post(
                "/api/v2/observability/behavior",
                json={
                    "agent_role": "lonely-agent",
                    "metric_type": "response_time",
                    "value": val,
                    "period_start": "2026-02-25T00:00:00Z",
                    "period_end": "2026-02-26T00:00:00Z",
                },
            )

        resp = await client.post(
            "/api/v2/observability/anomalies/lonely-agent"
        )
        assert resp.status_code == 200
        assert resp.json() == []


# ===================================================================
# Cost attribution with zero cost (1 test)
# ===================================================================


class TestCostAttributionEdgeCases:
    """Cost attribution at boundary values."""

    async def test_cost_attribution_zero_cost(self, client):
        """Attribute $0 cost succeeds without error."""
        resp = await client.post(
            "/api/v2/observability/costs",
            json={
                "agent_id": "free-agent",
                "cost_usd": 0.0,
                "input_tokens": 0,
                "output_tokens": 0,
                "feature_tag": "free_tier",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["cost_usd"] == 0.0


# ===================================================================
# Bottleneck detection with no tasks (1 test)
# ===================================================================


class TestBottleneckEdgeCases:
    """Bottleneck detection when no tasks exist in the pipeline."""

    async def test_bottleneck_no_tasks(self, client):
        """Detect bottlenecks with no in-progress tasks returns empty list."""
        resp = await client.post("/api/v2/observability/bottlenecks")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        # No in-progress tasks means no bottlenecks detected
        assert len(data) == 0


# ===================================================================
# Consensus with no votes (1 test)
# ===================================================================


class TestConsensusEdgeCases:
    """Tally votes when no votes have been cast."""

    async def test_consensus_no_votes(self, client):
        """Tally votes with zero votes returns no_quorum."""
        await client.post(
            "/api/v2/coordination/proposals/PROP-EMPTY",
            params={"description": "No-vote test"},
        )

        resp = await client.get(
            "/api/v2/coordination/votes/PROP-EMPTY/tally"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["result"] == "no_quorum"


# ===================================================================
# Work stealing with no overloaded agents (1 test)
# ===================================================================


class TestWorkStealingEdgeCases:
    """Work stealing when no agent is overloaded."""

    async def test_work_stealing_no_overloaded_agents(self, client):
        """Find stealable tasks when no agent has >3 pending tasks returns empty."""
        resp = await client.get(
            "/api/v2/coordination/stealable/idle-agent-99"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) == 0


# ===================================================================
# Additional edge cases (2 tests)
# ===================================================================


class TestAdditionalEdgeCases:
    """Extra edge case scenarios to strengthen coverage."""

    async def test_get_discoveries_empty(self, client):
        """Get discoveries when none exist returns empty list."""
        resp = await client.get("/api/v2/autonomous/discoveries")
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_resolve_bids_no_bids(self, client):
        """Resolve bids for a task with no bids returns null winner."""
        goal = await _create_task(client, "No bids task")
        task_id = goal["task_id"]

        resp = await client.post(
            f"/api/v2/autonomous/bids/{task_id}/resolve"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["winner"] is None
