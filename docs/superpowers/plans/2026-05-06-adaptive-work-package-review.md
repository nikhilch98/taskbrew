# Adaptive Work Package Review Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add first-class Work Packages and use them as the default system review boundary for non-trivial TaskBrew goals.

**Architecture:** Keep `groups` as the top-level Goal/Initiative and add `work_packages`, optional `milestones`, and `review_gates` as first-class persisted entities. Tasks remain the execution unit, but every new task belongs to a Work Package; package status and package review gates are reconciled from child tasks, dependencies, checks, and review outcomes. The first implementation keeps existing task branches and reviews the project root or task outputs as the integration target, while leaving package integration branches out of the first code path.

**Tech Stack:** Python 3.10+, SQLite/aiosqlite, FastAPI, vanilla dashboard JavaScript/CSS, pytest/pytest-asyncio.

---

## Scope Check

This plan implements the first production slice of the approved spec:

- first-class Work Package persistence
- optional Milestone persistence
- task-to-package linkage
- package status reconciliation
- package-level system review gates
- package-first API data
- minimal package visibility in the existing dashboard

This plan intentionally keeps task-level review support working. It does not remove `needs_review`, and it does not require package integration branches before package review can function.

## File Structure

- Modify `src/taskbrew/orchestrator/database.py`
  - Add baseline tables for `milestones`, `work_packages`, and `review_gates`.
  - Add `work_package_id`, `milestone_id`, and `review_scope` columns to `tasks`.

- Modify `src/taskbrew/orchestrator/migration.py`
  - Add migration 35 with the same schema changes and indexes.
  - Keep migration idempotent through existing column/table probes.

- Modify `src/taskbrew/orchestrator/task_board.py`
  - Add Work Package constants and helper methods.
  - Auto-create a default Work Package per group when a task has no explicit package.
  - Reconcile package status after task create, completion, dependency resolution, rejection, and failure.
  - Create and resolve package review gates.

- Modify `src/taskbrew/orchestrator/system_gates.py`
  - Add package review context and processing.
  - Include package gates in `process_pending_once`.
  - Reuse `ReviewResult` and existing analyzer call for package-level review.

- Modify `src/taskbrew/dashboard/models.py`
  - Add `work_package_id`, `milestone_id`, and `review_scope` to `CreateTaskBody`.
  - Add request models for creating Work Packages and Milestones.

- Modify `src/taskbrew/dashboard/routers/tasks.py`
  - Pass package fields into `TaskBoard.create_task`.
  - Add Work Package and Milestone API endpoints.
  - Add package board endpoint.

- Modify `src/taskbrew/tools/task_tools.py`
  - Let agents pass `work_package_id`.
  - Document that architect-created coder tasks should normally target a Work Package.

- Modify `config/roles/pm.yaml`, `config/roles/architect.yaml`, and `src/taskbrew/project_manager.py`
  - Update default role prompts to plan and create Work Packages.
  - Keep backwards compatibility for users who manually create raw tasks.

- Modify `src/taskbrew/dashboard/static/js/dashboard-core.js`
  - Load package board data and render Work Package cards by default when available.
  - Keep the existing task board fallback.

- Modify `src/taskbrew/dashboard/static/js/dashboard-ui.js`
  - Show nested package details: child tasks, review status, latest review reason, and progress.

- Modify `src/taskbrew/dashboard/static/css/main.css`
  - Add package card, package progress, and review gate styles.

- Add `tests/test_work_packages.py`
  - Unit tests for package creation, task association, status reconciliation, and package review readiness.

- Add or modify `tests/test_system_gates.py`
  - Package review gate tests.

- Modify `tests/test_dashboard_api.py`
  - API tests for package endpoints and package board responses.

---

### Task 1: Schema And Baseline Persistence

**Files:**
- Modify: `src/taskbrew/orchestrator/database.py`
- Modify: `src/taskbrew/orchestrator/migration.py`
- Test: `tests/test_work_packages.py`

- [ ] **Step 1: Write schema tests**

Add this file:

```python
"""Tests for Work Package persistence and task linkage."""

from __future__ import annotations

import pytest

from taskbrew.orchestrator.database import Database


@pytest.fixture
async def db():
    database = Database(":memory:")
    await database.initialize()
    yield database
    await database.close()


async def test_schema_has_work_package_tables(db: Database):
    tables = {
        row["name"]
        for row in await db.execute_fetchall(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }

    assert "milestones" in tables
    assert "work_packages" in tables
    assert "review_gates" in tables


async def test_tasks_schema_has_package_columns(db: Database):
    columns = {
        row["name"]
        for row in await db.execute_fetchall("PRAGMA table_info(tasks)")
    }

    assert {"work_package_id", "milestone_id", "review_scope"}.issubset(columns)
```

- [ ] **Step 2: Run the new tests and verify they fail**

Run:

```bash
.venv/bin/pytest tests/test_work_packages.py -q
```

Expected: failures because `milestones`, `work_packages`, `review_gates`, and task package columns do not exist.

- [ ] **Step 3: Add baseline schema**

In `src/taskbrew/orchestrator/database.py`, add these tables after `groups` and before `tasks`:

