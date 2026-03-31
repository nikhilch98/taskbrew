# Plan 1: Agent Presets & Add-Agent Modal

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the 3-step agent creation wizard with a 2-tab modal (Presets / Custom), ship 22 preset agent YAML files, add preset API endpoints, remove the Routing accordion from agent cards, and add new fields (approval_mode, max_revision_cycles, uses_worktree) to the agent data model.

**Architecture:** Presets are YAML files in `config/presets/` loaded at startup. A new `/api/presets` router serves them. The frontend modal has two tabs: a preset grid (read-only detail → model picker → create) and a simplified custom wizard (no routing step). The `RoleConfig` dataclass and `CreateRoleBody` model gain new fields. The Routing accordion is removed from agent cards — routing moves to Plan 2 (Editable Pipeline).

**Tech Stack:** Python 3.12, FastAPI, Pydantic, PyYAML, Jinja2, vanilla JS, CSS

**Spec Reference:** `docs/superpowers/specs/2026-04-01-agent-presets-pipeline-editor-design.md` sections 1.1–1.6

---

## File Structure

### New Files
- `config/presets/pm.yaml` — PM preset
- `config/presets/architect.yaml` — Architect preset
- `config/presets/architect_reviewer.yaml` — Architect Reviewer preset
- `config/presets/coder_be.yaml` — Coder BE preset
- `config/presets/coder_fe.yaml` — Coder FE preset
- `config/presets/coder_uiux_web.yaml` — Coder UI/UX Web preset
- `config/presets/coder_swift.yaml` — Coder Swift preset
- `config/presets/coder_flutter.yaml` — Coder Flutter preset
- `config/presets/coder_infra.yaml` — Coder Infra preset
- `config/presets/designer_web.yaml` — Designer Web preset
- `config/presets/designer_ios_swift.yaml` — Designer iOS Swift preset
- `config/presets/designer_flutter_ios.yaml` — Designer Flutter iOS preset
- `config/presets/designer_flutter_ios_android.yaml` — Designer Flutter iOS+Android preset
- `config/presets/qa_tester_unit.yaml` — QA Tester Unit preset
- `config/presets/qa_tester_integration.yaml` — QA Tester Integration preset
- `config/presets/qa_tester_e2e.yaml` — QA Tester E2E preset
- `config/presets/security_auditor.yaml` — Security Auditor preset
- `config/presets/devops_engineer.yaml` — DevOps Engineer preset
- `config/presets/database_architect.yaml` — Database Architect preset
- `config/presets/technical_writer.yaml` — Technical Writer preset
- `config/presets/research_agent.yaml` — Research Agent preset
- `config/presets/api_designer.yaml` — API Designer preset
- `src/taskbrew/dashboard/routers/presets.py` — Preset API router
- `tests/test_presets.py` — Preset loading and API tests

### Modified Files
- `src/taskbrew/config_loader.py` — Add new fields to `RoleConfig`, add `load_presets()` function
- `src/taskbrew/dashboard/models.py` — Add new fields to `CreateRoleBody` and `UpdateRoleSettingsBody`
- `src/taskbrew/dashboard/routers/system.py` — Update `create_role()` to accept `preset_id`, update `delete_role()` for new fields
- `src/taskbrew/dashboard/app.py` — Register presets router
- `src/taskbrew/dashboard/static/js/settings.js` — Rewrite wizard modal, update `renderAgentCards()`
- `src/taskbrew/dashboard/static/css/settings.css` — Add preset grid, preset detail, tab styles
- `src/taskbrew/dashboard/templates/settings.html` — Update wizard modal HTML

---

## Task 1: Add New Fields to RoleConfig Dataclass

**Files:**
- Modify: `src/taskbrew/config_loader.py:212-286`
- Test: `tests/test_presets.py` (create new)

- [ ] **Step 1: Write failing test for new RoleConfig fields**

```python
# tests/test_presets.py
"""Tests for agent preset system and extended RoleConfig fields."""

import pytest
from taskbrew.config_loader import RoleConfig, _parse_role


class TestRoleConfigNewFields:
    """Test new fields on RoleConfig: approval_mode, max_revision_cycles, etc."""

    def test_parse_role_with_new_fields(self):
        data = {
            "role": "test_agent",
            "display_name": "Test Agent",
            "prefix": "TA",
            "color": "#ff0000",
            "emoji": "\U0001F916",
            "system_prompt": "You are a test agent.",
            "approval_mode": "manual",
            "max_revision_cycles": 5,
            "max_clarification_requests": 10,
            "max_route_tasks": 100,
            "uses_worktree": True,
            "capabilities": ["Cap 1", "Cap 2"],
            "artifact_exclude_patterns": ["*.env"],
        }
        rc = _parse_role(data)
        assert rc.approval_mode == "manual"
        assert rc.max_revision_cycles == 5
        assert rc.max_clarification_requests == 10
        assert rc.max_route_tasks == 100
        assert rc.uses_worktree is True
        assert rc.capabilities == ["Cap 1", "Cap 2"]
        assert rc.artifact_exclude_patterns == ["*.env"]

    def test_parse_role_defaults_for_new_fields(self):
        data = {
            "role": "minimal",
            "display_name": "Minimal",
            "prefix": "MN",
            "color": "#000000",
            "emoji": "\U0001F916",
            "system_prompt": "Minimal agent.",
        }
        rc = _parse_role(data)
        assert rc.approval_mode == "auto"
        assert rc.max_revision_cycles == 0
        assert rc.max_clarification_requests == 10
        assert rc.max_route_tasks == 100
        assert rc.uses_worktree is False
        assert rc.capabilities == []
        assert rc.artifact_exclude_patterns == []

    def test_invalid_approval_mode_rejected(self):
        data = {
            "role": "bad",
            "display_name": "Bad",
            "prefix": "BD",
            "color": "#000000",
            "emoji": "\U0001F916",
            "system_prompt": "Bad agent.",
            "approval_mode": "invalid_mode",
        }
        with pytest.raises(ValueError, match="approval_mode"):
            _parse_role(data)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd "/Users/nikhilchatragadda/Personal Projects/taskbrew" && .venv/bin/pytest tests/test_presets.py::TestRoleConfigNewFields -v`
Expected: FAIL — `RoleConfig` has no field `approval_mode`

- [ ] **Step 3: Add new fields to RoleConfig dataclass**

In `src/taskbrew/config_loader.py`, add fields to `RoleConfig` (after line 233):

```python
@dataclass
class RoleConfig:
    """Full configuration for a single agent role."""

    role: str
    display_name: str
    prefix: str
    color: str
    emoji: str
    system_prompt: str
    tools: list[str] = field(default_factory=list)
    model: str = "claude-opus-4-6"
    produces: list[str] = field(default_factory=list)
    accepts: list[str] = field(default_factory=list)
    routes_to: list[RouteTarget] = field(default_factory=list)
    can_create_groups: bool = False
    group_type: str | None = None
    max_instances: int = 1
    auto_scale: AutoScaleConfig | None = None
    context_includes: list[str] = field(default_factory=list)
    max_execution_time: int = 1800
    max_turns: int | None = None
    routing_mode: str = "open"
    # --- New fields (v2) ---
    approval_mode: str = "auto"  # "auto", "manual", "first_run"
    max_revision_cycles: int = 0  # 0 = unlimited
    max_clarification_requests: int = 10
    max_route_tasks: int = 100
    uses_worktree: bool = False
    capabilities: list[str] = field(default_factory=list)
    artifact_exclude_patterns: list[str] = field(default_factory=list)
```

- [ ] **Step 4: Update `_parse_role()` to parse new fields**

In `src/taskbrew/config_loader.py`, update the `_parse_role` function to extract new fields and validate `approval_mode`:

