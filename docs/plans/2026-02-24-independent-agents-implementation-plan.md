# Independent Agents Redesign — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the rigid sequential pipeline system with independent agents that create tasks for each other, backed by a DAG-based task board with group hierarchy, configurable routing, and real-time filtered dashboard.

**Architecture:** Config-driven role definitions (YAML) feed a role loader that validates routing at startup. A new SQLite schema tracks groups, tasks, dependencies, artifacts, and agent instances. Agents run continuous poll/claim/execute/handoff loops. The dashboard provides board/list/graph views with stacking filters, powered by WebSocket events.

**Tech Stack:** Python 3.10+, Claude Agent SDK, FastAPI, aiosqlite, Jinja2, vanilla JS, YAML configs.

**Design Doc:** `docs/plans/2026-02-24-independent-agents-redesign.md`

---

## Task 1: Config Directory & Team Settings

**Files:**
- Create: `config/team.yaml`
- Create: `src/taskbrew/config_loader.py`
- Test: `tests/test_config_loader.py`
- Modify: `src/taskbrew/config.py:19-31` (add new fields to OrchestratorConfig)

**Step 1: Write failing test for team config loading**

```python
# tests/test_config_loader.py
import pytest
from pathlib import Path
from taskbrew.config_loader import load_team_config, TeamConfig

def test_load_team_config(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "team.yaml").write_text("""
team_name: "Test Team"
database:
  path: "data/taskbrew.db"
dashboard:
  host: "127.0.0.1"
  port: 8420
artifacts:
  base_dir: "artifacts"
defaults:
  max_instances: 1
  poll_interval_seconds: 5
  idle_timeout_minutes: 30
approval_required: []
group_prefixes:
  pm: "FEAT"
  architect: "DEBT"
""")
    config = load_team_config(config_dir / "team.yaml")
    assert config.team_name == "Test Team"
    assert config.dashboard_port == 8420
    assert config.group_prefixes["pm"] == "FEAT"

def test_load_team_config_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_team_config(tmp_path / "nonexistent.yaml")
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_config_loader.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'taskbrew.config_loader'`

**Step 3: Create team.yaml config file**

```yaml
# config/team.yaml
team_name: "AI Development Team"

database:
  path: "data/taskbrew.db"

dashboard:
  host: "127.0.0.1"
  port: 8420

artifacts:
  base_dir: "artifacts"

defaults:
  max_instances: 1
  poll_interval_seconds: 5
  idle_timeout_minutes: 30
  auto_scale:
    enabled: false
    scale_up_threshold: 3
    scale_down_idle: 15

approval_required: []

group_prefixes:
  pm: "FEAT"
  architect: "DEBT"
```

**Step 4: Implement config_loader.py**

```python
# src/taskbrew/config_loader.py
from dataclasses import dataclass, field
from pathlib import Path
import yaml


@dataclass
class AutoScaleDefaults:
    enabled: bool = False
    scale_up_threshold: int = 3
    scale_down_idle: int = 15


@dataclass
class TeamConfig:
    team_name: str
    db_path: str
    dashboard_host: str
    dashboard_port: int
    artifacts_base_dir: str
    default_max_instances: int
    default_poll_interval: int
    default_idle_timeout: int
    default_auto_scale: AutoScaleDefaults
    approval_required: list[str]
    group_prefixes: dict[str, str]


def load_team_config(path: Path) -> TeamConfig:
    if not path.exists():
        raise FileNotFoundError(f"Team config not found: {path}")
    with open(path) as f:
        data = yaml.safe_load(f)
    defaults = data.get("defaults", {})
    auto_scale = defaults.get("auto_scale", {})
    return TeamConfig(
        team_name=data["team_name"],
        db_path=data["database"]["path"],
        dashboard_host=data["dashboard"]["host"],
        dashboard_port=data["dashboard"]["port"],
        artifacts_base_dir=data["artifacts"]["base_dir"],
        default_max_instances=defaults.get("max_instances", 1),
        default_poll_interval=defaults.get("poll_interval_seconds", 5),
        default_idle_timeout=defaults.get("idle_timeout_minutes", 30),
        default_auto_scale=AutoScaleDefaults(
            enabled=auto_scale.get("enabled", False),
            scale_up_threshold=auto_scale.get("scale_up_threshold", 3),
            scale_down_idle=auto_scale.get("scale_down_idle", 15),
        ),
        approval_required=data.get("approval_required", []),
        group_prefixes=data.get("group_prefixes", {}),
    )
```

**Step 5: Run test to verify it passes**

Run: `pytest tests/test_config_loader.py -v`
Expected: PASS

**Step 6: Commit**

```bash
git add config/team.yaml src/taskbrew/config_loader.py tests/test_config_loader.py
git commit -m "feat: add team config YAML and loader"
```

---

## Task 2: Role YAML Definitions

**Files:**
- Create: `config/roles/pm.yaml`
- Create: `config/roles/architect.yaml`
- Create: `config/roles/coder.yaml`
- Create: `config/roles/tester.yaml`
- Create: `config/roles/reviewer.yaml`
- Modify: `src/taskbrew/config_loader.py` (add RoleConfig and load_roles)
- Test: `tests/test_config_loader.py` (add role loading tests)

**Step 1: Write failing test for role loading**

```python
# tests/test_config_loader.py (append)
from taskbrew.config_loader import load_roles, RoleConfig

def test_load_roles(tmp_path):
    roles_dir = tmp_path / "roles"
    roles_dir.mkdir()
    (roles_dir / "pm.yaml").write_text("""
role: pm
display_name: "Product Manager"
prefix: "PM"
color: "#3b82f6"
emoji: "📋"
system_prompt: "You are a PM."
tools: [Read, Glob, Grep, WebSearch]
produces: [prd, goal_decomposition]
accepts: [goal, revision]
routes_to:
  - role: architect
    task_types: [tech_design]
can_create_groups: true
group_type: "FEAT"
max_instances: 1
requires_approval: [prd]
context_includes: [parent_artifact, root_artifact]
""")
    roles = load_roles(roles_dir)
    assert "pm" in roles
    assert roles["pm"].prefix == "PM"
    assert roles["pm"].can_create_groups is True
    assert "tech_design" in roles["pm"].routes_to[0].task_types

def test_load_roles_empty_dir(tmp_path):
    roles_dir = tmp_path / "roles"
    roles_dir.mkdir()
    roles = load_roles(roles_dir)
    assert roles == {}
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_config_loader.py::test_load_roles -v`
Expected: FAIL — `ImportError: cannot import name 'load_roles'`

**Step 3: Create all 5 role YAML files**

Create `config/roles/pm.yaml`:
```yaml
role: pm
display_name: "Product Manager"
prefix: "PM"
color: "#3b82f6"
emoji: "📋"

system_prompt: |
  You are a Product Manager on an AI development team.
  Your responsibilities:
  1. Decompose high-level goals into detailed PRDs with acceptance criteria
  2. Read the codebase to understand scope and dependencies
  3. Create well-scoped tasks for the Architect team
  4. You NEVER write code — only analysis and documentation

  When creating tasks for architects:
  - Include clear acceptance criteria
  - Reference specific files and patterns from your codebase analysis
  - Set priority based on dependency order and business value

tools: [Read, Glob, Grep, WebSearch]

produces: [prd, goal_decomposition, requirement]

accepts: [goal, revision]

routes_to:
  - role: architect
    task_types: [tech_design, architecture_review]

can_create_groups: true
group_type: "FEAT"

max_instances: 1

requires_approval: [prd]

context_includes:
  - parent_artifact
  - root_artifact
  - sibling_summary
```

Create `config/roles/architect.yaml`:
```yaml
role: architect
display_name: "Architect"
prefix: "AR"
color: "#8b5cf6"
emoji: "🏗️"

system_prompt: |
  You are a Software Architect on an AI development team.
  Your responsibilities:
  1. Create technical design documents for PRDs assigned to you
  2. Identify and document tech debt with concrete fix plans
  3. Review architecture docs created by peer architects
  4. You do NOT write implementation code

  When creating tasks for coders:
  - Be specific about the approach, files to modify, and patterns to follow
  - Break large designs into implementable chunks (1 chunk = 1 coder task)
  - Set priority based on dependency order

tools: [Read, Glob, Grep, Write, WebSearch]

produces: [tech_design, tech_debt, architecture_review]

accepts: [prd, architecture_review_request, rejection]

routes_to:
  - role: coder
    task_types: [implementation, bug_fix]
  - role: architect
    task_types: [architecture_review]

can_create_groups: true
group_type: "DEBT"

max_instances: 2

auto_scale:
  enabled: true
  scale_up_threshold: 4
  scale_down_idle: 20

requires_approval: [tech_design]

context_includes:
  - parent_artifact
  - root_artifact
  - sibling_summary
  - rejection_history
```

Create `config/roles/coder.yaml`:
```yaml
role: coder
display_name: "Coder"
prefix: "CD"
color: "#f59e0b"
emoji: "💻"

system_prompt: |
  You are a Software Engineer (Coder) on an AI development team.
  Your responsibilities:
  1. Implement features based on technical design documents
  2. Write clean, tested code on feature branches
  3. Make atomic commits with clear messages
  4. Create test and review tasks when implementation is complete

  When you finish implementing:
  - Create a task for Tester with status pending
  - Create a task for Reviewer with status blocked (blocked_by the tester task)
  - Include the branch name and files changed in your artifact

tools: [Read, Write, Edit, Bash, Glob, Grep]

produces: [implementation, bug_fix, revision]

accepts: [implementation, bug_fix, revision]

routes_to:
  - role: tester
    task_types: [qa_verification]
  - role: reviewer
    task_types: [code_review]

can_create_groups: false

max_instances: 3

auto_scale:
  enabled: true
  scale_up_threshold: 3
  scale_down_idle: 15

requires_approval: []

context_includes:
  - parent_artifact
  - root_artifact
  - sibling_summary
  - rejection_history
```