```sql
CREATE TABLE IF NOT EXISTS milestones (
    id             TEXT PRIMARY KEY,
    group_id       TEXT NOT NULL REFERENCES groups(id),
    title          TEXT NOT NULL,
    description    TEXT,
    status         TEXT NOT NULL DEFAULT 'pending',
    review_scope   TEXT NOT NULL DEFAULT 'milestone',
    review_status  TEXT,
    review_reason  TEXT,
    review_round   INTEGER NOT NULL DEFAULT 0,
    max_review_rounds INTEGER NOT NULL DEFAULT 3,
    created_by     TEXT,
    created_at     TEXT NOT NULL,
    updated_at     TEXT,
    completed_at   TEXT
);

CREATE TABLE IF NOT EXISTS work_packages (
    id             TEXT PRIMARY KEY,
    group_id       TEXT NOT NULL REFERENCES groups(id),
    milestone_id   TEXT REFERENCES milestones(id),
    title          TEXT NOT NULL,
    description    TEXT,
    status         TEXT NOT NULL DEFAULT 'pending',
    risk_level     TEXT NOT NULL DEFAULT 'medium',
    review_scope   TEXT NOT NULL DEFAULT 'work_package',
    review_status  TEXT,
    review_reason  TEXT,
    review_round   INTEGER NOT NULL DEFAULT 0,
    max_review_rounds INTEGER NOT NULL DEFAULT 3,
    branch_name    TEXT,
    created_by     TEXT,
    created_at     TEXT NOT NULL,
    updated_at     TEXT,
    completed_at   TEXT
);

CREATE TABLE IF NOT EXISTS review_gates (
    id             TEXT PRIMARY KEY,
    entity_type    TEXT NOT NULL,
    entity_id      TEXT NOT NULL,
    group_id       TEXT NOT NULL REFERENCES groups(id),
    status         TEXT NOT NULL DEFAULT 'pending',
    outcome        TEXT,
    reason         TEXT,
    review_round   INTEGER NOT NULL DEFAULT 0,
    max_review_rounds INTEGER NOT NULL DEFAULT 3,
    system_gate_runs TEXT DEFAULT '[]',
    created_at     TEXT NOT NULL,
    updated_at     TEXT,
    completed_at   TEXT,
    UNIQUE(entity_type, entity_id)
);
```

In the `tasks` table definition, add these columns near `group_id`:

```sql
    work_package_id TEXT REFERENCES work_packages(id),
    milestone_id    TEXT REFERENCES milestones(id),
```

Add this system-gate field near `needs_review_decision`:

```sql
    review_scope                 TEXT,
```

Add these indexes to `_INDEX_SQL`:

```sql
CREATE INDEX IF NOT EXISTS idx_milestones_group_status
    ON milestones(group_id, status);

CREATE INDEX IF NOT EXISTS idx_work_packages_group_status
    ON work_packages(group_id, status);

CREATE INDEX IF NOT EXISTS idx_work_packages_milestone
    ON work_packages(milestone_id, status);

CREATE INDEX IF NOT EXISTS idx_tasks_work_package
    ON tasks(work_package_id, status);

CREATE INDEX IF NOT EXISTS idx_review_gates_entity
    ON review_gates(entity_type, entity_id);

CREATE INDEX IF NOT EXISTS idx_review_gates_status
    ON review_gates(status, created_at);
```

- [ ] **Step 4: Add migration 35**

In `src/taskbrew/orchestrator/migration.py`, append:

```python
    (35, "add_work_packages_and_review_gates", """
        CREATE TABLE IF NOT EXISTS milestones (
            id             TEXT PRIMARY KEY,
            group_id       TEXT NOT NULL REFERENCES groups(id),
            title          TEXT NOT NULL,
            description    TEXT,
            status         TEXT NOT NULL DEFAULT 'pending',
            review_scope   TEXT NOT NULL DEFAULT 'milestone',
            review_status  TEXT,
            review_reason  TEXT,
            review_round   INTEGER NOT NULL DEFAULT 0,
            max_review_rounds INTEGER NOT NULL DEFAULT 3,
            created_by     TEXT,
            created_at     TEXT NOT NULL,
            updated_at     TEXT,
            completed_at   TEXT
        );

        CREATE TABLE IF NOT EXISTS work_packages (
            id             TEXT PRIMARY KEY,
            group_id       TEXT NOT NULL REFERENCES groups(id),
            milestone_id   TEXT REFERENCES milestones(id),
            title          TEXT NOT NULL,
            description    TEXT,
            status         TEXT NOT NULL DEFAULT 'pending',
            risk_level     TEXT NOT NULL DEFAULT 'medium',
            review_scope   TEXT NOT NULL DEFAULT 'work_package',
            review_status  TEXT,
            review_reason  TEXT,
            review_round   INTEGER NOT NULL DEFAULT 0,
            max_review_rounds INTEGER NOT NULL DEFAULT 3,
            branch_name    TEXT,
            created_by     TEXT,
            created_at     TEXT NOT NULL,
            updated_at     TEXT,
            completed_at   TEXT
        );

        CREATE TABLE IF NOT EXISTS review_gates (
            id             TEXT PRIMARY KEY,
            entity_type    TEXT NOT NULL,
            entity_id      TEXT NOT NULL,
            group_id       TEXT NOT NULL REFERENCES groups(id),
            status         TEXT NOT NULL DEFAULT 'pending',
            outcome        TEXT,
            reason         TEXT,
            review_round   INTEGER NOT NULL DEFAULT 0,
            max_review_rounds INTEGER NOT NULL DEFAULT 3,
            system_gate_runs TEXT DEFAULT '[]',
            created_at     TEXT NOT NULL,
            updated_at     TEXT,
            completed_at   TEXT,
            UNIQUE(entity_type, entity_id)
        );

        ALTER TABLE tasks ADD COLUMN work_package_id TEXT REFERENCES work_packages(id);
        ALTER TABLE tasks ADD COLUMN milestone_id TEXT REFERENCES milestones(id);
        ALTER TABLE tasks ADD COLUMN review_scope TEXT;

        CREATE INDEX IF NOT EXISTS idx_milestones_group_status
            ON milestones(group_id, status);
        CREATE INDEX IF NOT EXISTS idx_work_packages_group_status
            ON work_packages(group_id, status);
        CREATE INDEX IF NOT EXISTS idx_work_packages_milestone
            ON work_packages(milestone_id, status);
        CREATE INDEX IF NOT EXISTS idx_tasks_work_package
            ON tasks(work_package_id, status);
        CREATE INDEX IF NOT EXISTS idx_review_gates_entity
            ON review_gates(entity_type, entity_id);
        CREATE INDEX IF NOT EXISTS idx_review_gates_status
            ON review_gates(status, created_at);
    """),
```

- [ ] **Step 5: Run schema tests**

Run:

```bash
.venv/bin/pytest tests/test_work_packages.py -q
```

Expected: `2 passed`.

- [ ] **Step 6: Commit schema changes**

Run:

```bash
git add src/taskbrew/orchestrator/database.py src/taskbrew/orchestrator/migration.py tests/test_work_packages.py
git commit -m "feat: add work package schema"
```

---

### Task 2: Work Package CRUD And Default Package Assignment

**Files:**
- Modify: `src/taskbrew/orchestrator/task_board.py`
- Test: `tests/test_work_packages.py`

- [ ] **Step 1: Add Work Package CRUD tests**

Append to `tests/test_work_packages.py`:

```python
from taskbrew.orchestrator.task_board import TaskBoard


class RecordingEventBus:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    async def emit(self, event_type: str, data: dict) -> None:
        self.events.append((event_type, data))


@pytest.fixture
async def board(db: Database) -> TaskBoard:
    task_board = TaskBoard(
        db,
        group_prefixes={"pm": "FEAT", "architect": "DEBT"},
        event_bus=RecordingEventBus(),
    )
    await task_board.register_prefixes(
        {"pm": "PM", "architect": "AR", "coder": "CD"}
    )
    return task_board


async def test_create_work_package(board: TaskBoard):
    group = await board.create_group(title="Feature", created_by="pm")

    package = await board.create_work_package(
        group_id=group["id"],
        title="Implement dashboard live updates",
        description="Deliver websocket-driven board refresh.",
        created_by="architect-1",
        risk_level="medium",
    )

    assert package["id"].startswith("WP-")
    assert package["group_id"] == group["id"]
    assert package["title"] == "Implement dashboard live updates"
    assert package["status"] == "pending"
    assert package["review_scope"] == "work_package"
    assert package["review_status"] is None


async def test_create_task_assigns_default_work_package(board: TaskBoard):
    group = await board.create_group(title="Feature", created_by="pm")

    task = await board.create_task(
        group_id=group["id"],
        title="Build widget",
        task_type="implementation",
        assigned_to="coder",
        created_by="architect-1",
    )

    assert task["work_package_id"].startswith("WP-")
    package = await board.get_work_package(task["work_package_id"])
    assert package["title"] == "Default package for Feature"
    assert package["group_id"] == group["id"]
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```bash
.venv/bin/pytest tests/test_work_packages.py -q
```

Expected: failures for missing `TaskBoard.create_work_package`, `TaskBoard.get_work_package`, and missing task return field.

- [ ] **Step 3: Add constants and package methods**

In `src/taskbrew/orchestrator/task_board.py`, add constants near `DEFAULT_MAX_REVIEW_ROUNDS`:

```python
DEFAULT_PACKAGE_REVIEW_SCOPE = "work_package"
NO_REVIEW_SCOPE = "none"
TASK_REVIEW_SCOPE = "task"
PACKAGE_STATUSES = (
    "backlog",
    "pending",
    "in_progress",
    "blocked",
    "review",
    "waiting_revision",
    "completed",
    "rejected",
    "failed",
)
```

Add these methods in the `TaskBoard` class before the `# Tasks` section:

```python
    async def create_work_package(
        self,
        *,
        group_id: str,
        title: str,
        description: str | None = None,
        milestone_id: str | None = None,
        created_by: str | None = None,
        risk_level: str = "medium",
        review_scope: str = DEFAULT_PACKAGE_REVIEW_SCOPE,
    ) -> dict:
        await self._db.register_prefix("WP")
        package_id = await self._db.generate_task_id("WP")
        now = _utcnow()
        await self._db.execute(
            "INSERT INTO work_packages "
            "(id, group_id, milestone_id, title, description, status, risk_level, "
            " review_scope, created_by, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?)",
            (
                package_id,
                group_id,
                milestone_id,
                title,
                description,
                risk_level,
                review_scope,
                created_by,
                now,
                now,
            ),
        )
        package = await self.get_work_package(package_id)
        if package is None:
            raise ValueError(f"Work package not found after create: {package_id}")
        return package

    async def get_work_package(self, package_id: str) -> dict | None:
        return await self._db.execute_fetchone(
            "SELECT * FROM work_packages WHERE id = ?",
            (package_id,),
        )

    async def get_group_work_packages(self, group_id: str) -> list[dict]:
        return await self._db.execute_fetchall(
            "SELECT * FROM work_packages WHERE group_id = ? ORDER BY created_at",
            (group_id,),
        )

    async def ensure_default_work_package(self, group_id: str) -> dict:
        existing = await self._db.execute_fetchone(
            "SELECT * FROM work_packages WHERE group_id = ? "
            "AND milestone_id IS NULL ORDER BY created_at LIMIT 1",
            (group_id,),
        )
        if existing:
            return existing
        group = await self._db.execute_fetchone(
            "SELECT title FROM groups WHERE id = ?",
            (group_id,),
        )
        if group is None:
            raise ValueError(f"Group not found: {group_id}")
        return await self.create_work_package(
            group_id=group_id,
            title=f"Default package for {group['title']}",
            description="Automatically created to keep existing tasks grouped.",
            created_by="system",
            risk_level="medium",
            review_scope=DEFAULT_PACKAGE_REVIEW_SCOPE,
        )
```

- [ ] **Step 4: Add package arguments to `create_task`**

Change the `TaskBoard.create_task` signature:

```python
        work_package_id: str | None = None,
        milestone_id: str | None = None,
        review_scope: str | None = None,
```

Insert before task id generation:

```python
        if work_package_id is None:
            package = await self.ensure_default_work_package(group_id)
            work_package_id = package["id"]
            milestone_id = milestone_id or package.get("milestone_id")
        else:
            package = await self.get_work_package(work_package_id)
            if package is None:
                raise ValueError(f"Work package not found: {work_package_id}")
            if package["group_id"] != group_id:
                raise ValueError(
                    f"Work package {work_package_id} does not belong to group {group_id}"
                )
            milestone_id = milestone_id or package.get("milestone_id")
```

In the task insert SQL, add columns:

```python
            " work_package_id, milestone_id, review_scope, "
```

Add matching values:

```python
                work_package_id,
                milestone_id,
                review_scope,
```

Add return fields to the `task` dict:

```python
            "work_package_id": work_package_id,
            "milestone_id": milestone_id,
            "review_scope": review_scope,
```

- [ ] **Step 5: Run package tests**

Run:

```bash
.venv/bin/pytest tests/test_work_packages.py -q
```

Expected: all tests in this file pass.

- [ ] **Step 6: Run existing task-board gate tests**

Run:

```bash
.venv/bin/pytest tests/test_task_board_system_gates.py tests/test_task_board.py -q
```

Expected: all selected tests pass. If legacy tests compare exact task dict keys, update expected dicts to include `work_package_id`, `milestone_id`, and `review_scope`.

- [ ] **Step 7: Commit package CRUD**

Run:

```bash
git add src/taskbrew/orchestrator/task_board.py tests/test_work_packages.py
git commit -m "feat: assign tasks to work packages"
```

---

### Task 3: Package Status Reconciliation

**Files:**
- Modify: `src/taskbrew/orchestrator/task_board.py`
- Test: `tests/test_work_packages.py`

- [ ] **Step 1: Add status reconciliation tests**

Append:

```python
async def test_package_status_tracks_child_task_progress(board: TaskBoard):
    group = await board.create_group(title="Feature", created_by="pm")
    package = await board.create_work_package(
        group_id=group["id"],
        title="Build package",
        created_by="architect-1",
    )
    task = await board.create_task(
        group_id=group["id"],
        work_package_id=package["id"],
        title="Build widget",
        task_type="implementation",
        assigned_to="coder",
        created_by="architect-1",
    )

    await board.apply_backlog_intake_decision(
        task["id"],
        needs_review=False,
        reason="Covered by package review.",
    )
    claimed = await board.claim_task("coder", "coder-1")
    assert claimed["id"] == task["id"]

    package = await board.get_work_package(package["id"])
    assert package["status"] == "in_progress"

    await board.complete_task_with_output(task["id"], "Done.")
    package = await board.get_work_package(package["id"])
    assert package["status"] == "review"
    assert package["review_status"] == "pending"


async def test_failed_child_blocks_package_review(board: TaskBoard):
    group = await board.create_group(title="Feature", created_by="pm")
    package = await board.create_work_package(
        group_id=group["id"],
        title="Build package",
        created_by="architect-1",
    )
    task = await board.create_task(
        group_id=group["id"],
        work_package_id=package["id"],
        title="Build widget",
        task_type="implementation",
        assigned_to="coder",
        created_by="architect-1",
    )
    await board.apply_backlog_intake_decision(
        task["id"],
        needs_review=False,
        reason="Covered by package review.",
    )
    claimed = await board.claim_task("coder", "coder-1")
    assert claimed["id"] == task["id"]
    await board.fail_task(task["id"], reason="Tests failed")

    package = await board.get_work_package(package["id"])
    assert package["status"] == "blocked"
    assert package["review_status"] is None
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```bash
.venv/bin/pytest tests/test_work_packages.py::test_package_status_tracks_child_task_progress tests/test_work_packages.py::test_failed_child_blocks_package_review -q
```

Expected: failures because package status is not reconciled.

- [ ] **Step 3: Add reconciliation helpers**

In `TaskBoard`, add:

```python
    async def reconcile_work_package_status(self, package_id: str) -> dict | None:
        package = await self.get_work_package(package_id)
        if package is None:
            return None
        if package["status"] in ("review", "waiting_revision", "completed", "rejected"):
            return package

        tasks = await self._db.execute_fetchall(
            "SELECT id, status, completion_checks FROM tasks "
            "WHERE work_package_id = ? ORDER BY created_at",
            (package_id,),
        )
        now = _utcnow()
        if not tasks:
            target_status = "pending"
            review_status = None
        elif any(t["status"] in ("failed", "rejected", "cancelled") for t in tasks):
            target_status = "blocked"
            review_status = None
        elif any(t["status"] in ("backlog", "blocked", "pending") for t in tasks):
            target_status = "blocked" if any(t["status"] == "blocked" for t in tasks) else "pending"
            review_status = None
        elif any(t["status"] == "in_progress" for t in tasks):
            target_status = "in_progress"
            review_status = None
        elif all(t["status"] == "completed" for t in tasks):
            if package.get("review_scope") == NO_REVIEW_SCOPE:
                target_status = "completed"
                review_status = "skipped"
            else:
                target_status = "review"
                review_status = "pending"
        else:
            target_status = package["status"]
            review_status = package.get("review_status")

        rows = await self._db.execute_returning(
            "UPDATE work_packages SET status = ?, review_status = ?, updated_at = ? "
            "WHERE id = ? RETURNING *",
            (target_status, review_status, now, package_id),
        )
        updated = rows[0] if rows else await self.get_work_package(package_id)
        if updated and updated["status"] == "review":
            await self.ensure_review_gate(
                entity_type="work_package",
                entity_id=package_id,
                group_id=updated["group_id"],
                max_rounds=int(updated.get("max_review_rounds") or DEFAULT_MAX_REVIEW_ROUNDS),
            )
        return updated

    async def reconcile_task_package(self, task_id: str) -> None:
        task = await self.get_task(task_id)
        if task and task.get("work_package_id"):
            await self.reconcile_work_package_status(task["work_package_id"])
```

Add `ensure_review_gate`:

```python
    async def ensure_review_gate(
        self,
        *,
        entity_type: str,
        entity_id: str,
        group_id: str,
        max_rounds: int = DEFAULT_MAX_REVIEW_ROUNDS,
    ) -> dict:
        existing = await self._db.execute_fetchone(
            "SELECT * FROM review_gates WHERE entity_type = ? AND entity_id = ?",
            (entity_type, entity_id),
        )
        if existing:
            return existing
        await self._db.register_prefix("RG")
        gate_id = await self._db.generate_task_id("RG")
        now = _utcnow()
        await self._db.execute(
            "INSERT INTO review_gates "
            "(id, entity_type, entity_id, group_id, status, review_round, "
            " max_review_rounds, system_gate_runs, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 'pending', 0, ?, '[]', ?, ?)",
            (gate_id, entity_type, entity_id, group_id, max_rounds, now, now),
        )
        gate = await self._db.execute_fetchone(
            "SELECT * FROM review_gates WHERE id = ?",
            (gate_id,),
        )
        if gate is None:
            raise ValueError(f"Review gate not found after create: {gate_id}")
        return gate