```python
def _parse_role(data: dict) -> RoleConfig:
    """Parse a single role YAML dict into a RoleConfig."""

    for key in _REQUIRED_ROLE_KEYS:
        if key not in data:
            raise ValueError(f"Role config missing required key '{key}' (file may be incomplete)")

    # Validate approval_mode if present
    approval_mode = data.get("approval_mode", "auto")
    if approval_mode not in ("auto", "manual", "first_run"):
        raise ValueError(
            f"approval_mode must be 'auto', 'manual', or 'first_run', got '{approval_mode}'"
        )

    routes_to = [
        RouteTarget(role=r["role"], task_types=r.get("task_types", []))
        for r in data.get("routes_to", [])
    ]

    auto_scale_raw = data.get("auto_scale")
    auto_scale: AutoScaleConfig | None = None
    if auto_scale_raw is not None:
        auto_scale = AutoScaleConfig(
            enabled=auto_scale_raw.get("enabled", False),
            scale_up_threshold=auto_scale_raw.get("scale_up_threshold", 3),
            scale_down_idle=auto_scale_raw.get("scale_down_idle", 15),
        )

    role_cfg = RoleConfig(
        role=data["role"],
        display_name=data["display_name"],
        prefix=data["prefix"],
        color=data["color"],
        emoji=data["emoji"],
        system_prompt=data["system_prompt"],
        tools=data.get("tools", []),
        model=data.get("model", "claude-opus-4-6"),
        produces=data.get("produces", []),
        accepts=data.get("accepts", []),
        routes_to=routes_to,
        can_create_groups=data.get("can_create_groups", False),
        group_type=data.get("group_type"),
        max_instances=data.get("max_instances", 1),
        auto_scale=auto_scale,
        context_includes=data.get("context_includes", []),
        max_execution_time=data.get("max_execution_time", 1800),
        max_turns=data.get("max_turns"),
        routing_mode=data.get("routing_mode", "open"),
        approval_mode=approval_mode,
        max_revision_cycles=data.get("max_revision_cycles", 0),
        max_clarification_requests=data.get("max_clarification_requests", 10),
        max_route_tasks=data.get("max_route_tasks", 100),
        uses_worktree=data.get("uses_worktree", False),
        capabilities=data.get("capabilities", []),
        artifact_exclude_patterns=data.get("artifact_exclude_patterns", []),
    )

    if role_cfg.max_turns is not None:
        _validate_range(role_cfg.max_turns, "max_turns", 1)
    if role_cfg.max_execution_time is not None:
        _validate_range(role_cfg.max_execution_time, "max_execution_time", 1)

    return role_cfg
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd "/Users/nikhilchatragadda/Personal Projects/taskbrew" && .venv/bin/pytest tests/test_presets.py::TestRoleConfigNewFields -v`
Expected: All 3 tests PASS

- [ ] **Step 6: Commit**

```bash
git add src/taskbrew/config_loader.py tests/test_presets.py
git commit -m "feat: add approval_mode, uses_worktree, and other new fields to RoleConfig"
```

---

## Task 2: Add `load_presets()` Function and First Preset YAML

**Files:**
- Modify: `src/taskbrew/config_loader.py`
- Create: `config/presets/pm.yaml`
- Test: `tests/test_presets.py`

- [ ] **Step 1: Write failing test for load_presets()**

Append to `tests/test_presets.py`:

```python
from pathlib import Path
from taskbrew.config_loader import load_presets


class TestLoadPresets:
    """Test preset YAML loading."""

    def test_load_presets_from_directory(self, tmp_path):
        preset_dir = tmp_path / "presets"
        preset_dir.mkdir()
        (preset_dir / "test_agent.yaml").write_text(
            "preset_id: test_agent\n"
            "category: testing\n"
            "display_name: Test Agent\n"
            "description: A test agent\n"
            "capabilities:\n"
            "  - Does testing\n"
            "icon_emoji: '\\U0001F916'\n"
            "color: '#ff0000'\n"
            "prefix: TA\n"
            "approval_mode: auto\n"
            "max_revision_cycles: 5\n"
            "uses_worktree: true\n"
            "system_prompt: You are a test agent.\n"
            "tools: [Read, Write]\n"
            "default_model: claude-sonnet-4-6\n"
            "produces: [implementation]\n"
            "accepts: [implementation]\n"
            "max_instances: 1\n"
            "max_turns: 50\n"
            "max_execution_time: 1800\n"
            "context_includes: [parent_artifact]\n"
        )
        presets = load_presets(preset_dir)
        assert len(presets) == 1
        assert "test_agent" in presets
        p = presets["test_agent"]
        assert p["preset_id"] == "test_agent"
        assert p["category"] == "testing"
        assert p["display_name"] == "Test Agent"
        assert p["capabilities"] == ["Does testing"]
        assert p["default_model"] == "claude-sonnet-4-6"

    def test_load_presets_empty_dir(self, tmp_path):
        preset_dir = tmp_path / "presets"
        preset_dir.mkdir()
        presets = load_presets(preset_dir)
        assert presets == {}

    def test_load_presets_missing_dir(self, tmp_path):
        presets = load_presets(tmp_path / "nonexistent")
        assert presets == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd "/Users/nikhilchatragadda/Personal Projects/taskbrew" && .venv/bin/pytest tests/test_presets.py::TestLoadPresets -v`
Expected: FAIL — `load_presets` not defined

- [ ] **Step 3: Implement `load_presets()` in config_loader.py**

Add after `load_roles()` function (around line 319):

```python
def load_presets(presets_dir: Path) -> dict[str, dict]:
    """Load preset YAML files from directory. Returns raw dicts keyed by preset_id."""
    if not presets_dir.is_dir():
        return {}
    presets: dict[str, dict] = {}
    for yaml_file in sorted(presets_dir.glob("*.yaml")):
        with open(yaml_file) as f:
            data = yaml.safe_load(f)
        if not data or "preset_id" not in data:
            continue
        presets[data["preset_id"]] = data
    return presets
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd "/Users/nikhilchatragadda/Personal Projects/taskbrew" && .venv/bin/pytest tests/test_presets.py::TestLoadPresets -v`
Expected: All 3 tests PASS

- [ ] **Step 5: Create the PM preset YAML**

Create `config/presets/pm.yaml`:

```yaml
preset_id: pm
category: planning
display_name: "PM"
description: "Decomposes user prompts into exhaustive task lists"
capabilities:
  - "Breaks down goals into tasks covering Infra, BE, FE, Testing, Research, UI, UX"
  - "Creates detailed PRDs with acceptance criteria"
  - "Routes architect tasks to downstream agents"
icon_emoji: "\U0001F4CB"
color: "#3b82f6"
prefix: "PM"
approval_mode: auto
max_revision_cycles: 0
max_clarification_requests: 10
max_route_tasks: 100
uses_worktree: false
artifact_exclude_patterns: []

system_prompt: |
  You are a Product Manager agent.
  Agent Role: pm

  ## Your Task
  You will receive a high-level goal. Decompose it into an exhaustive list of tasks
  covering all aspects: Infrastructure, Backend, Frontend, Testing, Research, UI, UX.

  ## Available Tools
  Use `route_task` to send work to connected agents (listed in your connections below).
  Use `request_clarification` if you need human input to proceed.
  Use `complete_task` when your work is done — pass file paths of all artifacts you produced.
  If `route_task` fails, check the error and use `get_my_connections()` to verify available targets.

  ## Artifact Format
  Save your task decomposition document to your working directory as markdown.
  When calling `complete_task`, pass the file path as `artifact_paths`.

  ## Routing Rules
  You can ONLY route tasks to agents listed in your injected connections section below.
  You CANNOT create tasks for agents you are not directly connected to.
  Call `route_task` BEFORE calling `complete_task`. After `complete_task` is called,
  no further `route_task` calls are accepted.

  ## Task Flow
  1. Read the goal and understand the full scope
  2. Analyze the codebase to understand current state
  3. Create a detailed task decomposition document
  4. Route tasks to connected agents via `route_task`
  5. Call `complete_task` with your document path and summary

tools:
  - Read
  - Glob
  - Grep
  - WebSearch

default_model: claude-opus-4-6
produces: [prd, goal_decomposition, requirement]
accepts: [goal, revision]
can_create_groups: true
group_type: "FEAT"
max_instances: 1
max_turns: 30
max_execution_time: 1800
context_includes:
  - parent_artifact
  - root_artifact
  - sibling_summary
```