Create `config/roles/tester.yaml`:
```yaml
role: tester
display_name: "Tester"
prefix: "TS"
color: "#10b981"
emoji: "🧪"

system_prompt: |
  You are a QA Tester on an AI development team.
  Your responsibilities:
  1. Write and run tests for code implementations
  2. Measure test coverage and report results
  3. Verify acceptance criteria from the original PRD
  4. Document any bugs found with reproduction steps

  Your test report should include:
  - Tests written and their pass/fail status
  - Coverage metrics
  - Any bugs or issues found
  - Whether acceptance criteria are met

tools: [Read, Write, Edit, Bash, Glob, Grep]

produces: [qa_verification, test_suite, regression_test]

accepts: [qa_verification]

routes_to: []

can_create_groups: false

max_instances: 2

auto_scale:
  enabled: true
  scale_up_threshold: 3
  scale_down_idle: 15

requires_approval: []

context_includes:
  - parent_artifact
  - root_artifact
```

Create `config/roles/reviewer.yaml`:
```yaml
role: reviewer
display_name: "Code Reviewer"
prefix: "RV"
color: "#ec4899"
emoji: "🔍"

system_prompt: |
  You are a Code Reviewer on an AI development team.
  Your responsibilities:
  1. Review code for quality, security, and maintainability
  2. Check that implementation matches the technical design
  3. Verify test coverage is adequate
  4. Approve the task if satisfactory, or reject with specific feedback

  When rejecting:
  - Create a revision task assigned to the coder with clear feedback
  - Or create an architecture_revision task for the architect if the design is flawed
  - Reference specific files and line numbers in your feedback

tools: [Read, Glob, Grep]

produces: [code_review, approval, rejection]

accepts: [code_review]

routes_to:
  - role: coder
    task_types: [revision]
  - role: architect
    task_types: [rejection]

can_create_groups: false

max_instances: 1

requires_approval: []

context_includes:
  - parent_artifact
  - root_artifact
  - sibling_summary
```

**Step 4: Add RoleConfig and load_roles to config_loader.py**

```python
# Add to src/taskbrew/config_loader.py

@dataclass
class RouteTarget:
    role: str
    task_types: list[str]


@dataclass
class AutoScaleConfig:
    enabled: bool = False
    scale_up_threshold: int = 3
    scale_down_idle: int = 15


@dataclass
class RoleConfig:
    role: str
    display_name: str
    prefix: str
    color: str
    emoji: str
    system_prompt: str
    tools: list[str]
    produces: list[str]
    accepts: list[str]
    routes_to: list[RouteTarget]
    can_create_groups: bool = False
    group_type: str | None = None
    max_instances: int = 1
    auto_scale: AutoScaleConfig | None = None
    requires_approval: list[str] = field(default_factory=list)
    context_includes: list[str] = field(default_factory=list)


def load_roles(roles_dir: Path) -> dict[str, RoleConfig]:
    roles = {}
    if not roles_dir.exists():
        return roles
    for path in sorted(roles_dir.glob("*.yaml")):
        with open(path) as f:
            data = yaml.safe_load(f)
        auto_scale_data = data.get("auto_scale", {})
        auto_scale = AutoScaleConfig(
            enabled=auto_scale_data.get("enabled", False),
            scale_up_threshold=auto_scale_data.get("scale_up_threshold", 3),
            scale_down_idle=auto_scale_data.get("scale_down_idle", 15),
        ) if auto_scale_data else None
        role = RoleConfig(
            role=data["role"],
            display_name=data["display_name"],
            prefix=data["prefix"],
            color=data["color"],
            emoji=data.get("emoji", ""),
            system_prompt=data["system_prompt"],
            tools=data["tools"],
            produces=data.get("produces", []),
            accepts=data.get("accepts", []),
            routes_to=[
                RouteTarget(role=r["role"], task_types=r["task_types"])
                for r in data.get("routes_to", [])
            ],
            can_create_groups=data.get("can_create_groups", False),
            group_type=data.get("group_type"),
            max_instances=data.get("max_instances", 1),
            auto_scale=auto_scale,
            requires_approval=data.get("requires_approval", []),
            context_includes=data.get("context_includes", []),
        )
        roles[role.role] = role
    return roles
```

**Step 5: Run tests to verify they pass**

Run: `pytest tests/test_config_loader.py -v`
Expected: ALL PASS

**Step 6: Commit**

```bash
git add config/roles/ src/taskbrew/config_loader.py tests/test_config_loader.py
git commit -m "feat: add role YAML definitions and role loader"
```

---

## Task 3: Routing Validation

**Files:**
- Modify: `src/taskbrew/config_loader.py` (add validate_routing)
- Test: `tests/test_config_loader.py` (add validation tests)

**Step 1: Write failing tests for routing validation**

```python
# tests/test_config_loader.py (append)
from taskbrew.config_loader import validate_routing

def test_validate_routing_valid(tmp_path):
    """Valid routing: pm -> architect -> coder, no errors."""
    roles = _make_roles({
        "pm": {"routes_to": [{"role": "architect", "task_types": ["tech_design"]}], "produces": ["prd"], "accepts": ["goal"], "can_create_groups": True},
        "architect": {"routes_to": [{"role": "coder", "task_types": ["implementation"]}], "produces": ["tech_design"], "accepts": ["tech_design"]},
        "coder": {"routes_to": [], "produces": ["implementation"], "accepts": ["implementation"]},
    })
    errors = validate_routing(roles)
    assert errors == []

def test_validate_routing_missing_target():
    """Route to nonexistent role should error."""
    roles = _make_roles({
        "pm": {"routes_to": [{"role": "ghost", "task_types": ["x"]}], "produces": ["prd"], "accepts": ["goal"], "can_create_groups": True},
    })
    errors = validate_routing(roles)
    assert any("ghost" in e for e in errors)

def test_validate_routing_no_entry_point():
    """No role with can_create_groups should error."""
    roles = _make_roles({
        "coder": {"routes_to": [], "produces": ["impl"], "accepts": ["impl"]},
    })
    errors = validate_routing(roles)
    assert any("entry point" in e.lower() for e in errors)

def test_validate_routing_duplicate_prefix():
    """Duplicate prefixes should error."""
    roles = _make_roles({
        "pm": {"prefix": "PM", "routes_to": [], "produces": [], "accepts": ["goal"], "can_create_groups": True},
        "pm2": {"prefix": "PM", "routes_to": [], "produces": [], "accepts": ["goal"]},
    })
    errors = validate_routing(roles)
    assert any("prefix" in e.lower() for e in errors)


def _make_roles(specs: dict) -> dict:
    """Helper to build minimal RoleConfig dicts for testing."""
    from taskbrew.config_loader import RoleConfig, RouteTarget
    roles = {}
    for name, spec in specs.items():
        roles[name] = RoleConfig(
            role=name,
            display_name=name.title(),
            prefix=spec.get("prefix", name.upper()[:2]),
            color="#000",
            emoji="",
            system_prompt="test",
            tools=["Read"],
            produces=spec.get("produces", []),
            accepts=spec.get("accepts", []),
            routes_to=[RouteTarget(**r) for r in spec.get("routes_to", [])],
            can_create_groups=spec.get("can_create_groups", False),
        )
    return roles
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_config_loader.py::test_validate_routing_valid -v`
Expected: FAIL — `ImportError: cannot import name 'validate_routing'`

**Step 3: Implement validate_routing**

```python
# Add to src/taskbrew/config_loader.py

def validate_routing(roles: dict[str, RoleConfig]) -> list[str]:
    errors = []

    # Check: at least one role can create groups (entry point)
    if not any(r.can_create_groups for r in roles.values()):
        errors.append("No role has can_create_groups=true. Need at least one entry point.")

    # Check: all route targets exist
    for name, role in roles.items():
        for route in role.routes_to:
            if route.role not in roles:
                errors.append(f"Role '{name}' routes to '{route.role}' which does not exist.")

    # Check: unique prefixes
    prefixes = {}
    for name, role in roles.items():
        if role.prefix in prefixes:
            errors.append(
                f"Duplicate prefix '{role.prefix}' used by both "
                f"'{prefixes[role.prefix]}' and '{name}'."
            )
        prefixes[role.prefix] = name

    # Check: unique group_type prefixes
    group_types = {}
    for name, role in roles.items():
        if role.group_type:
            if role.group_type in group_types:
                errors.append(
                    f"Duplicate group_type '{role.group_type}' used by both "
                    f"'{group_types[role.group_type]}' and '{name}'."
                )
            group_types[role.group_type] = name

    return errors
```

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_config_loader.py -v -k "routing"`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add src/taskbrew/config_loader.py tests/test_config_loader.py
git commit -m "feat: add routing validation for role configs"
```

---

## Task 4: New Database Schema

**Files:**
- Create: `src/taskbrew/orchestrator/database.py`
- Test: `tests/test_database.py`

**Step 1: Write failing tests for database initialization and task ID generation**

