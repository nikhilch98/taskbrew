# Backlog Review System Gates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add visible Backlog and Review task columns backed by one-time backlog intake and async
system-agent review gates.

**Architecture:** Store gate state on task rows, keep normal agents claimable only from `pending`,
and add a focused `SystemGateManager` that processes backlog and review work outside ordinary role
queues. The manager uses an injectable analyzer so tests can be deterministic while production uses
the locked per-project system agent profile.

**Tech Stack:** Python 3.10+, SQLite/aiosqlite, FastAPI, vanilla dashboard JavaScript/CSS, pytest,
pytest-asyncio.

---

## Scope Check

This is one cohesive workflow feature. It touches persistence, task transitions, async orchestration,
and dashboard rendering, but each piece is independently testable and builds toward the same visible
Backlog/Review behavior.

## File Structure

- Modify `src/taskbrew/orchestrator/database.py`
  Baseline task schema for new database files.
- Modify `src/taskbrew/orchestrator/migration.py`
  Upgrade existing database files with migration 34.
- Modify `src/taskbrew/orchestrator/task_board.py`
  Domain transitions for backlog intake, review routing, revision dependencies, and gate audit data.
- Create `src/taskbrew/orchestrator/system_gates.py`
  System gate dataclasses, analyzer abstraction, production analyzer, and async manager.
- Modify `src/taskbrew/system_agent.py`
  Extend the locked prompt with backlog/review gate constraints.
- Modify `src/taskbrew/main.py`
  Attach and start the system gate manager.
- Modify `src/taskbrew/dashboard/routers/tasks.py`
  Allow new statuses and expose task gate metadata in API responses.
- Modify `src/taskbrew/dashboard/templates/index.html`
  Add visible Backlog/Review columns, filters, details, card tags, and inline board logic.
- Modify `src/taskbrew/dashboard/static/js/dashboard-core.js`
  Keep the extracted dashboard script in sync with the inline template logic.
- Modify `src/taskbrew/dashboard/static/css/main.css`
  Add Backlog/Review accent and decision styles.
- Create `tests/test_task_board_system_gates.py`
  Domain tests for schema, creation, intake, review routing, rejection, and revision blocking.
- Create `tests/test_system_gates.py`
  Manager/analyzer tests with a deterministic fake analyzer.
- Modify `tests/test_task_board.py`, `tests/test_task_board_advanced.py`, and
  `tests/test_dashboard_api.py`
  Update existing expectations that new tasks are now born in `backlog`.

---

### Task 1: Schema And Constants

**Files:**
- Modify: `src/taskbrew/orchestrator/database.py`
- Modify: `src/taskbrew/orchestrator/migration.py`
- Modify: `src/taskbrew/orchestrator/task_board.py`
- Create: `tests/test_task_board_system_gates.py`

- [ ] **Step 1: Write the failing schema test**

Add this initial content to `tests/test_task_board_system_gates.py`:

```python
"""Tests for Backlog and Review system-gate task workflow."""

from __future__ import annotations

import json

import pytest

from taskbrew.orchestrator.database import Database
from taskbrew.orchestrator.task_board import TaskBoard


class RecordingEventBus:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    async def emit(self, event_type: str, data: dict) -> None:
        self.events.append((event_type, data))


@pytest.fixture
async def db():
    database = Database(":memory:")
    await database.initialize()
    yield database
    await database.close()


@pytest.fixture
async def event_bus():
    return RecordingEventBus()


@pytest.fixture
async def board(db: Database, event_bus: RecordingEventBus) -> TaskBoard:
    task_board = TaskBoard(
        db,
        group_prefixes={"pm": "FEAT", "architect": "DEBT"},
        event_bus=event_bus,
    )
    await task_board.register_prefixes(
        {
            "pm": "PM",
            "architect": "AR",
            "coder": "CD",
            "tester": "TS",
            "reviewer": "RV",
        }
    )
    return task_board


async def test_task_schema_has_system_gate_columns(db: Database):
    columns = {
        row["name"]: row
        for row in await db.execute_fetchall("PRAGMA table_info(tasks)")
    }

    expected = {
        "intended_status",
        "needs_review",
        "needs_review_reason",
        "needs_review_decision",
        "backlog_intake_status",
        "backlog_intake_processed_at",
        "review_status",
        "review_round",
        "max_review_rounds",
        "review_parent_task_id",
        "revision_task_ids",
        "system_gate_runs",
    }
    assert expected.issubset(columns.keys())
```

- [ ] **Step 2: Run the schema test and verify it fails**

Run:

```bash
pytest tests/test_task_board_system_gates.py::test_task_schema_has_system_gate_columns -v
```

Expected: FAIL because the new task columns do not exist.

- [ ] **Step 3: Add task status constants**

In `src/taskbrew/orchestrator/task_board.py`, below `_PRIORITY_ORDER`, add:

```python
BOARD_STATUSES = (
    "backlog",
    "pending",
    "in_progress",
    "review",
    "blocked",
    "completed",
    "rejected",
    "failed",
)

TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled", "rejected"})
CLAIMABLE_STATUS = "pending"
BACKLOG_STATUS = "backlog"
REVIEW_STATUS = "review"
DEFAULT_MAX_REVIEW_ROUNDS = 3
```

- [ ] **Step 4: Extend the baseline task schema**

In `src/taskbrew/orchestrator/database.py`, add these columns to the `tasks` table after
`awaiting_input_since TEXT`:

```sql
    ,
    -- System gate workflow fields (migration 34).
    intended_status              TEXT DEFAULT 'pending',
    needs_review                 INTEGER,
    needs_review_reason          TEXT,
    needs_review_decision        TEXT,
    backlog_intake_status        TEXT DEFAULT 'pending',
    backlog_intake_processed_at  TEXT,
    review_status                TEXT,
    review_round                 INTEGER DEFAULT 0,
    max_review_rounds            INTEGER DEFAULT 3,
    review_parent_task_id        TEXT REFERENCES tasks(id),
    revision_task_ids            TEXT DEFAULT '[]',
    system_gate_runs             TEXT DEFAULT '[]'
```

Keep the comma placement valid for the surrounding SQL block. The final column before the closing
parenthesis should not have a trailing comma.

- [ ] **Step 5: Add migration 34**

In `src/taskbrew/orchestrator/migration.py`, append this migration after version 33:

```python
    (34, "add_backlog_review_system_gate_columns", """
        ALTER TABLE tasks ADD COLUMN intended_status TEXT DEFAULT 'pending';
        ALTER TABLE tasks ADD COLUMN needs_review INTEGER;
        ALTER TABLE tasks ADD COLUMN needs_review_reason TEXT;
        ALTER TABLE tasks ADD COLUMN needs_review_decision TEXT;
        ALTER TABLE tasks ADD COLUMN backlog_intake_status TEXT DEFAULT 'pending';
        ALTER TABLE tasks ADD COLUMN backlog_intake_processed_at TEXT;
        ALTER TABLE tasks ADD COLUMN review_status TEXT;
        ALTER TABLE tasks ADD COLUMN review_round INTEGER DEFAULT 0;
        ALTER TABLE tasks ADD COLUMN max_review_rounds INTEGER DEFAULT 3;
        ALTER TABLE tasks ADD COLUMN review_parent_task_id TEXT REFERENCES tasks(id);
        ALTER TABLE tasks ADD COLUMN revision_task_ids TEXT DEFAULT '[]';
        ALTER TABLE tasks ADD COLUMN system_gate_runs TEXT DEFAULT '[]';

        CREATE INDEX IF NOT EXISTS idx_tasks_backlog_intake
            ON tasks(status, backlog_intake_status, created_at);
        CREATE INDEX IF NOT EXISTS idx_tasks_review_gate
            ON tasks(status, review_status, created_at);
        CREATE INDEX IF NOT EXISTS idx_tasks_review_parent
            ON tasks(review_parent_task_id);
    """),
```

- [ ] **Step 6: Run the schema test and verify it passes**

Run:

```bash
pytest tests/test_task_board_system_gates.py::test_task_schema_has_system_gate_columns -v
```

Expected: PASS.

- [ ] **Step 7: Commit schema work**

```bash
git add src/taskbrew/orchestrator/database.py \
  src/taskbrew/orchestrator/migration.py \
  src/taskbrew/orchestrator/task_board.py \
  tests/test_task_board_system_gates.py
git commit -m "feat: add system gate task schema"
```

---

### Task 2: Backlog Creation And Intake Transition

**Files:**
- Modify: `src/taskbrew/orchestrator/task_board.py`
- Modify: `tests/test_task_board_system_gates.py`

- [ ] **Step 1: Write failing backlog creation tests**

Append these tests to `tests/test_task_board_system_gates.py`:

```python
async def test_create_task_starts_in_backlog_with_pending_intent(
    board: TaskBoard,
    event_bus: RecordingEventBus,
):
    group = await board.create_group(title="Feature", created_by="pm")

    task = await board.create_task(
        group_id=group["id"],
        title="Build widget",
        task_type="implementation",
        assigned_to="coder",
        created_by="pm",
    )

    assert task["status"] == "backlog"
    assert task["intended_status"] == "pending"
    assert task["backlog_intake_status"] == "pending"
    assert task["needs_review"] is None
    assert event_bus.events == []

    claimed = await board.claim_task("coder", "coder-1")
    assert claimed is None


async def test_create_blocked_task_starts_in_backlog_with_blocked_intent(
    board: TaskBoard,
):
    group = await board.create_group(title="Feature", created_by="pm")
    blocker = await board.create_task(
        group_id=group["id"],
        title="Blocking work",
        task_type="implementation",
        assigned_to="coder",
    )

    blocked = await board.create_task(
        group_id=group["id"],
        title="Blocked work",
        task_type="qa_verification",
        assigned_to="tester",
        blocked_by=[blocker["id"]],
    )

    assert blocked["status"] == "backlog"
    assert blocked["intended_status"] == "blocked"
    deps = await board._db.execute_fetchall(
        "SELECT blocked_by, resolved FROM task_dependencies WHERE task_id = ?",
        (blocked["id"],),
    )
    assert deps == [{"blocked_by": blocker["id"], "resolved": 0}]
```

- [ ] **Step 2: Run the backlog creation tests and verify they fail**

Run:

```bash
pytest tests/test_task_board_system_gates.py::test_create_task_starts_in_backlog_with_pending_intent \
  tests/test_task_board_system_gates.py::test_create_blocked_task_starts_in_backlog_with_blocked_intent -v
```

Expected: FAIL because `create_task` still returns `pending` or `blocked`.

- [ ] **Step 3: Change task creation to always start in backlog**

In `TaskBoard.create_task`, replace:

```python
status = "blocked" if blocked_by else "pending"
```

with:

```python
intended_status = "blocked" if blocked_by else "pending"
status = BACKLOG_STATUS
```

Extend the `INSERT INTO tasks` column list with:

```python
" intended_status, backlog_intake_status, max_review_rounds) "
```

and extend the values with:

```python
intended_status,
"pending",
DEFAULT_MAX_REVIEW_ROUNDS,
```

Remove the existing `task.available` emit from `create_task`; backlog tasks are not claimable until
the intake gate moves them to `pending`.

Add these fields to the returned task dict:

```python
"intended_status": intended_status,
"needs_review": None,
"needs_review_reason": None,
"needs_review_decision": None,
"backlog_intake_status": "pending",
"backlog_intake_processed_at": None,
"review_status": None,
"review_round": 0,
"max_review_rounds": DEFAULT_MAX_REVIEW_ROUNDS,
"review_parent_task_id": None,
"revision_task_ids": [],
"system_gate_runs": [],
```

- [ ] **Step 4: Add helpers for JSON and dependency state**

In `TaskBoard`, add these private helpers near the task methods:

```python
def _json_list(self, value) -> list:
    if value in (None, ""):
        return []
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []


async def _has_unresolved_dependencies(self, task_id: str) -> bool:
    row = await self._db.execute_fetchone(
        "SELECT 1 FROM task_dependencies "
        "WHERE task_id = ? AND resolved = 0 LIMIT 1",
        (task_id,),
    )
    return row is not None


async def _target_status_after_intake(self, task_id: str, intended_status: str) -> str:
    if await self._has_unresolved_dependencies(task_id):
        return "blocked"
    return "pending" if intended_status in ("pending", "blocked") else intended_status
```

- [ ] **Step 5: Run the backlog creation tests and verify they pass**

Run:

```bash
pytest tests/test_task_board_system_gates.py::test_create_task_starts_in_backlog_with_pending_intent \
  tests/test_task_board_system_gates.py::test_create_blocked_task_starts_in_backlog_with_blocked_intent -v
```

Expected: PASS.

- [ ] **Step 6: Write failing backlog intake transition tests**

Append:

```python
async def test_apply_backlog_intake_decision_moves_pending_task_and_emits_available(
    board: TaskBoard,
    event_bus: RecordingEventBus,
):
    group = await board.create_group(title="Feature", created_by="pm")
    task = await board.create_task(
        group_id=group["id"],
        title="Build widget",
        task_type="implementation",
        assigned_to="coder",
    )

    updated = await board.apply_backlog_intake_decision(
        task["id"],
        needs_review=True,
        reason="Touches shared orchestration logic.",
        signals=["shared_orchestration_logic"],
        confidence="medium",
    )

    assert updated["status"] == "pending"
    assert updated["needs_review"] == 1
    assert updated["needs_review_reason"] == "Touches shared orchestration logic."
    decision = json.loads(updated["needs_review_decision"])
    assert decision["decision"] is True
    assert decision["signals"] == ["shared_orchestration_logic"]
    assert updated["backlog_intake_status"] == "completed"
    assert event_bus.events[-1] == (
        "task.available",
        {"task_id": task["id"], "role": "coder", "group_id": group["id"]},
    )


async def test_apply_backlog_intake_decision_runs_once(board: TaskBoard):
    group = await board.create_group(title="Feature", created_by="pm")
    task = await board.create_task(
        group_id=group["id"],
        title="Build widget",
        task_type="implementation",
        assigned_to="coder",
    )

    first = await board.apply_backlog_intake_decision(
        task["id"],
        needs_review=False,
        reason="Small docs-only change.",
    )
    second = await board.apply_backlog_intake_decision(
        task["id"],
        needs_review=True,
        reason="Second decision must not overwrite the first.",
    )

    assert first["status"] == "pending"
    assert second["status"] == "pending"
    assert second["needs_review"] == 0
    assert second["needs_review_reason"] == "Small docs-only change."
```

- [ ] **Step 7: Run the intake tests and verify they fail**

Run:

```bash
pytest tests/test_task_board_system_gates.py -k "apply_backlog_intake_decision" -v
```

Expected: FAIL because `apply_backlog_intake_decision` does not exist.

- [ ] **Step 8: Implement backlog intake decision methods**

Add this public method to `TaskBoard`:

```python
async def apply_backlog_intake_decision(
    self,
    task_id: str,
    *,
    needs_review: bool,
    reason: str,
    signals: list[str] | None = None,
    confidence: str = "medium",
) -> dict:
    """Store the one-time backlog intake decision and move to intended state."""
    task = await self.get_task(task_id)
    if task is None:
        raise ValueError(f"Task not found: {task_id}")
    if task["status"] != BACKLOG_STATUS:
        return task
    if task.get("backlog_intake_status") == "completed":
        return task

    now = _utcnow()
    decision = {
        "decision": bool(needs_review),
        "reason": reason,
        "signals": signals or [],
        "confidence": confidence,
        "decided_by": "system_agent",
        "decided_at": now,
    }
    intended_status = task.get("intended_status") or "pending"
    target_status = await self._target_status_after_intake(task_id, intended_status)
    rows = await self._db.execute_returning(
        "UPDATE tasks SET status = ?, needs_review = ?, needs_review_reason = ?, "
        "needs_review_decision = ?, backlog_intake_status = 'completed', "
        "backlog_intake_processed_at = ? "
        "WHERE id = ? AND status = 'backlog' "
        "AND COALESCE(backlog_intake_status, 'pending') != 'completed' RETURNING *",
        (
            target_status,
            1 if needs_review else 0,
            reason,
            json.dumps(decision),
            now,
            task_id,
        ),
    )
    updated = rows[0] if rows else await self.get_task(task_id)
    if (
        updated
        and self._event_bus is not None
        and updated["status"] == CLAIMABLE_STATUS
    ):
        await self._event_bus.emit(
            "task.available",
            {
                "task_id": updated["id"],
                "role": updated["assigned_to"],
                "group_id": updated["group_id"],
            },
        )
    return updated
```

