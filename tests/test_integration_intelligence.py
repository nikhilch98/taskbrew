"""Integration tests: intelligence features working together.

These tests verify that the various intelligence managers (quality, memory,
collaboration, escalation, context providers, impact analysis, planning)
work correctly end-to-end when wired together through the orchestrator.
"""

from __future__ import annotations

import textwrap
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

from taskbrew.orchestrator.database import Database
from taskbrew.orchestrator.event_bus import EventBus
from taskbrew.orchestrator.task_board import TaskBoard


# ------------------------------------------------------------------
# Helpers: set up a lightweight orchestrator-like environment without
# requiring the full config directory tree that build_orchestrator needs.
# ------------------------------------------------------------------


async def _make_db(tmp_path: Path) -> Database:
    """Create and initialize a Database, running all migrations."""
    db = Database(str(tmp_path / "test.db"))
    await db.initialize()
    from taskbrew.orchestrator.migration import MigrationManager

    mm = MigrationManager(db)
    await mm.apply_pending()
    return db


@pytest.fixture
async def env(tmp_path):
    """Lightweight test environment with DB, board, event bus, and managers."""
    db = await _make_db(tmp_path)
    event_bus = EventBus()
    board = TaskBoard(db, group_prefixes={"pm": "FEAT"})
    await board.register_prefixes({"pm": "PM", "architect": "AR", "coder": "CD", "verifier": "VR"})

    from taskbrew.intelligence.quality import QualityManager
    from taskbrew.intelligence.memory import MemoryManager
    from taskbrew.intelligence.collaboration import CollaborationManager
    from taskbrew.intelligence.escalation import EscalationManager
    from taskbrew.intelligence.context_providers import (
        ContextProviderRegistry,
        CrossTaskProvider,
        IssueTrackerProvider,
        RuntimeContextProvider,
    )
    from taskbrew.intelligence.impact import ImpactAnalyzer
    from taskbrew.intelligence.planning import PlanningManager

    memory_manager = MemoryManager(db)
    quality_manager = QualityManager(db, memory_manager=memory_manager)
    collaboration_manager = CollaborationManager(db, task_board=board, event_bus=event_bus)
    escalation_manager = EscalationManager(db, task_board=board, event_bus=event_bus)
    context_registry = ContextProviderRegistry(db, project_dir=str(tmp_path))
    context_registry.register(CrossTaskProvider(db))
    context_registry.register(IssueTrackerProvider(db))
    context_registry.register(RuntimeContextProvider(db))
    impact_analyzer = ImpactAnalyzer(db, project_dir=str(tmp_path))
    planning_manager = PlanningManager(db, task_board=board)

    yield {
        "db": db,
        "board": board,
        "event_bus": event_bus,
        "quality_manager": quality_manager,
        "memory_manager": memory_manager,
        "collaboration_manager": collaboration_manager,
        "escalation_manager": escalation_manager,
        "context_registry": context_registry,
        "impact_analyzer": impact_analyzer,
        "planning_manager": planning_manager,
        "tmp_path": tmp_path,
    }
    await db.close()


async def _create_task(board: TaskBoard, title: str = "Test task", **kwargs) -> dict:
    """Helper to create a group + task quickly."""
    group = await board.create_group(title="Test Group", origin="pm", created_by="pm")
    defaults = {
        "group_id": group["id"],
        "title": title,
        "task_type": "implementation",
        "assigned_to": "coder",
        "created_by": "pm-1",
    }
    defaults.update(kwargs)
    task = await board.create_task(**defaults)
    return task


# ------------------------------------------------------------------
# Test: Quality scoring end-to-end
# ------------------------------------------------------------------