```python
# tests/test_database.py
import pytest
from taskbrew.orchestrator.database import Database

@pytest.fixture
async def db(tmp_path):
    database = Database(str(tmp_path / "test.db"))
    await database.initialize()
    yield database
    await database.close()

@pytest.mark.asyncio
async def test_initialize_creates_tables(db):
    tables = await db.execute_fetchall(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    names = [t["name"] for t in tables]
    assert "groups" in names
    assert "tasks" in names
    assert "task_dependencies" in names
    assert "artifacts" in names
    assert "agent_instances" in names
    assert "id_sequences" in names
    assert "events" in names

@pytest.mark.asyncio
async def test_generate_task_id(db):
    await db.register_prefix("CD")
    id1 = await db.generate_task_id("CD")
    assert id1 == "CD-001"
    id2 = await db.generate_task_id("CD")
    assert id2 == "CD-002"

@pytest.mark.asyncio
async def test_generate_task_id_unregistered_prefix(db):
    with pytest.raises(ValueError, match="Unknown prefix"):
        await db.generate_task_id("XX")
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_database.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'taskbrew.orchestrator.database'`

**Step 3: Implement database.py with full schema**

```python
# src/taskbrew/orchestrator/database.py
import aiosqlite
from datetime import datetime, timezone


class Database:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def initialize(self):
        self._db = await aiosqlite.connect(self.db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA foreign_keys=ON")
        await self._create_tables()
        await self._create_indexes()
        await self._db.commit()

    async def _create_tables(self):
        await self._db.executescript("""
            CREATE TABLE IF NOT EXISTS groups (
                id           TEXT PRIMARY KEY,
                title        TEXT NOT NULL,
                origin       TEXT NOT NULL,
                status       TEXT DEFAULT 'active',
                created_by   TEXT NOT NULL,
                created_at   TEXT NOT NULL,
                completed_at TEXT
            );

            CREATE TABLE IF NOT EXISTS tasks (
                id               TEXT PRIMARY KEY,
                group_id         TEXT NOT NULL REFERENCES groups(id),
                parent_id        TEXT REFERENCES tasks(id),
                title            TEXT NOT NULL,
                description      TEXT,
                task_type        TEXT NOT NULL,
                priority         TEXT DEFAULT 'medium',
                assigned_to      TEXT NOT NULL,
                claimed_by       TEXT,
                status           TEXT DEFAULT 'pending',
                created_by       TEXT NOT NULL,
                created_at       TEXT NOT NULL,
                started_at       TEXT,
                completed_at     TEXT,
                rejection_reason TEXT,
                revision_of      TEXT REFERENCES tasks(id)
            );

            CREATE TABLE IF NOT EXISTS task_dependencies (
                task_id    TEXT NOT NULL REFERENCES tasks(id),
                blocked_by TEXT NOT NULL REFERENCES tasks(id),
                resolved   INTEGER DEFAULT 0,
                resolved_at TEXT,
                PRIMARY KEY (task_id, blocked_by),
                CHECK (task_id != blocked_by)
            );

            CREATE TABLE IF NOT EXISTS artifacts (
                id            TEXT PRIMARY KEY,
                task_id       TEXT NOT NULL REFERENCES tasks(id),
                file_path     TEXT NOT NULL,
                artifact_type TEXT DEFAULT 'output',
                created_at    TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS agent_instances (
                instance_id    TEXT PRIMARY KEY,
                role           TEXT NOT NULL,
                status         TEXT DEFAULT 'idle',
                current_task   TEXT REFERENCES tasks(id),
                started_at     TEXT NOT NULL,
                last_heartbeat TEXT
            );

            CREATE TABLE IF NOT EXISTS id_sequences (
                prefix   TEXT PRIMARY KEY,
                next_val INTEGER DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS events (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                group_id   TEXT,
                task_id    TEXT,
                agent_id   TEXT,
                data       TEXT,
                created_at TEXT NOT NULL
            );
        """)

    async def _create_indexes(self):
        await self._db.executescript("""
            CREATE INDEX IF NOT EXISTS idx_tasks_assignee_status
                ON tasks(assigned_to, status)
                WHERE status = 'pending' AND claimed_by IS NULL;
            CREATE INDEX IF NOT EXISTS idx_tasks_group
                ON tasks(group_id, status);
            CREATE INDEX IF NOT EXISTS idx_deps_blocked
                ON task_dependencies(blocked_by)
                WHERE resolved = 0;
            CREATE INDEX IF NOT EXISTS idx_tasks_parent
                ON tasks(parent_id);
            CREATE INDEX IF NOT EXISTS idx_events_group
                ON events(group_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_events_type
                ON events(event_type, created_at);
        """)

    async def register_prefix(self, prefix: str):
        await self._db.execute(
            "INSERT OR IGNORE INTO id_sequences (prefix, next_val) VALUES (?, 1)",
            (prefix,)
        )
        await self._db.commit()

    async def generate_task_id(self, prefix: str) -> str:
        cursor = await self._db.execute(
            "UPDATE id_sequences SET next_val = next_val + 1 "
            "WHERE prefix = ? RETURNING next_val",
            (prefix,)
        )
        row = await cursor.fetchone()
        if row is None:
            raise ValueError(f"Unknown prefix: {prefix}")
        await self._db.commit()
        seq = row[0] - 1  # we incremented, so subtract 1 for current
        return f"{prefix}-{seq:03d}"

    async def execute_fetchall(self, sql: str, params=()) -> list[dict]:
        cursor = await self._db.execute(sql, params)
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def execute_fetchone(self, sql: str, params=()) -> dict | None:
        cursor = await self._db.execute(sql, params)
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def execute(self, sql: str, params=()):
        await self._db.execute(sql, params)
        await self._db.commit()

    async def close(self):
        if self._db:
            await self._db.close()
```

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_database.py -v`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add src/taskbrew/orchestrator/database.py tests/test_database.py
git commit -m "feat: add new database schema with groups, tasks, dependencies"
```

---

## Task 5: Group & Task CRUD Operations

**Files:**
- Create: `src/taskbrew/orchestrator/task_board.py`
- Test: `tests/test_task_board.py`

**Step 1: Write failing tests for group and task creation**

```python
# tests/test_task_board.py
import pytest
from taskbrew.orchestrator.database import Database
from taskbrew.orchestrator.task_board import TaskBoard

@pytest.fixture
async def board(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    await db.initialize()
    board = TaskBoard(db)
    await board.register_prefixes({"pm": "PM", "architect": "AR", "coder": "CD"})
    yield board
    await db.close()

@pytest.mark.asyncio
async def test_create_group(board):
    group = await board.create_group(
        title="Add dark mode", origin="pm", created_by="pm-1"
    )
    assert group["id"].startswith("FEAT-")
    assert group["title"] == "Add dark mode"
    assert group["status"] == "active"

@pytest.mark.asyncio
async def test_create_task(board):
    group = await board.create_group(title="Test", origin="pm", created_by="pm-1")
    task = await board.create_task(
        group_id=group["id"],
        title="Create PRD",
        task_type="prd",
        assigned_to="architect",
        created_by="pm-1",
        priority="high",
    )
    assert task["id"].startswith("PM-")
    assert task["status"] == "pending"
    assert task["group_id"] == group["id"]

@pytest.mark.asyncio
async def test_create_task_with_parent(board):
    group = await board.create_group(title="Test", origin="pm", created_by="pm-1")
    parent = await board.create_task(
        group_id=group["id"], title="PRD", task_type="prd",
        assigned_to="architect", created_by="pm-1",
    )
    child = await board.create_task(
        group_id=group["id"], title="Design", task_type="tech_design",
        assigned_to="coder", created_by="architect-1",
        parent_id=parent["id"],
    )
    assert child["parent_id"] == parent["id"]

@pytest.mark.asyncio
async def test_claim_task(board):
    group = await board.create_group(title="Test", origin="pm", created_by="pm-1")
    task = await board.create_task(
        group_id=group["id"], title="Impl", task_type="implementation",
        assigned_to="coder", created_by="architect-1",
    )
    claimed = await board.claim_task(role="coder", instance_id="coder-1")
    assert claimed is not None
    assert claimed["id"] == task["id"]
    assert claimed["claimed_by"] == "coder-1"
    assert claimed["status"] == "in_progress"

@pytest.mark.asyncio
async def test_claim_task_empty_queue(board):
    claimed = await board.claim_task(role="coder", instance_id="coder-1")
    assert claimed is None

@pytest.mark.asyncio
async def test_complete_task(board):
    group = await board.create_group(title="Test", origin="pm", created_by="pm-1")
    task = await board.create_task(
        group_id=group["id"], title="Impl", task_type="implementation",
        assigned_to="coder", created_by="architect-1",
    )
    await board.claim_task(role="coder", instance_id="coder-1")
    result = await board.complete_task(task["id"])
    assert result["status"] == "completed"
    assert result["completed_at"] is not None

@pytest.mark.asyncio
async def test_reject_task(board):
    group = await board.create_group(title="Test", origin="pm", created_by="pm-1")
    task = await board.create_task(
        group_id=group["id"], title="Impl", task_type="implementation",
        assigned_to="coder", created_by="architect-1",
    )
    await board.claim_task(role="coder", instance_id="coder-1")
    result = await board.reject_task(task["id"], reason="Missing error handling")
    assert result["status"] == "rejected"
    assert result["rejection_reason"] == "Missing error handling"

@pytest.mark.asyncio
async def test_get_board_by_status(board):
    group = await board.create_group(title="Test", origin="pm", created_by="pm-1")
    await board.create_task(
        group_id=group["id"], title="T1", task_type="prd",
        assigned_to="architect", created_by="pm-1",
    )
    await board.create_task(
        group_id=group["id"], title="T2", task_type="prd",
        assigned_to="architect", created_by="pm-1",
    )
    result = await board.get_board()
    assert len(result["pending"]) == 2
    assert len(result["blocked"]) == 0

@pytest.mark.asyncio
async def test_get_board_filtered_by_group(board):
    g1 = await board.create_group(title="Feat1", origin="pm", created_by="pm-1")
    g2 = await board.create_group(title="Feat2", origin="pm", created_by="pm-1")
    await board.create_task(group_id=g1["id"], title="T1", task_type="prd", assigned_to="architect", created_by="pm-1")
    await board.create_task(group_id=g2["id"], title="T2", task_type="prd", assigned_to="architect", created_by="pm-1")
    result = await board.get_board(group_id=g1["id"])
    total = sum(len(v) for v in result.values())
    assert total == 1
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_task_board.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'taskbrew.orchestrator.task_board'`

