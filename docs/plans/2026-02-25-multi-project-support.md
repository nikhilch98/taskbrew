# Multi-Project Support Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Enable the AI Team orchestrator to manage multiple projects with isolated config, data, and agents — with a landing page wizard for first-time setup and a project selector dropdown for switching.

**Architecture:** A new `ProjectManager` class sits between the server and orchestrator. It reads a global registry at `~/.ai-team/projects.yaml`, handles project CRUD, and swaps orchestrators when the user switches projects. The dashboard gets a landing page state, project selector nav component, and create-project wizard. All existing code stays unchanged — it already operates on `project_dir`.

**Tech Stack:** Python/FastAPI backend, YAML registry (PyYAML), vanilla JS frontend, existing SQLite/async infrastructure

---

## Task 1: ProjectManager — Registry CRUD

**Files:**
- Create: `src/taskbrew/project_manager.py`
- Create: `tests/test_project_manager.py`

**Context:** This is the core new class. It manages `~/.ai-team/projects.yaml` — a YAML file that maps project IDs to their directory paths. No orchestrator logic yet — just registry read/write/validate.

**Step 1: Write the failing tests**

```python
# tests/test_project_manager.py
"""Tests for ProjectManager registry operations."""

import pytest
import yaml
from pathlib import Path
from taskbrew.project_manager import ProjectManager


@pytest.fixture
def registry_path(tmp_path):
    return tmp_path / ".ai-team" / "projects.yaml"


@pytest.fixture
def pm(registry_path):
    return ProjectManager(registry_path=registry_path)


class TestRegistryLifecycle:
    def test_list_empty_when_no_registry(self, pm):
        """No registry file → empty list."""
        assert pm.list_projects() == []

    def test_create_project_returns_id(self, pm, tmp_path):
        project_dir = tmp_path / "my-project"
        project_dir.mkdir()
        result = pm.create_project("My Project", str(project_dir))
        assert result["id"] == "my-project"
        assert result["name"] == "My Project"
        assert result["directory"] == str(project_dir)

    def test_create_project_persists_to_yaml(self, pm, registry_path, tmp_path):
        project_dir = tmp_path / "test-proj"
        project_dir.mkdir()
        pm.create_project("Test Proj", str(project_dir))
        assert registry_path.exists()
        data = yaml.safe_load(registry_path.read_text())
        assert "test-proj" in data["projects"]

    def test_create_project_creates_missing_directory(self, pm, tmp_path):
        project_dir = tmp_path / "new-dir"
        assert not project_dir.exists()
        pm.create_project("New Dir", str(project_dir))
        assert project_dir.exists()

    def test_create_project_rejects_relative_path(self, pm):
        with pytest.raises(ValueError, match="absolute"):
            pm.create_project("Bad", "relative/path")

    def test_create_project_rejects_duplicate_id(self, pm, tmp_path):
        d1 = tmp_path / "proj"
        d1.mkdir()
        pm.create_project("Proj", str(d1))
        d2 = tmp_path / "proj2"
        d2.mkdir()
        with pytest.raises(ValueError, match="already exists"):
            pm.create_project("Proj", str(d2))

    def test_list_projects_returns_all(self, pm, tmp_path):
        for name in ["alpha", "beta"]:
            d = tmp_path / name
            d.mkdir()
            pm.create_project(name.title(), str(d))
        projects = pm.list_projects()
        assert len(projects) == 2
        ids = {p["id"] for p in projects}
        assert ids == {"alpha", "beta"}

    def test_delete_project_removes_from_registry(self, pm, tmp_path):
        d = tmp_path / "to-delete"
        d.mkdir()
        pm.create_project("To Delete", str(d))
        pm.delete_project("to-delete")
        assert pm.list_projects() == []

    def test_delete_project_does_not_remove_files(self, pm, tmp_path):
        d = tmp_path / "keep-files"
        d.mkdir()
        (d / "important.txt").write_text("keep me")
        pm.create_project("Keep Files", str(d))
        pm.delete_project("keep-files")
        assert (d / "important.txt").exists()

    def test_delete_nonexistent_raises(self, pm):
        with pytest.raises(KeyError):
            pm.delete_project("nope")

    def test_get_active_none_initially(self, pm):
        assert pm.get_active() is None

    def test_set_active_persists(self, pm, registry_path, tmp_path):
        d = tmp_path / "active-proj"
        d.mkdir()
        pm.create_project("Active Proj", str(d))
        pm.set_active("active-proj")
        data = yaml.safe_load(registry_path.read_text())
        assert data["active_project"] == "active-proj"

    def test_get_active_returns_project(self, pm, tmp_path):
        d = tmp_path / "the-proj"
        d.mkdir()
        pm.create_project("The Proj", str(d))
        pm.set_active("the-proj")
        active = pm.get_active()
        assert active["id"] == "the-proj"
        assert active["name"] == "The Proj"

    def test_clear_active(self, pm, tmp_path):
        d = tmp_path / "proj"
        d.mkdir()
        pm.create_project("Proj", str(d))
        pm.set_active("proj")
        pm.clear_active()
        assert pm.get_active() is None

    def test_corrupted_registry_resets(self, pm, registry_path):
        registry_path.parent.mkdir(parents=True, exist_ok=True)
        registry_path.write_text("{{invalid yaml")
        projects = pm.list_projects()
        assert projects == []


class TestProjectScaffolding:
    def test_scaffold_creates_config_dir(self, pm, tmp_path):
        d = tmp_path / "scaffolded"
        pm.create_project("Scaffolded", str(d))
        assert (d / "config").is_dir()
        assert (d / "config" / "roles").is_dir()

    def test_scaffold_creates_team_yaml(self, pm, tmp_path):
        d = tmp_path / "scaffolded"
        pm.create_project("Scaffolded", str(d))
        team_yaml = d / "config" / "team.yaml"
        assert team_yaml.exists()
        data = yaml.safe_load(team_yaml.read_text())
        assert data["team_name"] == "Scaffolded"

    def test_scaffold_with_defaults_creates_role_files(self, pm, tmp_path):
        d = tmp_path / "with-defaults"
        pm.create_project("With Defaults", str(d), with_defaults=True)
        roles_dir = d / "config" / "roles"
        role_files = sorted(f.name for f in roles_dir.glob("*.yaml"))
        assert role_files == ["architect.yaml", "coder.yaml", "pm.yaml", "reviewer.yaml", "tester.yaml"]

    def test_scaffold_without_defaults_empty_roles(self, pm, tmp_path):
        d = tmp_path / "no-defaults"
        pm.create_project("No Defaults", str(d), with_defaults=False)
        roles_dir = d / "config" / "roles"
        assert roles_dir.is_dir()
        assert list(roles_dir.glob("*.yaml")) == []

    def test_existing_config_not_overwritten(self, pm, tmp_path):
        d = tmp_path / "existing"
        d.mkdir()
        (d / "config").mkdir()
        team_yaml = d / "config" / "team.yaml"
        team_yaml.write_text("team_name: Original\n")
        pm.create_project("Existing", str(d))
        assert "Original" in team_yaml.read_text()


class TestSlugGeneration:
    def test_simple_name(self, pm, tmp_path):
        d = tmp_path / "a"
        d.mkdir()
        result = pm.create_project("My SaaS App", str(d))
        assert result["id"] == "my-saas-app"

    def test_special_characters_removed(self, pm, tmp_path):
        d = tmp_path / "b"
        d.mkdir()
        result = pm.create_project("Hello World! (v2)", str(d))
        assert result["id"] == "hello-world-v2"
```

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_project_manager.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'taskbrew.project_manager'`

**Step 3: Implement ProjectManager**

```python
# src/taskbrew/project_manager.py
"""Project manager — handles the global project registry and scaffolding."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

_DEFAULT_REGISTRY = {"active_project": None, "projects": {}}

# Default team.yaml template for new projects
_DEFAULT_TEAM_YAML = """\
team_name: "{name}"

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

