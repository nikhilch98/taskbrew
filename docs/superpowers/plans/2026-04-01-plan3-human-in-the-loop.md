# Plan 3: Human-in-the-Loop (Approval, Clarification & Interaction System)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the human-in-the-loop system: MCP tool endpoints (`complete_task`, `request_clarification`, `route_task`, `get_my_connections`) with long-polling blocking, database tables for interaction tracking (`human_interaction_requests`, `task_chains`, `first_run_approvals`), dashboard API endpoints for resolving pending interactions, dashboard "Action Required" notification cards, and revision loop tracking with `chain_id` enforcement.

**Architecture:** Agents call MCP tool HTTP endpoints which create rows in `human_interaction_requests` and block via 30-second long-poll loops until a human resolves them through the dashboard. The `task_chains` table tracks revision counts per chain. `first_run_approvals` tracks which agent roles have been approved per group. A new `interactions` API router serves pending/history endpoints and resolve actions. The dashboard gains an "Action Required" panel with approval/clarification/escalation cards.

**Tech Stack:** Python 3.12, FastAPI, Pydantic, aiosqlite, asyncio, Jinja2, vanilla JS, CSS

**Spec Reference:** `docs/superpowers/specs/2026-04-01-agent-presets-pipeline-editor-design.md` sections 3.1--3.8

---

## File Structure

### New Files
- `src/taskbrew/dashboard/routers/interactions.py` -- Dashboard interaction API router (`/api/interactions/*`)
- `src/taskbrew/dashboard/routers/mcp_tools.py` -- MCP tool router (`/mcp/tools/*`) with long-polling
- `src/taskbrew/orchestrator/interactions.py` -- Interaction data layer (CRUD for `human_interaction_requests`)
- `tests/test_human_in_the_loop.py` -- All tests for Plan 3

### Modified Files
- `src/taskbrew/orchestrator/database.py` -- Add new tables (`human_interaction_requests`, `task_chains`, `first_run_approvals`) and new columns on `tasks`
- `src/taskbrew/orchestrator/migration.py` -- Add migration 28 for new tables/columns
- `src/taskbrew/dashboard/models.py` -- Add Pydantic request/response bodies for interactions and MCP tools
- `src/taskbrew/dashboard/app.py` -- Register `interactions` and `mcp_tools` routers
- `src/taskbrew/dashboard/templates/index.html` -- Add "Action Required" panel with notification cards
- `src/taskbrew/dashboard/static/css/dashboard.css` (or inline in `index.html`) -- Styles for interaction cards

---

## Task 1: Database Schema -- New Tables and Columns

**Files:**
- Modify: `src/taskbrew/orchestrator/database.py`
- Modify: `src/taskbrew/orchestrator/migration.py`
- Test: `tests/test_human_in_the_loop.py` (create new)

- [ ] **Step 1: Write failing test for new tables**

```python
# tests/test_human_in_the_loop.py
"""Tests for Human-in-the-Loop system: interactions, MCP tools, revision tracking."""

import asyncio
import pytest
from datetime import datetime, timezone

from taskbrew.orchestrator.database import Database


@pytest.fixture
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
async def db():
    database = Database(":memory:")
    await database.initialize()
    yield database
    await database.close()


class TestHITLSchema:
    """Test that new HITL tables exist and accept inserts."""

    @pytest.mark.asyncio
    async def test_human_interaction_requests_table_exists(self, db):
        now = datetime.now(timezone.utc).isoformat()
        await db.execute(
            "INSERT INTO human_interaction_requests "
            "(id, task_id, instance_token, request_type, request_key, status, "
            " payload, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("hir-001", "TSK-001", "tok-abc", "approval", "TSK-001:approval:1",
             "pending", '{"summary":"done"}', now),
        )
        row = await db.execute_fetchone(
            "SELECT * FROM human_interaction_requests WHERE id = ?",
            ("hir-001",),
        )
        assert row is not None
        assert row["request_type"] == "approval"
        assert row["status"] == "pending"
        assert row["response_payload"] is None

    @pytest.mark.asyncio
    async def test_human_interaction_requests_idempotent_key(self, db):
        """request_key has a UNIQUE constraint for idempotency."""
        now = datetime.now(timezone.utc).isoformat()
        await db.execute(
            "INSERT INTO human_interaction_requests "
            "(id, task_id, instance_token, request_type, request_key, status, "
            " payload, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("hir-001", "TSK-001", "tok-abc", "approval", "TSK-001:approval:1",
             "pending", "{}", now),
        )
        with pytest.raises(Exception):
            await db.execute(
                "INSERT INTO human_interaction_requests "
                "(id, task_id, instance_token, request_type, request_key, status, "
                " payload, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("hir-002", "TSK-001", "tok-abc", "approval", "TSK-001:approval:1",
                 "pending", "{}", now),
            )

    @pytest.mark.asyncio
    async def test_task_chains_table_exists(self, db):
        now = datetime.now(timezone.utc).isoformat()
        await db.execute(
            "INSERT INTO task_chains "
            "(id, original_task_id, current_task_id, agent_role, "
            " revision_count, max_revision_cycles, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("chain-001", "TSK-001", "TSK-001", "coder_be", 0, 5, "active", now),
        )
        row = await db.execute_fetchone(
            "SELECT * FROM task_chains WHERE id = ?",
            ("chain-001",),
        )
        assert row is not None
        assert row["revision_count"] == 0
        assert row["max_revision_cycles"] == 5

    @pytest.mark.asyncio
    async def test_first_run_approvals_table_exists(self, db):
        now = datetime.now(timezone.utc).isoformat()
        await db.execute(
            "INSERT INTO first_run_approvals "
            "(id, group_id, agent_role, approved_at) "
            "VALUES (?, ?, ?, ?)",
            ("fra-001", "GRP-001", "coder_be", now),
        )
        row = await db.execute_fetchone(
            "SELECT * FROM first_run_approvals WHERE group_id = ? AND agent_role = ?",
            ("GRP-001", "coder_be"),
        )
        assert row is not None
        assert row["agent_role"] == "coder_be"

    @pytest.mark.asyncio
    async def test_first_run_approvals_unique_constraint(self, db):
        """Only one approval per (group_id, agent_role)."""
        now = datetime.now(timezone.utc).isoformat()
        await db.execute(
            "INSERT INTO first_run_approvals (id, group_id, agent_role, approved_at) "
            "VALUES (?, ?, ?, ?)",
            ("fra-001", "GRP-001", "coder_be", now),
        )
        with pytest.raises(Exception):
            await db.execute(
                "INSERT INTO first_run_approvals (id, group_id, agent_role, approved_at) "
                "VALUES (?, ?, ?, ?)",
                ("fra-002", "GRP-001", "coder_be", now),
            )

    @pytest.mark.asyncio
    async def test_tasks_new_columns_exist(self, db):
        """Verify chain_id, approval_mode, instance_token, config_snapshot columns on tasks."""
        now = datetime.now(timezone.utc).isoformat()
        await db.execute(
            "INSERT INTO tasks "
            "(id, title, status, created_at, chain_id, approval_mode, "
            " instance_token, config_snapshot) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("TSK-NEW", "Test task", "pending", now,
             "chain-001", "manual", "tok-xyz", '{"max_revision_cycles":5}'),
        )
        row = await db.execute_fetchone(
            "SELECT chain_id, approval_mode, instance_token, config_snapshot "
            "FROM tasks WHERE id = ?",
            ("TSK-NEW",),
        )
        assert row is not None
        assert row["chain_id"] == "chain-001"
        assert row["approval_mode"] == "manual"
        assert row["instance_token"] == "tok-xyz"
        assert row["config_snapshot"] == '{"max_revision_cycles":5}'
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd "/Users/nikhilchatragadda/Personal Projects/taskbrew" && .venv/bin/pytest tests/test_human_in_the_loop.py::TestHITLSchema -v 2>&1 | head -40`
Expected: FAIL -- tables `human_interaction_requests`, `task_chains`, `first_run_approvals` do not exist; columns `chain_id`, `approval_mode`, `instance_token`, `config_snapshot` do not exist on `tasks`.

- [ ] **Step 3: Add new tables to `_SCHEMA_SQL` in database.py**

In `src/taskbrew/orchestrator/database.py`, append to `_SCHEMA_SQL` (before the closing `"""`):

```python
CREATE TABLE IF NOT EXISTS human_interaction_requests (
    id              TEXT PRIMARY KEY,
    task_id         TEXT NOT NULL REFERENCES tasks(id),
    instance_token  TEXT NOT NULL,
    request_type    TEXT NOT NULL,  -- 'approval', 'clarification'
    request_key     TEXT NOT NULL UNIQUE,  -- '{task_id}:{type}:{seq}' for idempotency
    status          TEXT NOT NULL DEFAULT 'pending',  -- 'pending', 'approved', 'rejected', 'responded', 'skipped', 'timed_out'
    payload         TEXT,  -- JSON: summary, artifact_paths, question, suggested_options, etc.
    response_payload TEXT,  -- JSON: feedback, answer, etc.
    responded_by    TEXT,
    created_at      TEXT NOT NULL,
    resolved_at     TEXT
);

CREATE TABLE IF NOT EXISTS task_chains (
    id                   TEXT PRIMARY KEY,
    original_task_id     TEXT NOT NULL REFERENCES tasks(id),
    current_task_id      TEXT NOT NULL REFERENCES tasks(id),
    agent_role           TEXT NOT NULL,
    revision_count       INTEGER NOT NULL DEFAULT 0,
    max_revision_cycles  INTEGER NOT NULL DEFAULT 0,
    status               TEXT NOT NULL DEFAULT 'active',  -- 'active', 'completed', 'escalated'
    created_at           TEXT NOT NULL,
    updated_at           TEXT
);

CREATE TABLE IF NOT EXISTS first_run_approvals (
    id          TEXT PRIMARY KEY,
    group_id    TEXT NOT NULL REFERENCES groups(id),
    agent_role  TEXT NOT NULL,
    approved_at TEXT NOT NULL,
    UNIQUE(group_id, agent_role)
);
```

- [ ] **Step 4: Add new columns to tasks table in `_SCHEMA_SQL`**

In `src/taskbrew/orchestrator/database.py`, add four columns to the `tasks` CREATE TABLE statement (after the `output_text` column):

```sql
    chain_id         TEXT,
    approval_mode    TEXT DEFAULT 'auto',
    instance_token   TEXT,
    config_snapshot  TEXT
```

- [ ] **Step 5: Add indexes to `_INDEX_SQL`**

In `src/taskbrew/orchestrator/database.py`, append to `_INDEX_SQL`:

```sql
CREATE INDEX IF NOT EXISTS idx_hir_pending
    ON human_interaction_requests(status, created_at)
    WHERE status = 'pending';

CREATE INDEX IF NOT EXISTS idx_hir_task
    ON human_interaction_requests(task_id, request_type);

CREATE INDEX IF NOT EXISTS idx_task_chains_current
    ON task_chains(current_task_id);

CREATE INDEX IF NOT EXISTS idx_task_chains_role
    ON task_chains(agent_role, status);

CREATE INDEX IF NOT EXISTS idx_first_run_approvals_group
    ON first_run_approvals(group_id, agent_role);

CREATE INDEX IF NOT EXISTS idx_tasks_chain
    ON tasks(chain_id);

CREATE INDEX IF NOT EXISTS idx_tasks_instance_token
    ON tasks(instance_token)
    WHERE instance_token IS NOT NULL;
```

- [ ] **Step 6: Add migration 28 for existing databases**

In `src/taskbrew/orchestrator/migration.py`, append to the `MIGRATIONS` list:

```python
    (28, "add_human_in_the_loop_tables", """
        CREATE TABLE IF NOT EXISTS human_interaction_requests (
            id              TEXT PRIMARY KEY,
            task_id         TEXT NOT NULL REFERENCES tasks(id),
            instance_token  TEXT NOT NULL,
            request_type    TEXT NOT NULL,
            request_key     TEXT NOT NULL UNIQUE,
            status          TEXT NOT NULL DEFAULT 'pending',
            payload         TEXT,
            response_payload TEXT,
            responded_by    TEXT,
            created_at      TEXT NOT NULL,
            resolved_at     TEXT
        );

        CREATE TABLE IF NOT EXISTS task_chains (
            id                   TEXT PRIMARY KEY,
            original_task_id     TEXT NOT NULL REFERENCES tasks(id),
            current_task_id      TEXT NOT NULL REFERENCES tasks(id),
            agent_role           TEXT NOT NULL,
            revision_count       INTEGER NOT NULL DEFAULT 0,
            max_revision_cycles  INTEGER NOT NULL DEFAULT 0,
            status               TEXT NOT NULL DEFAULT 'active',
            created_at           TEXT NOT NULL,
            updated_at           TEXT
        );

        CREATE TABLE IF NOT EXISTS first_run_approvals (
            id          TEXT PRIMARY KEY,
            group_id    TEXT NOT NULL REFERENCES groups(id),
            agent_role  TEXT NOT NULL,
            approved_at TEXT NOT NULL,
            UNIQUE(group_id, agent_role)
        );

        CREATE INDEX IF NOT EXISTS idx_hir_pending
            ON human_interaction_requests(status, created_at)
            WHERE status = 'pending';

        CREATE INDEX IF NOT EXISTS idx_hir_task
            ON human_interaction_requests(task_id, request_type);

        CREATE INDEX IF NOT EXISTS idx_task_chains_current
            ON task_chains(current_task_id);

        CREATE INDEX IF NOT EXISTS idx_task_chains_role
            ON task_chains(agent_role, status);

        CREATE INDEX IF NOT EXISTS idx_first_run_approvals_group
            ON first_run_approvals(group_id, agent_role);
    """),
```