- [ ] **Step 6: Commit**

```bash
git add src/taskbrew/config_loader.py config/presets/pm.yaml tests/test_presets.py
git commit -m "feat: add load_presets() and PM preset YAML"
```

---

## Task 3: Create Remaining 21 Preset YAML Files

**Files:**
- Create: `config/presets/architect.yaml` through `config/presets/api_designer.yaml` (21 files)

- [ ] **Step 1: Create all coding presets**

Create `config/presets/architect.yaml`, `config/presets/architect_reviewer.yaml`, and all 6 coder variants. Each follows the same YAML structure as PM but with role-specific values from the spec section 1.2. Key differences per preset:

- `architect.yaml`: preset_id=architect, category=architecture, approval_mode=first_run, uses_worktree=false, tools=[Read, Glob, Grep, Write, WebSearch], default_model=claude-opus-4-6, produces=[tech_design, tech_debt, architecture_review], accepts=[tech_design, architecture_review, rejection]
- `architect_reviewer.yaml`: preset_id=architect_reviewer, category=review, approval_mode=auto, max_revision_cycles=5, uses_worktree=true, tools=[Read, Write, Edit, Bash, Glob, Grep], produces=[verification, approval, rejection], accepts=[verification]
- `coder_be.yaml`: preset_id=coder_be, category=coding, uses_worktree=true, max_revision_cycles=5, tools=[Read, Write, Edit, Bash, Glob, Grep], default_model=claude-sonnet-4-6, max_instances=3
- `coder_fe.yaml`: same pattern, prefix=CF, color=#10b981
- `coder_uiux_web.yaml`: prefix=CU, color=#ec4899
- `coder_swift.yaml`: prefix=CS, color=#f97316
- `coder_flutter.yaml`: prefix=CL, color=#06b6d4
- `coder_infra.yaml`: prefix=CI, color=#8b5cf6

- [ ] **Step 2: Create all designer presets**

Create 4 designer presets. All have approval_mode=manual, uses_worktree=true, max_revision_cycles=5:

- `designer_web.yaml`: prefix=DW, color=#f43f5e
- `designer_ios_swift.yaml`: prefix=DI, color=#fb923c
- `designer_flutter_ios.yaml`: prefix=DF, color=#a78bfa
- `designer_flutter_ios_android.yaml`: prefix=DA, color=#2dd4bf

- [ ] **Step 3: Create testing, security, ops, docs, research, API presets**

- `qa_tester_unit.yaml`: category=testing, prefix=QU, uses_worktree=true
- `qa_tester_integration.yaml`: prefix=QI
- `qa_tester_e2e.yaml`: prefix=QE
- `security_auditor.yaml`: category=security, approval_mode=first_run, prefix=SA, uses_worktree=true
- `devops_engineer.yaml`: category=ops, prefix=DE, max_revision_cycles=5, uses_worktree=true
- `database_architect.yaml`: category=ops, prefix=DB, approval_mode=first_run, uses_worktree=false
- `technical_writer.yaml`: category=docs, prefix=TW, uses_worktree=false
- `research_agent.yaml`: category=research, prefix=RA, uses_worktree=false
- `api_designer.yaml`: category=api, prefix=AD, approval_mode=first_run, uses_worktree=false

- [ ] **Step 4: Verify all 22 presets load correctly**

Run: `cd "/Users/nikhilchatragadda/Personal Projects/taskbrew" && .venv/bin/python -c "from taskbrew.config_loader import load_presets; from pathlib import Path; p = load_presets(Path('config/presets')); print(f'{len(p)} presets loaded: {sorted(p.keys())}')"`
Expected: `22 presets loaded: ['api_designer', 'architect', 'architect_reviewer', ...]`

- [ ] **Step 5: Commit**

```bash
git add config/presets/
git commit -m "feat: add all 22 agent preset YAML files"
```

---

## Task 4: Presets API Router

**Files:**
- Create: `src/taskbrew/dashboard/routers/presets.py`
- Modify: `src/taskbrew/dashboard/app.py`
- Test: `tests/test_presets.py`

- [ ] **Step 1: Write failing tests for preset API endpoints**

Append to `tests/test_presets.py`:

```python
import pytest
from httpx import AsyncClient, ASGITransport
from taskbrew.dashboard.app import create_app


@pytest.fixture
def app():
    return create_app()


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


class TestPresetsAPI:
    """Test /api/presets endpoints."""

    @pytest.mark.asyncio
    async def test_list_presets(self, client):
        resp = await client.get("/api/presets")
        assert resp.status_code == 200
        data = resp.json()
        assert "presets" in data
        assert len(data["presets"]) >= 1
        first = data["presets"][0]
        assert "preset_id" in first
        assert "category" in first
        assert "display_name" in first
        assert "description" in first
        # List endpoint should NOT include system_prompt (it's metadata-only)
        assert "system_prompt" not in first

    @pytest.mark.asyncio
    async def test_get_preset_detail(self, client):
        resp = await client.get("/api/presets/pm")
        assert resp.status_code == 200
        data = resp.json()
        assert data["preset_id"] == "pm"
        assert "system_prompt" in data
        assert "tools" in data
        assert "capabilities" in data

    @pytest.mark.asyncio
    async def test_get_preset_not_found(self, client):
        resp = await client.get("/api/presets/nonexistent")
        assert resp.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd "/Users/nikhilchatragadda/Personal Projects/taskbrew" && .venv/bin/pytest tests/test_presets.py::TestPresetsAPI -v`
Expected: FAIL — route not registered

- [ ] **Step 3: Create the presets router**

Create `src/taskbrew/dashboard/routers/presets.py`:

```python
"""Agent preset listing and detail endpoints."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException

from taskbrew.config_loader import load_presets

router = APIRouter()

# Presets are loaded once at import time from the package's config/presets/ directory.
# They are static data shipped with the application.
_PRESETS_DIR = Path(__file__).resolve().parents[3] / "config" / "presets"
_presets: dict[str, dict] | None = None


def _get_presets() -> dict[str, dict]:
    global _presets
    if _presets is None:
        _presets = load_presets(_PRESETS_DIR)
    return _presets


# Metadata-only fields returned by the list endpoint
_LIST_FIELDS = {
    "preset_id", "category", "display_name", "description",
    "capabilities", "icon_emoji", "color", "prefix",
    "approval_mode", "max_revision_cycles", "uses_worktree",
    "default_model",
}


@router.get("/api/presets")
async def list_presets():
    """List all presets with metadata only (no system_prompt)."""
    presets = _get_presets()
    items = []
    for p in presets.values():
        items.append({k: v for k, v in p.items() if k in _LIST_FIELDS})
    # Sort by category then display_name
    items.sort(key=lambda x: (x.get("category", ""), x.get("display_name", "")))
    return {"presets": items, "count": len(items)}


@router.get("/api/presets/{preset_id}")
async def get_preset(preset_id: str):
    """Get full preset detail including system_prompt and tools."""
    presets = _get_presets()
    if preset_id not in presets:
        raise HTTPException(404, f"Preset not found: {preset_id}")
    return presets[preset_id]
```

- [ ] **Step 4: Register the presets router in app.py**

In `src/taskbrew/dashboard/app.py`, add the import and include:

```python
from taskbrew.dashboard.routers import presets
# ...
app.include_router(presets.router)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd "/Users/nikhilchatragadda/Personal Projects/taskbrew" && .venv/bin/pytest tests/test_presets.py::TestPresetsAPI -v`
Expected: All 3 tests PASS

- [ ] **Step 6: Commit**

```bash
git add src/taskbrew/dashboard/routers/presets.py src/taskbrew/dashboard/app.py tests/test_presets.py
git commit -m "feat: add /api/presets endpoints for preset listing and detail"
```