auth:
  enabled: false
  tokens: []

cost_budgets:
  enabled: false

webhooks:
  enabled: false
"""


def _slugify(name: str) -> str:
    """Convert a project name to a URL-safe slug ID."""
    slug = name.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    return slug or "project"


class ProjectManager:
    """Manages the global project registry at ``registry_path``.

    The registry is a YAML file with this structure::

        active_project: <project_id> | null
        projects:
          <project_id>:
            name: "Human Name"
            directory: "/absolute/path"
            created_at: "ISO-8601"
    """

    def __init__(self, registry_path: Path | str | None = None) -> None:
        if registry_path is None:
            registry_path = Path.home() / ".ai-team" / "projects.yaml"
        self.registry_path = Path(registry_path)

    # ------------------------------------------------------------------
    # Registry I/O
    # ------------------------------------------------------------------

    def _read_registry(self) -> dict:
        if not self.registry_path.exists():
            return dict(_DEFAULT_REGISTRY)
        try:
            data = yaml.safe_load(self.registry_path.read_text()) or {}
            if not isinstance(data, dict) or "projects" not in data:
                logger.warning("Corrupted registry at %s, resetting", self.registry_path)
                return dict(_DEFAULT_REGISTRY)
            return data
        except Exception:
            logger.warning("Failed to read registry at %s, resetting", self.registry_path, exc_info=True)
            return dict(_DEFAULT_REGISTRY)

    def _write_registry(self, data: dict) -> None:
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)
        self.registry_path.write_text(
            yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False)
        )

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def list_projects(self) -> list[dict]:
        data = self._read_registry()
        result = []
        for pid, info in data.get("projects", {}).items():
            result.append({"id": pid, **info})
        return result

    def create_project(
        self, name: str, directory: str, with_defaults: bool = True
    ) -> dict:
        path = Path(directory)
        if not path.is_absolute():
            raise ValueError(f"Project directory must be absolute, got: {directory}")

        slug = _slugify(name)
        data = self._read_registry()

        if slug in data.get("projects", {}):
            raise ValueError(f"Project '{slug}' already exists")

        # Create directory if needed
        path.mkdir(parents=True, exist_ok=True)

        # Scaffold config
        self._scaffold(path, name, with_defaults)

        # Register
        now = datetime.now(timezone.utc).isoformat()
        data.setdefault("projects", {})[slug] = {
            "name": name,
            "directory": str(path),
            "created_at": now,
        }
        self._write_registry(data)

        return {"id": slug, "name": name, "directory": str(path), "created_at": now}

    def delete_project(self, project_id: str) -> None:
        data = self._read_registry()
        if project_id not in data.get("projects", {}):
            raise KeyError(f"Project '{project_id}' not found")
        del data["projects"][project_id]
        if data.get("active_project") == project_id:
            data["active_project"] = None
        self._write_registry(data)

    def get_active(self) -> dict | None:
        data = self._read_registry()
        active_id = data.get("active_project")
        if not active_id or active_id not in data.get("projects", {}):
            return None
        return {"id": active_id, **data["projects"][active_id]}

    def set_active(self, project_id: str) -> None:
        data = self._read_registry()
        if project_id not in data.get("projects", {}):
            raise KeyError(f"Project '{project_id}' not found")
        data["active_project"] = project_id
        self._write_registry(data)

    def clear_active(self) -> None:
        data = self._read_registry()
        data["active_project"] = None
        self._write_registry(data)

    # ------------------------------------------------------------------
    # Scaffolding
    # ------------------------------------------------------------------

    def _scaffold(self, project_dir: Path, name: str, with_defaults: bool) -> None:
        config_dir = project_dir / "config"
        roles_dir = config_dir / "roles"
        roles_dir.mkdir(parents=True, exist_ok=True)

        # Only write team.yaml if it doesn't exist
        team_yaml = config_dir / "team.yaml"
        if not team_yaml.exists():
            team_yaml.write_text(_DEFAULT_TEAM_YAML.format(name=name))

        if with_defaults:
            self._scaffold_default_roles(roles_dir)

    def _scaffold_default_roles(self, roles_dir: Path) -> None:
        defaults = _get_default_roles()
        for role_name, role_data in defaults.items():
            role_file = roles_dir / f"{role_name}.yaml"
            if not role_file.exists():
                role_file.write_text(
                    yaml.dump(role_data, default_flow_style=False,
                              allow_unicode=True, sort_keys=False)
                )


def _get_default_roles() -> dict[str, dict]:
    """Return default role configurations for new projects."""
    return {
        "pm": {
            "role": "pm",
            "display_name": "Product Manager",
            "prefix": "PM",
            "color": "#3b82f6",
            "emoji": "\U0001F4CB",
            "max_turns": 200,
            "system_prompt": (
                "You are a Product Manager on an AI development team.\n"
                "Your responsibilities:\n"
                "1. Decompose high-level goals into detailed PRDs with acceptance criteria\n"
                "2. Read the codebase to understand scope and dependencies\n"
                "3. Create well-scoped tasks for the Architect team using the create_task tool\n"
                "4. You NEVER write code — only analysis and documentation\n"
            ),
            "tools": ["Read", "Glob", "Grep", "WebSearch", "mcp__task-tools__create_task"],
            "model": "claude-opus-4-6",
            "produces": ["prd", "goal_decomposition", "requirement"],
            "accepts": ["goal", "revision"],
            "routes_to": [{"role": "architect", "task_types": ["tech_design", "architecture_review"]}],
            "can_create_groups": True,
            "group_type": "FEAT",
            "max_instances": 1,
            "requires_approval": ["prd"],
            "context_includes": ["parent_artifact", "root_artifact", "sibling_summary"],
        },
        "architect": {
            "role": "architect",
            "display_name": "Architect",
            "prefix": "AR",
            "color": "#8b5cf6",
            "emoji": "\U0001F3D7",
            "max_turns": 200,
            "system_prompt": "You are a Software Architect on an AI development team.\n",
            "tools": ["Read", "Glob", "Grep", "Write", "WebSearch", "mcp__task-tools__create_task"],
            "model": "claude-opus-4-6",
            "produces": ["tech_design", "architecture_review"],
            "accepts": ["tech_design", "architecture_review", "revision"],
            "routes_to": [{"role": "coder", "task_types": ["implementation", "bug_fix"]}],
            "can_create_groups": False,
            "max_instances": 2,
            "context_includes": ["parent_artifact", "sibling_summary"],
        },
        "coder": {
            "role": "coder",
            "display_name": "Coder",
            "prefix": "CD",
            "color": "#f59e0b",
            "emoji": "\U0001F4BB",
            "max_turns": 200,
            "system_prompt": "You are a Software Engineer on an AI development team.\n",
            "tools": ["Read", "Write", "Edit", "Bash", "Glob", "Grep", "mcp__task-tools__create_task"],
            "model": "claude-opus-4-6",
            "produces": ["code_change", "implementation"],
            "accepts": ["implementation", "bug_fix", "revision"],
            "routes_to": [{"role": "tester", "task_types": ["qa_test"]}],
            "can_create_groups": False,
            "max_instances": 3,
            "context_includes": ["parent_artifact", "sibling_summary"],
        },
        "tester": {
            "role": "tester",
            "display_name": "Tester",
            "prefix": "TS",
            "color": "#10b981",
            "emoji": "\U0001F9EA",
            "max_turns": 200,
            "system_prompt": "You are a QA Engineer on an AI development team.\n",
            "tools": ["Read", "Write", "Edit", "Bash", "Glob", "Grep", "mcp__task-tools__create_task"],
            "model": "claude-opus-4-6",
            "produces": ["test_report", "qa_result"],
            "accepts": ["qa_test", "revision"],
            "routes_to": [{"role": "reviewer", "task_types": ["code_review"]}],
            "can_create_groups": False,
            "max_instances": 2,
            "context_includes": ["parent_artifact", "sibling_summary"],
        },
        "reviewer": {
            "role": "reviewer",
            "display_name": "Code Reviewer",
            "prefix": "RV",
            "color": "#ec4899",
            "emoji": "\U0001F50D",
            "max_turns": 200,
            "system_prompt": "You are a Code Reviewer on an AI development team.\n",
            "tools": ["Read", "Glob", "Grep", "Bash", "mcp__task-tools__create_task"],
            "model": "claude-opus-4-6",
            "produces": ["code_review", "approval"],
            "accepts": ["code_review", "revision"],
            "routes_to": [],
            "can_create_groups": False,
            "max_instances": 1,
            "context_includes": ["parent_artifact", "sibling_summary"],
        },
    }
```

**Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_project_manager.py -v`
Expected: All 18 tests PASS

**Step 5: Commit**

```bash
git add src/taskbrew/project_manager.py tests/test_project_manager.py
git commit -m "feat: add ProjectManager with registry CRUD, scaffolding, and slug generation"
```

---

## Task 2: ProjectManager — Orchestrator Lifecycle (activate/deactivate)

**Files:**
- Modify: `src/taskbrew/project_manager.py`
- Modify: `tests/test_project_manager.py`

**Context:** Now we add the async methods that actually boot and tear down orchestrators. `activate_project()` calls the existing `build_orchestrator()` from `main.py`. `deactivate_current()` calls `orchestrator.shutdown()`. The ProjectManager holds a reference to the currently active orchestrator.

**Step 1: Write the failing tests**

Add to `tests/test_project_manager.py`:

```python
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock


@pytest.fixture
def scaffolded_project(pm, tmp_path):
    """Create a project with default roles and config."""
    d = tmp_path / "test-project"
    pm.create_project("Test Project", str(d), with_defaults=True)
    return d


class TestOrchestratorLifecycle:
    @pytest.mark.asyncio
    async def test_activate_sets_orchestrator(self, pm, scaffolded_project):
        with patch("taskbrew.project_manager.build_orchestrator") as mock_build:
            mock_orch = MagicMock()
            mock_build.return_value = mock_orch
            result = await pm.activate_project("test-project")
            assert result is mock_orch
            assert pm.orchestrator is mock_orch

    @pytest.mark.asyncio
    async def test_activate_updates_registry(self, pm, scaffolded_project):
        with patch("taskbrew.project_manager.build_orchestrator") as mock_build:
            mock_build.return_value = MagicMock()
            await pm.activate_project("test-project")
            assert pm.get_active()["id"] == "test-project"

    @pytest.mark.asyncio
    async def test_activate_deactivates_previous(self, pm, tmp_path):
        d1 = tmp_path / "proj1"
        d2 = tmp_path / "proj2"
        pm.create_project("Proj1", str(d1), with_defaults=True)
        pm.create_project("Proj2", str(d2), with_defaults=True)

        with patch("taskbrew.project_manager.build_orchestrator") as mock_build:
            orch1 = MagicMock()
            orch1.shutdown = AsyncMock()
            orch2 = MagicMock()
            mock_build.side_effect = [orch1, orch2]

            await pm.activate_project("proj1")
            await pm.activate_project("proj2")

            orch1.shutdown.assert_awaited_once()
            assert pm.orchestrator is orch2

    @pytest.mark.asyncio
    async def test_deactivate_shuts_down(self, pm, scaffolded_project):
        with patch("taskbrew.project_manager.build_orchestrator") as mock_build:
            mock_orch = MagicMock()
            mock_orch.shutdown = AsyncMock()
            mock_build.return_value = mock_orch

            await pm.activate_project("test-project")
            await pm.deactivate_current()

            mock_orch.shutdown.assert_awaited_once()
            assert pm.orchestrator is None

    @pytest.mark.asyncio
    async def test_deactivate_when_none_is_noop(self, pm):
        await pm.deactivate_current()  # should not raise

    @pytest.mark.asyncio
    async def test_activate_nonexistent_raises(self, pm):
        with pytest.raises(KeyError):
            await pm.activate_project("nope")
```

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_project_manager.py::TestOrchestratorLifecycle -v`
Expected: FAIL — activate_project / deactivate_current not defined

**Step 3: Implement activate/deactivate**

Add to `ProjectManager` class in `src/taskbrew/project_manager.py`:

```python
    def __init__(self, registry_path=None):
        # ... existing code ...
        self.orchestrator = None  # Currently active orchestrator

    async def activate_project(self, project_id: str):
        """Boot the orchestrator for a project. Deactivates any current project first."""
        from taskbrew.main import build_orchestrator

        data = self._read_registry()
        if project_id not in data.get("projects", {}):
            raise KeyError(f"Project '{project_id}' not found")

        # Deactivate current if any
        if self.orchestrator is not None:
            await self.deactivate_current()

        project_info = data["projects"][project_id]
        project_dir = Path(project_info["directory"])

        self.orchestrator = await build_orchestrator(project_dir=project_dir)
        self.set_active(project_id)

        logger.info("Activated project '%s' at %s", project_id, project_dir)
        return self.orchestrator

    async def deactivate_current(self) -> None:
        """Stop agents and close DB for the current project."""
        if self.orchestrator is None:
            return
        try:
            await self.orchestrator.shutdown()
        except Exception:
            logger.warning("Error during orchestrator shutdown", exc_info=True)
        self.orchestrator = None
        self.clear_active()
        logger.info("Deactivated current project")
```

**Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_project_manager.py -v`
Expected: All tests PASS

**Step 5: Commit**

```bash
git add src/taskbrew/project_manager.py tests/test_project_manager.py
git commit -m "feat: add activate/deactivate orchestrator lifecycle to ProjectManager"
```

---

## Task 3: Modify main.py — ProjectManager-Based Startup

**Files:**
- Modify: `src/taskbrew/main.py:278-325`
- Modify: `src/taskbrew/main.py:158-232` (run_server function)

**Context:** The `serve` command now creates a ProjectManager, checks for auto-migration, and passes it to the dashboard. The `run_server()` function is refactored so that agent spawning is separate from server startup (since agents need to restart on project switch).

**Step 1: Refactor run_server to accept ProjectManager**

Modify `run_server()` in `src/taskbrew/main.py` to:
1. Accept a `ProjectManager` instead of a bare `Orchestrator`
2. Pass the project_manager to `create_app()`
3. Move agent spawning into a separate `start_agents(orch)` function
4. Keep orphan recovery and auto-scaler setup in `start_agents()`

The new `start_agents` function:

```python
async def start_agents(orch: Orchestrator) -> None:
    """Spawn agent loops and background tasks for the given orchestrator."""
    # Recover orphaned tasks
    recovered = await orch.task_board.recover_orphaned_tasks()
    if recovered:
        logger.info("Recovered %d orphaned tasks", len(recovered))
        for t in recovered:
            await orch.event_bus.emit("task.recovered", {"task_id": t["id"]})

    stuck = await orch.task_board.recover_stuck_blocked_tasks()
    if stuck:
        logger.info("Recovered %d stuck blocked tasks", len(stuck))

    # Background orphan recovery
    recovery_task = asyncio.create_task(_orphan_recovery_loop(orch))
    orch.agent_tasks.append(recovery_task)

    # Spawn agent loops
    connect_host = "127.0.0.1" if orch.team_config.dashboard_host in ("0.0.0.0", "::") else orch.team_config.dashboard_host
    api_url = f"http://{connect_host}:{orch.team_config.dashboard_port}"
    for role_name, role_config in orch.roles.items():
        needs_worktree = "Bash" in role_config.tools
        for i in range(1, role_config.max_instances + 1):
            instance_id = f"{role_name}-{i}"
            loop = AgentLoop(
                instance_id=instance_id,
                role_config=role_config,
                board=orch.task_board,
                event_bus=orch.event_bus,
                instance_manager=orch.instance_manager,
                all_roles=orch.roles,
                project_dir=orch.project_dir,
                poll_interval=orch.team_config.default_poll_interval,
                api_url=api_url,
                worktree_manager=orch.worktree_manager if needs_worktree else None,
            )
            task = asyncio.create_task(loop.run())
            orch.agent_tasks.append(task)

    # Auto-scaler
    has_auto_scale = any(
        r.auto_scale and r.auto_scale.enabled for r in orch.roles.values()
        if hasattr(r, 'auto_scale') and r.auto_scale
    )
    if has_auto_scale:
        from taskbrew.agents.auto_scaler import AutoScaler
        scaler = AutoScaler(orch.task_board, orch.instance_manager, orch.roles)
        scaler_task = asyncio.create_task(scaler.run())
        orch.agent_tasks.append(scaler_task)
```

The new `run_server`:

```python
async def run_server(project_manager: ProjectManager):
    import uvicorn
    from taskbrew.dashboard.app import create_app

    app = create_app(project_manager=project_manager)

    # If there's an active project, start its agents
    if project_manager.orchestrator:
        await start_agents(project_manager.orchestrator)

    # Determine host/port — use active project's config or defaults
    orch = project_manager.orchestrator
    host = orch.team_config.dashboard_host if orch else "127.0.0.1"
    port = orch.team_config.dashboard_port if orch else 8420

    config = uvicorn.Config(app, host=host, port=port, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()
```

**Step 2: Update async_main for auto-migration**

```python
async def async_main(args):
    if args.command == "serve":
        from taskbrew.project_manager import ProjectManager

        pm = ProjectManager()

        # Auto-migration: if --project-dir passed or CWD has config
        project_dir = Path(args.project_dir) if args.project_dir else None

        if project_dir is None:
            # Check if CWD looks like a project
            cwd = Path.cwd()
            if (cwd / "config" / "team.yaml").exists() and pm.get_active() is None:
                # Auto-register CWD as a project
                name = cwd.name.replace("-", " ").replace("_", " ").title()
                try:
                    pm.create_project(name, str(cwd))
                except ValueError:
                    pass  # already registered
                slug = _slugify(name)
                await pm.activate_project(slug)

        elif project_dir:
            # --project-dir flag: register and activate
            name = project_dir.name.replace("-", " ").replace("_", " ").title()
            try:
                pm.create_project(name, str(project_dir.resolve()))
            except ValueError:
                pass  # already registered
            slug = _slugify(name)
            await pm.activate_project(slug)

        else:
            # Try to activate last-used project
            active = pm.get_active()
            if active:
                try:
                    await pm.activate_project(active["id"])
                except Exception:
                    logger.warning("Failed to activate project %s", active["id"])

        try:
            await run_server(pm)
        finally:
            await pm.deactivate_current()
    # ... goal and status commands unchanged
```

Add the `_slugify` import at the top of `main.py`:

```python
from taskbrew.project_manager import ProjectManager, _slugify
```

**Step 3: Run all tests**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: All existing 105 tests + new project manager tests PASS

**Step 4: Commit**

```bash
git add src/taskbrew/main.py
git commit -m "feat: refactor main.py to use ProjectManager for startup and agent lifecycle"
```

---

## Task 4: Modify app.py — Project API Endpoints & Guards

**Files:**
- Modify: `src/taskbrew/dashboard/app.py:73-100` (create_app signature)
- Modify: `src/taskbrew/dashboard/app.py` (add project endpoints, add 409 guards)

**Context:** The `create_app()` function now accepts a `ProjectManager` instead of individual orchestrator components. All existing endpoints access the orchestrator via `project_manager.orchestrator`. When no project is active, they return 409. Six new `/api/projects/*` endpoints are added.

**Step 1: Refactor create_app signature**

Change `create_app` to accept `ProjectManager`:

```python
def create_app(
    project_manager=None,
    # Keep old params for backward compat in tests
    event_bus=None,
    task_board=None,
    instance_manager=None,
    chat_manager=None,
    roles=None,
    team_config=None,
    project_dir=None,
) -> FastAPI:
```

Add a helper to get current orchestrator components:

```python
    def _get_orch():
        """Get current orchestrator or raise 409."""
        if project_manager and project_manager.orchestrator:
            return project_manager.orchestrator
        # Fallback for backward compat (tests pass components directly)
        if task_board is not None:
            return type('Orch', (), {
                'task_board': task_board,
                'event_bus': event_bus,
                'instance_manager': instance_manager,
                'roles': roles,
                'team_config': team_config,
                'project_dir': project_dir,
            })()
        raise HTTPException(409, "No active project. Create or activate a project first.")
```

Then update existing endpoints to use `_get_orch()` instead of the closure variables. For example:

```python
    @app.get("/api/board")
    async def get_board():
        orch = _get_orch()
        return await orch.task_board.get_board()
```

**Step 2: Add project endpoints**

```python
    # ------------------------------------------------------------------
    # Projects
    # ------------------------------------------------------------------

    @app.get("/api/projects")
    async def list_projects():
        if not project_manager:
            return []
        return project_manager.list_projects()

    @app.post("/api/projects")
    async def create_project(body: dict):
        if not project_manager:
            raise HTTPException(500, "Project manager not initialized")
        name = body.get("name", "").strip()
        directory = body.get("directory", "").strip()
        with_defaults = body.get("with_defaults", True)
        if not name:
            raise HTTPException(400, "Project name is required")
        if not directory:
            raise HTTPException(400, "Project directory is required")
        try:
            result = project_manager.create_project(name, directory, with_defaults)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return result

    @app.delete("/api/projects/{project_id}")
    async def delete_project(project_id: str):
        if not project_manager:
            raise HTTPException(500, "Project manager not initialized")
        try:
            project_manager.delete_project(project_id)
        except KeyError:
            raise HTTPException(404, f"Project '{project_id}' not found")
        return {"status": "ok"}

    @app.post("/api/projects/{project_id}/activate")
    async def activate_project(project_id: str):
        if not project_manager:
            raise HTTPException(500, "Project manager not initialized")
        try:
            orch = await project_manager.activate_project(project_id)
        except KeyError:
            raise HTTPException(404, f"Project '{project_id}' not found")

        # Start agents for the new project
        from taskbrew.main import start_agents
        await start_agents(orch)

        return {"status": "ok", "project": project_manager.get_active()}

    @app.get("/api/projects/active")
    async def get_active_project():
        if not project_manager:
            return None
        return project_manager.get_active()

    @app.post("/api/projects/active/deactivate")
    async def deactivate_project():
        if not project_manager:
            raise HTTPException(500, "Project manager not initialized")
        await project_manager.deactivate_current()
        return {"status": "ok"}
```

**Step 3: Add has_project check endpoint for frontend**

```python
    @app.get("/api/projects/status")
    async def project_status():
        """Quick check for the frontend to determine what UI state to show."""
        if not project_manager:
            return {"has_manager": False, "has_projects": False, "active": None}
        projects = project_manager.list_projects()
        active = project_manager.get_active()
        return {
            "has_manager": True,
            "has_projects": len(projects) > 0,
            "project_count": len(projects),
            "active": active,
        }
```

**Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: All tests PASS (existing tests still pass because of backward-compat fallback in `_get_orch()`)

**Step 5: Commit**

```bash
git add src/taskbrew/dashboard/app.py
git commit -m "feat: add project CRUD endpoints and 409 guards for no-active-project"
```

---

## Task 5: Frontend — Landing Page & Create Project Wizard

**Files:**
- Modify: `src/taskbrew/dashboard/templates/index.html`

**Context:** When the dashboard loads, it first calls `GET /api/projects/status`. If `has_projects` is false, show the landing page. If `active` is null but projects exist, show project picker. Otherwise, load the normal dashboard.

**Step 1: Add landing page HTML**

Add before the main dashboard content in `index.html`, a hidden section:

```html
<!-- Landing Page — shown when no projects exist -->
<div id="landingPage" style="display:none" class="landing-page">
    <div class="landing-content">
        <div class="landing-icon">🚀</div>
        <h1>Welcome to AI Team</h1>
        <p class="landing-subtitle">Create your first project to get started with AI-powered development agents.</p>
        <button class="btn-primary btn-lg" onclick="openCreateProjectWizard()">
            Create Your First Project
        </button>
    </div>
</div>
```

**Step 2: Add Create Project Wizard modal**

```html
<!-- Create Project Wizard -->
<div id="createProjectOverlay" class="wizard-overlay" style="display:none">
    <div class="wizard-modal">
        <div class="wizard-header">
            <h2>Create New Project</h2>
            <button class="wizard-close" onclick="closeCreateProjectWizard()">&times;</button>
        </div>
        <div class="wizard-steps">
            <div class="step-dot active" data-step="1">1</div>
            <div class="step-line"></div>
            <div class="step-dot" data-step="2">2</div>
        </div>
        <div id="wizardProjectContent">
            <!-- Rendered by JS -->
        </div>
        <div class="wizard-footer">
            <button id="wizardPrevBtn" class="btn-secondary" onclick="wizardPrevStep()" style="display:none">Back</button>
            <button id="wizardNextBtn" class="btn-primary" onclick="wizardNextStep()">Next</button>
        </div>
    </div>
</div>
```

**Step 3: Add Project Selector to nav bar**

Add a dropdown in the top navigation:

```html
<div class="project-selector" id="projectSelector" style="display:none">
    <button class="project-selector-btn" onclick="toggleProjectDropdown()">
        <span class="project-dot"></span>
        <span id="activeProjectName">No Project</span>
        <span class="dropdown-caret">▾</span>
    </button>
    <div class="project-dropdown" id="projectDropdown" style="display:none">
        <div id="projectList"><!-- Populated by JS --></div>
        <div class="dropdown-divider"></div>
        <button class="dropdown-item new-project" onclick="openCreateProjectWizard()">+ New Project</button>
    </div>
</div>
```

**Step 4: Add CSS for landing page, wizard, and project selector**

Style the landing page with centered content, large icon, gradient button. Style the wizard as a modal (similar to the existing create-agent wizard in settings.html). Style the project selector dropdown in the nav.

**Step 5: Add JavaScript for project management**

```javascript
// Project state
let projectWizardStep = 1;
let projectWizardData = { name: '', directory: '', with_defaults: true };

async function checkProjectStatus() {
    const resp = await fetch('/api/projects/status');
    const status = await resp.json();

    if (!status.has_projects) {
        // Show landing page, hide dashboard
        document.getElementById('landingPage').style.display = 'flex';
        document.getElementById('mainDashboard').style.display = 'none';
        return;
    }

    if (!status.active) {
        // Projects exist but none active — show selector
        document.getElementById('projectSelector').style.display = 'block';
        await loadProjectList();
        toggleProjectDropdown(); // auto-open
        return;
    }

    // Normal state — show dashboard with selector
    document.getElementById('landingPage').style.display = 'none';
    document.getElementById('mainDashboard').style.display = 'block';
    document.getElementById('projectSelector').style.display = 'block';
    document.getElementById('activeProjectName').textContent = status.active.name;
    loadDashboard(); // existing function
}

async function createProject() {
    const resp = await fetch('/api/projects', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(projectWizardData),
    });
    if (!resp.ok) {
        const err = await resp.json();
        showToast(err.detail || 'Failed to create project', 'error');
        return;
    }
    const project = await resp.json();

    // Activate it
    await fetch(`/api/projects/${project.id}/activate`, { method: 'POST' });

    closeCreateProjectWizard();

    if (!projectWizardData.with_defaults) {
        window.location.href = '/settings';
    } else {
        window.location.reload();
    }
}

async function switchProject(projectId) {
    document.getElementById('projectDropdown').style.display = 'none';
    showToast('Switching project...', 'info');
    const resp = await fetch(`/api/projects/${projectId}/activate`, { method: 'POST' });
    if (resp.ok) {
        window.location.reload();
    } else {
        showToast('Failed to switch project', 'error');
    }
}
```

**Step 6: Wire up on page load**

Replace the existing `DOMContentLoaded` or page init to call `checkProjectStatus()` first:

```javascript
document.addEventListener('DOMContentLoaded', () => {
    checkProjectStatus();
});
```

The existing `loadDashboard()` / WebSocket init only runs if `checkProjectStatus()` determines there's an active project.

**Step 7: Run tests and manual verification**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: All tests pass

Manual: Start server, navigate to `http://127.0.0.1:8420/` — should see landing page since no project is active in the new ProjectManager flow.

**Step 8: Commit**

```bash
git add src/taskbrew/dashboard/templates/index.html
git commit -m "feat: add landing page, create-project wizard, and project selector dropdown"
```

---

## Task 6: Frontend — Project Selector in Settings & Metrics Pages

**Files:**
- Modify: `src/taskbrew/dashboard/templates/settings.html`
- Modify: `src/taskbrew/dashboard/templates/metrics.html`

**Context:** Add the same project selector dropdown to the settings and metrics page headers. Both pages should also check project status on load and redirect to `/` if no project is active.

**Step 1: Add project selector to settings.html header**

In the `<header class="settings-header">` section, add the project selector dropdown (same HTML as index.html) between the back link and the title.

**Step 2: Add project selector to metrics.html header**

Same pattern — add the dropdown to the metrics page navigation.

**Step 3: Add project check on load for both pages**

```javascript
async function checkProjectOrRedirect() {
    const resp = await fetch('/api/projects/status');
    const status = await resp.json();
    if (!status.active) {
        window.location.href = '/';
        return false;
    }
    document.getElementById('activeProjectName').textContent = status.active.name;
    return true;
}
```

Call this before loading settings/metrics data. If it returns false, the page redirects to `/` where the landing page or project picker handles the flow.

**Step 4: Add shared project selector CSS and JS**

Since all three pages need the same project selector, add the CSS and JS to each. (In a future cleanup task, this could be extracted to a shared file, but for now inline in each template is fine — matches the existing pattern.)

**Step 5: Commit**

```bash
git add src/taskbrew/dashboard/templates/settings.html src/taskbrew/dashboard/templates/metrics.html
git commit -m "feat: add project selector to settings and metrics pages with active-project guard"
```

---

## Task 7: Auto-Migration & Integration Testing

**Files:**
- Create: `tests/test_project_integration.py`
- Modify: `tests/conftest.py` (add project fixtures)

**Context:** End-to-end tests verifying: auto-migration when CWD has existing config, project creation + activation flow, project switching, and that existing tests still pass.

**Step 1: Write integration tests**

```python
# tests/test_project_integration.py
"""Integration tests for multi-project support."""

import pytest
import yaml
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock
from taskbrew.project_manager import ProjectManager


@pytest.fixture
def pm_with_registry(tmp_path):
    registry = tmp_path / "registry" / "projects.yaml"
    return ProjectManager(registry_path=registry)


class TestAutoMigration:
    def test_cwd_with_config_gets_registered(self, pm_with_registry, tmp_path):
        """Simulate existing project dir with config/team.yaml."""
        project_dir = tmp_path / "existing-project"
        project_dir.mkdir()
        config_dir = project_dir / "config"
        config_dir.mkdir()
        (config_dir / "team.yaml").write_text("team_name: Legacy\n")
        (config_dir / "roles").mkdir()

        # Register as if auto-migration happened
        pm_with_registry.create_project("Existing Project", str(project_dir))
        projects = pm_with_registry.list_projects()
        assert len(projects) == 1
        assert projects[0]["directory"] == str(project_dir)

        # Config should NOT be overwritten
        assert "Legacy" in (config_dir / "team.yaml").read_text()


class TestProjectSwitching:
    def test_create_two_projects(self, pm_with_registry, tmp_path):
        d1 = tmp_path / "project-a"
        d2 = tmp_path / "project-b"
        pm_with_registry.create_project("Project A", str(d1))
        pm_with_registry.create_project("Project B", str(d2))
        assert len(pm_with_registry.list_projects()) == 2

    def test_each_project_has_isolated_config(self, pm_with_registry, tmp_path):
        d1 = tmp_path / "proj1"
        d2 = tmp_path / "proj2"
        pm_with_registry.create_project("Proj1", str(d1))
        pm_with_registry.create_project("Proj2", str(d2))

        # Each has its own team.yaml
        t1 = yaml.safe_load((d1 / "config" / "team.yaml").read_text())
        t2 = yaml.safe_load((d2 / "config" / "team.yaml").read_text())
        assert t1["team_name"] == "Proj1"
        assert t2["team_name"] == "Proj2"

    def test_delete_clears_active_if_same(self, pm_with_registry, tmp_path):
        d = tmp_path / "to-delete"
        pm_with_registry.create_project("To Delete", str(d))
        pm_with_registry.set_active("to-delete")
        pm_with_registry.delete_project("to-delete")
        assert pm_with_registry.get_active() is None
```

**Step 2: Run all tests**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: All tests PASS (old 105 + new project manager + integration)

**Step 3: Commit**

```bash
git add tests/test_project_integration.py tests/conftest.py
git commit -m "test: add integration tests for multi-project auto-migration and switching"
```

---

## Task 8: Final Polish — Error Handling & Edge Cases

**Files:**
- Modify: `src/taskbrew/project_manager.py` (add directory validation)
- Modify: `src/taskbrew/dashboard/app.py` (add error handling for stale project dirs)

**Context:** Handle the edge cases from the design doc: directory deleted after registration, active_project pointing to missing entry, graceful agent shutdown timeout.

**Step 1: Add directory validation to activate_project**

In `ProjectManager.activate_project()`, before building orchestrator:

```python
    project_dir = Path(project_info["directory"])
    if not project_dir.exists():
        # Directory was deleted — remove from registry
        self.delete_project(project_id)
        raise FileNotFoundError(
            f"Project directory '{project_dir}' no longer exists. "
            f"Project '{project_id}' has been removed from the registry."
        )
    if not (project_dir / "config" / "team.yaml").exists():
        raise FileNotFoundError(
            f"Project directory '{project_dir}' is missing config/team.yaml"
        )
```

**Step 2: Add graceful shutdown timeout to deactivate**

```python
    async def deactivate_current(self) -> None:
        if self.orchestrator is None:
            return
        try:
            await asyncio.wait_for(self.orchestrator.shutdown(), timeout=5.0)
        except asyncio.TimeoutError:
            logger.warning("Orchestrator shutdown timed out after 5s, forcing")
            for task in self.orchestrator.agent_tasks:
                task.cancel()
        except Exception:
            logger.warning("Error during orchestrator shutdown", exc_info=True)
        self.orchestrator = None
        self.clear_active()
```

**Step 3: Handle stale active_project in frontend**

In the `/api/projects/{project_id}/activate` endpoint, catch `FileNotFoundError`:

```python
    @app.post("/api/projects/{project_id}/activate")
    async def activate_project(project_id: str):
        try:
            orch = await project_manager.activate_project(project_id)
        except KeyError:
            raise HTTPException(404, f"Project '{project_id}' not found")
        except FileNotFoundError as e:
            raise HTTPException(410, str(e))
        # ... rest of endpoint
```

**Step 4: Run full test suite**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: All tests PASS

**Step 5: Commit**

```bash
git add src/taskbrew/project_manager.py src/taskbrew/dashboard/app.py
git commit -m "feat: add edge case handling — missing dirs, shutdown timeout, stale refs"
```

---

## Summary

| Task | Files | What It Does |
|------|-------|-------------|
| 1 | `project_manager.py`, tests | Registry CRUD, scaffolding, slugs (18 tests) |
| 2 | `project_manager.py`, tests | activate/deactivate orchestrator lifecycle (6 tests) |
| 3 | `main.py` | Refactor startup to use ProjectManager, auto-migration |
| 4 | `app.py` | 7 new project endpoints, 409 guards, backward compat |
| 5 | `index.html` | Landing page, create wizard, project selector dropdown |
| 6 | `settings.html`, `metrics.html` | Project selector in nav, active-project guard |
| 7 | Integration tests | Auto-migration, switching, isolation tests |
| 8 | `project_manager.py`, `app.py` | Error handling: missing dirs, timeouts, stale refs |