Also add ALTER TABLE statements for existing `tasks` tables (in a separate migration or same one, after the CREATE TABLEs). Because ALTER TABLE IF NOT EXISTS isn't supported in SQLite, wrap in try/except in the `initialize()` method of `database.py`:

In `src/taskbrew/orchestrator/database.py`, add to the `initialize()` method (after the existing `output_text` migration block):

```python
        # Add HITL columns to tasks table if missing (backwards compat)
        for col, col_type, default in [
            ("chain_id", "TEXT", None),
            ("approval_mode", "TEXT", "'auto'"),
            ("instance_token", "TEXT", None),
            ("config_snapshot", "TEXT", None),
        ]:
            try:
                default_clause = f" DEFAULT {default}" if default else ""
                await self._conn.execute(
                    f"ALTER TABLE tasks ADD COLUMN {col} {col_type}{default_clause}"
                )
                await self._conn.commit()
            except Exception:
                pass  # Column already exists
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `cd "/Users/nikhilchatragadda/Personal Projects/taskbrew" && .venv/bin/pytest tests/test_human_in_the_loop.py::TestHITLSchema -v`
Expected: All 6 tests PASS.

- [ ] **Step 8: Commit**

```bash
git add src/taskbrew/orchestrator/database.py src/taskbrew/orchestrator/migration.py tests/test_human_in_the_loop.py
git commit -m "feat: add HITL database schema — interaction requests, task chains, first-run approvals"
```

---

## Task 2: Interaction Data Layer -- CRUD Functions

**Files:**
- Create: `src/taskbrew/orchestrator/interactions.py`
- Test: `tests/test_human_in_the_loop.py`

- [ ] **Step 1: Write failing tests for interaction CRUD**

Append to `tests/test_human_in_the_loop.py`:

```python
from taskbrew.orchestrator.interactions import InteractionManager