**Step 3: Implement TaskBoard class**

```python
# src/taskbrew/orchestrator/task_board.py
from datetime import datetime, timezone
from taskbrew.orchestrator.database import Database

STATUSES = ["blocked", "pending", "in_progress", "completed", "failed", "rejected"]


class TaskBoard:
    def __init__(self, db: Database, group_prefixes: dict[str, str] | None = None):
        self._db = db
        self._group_prefixes = group_prefixes or {}
        self._group_seq: dict[str, int] = {}

    async def register_prefixes(self, role_prefixes: dict[str, str]):
        """Register role prefix -> task ID prefix mapping."""
        for role, prefix in role_prefixes.items():
            await self._db.register_prefix(prefix)

    def set_group_prefixes(self, prefixes: dict[str, str]):
        self._group_prefixes = prefixes

    async def create_group(
        self, title: str, origin: str, created_by: str
    ) -> dict:
        prefix = self._group_prefixes.get(origin, "GRP")
        # Simple incrementing group IDs
        key = f"group_{prefix}"
        if key not in self._group_seq:
            count = await self._db.execute_fetchone(
                "SELECT COUNT(*) as c FROM groups WHERE id LIKE ?",
                (f"{prefix}-%",)
            )
            self._group_seq[key] = (count["c"] if count else 0) + 1
        else:
            self._group_seq[key] += 1
        group_id = f"{prefix}-{self._group_seq[key]:03d}"
        now = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            "INSERT INTO groups (id, title, origin, status, created_by, created_at) "
            "VALUES (?, ?, ?, 'active', ?, ?)",
            (group_id, title, origin, created_by, now),
        )
        return await self._db.execute_fetchone(
            "SELECT * FROM groups WHERE id = ?", (group_id,)
        )

    async def create_task(
        self,
        group_id: str,
        title: str,
        task_type: str,
        assigned_to: str,
        created_by: str,
        description: str = "",
        priority: str = "medium",
        parent_id: str | None = None,
        revision_of: str | None = None,
        blocked_by: list[str] | None = None,
    ) -> dict:
        # Determine prefix from created_by role (extract role from instance id like "pm-1" -> "pm")
        creator_role = created_by.rsplit("-", 1)[0] if "-" in created_by else created_by
        # Find prefix for the creator role
        prefix = None
        for role, pfx in (await self._get_role_prefixes()).items():
            if role == creator_role:
                prefix = pfx
                break
        if prefix is None:
            prefix = creator_role.upper()[:2]

        task_id = await self._db.generate_task_id(prefix)
        status = "blocked" if blocked_by else "pending"
        now = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            "INSERT INTO tasks (id, group_id, parent_id, title, description, "
            "task_type, priority, assigned_to, status, created_by, created_at, revision_of) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (task_id, group_id, parent_id, title, description,
             task_type, priority, assigned_to, status, created_by, now, revision_of),
        )
        if blocked_by:
            for dep_id in blocked_by:
                await self._db.execute(
                    "INSERT INTO task_dependencies (task_id, blocked_by) VALUES (?, ?)",
                    (task_id, dep_id),
                )
        return await self._db.execute_fetchone(
            "SELECT * FROM tasks WHERE id = ?", (task_id,)
        )

    async def claim_task(self, role: str, instance_id: str) -> dict | None:
        now = datetime.now(timezone.utc).isoformat()
        row = await self._db.execute_fetchone(
            "UPDATE tasks SET status = 'in_progress', claimed_by = ?, started_at = ? "
            "WHERE id = ("
            "  SELECT id FROM tasks "
            "  WHERE assigned_to = ? AND status = 'pending' AND claimed_by IS NULL "
            "  ORDER BY "
            "    CASE priority "
            "      WHEN 'critical' THEN 0 WHEN 'high' THEN 1 "
            "      WHEN 'medium' THEN 2 WHEN 'low' THEN 3 "
            "    END, "
            "    created_at ASC "
            "  LIMIT 1"
            ") RETURNING *",
            (instance_id, now, role),
        )
        return row

    async def complete_task(self, task_id: str) -> dict:
        now = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            "UPDATE tasks SET status = 'completed', completed_at = ? WHERE id = ?",
            (now, task_id),
        )
        await self._resolve_dependencies(task_id)
        return await self._db.execute_fetchone(
            "SELECT * FROM tasks WHERE id = ?", (task_id,)
        )

    async def reject_task(self, task_id: str, reason: str) -> dict:
        now = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            "UPDATE tasks SET status = 'rejected', rejection_reason = ?, completed_at = ? "
            "WHERE id = ?",
            (reason, now, task_id),
        )
        return await self._db.execute_fetchone(
            "SELECT * FROM tasks WHERE id = ?", (task_id,)
        )

    async def fail_task(self, task_id: str) -> dict:
        now = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            "UPDATE tasks SET status = 'failed', completed_at = ? WHERE id = ?",
            (now, task_id),
        )
        return await self._db.execute_fetchone(
            "SELECT * FROM tasks WHERE id = ?", (task_id,)
        )

    async def get_task(self, task_id: str) -> dict | None:
        return await self._db.execute_fetchone(
            "SELECT * FROM tasks WHERE id = ?", (task_id,)
        )

    async def get_board(
        self,
        group_id: str | None = None,
        assigned_to: str | None = None,
        claimed_by: str | None = None,
        task_type: str | None = None,
        priority: str | None = None,
    ) -> dict[str, list[dict]]:
        where_clauses = []
        params = []
        if group_id:
            where_clauses.append("group_id = ?")
            params.append(group_id)
        if assigned_to:
            where_clauses.append("assigned_to = ?")
            params.append(assigned_to)
        if claimed_by:
            where_clauses.append("claimed_by = ?")
            params.append(claimed_by)
        if task_type:
            where_clauses.append("task_type = ?")
            params.append(task_type)
        if priority:
            where_clauses.append("priority = ?")
            params.append(priority)

        where = " AND ".join(where_clauses) if where_clauses else "1=1"
        tasks = await self._db.execute_fetchall(
            f"SELECT * FROM tasks WHERE {where} ORDER BY created_at ASC",
            tuple(params),
        )
        board = {status: [] for status in STATUSES}
        for task in tasks:
            if task["status"] in board:
                board[task["status"]].append(task)
        return board

    async def get_group_tasks(self, group_id: str) -> list[dict]:
        return await self._db.execute_fetchall(
            "SELECT * FROM tasks WHERE group_id = ? ORDER BY created_at ASC",
            (group_id,),
        )

    async def get_groups(self, status: str | None = None) -> list[dict]:
        if status:
            return await self._db.execute_fetchall(
                "SELECT * FROM groups WHERE status = ? ORDER BY created_at DESC",
                (status,),
            )
        return await self._db.execute_fetchall(
            "SELECT * FROM groups ORDER BY created_at DESC"
        )

    async def has_cycle(self, task_id: str, blocked_by_id: str) -> bool:
        visited = set()
        queue = [blocked_by_id]
        while queue:
            current = queue.pop(0)
            if current == task_id:
                return True
            if current in visited:
                continue
            visited.add(current)
            deps = await self._db.execute_fetchall(
                "SELECT blocked_by FROM task_dependencies "
                "WHERE task_id = ? AND resolved = 0",
                (current,),
            )
            queue.extend(d["blocked_by"] for d in deps)
        return False

    async def _resolve_dependencies(self, completed_task_id: str):
        now = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            "UPDATE task_dependencies SET resolved = 1, resolved_at = ? "
            "WHERE blocked_by = ?",
            (now, completed_task_id),
        )
        # Find tasks that are blocked but have all dependencies resolved
        unblocked = await self._db.execute_fetchall(
            "SELECT id FROM tasks WHERE status = 'blocked' "
            "AND id NOT IN ("
            "  SELECT task_id FROM task_dependencies WHERE resolved = 0"
            ")"
        )
        for task in unblocked:
            await self._db.execute(
                "UPDATE tasks SET status = 'pending' WHERE id = ?",
                (task["id"],),
            )

    async def _get_role_prefixes(self) -> dict[str, str]:
        rows = await self._db.execute_fetchall("SELECT prefix FROM id_sequences")
        # This is a reverse lookup — we need role->prefix mapping
        # For now return from registered prefixes
        return {row["prefix"].lower(): row["prefix"] for row in rows}
```

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_task_board.py -v`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add src/taskbrew/orchestrator/task_board.py tests/test_task_board.py
git commit -m "feat: add TaskBoard with CRUD, claiming, dependencies, filtering"
```