Also add:

```python
async def mark_backlog_intake_failed(self, task_id: str, error: str) -> dict:
    now = _utcnow()
    rows = await self._db.execute_returning(
        "UPDATE tasks SET backlog_intake_status = 'failed', "
        "needs_review_reason = ? "
        "WHERE id = ? AND status = 'backlog' RETURNING *",
        (f"System backlog intake failed at {now}: {error[:500]}", task_id),
    )
    if not rows:
        task = await self.get_task(task_id)
        if task is None:
            raise ValueError(f"Task not found: {task_id}")
        return task
    return rows[0]
```

- [ ] **Step 9: Run the system-gate domain tests**

Run:

```bash
pytest tests/test_task_board_system_gates.py -v
```

Expected: PASS.

- [ ] **Step 10: Commit backlog creation and intake**

```bash
git add src/taskbrew/orchestrator/task_board.py tests/test_task_board_system_gates.py
git commit -m "feat: route new tasks through backlog intake"
```

---

### Task 3: Completion Routing And Review Domain Methods

**Files:**
- Modify: `src/taskbrew/orchestrator/task_board.py`
- Modify: `tests/test_task_board_system_gates.py`

- [ ] **Step 1: Write failing completion routing tests**

Append:

```python
async def test_complete_needs_review_task_moves_to_review(board: TaskBoard):
    group = await board.create_group(title="Feature", created_by="pm")
    task = await board.create_task(
        group_id=group["id"],
        title="Risky implementation",
        task_type="implementation",
        assigned_to="coder",
    )
    await board.apply_backlog_intake_decision(
        task["id"],
        needs_review=True,
        reason="Touches shared state transitions.",
    )
    await board.claim_task("coder", "coder-1")

    result = await board.complete_task_with_output(task["id"], "done")

    assert result["status"] == "review"
    assert result["review_status"] == "pending"
    assert result["output_text"] == "done"


async def test_complete_no_review_task_finishes_directly(board: TaskBoard):
    group = await board.create_group(title="Feature", created_by="pm")
    task = await board.create_task(
        group_id=group["id"],
        title="Simple task",
        task_type="documentation",
        assigned_to="coder",
    )
    await board.apply_backlog_intake_decision(
        task["id"],
        needs_review=False,
        reason="Small docs-only task.",
    )
    await board.claim_task("coder", "coder-1")

    result = await board.complete_task(task["id"])

    assert result["status"] == "completed"
    assert result["review_status"] is None
```

- [ ] **Step 2: Run the completion routing tests and verify they fail**

Run:

```bash
pytest tests/test_task_board_system_gates.py::test_complete_needs_review_task_moves_to_review \
  tests/test_task_board_system_gates.py::test_complete_no_review_task_finishes_directly -v
```

Expected: FAIL because completion still always writes `completed`.

- [ ] **Step 3: Add completion target helper**

In `TaskBoard`, add:

```python
def _completion_status_for(self, task: dict) -> tuple[str, str | None]:
    needs_review = task.get("needs_review")
    review_parent = task.get("review_parent_task_id")
    if needs_review in (1, True) and not review_parent:
        return REVIEW_STATUS, "pending"
    return "completed", None
```

- [ ] **Step 4: Update `complete_task`**

Replace the first `UPDATE` in `complete_task` with a fetch-then-update flow:

```python
task = await self.get_task(task_id)
if task is None:
    raise ValueError(f"Task not found: {task_id}")
if task["status"] != "in_progress":
    logger.warning(
        "complete_task(%s) skipped: task is in status '%s', expected 'in_progress'",
        task_id,
        task["status"],
    )
    return task

now = _utcnow()
target_status, review_status = self._completion_status_for(task)
rows = await self._db.execute_returning(
    "UPDATE tasks SET status = ?, completed_at = ?, review_status = ? "
    "WHERE id = ? AND status = 'in_progress' RETURNING *",
    (target_status, now, review_status, task_id),
)
```

Keep the existing `_resolve_dependencies`, `_check_group_completion`, and logging after the update.

- [ ] **Step 5: Update `complete_task_with_output`**

Use the same `target_status` logic in `complete_task_with_output`. The main update should become:

```python
task = await self.get_task(task_id)
if task is None:
    raise ValueError(f"Task not found: {task_id}")
target_status, review_status = self._completion_status_for(task)
rows = await self._db.execute_returning(
    "UPDATE tasks SET status = ?, completed_at = ?, output_text = ?, "
    "review_status = ? "
    "WHERE id = ? AND status = 'in_progress' RETURNING *",
    (target_status, now, persisted_output, review_status, task_id),
)
```

Preserve the existing idempotent branch for rows already in `completed`. Add a parallel branch for
rows already in `review`:

```python
if existing["status"] in ("completed", "review"):
    next_output = persisted_output or existing.get("output_text") or ""
    completed_rows = await self._db.execute_returning(
        "UPDATE tasks SET output_text = ? WHERE id = ? RETURNING *",
        (next_output, task_id),
    )
    return completed_rows[0] if completed_rows else existing
```

- [ ] **Step 6: Write failing review outcome tests**

Append:

```python
async def test_approve_review_gate_moves_to_completed(board: TaskBoard):
    group = await board.create_group(title="Feature", created_by="pm")
    task = await board.create_task(
        group_id=group["id"],
        title="Risky implementation",
        task_type="implementation",
        assigned_to="coder",
    )
    await board.apply_backlog_intake_decision(
        task["id"],
        needs_review=True,
        reason="Risky task.",
    )
    await board.claim_task("coder", "coder-1")
    await board.complete_task(task["id"])

    result = await board.approve_review_gate(task["id"], reason="Looks correct.")

    assert result["status"] == "completed"
    assert result["review_status"] == "approved"


async def test_reject_review_gate_stores_visible_reason(board: TaskBoard):
    group = await board.create_group(title="Feature", created_by="pm")
    task = await board.create_task(
        group_id=group["id"],
        title="Invalid task",
        task_type="implementation",
        assigned_to="coder",
    )
    await board.apply_backlog_intake_decision(
        task["id"],
        needs_review=True,
        reason="Risky task.",
    )
    await board.claim_task("coder", "coder-1")
    await board.complete_task(task["id"])

    result = await board.reject_review_gate(
        task["id"],
        reason="Task is obsolete and should not continue.",
    )

    assert result["status"] == "rejected"
    assert result["review_status"] == "rejected"
    assert result["rejection_reason"] == "Task is obsolete and should not continue."
```

- [ ] **Step 7: Run review outcome tests and verify they fail**

Run:

```bash
pytest tests/test_task_board_system_gates.py::test_approve_review_gate_moves_to_completed \
  tests/test_task_board_system_gates.py::test_reject_review_gate_stores_visible_reason -v
```

Expected: FAIL because the review outcome methods do not exist.

- [ ] **Step 8: Implement review outcome methods**

Add:

```python
async def approve_review_gate(self, task_id: str, *, reason: str) -> dict:
    now = _utcnow()
    rows = await self._db.execute_returning(
        "UPDATE tasks SET status = 'completed', review_status = 'approved', "
        "rejection_reason = NULL "
        "WHERE id = ? AND status = 'review' RETURNING *",
        (task_id,),
    )
    if not rows:
        task = await self.get_task(task_id)
        if task is None:
            raise ValueError(f"Task not found: {task_id}")
        return task
    await self._append_system_gate_run(
        task_id,
        {
            "gate": "review",
            "outcome": "approved",
            "reason": reason,
            "finished_at": now,
        },
    )
    await self._resolve_dependencies(task_id)
    await self._check_group_completion(task_id)
    return await self.get_task(task_id)


async def reject_review_gate(self, task_id: str, *, reason: str) -> dict:
    now = _utcnow()
    rows = await self._db.execute_returning(
        "UPDATE tasks SET status = 'rejected', review_status = 'rejected', "
        "rejection_reason = ? WHERE id = ? AND status = 'review' RETURNING *",
        (reason, task_id),
    )
    if not rows:
        task = await self.get_task(task_id)
        if task is None:
            raise ValueError(f"Task not found: {task_id}")
        return task
    await self._append_system_gate_run(
        task_id,
        {
            "gate": "review",
            "outcome": "rejected",
            "reason": reason,
            "finished_at": now,
        },
    )
    await self._cascade_failure(task_id)
    await self._check_group_completion(task_id)
    return await self.get_task(task_id)
```

Add the audit helper:

```python
async def _append_system_gate_run(self, task_id: str, entry: dict) -> None:
    task = await self.get_task(task_id)
    if task is None:
        raise ValueError(f"Task not found: {task_id}")
    runs = self._json_list(task.get("system_gate_runs"))
    runs.append(entry)
    await self._db.execute(
        "UPDATE tasks SET system_gate_runs = ? WHERE id = ?",
        (json.dumps(runs), task_id),
    )
```

- [ ] **Step 9: Make rejected terminal for group completion**

In `_check_group_completion`, replace:

```python
"AND status NOT IN ('completed', 'failed', 'cancelled') LIMIT 1",
```

with:

```python
"AND status NOT IN ('completed', 'failed', 'cancelled', 'rejected') LIMIT 1",
```

- [ ] **Step 10: Run system-gate domain tests**

Run:

```bash
pytest tests/test_task_board_system_gates.py -v
```

Expected: PASS.

- [ ] **Step 11: Commit completion routing**

```bash
git add src/taskbrew/orchestrator/task_board.py tests/test_task_board_system_gates.py
git commit -m "feat: route reviewed completions through review gate"
```

---

### Task 4: Revision Task Blocking

**Files:**
- Modify: `src/taskbrew/orchestrator/task_board.py`
- Modify: `tests/test_task_board_system_gates.py`

- [ ] **Step 1: Write failing revision tests**

Append:

```python
async def test_create_review_revision_tasks_blocks_original(board: TaskBoard):
    group = await board.create_group(title="Feature", created_by="pm")
    original = await board.create_task(
        group_id=group["id"],
        title="Risky implementation",
        task_type="implementation",
        assigned_to="coder",
    )
    await board.apply_backlog_intake_decision(
        original["id"],
        needs_review=True,
        reason="Risky task.",
    )
    await board.claim_task("coder", "coder-1")
    await board.complete_task(original["id"])

    revisions = await board.create_review_revision_tasks(
        original["id"],
        [
            {
                "title": "Fix missing tests",
                "description": "Add tests for the completed behavior.",
                "assigned_to": "tester",
                "priority": "high",
            }
        ],
    )

    assert len(revisions) == 1
    revision = revisions[0]
    assert revision["status"] == "backlog"
    assert revision["review_parent_task_id"] == original["id"]
    assert revision["revision_of"] == original["id"]

    parent = await board.get_task(original["id"])
    assert parent["status"] == "review"
    assert parent["review_status"] == "waiting_revision"
    assert revision["id"] in json.loads(parent["revision_task_ids"])

    deps = await board._db.execute_fetchall(
        "SELECT blocked_by, resolved FROM task_dependencies WHERE task_id = ?",
        (original["id"],),
    )
    assert deps == [{"blocked_by": revision["id"], "resolved": 0}]


async def test_review_parent_becomes_pending_after_revisions_complete(board: TaskBoard):
    group = await board.create_group(title="Feature", created_by="pm")
    original = await board.create_task(
        group_id=group["id"],
        title="Risky implementation",
        task_type="implementation",
        assigned_to="coder",
    )
    await board.apply_backlog_intake_decision(
        original["id"],
        needs_review=True,
        reason="Risky task.",
    )
    await board.claim_task("coder", "coder-1")
    await board.complete_task(original["id"])
    revisions = await board.create_review_revision_tasks(
        original["id"],
        [
            {
                "title": "Fix missing tests",
                "description": "Add tests.",
                "assigned_to": "tester",
                "priority": "high",
            }
        ],
    )
    revision = revisions[0]
    await board.apply_backlog_intake_decision(
        revision["id"],
        needs_review=False,
        reason="Revision task is reviewed through its parent gate.",
    )
    await board.claim_task("tester", "tester-1")
    await board.complete_task(revision["id"])

    ready = await board.mark_review_ready_if_unblocked(original["id"])

    assert ready["status"] == "review"
    assert ready["review_status"] == "pending"
```

- [ ] **Step 2: Run revision tests and verify they fail**

Run:

```bash
pytest tests/test_task_board_system_gates.py::test_create_review_revision_tasks_blocks_original \
  tests/test_task_board_system_gates.py::test_review_parent_becomes_pending_after_revisions_complete -v
```

Expected: FAIL because revision helpers do not exist.

- [ ] **Step 3: Allow `create_task` to store review parent metadata**

Extend the `TaskBoard.create_task` signature with:

```python
review_parent_task_id: str | None = None,
```

Add `review_parent_task_id` to the `INSERT INTO tasks` column list and values. Include it in the
returned dict:

```python
"review_parent_task_id": review_parent_task_id,
```

- [ ] **Step 4: Add a dependency helper**

Add:

```python
async def add_dependency(self, task_id: str, blocked_by_id: str) -> None:
    if await self.has_cycle(task_id, blocked_by_id):
        raise ValueError(f"Dependency {task_id} -> {blocked_by_id} would create a cycle")
    await self._db.execute(
        "INSERT OR IGNORE INTO task_dependencies (task_id, blocked_by) VALUES (?, ?)",
        (task_id, blocked_by_id),
    )
```

- [ ] **Step 5: Implement revision creation**

Add:

```python
async def create_review_revision_tasks(
    self,
    original_task_id: str,
    revisions: list[dict],
) -> list[dict]:
    original = await self.get_task(original_task_id)
    if original is None:
        raise ValueError(f"Task not found: {original_task_id}")
    if original["status"] != REVIEW_STATUS:
        raise ValueError(
            f"Can only create review revisions for review tasks, got {original['status']!r}"
        )

    created: list[dict] = []
    for revision in revisions:
        title = revision.get("title") or f"Revise {original_task_id}"
        description = revision.get("description") or "Address the system review finding."
        assigned_to = revision.get("assigned_to") or original.get("assigned_to") or "coder"
        priority = revision.get("priority") or original.get("priority") or "medium"
        task = await self.create_task(
            group_id=original["group_id"],
            title=title,
            task_type=revision.get("task_type") or "revision",
            assigned_to=assigned_to,
            created_by="system",
            description=description,
            priority=priority,
            parent_id=original_task_id,
            revision_of=original_task_id,
            review_parent_task_id=original_task_id,
        )
        await self.add_dependency(original_task_id, task["id"])
        created.append(task)

    revision_ids = self._json_list(original.get("revision_task_ids"))
    revision_ids.extend(task["id"] for task in created)
    await self._db.execute(
        "UPDATE tasks SET review_status = 'waiting_revision', revision_task_ids = ? "
        "WHERE id = ?",
        (json.dumps(revision_ids), original_task_id),
    )
    await self._append_system_gate_run(
        original_task_id,
        {
            "gate": "review",
            "outcome": "needs_revision",
            "revision_task_ids": [task["id"] for task in created],
            "finished_at": _utcnow(),
        },
    )
    return created
```