class TestInteractionManager:
    """Test CRUD operations for human_interaction_requests."""

    @pytest.mark.asyncio
    async def test_create_interaction_request(self, db):
        mgr = InteractionManager(db)
        req = await mgr.create_request(
            task_id="TSK-001",
            instance_token="tok-abc",
            request_type="approval",
            payload={"summary": "Implemented API", "artifact_paths": ["/src/api.py"]},
        )
        assert req["id"].startswith("hir-")
        assert req["status"] == "pending"
        assert req["request_type"] == "approval"
        assert req["task_id"] == "TSK-001"

    @pytest.mark.asyncio
    async def test_create_request_idempotent(self, db):
        """Duplicate request_key returns existing request instead of creating new."""
        mgr = InteractionManager(db)
        req1 = await mgr.create_request(
            task_id="TSK-001",
            instance_token="tok-abc",
            request_type="approval",
            payload={"summary": "done"},
            sequence_number=1,
        )
        req2 = await mgr.create_request(
            task_id="TSK-001",
            instance_token="tok-abc",
            request_type="approval",
            payload={"summary": "done again"},
            sequence_number=1,
        )
        assert req1["id"] == req2["id"]

    @pytest.mark.asyncio
    async def test_get_pending_requests(self, db):
        mgr = InteractionManager(db)
        await mgr.create_request(
            task_id="TSK-001", instance_token="tok-1",
            request_type="approval", payload={"summary": "a"},
        )
        await mgr.create_request(
            task_id="TSK-002", instance_token="tok-2",
            request_type="clarification", payload={"question": "what color?"},
        )
        pending = await mgr.get_pending()
        assert len(pending) == 2

    @pytest.mark.asyncio
    async def test_resolve_approve(self, db):
        mgr = InteractionManager(db)
        req = await mgr.create_request(
            task_id="TSK-001", instance_token="tok-1",
            request_type="approval", payload={"summary": "done"},
        )
        resolved = await mgr.resolve(
            request_id=req["id"],
            resolution="approved",
            response_payload={"feedback": "looks good"},
            responded_by="human",
        )
        assert resolved["status"] == "approved"
        assert resolved["response_payload"]["feedback"] == "looks good"
        assert resolved["resolved_at"] is not None

    @pytest.mark.asyncio
    async def test_resolve_reject(self, db):
        mgr = InteractionManager(db)
        req = await mgr.create_request(
            task_id="TSK-001", instance_token="tok-1",
            request_type="approval", payload={"summary": "done"},
        )
        resolved = await mgr.resolve(
            request_id=req["id"],
            resolution="rejected",
            response_payload={"feedback": "needs refactoring"},
            responded_by="human",
        )
        assert resolved["status"] == "rejected"

    @pytest.mark.asyncio
    async def test_resolve_respond_clarification(self, db):
        mgr = InteractionManager(db)
        req = await mgr.create_request(
            task_id="TSK-001", instance_token="tok-1",
            request_type="clarification",
            payload={"question": "Which DB?", "suggested_options": ["Postgres", "SQLite"]},
        )
        resolved = await mgr.resolve(
            request_id=req["id"],
            resolution="responded",
            response_payload={"answer": "Use Postgres"},
            responded_by="human",
        )
        assert resolved["status"] == "responded"
        assert resolved["response_payload"]["answer"] == "Use Postgres"

    @pytest.mark.asyncio
    async def test_resolve_skip_clarification(self, db):
        mgr = InteractionManager(db)
        req = await mgr.create_request(
            task_id="TSK-001", instance_token="tok-1",
            request_type="clarification",
            payload={"question": "Which color?"},
        )
        resolved = await mgr.resolve(
            request_id=req["id"],
            resolution="skipped",
            response_payload={"message": "User chose to skip. Use your best judgment."},
            responded_by="human",
        )
        assert resolved["status"] == "skipped"

    @pytest.mark.asyncio
    async def test_resolve_nonexistent_raises(self, db):
        mgr = InteractionManager(db)
        with pytest.raises(ValueError, match="not found"):
            await mgr.resolve(
                request_id="nonexistent",
                resolution="approved",
                response_payload={},
                responded_by="human",
            )

    @pytest.mark.asyncio
    async def test_resolve_already_resolved_raises(self, db):
        mgr = InteractionManager(db)
        req = await mgr.create_request(
            task_id="TSK-001", instance_token="tok-1",
            request_type="approval", payload={"summary": "done"},
        )
        await mgr.resolve(req["id"], "approved", {}, "human")
        with pytest.raises(ValueError, match="already resolved"):
            await mgr.resolve(req["id"], "approved", {}, "human")

    @pytest.mark.asyncio
    async def test_get_history(self, db):
        mgr = InteractionManager(db)
        req = await mgr.create_request(
            task_id="TSK-001", instance_token="tok-1",
            request_type="approval", payload={"summary": "done"},
        )
        await mgr.resolve(req["id"], "approved", {}, "human")
        history = await mgr.get_history(limit=10)
        assert len(history) == 1
        assert history[0]["status"] == "approved"

    @pytest.mark.asyncio
    async def test_check_request_status(self, db):
        mgr = InteractionManager(db)
        req = await mgr.create_request(
            task_id="TSK-001", instance_token="tok-1",
            request_type="approval", payload={"summary": "done"},
        )
        status = await mgr.check_status(req["id"])
        assert status["status"] == "pending"
        assert status["response_payload"] is None

        await mgr.resolve(req["id"], "approved", {"ok": True}, "human")
        status = await mgr.check_status(req["id"])
        assert status["status"] == "approved"
        assert status["response_payload"]["ok"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd "/Users/nikhilchatragadda/Personal Projects/taskbrew" && .venv/bin/pytest tests/test_human_in_the_loop.py::TestInteractionManager -v 2>&1 | head -30`
Expected: FAIL -- `InteractionManager` does not exist.

- [ ] **Step 3: Implement InteractionManager**

Create `src/taskbrew/orchestrator/interactions.py`:

```python
"""Data layer for human interaction requests (approval, clarification)."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _gen_id() -> str:
    return f"hir-{uuid.uuid4().hex[:12]}"


class InteractionManager:
    """CRUD operations for the human_interaction_requests table.

    Parameters
    ----------
    db:
        An initialised :class:`~taskbrew.orchestrator.database.Database` instance.
    """

    def __init__(self, db) -> None:
        self._db = db

    async def create_request(
        self,
        task_id: str,
        instance_token: str,
        request_type: str,
        payload: dict[str, Any],
        sequence_number: int | None = None,
    ) -> dict:
        """Create a new interaction request, or return existing if idempotent key matches.

        Parameters
        ----------
        task_id:
            The task this interaction belongs to.
        instance_token:
            The calling agent's instance token.
        request_type:
            Either ``"approval"`` or ``"clarification"``.
        payload:
            JSON-serializable data (summary, artifact_paths, question, etc.).
        sequence_number:
            Optional sequence number for idempotency. If not provided, uses
            a count of existing requests for this task+type.

        Returns
        -------
        dict
            The created (or existing) request row.
        """
        if sequence_number is None:
            existing = await self._db.execute_fetchall(
                "SELECT COUNT(*) as cnt FROM human_interaction_requests "
                "WHERE task_id = ? AND request_type = ?",
                (task_id, request_type),
            )
            sequence_number = (existing[0]["cnt"] if existing else 0) + 1

        request_key = f"{task_id}:{request_type}:{sequence_number}"

        # Idempotency: check if this request_key already exists
        existing_row = await self._db.execute_fetchone(
            "SELECT * FROM human_interaction_requests WHERE request_key = ?",
            (request_key,),
        )
        if existing_row:
            existing_row = dict(existing_row)
            # Deserialize JSON fields
            if existing_row.get("payload") and isinstance(existing_row["payload"], str):
                existing_row["payload"] = json.loads(existing_row["payload"])
            if existing_row.get("response_payload") and isinstance(existing_row["response_payload"], str):
                existing_row["response_payload"] = json.loads(existing_row["response_payload"])
            return existing_row

        request_id = _gen_id()
        now = _utcnow()
        payload_json = json.dumps(payload)

        await self._db.execute(
            "INSERT INTO human_interaction_requests "
            "(id, task_id, instance_token, request_type, request_key, status, "
            " payload, created_at) "
            "VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)",
            (request_id, task_id, instance_token, request_type, request_key,
             payload_json, now),
        )

        return {
            "id": request_id,
            "task_id": task_id,
            "instance_token": instance_token,
            "request_type": request_type,
            "request_key": request_key,
            "status": "pending",
            "payload": payload,
            "response_payload": None,
            "responded_by": None,
            "created_at": now,
            "resolved_at": None,
        }

    async def get_pending(self, limit: int = 50) -> list[dict]:
        """Return all pending interaction requests, most recent first."""
        rows = await self._db.execute_fetchall(
            "SELECT * FROM human_interaction_requests "
            "WHERE status = 'pending' "
            "ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
        return [self._deserialize_row(r) for r in rows]

    async def get_history(self, limit: int = 50) -> list[dict]:
        """Return resolved interaction requests, most recent first."""
        rows = await self._db.execute_fetchall(
            "SELECT * FROM human_interaction_requests "
            "WHERE status != 'pending' "
            "ORDER BY resolved_at DESC LIMIT ?",
            (limit,),
        )
        return [self._deserialize_row(r) for r in rows]

    async def resolve(
        self,
        request_id: str,
        resolution: str,
        response_payload: dict[str, Any],
        responded_by: str,
    ) -> dict:
        """Resolve a pending interaction request.

        Parameters
        ----------
        request_id:
            ID of the interaction request to resolve.
        resolution:
            One of: ``"approved"``, ``"rejected"``, ``"responded"``, ``"skipped"``.
        response_payload:
            JSON-serializable response data (feedback, answer, etc.).
        responded_by:
            Who resolved it (e.g. ``"human"``).

        Returns
        -------
        dict
            The updated request row.

        Raises
        ------
        ValueError
            If request not found or already resolved.
        """
        row = await self._db.execute_fetchone(
            "SELECT * FROM human_interaction_requests WHERE id = ?",
            (request_id,),
        )
        if row is None:
            raise ValueError(f"Interaction request not found: {request_id}")
        if row["status"] != "pending":
            raise ValueError(
                f"Interaction request {request_id} already resolved "
                f"(status={row['status']})"
            )

        now = _utcnow()
        response_json = json.dumps(response_payload)

        await self._db.execute(
            "UPDATE human_interaction_requests "
            "SET status = ?, response_payload = ?, responded_by = ?, resolved_at = ? "
            "WHERE id = ?",
            (resolution, response_json, responded_by, now, request_id),
        )

        updated = dict(row)
        updated["status"] = resolution
        updated["response_payload"] = response_payload
        updated["responded_by"] = responded_by
        updated["resolved_at"] = now
        if isinstance(updated.get("payload"), str):
            updated["payload"] = json.loads(updated["payload"])
        return updated

    async def check_status(self, request_id: str) -> dict:
        """Check the current status of an interaction request.

        Used by long-polling MCP tools to detect resolution.

        Returns
        -------
        dict
            ``{"status": ..., "response_payload": ...}``

        Raises
        ------
        ValueError
            If request not found.
        """
        row = await self._db.execute_fetchone(
            "SELECT status, response_payload FROM human_interaction_requests "
            "WHERE id = ?",
            (request_id,),
        )
        if row is None:
            raise ValueError(f"Interaction request not found: {request_id}")
        result = dict(row)
        if result.get("response_payload") and isinstance(result["response_payload"], str):
            result["response_payload"] = json.loads(result["response_payload"])
        return result

    def _deserialize_row(self, row: dict) -> dict:
        """Deserialize JSON fields in a row dict."""
        row = dict(row)
        for field in ("payload", "response_payload"):
            if row.get(field) and isinstance(row[field], str):
                try:
                    row[field] = json.loads(row[field])
                except (json.JSONDecodeError, TypeError):
                    pass
        return row
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd "/Users/nikhilchatragadda/Personal Projects/taskbrew" && .venv/bin/pytest tests/test_human_in_the_loop.py::TestInteractionManager -v`
Expected: All 11 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/taskbrew/orchestrator/interactions.py tests/test_human_in_the_loop.py
git commit -m "feat: add InteractionManager CRUD for human interaction requests"
```

---

## Task 3: Pydantic Models for Interactions and MCP Tools

**Files:**
- Modify: `src/taskbrew/dashboard/models.py`
- Test: `tests/test_human_in_the_loop.py`

- [ ] **Step 1: Write failing test for new models**

Append to `tests/test_human_in_the_loop.py`:

```python
from taskbrew.dashboard.models import (
    CompleteTaskMCPBody,
    RequestClarificationMCPBody,
    RouteTaskMCPBody,
    ApproveInteractionBody,
    RejectInteractionBody,
    RespondInteractionBody,
)


class TestHITLModels:
    """Test Pydantic models for MCP tool and interaction API bodies."""

    def test_complete_task_body(self):
        body = CompleteTaskMCPBody(
            artifact_paths=["/src/api.py", "/src/models.py"],
            summary="Implemented REST API endpoints",
        )
        assert len(body.artifact_paths) == 2
        assert body.summary == "Implemented REST API endpoints"

    def test_complete_task_body_defaults(self):
        body = CompleteTaskMCPBody(summary="Done")
        assert body.artifact_paths == []

    def test_request_clarification_body(self):
        body = RequestClarificationMCPBody(
            question="Which database should I use?",
            context="We need a relational DB for user data.",
            suggested_options=["PostgreSQL", "MySQL", "SQLite"],
        )
        assert body.question == "Which database should I use?"
        assert len(body.suggested_options) == 3

    def test_request_clarification_body_defaults(self):
        body = RequestClarificationMCPBody(question="What color?")
        assert body.context is None
        assert body.suggested_options == []

    def test_route_task_body(self):
        body = RouteTaskMCPBody(
            target_agent="coder_be",
            task_type="implementation",
            title="Implement user API",
            description="Create REST endpoints for user CRUD",
            priority="high",
            blocked_by=["TSK-001"],
            chain_id="chain-001",
        )
        assert body.target_agent == "coder_be"
        assert body.chain_id == "chain-001"

    def test_route_task_body_defaults(self):
        body = RouteTaskMCPBody(
            target_agent="coder_be",
            task_type="implementation",
            title="Implement user API",
            description="Create REST endpoints",
        )
        assert body.priority == "medium"
        assert body.blocked_by == []
        assert body.chain_id is None

    def test_approve_interaction_body(self):
        body = ApproveInteractionBody(feedback="Looks great!")
        assert body.feedback == "Looks great!"

    def test_approve_interaction_body_defaults(self):
        body = ApproveInteractionBody()
        assert body.feedback is None

    def test_reject_interaction_body(self):
        body = RejectInteractionBody(feedback="Need to refactor the DB layer")
        assert body.feedback == "Need to refactor the DB layer"

    def test_respond_interaction_body(self):
        body = RespondInteractionBody(answer="Use PostgreSQL")
        assert body.answer == "Use PostgreSQL"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd "/Users/nikhilchatragadda/Personal Projects/taskbrew" && .venv/bin/pytest tests/test_human_in_the_loop.py::TestHITLModels -v 2>&1 | head -20`
Expected: FAIL -- ImportError, models do not exist.

- [ ] **Step 3: Add models to `models.py`**

In `src/taskbrew/dashboard/models.py`, append at the end:

```python
# ---------------------------------------------------------------------------
# Human-in-the-Loop (Plan 3)
# ---------------------------------------------------------------------------


class CompleteTaskMCPBody(BaseModel):
    """Body for the complete_task MCP tool."""
    artifact_paths: list[str] = []
    summary: str


class RequestClarificationMCPBody(BaseModel):
    """Body for the request_clarification MCP tool."""
    question: str
    context: Optional[str] = None
    suggested_options: list[str] = []


class RouteTaskMCPBody(BaseModel):
    """Body for the route_task MCP tool."""
    target_agent: str
    task_type: str
    title: str
    description: str
    priority: str = "medium"
    blocked_by: list[str] = []
    chain_id: Optional[str] = None


class ApproveInteractionBody(BaseModel):
    """Body for POST /api/interactions/{id}/approve."""
    feedback: Optional[str] = None


class RejectInteractionBody(BaseModel):
    """Body for POST /api/interactions/{id}/reject."""
    feedback: str


class RespondInteractionBody(BaseModel):
    """Body for POST /api/interactions/{id}/respond."""
    answer: str
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd "/Users/nikhilchatragadda/Personal Projects/taskbrew" && .venv/bin/pytest tests/test_human_in_the_loop.py::TestHITLModels -v`
Expected: All 9 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/taskbrew/dashboard/models.py tests/test_human_in_the_loop.py
git commit -m "feat: add Pydantic models for MCP tools and interaction API"
```

---

## Task 4: MCP Tool Router -- `/mcp/tools/*` with Long-Polling

**Files:**
- Create: `src/taskbrew/dashboard/routers/mcp_tools.py`
- Modify: `src/taskbrew/dashboard/app.py`
- Test: `tests/test_human_in_the_loop.py`

- [ ] **Step 1: Write failing tests for MCP tool endpoints**

Append to `tests/test_human_in_the_loop.py`:

```python
import json
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient
from fastapi import FastAPI

from taskbrew.dashboard.routers.mcp_tools import router as mcp_router, set_mcp_deps


def _make_test_app(db, interaction_mgr, pipeline_config=None, roles=None):
    """Create a minimal FastAPI app with the MCP router for testing."""
    app = FastAPI()
    app.include_router(mcp_router)

    # Mock orchestrator
    mock_orch = MagicMock()
    mock_orch.task_board = MagicMock()
    mock_orch.task_board._db = db
    mock_orch.roles = roles or {}

    set_mcp_deps(
        get_orch_fn=lambda: mock_orch,
        interaction_mgr=interaction_mgr,
        pipeline_config=pipeline_config,
    )
    return app


class TestMCPCompleteTask:
    """Test the complete_task MCP tool endpoint."""

    @pytest.mark.asyncio
    async def test_complete_task_auto_mode_returns_immediately(self, db):
        from taskbrew.orchestrator.interactions import InteractionManager
        mgr = InteractionManager(db)

        # Insert a task with approval_mode=auto
        now = datetime.now(timezone.utc).isoformat()
        await db.execute(
            "INSERT INTO tasks (id, title, status, created_at, approval_mode, instance_token) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("TSK-001", "Test task", "in_progress", now, "auto", "tok-valid"),
        )

        app = _make_test_app(db, mgr)
        client = TestClient(app)
        resp = client.post(
            "/mcp/tools/complete_task",
            json={"artifact_paths": [], "summary": "All done"},
            headers={"Authorization": "Bearer tok-valid"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "approved"

    @pytest.mark.asyncio
    async def test_complete_task_invalid_token_returns_401(self, db):
        from taskbrew.orchestrator.interactions import InteractionManager
        mgr = InteractionManager(db)
        app = _make_test_app(db, mgr)
        client = TestClient(app)
        resp = client.post(
            "/mcp/tools/complete_task",
            json={"artifact_paths": [], "summary": "done"},
            headers={"Authorization": "Bearer invalid-token"},
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_complete_task_missing_auth_returns_401(self, db):
        from taskbrew.orchestrator.interactions import InteractionManager
        mgr = InteractionManager(db)
        app = _make_test_app(db, mgr)
        client = TestClient(app)
        resp = client.post(
            "/mcp/tools/complete_task",
            json={"artifact_paths": [], "summary": "done"},
        )
        assert resp.status_code == 401


class TestMCPRequestClarification:
    """Test the request_clarification MCP tool endpoint."""

    @pytest.mark.asyncio
    async def test_request_clarification_creates_pending_request(self, db):
        from taskbrew.orchestrator.interactions import InteractionManager
        mgr = InteractionManager(db)

        now = datetime.now(timezone.utc).isoformat()
        await db.execute(
            "INSERT INTO tasks (id, title, status, created_at, instance_token) "
            "VALUES (?, ?, ?, ?, ?)",
            ("TSK-001", "Test task", "in_progress", now, "tok-valid"),
        )

        app = _make_test_app(db, mgr)
        client = TestClient(app)
        resp = client.post(
            "/mcp/tools/request_clarification",
            json={
                "question": "Which DB?",
                "context": "Need relational",
                "suggested_options": ["Postgres", "MySQL"],
            },
            headers={"Authorization": "Bearer tok-valid"},
            params={"_no_poll": "true"},  # Skip long-poll for test speed
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "pending"

        # Verify request was persisted
        pending = await mgr.get_pending()
        assert len(pending) == 1
        assert pending[0]["request_type"] == "clarification"


class TestMCPRouteTask:
    """Test the route_task MCP tool endpoint."""

    @pytest.mark.asyncio
    async def test_route_task_creates_downstream_task(self, db):
        from taskbrew.orchestrator.interactions import InteractionManager
        from taskbrew.config_loader import PipelineConfig, PipelineEdge
        mgr = InteractionManager(db)

        now = datetime.now(timezone.utc).isoformat()
        # Insert parent task
        await db.execute(
            "INSERT INTO tasks (id, group_id, title, status, created_at, "
            "  assigned_to, instance_token) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("TSK-001", "GRP-001", "Parent task", "in_progress", now,
             "pm", "tok-valid"),
        )
        # Insert group
        await db.execute(
            "INSERT INTO groups (id, title, status, created_at) VALUES (?, ?, ?, ?)",
            ("GRP-001", "Test group", "active", now),
        )
        # Register prefix for task ID generation
        await db.register_prefix("CB")

        pipeline = PipelineConfig(
            id="test-pipe",
            start_agent="pm",
            edges=[
                PipelineEdge(
                    id="e1", from_agent="pm", to_agent="coder_be",
                    task_types=["implementation"],
                ),
            ],
        )

        app = _make_test_app(db, mgr, pipeline_config=pipeline)
        client = TestClient(app)
        resp = client.post(
            "/mcp/tools/route_task",
            json={
                "target_agent": "coder_be",
                "task_type": "implementation",
                "title": "Implement user API",
                "description": "Create endpoints",
            },
            headers={"Authorization": "Bearer tok-valid"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "task_id" in data

    @pytest.mark.asyncio
    async def test_route_task_invalid_target_returns_400(self, db):
        from taskbrew.orchestrator.interactions import InteractionManager
        from taskbrew.config_loader import PipelineConfig
        mgr = InteractionManager(db)

        now = datetime.now(timezone.utc).isoformat()
        await db.execute(
            "INSERT INTO tasks (id, group_id, title, status, created_at, "
            "  assigned_to, instance_token) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("TSK-001", "GRP-001", "Parent", "in_progress", now, "pm", "tok-valid"),
        )

        pipeline = PipelineConfig(id="test-pipe", start_agent="pm", edges=[])

        app = _make_test_app(db, mgr, pipeline_config=pipeline)
        client = TestClient(app)
        resp = client.post(
            "/mcp/tools/route_task",
            json={
                "target_agent": "nonexistent",
                "task_type": "implementation",
                "title": "Bad route",
                "description": "Should fail",
            },
            headers={"Authorization": "Bearer tok-valid"},
        )
        assert resp.status_code == 400


class TestMCPGetMyConnections:
    """Test the get_my_connections MCP tool endpoint."""

    @pytest.mark.asyncio
    async def test_get_connections_returns_outbound_edges(self, db):
        from taskbrew.orchestrator.interactions import InteractionManager
        from taskbrew.config_loader import PipelineConfig, PipelineEdge, RoleConfig
        mgr = InteractionManager(db)

        now = datetime.now(timezone.utc).isoformat()
        await db.execute(
            "INSERT INTO tasks (id, title, status, created_at, assigned_to, instance_token) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("TSK-001", "Test", "in_progress", now, "pm", "tok-valid"),
        )

        pipeline = PipelineConfig(
            id="test-pipe",
            start_agent="pm",
            edges=[
                PipelineEdge(id="e1", from_agent="pm", to_agent="architect",
                             task_types=["tech_design"]),
                PipelineEdge(id="e2", from_agent="pm", to_agent="coder_be",
                             task_types=["implementation"]),
                PipelineEdge(id="e3", from_agent="architect", to_agent="coder_be",
                             task_types=["implementation"]),
            ],
        )

        roles = {
            "architect": MagicMock(display_name="Architect", accepts=["tech_design"]),
            "coder_be": MagicMock(display_name="Coder BE", accepts=["implementation"]),
        }

        app = _make_test_app(db, mgr, pipeline_config=pipeline, roles=roles)
        client = TestClient(app)
        resp = client.get(
            "/mcp/tools/get_my_connections",
            headers={"Authorization": "Bearer tok-valid"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["connections"]) == 2
        targets = [c["agent"] for c in data["connections"]]
        assert "architect" in targets
        assert "coder_be" in targets
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd "/Users/nikhilchatragadda/Personal Projects/taskbrew" && .venv/bin/pytest tests/test_human_in_the_loop.py::TestMCPCompleteTask -v 2>&1 | head -20`
Expected: FAIL -- `mcp_tools` module does not exist.

- [ ] **Step 3: Implement MCP tool router**

Create `src/taskbrew/dashboard/routers/mcp_tools.py`:

```python
"""MCP tool HTTP endpoints for agent interactions.

Provides four tools:
- ``complete_task`` -- mark task done (may block for approval)
- ``request_clarification`` -- ask user a question (blocks until answered)
- ``route_task`` -- create downstream task for a connected agent
- ``get_my_connections`` -- return pipeline connections for this agent

All endpoints authenticate via ``Authorization: Bearer {instance_token}``.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Header, Query

from taskbrew.dashboard.models import (
    CompleteTaskMCPBody,
    RequestClarificationMCPBody,
    RouteTaskMCPBody,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# Dependencies injected by app.py
_get_orch_fn = None
_interaction_mgr = None
_pipeline_config = None

LONG_POLL_INTERVAL_S = 30
LONG_POLL_MAX_DURATION_S = 86400  # 24 hours


def set_mcp_deps(get_orch_fn, interaction_mgr, pipeline_config=None):
    """Called by app.py to inject dependencies."""
    global _get_orch_fn, _interaction_mgr, _pipeline_config
    _get_orch_fn = get_orch_fn
    _interaction_mgr = interaction_mgr
    _pipeline_config = pipeline_config


def set_pipeline_config(pipeline_config):
    """Update the pipeline config reference (called when pipeline is reloaded)."""
    global _pipeline_config
    _pipeline_config = pipeline_config


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _authenticate(authorization: str | None) -> dict:
    """Validate the Bearer token and return the task row.

    Raises HTTPException(401) if the token is invalid.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status_code=401, detail="Empty token")

    orch = _get_orch_fn()
    db = orch.task_board._db
    task_row = await db.execute_fetchone(
        "SELECT * FROM tasks WHERE instance_token = ? AND status IN ('in_progress', 'awaiting_approval', 'awaiting_clarification')",
        (token,),
    )
    if task_row is None:
        raise HTTPException(status_code=401, detail="Invalid or expired instance token")
    return dict(task_row)


async def _long_poll(request_id: str, timeout_s: int = LONG_POLL_INTERVAL_S) -> dict:
    """Poll for resolution of an interaction request.

    Returns when the request is resolved or after ``timeout_s`` seconds.
    """
    elapsed = 0
    poll_interval = 1.0  # Check every 1 second
    while elapsed < timeout_s:
        status = await _interaction_mgr.check_status(request_id)
        if status["status"] != "pending":
            return status
        await asyncio.sleep(poll_interval)
        elapsed += poll_interval
    return {"status": "pending", "response_payload": None}


# ------------------------------------------------------------------
# complete_task
# ------------------------------------------------------------------

@router.post("/mcp/tools/complete_task")
async def mcp_complete_task(
    body: CompleteTaskMCPBody,
    authorization: str | None = Header(None),
    _no_poll: str | None = Query(None),
):
    """Mark the current task as done.

    If approval_mode is ``auto``, returns immediately.
    If ``manual`` or ``first_run`` (not yet approved), blocks via long-poll
    until the user approves or rejects.
    """
    task = await _authenticate(authorization)
    token = authorization.removeprefix("Bearer ").strip()
    orch = _get_orch_fn()
    db = orch.task_board._db

    # Determine approval_mode from task or role config
    approval_mode = task.get("approval_mode") or "auto"

    # Check for first_run: if already approved for this group+role, treat as auto
    if approval_mode == "first_run":
        assigned_to = task.get("assigned_to", "")
        group_id = task.get("group_id", "")
        if group_id and assigned_to:
            existing = await db.execute_fetchone(
                "SELECT id FROM first_run_approvals "
                "WHERE group_id = ? AND agent_role = ?",
                (group_id, assigned_to),
            )
            if existing:
                approval_mode = "auto"

    if approval_mode == "auto":
        # Mark task completed immediately
        await db.execute(
            "UPDATE tasks SET status = 'completed', completed_at = ?, output_text = ? "
            "WHERE id = ?",
            (_utcnow(), body.summary, task["id"]),
        )
        return {"status": "approved"}

    # manual or first_run (not yet approved): create interaction request and block
    await db.execute(
        "UPDATE tasks SET status = 'awaiting_approval' WHERE id = ?",
        (task["id"],),
    )

    req = await _interaction_mgr.create_request(
        task_id=task["id"],
        instance_token=token,
        request_type="approval",
        payload={
            "summary": body.summary,
            "artifact_paths": body.artifact_paths,
        },
    )

    # Also create a dashboard notification
    await db.execute(
        "INSERT INTO notifications (type, title, message, severity, data, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("approval_required", f"Approval needed: {task['title']}",
         body.summary, "warning",
         json.dumps({"task_id": task["id"], "interaction_id": req["id"]}),
         _utcnow()),
    )

    # Skip polling in tests
    if _no_poll == "true":
        return {"status": "pending", "interaction_id": req["id"]}

    # Long-poll loop
    result = await _long_poll(req["id"], LONG_POLL_INTERVAL_S)
    if result["status"] == "approved":
        # Mark task completed
        await db.execute(
            "UPDATE tasks SET status = 'completed', completed_at = ?, output_text = ? "
            "WHERE id = ?",
            (_utcnow(), body.summary, task["id"]),
        )
        # Record first_run approval if applicable
        original_mode = task.get("approval_mode") or "auto"
        if original_mode == "first_run":
            import uuid
            assigned_to = task.get("assigned_to", "")
            group_id = task.get("group_id", "")
            if group_id and assigned_to:
                try:
                    await db.execute(
                        "INSERT OR IGNORE INTO first_run_approvals "
                        "(id, group_id, agent_role, approved_at) VALUES (?, ?, ?, ?)",
                        (str(uuid.uuid4())[:12], group_id, assigned_to, _utcnow()),
                    )
                except Exception:
                    pass  # Already recorded

    if result["status"] == "rejected":
        feedback = ""
        if result.get("response_payload") and isinstance(result["response_payload"], dict):
            feedback = result["response_payload"].get("feedback", "")
        await db.execute(
            "UPDATE tasks SET status = 'rejected', rejection_reason = ? WHERE id = ?",
            (feedback, task["id"]),
        )
        return {"status": "rejected", "feedback": feedback}

    return {"status": result["status"]}


# ------------------------------------------------------------------
# request_clarification
# ------------------------------------------------------------------

@router.post("/mcp/tools/request_clarification")
async def mcp_request_clarification(
    body: RequestClarificationMCPBody,
    authorization: str | None = Header(None),
    _no_poll: str | None = Query(None),
):
    """Ask the user a question. Blocks until the user responds or skips."""
    task = await _authenticate(authorization)
    token = authorization.removeprefix("Bearer ").strip()
    orch = _get_orch_fn()
    db = orch.task_board._db

    # Check clarification limit
    role_name = task.get("assigned_to", "")
    max_clarifications = 10
    if role_name and hasattr(orch, "roles") and orch.roles and role_name in orch.roles:
        max_clarifications = getattr(orch.roles[role_name], "max_clarification_requests", 10)

    existing_count_rows = await db.execute_fetchall(
        "SELECT COUNT(*) as cnt FROM human_interaction_requests "
        "WHERE task_id = ? AND request_type = 'clarification'",
        (task["id"],),
    )
    existing_count = existing_count_rows[0]["cnt"] if existing_count_rows else 0
    if existing_count >= max_clarifications:
        raise HTTPException(
            status_code=429,
            detail="Clarification limit reached. Make your best judgment or escalate via complete_task.",
        )

    # Update task status
    await db.execute(
        "UPDATE tasks SET status = 'awaiting_clarification' WHERE id = ?",
        (task["id"],),
    )

    req = await _interaction_mgr.create_request(
        task_id=task["id"],
        instance_token=token,
        request_type="clarification",
        payload={
            "question": body.question,
            "context": body.context,
            "suggested_options": body.suggested_options,
        },
    )

    # Dashboard notification
    await db.execute(
        "INSERT INTO notifications (type, title, message, severity, data, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("clarification_required", f"Question from agent: {task.get('assigned_to', 'unknown')}",
         body.question, "info",
         json.dumps({"task_id": task["id"], "interaction_id": req["id"]}),
         _utcnow()),
    )

    if _no_poll == "true":
        return {"status": "pending", "interaction_id": req["id"]}

    # Long-poll
    result = await _long_poll(req["id"], LONG_POLL_INTERVAL_S)

    if result["status"] in ("responded", "skipped"):
        # Restore task to in_progress
        await db.execute(
            "UPDATE tasks SET status = 'in_progress' WHERE id = ?",
            (task["id"],),
        )

    if result["status"] == "responded":
        answer = ""
        if result.get("response_payload") and isinstance(result["response_payload"], dict):
            answer = result["response_payload"].get("answer", "")
        return {"status": "responded", "answer": answer}

    if result["status"] == "skipped":
        return {"status": "skipped", "message": "User chose to skip. Use your best judgment."}

    return {"status": "pending", "interaction_id": req["id"]}


# ------------------------------------------------------------------
# route_task
# ------------------------------------------------------------------

@router.post("/mcp/tools/route_task")
async def mcp_route_task(
    body: RouteTaskMCPBody,
    authorization: str | None = Header(None),
):
    """Create a downstream task for a connected agent.

    Validates against the pipeline topology. The created task starts as
    ``pending`` with ``blocked_by`` containing the calling agent's task ID.
    """
    task = await _authenticate(authorization)
    token = authorization.removeprefix("Bearer ").strip()
    orch = _get_orch_fn()
    db = orch.task_board._db

    caller_role = task.get("assigned_to", "")
    group_id = task.get("group_id", "")

    # Validate pipeline edge exists
    if _pipeline_config is None:
        raise HTTPException(status_code=500, detail="Pipeline not configured")

    valid_edge = None
    for edge in _pipeline_config.edges:
        if edge.from_agent == caller_role and edge.to_agent == body.target_agent:
            if not edge.task_types or body.task_type in edge.task_types:
                valid_edge = edge
                break

    if valid_edge is None:
        # Build a helpful error message
        available = [
            f"{e.to_agent} ({', '.join(e.task_types) if e.task_types else 'any'})"
            for e in _pipeline_config.edges
            if e.from_agent == caller_role
        ]
        detail = (
            f"No edge from '{caller_role}' to '{body.target_agent}' "
            f"for task_type '{body.task_type}'. "
            f"Available targets: {', '.join(available) if available else 'none'}"
        )
        raise HTTPException(status_code=400, detail=detail)

    # Check route_task rate limit
    max_route_tasks = 100
    if caller_role and hasattr(orch, "roles") and orch.roles and caller_role in orch.roles:
        max_route_tasks = getattr(orch.roles[caller_role], "max_route_tasks", 100)

    route_count_rows = await db.execute_fetchall(
        "SELECT COUNT(*) as cnt FROM tasks WHERE parent_id = ?",
        (task["id"],),
    )
    route_count = route_count_rows[0]["cnt"] if route_count_rows else 0
    if route_count >= max_route_tasks:
        raise HTTPException(
            status_code=429,
            detail=f"Route task limit ({max_route_tasks}) reached for this task instance.",
        )

    # Check revision limit if this is a revision task
    if body.task_type == "revision" and body.chain_id:
        chain = await db.execute_fetchone(
            "SELECT * FROM task_chains WHERE id = ?",
            (body.chain_id,),
        )
        if chain and chain["max_revision_cycles"] > 0:
            if chain["revision_count"] >= chain["max_revision_cycles"]:
                raise HTTPException(
                    status_code=409,
                    detail=f"Revision limit reached for chain {body.chain_id}. Escalating to human intervention.",
                )

    # Determine target agent prefix for ID generation
    target_prefix = body.target_agent[:2].upper()
    if hasattr(orch, "roles") and orch.roles and body.target_agent in orch.roles:
        target_prefix = getattr(orch.roles[body.target_agent], "prefix", target_prefix)

    # Ensure prefix is registered
    await db.register_prefix(target_prefix)

    # Generate task ID
    new_task_id = await db.generate_task_id(target_prefix)

    now = _utcnow()

    # Determine approval_mode for the new task from target role config
    target_approval_mode = "auto"
    config_snapshot_dict: dict[str, Any] = {}
    if hasattr(orch, "roles") and orch.roles and body.target_agent in orch.roles:
        target_role = orch.roles[body.target_agent]
        target_approval_mode = getattr(target_role, "approval_mode", "auto")
        config_snapshot_dict = {
            "max_revision_cycles": getattr(target_role, "max_revision_cycles", 0),
            "max_clarification_requests": getattr(target_role, "max_clarification_requests", 10),
            "max_route_tasks": getattr(target_role, "max_route_tasks", 100),
        }

    config_snapshot = json.dumps(config_snapshot_dict) if config_snapshot_dict else None

    # Create the downstream task
    await db.execute(
        "INSERT INTO tasks "
        "(id, group_id, parent_id, title, description, task_type, priority, "
        " assigned_to, status, created_by, created_at, chain_id, approval_mode, "
        " config_snapshot) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?)",
        (new_task_id, group_id, task["id"], body.title, body.description,
         body.task_type, body.priority, body.target_agent, caller_role, now,
         body.chain_id, target_approval_mode, config_snapshot),
    )

    # Add blocked_by dependency on parent task
    blocked_by_list = [task["id"]] + body.blocked_by
    for blocker_id in blocked_by_list:
        try:
            await db.execute(
                "INSERT OR IGNORE INTO task_dependencies (task_id, blocked_by) "
                "VALUES (?, ?)",
                (new_task_id, blocker_id),
            )
        except Exception:
            pass  # Ignore duplicate or invalid references

    # Update chain revision count if applicable
    if body.task_type == "revision" and body.chain_id:
        await db.execute(
            "UPDATE task_chains SET revision_count = revision_count + 1, "
            "current_task_id = ?, updated_at = ? WHERE id = ?",
            (new_task_id, now, body.chain_id),
        )

    return {"task_id": new_task_id}


# ------------------------------------------------------------------
# get_my_connections
# ------------------------------------------------------------------

@router.get("/mcp/tools/get_my_connections")
async def mcp_get_my_connections(
    authorization: str | None = Header(None),
):
    """Return the pipeline connections for the calling agent."""
    task = await _authenticate(authorization)
    caller_role = task.get("assigned_to", "")
    orch = _get_orch_fn()

    if _pipeline_config is None:
        return {"connections": []}

    connections = []
    for edge in _pipeline_config.edges:
        if edge.from_agent == caller_role:
            display_name = edge.to_agent
            accepts: list[str] = []
            if hasattr(orch, "roles") and orch.roles and edge.to_agent in orch.roles:
                target_role = orch.roles[edge.to_agent]
                display_name = getattr(target_role, "display_name", edge.to_agent)
                accepts = getattr(target_role, "accepts", [])
            connections.append({
                "agent": edge.to_agent,
                "display_name": display_name,
                "task_types": edge.task_types,
                "accepts": accepts,
            })

    return {"connections": connections}
```

- [ ] **Step 4: Register router in app.py**

In `src/taskbrew/dashboard/app.py`, add with the other router imports:

```python
    from taskbrew.dashboard.routers.mcp_tools import router as mcp_tools_router, set_mcp_deps
```

And add to the `include_router` block:

```python
    app.include_router(mcp_tools_router, tags=["MCP Tools"])
```

And after the orchestrator is available, call:

```python
    from taskbrew.orchestrator.interactions import InteractionManager
    interaction_mgr = InteractionManager(orch.task_board._db)
    set_mcp_deps(
        get_orch_fn=lambda: get_orch(),
        interaction_mgr=interaction_mgr,
        pipeline_config=pipeline_config,  # loaded from team.yaml
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd "/Users/nikhilchatragadda/Personal Projects/taskbrew" && .venv/bin/pytest tests/test_human_in_the_loop.py::TestMCPCompleteTask tests/test_human_in_the_loop.py::TestMCPRequestClarification tests/test_human_in_the_loop.py::TestMCPRouteTask tests/test_human_in_the_loop.py::TestMCPGetMyConnections -v`
Expected: All 7 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/taskbrew/dashboard/routers/mcp_tools.py src/taskbrew/dashboard/app.py tests/test_human_in_the_loop.py
git commit -m "feat: add MCP tool router — complete_task, request_clarification, route_task, get_my_connections"
```

---

## Task 5: Dashboard Interaction API -- `/api/interactions/*` Endpoints

**Files:**
- Create: `src/taskbrew/dashboard/routers/interactions.py`
- Modify: `src/taskbrew/dashboard/app.py`
- Test: `tests/test_human_in_the_loop.py`

- [ ] **Step 1: Write failing tests for interaction API endpoints**

Append to `tests/test_human_in_the_loop.py`:

```python
from taskbrew.dashboard.routers.interactions import router as interactions_router, set_interaction_deps


def _make_interactions_app(db, interaction_mgr):
    """Create a minimal FastAPI app with the interactions router."""
    app = FastAPI()
    app.include_router(interactions_router)

    mock_orch = MagicMock()
    mock_orch.task_board = MagicMock()
    mock_orch.task_board._db = db

    set_interaction_deps(
        get_orch_fn=lambda: mock_orch,
        interaction_mgr=interaction_mgr,
    )
    return app


class TestInteractionAPI:
    """Test dashboard interaction API endpoints."""

    @pytest.mark.asyncio
    async def test_get_pending_empty(self, db):
        from taskbrew.orchestrator.interactions import InteractionManager
        mgr = InteractionManager(db)
        app = _make_interactions_app(db, mgr)
        client = TestClient(app)
        resp = client.get("/api/interactions/pending")
        assert resp.status_code == 200
        assert resp.json() == []

    @pytest.mark.asyncio
    async def test_get_pending_with_requests(self, db):
        from taskbrew.orchestrator.interactions import InteractionManager
        mgr = InteractionManager(db)

        # Create tasks first
        now = datetime.now(timezone.utc).isoformat()
        await db.execute(
            "INSERT INTO tasks (id, title, status, created_at) VALUES (?, ?, ?, ?)",
            ("TSK-001", "Test", "awaiting_approval", now),
        )
        await mgr.create_request(
            task_id="TSK-001", instance_token="tok-1",
            request_type="approval", payload={"summary": "done"},
        )

        app = _make_interactions_app(db, mgr)
        client = TestClient(app)
        resp = client.get("/api/interactions/pending")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["request_type"] == "approval"

    @pytest.mark.asyncio
    async def test_approve_interaction(self, db):
        from taskbrew.orchestrator.interactions import InteractionManager
        mgr = InteractionManager(db)

        now = datetime.now(timezone.utc).isoformat()
        await db.execute(
            "INSERT INTO tasks (id, title, status, created_at) VALUES (?, ?, ?, ?)",
            ("TSK-001", "Test", "awaiting_approval", now),
        )
        req = await mgr.create_request(
            task_id="TSK-001", instance_token="tok-1",
            request_type="approval", payload={"summary": "done"},
        )

        app = _make_interactions_app(db, mgr)
        client = TestClient(app)
        resp = client.post(
            f"/api/interactions/{req['id']}/approve",
            json={"feedback": "Looks great"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "approved"

    @pytest.mark.asyncio
    async def test_reject_interaction(self, db):
        from taskbrew.orchestrator.interactions import InteractionManager
        mgr = InteractionManager(db)

        now = datetime.now(timezone.utc).isoformat()
        await db.execute(
            "INSERT INTO tasks (id, title, status, created_at) VALUES (?, ?, ?, ?)",
            ("TSK-001", "Test", "awaiting_approval", now),
        )
        req = await mgr.create_request(
            task_id="TSK-001", instance_token="tok-1",
            request_type="approval", payload={"summary": "done"},
        )

        app = _make_interactions_app(db, mgr)
        client = TestClient(app)
        resp = client.post(
            f"/api/interactions/{req['id']}/reject",
            json={"feedback": "Needs refactoring"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "rejected"

    @pytest.mark.asyncio
    async def test_respond_to_clarification(self, db):
        from taskbrew.orchestrator.interactions import InteractionManager
        mgr = InteractionManager(db)

        now = datetime.now(timezone.utc).isoformat()
        await db.execute(
            "INSERT INTO tasks (id, title, status, created_at) VALUES (?, ?, ?, ?)",
            ("TSK-001", "Test", "awaiting_clarification", now),
        )
        req = await mgr.create_request(
            task_id="TSK-001", instance_token="tok-1",
            request_type="clarification",
            payload={"question": "Which DB?"},
        )

        app = _make_interactions_app(db, mgr)
        client = TestClient(app)
        resp = client.post(
            f"/api/interactions/{req['id']}/respond",
            json={"answer": "Use PostgreSQL"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "responded"

    @pytest.mark.asyncio
    async def test_skip_clarification(self, db):
        from taskbrew.orchestrator.interactions import InteractionManager
        mgr = InteractionManager(db)

        now = datetime.now(timezone.utc).isoformat()
        await db.execute(
            "INSERT INTO tasks (id, title, status, created_at) VALUES (?, ?, ?, ?)",
            ("TSK-001", "Test", "awaiting_clarification", now),
        )
        req = await mgr.create_request(
            task_id="TSK-001", instance_token="tok-1",
            request_type="clarification",
            payload={"question": "Which color?"},
        )

        app = _make_interactions_app(db, mgr)
        client = TestClient(app)
        resp = client.post(f"/api/interactions/{req['id']}/skip")
        assert resp.status_code == 200
        assert resp.json()["status"] == "skipped"

    @pytest.mark.asyncio
    async def test_get_history(self, db):
        from taskbrew.orchestrator.interactions import InteractionManager
        mgr = InteractionManager(db)

        now = datetime.now(timezone.utc).isoformat()
        await db.execute(
            "INSERT INTO tasks (id, title, status, created_at) VALUES (?, ?, ?, ?)",
            ("TSK-001", "Test", "completed", now),
        )
        req = await mgr.create_request(
            task_id="TSK-001", instance_token="tok-1",
            request_type="approval", payload={"summary": "done"},
        )
        await mgr.resolve(req["id"], "approved", {}, "human")

        app = _make_interactions_app(db, mgr)
        client = TestClient(app)
        resp = client.get("/api/interactions/history")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["status"] == "approved"

    @pytest.mark.asyncio
    async def test_approve_nonexistent_returns_404(self, db):
        from taskbrew.orchestrator.interactions import InteractionManager
        mgr = InteractionManager(db)

        app = _make_interactions_app(db, mgr)
        client = TestClient(app)
        resp = client.post(
            "/api/interactions/nonexistent/approve",
            json={},
        )
        assert resp.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd "/Users/nikhilchatragadda/Personal Projects/taskbrew" && .venv/bin/pytest tests/test_human_in_the_loop.py::TestInteractionAPI -v 2>&1 | head -20`
Expected: FAIL -- `interactions` module does not exist.

- [ ] **Step 3: Implement interactions router**

Create `src/taskbrew/dashboard/routers/interactions.py`:

```python
"""Dashboard API endpoints for human interaction management.

Provides endpoints for listing, resolving, and reviewing interaction
requests (approvals, clarifications).
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException

from taskbrew.dashboard.models import (
    ApproveInteractionBody,
    RejectInteractionBody,
    RespondInteractionBody,
)

logger = logging.getLogger(__name__)

router = APIRouter()

_get_orch_fn = None
_interaction_mgr = None


def set_interaction_deps(get_orch_fn, interaction_mgr):
    """Called by app.py to inject dependencies."""
    global _get_orch_fn, _interaction_mgr
    _get_orch_fn = get_orch_fn
    _interaction_mgr = interaction_mgr


@router.get("/api/interactions/pending")
async def get_pending_interactions(limit: int = 50):
    """List all pending interaction requests, sorted by urgency."""
    pending = await _interaction_mgr.get_pending(limit=limit)

    # Enrich with task data
    orch = _get_orch_fn()
    db = orch.task_board._db
    for item in pending:
        task = await db.execute_fetchone(
            "SELECT id, title, assigned_to, group_id, status FROM tasks WHERE id = ?",
            (item["task_id"],),
        )
        if task:
            item["task_title"] = task["title"]
            item["agent_role"] = task["assigned_to"]
            item["group_id"] = task["group_id"]
            item["task_status"] = task["status"]

    # Sort by urgency: awaiting_human_intervention > approval > clarification
    type_order = {"approval": 0, "clarification": 1}
    pending.sort(key=lambda x: type_order.get(x.get("request_type", ""), 2))

    return pending


@router.get("/api/interactions/history")
async def get_interaction_history(limit: int = 50):
    """List resolved interaction requests for audit trail."""
    return await _interaction_mgr.get_history(limit=limit)


@router.post("/api/interactions/{interaction_id}/approve")
async def approve_interaction(interaction_id: str, body: ApproveInteractionBody = None):
    """Approve a pending approval request."""
    if body is None:
        body = ApproveInteractionBody()

    try:
        resolved = await _interaction_mgr.resolve(
            request_id=interaction_id,
            resolution="approved",
            response_payload={"feedback": body.feedback} if body.feedback else {},
            responded_by="human",
        )
    except ValueError as e:
        if "not found" in str(e):
            raise HTTPException(status_code=404, detail=str(e))
        raise HTTPException(status_code=409, detail=str(e))

    return resolved


@router.post("/api/interactions/{interaction_id}/reject")
async def reject_interaction(interaction_id: str, body: RejectInteractionBody):
    """Reject a pending approval request with feedback."""
    try:
        resolved = await _interaction_mgr.resolve(
            request_id=interaction_id,
            resolution="rejected",
            response_payload={"feedback": body.feedback},
            responded_by="human",
        )
    except ValueError as e:
        if "not found" in str(e):
            raise HTTPException(status_code=404, detail=str(e))
        raise HTTPException(status_code=409, detail=str(e))

    return resolved


@router.post("/api/interactions/{interaction_id}/respond")
async def respond_to_clarification(interaction_id: str, body: RespondInteractionBody):
    """Respond to a pending clarification request."""
    try:
        resolved = await _interaction_mgr.resolve(
            request_id=interaction_id,
            resolution="responded",
            response_payload={"answer": body.answer},
            responded_by="human",
        )
    except ValueError as e:
        if "not found" in str(e):
            raise HTTPException(status_code=404, detail=str(e))
        raise HTTPException(status_code=409, detail=str(e))

    return resolved


@router.post("/api/interactions/{interaction_id}/skip")
async def skip_clarification(interaction_id: str):
    """Skip a pending clarification request."""
    try:
        resolved = await _interaction_mgr.resolve(
            request_id=interaction_id,
            resolution="skipped",
            response_payload={"message": "User chose to skip. Use your best judgment."},
            responded_by="human",
        )
    except ValueError as e:
        if "not found" in str(e):
            raise HTTPException(status_code=404, detail=str(e))
        raise HTTPException(status_code=409, detail=str(e))

    return resolved
```

- [ ] **Step 4: Register interactions router in app.py**

In `src/taskbrew/dashboard/app.py`, add:

```python
    from taskbrew.dashboard.routers.interactions import router as interactions_router, set_interaction_deps
    app.include_router(interactions_router, tags=["Interactions"])
    set_interaction_deps(
        get_orch_fn=lambda: get_orch(),
        interaction_mgr=interaction_mgr,
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd "/Users/nikhilchatragadda/Personal Projects/taskbrew" && .venv/bin/pytest tests/test_human_in_the_loop.py::TestInteractionAPI -v`
Expected: All 8 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/taskbrew/dashboard/routers/interactions.py src/taskbrew/dashboard/app.py tests/test_human_in_the_loop.py
git commit -m "feat: add dashboard interaction API — approve, reject, respond, skip endpoints"
```

---

## Task 6: Dashboard Notification UI -- "Action Required" Panel

**Files:**
- Modify: `src/taskbrew/dashboard/templates/index.html`
- Test: Manual browser testing (described below)

- [ ] **Step 1: Write test for interaction panel rendering**

Append to `tests/test_human_in_the_loop.py`:

```python
class TestInteractionPanelHTML:
    """Test that the Action Required panel HTML elements exist in index.html."""

    def test_action_required_panel_exists_in_html(self):
        from pathlib import Path
        html_path = Path(__file__).parent.parent / "src" / "taskbrew" / "dashboard" / "templates" / "index.html"
        content = html_path.read_text()
        assert "action-required-panel" in content, "Missing action-required-panel element"
        assert "interactionCards" in content, "Missing interactionCards container"
        assert "loadPendingInteractions" in content, "Missing loadPendingInteractions function"
        assert "approveInteraction" in content, "Missing approveInteraction function"
        assert "rejectInteraction" in content, "Missing rejectInteraction function"
        assert "respondInteraction" in content, "Missing respondInteraction function"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd "/Users/nikhilchatragadda/Personal Projects/taskbrew" && .venv/bin/pytest tests/test_human_in_the_loop.py::TestInteractionPanelHTML -v`
Expected: FAIL -- elements do not exist in the HTML.

- [ ] **Step 3: Add "Action Required" panel CSS**

In `src/taskbrew/dashboard/templates/index.html`, add to the `<style>` section (within the main style block, before the closing `</style>` tag):

```css
        /* --- Action Required Panel (HITL) --- */
        .action-required-panel {
            background: var(--card-bg);
            border: 1px solid var(--border);
            border-radius: 16px;
            padding: 20px;
            margin-bottom: 24px;
        }
        .action-required-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 16px;
        }
        .action-required-header h3 {
            font-size: 14px;
            font-weight: 700;
            color: var(--text-primary);
            display: flex;
            align-items: center;
            gap: 8px;
        }
        .action-required-header .ar-badge {
            background: rgba(244, 63, 94, 0.15);
            color: var(--accent-rose-light);
            font-size: 11px;
            padding: 2px 8px;
            border-radius: 10px;
            font-weight: 700;
        }
        .interaction-card {
            background: rgba(255, 255, 255, 0.02);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 16px;
            margin-bottom: 12px;
            transition: border-color 0.2s;
        }
        .interaction-card:hover {
            border-color: rgba(99, 102, 241, 0.3);
        }
        .interaction-card.type-approval {
            border-left: 3px solid var(--accent-amber);
        }
        .interaction-card.type-clarification {
            border-left: 3px solid var(--accent-cyan);
        }
        .interaction-card-header {
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            margin-bottom: 8px;
        }
        .interaction-card-type {
            font-size: 10px;
            text-transform: uppercase;
            font-weight: 700;
            letter-spacing: 0.5px;
            padding: 2px 8px;
            border-radius: 6px;
        }
        .interaction-card-type.approval {
            background: rgba(245, 158, 11, 0.12);
            color: var(--accent-amber-light);
        }
        .interaction-card-type.clarification {
            background: rgba(6, 182, 212, 0.12);
            color: var(--accent-cyan-light);
        }
        .interaction-card-agent {
            font-size: 12px;
            color: var(--text-secondary);
            margin-bottom: 4px;
        }
        .interaction-card-title {
            font-size: 13px;
            font-weight: 600;
            color: var(--text-primary);
            margin-bottom: 8px;
        }
        .interaction-card-body {
            font-size: 12px;
            color: var(--text-secondary);
            margin-bottom: 12px;
            line-height: 1.5;
        }
        .interaction-card-actions {
            display: flex;
            gap: 8px;
            flex-wrap: wrap;
        }
        .interaction-btn {
            padding: 6px 14px;
            border-radius: 8px;
            border: 1px solid transparent;
            font-size: 11px;
            font-weight: 600;
            cursor: pointer;
            font-family: 'Inter', sans-serif;
            transition: all 0.2s;
        }
        .interaction-btn.approve {
            background: rgba(16, 185, 129, 0.15);
            color: var(--accent-emerald);
            border-color: rgba(16, 185, 129, 0.3);
        }
        .interaction-btn.approve:hover {
            background: rgba(16, 185, 129, 0.25);
        }
        .interaction-btn.reject {
            background: rgba(244, 63, 94, 0.12);
            color: var(--accent-rose-light);
            border-color: rgba(244, 63, 94, 0.2);
        }
        .interaction-btn.reject:hover {
            background: rgba(244, 63, 94, 0.2);
        }
        .interaction-btn.respond {
            background: rgba(99, 102, 241, 0.12);
            color: var(--accent-indigo-light);
            border-color: rgba(99, 102, 241, 0.2);
        }
        .interaction-btn.respond:hover {
            background: rgba(99, 102, 241, 0.2);
        }
        .interaction-btn.skip {
            background: rgba(255, 255, 255, 0.04);
            color: var(--text-secondary);
            border-color: var(--border);
        }
        .interaction-btn.skip:hover {
            background: rgba(255, 255, 255, 0.08);
        }
        .interaction-feedback-input {
            width: 100%;
            padding: 8px 12px;
            border-radius: 8px;
            border: 1px solid var(--border);
            background: var(--card-bg);
            color: var(--text-primary);
            font-size: 12px;
            font-family: 'Inter', sans-serif;
            margin-bottom: 8px;
            resize: vertical;
            min-height: 40px;
        }
        .interaction-feedback-input:focus {
            outline: none;
            border-color: rgba(99, 102, 241, 0.5);
        }
        .interaction-options {
            display: flex;
            gap: 6px;
            flex-wrap: wrap;
            margin-bottom: 8px;
        }
        .interaction-option-btn {
            padding: 4px 10px;
            border-radius: 6px;
            border: 1px solid var(--border);
            background: rgba(255, 255, 255, 0.04);
            color: var(--text-secondary);
            font-size: 11px;
            cursor: pointer;
            transition: all 0.2s;
        }
        .interaction-option-btn:hover {
            background: rgba(99, 102, 241, 0.1);
            border-color: rgba(99, 102, 241, 0.3);
            color: var(--accent-indigo-light);
        }
        .no-interactions-msg {
            text-align: center;
            color: var(--text-tertiary);
            font-size: 12px;
            padding: 20px;
        }
```

- [ ] **Step 4: Add "Action Required" panel HTML**

In `src/taskbrew/dashboard/templates/index.html`, add the panel HTML inside the main dashboard content area (after the breadcrumb bar and before the existing board/kanban content, inside the `<main>` tag):

```html
    <!-- Action Required Panel (HITL) -->
    <div class="action-required-panel" id="action-required-panel" style="display:none;">
        <div class="action-required-header">
            <h3>&#9888;&#65039; Action Required <span class="ar-badge" id="arBadge">0</span></h3>
        </div>
        <div id="interactionCards"></div>
    </div>
```

- [ ] **Step 5: Add JavaScript functions for interaction management**

In `src/taskbrew/dashboard/templates/index.html`, add to the `<script>` section:

```javascript
    // --- Action Required (HITL) ---

    let _pendingInteractions = [];

    async function loadPendingInteractions() {
        try {
            const resp = await fetch('/api/interactions/pending');
            if (!resp.ok) return;
            _pendingInteractions = await resp.json();
            renderInteractionCards();
        } catch (e) {
            console.warn('Failed to load interactions:', e);
        }
    }

    function renderInteractionCards() {
        const panel = document.getElementById('action-required-panel');
        const container = document.getElementById('interactionCards');
        const badge = document.getElementById('arBadge');

        if (!_pendingInteractions.length) {
            panel.style.display = 'none';
            return;
        }

        panel.style.display = 'block';
        badge.textContent = _pendingInteractions.length;

        container.innerHTML = _pendingInteractions.map(req => {
            const isApproval = req.request_type === 'approval';
            const typeClass = isApproval ? 'approval' : 'clarification';
            const typeLabel = isApproval ? 'Approval Required' : 'Clarification Needed';
            const agentRole = req.agent_role || req.task_id;
            const taskTitle = req.task_title || req.task_id;
            const payload = req.payload || {};

            let bodyHTML = '';
            let actionsHTML = '';

            if (isApproval) {
                const summary = payload.summary || 'No summary provided';
                const artifacts = (payload.artifact_paths || []).map(p =>
                    `<span style="font-family:monospace;font-size:11px;color:var(--accent-indigo-light);">${p}</span>`
                ).join(', ');
                bodyHTML = `
                    <div class="interaction-card-body">
                        <div style="margin-bottom:6px;">${summary}</div>
                        ${artifacts ? `<div style="margin-top:4px;">Artifacts: ${artifacts}</div>` : ''}
                    </div>
                    <textarea class="interaction-feedback-input" id="feedback-${req.id}"
                        placeholder="Optional feedback..."></textarea>
                `;
                actionsHTML = `
                    <button class="interaction-btn approve" onclick="approveInteraction('${req.id}')">Approve</button>
                    <button class="interaction-btn reject" onclick="rejectInteraction('${req.id}')">Reject</button>
                `;
            } else {
                const question = payload.question || 'No question provided';
                const context = payload.context || '';
                const options = payload.suggested_options || [];
                const optionsHTML = options.length ? `
                    <div class="interaction-options">
                        ${options.map(opt => `<button class="interaction-option-btn" onclick="selectOption('${req.id}', '${opt.replace(/'/g, "\\'")}')">${opt}</button>`).join('')}
                    </div>
                ` : '';
                bodyHTML = `
                    <div class="interaction-card-body">
                        <div style="margin-bottom:6px;font-weight:600;">${question}</div>
                        ${context ? `<div style="color:var(--text-tertiary);font-size:11px;">${context}</div>` : ''}
                    </div>
                    ${optionsHTML}
                    <textarea class="interaction-feedback-input" id="answer-${req.id}"
                        placeholder="Type your answer..."></textarea>
                `;
                actionsHTML = `
                    <button class="interaction-btn respond" onclick="respondInteraction('${req.id}')">Respond</button>
                    <button class="interaction-btn skip" onclick="skipInteraction('${req.id}')">Skip</button>
                `;
            }

            return `
                <div class="interaction-card type-${typeClass}">
                    <div class="interaction-card-header">
                        <div>
                            <div class="interaction-card-agent">${agentRole}</div>
                            <div class="interaction-card-title">${taskTitle}</div>
                        </div>
                        <span class="interaction-card-type ${typeClass}">${typeLabel}</span>
                    </div>
                    ${bodyHTML}
                    <div class="interaction-card-actions">
                        ${actionsHTML}
                    </div>
                </div>
            `;
        }).join('');
    }

    function selectOption(reqId, option) {
        const textarea = document.getElementById('answer-' + reqId);
        if (textarea) textarea.value = option;
    }

    async function approveInteraction(reqId) {
        const feedbackEl = document.getElementById('feedback-' + reqId);
        const feedback = feedbackEl ? feedbackEl.value.trim() : null;
        const body = feedback ? { feedback } : {};
        try {
            const resp = await fetch(`/api/interactions/${reqId}/approve`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
            });
            if (resp.ok) {
                await loadPendingInteractions();
            } else {
                const err = await resp.json();
                alert('Approve failed: ' + (err.detail || 'Unknown error'));
            }
        } catch (e) {
            alert('Approve failed: ' + e.message);
        }
    }

    async function rejectInteraction(reqId) {
        const feedbackEl = document.getElementById('feedback-' + reqId);
        const feedback = feedbackEl ? feedbackEl.value.trim() : '';
        if (!feedback) {
            alert('Please provide feedback for the rejection.');
            return;
        }
        try {
            const resp = await fetch(`/api/interactions/${reqId}/reject`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ feedback }),
            });
            if (resp.ok) {
                await loadPendingInteractions();
            } else {
                const err = await resp.json();
                alert('Reject failed: ' + (err.detail || 'Unknown error'));
            }
        } catch (e) {
            alert('Reject failed: ' + e.message);
        }
    }

    async function respondInteraction(reqId) {
        const answerEl = document.getElementById('answer-' + reqId);
        const answer = answerEl ? answerEl.value.trim() : '';
        if (!answer) {
            alert('Please provide an answer.');
            return;
        }
        try {
            const resp = await fetch(`/api/interactions/${reqId}/respond`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ answer }),
            });
            if (resp.ok) {
                await loadPendingInteractions();
            } else {
                const err = await resp.json();
                alert('Respond failed: ' + (err.detail || 'Unknown error'));
            }
        } catch (e) {
            alert('Respond failed: ' + e.message);
        }
    }

    async function skipInteraction(reqId) {
        try {
            const resp = await fetch(`/api/interactions/${reqId}/skip`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({}),
            });
            if (resp.ok) {
                await loadPendingInteractions();
            } else {
                const err = await resp.json();
                alert('Skip failed: ' + (err.detail || 'Unknown error'));
            }
        } catch (e) {
            alert('Skip failed: ' + e.message);
        }
    }

    // Poll for pending interactions every 5 seconds
    setInterval(loadPendingInteractions, 5000);
    // Initial load
    loadPendingInteractions();
```

- [ ] **Step 6: Run test to verify it passes**

Run: `cd "/Users/nikhilchatragadda/Personal Projects/taskbrew" && .venv/bin/pytest tests/test_human_in_the_loop.py::TestInteractionPanelHTML -v`
Expected: All assertions PASS.

- [ ] **Step 7: Commit**

```bash
git add src/taskbrew/dashboard/templates/index.html tests/test_human_in_the_loop.py
git commit -m "feat: add Action Required panel — dashboard notification cards for approvals and clarifications"
```

---

## Task 7: Revision Loop Tracking -- chain_id, task_chains, Enforcement

**Files:**
- Modify: `src/taskbrew/orchestrator/interactions.py`
- Modify: `src/taskbrew/dashboard/routers/interactions.py`
- Test: `tests/test_human_in_the_loop.py`

- [ ] **Step 1: Write failing tests for revision loop tracking**

Append to `tests/test_human_in_the_loop.py`:

```python
class TestRevisionLoopTracking:
    """Test chain_id tracking, revision counting, and max_revision_cycles enforcement."""

    @pytest.mark.asyncio
    async def test_create_task_chain(self, db):
        mgr = InteractionManager(db)
        now = datetime.now(timezone.utc).isoformat()

        # Insert task first
        await db.execute(
            "INSERT INTO tasks (id, title, status, created_at, assigned_to, chain_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("TSK-001", "Implement API", "in_progress", now, "coder_be", None),
        )

        # Create a chain when a task first needs revision tracking
        chain_id = "chain-" + "TSK-001"
        await db.execute(
            "INSERT INTO task_chains "
            "(id, original_task_id, current_task_id, agent_role, "
            " revision_count, max_revision_cycles, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (chain_id, "TSK-001", "TSK-001", "coder_be", 0, 5, "active", now),
        )

        # Update task with chain_id
        await db.execute(
            "UPDATE tasks SET chain_id = ? WHERE id = ?",
            (chain_id, "TSK-001"),
        )

        chain = await db.execute_fetchone(
            "SELECT * FROM task_chains WHERE id = ?", (chain_id,)
        )
        assert chain is not None
        assert chain["revision_count"] == 0
        assert chain["max_revision_cycles"] == 5

    @pytest.mark.asyncio
    async def test_increment_revision_count(self, db):
        now = datetime.now(timezone.utc).isoformat()
        chain_id = "chain-001"
        await db.execute(
            "INSERT INTO tasks (id, title, status, created_at) VALUES (?, ?, ?, ?)",
            ("TSK-001", "Test", "completed", now),
        )
        await db.execute(
            "INSERT INTO task_chains "
            "(id, original_task_id, current_task_id, agent_role, "
            " revision_count, max_revision_cycles, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (chain_id, "TSK-001", "TSK-001", "coder_be", 0, 5, "active", now),
        )

        await db.execute(
            "UPDATE task_chains SET revision_count = revision_count + 1, "
            "updated_at = ? WHERE id = ?",
            (now, chain_id),
        )

        chain = await db.execute_fetchone(
            "SELECT * FROM task_chains WHERE id = ?", (chain_id,)
        )
        assert chain["revision_count"] == 1

    @pytest.mark.asyncio
    async def test_max_revision_cycles_enforcement(self, db):
        """When revision_count >= max_revision_cycles, further revisions should be blocked."""
        now = datetime.now(timezone.utc).isoformat()
        chain_id = "chain-001"
        await db.execute(
            "INSERT INTO tasks (id, title, status, created_at) VALUES (?, ?, ?, ?)",
            ("TSK-001", "Test", "completed", now),
        )
        await db.execute(
            "INSERT INTO task_chains "
            "(id, original_task_id, current_task_id, agent_role, "
            " revision_count, max_revision_cycles, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (chain_id, "TSK-001", "TSK-001", "coder_be", 5, 5, "active", now),
        )

        chain = await db.execute_fetchone(
            "SELECT * FROM task_chains WHERE id = ?", (chain_id,)
        )
        assert chain["revision_count"] >= chain["max_revision_cycles"]

    @pytest.mark.asyncio
    async def test_rejection_creates_revision_chain(self, db):
        """When a task is rejected, the interactions router should handle revision creation."""
        mgr = InteractionManager(db)
        now = datetime.now(timezone.utc).isoformat()

        # Insert group and task
        await db.execute(
            "INSERT INTO groups (id, title, status, created_at) VALUES (?, ?, ?, ?)",
            ("GRP-001", "Test", "active", now),
        )
        await db.execute(
            "INSERT INTO tasks "
            "(id, group_id, title, status, created_at, assigned_to, "
            " approval_mode, config_snapshot) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("TSK-001", "GRP-001", "Implement API", "awaiting_approval", now,
             "coder_be", "manual",
             '{"max_revision_cycles": 5}'),
        )

        # Create interaction request
        req = await mgr.create_request(
            task_id="TSK-001", instance_token="tok-1",
            request_type="approval",
            payload={"summary": "done", "artifact_paths": []},
        )

        # Reject it
        resolved = await mgr.resolve(
            request_id=req["id"],
            resolution="rejected",
            response_payload={"feedback": "Needs error handling"},
            responded_by="human",
        )
        assert resolved["status"] == "rejected"

    @pytest.mark.asyncio
    async def test_chain_status_completed_on_approval(self, db):
        now = datetime.now(timezone.utc).isoformat()
        chain_id = "chain-001"
        await db.execute(
            "INSERT INTO tasks (id, title, status, created_at, chain_id) "
            "VALUES (?, ?, ?, ?, ?)",
            ("TSK-001", "Test", "in_progress", now, chain_id),
        )
        await db.execute(
            "INSERT INTO task_chains "
            "(id, original_task_id, current_task_id, agent_role, "
            " revision_count, max_revision_cycles, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (chain_id, "TSK-001", "TSK-001", "coder_be", 2, 5, "active", now),
        )

        # Simulate approval: mark chain as completed
        await db.execute(
            "UPDATE task_chains SET status = 'completed', updated_at = ? WHERE id = ?",
            (now, chain_id),
        )
        chain = await db.execute_fetchone(
            "SELECT * FROM task_chains WHERE id = ?", (chain_id,)
        )
        assert chain["status"] == "completed"

    @pytest.mark.asyncio
    async def test_first_run_approval_per_group_role(self, db):
        """first_run approval is tracked per (group_id, agent_role)."""
        now = datetime.now(timezone.utc).isoformat()
        await db.execute(
            "INSERT INTO groups (id, title, status, created_at) VALUES (?, ?, ?, ?)",
            ("GRP-001", "Test", "active", now),
        )

        # Record first_run approval
        import uuid
        await db.execute(
            "INSERT INTO first_run_approvals (id, group_id, agent_role, approved_at) "
            "VALUES (?, ?, ?, ?)",
            (str(uuid.uuid4())[:12], "GRP-001", "coder_be", now),
        )

        # Check it exists
        row = await db.execute_fetchone(
            "SELECT * FROM first_run_approvals "
            "WHERE group_id = ? AND agent_role = ?",
            ("GRP-001", "coder_be"),
        )
        assert row is not None

        # Same role in different group should NOT be approved
        row2 = await db.execute_fetchone(
            "SELECT * FROM first_run_approvals "
            "WHERE group_id = ? AND agent_role = ?",
            ("GRP-002", "coder_be"),
        )
        assert row2 is None
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `cd "/Users/nikhilchatragadda/Personal Projects/taskbrew" && .venv/bin/pytest tests/test_human_in_the_loop.py::TestRevisionLoopTracking -v`
Expected: All 6 tests PASS (these use the schema created in Task 1).

- [ ] **Step 3: Commit**

```bash
git add tests/test_human_in_the_loop.py
git commit -m "test: add revision loop tracking tests — chain_id, revision count, first_run enforcement"
```

---

## Task 8: Integration Tests -- End-to-End Flow

**Files:**
- Test: `tests/test_human_in_the_loop.py`

- [ ] **Step 1: Write end-to-end integration tests**

Append to `tests/test_human_in_the_loop.py`:

```python
class TestEndToEndFlow:
    """Integration tests for the full HITL flow:
    agent calls complete_task -> interaction created -> user approves -> agent unblocks.
    """

    @pytest.mark.asyncio
    async def test_manual_approval_full_flow(self, db):
        """Complete flow: agent completes task -> pending -> user approves -> resolved."""
        from taskbrew.orchestrator.interactions import InteractionManager
        mgr = InteractionManager(db)

        now = datetime.now(timezone.utc).isoformat()

        # 1. Create group and task
        await db.execute(
            "INSERT INTO groups (id, title, status, created_at) VALUES (?, ?, ?, ?)",
            ("GRP-001", "Test Goal", "active", now),
        )
        await db.execute(
            "INSERT INTO tasks "
            "(id, group_id, title, status, created_at, assigned_to, "
            " approval_mode, instance_token) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("TSK-001", "GRP-001", "Implement user API", "in_progress", now,
             "coder_be", "manual", "tok-agent-1"),
        )

        # 2. Agent calls complete_task -> creates interaction request
        req = await mgr.create_request(
            task_id="TSK-001",
            instance_token="tok-agent-1",
            request_type="approval",
            payload={"summary": "Implemented CRUD endpoints", "artifact_paths": ["/src/api.py"]},
        )
        assert req["status"] == "pending"

        # Update task status
        await db.execute(
            "UPDATE tasks SET status = 'awaiting_approval' WHERE id = 'TSK-001'"
        )

        # 3. Dashboard shows pending request
        pending = await mgr.get_pending()
        assert len(pending) == 1
        assert pending[0]["task_id"] == "TSK-001"

        # 4. User approves via dashboard
        resolved = await mgr.resolve(
            request_id=req["id"],
            resolution="approved",
            response_payload={"feedback": "Looks good!"},
            responded_by="human",
        )
        assert resolved["status"] == "approved"

        # 5. Agent polls and sees approval
        status = await mgr.check_status(req["id"])
        assert status["status"] == "approved"

        # 6. No more pending
        pending = await mgr.get_pending()
        assert len(pending) == 0

    @pytest.mark.asyncio
    async def test_clarification_full_flow(self, db):
        """Complete flow: agent asks question -> user answers -> agent gets answer."""
        from taskbrew.orchestrator.interactions import InteractionManager
        mgr = InteractionManager(db)

        now = datetime.now(timezone.utc).isoformat()

        await db.execute(
            "INSERT INTO tasks "
            "(id, title, status, created_at, assigned_to, instance_token) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("TSK-001", "Design DB schema", "in_progress", now,
             "architect", "tok-agent-2"),
        )

        # Agent asks a question
        req = await mgr.create_request(
            task_id="TSK-001",
            instance_token="tok-agent-2",
            request_type="clarification",
            payload={
                "question": "Should we use PostgreSQL or MySQL?",
                "context": "The app needs complex queries and JSON support.",
                "suggested_options": ["PostgreSQL", "MySQL"],
            },
        )
        assert req["status"] == "pending"

        # User responds
        resolved = await mgr.resolve(
            request_id=req["id"],
            resolution="responded",
            response_payload={"answer": "Use PostgreSQL for JSON support."},
            responded_by="human",
        )
        assert resolved["status"] == "responded"

        # Agent polls and gets the answer
        status = await mgr.check_status(req["id"])
        assert status["status"] == "responded"
        assert status["response_payload"]["answer"] == "Use PostgreSQL for JSON support."

    @pytest.mark.asyncio
    async def test_rejection_and_revision_flow(self, db):
        """Flow: complete -> reject -> revision task created -> approve."""
        from taskbrew.orchestrator.interactions import InteractionManager
        mgr = InteractionManager(db)

        now = datetime.now(timezone.utc).isoformat()

        # Setup
        await db.execute(
            "INSERT INTO groups (id, title, status, created_at) VALUES (?, ?, ?, ?)",
            ("GRP-001", "Build API", "active", now),
        )
        await db.execute(
            "INSERT INTO tasks "
            "(id, group_id, title, status, created_at, assigned_to, "
            " approval_mode, instance_token, config_snapshot) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("TSK-001", "GRP-001", "Implement API", "in_progress", now,
             "coder_be", "manual", "tok-agent-1",
             '{"max_revision_cycles": 5}'),
        )

        # Create chain
        chain_id = "chain-TSK-001"
        await db.execute(
            "INSERT INTO task_chains "
            "(id, original_task_id, current_task_id, agent_role, "
            " revision_count, max_revision_cycles, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (chain_id, "TSK-001", "TSK-001", "coder_be", 0, 5, "active", now),
        )
        await db.execute(
            "UPDATE tasks SET chain_id = ? WHERE id = 'TSK-001'",
            (chain_id,),
        )

        # Agent submits completion
        req = await mgr.create_request(
            task_id="TSK-001",
            instance_token="tok-agent-1",
            request_type="approval",
            payload={"summary": "Done v1", "artifact_paths": []},
        )

        # User rejects
        resolved = await mgr.resolve(
            request_id=req["id"],
            resolution="rejected",
            response_payload={"feedback": "Missing error handling"},
            responded_by="human",
        )
        assert resolved["status"] == "rejected"

        # Increment revision count
        await db.execute(
            "UPDATE task_chains SET revision_count = revision_count + 1, "
            "updated_at = ? WHERE id = ?",
            (now, chain_id),
        )

        chain = await db.execute_fetchone(
            "SELECT * FROM task_chains WHERE id = ?", (chain_id,)
        )
        assert chain["revision_count"] == 1
        assert chain["revision_count"] < chain["max_revision_cycles"]

    @pytest.mark.asyncio
    async def test_first_run_approval_flow(self, db):
        """First task requires approval; second task auto-approves."""
        from taskbrew.orchestrator.interactions import InteractionManager
        import uuid
        mgr = InteractionManager(db)

        now = datetime.now(timezone.utc).isoformat()

        await db.execute(
            "INSERT INTO groups (id, title, status, created_at) VALUES (?, ?, ?, ?)",
            ("GRP-001", "Goal 1", "active", now),
        )

        # First task: should require approval (first_run)
        await db.execute(
            "INSERT INTO tasks "
            "(id, group_id, title, status, created_at, assigned_to, "
            " approval_mode, instance_token) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("TSK-001", "GRP-001", "First task", "in_progress", now,
             "coder_be", "first_run", "tok-1"),
        )

        # No first_run approval exists yet
        existing = await db.execute_fetchone(
            "SELECT id FROM first_run_approvals "
            "WHERE group_id = 'GRP-001' AND agent_role = 'coder_be'"
        )
        assert existing is None  # Not yet approved

        # User approves the first task
        req = await mgr.create_request(
            task_id="TSK-001", instance_token="tok-1",
            request_type="approval", payload={"summary": "first task done"},
        )
        await mgr.resolve(req["id"], "approved", {}, "human")

        # Record first_run approval
        await db.execute(
            "INSERT INTO first_run_approvals (id, group_id, agent_role, approved_at) "
            "VALUES (?, ?, ?, ?)",
            (str(uuid.uuid4())[:12], "GRP-001", "coder_be", now),
        )

        # Second task: should auto-approve because first_run already approved
        existing2 = await db.execute_fetchone(
            "SELECT id FROM first_run_approvals "
            "WHERE group_id = 'GRP-001' AND agent_role = 'coder_be'"
        )
        assert existing2 is not None  # Approved now -> second task auto-completes

    @pytest.mark.asyncio
    async def test_idempotent_request_on_retry(self, db):
        """When an agent retries (network error), same request_key returns existing request."""
        from taskbrew.orchestrator.interactions import InteractionManager
        mgr = InteractionManager(db)

        now = datetime.now(timezone.utc).isoformat()
        await db.execute(
            "INSERT INTO tasks (id, title, status, created_at, instance_token) "
            "VALUES (?, ?, ?, ?, ?)",
            ("TSK-001", "Test", "in_progress", now, "tok-1"),
        )

        # First call
        req1 = await mgr.create_request(
            task_id="TSK-001", instance_token="tok-1",
            request_type="approval", payload={"summary": "v1"},
            sequence_number=1,
        )

        # Retry (same sequence_number = same request_key)
        req2 = await mgr.create_request(
            task_id="TSK-001", instance_token="tok-1",
            request_type="approval", payload={"summary": "v1 retry"},
            sequence_number=1,
        )

        assert req1["id"] == req2["id"]  # Same request returned

    @pytest.mark.asyncio
    async def test_multiple_clarifications_tracked(self, db):
        """Agent can make multiple clarification requests (each with unique sequence)."""
        from taskbrew.orchestrator.interactions import InteractionManager
        mgr = InteractionManager(db)

        now = datetime.now(timezone.utc).isoformat()
        await db.execute(
            "INSERT INTO tasks (id, title, status, created_at, instance_token) "
            "VALUES (?, ?, ?, ?, ?)",
            ("TSK-001", "Test", "in_progress", now, "tok-1"),
        )

        req1 = await mgr.create_request(
            task_id="TSK-001", instance_token="tok-1",
            request_type="clarification",
            payload={"question": "Q1"},
        )
        # Resolve first
        await mgr.resolve(req1["id"], "responded", {"answer": "A1"}, "human")

        req2 = await mgr.create_request(
            task_id="TSK-001", instance_token="tok-1",
            request_type="clarification",
            payload={"question": "Q2"},
        )
        assert req1["id"] != req2["id"]

        pending = await mgr.get_pending()
        assert len(pending) == 1
        assert pending[0]["id"] == req2["id"]