---

## Task 6: Dependency Resolution & Cycle Detection

**Files:**
- Modify: `tests/test_task_board.py` (add dependency tests)
- Modify: `src/taskbrew/orchestrator/task_board.py` (if fixes needed)

**Step 1: Write failing tests for dependency flows**

```python
# tests/test_task_board.py (append)

@pytest.mark.asyncio
async def test_blocked_task_unblocks_on_dependency_complete(board):
    group = await board.create_group(title="Test", origin="pm", created_by="pm-1")
    # Coder creates tester + reviewer tasks
    tester_task = await board.create_task(
        group_id=group["id"], title="Test impl", task_type="qa",
        assigned_to="tester", created_by="coder-1",
    )
    reviewer_task = await board.create_task(
        group_id=group["id"], title="Review impl", task_type="code_review",
        assigned_to="reviewer", created_by="coder-1",
        blocked_by=[tester_task["id"]],
    )
    assert reviewer_task["status"] == "blocked"

    # Tester completes — reviewer should unblock
    await board.claim_task(role="tester", instance_id="tester-1")
    await board.complete_task(tester_task["id"])

    updated = await board.get_task(reviewer_task["id"])
    assert updated["status"] == "pending"

@pytest.mark.asyncio
async def test_multiple_dependencies_all_must_resolve(board):
    group = await board.create_group(title="Test", origin="pm", created_by="pm-1")
    dep1 = await board.create_task(
        group_id=group["id"], title="Dep1", task_type="qa",
        assigned_to="tester", created_by="coder-1",
    )
    dep2 = await board.create_task(
        group_id=group["id"], title="Dep2", task_type="qa",
        assigned_to="tester", created_by="coder-1",
    )
    blocked = await board.create_task(
        group_id=group["id"], title="Blocked", task_type="code_review",
        assigned_to="reviewer", created_by="coder-1",
        blocked_by=[dep1["id"], dep2["id"]],
    )
    assert blocked["status"] == "blocked"

    # Complete only dep1 — should stay blocked
    await board.claim_task(role="tester", instance_id="tester-1")
    await board.complete_task(dep1["id"])
    still_blocked = await board.get_task(blocked["id"])
    assert still_blocked["status"] == "blocked"

    # Complete dep2 — now should unblock
    await board.claim_task(role="tester", instance_id="tester-1")
    await board.complete_task(dep2["id"])
    now_pending = await board.get_task(blocked["id"])
    assert now_pending["status"] == "pending"

@pytest.mark.asyncio
async def test_cycle_detection(board):
    group = await board.create_group(title="Test", origin="pm", created_by="pm-1")
    t1 = await board.create_task(
        group_id=group["id"], title="T1", task_type="impl",
        assigned_to="coder", created_by="pm-1",
    )
    t2 = await board.create_task(
        group_id=group["id"], title="T2", task_type="impl",
        assigned_to="coder", created_by="pm-1",
        blocked_by=[t1["id"]],
    )
    # t1 blocked_by t2 would create a cycle: t1 -> t2 -> t1
    assert await board.has_cycle(t1["id"], t2["id"]) is True

@pytest.mark.asyncio
async def test_no_false_cycle(board):
    group = await board.create_group(title="Test", origin="pm", created_by="pm-1")
    t1 = await board.create_task(
        group_id=group["id"], title="T1", task_type="impl",
        assigned_to="coder", created_by="pm-1",
    )
    t2 = await board.create_task(
        group_id=group["id"], title="T2", task_type="impl",
        assigned_to="coder", created_by="pm-1",
    )
    # t2 blocked_by t1 — no cycle since t1 has no deps
    assert await board.has_cycle(t2["id"], t1["id"]) is False
```

**Step 2: Run tests to verify they pass (these should work with existing implementation)**

Run: `pytest tests/test_task_board.py -v -k "dependency or cycle"`
Expected: ALL PASS (if implementation from Task 5 is correct)

**Step 3: Commit**

```bash
git add tests/test_task_board.py
git commit -m "test: add dependency resolution and cycle detection tests"
```

---

## Task 7: Agent Instance Manager

**Files:**
- Create: `src/taskbrew/agents/instance_manager.py`
- Test: `tests/test_instance_manager.py`

**Step 1: Write failing tests**

```python
# tests/test_instance_manager.py
import pytest
from taskbrew.orchestrator.database import Database
from taskbrew.agents.instance_manager import InstanceManager
from taskbrew.config_loader import RoleConfig, RouteTarget

@pytest.fixture
def coder_role():
    return RoleConfig(
        role="coder", display_name="Coder", prefix="CD", color="#f59e0b",
        emoji="", system_prompt="You are a coder.", tools=["Read", "Write"],
        produces=["implementation"], accepts=["implementation"],
        routes_to=[], max_instances=3,
    )

@pytest.fixture
async def manager(tmp_path, coder_role):
    db = Database(str(tmp_path / "test.db"))
    await db.initialize()
    mgr = InstanceManager(db)
    yield mgr
    await db.close()

@pytest.mark.asyncio
async def test_register_instance(manager, coder_role):
    instance = await manager.register_instance("coder-1", coder_role)
    assert instance["instance_id"] == "coder-1"
    assert instance["role"] == "coder"
    assert instance["status"] == "idle"

@pytest.mark.asyncio
async def test_update_status(manager, coder_role):
    await manager.register_instance("coder-1", coder_role)
    updated = await manager.update_status("coder-1", "working", current_task="CD-001")
    assert updated["status"] == "working"
    assert updated["current_task"] == "CD-001"

@pytest.mark.asyncio
async def test_get_all_instances(manager, coder_role):
    await manager.register_instance("coder-1", coder_role)
    await manager.register_instance("coder-2", coder_role)
    instances = await manager.get_all_instances()
    assert len(instances) == 2

@pytest.mark.asyncio
async def test_heartbeat(manager, coder_role):
    await manager.register_instance("coder-1", coder_role)
    await manager.heartbeat("coder-1")
    instance = await manager.get_instance("coder-1")
    assert instance["last_heartbeat"] is not None

@pytest.mark.asyncio
async def test_remove_instance(manager, coder_role):
    await manager.register_instance("coder-1", coder_role)
    await manager.remove_instance("coder-1")
    instance = await manager.get_instance("coder-1")
    assert instance is None
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_instance_manager.py -v`
Expected: FAIL — `ModuleNotFoundError`

**Step 3: Implement InstanceManager**

```python
# src/taskbrew/agents/instance_manager.py
from datetime import datetime, timezone
from taskbrew.orchestrator.database import Database
from taskbrew.config_loader import RoleConfig


class InstanceManager:
    def __init__(self, db: Database):
        self._db = db

    async def register_instance(
        self, instance_id: str, role_config: RoleConfig
    ) -> dict:
        now = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            "INSERT OR REPLACE INTO agent_instances "
            "(instance_id, role, status, started_at, last_heartbeat) "
            "VALUES (?, ?, 'idle', ?, ?)",
            (instance_id, role_config.role, now, now),
        )
        return await self.get_instance(instance_id)

    async def update_status(
        self, instance_id: str, status: str,
        current_task: str | None = None,
    ) -> dict:
        await self._db.execute(
            "UPDATE agent_instances SET status = ?, current_task = ? "
            "WHERE instance_id = ?",
            (status, current_task, instance_id),
        )
        return await self.get_instance(instance_id)

    async def heartbeat(self, instance_id: str):
        now = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            "UPDATE agent_instances SET last_heartbeat = ? WHERE instance_id = ?",
            (now, instance_id),
        )

    async def get_instance(self, instance_id: str) -> dict | None:
        return await self._db.execute_fetchone(
            "SELECT * FROM agent_instances WHERE instance_id = ?",
            (instance_id,),
        )

    async def get_all_instances(self) -> list[dict]:
        return await self._db.execute_fetchall(
            "SELECT * FROM agent_instances ORDER BY instance_id"
        )

    async def get_instances_by_role(self, role: str) -> list[dict]:
        return await self._db.execute_fetchall(
            "SELECT * FROM agent_instances WHERE role = ? ORDER BY instance_id",
            (role,),
        )

    async def remove_instance(self, instance_id: str):
        await self._db.execute(
            "DELETE FROM agent_instances WHERE instance_id = ?",
            (instance_id,),
        )
```

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_instance_manager.py -v`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add src/taskbrew/agents/instance_manager.py tests/test_instance_manager.py
git commit -m "feat: add agent InstanceManager for tracking agent instances"
```

---

## Task 8: Agent Loop — Poll/Claim/Execute/Handoff/Complete

**Files:**
- Create: `src/taskbrew/agents/agent_loop.py`
- Test: `tests/test_agent_loop.py`
- Modify: `src/taskbrew/agents/base.py` (keep AgentRunner, used by loop)

**Step 1: Write failing test for the agent loop claiming and completing a task**