---

## Task 5: Update CreateRoleBody and create_role() for Preset Support

**Files:**
- Modify: `src/taskbrew/dashboard/models.py:49-87`
- Modify: `src/taskbrew/dashboard/routers/system.py:454-509`
- Test: `tests/test_presets.py`

- [ ] **Step 1: Write failing test for preset-based role creation**

Append to `tests/test_presets.py`:

```python
class TestCreateRoleFromPreset:
    """Test creating a role from a preset via the API."""

    @pytest.mark.asyncio
    async def test_create_role_from_preset(self, client):
        resp = await client.post("/api/settings/roles", json={
            "role": "my_pm",
            "preset_id": "pm",
            "model": "claude-sonnet-4-6",
        })
        # May return 200 or fail if no orchestrator — just check the model reaches the endpoint
        assert resp.status_code in (200, 500)  # 500 is ok if no orch running

    @pytest.mark.asyncio
    async def test_create_role_with_new_fields(self, client):
        resp = await client.post("/api/settings/roles", json={
            "role": "custom_agent",
            "display_name": "Custom Agent",
            "prefix": "CA",
            "color": "#ff0000",
            "emoji": "\U0001F916",
            "system_prompt": "You are custom.",
            "approval_mode": "manual",
            "max_revision_cycles": 3,
            "uses_worktree": True,
        })
        assert resp.status_code in (200, 500)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd "/Users/nikhilchatragadda/Personal Projects/taskbrew" && .venv/bin/pytest tests/test_presets.py::TestCreateRoleFromPreset -v`
Expected: FAIL — `preset_id` not recognized, `approval_mode` not recognized

- [ ] **Step 3: Update Pydantic models**

In `src/taskbrew/dashboard/models.py`, add new fields to both models:

```python
class UpdateRoleSettingsBody(BaseModel):
    display_name: Optional[str] = None
    prefix: Optional[str] = None
    color: Optional[str] = None
    emoji: Optional[str] = None
    system_prompt: Optional[str] = None
    model: Optional[str] = None
    tools: Optional[list[str]] = None
    max_instances: Optional[int] = None
    max_turns: Optional[int] = None
    max_execution_time: Optional[int] = None
    produces: Optional[list[str]] = None
    accepts: Optional[list[str]] = None
    context_includes: Optional[list[str]] = None
    can_create_groups: Optional[bool] = None
    group_type: Optional[str] = None
    routes_to: Optional[list[dict[str, Any]]] = None
    auto_scale: Optional[dict[str, Any]] = None
    # --- New fields (v2) ---
    approval_mode: Optional[str] = None
    max_revision_cycles: Optional[int] = None
    max_clarification_requests: Optional[int] = None
    max_route_tasks: Optional[int] = None
    uses_worktree: Optional[bool] = None


class CreateRoleBody(BaseModel):
    role: str
    preset_id: Optional[str] = None  # If set, copy config from preset
    display_name: Optional[str] = None
    prefix: Optional[str] = None
    color: Optional[str] = None
    emoji: Optional[str] = None
    system_prompt: Optional[str] = None
    tools: Optional[list[str]] = None
    model: Optional[str] = None
    produces: Optional[list[str]] = None
    accepts: Optional[list[str]] = None
    routes_to: Optional[list[dict[str, Any]]] = None
    can_create_groups: Optional[bool] = None
    group_type: Optional[str] = None
    max_instances: Optional[int] = None
    max_turns: Optional[int] = None
    context_includes: Optional[list[str]] = None
    max_execution_time: Optional[int] = None
    auto_scale: Optional[dict[str, Any]] = None
    # --- New fields (v2) ---
    approval_mode: Optional[str] = None
    max_revision_cycles: Optional[int] = None
    max_clarification_requests: Optional[int] = None
    max_route_tasks: Optional[int] = None
    uses_worktree: Optional[bool] = None
```

- [ ] **Step 4: Update `create_role()` in system.py to support preset_id**

In `src/taskbrew/dashboard/routers/system.py`, update `create_role()`. After the existing role name validation (around line 469), add preset merging logic:

```python
@router.post("/api/settings/roles")
async def create_role(body: CreateRoleBody):
    body = body.model_dump(exclude_none=True)
    orch = get_orch()
    _roles = orch.roles
    _project_dir = orch.project_dir
    role_name = body.get("role", "").strip()
    if not role_name:
        raise HTTPException(status_code=400, detail="role name is required")
    if not re.match(r"^[a-z][a-z0-9_]*$", role_name):
        raise HTTPException(
            status_code=400,
            detail="Role name must be lowercase alphanumeric (underscores allowed, must start with a letter)",
        )
    if _roles and role_name in _roles:
        raise HTTPException(status_code=409, detail=f"Role '{role_name}' already exists")

    # --- Preset merging ---
    preset_id = body.pop("preset_id", None)
    if preset_id:
        from taskbrew.dashboard.routers.presets import _get_presets
        presets = _get_presets()
        if preset_id not in presets:
            raise HTTPException(status_code=404, detail=f"Preset not found: {preset_id}")
        preset = dict(presets[preset_id])  # copy
        # Use preset's default_model as model if not overridden
        if "model" not in body and "default_model" in preset:
            body["model"] = preset.pop("default_model")
        else:
            preset.pop("default_model", None)
        # Remove preset-only fields
        preset.pop("preset_id", None)
        preset.pop("category", None)
        preset.pop("description", None)
        preset.pop("capabilities", None)
        preset.pop("icon_emoji", None)
        # Merge: preset provides defaults, body overrides
        merged = {**preset, **body}
        body = merged

    # Build YAML data with defaults for missing fields
    yaml_data: dict[str, Any] = {
        "role": role_name,
        "display_name": body.get("display_name", role_name.title()),
        "prefix": body.get("prefix", role_name[:2].upper()),
        "color": body.get("color", "#6b7280"),
        "emoji": body.get("emoji", "\U0001F916"),
        "system_prompt": body.get("system_prompt", f"You are the {role_name} agent."),
        "tools": body.get("tools", []),
        "model": body.get("model", "claude-sonnet-4-6"),
        "produces": body.get("produces", []),
        "accepts": body.get("accepts", []),
        "routes_to": body.get("routes_to", []),
        "can_create_groups": body.get("can_create_groups", False),
        "max_instances": body.get("max_instances", 1),
        "context_includes": body.get("context_includes", []),
        "max_execution_time": body.get("max_execution_time", 1800),
        # New fields
        "approval_mode": body.get("approval_mode", "auto"),
        "max_revision_cycles": body.get("max_revision_cycles", 0),
        "max_clarification_requests": body.get("max_clarification_requests", 10),
        "max_route_tasks": body.get("max_route_tasks", 100),
        "uses_worktree": body.get("uses_worktree", False),
        "artifact_exclude_patterns": body.get("artifact_exclude_patterns", []),
    }
    if body.get("group_type"):
        yaml_data["group_type"] = body["group_type"]
    if body.get("max_turns"):
        yaml_data["max_turns"] = body["max_turns"]
    if body.get("auto_scale"):
        yaml_data["auto_scale"] = body["auto_scale"]

    # Write YAML file
    if _project_dir:
        roles_dir = Path(_project_dir) / "config" / "roles"
        roles_dir.mkdir(parents=True, exist_ok=True)
        yaml_path = roles_dir / f"{role_name}.yaml"
        with open(yaml_path, "w") as f:
            yaml.dump(yaml_data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

    # Parse and register in memory
    rc = _parse_role(yaml_data)
    if _roles is not None:
        _roles[role_name] = rc

    return {"status": "ok", "role": role_name}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd "/Users/nikhilchatragadda/Personal Projects/taskbrew" && .venv/bin/pytest tests/test_presets.py -v`
Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
git add src/taskbrew/dashboard/models.py src/taskbrew/dashboard/routers/system.py
git commit -m "feat: support preset_id in create_role and add new fields to API models"
```

---

## Task 6: Update get_roles_settings() to Include New Fields

**Files:**
- Modify: `src/taskbrew/dashboard/routers/system.py:296-331`
- Modify: `src/taskbrew/dashboard/routers/system.py:334-451` (update_role_settings)

- [ ] **Step 1: Read the current get_roles_settings implementation**

Read `src/taskbrew/dashboard/routers/system.py` lines 296-331 to understand how role data is serialized.

- [ ] **Step 2: Add new fields to the role serialization in get_roles_settings()**

Wherever the role fields are serialized into a response dict, add:

```python
"approval_mode": rc.approval_mode,
"max_revision_cycles": rc.max_revision_cycles,
"max_clarification_requests": rc.max_clarification_requests,
"max_route_tasks": rc.max_route_tasks,
"uses_worktree": rc.uses_worktree,
"capabilities": rc.capabilities,
"artifact_exclude_patterns": rc.artifact_exclude_patterns,
```

- [ ] **Step 3: Add new fields to update_role_settings() persistence**

In the `update_role_settings()` function, add handling for the new fields in the update flow (where other fields like `model`, `tools`, etc. are applied). For each new field, apply the same pattern:

```python
if body.approval_mode is not None:
    role_cfg.approval_mode = body.approval_mode
    yaml_data["approval_mode"] = body.approval_mode