class TestQualityScoringEndToEnd:
    """Feature 41/44/45: quality scoring pipeline."""

    async def test_extract_self_review_records_and_retrieves(self, env):
        """Create a task, run self-review extraction, and verify scores are persisted."""
        qm = env["quality_manager"]
        board = env["board"]

        task = await _create_task(board, "Implement login feature")
        output = (
            "I implemented the login feature using pytest for testing.\n"
            "Added error handling with try/except blocks.\n"
            "```python\ndef login(): ...\n```\n"
            "Verified the code works correctly."
        )

        result = await qm.extract_self_review(task["id"], "coder-1", output)
        assert result["score"] > 0.5
        assert result["signals"]["mentions_testing"] is True
        assert result["signals"]["mentions_error_handling"] is True
        assert result["signals"]["has_code_blocks"] is True

        # Verify score is stored and retrievable
        scores = await qm.get_scores(task_id=task["id"])
        assert len(scores) >= 1
        assert scores[0]["score_type"] == "self_review"
        assert scores[0]["task_id"] == task["id"]

    async def test_confidence_scoring(self, env):
        """Feature 45: confidence scoring based on language analysis."""
        qm = env["quality_manager"]
        board = env["board"]

        task = await _create_task(board, "Fix a bug")

        # High-confidence output
        high_conf = "I verified and confirmed that all tests pass successfully. The fix is complete."
        score_high = await qm.score_confidence(task["id"], "coder-1", high_conf)

        # Low-confidence output
        low_conf = "I'm not sure, maybe this could be the issue. Perhaps it might work, I think."
        score_low = await qm.score_confidence(task["id"], "coder-1", low_conf)

        assert score_high > score_low

    async def test_code_quality_scoring(self, env):
        """Feature 44: code quality scoring."""
        qm = env["quality_manager"]
        board = env["board"]

        task = await _create_task(board, "Add user model")
        code_output = textwrap.dedent("""\
            import os
            from dataclasses import dataclass

            @dataclass
            class User:
                '''User model for authentication.'''
                name: str
                email: str

                def validate(self) -> bool:
                    try:
                        return "@" in self.email
                    except Exception:
                        return False
        """)

        result = await qm.score_code_quality(task["id"], "coder-1", code_output)
        assert result["score"] > 0.0
        assert result["checks"]["has_imports"] is True
        assert result["checks"]["has_classes"] is True
        assert result["checks"]["has_docstrings"] is True

        # Verify all three score types are retrievable
        scores = await qm.get_scores(task_id=task["id"])
        assert len(scores) >= 1

    async def test_full_quality_pipeline(self, env):
        """All quality methods produce scores retrievable via get_scores."""
        qm = env["quality_manager"]
        board = env["board"]

        task = await _create_task(board, "Full pipeline task")
        output = "Confirmed: all tests pass. Implemented with try/except. ```code```"

        await qm.extract_self_review(task["id"], "coder-1", output)
        await qm.score_confidence(task["id"], "coder-1", output)
        await qm.score_code_quality(task["id"], "coder-1", output)

        scores = await qm.get_scores(task_id=task["id"])
        score_types = {s["score_type"] for s in scores}
        assert "self_review" in score_types
        assert "confidence" in score_types
        assert "code_quality" in score_types


# ------------------------------------------------------------------
# Test: Memory store and recall
# ------------------------------------------------------------------


class TestMemoryStoreAndRecall:
    """Features 35-40: memory management."""

    async def test_store_and_recall_lesson(self, env):
        """Store a lesson and recall it by keyword search."""
        mm = env["memory_manager"]

        await mm.store_lesson(
            "coder", "Always validate input", "Never trust user input in forms",
            tags=["security", "input"],
        )
        memories = await mm.recall("coder", "validate input security")
        assert len(memories) >= 1
        assert "validate" in memories[0]["title"].lower()

    async def test_recall_updates_access_count(self, env):
        """Recalling a memory should increment its access_count."""
        mm = env["memory_manager"]
        db = env["db"]

        await mm.store_lesson(
            "coder", "Unique test lesson for access count",
            "This tests the access tracking mechanism",
            tags=["tracking"],
        )

        # First recall
        results = await mm.recall("coder", "unique test lesson access count")
        assert len(results) >= 1
        mem_id = results[0]["id"]

        # Check access_count was incremented
        row = await db.execute_fetchone(
            "SELECT access_count FROM agent_memories WHERE id = ?", (mem_id,)
        )
        assert row["access_count"] >= 1

    async def test_store_and_recall_pattern(self, env):
        """Store a pattern and find it by tags."""
        mm = env["memory_manager"]

        await mm.store_pattern(
            "coder", "Singleton pattern",
            "Use __new__ for singleton implementation",
            tags=["design_pattern", "python"],
        )
        patterns = await mm.find_patterns("coder", tags=["design_pattern"])
        assert len(patterns) >= 1

    async def test_memory_types_isolation(self, env):
        """Different memory types are properly isolated."""
        mm = env["memory_manager"]

        await mm.store_lesson("coder", "A lesson", "content")
        await mm.store_pattern("coder", "A pattern", "content")
        await mm.store_style_rule("coder", "Always use type hints")

        lessons = await mm.get_memories(agent_role="coder", memory_type="lesson")
        patterns = await mm.get_memories(agent_role="coder", memory_type="pattern")
        styles = await mm.get_memories(agent_role="coder", memory_type="style")

        # Each type should have at least 1 entry
        assert len(lessons) >= 1
        assert len(patterns) >= 1
        assert len(styles) >= 1

        # Titles should match expected types
        assert all(m["memory_type"] == "lesson" for m in lessons)
        assert all(m["memory_type"] == "pattern" for m in patterns)
        assert all(m["memory_type"] == "style" for m in styles)