```python
# tests/test_agent_loop.py
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from taskbrew.agents.agent_loop import AgentLoop
from taskbrew.orchestrator.database import Database
from taskbrew.orchestrator.task_board import TaskBoard
from taskbrew.orchestrator.event_bus import EventBus
from taskbrew.agents.instance_manager import InstanceManager
from taskbrew.config_loader import RoleConfig, RouteTarget

@pytest.fixture
def coder_role():
    return RoleConfig(
        role="coder", display_name="Coder", prefix="CD", color="#f59e0b",
        emoji="", system_prompt="You are a coder.", tools=["Read", "Write"],
        produces=["implementation"], accepts=["implementation"],
        routes_to=[
            RouteTarget(role="tester", task_types=["qa_verification"]),
            RouteTarget(role="reviewer", task_types=["code_review"]),
        ],
        max_instances=1,
        context_includes=["parent_artifact"],
    )

@pytest.fixture
async def setup(tmp_path, coder_role):
    db = Database(str(tmp_path / "test.db"))
    await db.initialize()
    board = TaskBoard(db)
    await board.register_prefixes({"pm": "PM", "coder": "CD", "tester": "TS", "reviewer": "RV"})
    event_bus = EventBus()
    instance_mgr = InstanceManager(db)
    roles = {"coder": coder_role}
    loop = AgentLoop(
        instance_id="coder-1",
        role_config=coder_role,
        board=board,
        event_bus=event_bus,
        instance_manager=instance_mgr,
        all_roles=roles,
        cli_path=None,
        project_dir=str(tmp_path),
    )
    yield {"db": db, "board": board, "loop": loop, "event_bus": event_bus}
    await db.close()

@pytest.mark.asyncio
async def test_poll_claims_task(setup):
    board = setup["board"]
    loop = setup["loop"]

    group = await board.create_group(title="Test", origin="pm", created_by="pm-1")
    task = await board.create_task(
        group_id=group["id"], title="Impl", task_type="implementation",
        assigned_to="coder", created_by="architect-1",
    )

    claimed = await loop.poll_for_task()
    assert claimed is not None
    assert claimed["id"] == task["id"]
    assert claimed["status"] == "in_progress"

@pytest.mark.asyncio
async def test_poll_returns_none_when_empty(setup):
    loop = setup["loop"]
    claimed = await loop.poll_for_task()
    assert claimed is None
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_agent_loop.py -v`
Expected: FAIL — `ModuleNotFoundError`

**Step 3: Implement AgentLoop**

```python
# src/taskbrew/agents/agent_loop.py
import asyncio
import logging
from taskbrew.orchestrator.task_board import TaskBoard
from taskbrew.orchestrator.event_bus import EventBus
from taskbrew.agents.instance_manager import InstanceManager
from taskbrew.config_loader import RoleConfig

logger = logging.getLogger(__name__)


class AgentLoop:
    def __init__(
        self,
        instance_id: str,
        role_config: RoleConfig,
        board: TaskBoard,
        event_bus: EventBus,
        instance_manager: InstanceManager,
        all_roles: dict[str, RoleConfig],
        cli_path: str | None = None,
        project_dir: str = ".",
        poll_interval: float = 5.0,
    ):
        self.instance_id = instance_id
        self.role_config = role_config
        self.board = board
        self.event_bus = event_bus
        self.instance_manager = instance_manager
        self.all_roles = all_roles
        self.cli_path = cli_path
        self.project_dir = project_dir
        self.poll_interval = poll_interval
        self._running = False

    async def poll_for_task(self) -> dict | None:
        """Try to claim the next pending task for this role."""
        return await self.board.claim_task(
            role=self.role_config.role,
            instance_id=self.instance_id,
        )

    async def build_context(self, task: dict) -> str:
        """Assemble prompt context from task data, parent artifacts, etc."""
        parts = [
            f"You are {self.role_config.display_name} (instance {self.instance_id}).\n",
            f"## Your Task",
            f"**{task['id']}**: {task['title']}",
            f"Type: {task['task_type']} | Priority: {task['priority']}",
            f"Group: {task['group_id']}",
        ]
        if task.get("description"):
            parts.append(f"\n## Description\n{task['description']}")
        # Add parent artifact if available
        if task.get("parent_id") and "parent_artifact" in self.role_config.context_includes:
            parent = await self.board.get_task(task["parent_id"])
            if parent:
                parts.append(f"\n## Parent Task ({parent['id']}): {parent['title']}")
        # Add routing instructions
        if self.role_config.routes_to:
            parts.append("\n## When Complete")
            parts.append("Create tasks for:")
            for route in self.role_config.routes_to:
                parts.append(f"- **{route.role}** (types: {', '.join(route.task_types)})")
        return "\n".join(parts)

    async def execute_task(self, task: dict) -> str:
        """Run the Claude SDK agent on the task. Returns output text."""
        from taskbrew.agents.base import AgentRunner
        from taskbrew.config import AgentConfig

        agent_config = AgentConfig(
            name=self.instance_id,
            role=self.role_config.role,
            system_prompt=self.role_config.system_prompt,
            allowed_tools=self.role_config.tools,
            cwd=self.project_dir,
        )
        runner = AgentRunner(
            config=agent_config,
            cli_path=self.cli_path,
            event_bus=self.event_bus,
        )
        context = await self.build_context(task)
        return await runner.run(prompt=context, cwd=self.project_dir)

    async def complete_and_handoff(self, task: dict, output: str):
        """Mark task complete and create downstream tasks per routing."""
        await self.board.complete_task(task["id"])
        await self.event_bus.emit("task.completed", {
            "task_id": task["id"],
            "group_id": task["group_id"],
            "agent_id": self.instance_id,
        })

    async def run_once(self) -> bool:
        """Run one poll/claim/execute/complete cycle. Returns True if a task was processed."""
        task = await self.poll_for_task()
        if task is None:
            return False

        await self.instance_manager.update_status(
            self.instance_id, "working", current_task=task["id"]
        )
        await self.event_bus.emit("task.claimed", {
            "task_id": task["id"],
            "claimed_by": self.instance_id,
        })

        try:
            output = await self.execute_task(task)
            await self.complete_and_handoff(task, output)
        except Exception as e:
            logger.error(f"Agent {self.instance_id} failed on {task['id']}: {e}")
            await self.board.fail_task(task["id"])
            await self.event_bus.emit("task.failed", {
                "task_id": task["id"],
                "error": str(e),
            })
        finally:
            await self.instance_manager.update_status(self.instance_id, "idle")

        return True

    async def run(self):
        """Main loop: poll, claim, execute, repeat."""
        self._running = True
        await self.instance_manager.register_instance(
            self.instance_id, self.role_config
        )
        await self.event_bus.emit("agent.status_changed", {
            "instance_id": self.instance_id,
            "status": "idle",
        })

        while self._running:
            processed = await self.run_once()
            if not processed:
                await asyncio.sleep(self.poll_interval)
            await self.instance_manager.heartbeat(self.instance_id)

    def stop(self):
        self._running = False
```

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_agent_loop.py -v`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add src/taskbrew/agents/agent_loop.py tests/test_agent_loop.py
git commit -m "feat: add AgentLoop with poll/claim/execute/handoff cycle"
```

---

## Task 9: Updated Artifact Store

**Files:**
- Modify: `src/taskbrew/orchestrator/artifact_store.py` (reorganize by group_id/task_id)
- Test: `tests/test_artifact_store.py` (update tests)

**Step 1: Write failing test for new artifact structure**

```python
# tests/test_artifact_store_v2.py
import pytest
from taskbrew.orchestrator.artifact_store import ArtifactStore

@pytest.fixture
def store(tmp_path):
    return ArtifactStore(base_dir=str(tmp_path / "artifacts"))

def test_save_artifact_by_group_and_task(store):
    path = store.save_artifact(
        group_id="FEAT-001", task_id="CD-001",
        filename="output.md", content="# Implementation\nDone."
    )
    assert "FEAT-001" in path
    assert "CD-001" in path
    assert path.endswith("output.md")

def test_load_artifact(store):
    store.save_artifact(
        group_id="FEAT-001", task_id="CD-001",
        filename="output.md", content="hello"
    )
    content = store.load_artifact("FEAT-001", "CD-001", "output.md")
    assert content == "hello"

def test_load_missing_artifact(store):
    content = store.load_artifact("FEAT-999", "CD-999", "nope.md")
    assert content == ""
```

**Step 2: Run test to verify current artifact store fails (wrong API)**

Run: `pytest tests/test_artifact_store_v2.py -v`
Expected: FAIL — signature mismatch (current uses run_id/step_index/agent_name)

**Step 3: Rewrite artifact_store.py with new API**

```python
# src/taskbrew/orchestrator/artifact_store.py
import os
from pathlib import Path


class ArtifactStore:
    def __init__(self, base_dir: str):
        self.base_dir = base_dir

    def get_artifact_dir(self, group_id: str, task_id: str) -> str:
        path = os.path.join(self.base_dir, group_id, task_id)
        os.makedirs(path, exist_ok=True)
        return path

    def save_artifact(
        self, group_id: str, task_id: str,
        filename: str, content: str,
    ) -> str:
        artifact_dir = self.get_artifact_dir(group_id, task_id)
        filepath = os.path.join(artifact_dir, filename)
        with open(filepath, "w") as f:
            f.write(content)
        return filepath

    def load_artifact(
        self, group_id: str, task_id: str, filename: str,
    ) -> str:
        filepath = os.path.join(self.base_dir, group_id, task_id, filename)
        if not os.path.exists(filepath):
            return ""
        with open(filepath) as f:
            return f.read()

    def get_task_artifacts(self, group_id: str, task_id: str) -> list[str]:
        artifact_dir = os.path.join(self.base_dir, group_id, task_id)
        if not os.path.exists(artifact_dir):
            return []
        return [f for f in os.listdir(artifact_dir) if os.path.isfile(
            os.path.join(artifact_dir, f)
        )]

    def get_group_artifacts(self, group_id: str) -> dict[str, list[str]]:
        group_dir = os.path.join(self.base_dir, group_id)
        if not os.path.exists(group_dir):
            return {}
        result = {}
        for task_dir in sorted(os.listdir(group_dir)):
            task_path = os.path.join(group_dir, task_dir)
            if os.path.isdir(task_path):
                files = [f for f in os.listdir(task_path)
                         if os.path.isfile(os.path.join(task_path, f))]
                if files:
                    result[task_dir] = files
        return result
```

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_artifact_store_v2.py -v`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add src/taskbrew/orchestrator/artifact_store.py tests/test_artifact_store_v2.py
git commit -m "refactor: reorganize artifact store by group_id/task_id"
```