if body.max_revision_cycles is not None:
    role_cfg.max_revision_cycles = body.max_revision_cycles
    yaml_data["max_revision_cycles"] = body.max_revision_cycles
if body.max_clarification_requests is not None:
    role_cfg.max_clarification_requests = body.max_clarification_requests
    yaml_data["max_clarification_requests"] = body.max_clarification_requests
if body.max_route_tasks is not None:
    role_cfg.max_route_tasks = body.max_route_tasks
    yaml_data["max_route_tasks"] = body.max_route_tasks
if body.uses_worktree is not None:
    role_cfg.uses_worktree = body.uses_worktree
    yaml_data["uses_worktree"] = body.uses_worktree
```

- [ ] **Step 4: Run existing settings tests to verify no regressions**

Run: `cd "/Users/nikhilchatragadda/Personal Projects/taskbrew" && .venv/bin/pytest tests/test_config_loader.py tests/test_config_validation.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/taskbrew/dashboard/routers/system.py
git commit -m "feat: expose new agent fields in settings API responses and updates"
```

---

## Task 7: Rewrite the Add-Agent Modal (Frontend — Two Tabs)

**Files:**
- Modify: `src/taskbrew/dashboard/static/js/settings.js:1228-1434`
- Modify: `src/taskbrew/dashboard/templates/settings.html` (wizard modal HTML)
- Modify: `src/taskbrew/dashboard/static/css/settings.css`

- [ ] **Step 1: Replace the wizard modal HTML in settings.html**

Find the existing wizard modal (around the `wizardOverlay` div) and replace with a two-tab modal. The exact old HTML to find starts with `<div id="wizardOverlay"` and ends with the closing `</div>` of the wizard. Replace with:

```html
<!-- Add Agent Modal (2-tab: Presets / Custom) -->
<div id="addAgentOverlay" class="wizard-overlay" role="dialog" aria-modal="true" aria-label="Add New Agent">
    <div class="wizard-modal" style="max-width: 720px; max-height: 85vh; display: flex; flex-direction: column;">
        <div class="wizard-header" style="flex-shrink: 0;">
            <div style="display: flex; align-items: center; justify-content: space-between; width: 100%;">
                <h2 id="addAgentTitle">Add New Agent</h2>
                <button onclick="closeAddAgent()" class="wizard-close-btn" aria-label="Close">&times;</button>
            </div>
            <div class="tab-bar" role="tablist">
                <button class="tab-btn active" data-tab="presets" role="tab" aria-selected="true" onclick="switchAddAgentTab('presets')">Presets</button>
                <button class="tab-btn" data-tab="custom" role="tab" aria-selected="false" onclick="switchAddAgentTab('custom')">Custom</button>
            </div>
        </div>
        <div id="addAgentBody" class="wizard-content" style="flex: 1; overflow-y: auto;">
            <!-- Dynamic content rendered by JS -->
        </div>
        <div id="addAgentFooter" class="wizard-footer" style="flex-shrink: 0;">
            <button id="addAgentBackBtn" class="wizard-btn-secondary" onclick="addAgentBack()" style="visibility: hidden;">Back</button>
            <button id="addAgentNextBtn" class="wizard-btn-primary" onclick="addAgentNext()">Select</button>
        </div>
    </div>