- [ ] **Step 6: Implement parent readiness**

Add:

```python
async def mark_review_ready_if_unblocked(self, task_id: str) -> dict:
    task = await self.get_task(task_id)
    if task is None:
        raise ValueError(f"Task not found: {task_id}")
    if task["status"] != REVIEW_STATUS:
        return task
    if await self._has_unresolved_dependencies(task_id):
        return task
    rows = await self._db.execute_returning(
        "UPDATE tasks SET review_status = 'pending' "
        "WHERE id = ? AND status = 'review' "
        "AND review_status = 'waiting_revision' RETURNING *",
        (task_id,),
    )
    return rows[0] if rows else task
```

- [ ] **Step 7: Ensure revision tasks do not enter Review**

Keep `_completion_status_for` constrained to original tasks:

```python
if needs_review in (1, True) and not review_parent:
    return REVIEW_STATUS, "pending"
```

Add this assertion to `test_review_parent_becomes_pending_after_revisions_complete` after
completing the revision:

```python
completed_revision = await board.get_task(revision["id"])
assert completed_revision["status"] == "completed"
```

- [ ] **Step 8: Run revision tests**

Run:

```bash
pytest tests/test_task_board_system_gates.py -v
```

Expected: PASS.

- [ ] **Step 9: Commit revision blocking**

```bash
git add src/taskbrew/orchestrator/task_board.py tests/test_task_board_system_gates.py
git commit -m "feat: block review tasks on system revisions"
```

---

### Task 5: System Gate Analyzer And Manager

**Files:**
- Create: `src/taskbrew/orchestrator/system_gates.py`
- Modify: `src/taskbrew/system_agent.py`
- Create: `tests/test_system_gates.py`

- [ ] **Step 1: Write failing manager tests**

Create `tests/test_system_gates.py`:

```python
"""Tests for async Backlog and Review system gate manager."""

from __future__ import annotations

import pytest

from taskbrew.orchestrator.database import Database
from taskbrew.orchestrator.system_gates import (
    BacklogIntakeResult,
    ReviewResult,
    RevisionRequest,
    SystemGateAnalyzer,
    SystemGateManager,
)
from taskbrew.orchestrator.task_board import TaskBoard


class FakeAnalyzer(SystemGateAnalyzer):
    def __init__(
        self,
        *,
        backlog_result: BacklogIntakeResult | None = None,
        review_result: ReviewResult | None = None,
    ) -> None:
        self.backlog_result = backlog_result or BacklogIntakeResult(
            needs_review=False,
            reason="Low-risk task.",
            signals=[],
            confidence="high",
        )
        self.review_result = review_result or ReviewResult(
            outcome="approved",
            reason="Output satisfies the task.",
            revisions=[],
        )

    async def decide_needs_review(self, context: dict) -> BacklogIntakeResult:
        return self.backlog_result

    async def review_completed_task(self, context: dict) -> ReviewResult:
        return self.review_result


@pytest.fixture
async def db():
    database = Database(":memory:")
    await database.initialize()
    yield database
    await database.close()


@pytest.fixture
async def board(db: Database) -> TaskBoard:
    task_board = TaskBoard(db, group_prefixes={"pm": "FEAT"})
    await task_board.register_prefixes(
        {"pm": "PM", "coder": "CD", "tester": "TS", "reviewer": "RV"}
    )
    return task_board


async def test_manager_processes_backlog_once(board: TaskBoard):
    group = await board.create_group(title="Feature", created_by="pm")
    task = await board.create_task(
        group_id=group["id"],
        title="Build widget",
        task_type="implementation",
        assigned_to="coder",
    )
    manager = SystemGateManager(
        board=board,
        analyzer=FakeAnalyzer(
            backlog_result=BacklogIntakeResult(
                needs_review=True,
                reason="Shared orchestration change.",
                signals=["shared_orchestration_logic"],
                confidence="medium",
            )
        ),
    )

    processed = await manager.process_pending_once()

    assert processed == {"backlog": 1, "review": 0}
    updated = await board.get_task(task["id"])
    assert updated["status"] == "pending"
    assert updated["needs_review"] == 1
    assert updated["needs_review_reason"] == "Shared orchestration change."


async def test_manager_approves_review_task(board: TaskBoard):
    group = await board.create_group(title="Feature", created_by="pm")
    task = await board.create_task(
        group_id=group["id"],
        title="Build widget",
        task_type="implementation",
        assigned_to="coder",
    )
    await board.apply_backlog_intake_decision(
        task["id"],
        needs_review=True,
        reason="Risky task.",
    )
    await board.claim_task("coder", "coder-1")
    await board.complete_task_with_output(task["id"], "done")
    manager = SystemGateManager(board=board, analyzer=FakeAnalyzer())

    processed = await manager.process_pending_once()

    assert processed == {"backlog": 0, "review": 1}
    updated = await board.get_task(task["id"])
    assert updated["status"] == "completed"
    assert updated["review_status"] == "approved"


async def test_manager_creates_revision_tasks(board: TaskBoard):
    group = await board.create_group(title="Feature", created_by="pm")
    task = await board.create_task(
        group_id=group["id"],
        title="Build widget",
        task_type="implementation",
        assigned_to="coder",
    )
    await board.apply_backlog_intake_decision(
        task["id"],
        needs_review=True,
        reason="Risky task.",
    )
    await board.claim_task("coder", "coder-1")
    await board.complete_task_with_output(task["id"], "done")
    manager = SystemGateManager(
        board=board,
        analyzer=FakeAnalyzer(
            review_result=ReviewResult(
                outcome="needs_revision",
                reason="Tests are missing.",
                revisions=[
                    RevisionRequest(
                        title="Add missing tests",
                        description="Cover the completed behavior with tests.",
                        assigned_to="tester",
                        priority="high",
                    )
                ],
            )
        ),
    )

    processed = await manager.process_pending_once()

    assert processed == {"backlog": 0, "review": 1}
    updated = await board.get_task(task["id"])
    assert updated["status"] == "review"
    assert updated["review_status"] == "waiting_revision"
```

- [ ] **Step 2: Run manager tests and verify they fail**

Run:

```bash
pytest tests/test_system_gates.py -v
```

Expected: FAIL because `taskbrew.orchestrator.system_gates` does not exist.

- [ ] **Step 3: Implement dataclasses and analyzer abstraction**

Create `src/taskbrew/orchestrator/system_gates.py` with:

```python
"""Async system gates for backlog intake and task review."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from taskbrew.agents.base import AgentRunner
from taskbrew.config import AgentConfig
from taskbrew.orchestrator.task_board import BACKLOG_STATUS, REVIEW_STATUS, TaskBoard

logger = logging.getLogger(__name__)


@dataclass
class BacklogIntakeResult:
    needs_review: bool
    reason: str
    signals: list[str] = field(default_factory=list)
    confidence: str = "medium"


@dataclass
class RevisionRequest:
    title: str
    description: str
    assigned_to: str
    priority: str = "high"
    task_type: str = "revision"


@dataclass
class ReviewResult:
    outcome: Literal["approved", "needs_revision", "rejected", "failed_review"]
    reason: str
    revisions: list[RevisionRequest] = field(default_factory=list)


class SystemGateAnalysisError(RuntimeError):
    """Raised when the system gate analyzer cannot produce a valid decision."""


class SystemGateAnalyzer:
    async def decide_needs_review(self, context: dict) -> BacklogIntakeResult:
        raise NotImplementedError

    async def review_completed_task(self, context: dict) -> ReviewResult:
        raise NotImplementedError
```

- [ ] **Step 4: Implement JSON extraction and production analyzer**

Append:

```python
def _extract_json_object(text: str) -> dict:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise SystemGateAnalysisError("System agent response did not contain JSON")
    try:
        parsed = json.loads(text[start:end + 1])
    except json.JSONDecodeError as exc:
        raise SystemGateAnalysisError(f"Invalid JSON from system agent: {exc}") from exc
    if not isinstance(parsed, dict):
        raise SystemGateAnalysisError("System agent JSON response must be an object")
    return parsed


class AgentRunnerSystemGateAnalyzer(SystemGateAnalyzer):
    def __init__(
        self,
        *,
        config: AgentConfig,
        project_dir: str | Path | None = None,
        event_bus=None,
    ) -> None:
        self._runner = AgentRunner(config, event_bus=event_bus)
        self._project_dir = str(project_dir) if project_dir else None

    async def decide_needs_review(self, context: dict) -> BacklogIntakeResult:
        prompt = (
            "You are running TaskBrew backlog intake. Decide whether this task "
            "needs a system review gate after completion. Return only JSON with "
            "keys: needs_review (boolean), reason (string), signals (array of "
            "strings), confidence (low|medium|high).\n\n"
            f"Context:\n{json.dumps(context, indent=2, sort_keys=True)}"
        )
        data = _extract_json_object(
            await self._runner.run(prompt=prompt, cwd=self._project_dir)
        )
        if not isinstance(data.get("needs_review"), bool):
            raise SystemGateAnalysisError("needs_review must be boolean")
        reason = str(data.get("reason") or "").strip()
        if not reason:
            raise SystemGateAnalysisError("reason is required")
        signals = data.get("signals") if isinstance(data.get("signals"), list) else []
        return BacklogIntakeResult(
            needs_review=data["needs_review"],
            reason=reason,
            signals=[str(signal) for signal in signals],
            confidence=str(data.get("confidence") or "medium"),
        )

    async def review_completed_task(self, context: dict) -> ReviewResult:
        prompt = (
            "You are running a TaskBrew system review gate. Review the completed "
            "task. Return only JSON with keys: outcome (approved|needs_revision|"
            "rejected|failed_review), reason (string), revisions (array). Each "
            "revision object must have title, description, assigned_to, priority, "
            "and task_type.\n\n"
            f"Context:\n{json.dumps(context, indent=2, sort_keys=True)}"
        )
        data = _extract_json_object(
            await self._runner.run(prompt=prompt, cwd=self._project_dir)
        )
        outcome = str(data.get("outcome") or "").strip()
        if outcome not in {"approved", "needs_revision", "rejected", "failed_review"}:
            raise SystemGateAnalysisError(f"Invalid review outcome: {outcome!r}")
        reason = str(data.get("reason") or "").strip()
        if not reason:
            raise SystemGateAnalysisError("reason is required")
        revisions = []
        for item in data.get("revisions") or []:
            if not isinstance(item, dict):
                continue
            revisions.append(
                RevisionRequest(
                    title=str(item.get("title") or "Revise task"),
                    description=str(item.get("description") or reason),
                    assigned_to=str(item.get("assigned_to") or context["task"]["assigned_to"]),
                    priority=str(item.get("priority") or "high"),
                    task_type=str(item.get("task_type") or "revision"),
                )
            )
        return ReviewResult(outcome=outcome, reason=reason, revisions=revisions)
```

- [ ] **Step 5: Implement manager context builders**

Append:

```python
class SystemGateManager:
    def __init__(
        self,
        *,
        board: TaskBoard,
        analyzer: SystemGateAnalyzer,
        interval_seconds: float = 5.0,
        batch_size: int = 10,
    ) -> None:
        self._board = board
        self._analyzer = analyzer
        self._interval = interval_seconds
        self._batch_size = batch_size
        self._stopping = False

    async def _backlog_context(self, task: dict) -> dict:
        dependencies = await self._board._db.execute_fetchall(
            "SELECT blocked_by, resolved FROM task_dependencies WHERE task_id = ?",
            (task["id"],),
        )
        siblings = await self._board._db.execute_fetchall(
            "SELECT id, title, status, task_type, assigned_to FROM tasks "
            "WHERE group_id = ? AND id != ? ORDER BY created_at DESC LIMIT 20",
            (task["group_id"], task["id"]),
        )
        return {
            "task": task,
            "dependencies": dependencies,
            "nearby_tasks": siblings,
        }

    async def _review_context(self, task: dict) -> dict:
        dependencies = await self._board._db.execute_fetchall(
            "SELECT blocked_by, resolved FROM task_dependencies WHERE task_id = ?",
            (task["id"],),
        )
        revisions = await self._board._db.execute_fetchall(
            "SELECT id, title, status, assigned_to, output_text FROM tasks "
            "WHERE review_parent_task_id = ? ORDER BY created_at",
            (task["id"],),
        )
        return {
            "task": task,
            "dependencies": dependencies,
            "revisions": revisions,
            "needs_review_reason": task.get("needs_review_reason"),
            "output_text": task.get("output_text"),
        }
```

- [ ] **Step 6: Implement manager processing**

Append:

```python
    async def process_backlog_task(self, task_id: str) -> bool:
        task = await self._board.get_task(task_id)
        if not task or task["status"] != BACKLOG_STATUS:
            return False
        try:
            await self._board._db.execute(
                "UPDATE tasks SET backlog_intake_status = 'running' "
                "WHERE id = ? AND status = 'backlog'",
                (task_id,),
            )
            result = await self._analyzer.decide_needs_review(
                await self._backlog_context(task)
            )
            await self._board.apply_backlog_intake_decision(
                task_id,
                needs_review=result.needs_review,
                reason=result.reason,
                signals=result.signals,
                confidence=result.confidence,
            )
            return True
        except Exception as exc:
            logger.exception("Backlog intake failed for %s", task_id)
            await self._board.mark_backlog_intake_failed(task_id, str(exc))
            return False

    async def process_review_task(self, task_id: str) -> bool:
        task = await self._board.get_task(task_id)
        if not task or task["status"] != REVIEW_STATUS:
            return False
        if await self._board._has_unresolved_dependencies(task_id):
            return False
        try:
            await self._board._db.execute(
                "UPDATE tasks SET review_status = 'running' "
                "WHERE id = ? AND status = 'review'",
                (task_id,),
            )
            result = await self._analyzer.review_completed_task(
                await self._review_context(task)
            )
            if result.outcome == "approved":
                await self._board.approve_review_gate(task_id, reason=result.reason)
            elif result.outcome == "rejected":
                await self._board.reject_review_gate(task_id, reason=result.reason)
            elif result.outcome == "needs_revision":
                revision_dicts = [
                    {
                        "title": revision.title,
                        "description": revision.description,
                        "assigned_to": revision.assigned_to,
                        "priority": revision.priority,
                        "task_type": revision.task_type,
                    }
                    for revision in result.revisions
                ]
                if not revision_dicts:
                    revision_dicts = [
                        {
                            "title": f"Revise {task_id}",
                            "description": result.reason,
                            "assigned_to": task.get("assigned_to") or "coder",
                            "priority": task.get("priority") or "high",
                            "task_type": "revision",
                        }
                    ]
                await self._board.create_review_revision_tasks(task_id, revision_dicts)
            else:
                await self._board.mark_review_failed(task_id, result.reason)
            return True
        except Exception as exc:
            logger.exception("Review gate failed for %s", task_id)
            await self._board.mark_review_failed(task_id, str(exc))
            return False

    async def process_pending_once(self) -> dict[str, int]:
        backlog = await self._board._db.execute_fetchall(
            "SELECT id FROM tasks WHERE status = 'backlog' "
            "AND COALESCE(backlog_intake_status, 'pending') IN ('pending', 'failed') "
            "ORDER BY created_at LIMIT ?",
            (self._batch_size,),
        )
        backlog_count = 0
        for row in backlog:
            if await self.process_backlog_task(row["id"]):
                backlog_count += 1

        reviews = await self._board._db.execute_fetchall(
            "SELECT id FROM tasks WHERE status = 'review' "
            "AND COALESCE(review_status, 'pending') IN ('pending', 'failed') "
            "AND NOT EXISTS ("
            "  SELECT 1 FROM task_dependencies d "
            "  WHERE d.task_id = tasks.id AND d.resolved = 0"
            ") ORDER BY created_at LIMIT ?",
            (self._batch_size,),
        )
        review_count = 0
        for row in reviews:
            if await self.process_review_task(row["id"]):
                review_count += 1
        return {"backlog": backlog_count, "review": review_count}

    async def run(self) -> None:
        while not self._stopping:
            await self.process_pending_once()
            await asyncio.sleep(self._interval)

    def stop(self) -> None:
        self._stopping = True
```