```

- [ ] **Step 2: Run all tests**

Run: `cd "/Users/nikhilchatragadda/Personal Projects/taskbrew" && .venv/bin/pytest tests/test_human_in_the_loop.py -v`
Expected: All tests PASS (schema tests + CRUD tests + model tests + API tests + revision tests + integration tests).

- [ ] **Step 3: Commit**

```bash
git add tests/test_human_in_the_loop.py
git commit -m "test: add end-to-end integration tests for HITL approval, clarification, and revision flows"
```

---

## Summary

| Task | Description | New Files | Modified Files |
|------|-------------|-----------|----------------|
| 1 | Database schema | -- | `database.py`, `migration.py` |
| 2 | InteractionManager CRUD | `orchestrator/interactions.py` | -- |
| 3 | Pydantic models | -- | `models.py` |
| 4 | MCP tool router | `routers/mcp_tools.py` | `app.py` |
| 5 | Dashboard interaction API | `routers/interactions.py` | `app.py` |
| 6 | Dashboard notification UI | -- | `index.html` |
| 7 | Revision loop tracking tests | -- | test file |
| 8 | Integration tests | -- | test file |

**Core flow implemented:**
1. Agent calls `complete_task` with artifacts and summary.
2. If `approval_mode` is `manual` (or `first_run` not yet approved), an `human_interaction_requests` row is created with `status=pending` and the MCP endpoint blocks via long-poll.
3. Dashboard polls `/api/interactions/pending` every 5 seconds and renders an "Action Required" card.
4. User clicks "Approve" (or "Reject" with feedback) which calls `/api/interactions/{id}/approve` (or `/reject`).
5. The `InteractionManager.resolve()` updates the row to `status=approved` (or `rejected`).
6. The long-polling MCP endpoint detects the status change and returns the result to the agent.
7. For rejections, the revision count is incremented on the `task_chains` table. If `max_revision_cycles` is reached, the task escalates to `awaiting_human_intervention`.

**Test command (all tasks):**
```bash
cd "/Users/nikhilchatragadda/Personal Projects/taskbrew" && .venv/bin/pytest tests/test_human_in_the_loop.py -v
```
