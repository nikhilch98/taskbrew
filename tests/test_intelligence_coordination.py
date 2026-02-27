"""Tests for the CoordinationManager."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from taskbrew.orchestrator.database import Database
from taskbrew.intelligence.coordination import CoordinationManager


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture
async def db():
    """Create and initialise an in-memory database."""
    database = Database(":memory:")
    await database.initialize()

    # Module-specific tables
    await database.executescript("""
        CREATE TABLE IF NOT EXISTS standup_reports (
            id TEXT PRIMARY KEY,
            agent_id TEXT NOT NULL,
            report_type TEXT NOT NULL,
            completed_tasks TEXT,
            in_progress_tasks TEXT,
            blockers TEXT,
            plan TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS file_locks (
            id TEXT PRIMARY KEY,
            file_path TEXT NOT NULL UNIQUE,
            locked_by TEXT NOT NULL,
            task_id TEXT,
            locked_at TEXT NOT NULL,
            expires_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS knowledge_digests (
            id TEXT PRIMARY KEY,
            digest_type TEXT NOT NULL,
            content TEXT NOT NULL,
            target_roles TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS mentor_pairs (
            id TEXT PRIMARY KEY,
            mentor_role TEXT NOT NULL,
            mentee_role TEXT NOT NULL,
            skill_area TEXT NOT NULL,
            status TEXT DEFAULT 'active',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS consensus_votes (
            id TEXT PRIMARY KEY,
            proposal_id TEXT NOT NULL,
            voter_id TEXT NOT NULL,
            vote TEXT NOT NULL,
            reasoning TEXT,
            created_at TEXT NOT NULL,
            UNIQUE(proposal_id, voter_id)
        );

        CREATE TABLE IF NOT EXISTS progress_heartbeats (
            id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            agent_id TEXT NOT NULL,
            progress_pct REAL NOT NULL,
            status_message TEXT,
            created_at TEXT NOT NULL
        );
    """)

    yield database
    await database.close()


@pytest.fixture
async def coord(db: Database) -> CoordinationManager:
    """Create a CoordinationManager backed by the in-memory database."""
    return CoordinationManager(db)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


async def _seed_task(
    db: Database,
    status: str = "pending",
    assigned_to: str = "coder",
    claimed_by: str | None = None,
    completed_at: str | None = None,
) -> str:
    """Insert a minimal group + task for testing."""
    now = datetime.now(timezone.utc).isoformat()
    group_id = f"GRP-{uuid.uuid4().hex[:4]}"
    await db.execute(
        "INSERT INTO groups (id, title, origin, status, created_at) VALUES (?, 'Test', 'test', 'active', ?)",
        (group_id, now),
    )
    task_id = f"TSK-{uuid.uuid4().hex[:4]}"
    await db.execute(
        "INSERT INTO tasks (id, group_id, title, task_type, priority, assigned_to, claimed_by, "
        "status, created_by, created_at, completed_at) "
        "VALUES (?, ?, 'Test Task', 'implementation', 'medium', ?, ?, ?, 'test', ?, ?)",
        (task_id, group_id, assigned_to, claimed_by, status, now, completed_at),
    )
    return task_id


# ------------------------------------------------------------------
# Feature 20: Agent Standup Reports
# ------------------------------------------------------------------


async def test_generate_standup(coord: CoordinationManager, db: Database):
    """generate_standup builds report from recent tasks."""
    now_iso = datetime.now(timezone.utc).isoformat()
    await _seed_task(db, status="completed", claimed_by="agent-1", completed_at=now_iso)
    await _seed_task(db, status="in_progress", claimed_by="agent-1")
    await _seed_task(db, status="blocked", claimed_by="agent-1")

    report = await coord.generate_standup("agent-1")

    assert report["agent_id"] == "agent-1"
    assert report["report_type"] == "daily"
    assert len(report["completed_tasks"]) >= 1
    assert len(report["in_progress_tasks"]) >= 1
    assert len(report["blockers"]) >= 1
    assert "plan" in report


async def test_generate_standup_empty(coord: CoordinationManager, db: Database):
    """generate_standup with no tasks produces empty lists."""
    report = await coord.generate_standup("agent-no-tasks")

    assert report["agent_id"] == "agent-no-tasks"
    assert report["completed_tasks"] == []
    assert report["in_progress_tasks"] == []
    assert report["blockers"] == []


async def test_get_standups(coord: CoordinationManager, db: Database):
    """get_standups retrieves stored reports."""
    now_iso = datetime.now(timezone.utc).isoformat()
    await _seed_task(db, status="completed", claimed_by="agent-1", completed_at=now_iso)

    await coord.generate_standup("agent-1")
    await coord.generate_standup("agent-1")

    reports = await coord.get_standups(agent_id="agent-1")
    assert len(reports) == 2
    # JSON fields should be parsed
    assert isinstance(reports[0]["completed_tasks"], list)


# ------------------------------------------------------------------
# Feature 21: File Conflict Detection
# ------------------------------------------------------------------


async def test_acquire_lock(coord: CoordinationManager):
    """acquire_lock succeeds on first call for a file."""
    result = await coord.acquire_lock("src/main.py", "agent-1", task_id="TSK-001")
    assert result["conflict"] is False
    assert result["locked_by"] == "agent-1"
    assert result["file_path"] == "src/main.py"
    assert "expires_at" in result


async def test_acquire_lock_conflict(coord: CoordinationManager):
    """acquire_lock returns conflict when file already locked."""
    await coord.acquire_lock("src/main.py", "agent-1")
    result = await coord.acquire_lock("src/main.py", "agent-2")
    assert result["conflict"] is True
    assert result["locked_by"] == "agent-1"


async def test_release_lock(coord: CoordinationManager):
    """release_lock frees the file for a new lock."""
    await coord.acquire_lock("src/utils.py", "agent-1")
    result = await coord.release_lock("src/utils.py", "agent-1")
    assert result["released"] is True

    # Now another agent can lock it
    result2 = await coord.acquire_lock("src/utils.py", "agent-2")
    assert result2["conflict"] is False


async def test_release_lock_not_found(coord: CoordinationManager):
    """release_lock returns not_found for non-existent lock."""
    result = await coord.release_lock("nonexistent.py", "agent-1")
    assert result["released"] is False
    assert result["reason"] == "not_found"


# ------------------------------------------------------------------
# Feature 22: Knowledge Sharing Digest
# ------------------------------------------------------------------


async def test_create_and_get_digest(coord: CoordinationManager):
    """create_digest stores and get_digests retrieves."""
    await coord.create_digest("daily", "Summary of today's work", target_roles=["coder", "reviewer"])
    await coord.create_digest("weekly", "Weekly overview", target_roles=None)

    digests = await coord.get_digests()
    assert len(digests) == 2

    # Filter by role
    coder_digests = await coord.get_digests(role="coder")
    # Should include the targeted one and the untargeted one
    assert len(coder_digests) == 2

    # A role not in target_roles gets only untargeted digests
    pm_digests = await coord.get_digests(role="pm")
    assert len(pm_digests) >= 1  # At least the NULL target_roles one


# ------------------------------------------------------------------
# Feature 23: Mentor-Mentee Pairing
# ------------------------------------------------------------------


async def test_create_and_get_pairs(coord: CoordinationManager):
    """create_pair and get_pairs work correctly."""
    p1 = await coord.create_pair("architect", "coder", "system_design")
    assert p1["mentor_role"] == "architect"
    assert p1["mentee_role"] == "coder"
    assert p1["status"] == "active"

    await coord.create_pair("reviewer", "coder", "code_quality")

    # Filter by role
    coder_pairs = await coord.get_pairs(role="coder")
    assert len(coder_pairs) == 2  # coder is mentee in both

    architect_pairs = await coord.get_pairs(role="architect")
    assert len(architect_pairs) == 1


# ------------------------------------------------------------------
# Feature 24: Multi-Agent Consensus Protocol
# ------------------------------------------------------------------


async def test_create_proposal(coord: CoordinationManager):
    """create_proposal returns a lightweight proposal dict."""
    proposal = await coord.create_proposal("PROP-001", "Use microservices architecture")
    assert proposal["proposal_id"] == "PROP-001"
    assert proposal["description"] == "Use microservices architecture"
    assert proposal["status"] == "open"


async def test_cast_vote_and_tally_approved(coord: CoordinationManager):
    """tally_votes returns approved when majority approves."""
    await coord.cast_vote("PROP-001", "agent-1", "approve", reasoning="Good idea")
    await coord.cast_vote("PROP-001", "agent-2", "approve")
    await coord.cast_vote("PROP-001", "agent-3", "reject", reasoning="Too complex")

    tally = await coord.tally_votes("PROP-001")
    assert tally["approve"] == 2
    assert tally["reject"] == 1
    assert tally["abstain"] == 0
    assert tally["total"] == 3
    assert tally["result"] == "approved"


async def test_tally_votes_rejected(coord: CoordinationManager):
    """tally_votes returns rejected when majority does not approve."""
    await coord.cast_vote("PROP-002", "agent-1", "reject")
    await coord.cast_vote("PROP-002", "agent-2", "abstain")

    tally = await coord.tally_votes("PROP-002")
    assert tally["result"] == "rejected"


async def test_tally_votes_no_quorum(coord: CoordinationManager):
    """tally_votes returns no_quorum with zero votes."""
    tally = await coord.tally_votes("PROP-999")
    assert tally["result"] == "no_quorum"


# ------------------------------------------------------------------
# Feature 25: Work Stealing
# ------------------------------------------------------------------


async def test_find_stealable_tasks(coord: CoordinationManager, db: Database):
    """find_stealable_tasks returns tasks from overloaded agents."""
    # Create 4+ pending tasks assigned to 'coder' to make them stealable
    for _ in range(4):
        await _seed_task(db, status="pending", assigned_to="coder")

    stealable = await coord.find_stealable_tasks("reviewer")
    assert len(stealable) >= 4
    assert all(t["assigned_to"] == "coder" for t in stealable)


async def test_steal_task(coord: CoordinationManager, db: Database):
    """steal_task claims a pending task for the requesting agent."""
    task_id = await _seed_task(db, status="pending", assigned_to="coder")

    result = await coord.steal_task(task_id, "reviewer")
    assert result["success"] is True
    assert result["claimed_by"] == "reviewer"

    # Verify in DB
    task = await db.execute_fetchone("SELECT claimed_by FROM tasks WHERE id = ?", (task_id,))
    assert task["claimed_by"] == "reviewer"


async def test_steal_task_not_pending(coord: CoordinationManager, db: Database):
    """steal_task fails for non-pending tasks."""
    task_id = await _seed_task(db, status="in_progress", assigned_to="coder")

    result = await coord.steal_task(task_id, "reviewer")
    assert result["success"] is False


# ------------------------------------------------------------------
# Feature 26: Progress Heartbeats
# ------------------------------------------------------------------


async def test_record_and_get_heartbeats(coord: CoordinationManager, db: Database):
    """record_heartbeat stores and get_heartbeats retrieves in order."""
    task_id = await _seed_task(db, status="in_progress", claimed_by="agent-1")

    await coord.record_heartbeat(task_id, "agent-1", 25.0, "Parsing input files")
    await coord.record_heartbeat(task_id, "agent-1", 50.0, "Generating output")
    await coord.record_heartbeat(task_id, "agent-1", 75.0, "Running tests")

    heartbeats = await coord.get_heartbeats(task_id)
    assert len(heartbeats) == 3
    # Most recent first
    assert heartbeats[0]["progress_pct"] == 75.0
    assert heartbeats[2]["progress_pct"] == 25.0