# ------------------------------------------------------------------
# Test: Collaboration peer review flow
# ------------------------------------------------------------------


class TestCollaborationPeerReviewFlow:
    """Feature 11: peer review task creation."""

    async def test_peer_review_creates_review_task(self, env):
        """Request peer review -> creates a code_review task with correct parent."""
        cm = env["collaboration_manager"]
        board = env["board"]
        db = env["db"]

        task = await _create_task(board, "Implement auth module")

        result = await cm.request_peer_review(task["id"], reviewer_role="coder")
        assert "review_task_id" in result
        assert result["original_task_id"] == task["id"]

        # Verify the review task was created in the database
        review = await db.execute_fetchone(
            "SELECT * FROM tasks WHERE id = ?", (result["review_task_id"],)
        )
        assert review is not None
        assert review["task_type"] == "code_review"
        assert review["parent_id"] == task["id"]
        assert review["assigned_to"] == "coder"
        assert "Peer Review" in review["title"]

    async def test_peer_review_on_nonexistent_task(self, env):
        """Requesting peer review on a non-existent task returns an error."""
        cm = env["collaboration_manager"]
        result = await cm.request_peer_review("NONEXISTENT-999")
        assert "error" in result

    async def test_peer_review_emits_event(self, env):
        """Peer review request should emit an event."""
        import asyncio

        cm = env["collaboration_manager"]
        board = env["board"]
        event_bus = env["event_bus"]

        events_received = []

        async def capture_event(e):
            events_received.append(e)

        event_bus.subscribe("collaboration.peer_review_requested", capture_event)

        task = await _create_task(board, "Event test task")
        await cm.request_peer_review(task["id"])

        # Give asyncio.create_task handlers a chance to run
        await asyncio.sleep(0.05)

        assert len(events_received) == 1
        assert events_received[0]["original_task_id"] == task["id"]


# ------------------------------------------------------------------
# Test: Escalation check stuck
# ------------------------------------------------------------------


class TestEscalationCheckStuck:
    """Feature 18: escalation detection for stuck tasks."""

    async def test_detects_stuck_task(self, env):
        """A task in_progress for longer than the threshold should be detected."""
        em = env["escalation_manager"]
        db = env["db"]
        board = env["board"]

        task = await _create_task(board, "Stuck task")

        # Manually set the task to in_progress with an old started_at
        old_time = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        await db.execute(
            "UPDATE tasks SET status = 'in_progress', started_at = ?, claimed_by = 'coder-1' WHERE id = ?",
            (old_time, task["id"]),
        )

        stuck = await em.check_stuck_tasks(timeout_minutes=30)
        stuck_ids = [s["id"] for s in stuck]
        assert task["id"] in stuck_ids

    async def test_does_not_flag_recent_task(self, env):
        """A task started recently should not be flagged as stuck."""
        em = env["escalation_manager"]
        db = env["db"]
        board = env["board"]

        task = await _create_task(board, "Fresh task")
        now = datetime.now(timezone.utc).isoformat()
        await db.execute(
            "UPDATE tasks SET status = 'in_progress', started_at = ?, claimed_by = 'coder-1' WHERE id = ?",
            (now, task["id"]),
        )

        stuck = await em.check_stuck_tasks(timeout_minutes=30)
        stuck_ids = [s["id"] for s in stuck]
        assert task["id"] not in stuck_ids

    async def test_escalation_creates_notification(self, env):
        """Creating an escalation should also create a notification."""
        em = env["escalation_manager"]
        db = env["db"]
        board = env["board"]

        task = await _create_task(board, "Needs escalation")
        await em.escalate(task["id"], "coder-1", "Task is blocked on external dependency", severity="high")

        notifications = await db.get_unread_notifications(10)
        assert len(notifications) >= 1
        assert any("Escalation" in n["title"] for n in notifications)


# ------------------------------------------------------------------
# Test: Context provider registry
# ------------------------------------------------------------------