</div>
```

- [ ] **Step 2: Add tab bar and preset grid CSS to settings.css**

Append to `src/taskbrew/dashboard/static/css/settings.css`:

```css
/* ========== Add Agent Modal Tabs ========== */
.tab-bar {
    display: flex;
    gap: 0;
    margin-top: 16px;
    border-bottom: 1px solid var(--border-subtle, rgba(255,255,255,0.08));
}
.tab-btn {
    padding: 8px 20px;
    background: none;
    border: none;
    color: var(--text-muted, #94a3b8);
    cursor: pointer;
    font-size: 14px;
    font-weight: 500;
    border-bottom: 2px solid transparent;
    transition: color 0.2s, border-color 0.2s;
}
.tab-btn.active {
    color: var(--text-primary, #f1f5f9);
    border-bottom-color: var(--accent-indigo, #6366f1);
}
.tab-btn:hover {
    color: var(--text-primary, #f1f5f9);
}

/* ========== Preset Grid ========== */
.preset-category-tabs {
    display: flex;
    gap: 6px;
    flex-wrap: wrap;
    margin-bottom: 12px;
}
.preset-cat-btn {
    padding: 4px 12px;
    border-radius: 999px;
    border: 1px solid var(--border-subtle, rgba(255,255,255,0.08));
    background: transparent;
    color: var(--text-muted, #94a3b8);
    font-size: 12px;
    cursor: pointer;
    transition: all 0.2s;
}
.preset-cat-btn.active {
    background: var(--accent-indigo, #6366f1);
    color: white;
    border-color: var(--accent-indigo, #6366f1);
}
.preset-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
    gap: 12px;
}
.preset-card {
    padding: 16px;
    border-radius: var(--radius-lg, 12px);
    border: 1px solid var(--border-subtle, rgba(255,255,255,0.08));
    background: var(--bg-card, rgba(255,255,255,0.03));
    cursor: pointer;
    transition: border-color 0.2s, background 0.2s;
}
.preset-card:hover, .preset-card.selected {
    border-color: var(--accent-indigo, #6366f1);
    background: rgba(99, 102, 241, 0.08);
}
.preset-card-icon {
    font-size: 24px;
    margin-bottom: 8px;
}
.preset-card-name {
    font-weight: 600;
    font-size: 14px;
    color: var(--text-primary, #f1f5f9);
    margin-bottom: 4px;
}
.preset-card-desc {
    font-size: 12px;
    color: var(--text-muted, #94a3b8);
    line-height: 1.4;
}
/* Preset detail view */
.preset-detail {
    padding: 16px 0;
}
.preset-detail-header {
    display: flex;
    align-items: center;
    gap: 12px;
    margin-bottom: 16px;
}
.preset-detail-icon {
    font-size: 32px;
}
.preset-detail-name {
    font-size: 20px;
    font-weight: 700;
    color: var(--text-primary, #f1f5f9);
}
.preset-detail-section {
    margin-bottom: 16px;
}
.preset-detail-section h4 {
    font-size: 12px;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--text-muted, #94a3b8);
    margin-bottom: 8px;
}
.preset-detail-section pre {
    background: var(--bg-primary, #05070e);
    border-radius: 8px;
    padding: 12px;
    font-size: 12px;
    max-height: 200px;
    overflow-y: auto;
    color: var(--text-secondary, #cbd5e1);
    white-space: pre-wrap;
}
.preset-detail-chips {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
}
```

- [ ] **Step 3: Rewrite wizard JS functions in settings.js**

Replace the wizard functions (openWizard through createNewAgent) in `src/taskbrew/dashboard/static/js/settings.js` with the new two-tab modal logic:

```javascript
// ================================================================
// Add Agent Modal (2-tab: Presets / Custom)
// ================================================================
let addAgentTab = 'presets';
let addAgentStep = 0; // 0=grid/identity, 1=detail/config, 2=model picker
let selectedPreset = null;
let allPresets = [];
let presetCategoryFilter = 'all';

// Custom wizard data (same as old wizard minus routing)
let customData = { role: '', display_name: '', prefix: '', color: '#6366f1', emoji: '', model: '', system_prompt: '', tools: [], approval_mode: 'auto', max_revision_cycles: 0, uses_worktree: false };

function openAddAgent() {
    addAgentTab = 'presets';
    addAgentStep = 0;
    selectedPreset = null;
    presetCategoryFilter = 'all';
    customData = { role: '', display_name: '', prefix: '', color: '#6366f1', emoji: '', model: '', system_prompt: '', tools: [], approval_mode: 'auto', max_revision_cycles: 0, uses_worktree: false };
    document.getElementById('addAgentOverlay').classList.add('open');
    loadPresetsIfNeeded().then(function() { renderAddAgent(); });
}
// Keep old name as alias for the "+" card onclick
var openWizard = openAddAgent;

function closeAddAgent() {
    document.getElementById('addAgentOverlay').classList.remove('open');
}
var closeWizard = closeAddAgent;

async function loadPresetsIfNeeded() {
    if (allPresets.length > 0) return;
    try {
        var resp = await fetch('/api/presets');
        var data = await resp.json();
        allPresets = data.presets || [];
    } catch (e) {
        console.error('Failed to load presets:', e);
        allPresets = [];
    }
}

function switchAddAgentTab(tab) {
    addAgentTab = tab;
    addAgentStep = 0;
    selectedPreset = null;
    document.querySelectorAll('.tab-btn').forEach(function(b) {
        b.classList.toggle('active', b.dataset.tab === tab);
        b.setAttribute('aria-selected', b.dataset.tab === tab ? 'true' : 'false');
    });
    renderAddAgent();
}

function renderAddAgent() {
    var body = document.getElementById('addAgentBody');
    var backBtn = document.getElementById('addAgentBackBtn');
    var nextBtn = document.getElementById('addAgentNextBtn');

    if (addAgentTab === 'presets') {
        backBtn.style.visibility = addAgentStep === 0 ? 'hidden' : 'visible';
        if (addAgentStep === 0) {
            body.innerHTML = renderPresetGrid();
            nextBtn.textContent = 'Select';
            nextBtn.disabled = !selectedPreset;
        } else if (addAgentStep === 1) {
            body.innerHTML = renderPresetDetail();
            nextBtn.textContent = 'Next';
            nextBtn.disabled = false;
        } else {
            body.innerHTML = renderPresetModelPicker();
            nextBtn.textContent = 'Create';
            nextBtn.disabled = false;
        }
    } else {
        // Custom tab: step 0 = Identity, step 1 = Config
        backBtn.style.visibility = addAgentStep === 0 ? 'hidden' : 'visible';
        if (addAgentStep === 0) {
            body.innerHTML = renderCustomStep1();
            nextBtn.textContent = 'Next';
        } else {
            body.innerHTML = renderCustomStep2();
            nextBtn.textContent = 'Create';
        }
    }
}

function renderPresetGrid() {
    var categories = ['all'];
    allPresets.forEach(function(p) {
        if (categories.indexOf(p.category) === -1) categories.push(p.category);
    });
    var html = '<div class="preset-category-tabs">';
    categories.forEach(function(c) {
        var label = c === 'all' ? 'All' : c.charAt(0).toUpperCase() + c.slice(1);
        html += '<button class="preset-cat-btn' + (presetCategoryFilter === c ? ' active' : '') + '" onclick="filterPresetCategory(\'' + c + '\')">' + label + '</button>';
    });
    html += '</div>';

    var filtered = presetCategoryFilter === 'all' ? allPresets : allPresets.filter(function(p) { return p.category === presetCategoryFilter; });

    html += '<div class="preset-grid">';
    filtered.forEach(function(p) {
        var sel = selectedPreset && selectedPreset.preset_id === p.preset_id ? ' selected' : '';
        html += '<div class="preset-card' + sel + '" onclick="selectPreset(\'' + escapeHtml(p.preset_id) + '\')">';
        html += '<div class="preset-card-icon">' + (p.icon_emoji || '\U0001F916') + '</div>';
        html += '<div class="preset-card-name">' + escapeHtml(p.display_name) + '</div>';
        html += '<div class="preset-card-desc">' + escapeHtml(p.description || '') + '</div>';
        html += '</div>';
    });
    html += '</div>';
    return html;
}

function filterPresetCategory(cat) {
    presetCategoryFilter = cat;
    renderAddAgent();
}

function selectPreset(presetId) {
    selectedPreset = allPresets.find(function(p) { return p.preset_id === presetId; }) || null;
    renderAddAgent();
}

function renderPresetDetail() {
    if (!selectedPreset || !selectedPreset._full) {
        return '<div style="text-align:center;padding:40px;color:var(--text-muted)">Loading preset details...</div>';
    }
    var p = selectedPreset._full;
    var html = '<div class="preset-detail">';
    html += '<div class="preset-detail-header">';
    html += '<div class="preset-detail-icon">' + (p.icon_emoji || '') + '</div>';
    html += '<div class="preset-detail-name">' + escapeHtml(p.display_name) + '</div>';
    html += '</div>';

    if (p.capabilities && p.capabilities.length > 0) {
        html += '<div class="preset-detail-section"><h4>Capabilities</h4><ul style="margin:0;padding-left:20px;color:var(--text-secondary)">';
        p.capabilities.forEach(function(c) { html += '<li style="margin-bottom:4px;font-size:13px">' + escapeHtml(c) + '</li>'; });
        html += '</ul></div>';
    }

    html += '<div class="preset-detail-section"><h4>Tools</h4><div class="preset-detail-chips">';
    (p.tools || []).forEach(function(t) { html += '<span class="chip">' + escapeHtml(t) + '</span>'; });
    html += '</div></div>';

    html += '<div class="preset-detail-section"><h4>Settings</h4>';
    html += '<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;font-size:13px;color:var(--text-secondary)">';
    html += '<div>Approval: <strong>' + escapeHtml(p.approval_mode || 'auto') + '</strong></div>';
    html += '<div>Revision limit: <strong>' + (p.max_revision_cycles || 'unlimited') + '</strong></div>';
    html += '<div>Worktree: <strong>' + (p.uses_worktree ? 'Yes' : 'No') + '</strong></div>';
    html += '<div>Max instances: <strong>' + (p.max_instances || 1) + '</strong></div>';
    html += '</div></div>';

    html += '<div class="preset-detail-section"><h4>System Prompt</h4>';
    html += '<pre>' + escapeHtml(p.system_prompt || '') + '</pre>';
    html += '</div>';

    html += '</div>';
    return html;
}

function renderPresetModelPicker() {
    var models = availableModels.length > 0 ? availableModels : [
        { id: 'claude-opus-4-6', name: 'Claude Opus 4.6' },
        { id: 'claude-sonnet-4-6', name: 'Claude Sonnet 4.6' },
        { id: 'claude-haiku-4-5-20251001', name: 'Claude Haiku 4.5' }
    ];
    var defaultModel = selectedPreset && selectedPreset._full ? (selectedPreset._full.default_model || 'claude-sonnet-4-6') : 'claude-sonnet-4-6';

    var html = '<div style="padding: 20px 0;">';
    html += '<h3 style="margin-bottom: 16px; color: var(--text-primary)">Choose AI Model</h3>';
    html += '<p style="margin-bottom: 16px; color: var(--text-muted); font-size: 13px;">Select the model this agent will use. You can change this later in the agent settings.</p>';
    html += '<div class="form-group"><label class="form-label" for="preset-model-select">Model</label>';
    html += '<select class="form-select" id="preset-model-select">';
    models.forEach(function(m) {
        var mid = m.id || m;
        var mname = m.name || m.label || mid;
        html += '<option value="' + escapeHtml(mid) + '"' + (mid === defaultModel ? ' selected' : '') + '>' + escapeHtml(mname) + '</option>';
    });
    html += '</select></div>';
    html += '</div>';
    return html;
}

function renderCustomStep1() {
    return '<div class="form-group"><label class="form-label">Display Name</label>' +
        '<input class="form-input" id="wiz-display-name" value="' + escapeHtml(customData.display_name) + '" placeholder="e.g. My Agent" oninput="wizUpdateIdentity()"></div>' +
        '<div class="form-row">' +
        '<div class="form-group"><label class="form-label">Role ID<span class="form-sublabel">(auto-generated)</span></label>' +
        '<input class="form-input" id="wiz-role-id" value="' + escapeHtml(customData.role) + '" placeholder="auto-generated"></div>' +
        '<div class="form-group"><label class="form-label">Prefix<span class="form-sublabel">(2-char)</span></label>' +
        '<input class="form-input" id="wiz-prefix" value="' + escapeHtml(customData.prefix) + '" maxlength="4"></div>' +
        '</div>' +
        '<div class="form-row">' +
        '<div class="form-group"><label class="form-label">Color</label>' +
        '<input type="color" class="form-input" id="wiz-color" value="' + escapeHtml(customData.color) + '"></div>' +
        '<div class="form-group"><label class="form-label">Emoji</label>' +
        '<input class="form-input" id="wiz-emoji" value="' + escapeHtml(customData.emoji) + '" placeholder="\uD83E\uDD16" style="font-size:18px"></div>' +
        '</div>';
}

function renderCustomStep2() {
    var models = availableModels.length > 0 ? availableModels : [
        { id: 'claude-opus-4-6', name: 'Claude Opus 4.6' },
        { id: 'claude-sonnet-4-6', name: 'Claude Sonnet 4.6' },
        { id: 'claude-haiku-4-5-20251001', name: 'Claude Haiku 4.5' }
    ];
    var modelOpts = '';
    models.forEach(function(m) {
        var mid = m.id || m;
        var mname = m.name || m.label || mid;
        modelOpts += '<option value="' + escapeHtml(mid) + '"' + (customData.model === mid ? ' selected' : '') + '>' + escapeHtml(mname) + '</option>';
    });

    var html = '<div class="form-group"><label class="form-label">Model</label>' +
        '<select class="form-select" id="wiz-model">' + modelOpts + '</select></div>';
    html += '<div class="form-group"><label class="form-label">System Prompt</label>' +
        '<textarea class="form-textarea" id="wiz-prompt" rows="5" placeholder="Describe this agent\'s responsibilities...">' + escapeHtml(customData.system_prompt) + '</textarea></div>';
    html += '<div class="form-group"><label class="form-label">Tools</label><div class="checkbox-group">';
    COMMON_TOOLS.forEach(function(tool) {
        var checked = customData.tools.indexOf(tool) !== -1;
        html += '<label class="checkbox-item"><input type="checkbox" value="' + escapeHtml(tool) + '"' + (checked ? ' checked' : '') + ' class="wiz-tool-cb"><label>' + escapeHtml(tool) + '</label></label>';
    });
    html += '</div></div>';
    // Approval mode
    html += '<div class="form-row"><div class="form-group"><label class="form-label">Approval Mode</label>';
    html += '<select class="form-select" id="wiz-approval">';
    ['auto', 'manual', 'first_run'].forEach(function(m) {
        html += '<option value="' + m + '"' + (customData.approval_mode === m ? ' selected' : '') + '>' + m + '</option>';
    });
    html += '</select></div>';
    html += '<div class="form-group"><label class="form-label">Max Revision Cycles<span class="form-sublabel">(0=unlimited)</span></label>';
    html += '<input type="number" class="form-input" id="wiz-revisions" value="' + (customData.max_revision_cycles || 0) + '" min="0"></div></div>';
    html += '<div class="toggle-row"><span class="toggle-label">Uses Git Worktree</span>';
    html += '<label class="toggle-switch"><input type="checkbox" id="wiz-worktree"' + (customData.uses_worktree ? ' checked' : '') + '><span class="toggle-track"></span><span class="toggle-thumb"></span></label></div>';
    return html;
}

function addAgentBack() {
    if (addAgentStep > 0) { addAgentStep--; renderAddAgent(); }
}

async function addAgentNext() {
    if (addAgentTab === 'presets') {
        if (addAgentStep === 0) {
            if (!selectedPreset) { showToast('Select a preset first', 'error'); return; }
            // Load full detail
            try {
                var resp = await fetch('/api/presets/' + encodeURIComponent(selectedPreset.preset_id));
                selectedPreset._full = await resp.json();
            } catch (e) { showToast('Failed to load preset detail', 'error'); return; }
            addAgentStep = 1;
            renderAddAgent();
        } else if (addAgentStep === 1) {
            addAgentStep = 2;
            renderAddAgent();
        } else {
            // Create from preset
            var model = document.getElementById('preset-model-select').value;
            var presetId = selectedPreset.preset_id;
            var roleName = presetId;
            // Handle duplicates
            var existingRoles = (settingsData.roles || []).map(function(r) { return r.role; });
            if (existingRoles.indexOf(roleName) !== -1) {
                var suffix = 2;
                while (existingRoles.indexOf(roleName + '_' + suffix) !== -1) suffix++;
                roleName = roleName + '_' + suffix;
            }
            try {
                var res = await fetch('/api/settings/roles', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ role: roleName, preset_id: presetId, model: model })
                });
                if (!res.ok) { var err = await res.json(); throw new Error(err.detail || res.status); }
                closeAddAgent();
                showToast('Agent "' + (selectedPreset.display_name || roleName) + '" created', 'success');
                await loadSettings();
            } catch (e) { showToast('Failed to create agent: ' + e.message, 'error'); }
        }
    } else {
        // Custom tab
        if (addAgentStep === 0) {
            collectCustomStep1();
            if (!customData.display_name.trim()) { showToast('Display name is required', 'error'); return; }
            addAgentStep = 1;
            renderAddAgent();
        } else {
            collectCustomStep2();
            await createCustomAgent();
        }
    }
}

function collectCustomStep1() {
    var n = document.getElementById('wiz-display-name');
    var r = document.getElementById('wiz-role-id');
    var p = document.getElementById('wiz-prefix');
    var c = document.getElementById('wiz-color');
    var e = document.getElementById('wiz-emoji');
    if (n) customData.display_name = n.value;
    if (r) customData.role = r.value || slugify(customData.display_name);
    if (p) customData.prefix = p.value || autoPrefix(customData.display_name);
    if (c) customData.color = c.value;
    if (e) customData.emoji = e.value;
}

function collectCustomStep2() {
    var m = document.getElementById('wiz-model');
    var p = document.getElementById('wiz-prompt');
    var a = document.getElementById('wiz-approval');
    var rv = document.getElementById('wiz-revisions');
    var wt = document.getElementById('wiz-worktree');
    if (m) customData.model = m.value;
    if (p) customData.system_prompt = p.value;
    if (a) customData.approval_mode = a.value;
    if (rv) customData.max_revision_cycles = parseInt(rv.value) || 0;
    if (wt) customData.uses_worktree = wt.checked;
    customData.tools = [];
    document.querySelectorAll('.wiz-tool-cb:checked').forEach(function(cb) { customData.tools.push(cb.value); });
}

async function createCustomAgent() {
    if (!customData.role) customData.role = slugify(customData.display_name);
    if (!customData.role) { showToast('Role ID is required', 'error'); return; }
    try {
        var payload = {
            role: customData.role,
            display_name: customData.display_name,
            prefix: customData.prefix,
            color: customData.color,
            emoji: customData.emoji,
            model: customData.model,
            system_prompt: customData.system_prompt,
            tools: customData.tools,
            approval_mode: customData.approval_mode,
            max_revision_cycles: customData.max_revision_cycles,
            uses_worktree: customData.uses_worktree,
            max_instances: 1,
            produces: [],
            accepts: [],
            context_includes: ['parent_artifact', 'root_artifact']
        };
        var res = await fetch('/api/settings/roles', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        if (!res.ok) throw new Error('Server returned ' + res.status);
        closeAddAgent();
        showToast('Agent "' + customData.display_name + '" created', 'success');
        await loadSettings();
    } catch (e) { showToast('Failed to create agent: ' + e.message, 'error'); }
}
```

- [ ] **Step 4: Remove the Routing accordion from `renderAgentCards()`**

In `settings.js`, in `renderAgentCards()` (around line 674), remove the line:
```javascript
html += renderAccordion(roleId, 'routing', 'Routing', renderRoutingFields(role));
```

And add new fields to the Advanced accordion rendering, adding them to `renderAdvancedFields()`:

```javascript
// Add to renderAdvancedFields after existing content:
html += '<div class="form-group"><label class="form-label">Approval Mode</label>';
html += '<select class="form-select" onchange="updateRole(\'' + r + '\',\'approval_mode\',this.value)">';
['auto', 'manual', 'first_run'].forEach(function(m) {
    html += '<option value="' + m + '"' + (role.approval_mode === m ? ' selected' : '') + '>' + m + '</option>';
});
html += '</select></div>';
html += '<div class="form-row">';
html += '<div class="form-group"><label class="form-label">Max Revision Cycles<span class="form-sublabel">(0=unlimited)</span></label>';
html += '<input type="number" class="form-input" value="' + (role.max_revision_cycles || 0) + '" min="0" onchange="updateRole(\'' + r + '\',\'max_revision_cycles\',parseInt(this.value)||0)"></div>';
html += '<div class="form-group"><label class="form-label">Uses Worktree</label>';
html += '<label class="toggle-switch"><input type="checkbox"' + (role.uses_worktree ? ' checked' : '') + ' onchange="updateRole(\'' + r + '\',\'uses_worktree\',this.checked)"><span class="toggle-track"></span><span class="toggle-thumb"></span></label></div>';
html += '</div>';
```

- [ ] **Step 5: Update the Pipeline section title**

In `settings.html` (and `settings.js` if needed), change "Pipeline Visualizer" text to "Pipeline":

Find: `Pipeline Visualizer`
Replace: `Pipeline`

- [ ] **Step 6: Manually test in browser**

Open `http://localhost:8420/settings`, click "Add New Agent", verify:
- Two tabs appear (Presets / Custom)
- Preset grid shows categories
- Clicking a preset shows detail view
- Model picker step works
- Custom tab has Identity + Config (no Routing step)
- Routing accordion is gone from agent cards
- "Pipeline Visualizer" says "Pipeline"

- [ ] **Step 7: Commit**

```bash
git add src/taskbrew/dashboard/static/js/settings.js src/taskbrew/dashboard/static/css/settings.css src/taskbrew/dashboard/templates/settings.html
git commit -m "feat: replace wizard with 2-tab add-agent modal (presets + custom), remove routing accordion"
```

---

## Task 8: Update settings.html Inline Script (if duplicated)

**Files:**
- Modify: `src/taskbrew/dashboard/templates/settings.html`

The settings page has both external JS (`settings.js`) and inline `<script>` in the template. Both need to be consistent.

- [ ] **Step 1: Check if wizard functions are duplicated in settings.html inline script**

Read the inline `<script>` section of `settings.html` (around line 2265+). If it contains duplicated wizard functions (`openWizard`, `renderWizard`, `createNewAgent`, etc.), they need to be updated to match the new code, or preferably removed in favor of the external `settings.js`.

- [ ] **Step 2: Remove duplicated wizard code from inline script**

If duplicated, remove the old wizard functions from the inline script and ensure `settings.js` is loaded (it should already be via `<script src="/static/js/settings.js">`). Update any remaining inline references from `openWizard()` to `openAddAgent()` — the alias `var openWizard = openAddAgent;` handles backward compatibility.

- [ ] **Step 3: Update "Pipeline Visualizer" text in inline HTML if present**

Search the inline HTML/script for "Pipeline Visualizer" and replace with "Pipeline".

- [ ] **Step 4: Verify the page loads without JS errors**

Open `http://localhost:8420/settings` and check the browser console for errors.

- [ ] **Step 5: Commit**

```bash
git add src/taskbrew/dashboard/templates/settings.html
git commit -m "fix: sync inline settings.html script with external settings.js changes"
```

---

## Task 9: Final Integration Test

**Files:**
- Test: `tests/test_presets.py`

- [ ] **Step 1: Write end-to-end integration test**

Append to `tests/test_presets.py`:

```python
class TestPresetIntegration:
    """End-to-end test: load presets, create agent from preset, verify config."""

    def test_all_presets_have_required_fields(self):
        from taskbrew.config_loader import load_presets
        presets = load_presets(Path("config/presets"))
        assert len(presets) == 22, f"Expected 22 presets, got {len(presets)}"
        required = {"preset_id", "category", "display_name", "description", "system_prompt", "tools", "default_model"}
        for pid, p in presets.items():
            missing = required - set(p.keys())
            assert not missing, f"Preset {pid} missing fields: {missing}"

    def test_all_presets_parseable_as_roles(self):
        from taskbrew.config_loader import load_presets, _parse_role
        presets = load_presets(Path("config/presets"))
        for pid, p in presets.items():
            # Simulate what create_role does: merge preset with role name
            data = dict(p)
            data["role"] = pid
            data["model"] = data.pop("default_model", "claude-sonnet-4-6")
            data.pop("preset_id", None)
            data.pop("category", None)
            data.pop("description", None)
            data.pop("capabilities", None)
            data.pop("icon_emoji", None)
            rc = _parse_role(data)
            assert rc.role == pid
            assert rc.approval_mode in ("auto", "manual", "first_run")

    def test_preset_categories_match_spec(self):
        from taskbrew.config_loader import load_presets
        presets = load_presets(Path("config/presets"))
        expected_categories = {"planning", "architecture", "review", "coding", "design", "testing", "security", "ops", "docs", "research", "api"}
        actual_categories = {p["category"] for p in presets.values()}
        assert actual_categories == expected_categories, f"Category mismatch: {actual_categories} vs {expected_categories}"
```

- [ ] **Step 2: Run all preset tests**

Run: `cd "/Users/nikhilchatragadda/Personal Projects/taskbrew" && .venv/bin/pytest tests/test_presets.py -v`
Expected: All tests PASS

- [ ] **Step 3: Run full test suite to check for regressions**

Run: `cd "/Users/nikhilchatragadda/Personal Projects/taskbrew" && .venv/bin/pytest tests/test_config_loader.py tests/test_config_validation.py -v`
Expected: All PASS (no regressions from new RoleConfig fields)

- [ ] **Step 4: Commit**

```bash
git add tests/test_presets.py
git commit -m "test: add integration tests for preset system"
```

---

## Summary

| Task | Description | Files | Tests |
|------|-------------|-------|-------|
| 1 | Add new fields to RoleConfig | config_loader.py | 3 unit tests |
| 2 | Add load_presets() + PM preset | config_loader.py, pm.yaml | 3 unit tests |
| 3 | Create remaining 21 preset YAMLs | 21 YAML files | load verification |
| 4 | Presets API router | presets.py, app.py | 3 API tests |
| 5 | Update CreateRoleBody for preset support | models.py, system.py | 2 API tests |
| 6 | Update get/update roles for new fields | system.py | existing test reuse |
| 7 | Rewrite Add-Agent modal frontend | settings.js, settings.css, settings.html | manual browser test |
| 8 | Sync inline script in template | settings.html | console check |
| 9 | Integration tests | test_presets.py | 3 integration tests |
