"""Tests for the CollaborationManager."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from taskbrew.orchestrator.database import Database
from taskbrew.intelligence.collaboration import CollaborationManager


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture
async def db():
    """Create and initialise an in-memory database."""
    database = Database(":memory:")
    await database.initialize()
    yield database
    await database.close()


@pytest.fixture
async def collab(db: Database) -> CollaborationManager:
    """Create a CollaborationManager backed by the in-memory database."""
    return CollaborationManager(db)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


async def _create_test_group_and_task(
    db: Database,
    title: str = "Test Task",
    description: str = "Test description",
    status: str = "completed",
) -> tuple[str, str]:
    """Helper to create a group and task for testing."""
    now = datetime.now(timezone.utc).isoformat()
    group_id = f"GRP-{uuid.uuid4().hex[:4]}"
    await db.execute(
        "INSERT INTO groups (id, title, origin, status, created_at) VALUES (?, ?, 'test', 'active', ?)",
        (group_id, "Test Group", now),
    )
    task_id = f"TSK-{uuid.uuid4().hex[:4]}"
    await db.execute(
        "INSERT INTO tasks (id, group_id, title, description, task_type, priority, "
        "assigned_to, status, created_by, created_at) "
        "VALUES (?, ?, ?, ?, 'implementation', 'medium', 'coder', ?, 'test', ?)",
        (task_id, group_id, title, description, status, now),
    )
    return group_id, task_id


# ------------------------------------------------------------------
# Tests: Feature 11 – Peer Review
# ------------------------------------------------------------------


async def test_request_peer_review(collab: CollaborationManager, db: Database):
    """request_peer_review creates a review task linked to the original."""
    group_id, task_id = await _create_test_group_and_task(db)

    result = await collab.request_peer_review(task_id, reviewer_role="reviewer")

    assert "review_task_id" in result
    assert result["original_task_id"] == task_id
    assert result["reviewer_role"] == "reviewer"

    # Verify review task exists in the DB
    review = await db.execute_fetchone(
        "SELECT * FROM tasks WHERE id = ?", (result["review_task_id"],)
    )
    assert review is not None
    assert review["parent_id"] == task_id
    assert review["task_type"] == "code_review"
    assert review["assigned_to"] == "reviewer"
    assert review["status"] == "pending"
    assert review["group_id"] == group_id
    assert task_id in review["description"]


async def test_request_peer_review_not_found(collab: CollaborationManager):
    """request_peer_review returns error for nonexistent task."""
    result = await collab.request_peer_review("NONEXISTENT-123")
    assert result.get("error") == "Task not found"


# ------------------------------------------------------------------
# Tests: Feature 12 – Pair Programming
# ------------------------------------------------------------------


async def test_start_pair_session(collab: CollaborationManager, db: Database):
    """start_pair_session creates a pair session thread with messages."""
    group_id, task_id = await _create_test_group_and_task(db)

    result = await collab.start_pair_session(task_id, "coder-1", "coder-2")

    assert result["task_id"] == task_id
    assert result["agent1"] == "coder-1"
    assert result["agent2"] == "coder-2"
    assert result["status"] == "active"
    assert result["thread_id"].startswith("pair-")

    # Verify two messages were created in the DB
    messages = await db.execute_fetchall(
        "SELECT * FROM agent_messages WHERE thread_id = ?",
        (result["thread_id"],),
    )
    assert len(messages) == 2
    assert messages[0]["message_type"] == "pair_session"

    # Both agents should have sent a message to each other
    senders = {m["from_agent"] for m in messages}
    receivers = {m["to_agent"] for m in messages}
    assert senders == {"coder-1", "coder-2"}
    assert receivers == {"coder-1", "coder-2"}


async def test_get_pair_context(collab: CollaborationManager, db: Database):
    """get_pair_context retrieves formatted pair context for an agent."""
    group_id, task_id = await _create_test_group_and_task(db)

    session = await collab.start_pair_session(task_id, "coder-1", "coder-2")
    thread_id = session["thread_id"]

    # Add an extra message to the thread
    await db.execute(
        "INSERT INTO agent_messages (from_agent, to_agent, content, message_type, thread_id, created_at) "
        "VALUES (?, ?, ?, 'pair_session', ?, ?)",
        ("coder-1", "coder-2", "I suggest we use a factory pattern here.",
         thread_id, datetime.now(timezone.utc).isoformat()),
    )

    context = await collab.get_pair_context(thread_id, for_agent="coder-2")

    assert "## Pair Session" in context
    assert "Partner (coder-1)" in context
    assert "You" in context  # coder-2's own messages should be labeled "You"


async def test_get_pair_context_empty(collab: CollaborationManager):
    """get_pair_context returns empty string for unknown thread."""
    context = await collab.get_pair_context("nonexistent-thread", for_agent="coder-1")
    assert context == ""


# ------------------------------------------------------------------
# Tests: Feature 13 – Handoff Summaries
# ------------------------------------------------------------------


async def test_generate_handoff_summary(collab: CollaborationManager, db: Database):
    """generate_handoff_summary creates and stores a summary."""
    group_id, task_id = await _create_test_group_and_task(db)
    output = (
        "Implemented the login module.\n"
        "Modified src/auth/login.py to add token validation.\n"
        "Updated config/settings.yaml with new auth parameters."
    )

    summary = await collab.generate_handoff_summary(task_id, output)

    assert f"## Handoff Summary for {task_id}" in summary
    assert "**Output preview**" in summary
    assert "**Files referenced**" in summary
    assert "login.py" in summary

    # Verify stored in DB
    task = await db.execute_fetchone("SELECT output_text FROM tasks WHERE id = ?", (task_id,))
    assert task["output_text"] == summary


async def test_generate_handoff_summary_empty_output(collab: CollaborationManager, db: Database):
    """generate_handoff_summary handles empty output gracefully."""
    group_id, task_id = await _create_test_group_and_task(db)

    summary = await collab.generate_handoff_summary(task_id, "")

    assert f"## Handoff Summary for {task_id}" in summary
    assert "**Output preview**" not in summary  # No output to preview


async def test_get_handoff_context(collab: CollaborationManager, db: Database):
    """get_handoff_context returns parent task output."""
    group_id, parent_id = await _create_test_group_and_task(
        db, title="Parent Task", description="Parent description"
    )

    # Store output on parent
    await db.execute(
        "UPDATE tasks SET output_text = ? WHERE id = ?",
        ("Parent produced output X", parent_id),
    )

    # Create a child task
    now = datetime.now(timezone.utc).isoformat()
    child_id = f"TSK-{uuid.uuid4().hex[:4]}"
    await db.execute(
        "INSERT INTO tasks (id, group_id, parent_id, title, description, task_type, "
        "priority, assigned_to, status, created_by, created_at) "
        "VALUES (?, ?, ?, 'Child Task', 'Child desc', 'implementation', 'medium', 'coder', 'pending', 'test', ?)",
        (child_id, group_id, parent_id, now),
    )

    context = await collab.get_handoff_context(child_id)

    assert "Previous Task Output" in context
    assert parent_id in context
    assert "Parent Task" in context
    assert "Parent produced output X" in context


async def test_get_handoff_context_no_parent(collab: CollaborationManager, db: Database):
    """get_handoff_context returns empty string for tasks with no parent."""
    group_id, task_id = await _create_test_group_and_task(db)
    context = await collab.get_handoff_context(task_id)
    assert context == ""


# ------------------------------------------------------------------
# Tests: Feature 14 – Debate Protocol
# ------------------------------------------------------------------


async def test_start_debate(collab: CollaborationManager, db: Database):
    """start_debate creates debater A, B, and judge tasks with dependencies."""
    group_id, task_id = await _create_test_group_and_task(
        db, title="Design API Layer", description="Design the REST API layer"
    )

    result = await collab.start_debate(task_id, debater_role="coder", judge_role="architect")

    assert result["original_task_id"] == task_id
    assert result["status"] == "started"
    assert result["debate_a_id"].startswith("DEB-A-")
    assert result["debate_b_id"].startswith("DEB-B-")
    assert result["judge_id"].startswith("DEB-J-")

    # Verify debater tasks
    debate_a = await db.execute_fetchone(
        "SELECT * FROM tasks WHERE id = ?", (result["debate_a_id"],)
    )
    assert debate_a is not None
    assert debate_a["task_type"] == "tech_design"
    assert debate_a["assigned_to"] == "coder"
    assert debate_a["status"] == "pending"
    assert debate_a["parent_id"] == task_id
    assert "Approach A" in debate_a["description"]

    debate_b = await db.execute_fetchone(
        "SELECT * FROM tasks WHERE id = ?", (result["debate_b_id"],)
    )
    assert debate_b is not None
    assert debate_b["task_type"] == "tech_design"
    assert "Approach B" in debate_b["description"]

    # Verify judge task
    judge = await db.execute_fetchone(
        "SELECT * FROM tasks WHERE id = ?", (result["judge_id"],)
    )
    assert judge is not None
    assert judge["task_type"] == "architecture_review"
    assert judge["assigned_to"] == "architect"
    assert judge["status"] == "blocked"

    # Verify dependencies: judge blocked by both debaters
    deps = await db.execute_fetchall(
        "SELECT * FROM task_dependencies WHERE task_id = ?",
        (result["judge_id"],),
    )
    assert len(deps) == 2
    blocker_ids = {d["blocked_by"] for d in deps}
    assert result["debate_a_id"] in blocker_ids
    assert result["debate_b_id"] in blocker_ids


async def test_start_debate_not_found(collab: CollaborationManager):
    """start_debate returns error for nonexistent task."""
    result = await collab.start_debate("NONEXISTENT-123")
    assert result.get("error") == "Task not found"


async def test_get_active_collaborations(collab: CollaborationManager, db: Database):
    """get_active_collaborations returns pair sessions and debates."""
    group_id, task_id = await _create_test_group_and_task(db)

    # Create a pair session
    await collab.start_pair_session(task_id, "coder-1", "coder-2")
    # Create a debate
    await collab.start_debate(task_id)

    active = await collab.get_active_collaborations()

    assert "pair_sessions" in active
    assert "active_debates" in active
    assert len(active["pair_sessions"]) >= 1
    assert len(active["active_debates"]) >= 1


async def test_pair_session_atomicity(collab: CollaborationManager, db: Database):
    """Regression: start_pair_session wraps inserts in a transaction.

    Verify both messages are inserted atomically (both present after success).
    """
    group_id, task_id = await _create_test_group_and_task(db)

    result = await collab.start_pair_session(task_id, "agent-a", "agent-b")

    messages = await db.execute_fetchall(
        "SELECT * FROM agent_messages WHERE thread_id = ? ORDER BY from_agent",
        (result["thread_id"],),
    )
    # Both messages must be present (atomicity)
    assert len(messages) == 2
    from_agents = [m["from_agent"] for m in messages]
    assert "agent-a" in from_agents
    assert "agent-b" in from_agents


async def test_debate_atomicity(collab: CollaborationManager, db: Database):
    """Regression: start_debate wraps all inserts in a transaction.

    Verify all 3 tasks and 2 dependencies are inserted atomically.
    """
    group_id, task_id = await _create_test_group_and_task(
        db, title="Design System", description="Design the system architecture"
    )

    result = await collab.start_debate(task_id)

    # All 3 debate tasks must exist
    for tid in [result["debate_a_id"], result["debate_b_id"], result["judge_id"]]:
        task = await db.execute_fetchone("SELECT * FROM tasks WHERE id = ?", (tid,))
        assert task is not None, f"Task {tid} should exist after atomic insert"

    # Both dependencies must exist
    deps = await db.execute_fetchall(
        "SELECT * FROM task_dependencies WHERE task_id = ?",
        (result["judge_id"],),
    )
    assert len(deps) == 2