---

## Task 10: Updated Dashboard API Endpoints

**Files:**
- Modify: `src/taskbrew/dashboard/app.py` (replace old endpoints with new task board API)
- Test: `tests/test_dashboard_api.py`

This is a large task. The key new endpoints:

- `GET /api/board` — task board with filters (replaces `/api/tasks/board`)
- `GET /api/groups` — list all groups
- `GET /api/groups/{id}/graph` — task graph for a group (for DAG visualization)
- `POST /api/goals` — submit a new goal (creates group + PM task)
- `GET /api/agents` — all agent instances (replaces `/api/team`)
- `GET /api/board/filters` — available filter values (roles, groups, statuses)

**Step 1: Write failing test for the new board endpoint**

```python
# tests/test_dashboard_api.py
import pytest
from httpx import AsyncClient, ASGITransport
from taskbrew.orchestrator.database import Database
from taskbrew.orchestrator.task_board import TaskBoard
from taskbrew.orchestrator.event_bus import EventBus
from taskbrew.agents.instance_manager import InstanceManager

@pytest.fixture
async def app_client(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    await db.initialize()
    board = TaskBoard(db)
    await board.register_prefixes({"pm": "PM", "architect": "AR", "coder": "CD"})
    event_bus = EventBus()
    instance_mgr = InstanceManager(db)

    from taskbrew.dashboard.app import create_app
    app = create_app(
        event_bus=event_bus,
        task_board=board,
        instance_manager=instance_mgr,
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield {"client": client, "board": board}
    await db.close()

@pytest.mark.asyncio
async def test_get_board_empty(app_client):
    resp = await app_client["client"].get("/api/board")
    assert resp.status_code == 200
    data = resp.json()
    assert "pending" in data
    assert "blocked" in data

@pytest.mark.asyncio
async def test_get_board_with_tasks(app_client):
    board = app_client["board"]
    group = await board.create_group(title="Test", origin="pm", created_by="pm-1")
    await board.create_task(
        group_id=group["id"], title="PRD", task_type="prd",
        assigned_to="architect", created_by="pm-1",
    )
    resp = await app_client["client"].get("/api/board")
    data = resp.json()
    assert len(data["pending"]) == 1

@pytest.mark.asyncio
async def test_get_board_filtered_by_group(app_client):
    board = app_client["board"]
    g1 = await board.create_group(title="F1", origin="pm", created_by="pm-1")
    g2 = await board.create_group(title="F2", origin="pm", created_by="pm-1")
    await board.create_task(group_id=g1["id"], title="T1", task_type="prd", assigned_to="architect", created_by="pm-1")
    await board.create_task(group_id=g2["id"], title="T2", task_type="prd", assigned_to="architect", created_by="pm-1")
    resp = await app_client["client"].get(f"/api/board?group_id={g1['id']}")
    data = resp.json()
    total = sum(len(v) for v in data.values())
    assert total == 1

@pytest.mark.asyncio
async def test_get_groups(app_client):
    board = app_client["board"]
    await board.create_group(title="F1", origin="pm", created_by="pm-1")
    resp = await app_client["client"].get("/api/groups")
    assert resp.status_code == 200
    assert len(resp.json()) == 1

@pytest.mark.asyncio
async def test_post_goal(app_client):
    resp = await app_client["client"].post("/api/goals", json={
        "title": "Add dark mode",
        "description": "Implement dark mode for the dashboard",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "group_id" in data
    assert "task_id" in data

@pytest.mark.asyncio
async def test_get_group_graph(app_client):
    board = app_client["board"]
    group = await board.create_group(title="F1", origin="pm", created_by="pm-1")
    parent = await board.create_task(
        group_id=group["id"], title="PRD", task_type="prd",
        assigned_to="architect", created_by="pm-1",
    )
    child = await board.create_task(
        group_id=group["id"], title="Design", task_type="tech_design",
        assigned_to="coder", created_by="architect-1",
        parent_id=parent["id"],
    )
    resp = await app_client["client"].get(f"/api/groups/{group['id']}/graph")
    assert resp.status_code == 200
    data = resp.json()
    assert "nodes" in data
    assert "edges" in data
    assert len(data["nodes"]) == 2
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_dashboard_api.py -v`
Expected: FAIL — `create_app` signature has changed

**Step 3: Rewrite app.py create_app with new signature and endpoints**

The new `create_app` should accept `event_bus`, `task_board`, `instance_manager`, and optionally `chat_manager`, `artifact_store`, `roles`. Replace old pipeline/workflow endpoints with the new board/group/goal endpoints. Keep the WebSocket broadcast pattern. Keep the chat endpoints. Remove old `/api/tasks/board`, `/api/runs`, `/api/pipelines/{name}/run`, `/api/runs/{id}/approve`, `/api/runs/{id}/reject`.

Full implementation is substantial — write the new endpoints matching the test expectations above. The graph endpoint should return `{"nodes": [...], "edges": [...]}` where nodes are tasks and edges represent parent_id and blocked_by relationships.

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_dashboard_api.py -v`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add src/taskbrew/dashboard/app.py tests/test_dashboard_api.py
git commit -m "feat: rewrite dashboard API for task board, groups, and goals"
```

---

## Task 11: Dashboard UI — Board View with Filters

**Files:**
- Modify: `src/taskbrew/dashboard/templates/index.html` (major rewrite of main content area)

This is the largest UI task. Replace the current agents grid + kanban + pipeline runs with:
1. Filter bar (Group, Assignee, Type, Priority, Status dropdowns + View toggle: Board/List/Graph)
2. Board view: columns by status (Blocked, Pending, In Progress, Completed, Rejected)
3. Task cards with role-colored badges, group tags, blocked indicators
4. Updated stats bar (Agents online, Active tasks, Blocked, Groups active, Events)
5. Agent sidebar (collapsible right panel)

**Step 1: Replace the stats bar, filter bar, and board columns HTML**

Remove the old agents-grid, kanban-board, runs-list, and event-log sections. Replace with the new task-board-centric layout. Update the CSS design tokens and add new classes for filters, view toggles, task cards, and the agent sidebar.

**Step 2: Implement JavaScript functions**

- `refreshBoard()` — fetches `/api/board` with current filter params, renders cards into columns
- `refreshGroups()` — fetches `/api/groups`, populates the group filter dropdown
- `refreshAgentSidebar()` — fetches `/api/agents`, updates sidebar
- `applyFilters()` — reads filter dropdowns, calls `refreshBoard()` with query params
- `switchView(mode)` — toggles between Board/List/Graph views
- `submitGoal()` — POST `/api/goals` with user input

**Step 3: Update WebSocket handler**

Map new event types to refresh functions:
- `task.created`, `task.claimed`, `task.status_changed`, `task.unblocked`, `task.completed`, `task.rejected` → `refreshBoard()`
- `group.created`, `group.completed` → `refreshGroups()` + stats
- `agent.status_changed`, `agent.scaled_up` → `refreshAgentSidebar()`

**Step 4: Test manually**

Run: `ai-team serve` and verify the board renders, filters work, cards appear in correct columns.

**Step 5: Commit**

```bash
git add src/taskbrew/dashboard/templates/index.html
git commit -m "feat: rewrite dashboard UI with task board, filters, and agent sidebar"
```

---

## Task 12: Dashboard UI — List View

**Files:**
- Modify: `src/taskbrew/dashboard/templates/index.html`

**Step 1: Add list view HTML**

A `<table>` element hidden by default, shown when "List" view is selected. Columns: ID, Title, Assignee, Status, Group, Blocked By, Priority, Created. Sortable headers (click to sort).

**Step 2: Add list rendering JS**

- `renderListView(tasks)` — flatten all status columns into one sorted array, render table rows
- Sort handler: re-sort and re-render on header click

**Step 3: Test manually and commit**

```bash
git add src/taskbrew/dashboard/templates/index.html
git commit -m "feat: add list view to dashboard task board"
```

---

## Task 13: Dashboard UI — Graph View

**Files:**
- Modify: `src/taskbrew/dashboard/templates/index.html`

**Step 1: Add graph view container**

A `<div>` with a `<canvas>` or SVG area for rendering the DAG. When a group is selected in the filter, fetch `/api/groups/{id}/graph` and render.

**Step 2: Implement DAG rendering in JS**

Use a simple layout algorithm:
- Nodes positioned by depth (root at top, children below)
- Nodes colored by role (use role colors from config)
- Solid edges for parent→child, dashed edges for blocked_by
- Status icons on each node
- Progress bar at the top showing completed/total

**Step 3: Test manually and commit**

```bash
git add src/taskbrew/dashboard/templates/index.html
git commit -m "feat: add graph view for group DAG visualization"
```

---

## Task 14: Updated Main Entry Point

**Files:**
- Modify: `src/taskbrew/main.py` (rewrite to use new components)
- Modify: `src/taskbrew/orchestrator/__init__.py`