- [ ] **Step 7: Add review failed method**

In `TaskBoard`, add:

```python
async def mark_review_failed(self, task_id: str, reason: str) -> dict:
    await self._append_system_gate_run(
        task_id,
        {
            "gate": "review",
            "outcome": "failed_review",
            "reason": reason[:1000],
            "finished_at": _utcnow(),
        },
    )
    rows = await self._db.execute_returning(
        "UPDATE tasks SET review_status = 'failed' "
        "WHERE id = ? AND status = 'review' RETURNING *",
        (task_id,),
    )
    if not rows:
        task = await self.get_task(task_id)
        if task is None:
            raise ValueError(f"Task not found: {task_id}")
        return task
    return rows[0]
```

- [ ] **Step 8: Extend the locked system prompt**

In `src/taskbrew/system_agent.py`, append these constraints inside `SYSTEM_AGENT_PROMPT`:

```python
When running backlog intake, you may decide only whether a task needs review
and explain that decision. Do not rewrite the task.

When running review gates, inspect the provided task context and choose one
outcome: approved, needs_revision, rejected, or failed_review. Rejected means
the task should not continue as requested. Fixable work should become revision
tasks.
```

- [ ] **Step 9: Run manager tests**

Run:

```bash
pytest tests/test_system_gates.py tests/test_task_board_system_gates.py -v
```

Expected: PASS.

- [ ] **Step 10: Commit system gate manager**

```bash
git add src/taskbrew/orchestrator/system_gates.py src/taskbrew/system_agent.py \
  src/taskbrew/orchestrator/task_board.py tests/test_system_gates.py
git commit -m "feat: add async system gate manager"
```

---

### Task 6: Orchestrator Wiring

**Files:**
- Modify: `src/taskbrew/main.py`
- Modify: `tests/test_system_gates.py`

- [ ] **Step 1: Add a wiring test**

Append to `tests/test_system_gates.py`:

```python
def test_system_gate_manager_exposes_stop_method(board: TaskBoard):
    manager = SystemGateManager(board=board, analyzer=FakeAnalyzer())

    manager.stop()

    assert manager._stopping is True
```

This small test keeps the shutdown contract visible while wiring uses the manager in `main.py`.

- [ ] **Step 2: Add orchestrator attribute**

In `src/taskbrew/main.py`, inside `Orchestrator.__init__`, add:

```python
self.system_gate_manager = None
self._system_gate_task = None
```

- [ ] **Step 3: Instantiate the manager in `build_orchestrator`**

After the existing manager setup and before `return orch`, add:

```python
from taskbrew.orchestrator.system_gates import (
    AgentRunnerSystemGateAnalyzer,
    SystemGateManager,
)
from taskbrew.system_agent import build_system_agent_config

connect_host = (
    "127.0.0.1"
    if team_config.dashboard_host in ("0.0.0.0", "::")
    else team_config.dashboard_host
)
system_agent_config = build_system_agent_config(
    team_config,
    project_dir=project_dir,
    api_url=f"http://{connect_host}:{team_config.dashboard_port}",
)
analyzer = AgentRunnerSystemGateAnalyzer(
    config=system_agent_config,
    project_dir=project_dir,
    event_bus=event_bus,
)
orch.system_gate_manager = SystemGateManager(
    board=task_board,
    analyzer=analyzer,
)
```

- [ ] **Step 4: Start the gate manager in `start_agents`**

Near the merge broker startup in `start_agents`, add:

```python
if orch.system_gate_manager:
    system_gate_task = asyncio.create_task(orch.system_gate_manager.run())
    orch._system_gate_task = system_gate_task
    orch.agent_tasks.append(system_gate_task)
```

- [ ] **Step 5: Stop the manager during shutdown**

In `Orchestrator.shutdown`, after `if self.merge_broker: self.merge_broker.stop()`, add:

```python
if self.system_gate_manager:
    self.system_gate_manager.stop()
```

- [ ] **Step 6: Run focused tests**

Run:

```bash
pytest tests/test_system_gates.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit orchestration wiring**

```bash
git add src/taskbrew/main.py tests/test_system_gates.py
git commit -m "feat: wire system gates into orchestrator"
```

---

### Task 7: API And Existing Test Updates

**Files:**
- Modify: `src/taskbrew/dashboard/routers/tasks.py`
- Modify: `tests/test_task_board.py`
- Modify: `tests/test_task_board_advanced.py`
- Modify: `tests/test_dashboard_api.py`
- Modify: `tests/test_task_board_system_gates.py`

- [ ] **Step 1: Update allowed task statuses**

In `src/taskbrew/dashboard/routers/tasks.py`, change `_VALID_TASK_STATUSES` to:

```python
_VALID_TASK_STATUSES = frozenset({
    "backlog", "blocked", "pending", "in_progress", "review",
    "completed", "failed", "rejected", "cancelled",
})
```

- [ ] **Step 2: Enrich task details with review dependency data**

In `get_task_detail`, after loading `children`, add:

```python
revision_tasks = await orch.task_board._db.execute_fetchall(
    "SELECT id, title, status, assigned_to FROM tasks "
    "WHERE review_parent_task_id = ? ORDER BY created_at",
    (task_id,),
)
task["revision_tasks"] = revision_tasks
```

- [ ] **Step 3: Update existing TaskBoard tests for backlog birth**

In `tests/test_task_board.py`, update tests that create a task and expect immediate `pending` or
`blocked`. Use this helper near the fixtures:

```python
async def intake(
    board: TaskBoard,
    task_id: str,
    *,
    needs_review: bool = False,
    reason: str = "Test intake decision.",
) -> dict:
    return await board.apply_backlog_intake_decision(
        task_id,
        needs_review=needs_review,
        reason=reason,
    )
```

For claim/complete tests, call `await intake(board, task["id"])` before `claim_task`.

For task creation tests, change the creation assertion to:

```python
assert task["status"] == "backlog"
assert task["intended_status"] == "pending"
```

For dependency creation tests, change:

```python
assert blocked["status"] == "blocked"
```

to:

```python
assert blocked["status"] == "backlog"
assert blocked["intended_status"] == "blocked"
```

- [ ] **Step 4: Update advanced TaskBoard tests**

Apply the same pattern in `tests/test_task_board_advanced.py`: run backlog intake before tests that
claim or complete tasks. For tests that assert blocked birth, assert backlog plus blocked intent.

- [ ] **Step 5: Update dashboard API test expectations**

In `tests/test_dashboard_api.py::test_get_board_with_tasks`, change:

```python
assert "pending" in data
assert len(data["pending"]) == 1
assert data["pending"][0]["title"] == "Implement login"
```

to:

```python
assert "backlog" in data
assert len(data["backlog"]) == 1
assert data["backlog"][0]["title"] == "Implement login"
```

Add a new test:

```python
async def test_task_detail_exposes_system_gate_fields(app_client):
    board = app_client["board"]
    group = await board.create_group(title="Gate Detail", created_by="pm")
    task = await board.create_task(
        group_id=group["id"],
        title="Risky task",
        task_type="implementation",
        assigned_to="coder",
        created_by="pm",
    )
    await board.apply_backlog_intake_decision(
        task["id"],
        needs_review=True,
        reason="Touches shared orchestration logic.",
    )

    resp = await app_client["client"].get(f"/api/tasks/{task['id']}")

    assert resp.status_code == 200
    data = resp.json()
    assert data["needs_review"] == 1
    assert data["needs_review_reason"] == "Touches shared orchestration logic."
    assert "revision_tasks" in data