class TestContextProviderRegistry:
    """Features 25-32: context provider registry with caching."""

    async def test_register_and_get_context(self, env):
        """Register providers, call get_context, verify data returned."""
        registry = env["context_registry"]

        # The registry already has CrossTaskProvider, IssueTrackerProvider, RuntimeContextProvider
        available = registry.get_available_providers()
        assert "cross_task" in available
        assert "issue_tracker" in available

        # get_context should not raise even with no tasks
        ctx = await registry.get_context(["cross_task", "issue_tracker", "runtime"])
        # With empty DB, most providers return empty strings
        assert isinstance(ctx, str)

    async def test_caching_works(self, env):
        """Second call to get_context should use cached data."""
        registry = env["context_registry"]
        db = env["db"]
        board = env["board"]

        # Create a task so issue_tracker has something to return
        await _create_task(board, "Context caching test")

        # First call - fresh fetch
        ctx1 = await registry.get_context(["issue_tracker"])

        # Check that a cache entry was created
        cached = await db.execute_fetchone(
            "SELECT * FROM context_snapshots WHERE context_type = 'issue_tracker' ORDER BY created_at DESC LIMIT 1"
        )
        assert cached is not None

        # Second call - should use cache
        ctx2 = await registry.get_context(["issue_tracker"])
        assert ctx2 == ctx1  # Same data from cache

    async def test_unknown_provider_silently_skipped(self, env):
        """Requesting a non-existent provider should not raise."""
        registry = env["context_registry"]
        ctx = await registry.get_context(["nonexistent_provider"])
        assert ctx == ""


# ------------------------------------------------------------------
# Test: Impact analysis
# ------------------------------------------------------------------


class TestImpactAnalysis:
    """Feature 22: impact analysis for code changes."""

    async def test_trace_dependencies_detects_imports(self, env):
        """Create a Python file and verify imports are detected."""
        tmp_path = env["tmp_path"]
        ia = env["impact_analyzer"]

        # Create a Python file with imports
        src_dir = tmp_path / "src"
        src_dir.mkdir(exist_ok=True)
        (src_dir / "app.py").write_text(textwrap.dedent("""\
            import os
            import json
            from pathlib import Path

            def main():
                pass
        """))

        result = await ia.trace_dependencies("src/app.py")
        assert result["file"] == "src/app.py"
        assert "os" in result["imports"]
        assert "json" in result["imports"]
        assert "pathlib" in result["imports"]

    async def test_trace_dependencies_finds_importers(self, env):
        """When file B imports file A, trace_dependencies(A) should list B."""
        tmp_path = env["tmp_path"]
        ia = env["impact_analyzer"]

        src_dir = tmp_path / "src"
        src_dir.mkdir(exist_ok=True)
        (src_dir / "utils.py").write_text("def helper(): pass\n")
        (src_dir / "main.py").write_text("from utils import helper\n\ndef run(): helper()\n")

        result = await ia.trace_dependencies("src/utils.py")
        assert "src/main.py" in result["imported_by"]
        assert result["blast_radius"] >= 1

    async def test_nonexistent_file_returns_empty(self, env):
        """Tracing a nonexistent file should return empty imports."""
        ia = env["impact_analyzer"]
        result = await ia.trace_dependencies("nonexistent/file.py")
        assert result["imports"] == []


# ------------------------------------------------------------------
# Test: Planning alternatives
# ------------------------------------------------------------------


class TestPlanningAlternatives:
    """Feature 21: task-type-aware alternative generation."""

    async def test_implementation_task_alternatives(self, env):
        """Implementation tasks should get implementation-specific alternatives."""
        pm = env["planning_manager"]
        board = env["board"]

        task = await _create_task(board, "Build user dashboard", task_type="implementation")
        result = await pm.generate_alternatives(task["id"])

        assert "content" in result
        content = result["content"]
        assert content["task_type"] == "implementation"
        assert len(content["approaches"]) >= 2
        assert any("Direct" in a["name"] for a in content["approaches"])

    async def test_bug_fix_task_alternatives(self, env):
        """Bug fix tasks should get fix-specific alternatives."""
        pm = env["planning_manager"]
        board = env["board"]

        task = await _create_task(board, "Fix login crash", task_type="bug_fix")
        result = await pm.generate_alternatives(task["id"])

        content = result["content"]
        assert content["task_type"] == "bug_fix"
        assert any("Hot fix" in a["name"] or "Root cause" in a["name"] for a in content["approaches"])

    async def test_code_review_task_alternatives(self, env):
        """Code review tasks should get review-specific alternatives."""
        pm = env["planning_manager"]
        board = env["board"]

        task = await _create_task(board, "Review auth module", task_type="code_review")
        result = await pm.generate_alternatives(task["id"])

        content = result["content"]
        assert content["task_type"] == "code_review"

    async def test_nonexistent_task_returns_error(self, env):
        """Generating alternatives for a non-existent task returns error."""
        pm = env["planning_manager"]
        result = await pm.generate_alternatives("FAKE-999")
        assert "error" in result

    async def test_alternatives_are_stored_as_plan(self, env):
        """Generated alternatives should be stored in the task_plans table."""
        pm = env["planning_manager"]
        board = env["board"]

        task = await _create_task(board, "Stored plan test")
        await pm.generate_alternatives(task["id"])

        plans = await pm.get_plans(task["id"], plan_type="alternatives")
        assert len(plans) >= 1
        assert plans[0]["plan_type"] == "alternatives"