**Step 1: Rewrite main.py**

New flow:
1. Load `config/team.yaml` → `TeamConfig`
2. Load `config/roles/*.yaml` → `dict[str, RoleConfig]`
3. Validate routing
4. Initialize `Database`, `TaskBoard`, `ArtifactStore`, `EventBus`, `InstanceManager`
5. Register all role prefixes in database
6. For `serve` command: start FastAPI dashboard + spawn agent loops
7. For `goal` command: create group + PM task directly via TaskBoard
8. For `status` command: print agent instances and task board summary

New CLI:
```
ai-team serve                              # start dashboard + agents
ai-team goal "Add dark mode"               # submit a goal
ai-team status                             # print status
```

**Step 2: Implement agent spawning in serve command**

For each role in the config, spawn `max_instances` AgentLoop instances as asyncio tasks. Each runs `agent_loop.run()` concurrently with the FastAPI server.

**Step 3: Test by running `ai-team serve`**

Verify: dashboard starts, agents spawn, submitting a goal creates a group and PM task, PM picks it up.

**Step 4: Commit**

```bash
git add src/taskbrew/main.py
git commit -m "feat: rewrite main entry point for independent agent system"
```

---

## Task 15: Remove Deprecated Code

**Files:**
- Delete: `src/taskbrew/orchestrator/workflow.py` (pipeline engine — replaced by task board routing)
- Delete: `src/taskbrew/orchestrator/task_queue.py` (old task queue — replaced by task_board.py)
- Delete: `pipelines/` directory (YAML pipelines — replaced by role routing)
- Modify: `src/taskbrew/agents/roles.py` (keep as fallback or delete if fully replaced by YAML configs)
- Update: `tests/` (remove tests for deleted modules, update imports)

**Step 1: Remove deprecated files**

```bash
git rm src/taskbrew/orchestrator/workflow.py
git rm src/taskbrew/orchestrator/task_queue.py
git rm -r pipelines/
```

**Step 2: Update imports across codebase**

Grep for any remaining imports of `workflow`, `task_queue`, `PipelineRun`, `PipelineStep`, `Pipeline`, `WorkflowEngine`, `TaskQueue`, `TaskStatus` and remove or replace them.

**Step 3: Run full test suite**

Run: `pytest tests/ -v`
Expected: ALL PASS (some old tests will need removal or update)

**Step 4: Commit**

```bash
git add -A
git commit -m "refactor: remove deprecated pipeline engine and old task queue"
```

---

## Task 16: Integration Test — Full Goal-to-Completion Flow

**Files:**
- Create: `tests/test_integration_v2.py`

**Step 1: Write end-to-end test**

```python
# tests/test_integration_v2.py
import pytest
from taskbrew.orchestrator.database import Database
from taskbrew.orchestrator.task_board import TaskBoard
from taskbrew.orchestrator.event_bus import EventBus
from taskbrew.agents.instance_manager import InstanceManager

@pytest.fixture
async def system(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    await db.initialize()
    board = TaskBoard(db, group_prefixes={"pm": "FEAT", "architect": "DEBT"})
    await board.register_prefixes({
        "pm": "PM", "architect": "AR", "coder": "CD",
        "tester": "TS", "reviewer": "RV"
    })
    event_bus = EventBus()
    yield {"db": db, "board": board, "event_bus": event_bus}
    await db.close()

@pytest.mark.asyncio
async def test_full_task_flow(system):
    board = system["board"]

    # 1. Human submits goal -> PM task created
    group = await board.create_group(title="Add dark mode", origin="pm", created_by="human")
    pm_task = await board.create_task(
        group_id=group["id"], title="Create PRD for dark mode",
        task_type="goal", assigned_to="pm", created_by="human",
    )
    assert pm_task["status"] == "pending"

    # 2. PM claims and completes -> creates architect task
    claimed = await board.claim_task("pm", "pm-1")
    assert claimed["id"] == pm_task["id"]
    await board.complete_task(pm_task["id"])
    ar_task = await board.create_task(
        group_id=group["id"], title="Design theme architecture",
        task_type="tech_design", assigned_to="architect", created_by="pm-1",
        parent_id=pm_task["id"],
    )

    # 3. Architect claims and completes -> creates coder task
    await board.claim_task("architect", "architect-1")
    await board.complete_task(ar_task["id"])
    cd_task = await board.create_task(
        group_id=group["id"], title="Implement CSS variables",
        task_type="implementation", assigned_to="coder", created_by="architect-1",
        parent_id=ar_task["id"],
    )

    # 4. Coder claims and completes -> creates tester + blocked reviewer
    await board.claim_task("coder", "coder-1")
    await board.complete_task(cd_task["id"])
    ts_task = await board.create_task(
        group_id=group["id"], title="Test CSS variables",
        task_type="qa_verification", assigned_to="tester", created_by="coder-1",
        parent_id=cd_task["id"],
    )
    rv_task = await board.create_task(
        group_id=group["id"], title="Review CSS variables",
        task_type="code_review", assigned_to="reviewer", created_by="coder-1",
        parent_id=cd_task["id"], blocked_by=[ts_task["id"]],
    )
    assert rv_task["status"] == "blocked"

    # 5. Tester completes -> reviewer unblocks
    await board.claim_task("tester", "tester-1")
    await board.complete_task(ts_task["id"])
    rv_updated = await board.get_task(rv_task["id"])
    assert rv_updated["status"] == "pending"

    # 6. Reviewer completes -> done
    await board.claim_task("reviewer", "reviewer-1")
    await board.complete_task(rv_task["id"])

    # Verify: all tasks completed, group audit trail intact
    all_tasks = await board.get_group_tasks(group["id"])
    assert len(all_tasks) == 5
    assert all(t["status"] == "completed" for t in all_tasks)
    assert all(t["group_id"] == group["id"] for t in all_tasks)

@pytest.mark.asyncio
async def test_rejection_flow(system):
    board = system["board"]

    group = await board.create_group(title="Feature", origin="pm", created_by="human")
    cd_task = await board.create_task(
        group_id=group["id"], title="Implement X",
        task_type="implementation", assigned_to="coder", created_by="architect-1",
    )
    await board.claim_task("coder", "coder-1")
    await board.complete_task(cd_task["id"])

    # Reviewer rejects
    rv_task = await board.create_task(
        group_id=group["id"], title="Review X",
        task_type="code_review", assigned_to="reviewer", created_by="coder-1",
        parent_id=cd_task["id"],
    )
    await board.claim_task("reviewer", "reviewer-1")
    await board.reject_task(rv_task["id"], reason="Missing error handling")

    # Reviewer creates revision task back to coder
    revision = await board.create_task(
        group_id=group["id"], title="Fix: Missing error handling",
        task_type="revision", assigned_to="coder", created_by="reviewer-1",
        parent_id=rv_task["id"], revision_of=cd_task["id"],
    )
    assert revision["status"] == "pending"
    assert revision["revision_of"] == cd_task["id"]

    # Verify audit: original rejected, revision pending
    original = await board.get_task(rv_task["id"])
    assert original["status"] == "rejected"
```

**Step 2: Run integration tests**

Run: `pytest tests/test_integration_v2.py -v`
Expected: ALL PASS

**Step 3: Commit**

```bash
git add tests/test_integration_v2.py
git commit -m "test: add end-to-end integration tests for full task lifecycle"
```

---

## Task 17: Update FAQ with New System Information

**Files:**
- Modify: `src/taskbrew/dashboard/templates/index.html` (FAQ modal content)

**Step 1: Update FAQ sections to reflect the new system**

Replace the current FAQ content with:
1. **Getting Started** — `ai-team serve`, `ai-team goal "description"`
2. **How It Works** — agents work independently, create tasks for each other
3. **Task Board** — board/list/graph views, filters, statuses (blocked/pending/in_progress/completed/rejected)
4. **Agent Roles** — PM, Architect, Coder, Tester, Reviewer with what each does
5. **Adding a Role** — create a YAML file in config/roles/
6. **CLI Commands** — serve, goal, status

**Step 2: Commit**

```bash
git add src/taskbrew/dashboard/templates/index.html
git commit -m "docs: update FAQ for new independent agents system"
```

---

## Summary

| Task | Component | Key Deliverable |
|------|-----------|-----------------|
| 1 | Config | team.yaml + config_loader.py |
| 2 | Config | Role YAML files + RoleConfig loader |
| 3 | Config | Routing validation |
| 4 | Database | New schema (7 tables + indexes) |
| 5 | Core | TaskBoard CRUD + claiming + filtering |
| 6 | Core | Dependency resolution + cycle detection tests |
| 7 | Agents | InstanceManager |
| 8 | Agents | AgentLoop (poll/claim/execute/handoff) |
| 9 | Artifacts | Reorganized by group_id/task_id |
| 10 | Dashboard API | New REST endpoints (board, groups, goals, graph) |
| 11 | Dashboard UI | Board view with filters |
| 12 | Dashboard UI | List view |
| 13 | Dashboard UI | Graph view (DAG) |
| 14 | Main | Rewritten entry point |
| 15 | Cleanup | Remove deprecated pipeline code |
| 16 | Testing | Full integration tests |
| 17 | Docs | Updated FAQ |

**Execution order matters:** Tasks 1-6 are foundation (no dependencies between tasks within this group, but must complete before 7+). Tasks 7-9 build the agent system. Tasks 10-13 build the dashboard. Tasks 14-17 are integration and cleanup.