```

- [ ] **Step 6: Run focused API/domain tests**

Run:

```bash
pytest tests/test_task_board.py tests/test_task_board_advanced.py \
  tests/test_task_board_system_gates.py tests/test_dashboard_api.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit API and test updates**

```bash
git add src/taskbrew/dashboard/routers/tasks.py tests/test_task_board.py \
  tests/test_task_board_advanced.py tests/test_dashboard_api.py \
  tests/test_task_board_system_gates.py
git commit -m "feat: expose backlog review gate state through API"
```

---

### Task 8: Dashboard Backlog And Review UI

**Files:**
- Modify: `src/taskbrew/dashboard/templates/index.html`
- Modify: `src/taskbrew/dashboard/static/js/dashboard-core.js`
- Modify: `src/taskbrew/dashboard/static/css/main.css`

- [ ] **Step 1: Add Backlog and Review filter options**

In both status filter blocks in `src/taskbrew/dashboard/templates/index.html`, add:

```html
<option value="backlog">Backlog</option>
<option value="review">Review</option>
```

Place `Backlog` before `Pending` and `Review` after `In Progress`.

- [ ] **Step 2: Add visible board columns**

In the Kanban board HTML, add a Backlog column before Pending:

```html
<div class="kanban-column" id="col-backlog" role="listitem" aria-label="Backlog tasks">
    <div class="kanban-column-header">
        <h3><span class="accent-bar accent-backlog"></span> Backlog <span class="count" id="countBacklog">0</span></h3>
    </div>
    <div class="column-tasks" id="tasksBacklog" role="list" aria-label="Backlog task cards"></div>
</div>
```

Add a Review column after In Progress:

```html
<div class="kanban-column" id="col-review" role="listitem" aria-label="Review tasks">
    <div class="kanban-column-header">
        <h3><span class="accent-bar accent-review"></span> Review <span class="count" id="countReview">0</span></h3>
    </div>
    <div class="column-tasks" id="tasksReview" role="list" aria-label="Review task cards"></div>
</div>
```

- [ ] **Step 3: Update board column maps in inline template JS**

In each inline `columns` map in `src/taskbrew/dashboard/templates/index.html`, use:

```javascript
const columns = {
    backlog:    { el: 'tasksBacklog',   count: 'countBacklog' },
    pending:    { el: 'tasksPending',   count: 'countPending' },
    in_progress:{ el: 'tasksInProgress',count: 'countInProgress' },
    review:     { el: 'tasksReview',    count: 'countReview' },
    blocked:    { el: 'tasksBlocked',   count: 'countBlocked' },
    completed:  { el: 'tasksCompleted', count: 'countCompleted' },
    rejected:   { el: 'tasksRejected',  count: 'countRejected' },
    failed:     { el: 'tasksFailed',    count: 'countFailed' },
};
```

Use `var` instead of `const` in older inline functions that already use `var`.

- [ ] **Step 4: Update status-to-column maps**

In both inline `STATUS_TO_COL_ID` and drag/drop `COL_STATUS_MAP`, include:

```javascript
backlog: 'col-backlog',
review: 'col-review',
failed: 'col-failed',
```

and:

```javascript
'col-backlog': 'backlog',
'col-review': 'review',
'col-failed': 'failed',
```

Keep `cancelled` mapped to `col-rejected`.

- [ ] **Step 5: Add needs-review card badge**

In `createTaskCard` after the group badge, add:

```javascript
if (task.needs_review === true || task.needs_review === 1) {
    html += '<span class="badge badge-needs-review">Needs Review</span>';
}
```

- [ ] **Step 6: Add task detail decision section**

In `openTaskDetail`, after the Description section and before Rejection Reason, add:

```javascript
if (task.needs_review !== null && task.needs_review !== undefined) {
    html += '<div class="task-detail-section"><div class="task-detail-section-title">System Review Decision</div>';
    html += '<div class="task-detail-grid">';
    html += tdField('Needs Review', (task.needs_review === true || task.needs_review === 1) ? 'Yes' : 'No');
    var reasonText = task.needs_review_reason ? escapeHtml(task.needs_review_reason) : '-';
    html += tdField('Reason', reasonText, task.needs_review_reason ? '' : 'muted');
    if (task.review_status) {
        html += tdField('Review Status', escapeHtml(String(task.review_status)));
    }
    if (task.review_round !== null && task.review_round !== undefined) {
        html += tdField('Review Round', escapeHtml(String(task.review_round)));
    }
    html += '</div></div>';
}
```

- [ ] **Step 7: Mirror JS updates in extracted dashboard script**

Apply the same `columns`, `STATUS_TO_COL_ID`, and card badge changes in
`src/taskbrew/dashboard/static/js/dashboard-core.js`.

- [ ] **Step 8: Add CSS styles**

In `src/taskbrew/dashboard/static/css/main.css`, add:

```css
.accent-backlog { background: var(--text-muted); box-shadow: 0 0 8px rgba(139, 147, 167, 0.35); }
.accent-review { background: var(--accent-cyan); box-shadow: 0 0 8px rgba(6, 182, 212, 0.35); }

.badge-needs-review {
    background: rgba(6, 182, 212, 0.12);
    color: #67e8f9;
    border: 1px solid rgba(6, 182, 212, 0.24);
}

.task-detail-badge.badge-status-backlog {
    background: rgba(139, 147, 167, 0.12);
    color: var(--text-secondary);
    border-color: rgba(139, 147, 167, 0.2);
}

.task-detail-badge.badge-status-review {
    background: rgba(6, 182, 212, 0.12);
    color: var(--accent-cyan-light);
    border-color: rgba(6, 182, 212, 0.2);
}
```

Also mirror the CSS in the inline `<style>` block of `src/taskbrew/dashboard/templates/index.html`
because this template currently carries duplicated dashboard styles.

- [ ] **Step 9: Update inline status icons**

Where `statusIcons` or `STATUS_ICONS` are defined, add:

```javascript
backlog: 'B',
review: 'R',
```

Do not add decorative copy to the app explaining the feature.

- [ ] **Step 10: Run dashboard API smoke tests**

Run:

```bash
pytest tests/test_dashboard_api.py -v
```

Expected: PASS.

- [ ] **Step 11: Commit dashboard UI**

```bash
git add src/taskbrew/dashboard/templates/index.html \
  src/taskbrew/dashboard/static/js/dashboard-core.js \
  src/taskbrew/dashboard/static/css/main.css
git commit -m "feat: show backlog and review gate columns"
```

---

### Task 9: Full Verification And Browser Test

**Files:**
- Read: `docs/superpowers/specs/2026-05-05-backlog-review-system-gates-design.md`
- No code changes unless verification finds a defect.

- [ ] **Step 1: Run focused system gate suite**

Run:

```bash
pytest tests/test_task_board_system_gates.py tests/test_system_gates.py -v
```

Expected: PASS.

- [ ] **Step 2: Run dashboard and task board tests**

Run:

```bash
pytest tests/test_task_board.py tests/test_task_board_advanced.py tests/test_dashboard_api.py -v
```

Expected: PASS.

- [ ] **Step 3: Run full test suite**

Run:

```bash
pytest tests/ -x
```

Expected: PASS.

- [ ] **Step 4: Run Ruff**

Run:

```bash
ruff check src/ tests/
```

Expected: PASS.

- [ ] **Step 5: Start or reuse the dashboard server**

If no server is running for this workspace, run:

```bash
taskbrew start
```

Expected: the dashboard serves locally and logs the URL.

- [ ] **Step 6: Test in the in-app browser**

Use the `browser-use:browser` skill against the active dashboard URL. Verify:

- `Backlog` column is visible.
- `Review` column is visible.
- A newly created task appears in `Backlog` before intake completes.
- After system intake, the card moves to `Pending` or `Blocked`.
- A task with `needs_review` shows the card badge.
- Task details show `System Review Decision` and the visible reason.
- A reviewed task can move to `Completed`, `Rejected`, or stay in `Review` with revision tasks.

- [ ] **Step 7: Final status check**

Run:

```bash
git status --short
```

Expected: only intentional implementation files are modified.