```

- [ ] **Step 4: Call reconciliation from task lifecycle**

In `create_task`, after possible dependency reconciliation and before return:

```python
        if task.get("work_package_id"):
            await self.reconcile_work_package_status(task["work_package_id"])
```

In `claim_task`, after the row is claimed:

```python
        if result.get("work_package_id"):
            await self.reconcile_work_package_status(result["work_package_id"])
```

In `complete_task` and `complete_task_with_output`, after the task update:

```python
        await self.reconcile_task_package(task_id)
```

In `reject_task`, `fail_task`, and `cancel_task`, add the same call after updating the task row.

- [ ] **Step 5: Run reconciliation tests**

Run:

```bash
.venv/bin/pytest tests/test_work_packages.py -q
```

Expected: package status tests pass.

- [ ] **Step 6: Commit reconciliation**

Run:

```bash
git add src/taskbrew/orchestrator/task_board.py tests/test_work_packages.py
git commit -m "feat: reconcile work package status"
```

---

### Task 4: Package Review Gates In SystemGateManager

**Files:**
- Modify: `src/taskbrew/orchestrator/task_board.py`
- Modify: `src/taskbrew/orchestrator/system_gates.py`
- Test: `tests/test_system_gates.py`
- Test: `tests/test_work_packages.py`

- [ ] **Step 1: Add package review tests**

Append to `tests/test_system_gates.py`:

```python
async def test_process_pending_once_approves_package_review(
    board: TaskBoard,
):
    group = await _create_group(board)
    package = await board.create_work_package(
        group_id=group["id"],
        title="Package review target",
        created_by="architect-1",
    )
    task = await board.create_task(
        group_id=group["id"],
        work_package_id=package["id"],
        title="Build package part",
        task_type="implementation",
        assigned_to="coder",
    )
    await board.apply_backlog_intake_decision(
        task["id"],
        needs_review=False,
        reason="Covered by package review.",
    )
    claimed = await board.claim_task("coder", "coder-1")
    assert claimed["id"] == task["id"]
    await board.complete_task_with_output(task["id"], "Implemented package part.")

    analyzer = FakeAnalyzer(
        review_results=[
            ReviewResult(outcome="approved", reason="Package satisfies spec.")
        ]
    )
    manager = SystemGateManager(board=board, analyzer=analyzer)

    counts = await manager.process_pending_once()

    assert counts["package_review"] == 1
    updated = await board.get_work_package(package["id"])
    assert updated["status"] == "completed"
    assert updated["review_status"] == "approved"
```

- [ ] **Step 2: Run the package review test and verify it fails**

Run:

```bash
.venv/bin/pytest tests/test_system_gates.py::test_process_pending_once_approves_package_review -q
```

Expected: failure because package review gates are not processed.

- [ ] **Step 3: Add review gate append helper**

In `TaskBoard`, add:

```python
    async def _append_review_gate_run(self, gate_id: str, run: dict) -> None:
        row = await self._db.execute_fetchone(
            "SELECT system_gate_runs FROM review_gates WHERE id = ?",
            (gate_id,),
        )
        if row is None:
            raise ValueError(f"Review gate not found: {gate_id}")
        runs = self._json_list(row.get("system_gate_runs"))
        runs.append(run)
        await self._db.execute(
            "UPDATE review_gates SET system_gate_runs = ?, updated_at = ? "
            "WHERE id = ?",
            (json.dumps(runs), _utcnow(), gate_id),
        )
```

Add package approval/rejection methods:

```python
    async def approve_work_package_review(self, package_id: str, *, reason: str) -> dict:
        package = await self.get_work_package(package_id)
        if package is None:
            raise ValueError(f"Work package not found: {package_id}")
        now = _utcnow()
        rows = await self._db.execute_returning(
            "UPDATE work_packages SET status = 'completed', review_status = 'approved', "
            "review_reason = ?, completed_at = ?, updated_at = ? "
            "WHERE id = ? AND status = 'review' RETURNING *",
            (reason, now, now, package_id),
        )
        updated = rows[0] if rows else await self.get_work_package(package_id)
        gate = await self._db.execute_fetchone(
            "SELECT id FROM review_gates WHERE entity_type = 'work_package' "
            "AND entity_id = ?",
            (package_id,),
        )
        if gate:
            await self._db.execute(
                "UPDATE review_gates SET status = 'completed', outcome = 'approved', "
                "reason = ?, completed_at = ?, updated_at = ? WHERE id = ?",
                (reason, now, now, gate["id"]),
            )
            await self._append_review_gate_run(
                gate["id"],
                {"gate": "review", "outcome": "approved", "reason": reason, "finished_at": now},
            )
        return updated

    async def reject_work_package_review(self, package_id: str, *, reason: str) -> dict:
        package = await self.get_work_package(package_id)
        if package is None:
            raise ValueError(f"Work package not found: {package_id}")
        now = _utcnow()
        rows = await self._db.execute_returning(
            "UPDATE work_packages SET status = 'rejected', review_status = 'rejected', "
            "review_reason = ?, updated_at = ? "
            "WHERE id = ? AND status IN ('review', 'waiting_revision') RETURNING *",
            (reason, now, package_id),
        )
        updated = rows[0] if rows else await self.get_work_package(package_id)
        gate = await self._db.execute_fetchone(
            "SELECT id FROM review_gates WHERE entity_type = 'work_package' "
            "AND entity_id = ?",
            (package_id,),
        )
        if gate:
            await self._db.execute(
                "UPDATE review_gates SET status = 'completed', outcome = 'rejected', "
                "reason = ?, completed_at = ?, updated_at = ? WHERE id = ?",
                (reason, now, now, gate["id"]),
            )
            await self._append_review_gate_run(
                gate["id"],
                {"gate": "review", "outcome": "rejected", "reason": reason, "finished_at": now},
            )
        return updated
```

- [ ] **Step 4: Add package context and processing**

In `SystemGateManager.process_pending_once`, change the count seed:

```python
        counts = {"backlog": 0, "review": 0, "package_review": 0}
```

Add before `return counts`:

```python
        package_rows = await self._board._db.execute_fetchall(
            "SELECT rg.id, rg.entity_id "
            "FROM review_gates rg "
            "JOIN work_packages wp ON wp.id = rg.entity_id "
            "WHERE rg.entity_type = 'work_package' "
            "AND rg.status IN ('pending', 'failed') "
            "AND wp.status = 'review' "
            "ORDER BY rg.created_at LIMIT ?",
            (self._batch_size,),
        )
        for row in package_rows:
            if await self.process_package_review_gate(row["id"]):
                counts["package_review"] += 1
        return counts
```

Add:

```python
    async def _package_review_context(self, package_id: str) -> dict:
        package = await self._board.get_work_package(package_id)
        if package is None:
            raise ValueError(f"Work package not found: {package_id}")
        tasks = await self._board._db.execute_fetchall(
            "SELECT id, title, status, task_type, assigned_to, priority, "
            "output_text, completion_checks, branch_name, parent_branch "
            "FROM tasks WHERE work_package_id = ? ORDER BY created_at",
            (package_id,),
        )
        group = await self._board._db.execute_fetchone(
            "SELECT * FROM groups WHERE id = ?",
            (package["group_id"],),
        )
        return {"entity_type": "work_package", "package": package, "group": group, "tasks": tasks}

    async def process_package_review_gate(self, gate_id: str) -> bool:
        gate = await self._board._db.execute_fetchone(
            "SELECT * FROM review_gates WHERE id = ? AND entity_type = 'work_package'",
            (gate_id,),
        )
        if gate is None:
            return False
        package = await self._board.get_work_package(gate["entity_id"])
        if package is None or package["status"] != "review":
            return False
        now = _utcnow()
        rows = await self._board._db.execute_returning(
            "UPDATE review_gates SET status = 'running', updated_at = ? "
            "WHERE id = ? AND status IN ('pending', 'failed') RETURNING *",
            (now, gate_id),
        )
        if not rows:
            return False
        await self._board._append_review_gate_run(
            gate_id,
            {"gate": "review", "outcome": "running", "started_at": now},
        )
        result = await self._analyzer.review_completed_task(
            await self._package_review_context(package["id"])
        )
        if result.outcome == "approved":
            await self._board.approve_work_package_review(package["id"], reason=result.reason)
            return True
        if result.outcome == "rejected":
            await self._board.reject_work_package_review(package["id"], reason=result.reason)
            return True
        if result.outcome == "failed_review":
            await self._board._db.execute(
                "UPDATE review_gates SET status = 'failed', outcome = 'failed_review', "
                "reason = ?, updated_at = ? WHERE id = ?",
                (result.reason, _utcnow(), gate_id),
            )
            return True
        if result.outcome == "needs_revision":
            await self._board._db.execute(
                "UPDATE work_packages SET status = 'waiting_revision', "
                "review_status = 'waiting_revision', review_reason = ?, updated_at = ? "
                "WHERE id = ?",
                (result.reason, _utcnow(), package["id"]),
            )
            await self._board._db.execute(
                "UPDATE review_gates SET status = 'waiting_revision', outcome = 'needs_revision', "
                "reason = ?, updated_at = ? WHERE id = ?",
                (result.reason, _utcnow(), gate_id),
            )
            return True
        raise SystemGateAnalysisError(f"Unsupported package review outcome: {result.outcome}")
```

- [ ] **Step 5: Run package review tests**

Run:

```bash
.venv/bin/pytest tests/test_system_gates.py::test_process_pending_once_approves_package_review -q
```

Expected: pass.

- [ ] **Step 6: Run system gate suite**

Run:

```bash
.venv/bin/pytest tests/test_system_gates.py tests/test_task_board_system_gates.py -q
```

Expected: pass. Update expected `counts` dicts in existing tests to include `"package_review": 0`.

- [ ] **Step 7: Commit package review gates**

Run:

```bash
git add src/taskbrew/orchestrator/task_board.py src/taskbrew/orchestrator/system_gates.py tests/test_system_gates.py tests/test_task_board_system_gates.py tests/test_work_packages.py
git commit -m "feat: process work package review gates"
```

---

### Task 5: Dashboard And MCP API Surface

**Files:**
- Modify: `src/taskbrew/dashboard/models.py`
- Modify: `src/taskbrew/dashboard/routers/tasks.py`
- Modify: `src/taskbrew/tools/task_tools.py`
- Test: `tests/test_dashboard_api.py`

- [ ] **Step 1: Add API tests**

Append to `tests/test_dashboard_api.py`:

```python
async def test_work_package_api_round_trip(client, board):
    group = await board.create_group(title="Feature", created_by="pm")

    response = await client.post(
        "/api/work-packages",
        json={
            "group_id": group["id"],
            "title": "Dashboard live updates",
            "description": "Reviewable dashboard slice.",
            "created_by": "architect-1",
            "risk_level": "medium",
        },
    )
    assert response.status_code == 200
    package = response.json()
    assert package["id"].startswith("WP-")

    task_response = await client.post(
        "/api/tasks",
        json={
            "group_id": group["id"],
            "work_package_id": package["id"],
            "title": "Implement websocket refresh",
            "assigned_to": "coder",
            "assigned_by": "human",
            "task_type": "implementation",
            "priority": "high",
        },
    )
    assert task_response.status_code == 200
    assert task_response.json()["work_package_id"] == package["id"]

    board_response = await client.get(f"/api/work-packages/board?group_id={group['id']}")
    assert board_response.status_code == 200
    data = board_response.json()
    assert data["columns"]["pending"][0]["id"] == package["id"]
    assert data["columns"]["pending"][0]["task_counts"]["total"] == 1
```

- [ ] **Step 2: Run API test and verify it fails**

Run:

```bash
.venv/bin/pytest tests/test_dashboard_api.py::test_work_package_api_round_trip -q
```

Expected: 404 or 422 because endpoints/models are missing.

- [ ] **Step 3: Extend models**

In `src/taskbrew/dashboard/models.py`, update `CreateTaskBody`:

```python
    work_package_id: Optional[str] = None
    milestone_id: Optional[str] = None
    review_scope: Optional[str] = None
```

Add:

```python
class CreateWorkPackageBody(BaseModel):
    group_id: str
    title: str
    description: Optional[str] = None
    milestone_id: Optional[str] = None
    created_by: Optional[str] = None
    risk_level: str = "medium"
    review_scope: str = "work_package"
```

Import `CreateWorkPackageBody` in `src/taskbrew/dashboard/routers/tasks.py`.

- [ ] **Step 4: Pass task package fields**

In `/api/tasks`, update `orch.task_board.create_task` call:

```python
        work_package_id=body.work_package_id,
        milestone_id=body.milestone_id,
        review_scope=body.review_scope,
```

- [ ] **Step 5: Add package endpoints**

In `src/taskbrew/dashboard/routers/tasks.py`, add:

```python
@router.post("/api/work-packages")
async def create_work_package(body: CreateWorkPackageBody):
    orch = get_orch()
    package = await orch.task_board.create_work_package(
        group_id=body.group_id,
        title=body.title,
        description=body.description,
        milestone_id=body.milestone_id,
        created_by=body.created_by,
        risk_level=body.risk_level,
        review_scope=body.review_scope,
    )
    await orch.event_bus.emit(
        "work_package.created",
        {"work_package_id": package["id"], "group_id": package["group_id"]},
    )
    return package


@router.get("/api/work-packages")
async def list_work_packages(group_id: str):
    orch = get_orch()
    return await orch.task_board.get_group_work_packages(group_id)


@router.get("/api/work-packages/board")
async def get_work_package_board(group_id: str | None = None):
    orch = get_orch()
    return await orch.task_board.get_work_package_board(group_id=group_id)
```

Add this method to `TaskBoard`:

```python
    async def get_work_package_board(self, group_id: str | None = None) -> dict:
        params: list = []
        where = ""
        if group_id:
            where = "WHERE group_id = ?"
            params.append(group_id)
        packages = await self._db.execute_fetchall(
            f"SELECT * FROM work_packages {where} ORDER BY created_at",
            tuple(params),
        )
        columns = {status: [] for status in PACKAGE_STATUSES}
        for package in packages:
            counts = await self._db.execute_fetchall(
                "SELECT status, COUNT(*) AS count FROM tasks "
                "WHERE work_package_id = ? GROUP BY status",
                (package["id"],),
            )
            task_counts = {"total": 0}
            for row in counts:
                task_counts[row["status"]] = row["count"]
                task_counts["total"] += row["count"]
            card = dict(package)
            card["task_counts"] = task_counts
            columns.setdefault(package["status"], []).append(card)
        return {"columns": columns, "packages": packages}
```

- [ ] **Step 6: Extend MCP create_task**

In `src/taskbrew/tools/task_tools.py`, add parameter:

```python
        work_package_id: str = "",
```

Add to docstring:

```text
            work_package_id: Optional Work Package ID. Use this when creating tasks for a planned package.
```

Before request:

```python
        if work_package_id:
            payload["work_package_id"] = work_package_id
```

- [ ] **Step 7: Run API tests**

Run:

```bash
.venv/bin/pytest tests/test_dashboard_api.py::test_work_package_api_round_trip tests/test_tools.py -q
```

Expected: pass.

- [ ] **Step 8: Commit API surface**

Run:

```bash
git add src/taskbrew/dashboard/models.py src/taskbrew/dashboard/routers/tasks.py src/taskbrew/tools/task_tools.py tests/test_dashboard_api.py
git commit -m "feat: expose work package APIs"
```

---

### Task 6: Default Role Prompt Updates

**Files:**
- Modify: `config/roles/pm.yaml`
- Modify: `config/roles/architect.yaml`
- Modify: `src/taskbrew/project_manager.py`
- Test: `tests/test_config_loader.py`

- [ ] **Step 1: Update PM prompt**

In `config/roles/pm.yaml`, under task creation guidelines, replace the “architect tasks” guidance with:

```yaml
  Task creation guidelines:
  - Create a PRD that identifies whether the goal is tiny, small, medium, or large.
  - For tiny goals, create one minimal architect task or one minimal Work Package.
  - For small and medium goals, describe reviewable Work Packages in the PRD.
  - For large goals, describe Milestones first, then the Work Packages inside each Milestone.
  - Create architect tasks that ask for Work Package designs, not isolated implementation fragments.
  - Use the group_id from your task context (shown as "Group: GRP-XXX").
  - Set assigned_to: "architect", task_type: "tech_design".
  - Include full PRD content, proposed package boundaries, and acceptance criteria in the description.
  - Set priority: "high" for core packages, "medium" for enhancements.
```

- [ ] **Step 2: Update Architect prompt**

In `config/roles/architect.yaml`, add:

```yaml
  Work Package responsibilities:
  - Turn PRDs into coherent Work Packages that can be reviewed independently.
  - Keep a Work Package focused on a deliverable slice, not a single implementation step.
  - When creating coder tasks, include the Work Package ID once the package exists.
  - Prefer package-level system review unless a specific task is high risk.
  - Include expected build, test, lint, and manual verification commands for each package.
```

- [ ] **Step 3: Mirror defaults in project scaffolding**

In `src/taskbrew/project_manager.py`, update `DEFAULT_ROLES["pm"]["system_prompt"]` and `DEFAULT_ROLES["architect"]["system_prompt"]` with the same text so newly scaffolded projects match `config/roles`.

- [ ] **Step 4: Run config tests**

Run:

```bash
.venv/bin/pytest tests/test_config_loader.py -q
```

Expected: pass.

- [ ] **Step 5: Commit prompt updates**

Run:

```bash
git add config/roles/pm.yaml config/roles/architect.yaml src/taskbrew/project_manager.py tests/test_config_loader.py
git commit -m "feat: guide defaults toward work packages"
```

---

### Task 7: Minimal Dashboard Package Visibility

**Files:**
- Modify: `src/taskbrew/dashboard/static/js/dashboard-core.js`
- Modify: `src/taskbrew/dashboard/static/js/dashboard-ui.js`
- Modify: `src/taskbrew/dashboard/static/css/main.css`
- Test: browser verification

- [ ] **Step 1: Add package board loader**

In `dashboard-core.js`, add:

```javascript
async function loadPackageBoard() {
    const params = new URLSearchParams();
    if (state.filters.group) params.set('group_id', state.filters.group);
    const resp = await fetch('/api/work-packages/board?' + params.toString());
    if (!resp.ok) return null;
    return await resp.json();
}
```

- [ ] **Step 2: Render package cards before task fallback**

In the existing board refresh path, call:

```javascript
const packageBoard = await loadPackageBoard();
if (packageBoard && packageBoard.packages && packageBoard.packages.length > 0) {
    renderPackageBoard(packageBoard);
    return;
}
```

Add:

```javascript
function renderPackageBoard(data) {
    const statuses = ['backlog', 'pending', 'in_progress', 'review', 'blocked', 'completed', 'rejected', 'failed'];
    for (const status of statuses) {
        const config = BOARD_COLUMN_CONFIG[status];
        if (!config) continue;
        const el = document.getElementById(config.el);
        const count = document.getElementById(config.count);
        const packages = (data.columns && data.columns[status]) || [];
        if (count) count.textContent = packages.length;
        if (!el) continue;
        el.innerHTML = packages.map(renderPackageCard).join('');
    }
}

function renderPackageCard(pkg) {
    const counts = pkg.task_counts || {};
    const completed = counts.completed || 0;
    const total = counts.total || 0;
    const review = pkg.review_status ? `<span class="badge badge-system-gate">${escapeHtml(pkg.review_status)}</span>` : '';
    return `
        <div class="task-card package-card" data-package-id="${escapeHtml(pkg.id)}">
            <div class="task-header">
                <span class="task-id">${escapeHtml(pkg.id)}</span>
                <span class="task-role role-system">Work Package</span>
            </div>
            <div class="task-title">${escapeHtml(pkg.title)}</div>
            <div class="package-progress">${completed}/${total} tasks complete</div>
            <div class="task-badges">${review}<span class="badge">${escapeHtml(pkg.risk_level || 'medium')}</span></div>
        </div>
    `;
}
```

- [ ] **Step 3: Add package card styles**

In `main.css`, add:

```css
.package-card {
    border-left: 3px solid var(--accent-blue, #3b82f6);
}

.package-progress {
    color: var(--text-secondary, #94a3b8);
    font-size: 12px;
    margin-top: 6px;
}
```

- [ ] **Step 4: Check JavaScript syntax**

Run:

```bash
node --check src/taskbrew/dashboard/static/js/dashboard-core.js
node --check src/taskbrew/dashboard/static/js/dashboard-ui.js
```

Expected: both commands pass with no output.

- [ ] **Step 5: Commit dashboard visibility**

Run:

```bash
git add src/taskbrew/dashboard/static/js/dashboard-core.js src/taskbrew/dashboard/static/js/dashboard-ui.js src/taskbrew/dashboard/static/css/main.css
git commit -m "feat: show work packages on board"
```

---

### Task 8: Verification Sweep

**Files:**
- No planned source edits unless verification finds a regression.

- [ ] **Step 1: Run focused tests**

Run:

```bash
.venv/bin/pytest tests/test_work_packages.py tests/test_system_gates.py tests/test_dashboard_api.py -q
```

Expected: pass.

- [ ] **Step 2: Run broader task-board tests**

Run:

```bash
.venv/bin/pytest tests/test_task_board.py tests/test_task_board_system_gates.py tests/test_agent_loop.py -q
```

Expected: pass.

- [ ] **Step 3: Run lint**

Run:

```bash
.venv/bin/ruff check src/taskbrew/orchestrator src/taskbrew/dashboard src/taskbrew/tools tests/test_work_packages.py tests/test_system_gates.py tests/test_dashboard_api.py
```

Expected: `All checks passed!`

- [ ] **Step 4: Run JavaScript syntax checks**

Run:

```bash
node --check src/taskbrew/dashboard/static/js/dashboard-core.js
node --check src/taskbrew/dashboard/static/js/dashboard-ui.js
```

Expected: both commands pass with no output.

- [ ] **Step 5: Browser verification**

Start TaskBrew only if the user allows a restart. If the running Add Num project is still active, do not restart it without explicit permission.

When a safe dev server is available:

```text
Open the dashboard, create a test goal, confirm Work Package cards appear, expand one package, and confirm child task progress and review state render without refreshing.
```

- [ ] **Step 6: Final commit if verification changes were needed**

If verification required edits:

```bash
git add <changed-files>
git commit -m "fix: stabilize work package review flow"
```

If no edits were needed, skip this commit.

---

## Self-Review Notes

Spec coverage:

- First-class Work Packages: covered by Tasks 1, 2, 5, and 7.
- Optional Milestones: covered at persistence level in Task 1; milestone UI flow is intentionally not the first dashboard surface.
- Adaptive `review_scope`: covered by Tasks 1, 2, 3, and 4.
- Package readiness and failed-task blocking: covered by Task 3.
- Package review gate outcomes: covered by Task 4.
- Dashboard visibility: covered by Task 7.
- Existing task-level review compatibility: preserved by leaving `needs_review` and task review gates intact.
- Integration target correctness: covered by package context in Task 4 and verification expectations in Task 8.

Placeholder scan:

- The plan contains no blocked-out markers or unfinished task bodies.
- Deferred package integration branches are explicitly outside the first implementation path and are not needed for package review to function.

Type consistency:

- `work_package_id`, `milestone_id`, and `review_scope` are the same field names across schema, models, task creation, and API tests.
- `review_gates.entity_type='work_package'` is the only new gate type processed by this plan.
- Package statuses use the same strings in schema, reconciliation, board response, and dashboard rendering.
