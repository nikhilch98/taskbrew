# Plan 2: Editable Pipeline

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace per-agent `routes_to` with a top-level `pipeline` section in `team.yaml`, add CRUD API endpoints for pipeline topology, and make the existing SVG pipeline visualization interactive (click-to-connect edges, remove edges, set start agent, edge/node popovers with config, validation indicators).

**Architecture:** The pipeline topology is stored as a `pipeline` key in `config/team.yaml` with `id`, `name`, `start_agent`, `edges[]`, and `node_config{}`. A new `/api/pipeline` router (`src/taskbrew/dashboard/routers/pipeline_editor.py`) provides GET/PUT/POST/DELETE endpoints. The frontend (`settings.js`) gains a `pipelineData` object loaded from `/api/pipeline` and the existing `renderPipeline()` / `computeGraphLayout()` functions are modified to read edges from `pipelineData.edges` instead of `routes_to`. Interactive behaviors (click-to-connect, hover-to-delete, context menus, popovers) are layered on top of the existing SVG rendering. An auto-migration path converts legacy `routes_to` data into pipeline edges on first load.

**Tech Stack:** Python 3.12, FastAPI, Pydantic, PyYAML, Jinja2, vanilla JS, CSS

**Spec Reference:** `docs/superpowers/specs/2026-04-01-agent-presets-pipeline-editor-design.md` sections 2.1--2.8

---

## File Structure

### New Files
- `src/taskbrew/dashboard/routers/pipeline_editor.py` -- Pipeline topology API router
- `tests/test_pipeline_editor.py` -- Pipeline editor unit and integration tests

### Modified Files
- `src/taskbrew/config_loader.py` -- Add `PipelineEdge`, `PipelineNodeConfig`, `PipelineConfig` dataclasses; add `load_pipeline()` and `save_pipeline()` functions; add `migrate_routes_to_pipeline()` auto-migration
- `src/taskbrew/dashboard/models.py` -- Add Pydantic request bodies for pipeline API
- `src/taskbrew/dashboard/app.py` -- Register `pipeline_editor` router
- `src/taskbrew/dashboard/routers/system.py` -- Update `delete_role()` to clean up pipeline edges referencing deleted role
- `src/taskbrew/dashboard/static/js/settings.js` -- Load pipeline from `/api/pipeline`, modify `renderPipeline()` and `computeGraphLayout()` to use `pipelineData`, add interactive behaviors
- `src/taskbrew/dashboard/static/css/settings.css` -- Add styles for pipeline interactivity (hover effects, selection state, popovers, dashed unconnected nodes, start agent badge, validation indicators)
- `config/team.yaml` -- (schema change only; auto-migration adds `pipeline` key at runtime)

---

## Task 1: Pipeline Data Model (config_loader.py)

**Files:**
- Modify: `src/taskbrew/config_loader.py`
- Test: `tests/test_pipeline_editor.py` (create new)

- [ ] **Step 1: Write failing tests for new pipeline dataclasses**

```python
# tests/test_pipeline_editor.py
"""Tests for editable pipeline data model, API, and migration."""

import pytest
import yaml
from pathlib import Path
from taskbrew.config_loader import (
    PipelineEdge,
    PipelineNodeConfig,
    PipelineConfig,
    load_pipeline,
    save_pipeline,
)


class TestPipelineDataModel:
    """Test PipelineEdge, PipelineNodeConfig, PipelineConfig dataclasses."""

    def test_pipeline_edge_defaults(self):
        edge = PipelineEdge(id="e1", from_agent="pm", to_agent="architect")
        assert edge.id == "e1"
        assert edge.from_agent == "pm"
        assert edge.to_agent == "architect"
        assert edge.task_types == []
        assert edge.on_failure == "block"

    def test_pipeline_edge_full(self):
        edge = PipelineEdge(
            id="e2",
            from_agent="coder_be",
            to_agent="architect_reviewer",
            task_types=["verification", "implementation"],
            on_failure="continue_partial",
        )
        assert edge.task_types == ["verification", "implementation"]
        assert edge.on_failure == "continue_partial"

    def test_pipeline_node_config_defaults(self):
        nc = PipelineNodeConfig()
        assert nc.join_strategy == "wait_all"

    def test_pipeline_node_config_stream(self):
        nc = PipelineNodeConfig(join_strategy="stream")
        assert nc.join_strategy == "stream"

    def test_pipeline_config_defaults(self):
        pc = PipelineConfig(id="default-pipeline")
        assert pc.id == "default-pipeline"
        assert pc.name == "Default Pipeline"
        assert pc.start_agent is None
        assert pc.edges == []
        assert pc.node_config == {}

    def test_pipeline_config_full(self):
        edges = [
            PipelineEdge(id="e1", from_agent="pm", to_agent="arch",
                         task_types=["tech_design"]),
        ]
        node_cfg = {"arch": PipelineNodeConfig(join_strategy="stream")}
        pc = PipelineConfig(
            id="pipe-1",
            name="My Pipeline",
            start_agent="pm",
            edges=edges,
            node_config=node_cfg,
        )
        assert pc.start_agent == "pm"
        assert len(pc.edges) == 1
        assert pc.node_config["arch"].join_strategy == "stream"


class TestLoadPipeline:
    """Test loading pipeline config from team.yaml."""

    def test_load_pipeline_from_yaml(self, tmp_path):
        team_yaml = tmp_path / "team.yaml"
        team_yaml.write_text(yaml.dump({
            "team_name": "Test",
            "pipeline": {
                "id": "test-pipe",
                "name": "Test Pipeline",
                "start_agent": "pm",
                "edges": [
                    {
                        "id": "edge-1",
                        "from": "pm",
                        "to": "architect",
                        "task_types": ["tech_design"],
                        "on_failure": "block",
                    },
                ],
                "node_config": {
                    "architect": {"join_strategy": "stream"},
                },
            },
        }))
        pc = load_pipeline(team_yaml)
        assert pc.id == "test-pipe"
        assert pc.name == "Test Pipeline"
        assert pc.start_agent == "pm"
        assert len(pc.edges) == 1
        assert pc.edges[0].from_agent == "pm"
        assert pc.edges[0].to_agent == "architect"
        assert pc.edges[0].task_types == ["tech_design"]
        assert pc.node_config["architect"].join_strategy == "stream"

    def test_load_pipeline_missing_returns_empty(self, tmp_path):
        team_yaml = tmp_path / "team.yaml"
        team_yaml.write_text(yaml.dump({"team_name": "Test"}))
        pc = load_pipeline(team_yaml)
        assert pc.id == "default-pipeline"
        assert pc.edges == []
        assert pc.start_agent is None

    def test_load_pipeline_file_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_pipeline(tmp_path / "nonexistent.yaml")


class TestSavePipeline:
    """Test persisting pipeline config back to team.yaml."""

    def test_save_pipeline_preserves_other_keys(self, tmp_path):
        team_yaml = tmp_path / "team.yaml"
        team_yaml.write_text(yaml.dump({
            "team_name": "Test Team",
            "database": {"path": "~/.taskbrew/data/taskbrew.db"},
        }))
        pc = PipelineConfig(
            id="pipe-1",
            name="My Pipeline",
            start_agent="pm",
            edges=[
                PipelineEdge(id="e1", from_agent="pm", to_agent="arch",
                             task_types=["design"], on_failure="block"),
            ],
            node_config={"arch": PipelineNodeConfig(join_strategy="stream")},
        )
        save_pipeline(team_yaml, pc)

        with open(team_yaml) as f:
            data = yaml.safe_load(f)
        assert data["team_name"] == "Test Team"
        assert data["database"]["path"] == "~/.taskbrew/data/taskbrew.db"
        assert data["pipeline"]["id"] == "pipe-1"
        assert data["pipeline"]["start_agent"] == "pm"
        assert len(data["pipeline"]["edges"]) == 1
        assert data["pipeline"]["edges"][0]["from"] == "pm"
        assert data["pipeline"]["edges"][0]["to"] == "arch"
        assert data["pipeline"]["node_config"]["arch"]["join_strategy"] == "stream"

    def test_save_pipeline_roundtrip(self, tmp_path):
        team_yaml = tmp_path / "team.yaml"
        team_yaml.write_text(yaml.dump({"team_name": "RT"}))
        original = PipelineConfig(
            id="rt-pipe",
            name="Roundtrip",
            start_agent="coder",
            edges=[
                PipelineEdge(id="e1", from_agent="coder", to_agent="reviewer",
                             task_types=["verification"], on_failure="cancel_pipeline"),
                PipelineEdge(id="e2", from_agent="reviewer", to_agent="coder",
                             task_types=["revision"], on_failure="block"),
            ],
            node_config={
                "reviewer": PipelineNodeConfig(join_strategy="stream"),
            },
        )
        save_pipeline(team_yaml, original)
        loaded = load_pipeline(team_yaml)
        assert loaded.id == original.id
        assert loaded.name == original.name
        assert loaded.start_agent == original.start_agent
        assert len(loaded.edges) == 2
        assert loaded.edges[0].from_agent == "coder"
        assert loaded.edges[1].on_failure == "block"
        assert loaded.node_config["reviewer"].join_strategy == "stream"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd "/Users/nikhilchatragadda/Personal Projects/taskbrew" && .venv/bin/pytest tests/test_pipeline_editor.py::TestPipelineDataModel -v 2>&1 | head -30`
Expected: ImportError -- `PipelineEdge`, `PipelineConfig`, etc. do not exist yet.

- [ ] **Step 3: Add dataclasses to config_loader.py**

In `src/taskbrew/config_loader.py`, add after the `RouteTarget` dataclass (after line 199):

```python
# ---------------------------------------------------------------------------
# Pipeline configuration
# ---------------------------------------------------------------------------


@dataclass
class PipelineEdge:
    """A single directed edge in the pipeline graph."""

    id: str
    from_agent: str
    to_agent: str
    task_types: list[str] = field(default_factory=list)
    on_failure: str = "block"  # "block", "continue_partial", "cancel_pipeline"


@dataclass
class PipelineNodeConfig:
    """Per-node configuration in the pipeline (receiving-side settings)."""

    join_strategy: str = "wait_all"  # "wait_all" or "stream"


@dataclass
class PipelineConfig:
    """Top-level pipeline topology stored in team.yaml."""

    id: str = "default-pipeline"
    name: str = "Default Pipeline"
    start_agent: str | None = None
    edges: list[PipelineEdge] = field(default_factory=list)
    node_config: dict[str, PipelineNodeConfig] = field(default_factory=dict)
```

- [ ] **Step 4: Add `load_pipeline()` function**

In `src/taskbrew/config_loader.py`, add after the new dataclasses:

```python
def load_pipeline(team_yaml_path: Path) -> PipelineConfig:
    """Load pipeline topology from team.yaml.

    Parameters
    ----------
    team_yaml_path:
        Path to the team YAML file (e.g. ``config/team.yaml``).

    Returns
    -------
    PipelineConfig
        Parsed pipeline configuration. Returns a default empty pipeline
        if the ``pipeline`` key is missing from the YAML.

    Raises
    ------
    FileNotFoundError
        If *team_yaml_path* does not exist.
    """
    if not team_yaml_path.exists():
        raise FileNotFoundError(f"Team config not found: {team_yaml_path}")

    with open(team_yaml_path) as f:
        data = yaml.safe_load(f) or {}

    pipeline_raw = data.get("pipeline")
    if not pipeline_raw:
        return PipelineConfig()

    edges = []
    for e in pipeline_raw.get("edges", []):
        edges.append(PipelineEdge(
            id=e["id"],
            from_agent=e["from"],
            to_agent=e["to"],
            task_types=e.get("task_types", []),
            on_failure=e.get("on_failure", "block"),
        ))

    node_config: dict[str, PipelineNodeConfig] = {}
    for role_name, nc_raw in pipeline_raw.get("node_config", {}).items():
        node_config[role_name] = PipelineNodeConfig(
            join_strategy=nc_raw.get("join_strategy", "wait_all"),
        )

    return PipelineConfig(
        id=pipeline_raw.get("id", "default-pipeline"),
        name=pipeline_raw.get("name", "Default Pipeline"),
        start_agent=pipeline_raw.get("start_agent"),
        edges=edges,
        node_config=node_config,
    )
```

- [ ] **Step 5: Add `save_pipeline()` function**

In `src/taskbrew/config_loader.py`, add after `load_pipeline()`:

```python
def save_pipeline(team_yaml_path: Path, pipeline: PipelineConfig) -> None:
    """Persist pipeline topology to team.yaml.

    Reads the existing file, updates the ``pipeline`` key, and writes back.
    All other top-level keys are preserved.

    Parameters
    ----------
    team_yaml_path:
        Path to the team YAML file.
    pipeline:
        The pipeline config to save.
    """
    with open(team_yaml_path) as f:
        data = yaml.safe_load(f) or {}

    data["pipeline"] = {
        "id": pipeline.id,
        "name": pipeline.name,
        "start_agent": pipeline.start_agent,
        "edges": [
            {
                "id": e.id,
                "from": e.from_agent,
                "to": e.to_agent,
                "task_types": e.task_types,
                "on_failure": e.on_failure,
            }
            for e in pipeline.edges
        ],
        "node_config": {
            role: {"join_strategy": nc.join_strategy}
            for role, nc in pipeline.node_config.items()
        },
    }

    with open(team_yaml_path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd "/Users/nikhilchatragadda/Personal Projects/taskbrew" && .venv/bin/pytest tests/test_pipeline_editor.py::TestPipelineDataModel tests/test_pipeline_editor.py::TestLoadPipeline tests/test_pipeline_editor.py::TestSavePipeline -v`
Expected: All 10 tests PASS.

- [ ] **Step 7: Commit**

```bash
git add src/taskbrew/config_loader.py tests/test_pipeline_editor.py
git commit -m "feat: add PipelineConfig data model with load/save functions"
```

---

## Task 2: Auto-Migration from routes_to

**Files:**
- Modify: `src/taskbrew/config_loader.py`
- Test: `tests/test_pipeline_editor.py`

- [ ] **Step 1: Write failing tests for auto-migration**

Append to `tests/test_pipeline_editor.py`:

```python
from taskbrew.config_loader import migrate_routes_to_pipeline, RoleConfig, RouteTarget


class TestMigrateRoutesToPipeline:
    """Test auto-migration from per-role routes_to to pipeline edges."""

    def test_migrate_basic_routes(self):
        roles = {
            "pm": RoleConfig(
                role="pm", display_name="PM", prefix="PM", color="#f00",
                emoji="E", system_prompt="PM agent.",
                routes_to=[
                    RouteTarget(role="architect", task_types=["tech_design"]),
                ],
            ),
            "architect": RoleConfig(
                role="architect", display_name="Architect", prefix="AR",
                color="#0f0", emoji="A", system_prompt="Architect agent.",
                routes_to=[
                    RouteTarget(role="coder_be", task_types=["implementation"]),
                ],
            ),
            "coder_be": RoleConfig(
                role="coder_be", display_name="Coder BE", prefix="CB",
                color="#00f", emoji="C", system_prompt="Coder agent.",
                routes_to=[],
            ),
        }
        pc = migrate_routes_to_pipeline(roles)
        assert pc.id == "default-pipeline"
        assert pc.name == "Default Pipeline"
        assert pc.start_agent == "pm"
        assert len(pc.edges) == 2
        edge_pairs = [(e.from_agent, e.to_agent) for e in pc.edges]
        assert ("pm", "architect") in edge_pairs
        assert ("architect", "coder_be") in edge_pairs
        # Check task_types carried over
        pm_to_arch = [e for e in pc.edges if e.from_agent == "pm"][0]
        assert pm_to_arch.task_types == ["tech_design"]

    def test_migrate_detects_start_agent_no_inbound(self):
        """Start agent = the role with no inbound routes."""
        roles = {
            "leader": RoleConfig(
                role="leader", display_name="Leader", prefix="LD",
                color="#f00", emoji="L", system_prompt="Leader.",
                routes_to=[RouteTarget(role="worker")],
            ),
            "worker": RoleConfig(
                role="worker", display_name="Worker", prefix="WK",
                color="#0f0", emoji="W", system_prompt="Worker.",
                routes_to=[],
            ),
        }
        pc = migrate_routes_to_pipeline(roles)
        assert pc.start_agent == "leader"

    def test_migrate_no_routes_empty_pipeline(self):
        roles = {
            "solo": RoleConfig(
                role="solo", display_name="Solo", prefix="SL",
                color="#f00", emoji="S", system_prompt="Solo agent.",
                routes_to=[],
            ),
        }
        pc = migrate_routes_to_pipeline(roles)
        assert len(pc.edges) == 0
        # Single agent with no routes -- no auto start_agent
        assert pc.start_agent is None

    def test_migrate_self_loop(self):
        roles = {
            "reviewer": RoleConfig(
                role="reviewer", display_name="Reviewer", prefix="RV",
                color="#f00", emoji="R", system_prompt="Reviewer.",
                routes_to=[RouteTarget(role="reviewer", task_types=["revision"])],
            ),
        }
        pc = migrate_routes_to_pipeline(roles)
        assert len(pc.edges) == 1
        assert pc.edges[0].from_agent == "reviewer"
        assert pc.edges[0].to_agent == "reviewer"

    def test_migrate_skips_unknown_targets(self):
        """If routes_to references a role not in the roles dict, skip it."""
        roles = {
            "pm": RoleConfig(
                role="pm", display_name="PM", prefix="PM", color="#f00",
                emoji="P", system_prompt="PM.",
                routes_to=[RouteTarget(role="nonexistent")],
            ),
        }
        pc = migrate_routes_to_pipeline(roles)
        assert len(pc.edges) == 0

    def test_migrate_assigns_unique_edge_ids(self):
        roles = {
            "a": RoleConfig(
                role="a", display_name="A", prefix="AA", color="#f00",
                emoji="A", system_prompt="A.",
                routes_to=[
                    RouteTarget(role="b", task_types=["impl"]),
                    RouteTarget(role="c", task_types=["review"]),
                ],
            ),
            "b": RoleConfig(
                role="b", display_name="B", prefix="BB", color="#0f0",
                emoji="B", system_prompt="B.", routes_to=[],
            ),
            "c": RoleConfig(
                role="c", display_name="C", prefix="CC", color="#00f",
                emoji="C", system_prompt="C.", routes_to=[],
            ),
        }
        pc = migrate_routes_to_pipeline(roles)
        edge_ids = [e.id for e in pc.edges]
        assert len(edge_ids) == len(set(edge_ids)), "Edge IDs must be unique"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd "/Users/nikhilchatragadda/Personal Projects/taskbrew" && .venv/bin/pytest tests/test_pipeline_editor.py::TestMigrateRoutesToPipeline -v 2>&1 | head -20`
Expected: ImportError -- `migrate_routes_to_pipeline` does not exist.

- [ ] **Step 3: Implement `migrate_routes_to_pipeline()`**

In `src/taskbrew/config_loader.py`, add after `save_pipeline()`:

```python
def migrate_routes_to_pipeline(roles: dict[str, RoleConfig]) -> PipelineConfig:
    """Auto-generate a PipelineConfig from per-role routes_to fields.

    Used on first load when team.yaml has no ``pipeline`` section but
    roles have ``routes_to`` entries.

    Parameters
    ----------
    roles:
        Mapping of role name to RoleConfig (as returned by :func:`load_roles`).

    Returns
    -------
    PipelineConfig
        A new pipeline with edges derived from all roles' ``routes_to``.
    """
    edges: list[PipelineEdge] = []
    edge_counter = 0

    for role_name, rc in roles.items():
        for rt in rc.routes_to:
            # Skip routes to non-existent roles
            if rt.role not in roles:
                logger.warning(
                    "Migration: skipping route from '%s' to unknown role '%s'",
                    role_name, rt.role,
                )
                continue
            edge_counter += 1
            edges.append(PipelineEdge(
                id=f"migrated-edge-{edge_counter}",
                from_agent=role_name,
                to_agent=rt.role,
                task_types=rt.task_types,
                on_failure="block",
            ))

    # Detect start agent: role with no inbound edges (and at least one
    # outbound edge, or at least one edge exists).
    if edges:
        all_roles = set(roles.keys())
        routed_to = {e.to_agent for e in edges}
        entry_points = all_roles - routed_to
        # Among entry points, prefer those with outbound edges
        entry_with_outbound = [
            r for r in entry_points
            if any(e.from_agent == r for e in edges)
        ]
        start_agent = entry_with_outbound[0] if entry_with_outbound else None
    else:
        start_agent = None

    return PipelineConfig(
        id="default-pipeline",
        name="Default Pipeline",
        start_agent=start_agent,
        edges=edges,
        node_config={},
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd "/Users/nikhilchatragadda/Personal Projects/taskbrew" && .venv/bin/pytest tests/test_pipeline_editor.py::TestMigrateRoutesToPipeline -v`
Expected: All 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/taskbrew/config_loader.py tests/test_pipeline_editor.py
git commit -m "feat: add auto-migration from routes_to to pipeline edges"
```

---

## Task 3: Pipeline API Router (CRUD endpoints)

**Files:**
- Create: `src/taskbrew/dashboard/routers/pipeline_editor.py`
- Modify: `src/taskbrew/dashboard/models.py`
- Modify: `src/taskbrew/dashboard/app.py`
- Test: `tests/test_pipeline_editor.py`

- [ ] **Step 1: Add Pydantic request models**

In `src/taskbrew/dashboard/models.py`, add at the end of the file:

```python
# ---------------------------------------------------------------------------
# Pipeline Editor (Plan 2)
# ---------------------------------------------------------------------------


class PipelineEdgeBody(BaseModel):
    from_agent: str
    to_agent: str
    task_types: list[str] = []
    on_failure: str = "block"


class UpdatePipelineEdgeBody(BaseModel):
    task_types: Optional[list[str]] = None
    on_failure: Optional[str] = None


class UpdatePipelineBody(BaseModel):
    name: Optional[str] = None
    start_agent: Optional[str] = None
    edges: Optional[list[dict[str, Any]]] = None
    node_config: Optional[dict[str, dict[str, str]]] = None


class SetStartAgentBody(BaseModel):
    role: str


class SetNodeConfigBody(BaseModel):
    join_strategy: str = "wait_all"


class ValidatePipelineBody(BaseModel):
    """Optional body for pipeline validation (can be empty)."""
    pass
```

- [ ] **Step 2: Write failing tests for pipeline API**

Append to `tests/test_pipeline_editor.py`:

```python
import json
from unittest.mock import MagicMock, patch, PropertyMock
from fastapi.testclient import TestClient
from pathlib import Path as RealPath
import tempfile
import shutil


def _make_test_app(roles_dict, pipeline_config=None, project_dir=None):
    """Create a minimal FastAPI test app with mock orchestrator."""
    from fastapi import FastAPI
    from taskbrew.dashboard.routers.pipeline_editor import router, set_pipeline_deps
    from taskbrew.dashboard.routers._deps import set_orchestrator

    app = FastAPI()
    app.include_router(router)

    orch = MagicMock()
    orch.roles = roles_dict
    orch.project_dir = project_dir
    orch.team_config = MagicMock()
    set_orchestrator(orch)

    if pipeline_config is not None:
        set_pipeline_deps(pipeline_config)

    return app


class TestPipelineAPIGet:
    """Test GET /api/pipeline."""

    def test_get_pipeline_returns_config(self):
        pc = PipelineConfig(
            id="test-pipe", name="Test", start_agent="pm",
            edges=[PipelineEdge(id="e1", from_agent="pm", to_agent="arch",
                                task_types=["design"])],
            node_config={"arch": PipelineNodeConfig(join_strategy="stream")},
        )
        roles = {
            "pm": RoleConfig(role="pm", display_name="PM", prefix="PM",
                             color="#f00", emoji="P", system_prompt="PM."),
            "arch": RoleConfig(role="arch", display_name="Arch", prefix="AR",
                               color="#0f0", emoji="A", system_prompt="Arch."),
        }
        app = _make_test_app(roles, pc)
        client = TestClient(app)
        resp = client.get("/api/pipeline")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "test-pipe"
        assert data["name"] == "Test"
        assert data["start_agent"] == "pm"
        assert len(data["edges"]) == 1
        assert data["edges"][0]["from"] == "pm"
        assert data["edges"][0]["to"] == "arch"
        assert data["node_config"]["arch"]["join_strategy"] == "stream"

    def test_get_pipeline_empty(self):
        pc = PipelineConfig()
        app = _make_test_app({}, pc)
        client = TestClient(app)
        resp = client.get("/api/pipeline")
        assert resp.status_code == 200
        data = resp.json()
        assert data["edges"] == []
        assert data["start_agent"] is None


class TestPipelineAPIEdges:
    """Test edge CRUD endpoints."""

    def test_add_edge(self):
        pc = PipelineConfig(id="p1", start_agent="pm")
        roles = {
            "pm": RoleConfig(role="pm", display_name="PM", prefix="PM",
                             color="#f00", emoji="P", system_prompt="PM."),
            "arch": RoleConfig(role="arch", display_name="Arch", prefix="AR",
                               color="#0f0", emoji="A", system_prompt="Arch."),
        }
        tmpdir = tempfile.mkdtemp()
        team_yaml = RealPath(tmpdir) / "config" / "team.yaml"
        team_yaml.parent.mkdir(parents=True)
        team_yaml.write_text(yaml.dump({"team_name": "Test"}))
        try:
            app = _make_test_app(roles, pc, project_dir=tmpdir)
            client = TestClient(app)
            resp = client.post("/api/pipeline/edges", json={
                "from_agent": "pm", "to_agent": "arch",
                "task_types": ["tech_design"],
            })
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "ok"
            assert "edge_id" in data

            # Verify edge was added
            get_resp = client.get("/api/pipeline")
            edges = get_resp.json()["edges"]
            assert len(edges) == 1
            assert edges[0]["from"] == "pm"
            assert edges[0]["to"] == "arch"
        finally:
            shutil.rmtree(tmpdir)

    def test_add_edge_unknown_role_rejected(self):
        pc = PipelineConfig(id="p1")
        roles = {
            "pm": RoleConfig(role="pm", display_name="PM", prefix="PM",
                             color="#f00", emoji="P", system_prompt="PM."),
        }
        app = _make_test_app(roles, pc)
        client = TestClient(app)
        resp = client.post("/api/pipeline/edges", json={
            "from_agent": "pm", "to_agent": "nonexistent",
            "task_types": [],
        })
        assert resp.status_code == 400

    def test_delete_edge(self):
        pc = PipelineConfig(
            id="p1",
            edges=[PipelineEdge(id="e1", from_agent="pm", to_agent="arch")],
        )
        roles = {
            "pm": RoleConfig(role="pm", display_name="PM", prefix="PM",
                             color="#f00", emoji="P", system_prompt="PM."),
            "arch": RoleConfig(role="arch", display_name="Arch", prefix="AR",
                               color="#0f0", emoji="A", system_prompt="Arch."),
        }
        tmpdir = tempfile.mkdtemp()
        team_yaml = RealPath(tmpdir) / "config" / "team.yaml"
        team_yaml.parent.mkdir(parents=True)
        team_yaml.write_text(yaml.dump({"team_name": "Test"}))
        try:
            app = _make_test_app(roles, pc, project_dir=tmpdir)
            client = TestClient(app)
            resp = client.delete("/api/pipeline/edges/e1")
            assert resp.status_code == 200
            # Verify edge removed
            get_resp = client.get("/api/pipeline")
            assert len(get_resp.json()["edges"]) == 0
        finally:
            shutil.rmtree(tmpdir)

    def test_delete_edge_not_found(self):
        pc = PipelineConfig(id="p1")
        app = _make_test_app({}, pc)
        client = TestClient(app)
        resp = client.delete("/api/pipeline/edges/nonexistent")
        assert resp.status_code == 404

    def test_update_edge(self):
        pc = PipelineConfig(
            id="p1",
            edges=[PipelineEdge(id="e1", from_agent="pm", to_agent="arch",
                                task_types=["design"], on_failure="block")],
        )
        roles = {
            "pm": RoleConfig(role="pm", display_name="PM", prefix="PM",
                             color="#f00", emoji="P", system_prompt="PM."),
            "arch": RoleConfig(role="arch", display_name="Arch", prefix="AR",
                               color="#0f0", emoji="A", system_prompt="Arch."),
        }
        tmpdir = tempfile.mkdtemp()
        team_yaml = RealPath(tmpdir) / "config" / "team.yaml"
        team_yaml.parent.mkdir(parents=True)
        team_yaml.write_text(yaml.dump({"team_name": "Test"}))
        try:
            app = _make_test_app(roles, pc, project_dir=tmpdir)
            client = TestClient(app)
            resp = client.put("/api/pipeline/edges/e1", json={
                "task_types": ["implementation", "verification"],
                "on_failure": "cancel_pipeline",
            })
            assert resp.status_code == 200
            get_resp = client.get("/api/pipeline")
            edge = get_resp.json()["edges"][0]
            assert edge["task_types"] == ["implementation", "verification"]
            assert edge["on_failure"] == "cancel_pipeline"
        finally:
            shutil.rmtree(tmpdir)


class TestPipelineAPIStartAgent:
    """Test start agent endpoint."""

    def test_set_start_agent(self):
        pc = PipelineConfig(id="p1", start_agent=None)
        roles = {
            "pm": RoleConfig(role="pm", display_name="PM", prefix="PM",
                             color="#f00", emoji="P", system_prompt="PM."),
        }
        tmpdir = tempfile.mkdtemp()
        team_yaml = RealPath(tmpdir) / "config" / "team.yaml"
        team_yaml.parent.mkdir(parents=True)
        team_yaml.write_text(yaml.dump({"team_name": "Test"}))
        try:
            app = _make_test_app(roles, pc, project_dir=tmpdir)
            client = TestClient(app)
            resp = client.put("/api/pipeline/start-agent", json={"role": "pm"})
            assert resp.status_code == 200
            get_resp = client.get("/api/pipeline")
            assert get_resp.json()["start_agent"] == "pm"
        finally:
            shutil.rmtree(tmpdir)

    def test_set_start_agent_unknown_role(self):
        pc = PipelineConfig(id="p1")
        roles = {}
        app = _make_test_app(roles, pc)
        client = TestClient(app)
        resp = client.put("/api/pipeline/start-agent", json={"role": "nope"})
        assert resp.status_code == 400


class TestPipelineAPINodeConfig:
    """Test node config endpoint."""

    def test_set_node_config(self):
        pc = PipelineConfig(id="p1")
        roles = {
            "arch": RoleConfig(role="arch", display_name="Arch", prefix="AR",
                               color="#0f0", emoji="A", system_prompt="Arch."),
        }
        tmpdir = tempfile.mkdtemp()
        team_yaml = RealPath(tmpdir) / "config" / "team.yaml"
        team_yaml.parent.mkdir(parents=True)
        team_yaml.write_text(yaml.dump({"team_name": "Test"}))
        try:
            app = _make_test_app(roles, pc, project_dir=tmpdir)
            client = TestClient(app)
            resp = client.put("/api/pipeline/node-config/arch",
                              json={"join_strategy": "stream"})
            assert resp.status_code == 200
            get_resp = client.get("/api/pipeline")
            assert get_resp.json()["node_config"]["arch"]["join_strategy"] == "stream"
        finally:
            shutil.rmtree(tmpdir)

    def test_set_node_config_invalid_strategy(self):
        pc = PipelineConfig(id="p1")
        roles = {
            "arch": RoleConfig(role="arch", display_name="Arch", prefix="AR",
                               color="#0f0", emoji="A", system_prompt="Arch."),
        }
        app = _make_test_app(roles, pc)
        client = TestClient(app)
        resp = client.put("/api/pipeline/node-config/arch",
                          json={"join_strategy": "invalid"})
        assert resp.status_code == 400


class TestPipelineAPIValidate:
    """Test pipeline validation endpoint."""

    def test_validate_pipeline_no_start_agent(self):
        pc = PipelineConfig(id="p1", start_agent=None, edges=[
            PipelineEdge(id="e1", from_agent="a", to_agent="b"),
        ])
        roles = {
            "a": RoleConfig(role="a", display_name="A", prefix="AA",
                            color="#f00", emoji="A", system_prompt="A."),
            "b": RoleConfig(role="b", display_name="B", prefix="BB",
                            color="#0f0", emoji="B", system_prompt="B."),
        }
        app = _make_test_app(roles, pc)
        client = TestClient(app)
        resp = client.post("/api/pipeline/validate")
        data = resp.json()
        assert data["valid"] is False
        assert any("start agent" in e.lower() for e in data["errors"])

    def test_validate_pipeline_valid(self):
        pc = PipelineConfig(
            id="p1", start_agent="pm",
            edges=[PipelineEdge(id="e1", from_agent="pm", to_agent="arch",
                                task_types=["design"])],
        )
        roles = {
            "pm": RoleConfig(role="pm", display_name="PM", prefix="PM",
                             color="#f00", emoji="P", system_prompt="PM.",
                             produces=["design"]),
            "arch": RoleConfig(role="arch", display_name="Arch", prefix="AR",
                               color="#0f0", emoji="A", system_prompt="Arch.",
                               accepts=["design"]),
        }
        app = _make_test_app(roles, pc)
        client = TestClient(app)
        resp = client.post("/api/pipeline/validate")
        data = resp.json()
        assert data["valid"] is True

    def test_validate_disconnected_agents(self):
        pc = PipelineConfig(
            id="p1", start_agent="pm",
            edges=[PipelineEdge(id="e1", from_agent="pm", to_agent="arch")],
        )
        roles = {
            "pm": RoleConfig(role="pm", display_name="PM", prefix="PM",
                             color="#f00", emoji="P", system_prompt="PM."),
            "arch": RoleConfig(role="arch", display_name="Arch", prefix="AR",
                               color="#0f0", emoji="A", system_prompt="Arch."),
            "orphan": RoleConfig(role="orphan", display_name="Orphan", prefix="OR",
                                 color="#00f", emoji="O", system_prompt="Orphan."),
        }
        app = _make_test_app(roles, pc)
        client = TestClient(app)
        resp = client.post("/api/pipeline/validate")
        data = resp.json()
        assert any("disconnected" in w.lower() or "orphan" in w.lower()
                    for w in data.get("warnings", []))


class TestPipelineAPIPutFull:
    """Test PUT /api/pipeline (full update)."""

    def test_full_pipeline_update(self):
        pc = PipelineConfig(id="p1", start_agent="pm")
        roles = {
            "pm": RoleConfig(role="pm", display_name="PM", prefix="PM",
                             color="#f00", emoji="P", system_prompt="PM."),
            "arch": RoleConfig(role="arch", display_name="Arch", prefix="AR",
                               color="#0f0", emoji="A", system_prompt="Arch."),
        }
        tmpdir = tempfile.mkdtemp()
        team_yaml = RealPath(tmpdir) / "config" / "team.yaml"
        team_yaml.parent.mkdir(parents=True)
        team_yaml.write_text(yaml.dump({"team_name": "Test"}))
        try:
            app = _make_test_app(roles, pc, project_dir=tmpdir)
            client = TestClient(app)
            resp = client.put("/api/pipeline", json={
                "name": "Updated Pipeline",
                "start_agent": "arch",
                "edges": [
                    {"id": "new-e1", "from": "arch", "to": "pm",
                     "task_types": ["review"], "on_failure": "block"},
                ],
                "node_config": {
                    "pm": {"join_strategy": "stream"},
                },
            })
            assert resp.status_code == 200
            get_resp = client.get("/api/pipeline")
            data = get_resp.json()
            assert data["name"] == "Updated Pipeline"
            assert data["start_agent"] == "arch"
            assert len(data["edges"]) == 1
            assert data["edges"][0]["from"] == "arch"
            assert data["node_config"]["pm"]["join_strategy"] == "stream"
        finally:
            shutil.rmtree(tmpdir)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd "/Users/nikhilchatragadda/Personal Projects/taskbrew" && .venv/bin/pytest tests/test_pipeline_editor.py::TestPipelineAPIGet -v 2>&1 | head -20`
Expected: ImportError -- `pipeline_editor` module does not exist.

- [ ] **Step 3: Create the pipeline_editor router**

Create `src/taskbrew/dashboard/routers/pipeline_editor.py`:

```python
"""Pipeline topology editor: CRUD for pipeline edges, start agent, node config."""

from __future__ import annotations

import uuid
from pathlib import Path

import yaml
from fastapi import APIRouter, HTTPException

from taskbrew.config_loader import (
    PipelineConfig,
    PipelineEdge,
    PipelineNodeConfig,
    load_pipeline,
    save_pipeline,
)
from taskbrew.dashboard.models import (
    PipelineEdgeBody,
    SetNodeConfigBody,
    SetStartAgentBody,
    UpdatePipelineBody,
    UpdatePipelineEdgeBody,
)
from taskbrew.dashboard.routers._deps import get_orch, get_orch_optional

router = APIRouter()

# In-memory pipeline state, loaded at startup or set by tests.
_pipeline: PipelineConfig | None = None


def set_pipeline_deps(pipeline: PipelineConfig) -> None:
    """Inject pipeline config (called by app.py or tests)."""
    global _pipeline
    _pipeline = pipeline


def get_pipeline() -> PipelineConfig:
    """Return the current in-memory pipeline, initialising if needed."""
    global _pipeline
    if _pipeline is None:
        _pipeline = PipelineConfig()
    return _pipeline


def _persist(project_dir: str | None) -> None:
    """Write the current in-memory pipeline to team.yaml if project_dir set."""
    if not project_dir:
        return
    yaml_path = Path(project_dir) / "config" / "team.yaml"
    if yaml_path.exists():
        save_pipeline(yaml_path, get_pipeline())


# ------------------------------------------------------------------
# GET /api/pipeline — return full pipeline
# ------------------------------------------------------------------


@router.get("/api/pipeline")
async def get_pipeline_config():
    pc = get_pipeline()
    return {
        "id": pc.id,
        "name": pc.name,
        "start_agent": pc.start_agent,
        "edges": [
            {
                "id": e.id,
                "from": e.from_agent,
                "to": e.to_agent,
                "task_types": e.task_types,
                "on_failure": e.on_failure,
            }
            for e in pc.edges
        ],
        "node_config": {
            role: {"join_strategy": nc.join_strategy}
            for role, nc in pc.node_config.items()
        },
    }


# ------------------------------------------------------------------
# PUT /api/pipeline — full update (Save All Changes)
# ------------------------------------------------------------------


@router.put("/api/pipeline")
async def update_pipeline_full(body: UpdatePipelineBody):
    orch = get_orch()
    pc = get_pipeline()
    roles = orch.roles or {}

    if body.name is not None:
        pc.name = body.name
    if body.start_agent is not None:
        if body.start_agent not in roles:
            raise HTTPException(400, f"Unknown role: {body.start_agent}")
        pc.start_agent = body.start_agent
    if body.edges is not None:
        new_edges = []
        for e in body.edges:
            from_agent = e.get("from", "")
            to_agent = e.get("to", "")
            if from_agent not in roles:
                raise HTTPException(400, f"Unknown source role: {from_agent}")
            if to_agent not in roles:
                raise HTTPException(400, f"Unknown target role: {to_agent}")
            new_edges.append(PipelineEdge(
                id=e.get("id", f"edge-{uuid.uuid4().hex[:8]}"),
                from_agent=from_agent,
                to_agent=to_agent,
                task_types=e.get("task_types", []),
                on_failure=e.get("on_failure", "block"),
            ))
        pc.edges = new_edges
    if body.node_config is not None:
        new_nc: dict[str, PipelineNodeConfig] = {}
        for role, cfg in body.node_config.items():
            js = cfg.get("join_strategy", "wait_all")
            if js not in ("wait_all", "stream"):
                raise HTTPException(400, f"Invalid join_strategy: {js}")
            new_nc[role] = PipelineNodeConfig(join_strategy=js)
        pc.node_config = new_nc

    _persist(orch.project_dir)
    return {"status": "ok"}


# ------------------------------------------------------------------
# POST /api/pipeline/edges — add edge
# ------------------------------------------------------------------


@router.post("/api/pipeline/edges")
async def add_pipeline_edge(body: PipelineEdgeBody):
    orch = get_orch()
    roles = orch.roles or {}
    pc = get_pipeline()

    if body.from_agent not in roles:
        raise HTTPException(400, f"Unknown source role: {body.from_agent}")
    if body.to_agent not in roles:
        raise HTTPException(400, f"Unknown target role: {body.to_agent}")
    if body.on_failure not in ("block", "continue_partial", "cancel_pipeline"):
        raise HTTPException(400, f"Invalid on_failure: {body.on_failure}")

    edge_id = f"edge-{uuid.uuid4().hex[:8]}"
    edge = PipelineEdge(
        id=edge_id,
        from_agent=body.from_agent,
        to_agent=body.to_agent,
        task_types=body.task_types,
        on_failure=body.on_failure,
    )
    pc.edges.append(edge)
    _persist(orch.project_dir)

    return {"status": "ok", "edge_id": edge_id}


# ------------------------------------------------------------------
# DELETE /api/pipeline/edges/{edge_id} — remove edge
# ------------------------------------------------------------------


@router.delete("/api/pipeline/edges/{edge_id}")
async def delete_pipeline_edge(edge_id: str):
    orch = get_orch()
    pc = get_pipeline()

    original_len = len(pc.edges)
    pc.edges = [e for e in pc.edges if e.id != edge_id]
    if len(pc.edges) == original_len:
        raise HTTPException(404, f"Edge not found: {edge_id}")

    _persist(orch.project_dir)
    return {"status": "ok"}


# ------------------------------------------------------------------
# PUT /api/pipeline/edges/{edge_id} — update edge config
# ------------------------------------------------------------------


@router.put("/api/pipeline/edges/{edge_id}")
async def update_pipeline_edge(edge_id: str, body: UpdatePipelineEdgeBody):
    orch = get_orch()
    pc = get_pipeline()

    edge = next((e for e in pc.edges if e.id == edge_id), None)
    if edge is None:
        raise HTTPException(404, f"Edge not found: {edge_id}")

    if body.task_types is not None:
        edge.task_types = body.task_types
    if body.on_failure is not None:
        if body.on_failure not in ("block", "continue_partial", "cancel_pipeline"):
            raise HTTPException(400, f"Invalid on_failure: {body.on_failure}")
        edge.on_failure = body.on_failure

    _persist(orch.project_dir)
    return {"status": "ok"}


# ------------------------------------------------------------------
# PUT /api/pipeline/start-agent — set start agent
# ------------------------------------------------------------------


@router.put("/api/pipeline/start-agent")
async def set_start_agent(body: SetStartAgentBody):
    orch = get_orch()
    roles = orch.roles or {}
    pc = get_pipeline()

    if body.role not in roles:
        raise HTTPException(400, f"Unknown role: {body.role}")

    pc.start_agent = body.role
    _persist(orch.project_dir)
    return {"status": "ok"}


# ------------------------------------------------------------------
# PUT /api/pipeline/node-config/{role_name} — set join strategy
# ------------------------------------------------------------------


@router.put("/api/pipeline/node-config/{role_name}")
async def set_node_config(role_name: str, body: SetNodeConfigBody):
    orch = get_orch()
    roles = orch.roles or {}
    pc = get_pipeline()

    if role_name not in roles:
        raise HTTPException(400, f"Unknown role: {role_name}")
    if body.join_strategy not in ("wait_all", "stream"):
        raise HTTPException(400, f"Invalid join_strategy: {body.join_strategy}")

    pc.node_config[role_name] = PipelineNodeConfig(
        join_strategy=body.join_strategy,
    )
    _persist(orch.project_dir)
    return {"status": "ok"}


# ------------------------------------------------------------------
# POST /api/pipeline/validate — validate pipeline
# ------------------------------------------------------------------


@router.post("/api/pipeline/validate")
async def validate_pipeline():
    orch = get_orch_optional()
    roles = orch.roles if orch else {}
    pc = get_pipeline()

    errors: list[str] = []
    warnings: list[str] = []
    infos: list[str] = []

    # Error: No start agent
    if pc.start_agent is None and (pc.edges or roles):
        errors.append("No start agent marked. Set a start agent for the pipeline.")

    # Error: Start agent does not exist
    if pc.start_agent and pc.start_agent not in roles:
        errors.append(
            f"Start agent '{pc.start_agent}' does not exist. "
            "Re-create it or set a different start agent."
        )

    # Warning: Start agent has incoming edges
    if pc.start_agent:
        incoming_to_start = [
            e for e in pc.edges
            if e.to_agent == pc.start_agent and e.from_agent != pc.start_agent
        ]
        if incoming_to_start:
            warnings.append(
                f"Start agent '{pc.start_agent}' has incoming edges from other agents. "
                "Start agents should only receive tasks from the user."
            )

    # Warning: Edges referencing unknown roles
    for edge in pc.edges:
        if edge.from_agent not in roles:
            warnings.append(
                f"Edge '{edge.id}' references unknown agent: {edge.from_agent}. "
                "Remove or re-create this agent."
            )
        if edge.to_agent not in roles:
            warnings.append(
                f"Edge '{edge.id}' references unknown agent: {edge.to_agent}. "
                "Remove or re-create this agent."
            )

    # Warning: Revision loops without max_revision_cycles cap
    for edge in pc.edges:
        if "revision" in edge.task_types:
            target_role = roles.get(edge.to_agent)
            if target_role and target_role.max_revision_cycles == 0:
                warnings.append(
                    f"Edge '{edge.id}' ({edge.from_agent} -> {edge.to_agent}) "
                    f"carries 'revision' tasks but '{edge.to_agent}' has "
                    "max_revision_cycles=0 (unlimited). Consider setting a cap."
                )

    # Warning: Edge task_types not in source produces or target accepts
    for edge in pc.edges:
        source = roles.get(edge.from_agent)
        target = roles.get(edge.to_agent)
        if source and target and edge.task_types:
            for tt in edge.task_types:
                if source.produces and tt not in source.produces:
                    warnings.append(
                        f"Edge '{edge.id}': task_type '{tt}' not in "
                        f"'{edge.from_agent}' produces list."
                    )
                if target.accepts and tt not in target.accepts:
                    warnings.append(
                        f"Edge '{edge.id}': task_type '{tt}' not in "
                        f"'{edge.to_agent}' accepts list."
                    )

    # Info: Disconnected agents
    connected_roles = set()
    for edge in pc.edges:
        connected_roles.add(edge.from_agent)
        connected_roles.add(edge.to_agent)
    if pc.start_agent:
        connected_roles.add(pc.start_agent)
    for role_name in roles:
        if role_name not in connected_roles:
            infos.append(
                f"Agent '{role_name}' is disconnected (no edges, not start agent)."
            )

    valid = len(errors) == 0
    return {
        "valid": valid,
        "errors": errors,
        "warnings": warnings,
        "infos": infos,
    }
```

- [ ] **Step 4: Register the router in app.py**

In `src/taskbrew/dashboard/app.py`, add the import and include_router calls. Find the line:

```python
    from taskbrew.dashboard.routers.presets import router as presets_router
```

And add after it:

```python
    from taskbrew.dashboard.routers.pipeline_editor import router as pipeline_editor_router
```

Find the line:

```python
    app.include_router(presets_router.router, tags=["Presets"])
```

And add after it:

```python
    app.include_router(pipeline_editor_router, tags=["Pipeline Editor"])
```

Also add pipeline initialization. Find where `set_orchestrator(orch)` is called in `create_app()` and add after it:

```python
    # Initialize pipeline config from team.yaml or auto-migrate
    from taskbrew.dashboard.routers.pipeline_editor import set_pipeline_deps
    from taskbrew.config_loader import load_pipeline, migrate_routes_to_pipeline, save_pipeline
    if orch and orch.project_dir:
        team_yaml_path = Path(orch.project_dir) / "config" / "team.yaml"
        if team_yaml_path.exists():
            pc = load_pipeline(team_yaml_path)
            if not pc.edges and orch.roles:
                # Auto-migrate from routes_to
                pc = migrate_routes_to_pipeline(orch.roles)
                if pc.edges:
                    save_pipeline(team_yaml_path, pc)
            set_pipeline_deps(pc)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd "/Users/nikhilchatragadda/Personal Projects/taskbrew" && .venv/bin/pytest tests/test_pipeline_editor.py -v -x`
Expected: All tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/taskbrew/dashboard/routers/pipeline_editor.py src/taskbrew/dashboard/models.py src/taskbrew/dashboard/app.py tests/test_pipeline_editor.py
git commit -m "feat: add pipeline editor API router with CRUD endpoints"
```

---

## Task 4: Update delete_role() to Clean Up Pipeline Edges

**Files:**
- Modify: `src/taskbrew/dashboard/routers/system.py`
- Test: `tests/test_pipeline_editor.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_pipeline_editor.py`:

```python
class TestDeleteRolePipelineCleanup:
    """Test that deleting a role removes its edges from the pipeline."""

    def test_delete_role_removes_pipeline_edges(self):
        """When a role is deleted, all pipeline edges involving it are removed."""
        from taskbrew.dashboard.routers.pipeline_editor import (
            get_pipeline, set_pipeline_deps,
        )

        pc = PipelineConfig(
            id="p1",
            start_agent="pm",
            edges=[
                PipelineEdge(id="e1", from_agent="pm", to_agent="arch"),
                PipelineEdge(id="e2", from_agent="arch", to_agent="coder"),
                PipelineEdge(id="e3", from_agent="coder", to_agent="arch"),
            ],
            node_config={"arch": PipelineNodeConfig(join_strategy="stream")},
        )
        set_pipeline_deps(pc)

        # After removing "arch", edges e1, e2, e3 (all reference arch) should be gone
        # except e1 references arch as target, e2 as source, e3 as target
        # Actually: e1 (pm->arch), e2 (arch->coder), e3 (coder->arch)
        # Removing arch removes all three
        from taskbrew.dashboard.routers.pipeline_editor import _cleanup_role_from_pipeline
        _cleanup_role_from_pipeline("arch")

        remaining = get_pipeline()
        assert len(remaining.edges) == 0
        assert "arch" not in remaining.node_config

    def test_delete_role_clears_start_agent(self):
        from taskbrew.dashboard.routers.pipeline_editor import (
            get_pipeline, set_pipeline_deps, _cleanup_role_from_pipeline,
        )
        pc = PipelineConfig(id="p1", start_agent="deleted_role")
        set_pipeline_deps(pc)
        _cleanup_role_from_pipeline("deleted_role")
        assert get_pipeline().start_agent is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd "/Users/nikhilchatragadda/Personal Projects/taskbrew" && .venv/bin/pytest tests/test_pipeline_editor.py::TestDeleteRolePipelineCleanup -v 2>&1 | head -20`
Expected: ImportError -- `_cleanup_role_from_pipeline` does not exist.

- [ ] **Step 3: Add `_cleanup_role_from_pipeline()` to pipeline_editor.py**

In `src/taskbrew/dashboard/routers/pipeline_editor.py`, add after the `_persist()` function:

```python
def _cleanup_role_from_pipeline(role_name: str) -> None:
    """Remove all edges and node_config referencing a deleted role."""
    pc = get_pipeline()
    pc.edges = [
        e for e in pc.edges
        if e.from_agent != role_name and e.to_agent != role_name
    ]
    pc.node_config.pop(role_name, None)
    if pc.start_agent == role_name:
        pc.start_agent = None
```

- [ ] **Step 4: Integrate into system.py delete_role()**

In `src/taskbrew/dashboard/routers/system.py`, find the `delete_role` function. After the line:

```python
    del _roles[role_name]
```

Add:

```python
    # Clean up pipeline edges referencing the deleted role
    try:
        from taskbrew.dashboard.routers.pipeline_editor import (
            _cleanup_role_from_pipeline, _persist,
        )
        _cleanup_role_from_pipeline(role_name)
        _persist(_project_dir)
    except ImportError:
        pass  # Pipeline editor not installed
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd "/Users/nikhilchatragadda/Personal Projects/taskbrew" && .venv/bin/pytest tests/test_pipeline_editor.py::TestDeleteRolePipelineCleanup -v`
Expected: Both tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/taskbrew/dashboard/routers/pipeline_editor.py src/taskbrew/dashboard/routers/system.py tests/test_pipeline_editor.py
git commit -m "feat: clean up pipeline edges when a role is deleted"
```

---

## Task 5: Frontend -- Load Pipeline from API

**Files:**
- Modify: `src/taskbrew/dashboard/static/js/settings.js`

This task changes the data source for the pipeline visualization from `settingsData.roles[].routes_to` to a separate `pipelineData` object loaded from `/api/pipeline`.

- [ ] **Step 1: Add `pipelineData` state variable**

In `src/taskbrew/dashboard/static/js/settings.js`, find line 91:

```javascript
let settingsData = { team: {}, roles: [] };
```

Add after it (after `let dragState = null;` on line 102):

```javascript
let pipelineData = { id: '', name: '', start_agent: null, edges: [], node_config: {} };
let pipelineUndoStack = [];
let pipelineRedoStack = [];
const PIPELINE_MAX_UNDO = 50;
```

- [ ] **Step 2: Add pipeline load function**

In `src/taskbrew/dashboard/static/js/settings.js`, find the `loadSettings()` function (line 194). Replace it with:

```javascript
async function loadSettings() {
    try {
        const [teamRes, rolesRes, modelsRes, pipelineRes] = await Promise.all([
            fetch('/api/settings/team'),
            fetch('/api/settings/roles'),
            fetch('/api/settings/models').catch(function() { return null; }),
            fetch('/api/pipeline').catch(function() { return null; }),
        ]);
        settingsData.team = await teamRes.json();
        settingsData.roles = await rolesRes.json();
        if (modelsRes && modelsRes.ok) {
            var md = await modelsRes.json();
            availableModels = md.models || [];
        }
        if (pipelineRes && pipelineRes.ok) {
            pipelineData = await pipelineRes.json();
        }
        originalData = deepClone(settingsData);
        renderAll();
    } catch (e) {
        showToast('Failed to load settings: ' + e.message, 'error');
    }
}
```

- [ ] **Step 3: Add pipeline undo/redo helper functions**

Add after the `markSaved()` function (after line 182):

```javascript
function pushPipelineUndo() {
    pipelineUndoStack.push(deepClone(pipelineData));
    if (pipelineUndoStack.length > PIPELINE_MAX_UNDO) {
        pipelineUndoStack.shift();
    }
    pipelineRedoStack = [];
}

function pipelineUndo() {
    if (pipelineUndoStack.length === 0) return;
    pipelineRedoStack.push(deepClone(pipelineData));
    pipelineData = pipelineUndoStack.pop();
    markUnsaved();
    renderPipeline();
}

function pipelineRedo() {
    if (pipelineRedoStack.length === 0) return;
    pipelineUndoStack.push(deepClone(pipelineData));
    pipelineData = pipelineRedoStack.pop();
    markUnsaved();
    renderPipeline();
}
```

- [ ] **Step 4: Update `computeGraphLayout()` to accept edges parameter**

Replace the `computeGraphLayout` function (lines 223--307) with:

```javascript
function computeGraphLayout(roles, pipelineEdges) {
    // Build role map
    var roleMap = {};
    roles.forEach(function(r) { roleMap[r.role] = r; });

    // Build edges from pipelineData.edges instead of routes_to
    var allEdges = [];
    (pipelineEdges || []).forEach(function(pe) {
        if (roleMap[pe.from] && roleMap[pe.to]) {
            allEdges.push({
                id: pe.id,
                from: pe.from,
                to: pe.to,
                taskTypes: pe.task_types || [],
                onFailure: pe.on_failure || 'block',
            });
        }
    });

    // Assign layers via longest-path from sources (ignoring back-edges iteratively)
    var layers = {};
    var inDegree = {};
    var forwardAdj = {};
    roles.forEach(function(r) { inDegree[r.role] = 0; forwardAdj[r.role] = []; });
    allEdges.forEach(function(e) {
        if (e.from !== e.to) {
            forwardAdj[e.from].push(e.to);
            inDegree[e.to] = (inDegree[e.to] || 0) + 1;
        }
    });
    // Kahn's to get an ordering
    var queue = [];
    Object.keys(inDegree).forEach(function(k) { if (inDegree[k] === 0) queue.push(k); });
    var topoOrder = [];
    var tempInDeg = Object.assign({}, inDegree);
    while (queue.length > 0) {
        var node = queue.shift();
        topoOrder.push(node);
        (forwardAdj[node] || []).forEach(function(next) {
            tempInDeg[next]--;
            if (tempInDeg[next] === 0) queue.push(next);
        });
    }
    roles.forEach(function(r) { if (topoOrder.indexOf(r.role) === -1) topoOrder.push(r.role); });

    // Assign layers: longest path from any source
    topoOrder.forEach(function(r) { layers[r] = 0; });
    topoOrder.forEach(function(r) {
        (forwardAdj[r] || []).forEach(function(next) {
            if (topoOrder.indexOf(next) > topoOrder.indexOf(r)) {
                layers[next] = Math.max(layers[next], layers[r] + 1);
            }
        });
    });

    // Classify edges
    var forwardEdges = [];
    var backwardEdges = [];
    var selfLoops = [];
    allEdges.forEach(function(e) {
        if (e.from === e.to) {
            selfLoops.push(e);
        } else if (layers[e.to] > layers[e.from]) {
            forwardEdges.push(e);
        } else {
            backwardEdges.push(e);
        }
    });

    // Group nodes by layer for Y positioning
    var layerGroups = {};
    topoOrder.forEach(function(r) {
        var l = layers[r];
        if (!layerGroups[l]) layerGroups[l] = [];
        layerGroups[l].push(r);
    });

    return {
        topoOrder: topoOrder,
        layers: layers,
        layerGroups: layerGroups,
        forwardEdges: forwardEdges,
        backwardEdges: backwardEdges,
        selfLoops: selfLoops,
        roleMap: roleMap,
        numLayers: Math.max.apply(null, Object.values(layers).concat([0])) + 1,
    };
}
```

- [ ] **Step 5: Update `renderPipeline()` call to pass pipeline edges**

In the `renderPipeline()` function (line 309), find the line:

```javascript
    const layout = computeGraphLayout(roles);
```

Replace with:

```javascript
    var layout = computeGraphLayout(roles, pipelineData.edges || []);
```

- [ ] **Step 6: Update `addRouteFromPipeline()` to add pipeline edge**

Replace the `addRouteFromPipeline` function (lines 608--621) with:

```javascript
function addRouteFromPipeline(fromRole, toRole) {
    // Compute default task_types from intersection of source produces and target accepts
    var fromR = getRoleByName(fromRole);
    var toR = getRoleByName(toRole);
    var defaultTypes = [];
    if (fromR && toR && fromR.produces && toR.accepts) {
        defaultTypes = fromR.produces.filter(function(t) {
            return toR.accepts.indexOf(t) !== -1;
        });
        if (defaultTypes.length === 0 && fromR.produces.length > 0) {
            defaultTypes = fromR.produces.slice();
        }
    }

    pushPipelineUndo();
    var edgeId = 'edge-' + Date.now() + '-' + Math.random().toString(36).substr(2, 6);
    pipelineData.edges.push({
        id: edgeId,
        from: fromRole,
        to: toRole,
        task_types: defaultTypes,
        on_failure: 'block',
    });
    markUnsaved();
    renderAll();
    showToast('Edge added: ' + fromRole + ' \u2192 ' + toRole, 'success');
}
```

- [ ] **Step 7: Update `removeRoute()` to remove pipeline edge**

Replace the `removeRoute` function (lines 623--630) with:

```javascript
function removeRoute(fromRole, toRole, edgeId) {
    pushPipelineUndo();
    if (edgeId) {
        pipelineData.edges = pipelineData.edges.filter(function(e) { return e.id !== edgeId; });
    } else {
        pipelineData.edges = pipelineData.edges.filter(function(e) {
            return !(e.from === fromRole && e.to === toRole);
        });
    }
    markUnsaved();
    renderAll();
    showToast('Edge removed', 'info');
}
```

- [ ] **Step 8: Update `saveAll()` to include pipeline save**

In the `saveAll()` function (line 1199), find the comment `// Save each role` block. After the role-saving loop (after the `}` that closes the for loop on line 1234), add:

```javascript
        // Save pipeline
        await fetch('/api/pipeline', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                name: pipelineData.name,
                start_agent: pipelineData.start_agent,
                edges: pipelineData.edges,
                node_config: pipelineData.node_config,
            })
        });
```

- [ ] **Step 9: Verify by manual inspection**

Open the dashboard in a browser, go to Settings. Confirm:
1. Pipeline section loads and renders nodes and edges from `/api/pipeline`.
2. Drag-to-connect still works (creates edges in `pipelineData`).
3. Click edge to delete still works.
4. Save All Changes persists pipeline to `team.yaml`.

- [ ] **Step 10: Commit**

```bash
git add src/taskbrew/dashboard/static/js/settings.js
git commit -m "feat: load pipeline from /api/pipeline, replace routes_to-based rendering"
```

---

## Task 6: Frontend -- Interactive Edge Drawing and Selection

**Files:**
- Modify: `src/taskbrew/dashboard/static/js/settings.js`
- Modify: `src/taskbrew/dashboard/static/css/settings.css`

This task adds click-to-connect (source selected state), edge hover-to-delete, right-click start agent, and keyboard accessibility.

- [ ] **Step 1: Add pipeline interaction state variables**

In `src/taskbrew/dashboard/static/js/settings.js`, find the `pipelineRedoStack` variable added in Task 5. Add after the undo/redo variables:

```javascript
let pipelineSelectedSource = null; // role name of currently selected source node
let pipelineHoveredEdge = null;    // edge id of currently hovered edge
let pipelineFocusedEdge = null;    // edge id of currently focused edge (for keyboard)
```

- [ ] **Step 2: Add click-to-connect node handler to renderPipeline()**

In `renderPipeline()`, find the node rendering section (the `layout.topoOrder.forEach` block starting around line 507). Replace the node `<g>` rendering with the updated version that adds click/contextmenu handlers and start agent badge. Find:

```javascript
    // ---- Nodes ----
    layout.topoOrder.forEach(function(roleName) {
```

Replace the entire Nodes section (up to the "+" node section) with:

```javascript
    // ---- Nodes ----
    var connectedRoles = new Set();
    allEdges.forEach(function(e) { connectedRoles.add(e.from); connectedRoles.add(e.to); });
    if (pipelineData.start_agent) connectedRoles.add(pipelineData.start_agent);

    layout.topoOrder.forEach(function(roleName) {
        var r = roleMap[roleName];
        if (!r) return;
        var pos = positions[roleName];
        var c = r.color || '#6366f1';
        var isStartAgent = (pipelineData.start_agent === roleName);
        var isSelected = (pipelineSelectedSource === roleName);
        var isConnected = connectedRoles.has(roleName);
        var strokeDash = isConnected ? '' : ' stroke-dasharray="6 4"';
        var strokeWidth = isSelected ? '3' : '1.5';
        var strokeColor = isSelected ? 'var(--accent-cyan)' : escapeHtml(c);

        html += '<g class="pipeline-node' + (isSelected ? ' selected' : '') + (isStartAgent ? ' start-agent' : '') + '"';
        html += ' data-role="' + escapeHtml(r.role) + '"';
        html += ' tabindex="0"';
        html += ' transform="translate(' + pos.x + ',' + pos.y + ')"';
        html += ' style="cursor:pointer">';

        html += '<rect width="' + nodeW + '" height="' + nodeH + '" rx="12"';
        html += ' fill="' + escapeHtml(c) + '" fill-opacity="0.12"';
        html += ' stroke="' + strokeColor + '" stroke-width="' + strokeWidth + '"' + strokeDash;
        html += ' filter="url(#glow-' + r.role + ')"/>';

        // Start agent badge (play icon)
        if (isStartAgent) {
            html += '<g transform="translate(' + (nodeW - 20) + ', 4)">';
            html += '<circle r="8" cx="8" cy="8" fill="var(--accent-cyan)" opacity="0.9"/>';
            html += '<polygon points="6,4 13,8 6,12" fill="var(--bg-primary)" opacity="0.9"/>';
            html += '</g>';
        }

        html += '<text x="' + (nodeW / 2) + '" y="' + (nodeH / 2 - 6) + '" text-anchor="middle" font-size="18" fill="var(--text-primary)">' + escapeHtml(r.emoji || '') + '</text>';
        html += '<text x="' + (nodeW / 2) + '" y="' + (nodeH / 2 + 14) + '" text-anchor="middle" font-size="12" font-weight="600" fill="var(--text-primary)" font-family="Inter, sans-serif">' + escapeHtml(r.display_name || r.role) + '</text>';

        // Output port (right edge)
        html += '<circle class="pipeline-port output-port" cx="' + nodeW + '" cy="' + (nodeH / 2) + '" r="5" fill="' + escapeHtml(c) + '" stroke="var(--bg-primary)" stroke-width="2" data-role="' + escapeHtml(r.role) + '" data-port="output"/>';
        // Input port (left edge)
        html += '<circle class="pipeline-port input-port" cx="0" cy="' + (nodeH / 2) + '" r="5" fill="' + escapeHtml(c) + '" stroke="var(--bg-primary)" stroke-width="2" data-role="' + escapeHtml(r.role) + '" data-port="input"/>';
        html += '</g>';
    });
```

- [ ] **Step 3: Add edge hover "x" button rendering**

In `renderPipeline()`, in the forward edges rendering section, after the particle circles for each edge, add an "x" button at the midpoint. Find the closing of the forward edges loop (after the second particle `</circle>`) and add before `connIdx++;`:

```javascript
        // Hover delete button at midpoint
        var midX = (x1 + x2) / 2;
        var midY = (y1 + y2) / 2;
        html += '<g class="pipeline-edge-delete" data-edge-id="' + escapeHtml(e.id) + '" data-from="' + escapeHtml(e.from) + '" data-to="' + escapeHtml(e.to) + '" style="cursor:pointer;opacity:0" transform="translate(' + midX + ',' + midY + ')">';
        html += '<circle r="10" fill="var(--bg-secondary)" stroke="var(--border-subtle)" stroke-width="1"/>';
        html += '<line x1="-4" y1="-4" x2="4" y2="4" stroke="var(--text-muted)" stroke-width="2" stroke-linecap="round"/>';
        html += '<line x1="4" y1="-4" x2="-4" y2="4" stroke="var(--text-muted)" stroke-width="2" stroke-linecap="round"/>';
        html += '</g>';
```

Do the same for backward edges and self-loops (add the "x" button at appropriate midpoints).

For backward edges, after the small particle circle, before `connIdx++; backIdx++;`:

```javascript
        // Hover delete button at midpoint of backward arc
        var bmidX = (x1 + x2) / 2;
        var bmidY = arcY;
        html += '<g class="pipeline-edge-delete" data-edge-id="' + escapeHtml(e.id) + '" data-from="' + escapeHtml(e.from) + '" data-to="' + escapeHtml(e.to) + '" style="cursor:pointer;opacity:0" transform="translate(' + bmidX + ',' + bmidY + ')">';
        html += '<circle r="10" fill="var(--bg-secondary)" stroke="var(--border-subtle)" stroke-width="1"/>';
        html += '<line x1="-4" y1="-4" x2="4" y2="4" stroke="var(--text-muted)" stroke-width="2" stroke-linecap="round"/>';
        html += '<line x1="4" y1="-4" x2="-4" y2="4" stroke="var(--text-muted)" stroke-width="2" stroke-linecap="round"/>';
        html += '</g>';
```

- [ ] **Step 4: Update `attachPipelineDragHandlers()` for click-to-connect and context menu**

Replace the `attachPipelineDragHandlers()` function (lines 538--606) with:

```javascript
function attachPipelineDragHandlers() {
    var svg = document.getElementById('pipelineSvg');
    var dragLine = document.getElementById('pipelineDragLine');
    if (!svg || !dragLine) return;

    // --- Port drag-to-connect (existing behavior, kept) ---
    svg.querySelectorAll('.output-port').forEach(function(port) {
        port.addEventListener('mousedown', function(e) {
            e.stopPropagation();
            var role = port.getAttribute('data-role');
            var rect = svg.getBoundingClientRect();
            var svgW = svg.viewBox.baseVal.width;
            var svgH = svg.viewBox.baseVal.height;
            var scaleX = svgW / rect.width;
            var scaleY = svgH / rect.height;
            var cx = parseFloat(port.getAttribute('cx'));
            var parentTransform = port.closest('.pipeline-node').getAttribute('transform');
            var match = parentTransform.match(/translate\(([^,]+),([^)]+)\)/);
            var tx = match ? parseFloat(match[1]) : 0;
            var ty = match ? parseFloat(match[2]) : 0;
            var cy = parseFloat(port.getAttribute('cy'));
            dragState = {
                fromRole: role,
                startX: tx + cx,
                startY: ty + cy,
                rect: rect,
                scaleX: scaleX,
                scaleY: scaleY
            };
            dragLine.setAttribute('x1', dragState.startX);
            dragLine.setAttribute('y1', dragState.startY);
            dragLine.setAttribute('x2', dragState.startX);
            dragLine.setAttribute('y2', dragState.startY);
            dragLine.setAttribute('visibility', 'visible');
        });
    });

    svg.addEventListener('mousemove', function(e) {
        if (!dragState) return;
        var x = (e.clientX - dragState.rect.left) * dragState.scaleX;
        var y = (e.clientY - dragState.rect.top) * dragState.scaleY;
        dragLine.setAttribute('x2', x);
        dragLine.setAttribute('y2', y);
    });

    svg.addEventListener('mouseup', function(e) {
        if (!dragState) return;
        var target = e.target.closest('.input-port');
        if (target) {
            var toRole = target.getAttribute('data-role');
            if (toRole && toRole !== dragState.fromRole) {
                addRouteFromPipeline(dragState.fromRole, toRole);
            }
        }
        dragLine.setAttribute('visibility', 'hidden');
        dragState = null;
    });

    // --- Click-to-connect (click node to select source, click another to draw edge) ---
    svg.querySelectorAll('.pipeline-node').forEach(function(nodeEl) {
        nodeEl.addEventListener('click', function(e) {
            // Don't trigger if clicking a port
            if (e.target.closest('.pipeline-port')) return;
            if (e.target.closest('.pipeline-edge-delete')) return;
            var role = nodeEl.getAttribute('data-role');

            if (pipelineSelectedSource === null) {
                // Select as source
                pipelineSelectedSource = role;
                renderPipeline();
            } else if (pipelineSelectedSource === role) {
                // Deselect
                pipelineSelectedSource = null;
                renderPipeline();
            } else {
                // Draw edge from source to this target
                addRouteFromPipeline(pipelineSelectedSource, role);
                pipelineSelectedSource = null;
            }
        });

        // Right-click / context menu for "Set as Start Agent"
        nodeEl.addEventListener('contextmenu', function(e) {
            e.preventDefault();
            var role = nodeEl.getAttribute('data-role');
            showPipelineContextMenu(e, role);
        });

        // Keyboard: Enter to select/connect, Escape to cancel
        nodeEl.addEventListener('keydown', function(e) {
            if (e.key === 'Enter') {
                nodeEl.click();
            } else if (e.key === 'Escape') {
                pipelineSelectedSource = null;
                renderPipeline();
            } else if (e.key === 'Delete' || e.key === 'Backspace') {
                // If an edge is focused, remove it
                if (pipelineFocusedEdge) {
                    removeRoute(null, null, pipelineFocusedEdge);
                    pipelineFocusedEdge = null;
                }
            }
        });
    });

    // --- Click empty space to cancel selection ---
    svg.addEventListener('click', function(e) {
        if (!e.target.closest('.pipeline-node') && !e.target.closest('.pipeline-connection') && !e.target.closest('.pipeline-edge-delete')) {
            if (pipelineSelectedSource) {
                pipelineSelectedSource = null;
                renderPipeline();
            }
        }
    });

    // --- Edge hover: show/hide delete button ---
    svg.querySelectorAll('.pipeline-connection').forEach(function(conn) {
        var edgeId = conn.getAttribute('data-edge-id');
        var deleteBtn = svg.querySelector('.pipeline-edge-delete[data-edge-id="' + edgeId + '"]');

        conn.addEventListener('mouseenter', function() {
            if (deleteBtn) deleteBtn.style.opacity = '1';
            conn.style.opacity = '1';
        });
        conn.addEventListener('mouseleave', function() {
            if (deleteBtn) deleteBtn.style.opacity = '0';
            conn.style.opacity = '';
        });
    });

    // --- Edge delete button click ---
    svg.querySelectorAll('.pipeline-edge-delete').forEach(function(btn) {
        btn.addEventListener('click', function(e) {
            e.stopPropagation();
            var edgeId = btn.getAttribute('data-edge-id');
            var from = btn.getAttribute('data-from');
            var to = btn.getAttribute('data-to');
            removeRoute(from, to, edgeId);
        });

        btn.addEventListener('mouseenter', function() { btn.style.opacity = '1'; });
        btn.addEventListener('mouseleave', function() { btn.style.opacity = '0'; });
    });

    // --- Edge click: show config popover ---
    svg.querySelectorAll('.pipeline-connection').forEach(function(conn) {
        conn.addEventListener('click', function(e) {
            e.stopPropagation();
            var edgeId = conn.getAttribute('data-edge-id');
            if (edgeId) {
                showEdgePopover(e, edgeId);
            }
        });
    });
}
```

- [ ] **Step 5: Add connection data-edge-id attributes to rendered edges**

In `renderPipeline()`, update the forward edge `<path>` to include `data-edge-id`. Find the main path line in the forward edges loop:

```javascript
        html += '<path class="pipeline-connection" data-from="' + escapeHtml(e.from) + '" data-to="' + escapeHtml(e.to) + '"
```

Replace with:

```javascript
        html += '<path class="pipeline-connection" data-edge-id="' + escapeHtml(e.id) + '" data-from="' + escapeHtml(e.from) + '" data-to="' + escapeHtml(e.to) + '"
```

Do the same for backward edges and self-loops -- add `data-edge-id` attribute.

- [ ] **Step 6: Add context menu function for "Set as Start Agent"**

Add after the `attachPipelineDragHandlers()` function:

```javascript
function showPipelineContextMenu(event, roleName) {
    // Remove any existing context menu
    var existing = document.getElementById('pipelineContextMenu');
    if (existing) existing.remove();

    var menu = document.createElement('div');
    menu.id = 'pipelineContextMenu';
    menu.className = 'pipeline-context-menu';
    menu.style.position = 'fixed';
    menu.style.left = event.clientX + 'px';
    menu.style.top = event.clientY + 'px';
    menu.style.zIndex = '10000';

    var isStart = (pipelineData.start_agent === roleName);
    var label = isStart ? 'Unset as Start Agent' : 'Set as Start Agent';

    menu.innerHTML = '<div class="pipeline-context-item" data-action="start">'
        + '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:14px;height:14px;margin-right:6px"><polygon points="5 3 19 12 5 21 5 3"/></svg>'
        + label + '</div>';

    document.body.appendChild(menu);

    menu.querySelector('[data-action="start"]').addEventListener('click', function() {
        pushPipelineUndo();
        if (isStart) {
            pipelineData.start_agent = null;
        } else {
            pipelineData.start_agent = roleName;
        }
        markUnsaved();
        renderPipeline();
        menu.remove();
    });

    // Close on click outside
    function closeMenu(e) {
        if (!menu.contains(e.target)) {
            menu.remove();
            document.removeEventListener('click', closeMenu);
        }
    }
    setTimeout(function() { document.addEventListener('click', closeMenu); }, 10);
}
```

- [ ] **Step 7: Add keyboard shortcuts for undo/redo**

Find the existing keyboard handler (around line 1709). Add before the Escape handler:

```javascript
    // Undo/Redo for pipeline
    if ((e.ctrlKey || e.metaKey) && e.key === 'z' && !e.shiftKey) {
        e.preventDefault();
        pipelineUndo();
    }
    if ((e.ctrlKey || e.metaKey) && ((e.key === 'z' && e.shiftKey) || e.key === 'Z')) {
        e.preventDefault();
        pipelineRedo();
    }
```

- [ ] **Step 8: Add CSS styles for interactive pipeline elements**

In `src/taskbrew/dashboard/static/css/settings.css`, add at the end:

```css
/* ================================================================
   Pipeline Editor Interactive Styles
   ================================================================ */

/* Selected node highlight */
.pipeline-node.selected rect {
    stroke: var(--accent-cyan) !important;
    stroke-width: 3 !important;
    filter: drop-shadow(0 0 8px var(--accent-cyan));
}

/* Start agent badge glow */
.pipeline-node.start-agent rect {
    filter: drop-shadow(0 0 12px var(--accent-cyan));
}

/* Hoverable edges */
.pipeline-connection {
    cursor: pointer;
    transition: opacity 0.15s;
}
.pipeline-connection:hover {
    opacity: 1 !important;
    stroke-width: 4 !important;
}

/* Edge delete button */
.pipeline-edge-delete {
    transition: opacity 0.2s;
    pointer-events: all;
}
.pipeline-edge-delete:hover circle {
    fill: var(--accent-red, #ef4444);
}
.pipeline-edge-delete:hover line {
    stroke: white;
}

/* Context menu */
.pipeline-context-menu {
    background: var(--bg-secondary);
    border: 1px solid var(--border-subtle);
    border-radius: 8px;
    padding: 4px;
    min-width: 180px;
    box-shadow: 0 8px 24px rgba(0,0,0,0.3);
}
.pipeline-context-item {
    display: flex;
    align-items: center;
    padding: 8px 12px;
    font-size: 13px;
    color: var(--text-primary);
    cursor: pointer;
    border-radius: 6px;
    transition: background 0.1s;
}
.pipeline-context-item:hover {
    background: var(--bg-tertiary, rgba(255,255,255,0.06));
}

/* Unconnected node dashed border -- handled inline via SVG stroke-dasharray */

/* Pipeline section unsaved dot */
.pipeline-section-header .unsaved-dot {
    display: inline-block;
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: var(--accent-cyan);
    margin-left: 8px;
    opacity: 0;
    transition: opacity 0.2s;
}
.pipeline-section-header .unsaved-dot.visible {
    opacity: 1;
}
```

- [ ] **Step 9: Commit**

```bash
git add src/taskbrew/dashboard/static/js/settings.js src/taskbrew/dashboard/static/css/settings.css
git commit -m "feat: add interactive pipeline editing — click-to-connect, edge delete, start agent"
```

---

## Task 7: Frontend -- Edge and Node Popovers

**Files:**
- Modify: `src/taskbrew/dashboard/static/js/settings.js`
- Modify: `src/taskbrew/dashboard/static/css/settings.css`

- [ ] **Step 1: Add edge popover function**

Add to `src/taskbrew/dashboard/static/js/settings.js`, after `showPipelineContextMenu()`:

```javascript
function showEdgePopover(event, edgeId) {
    var edge = pipelineData.edges.find(function(e) { return e.id === edgeId; });
    if (!edge) return;

    // Remove any existing popover
    var existing = document.getElementById('pipelinePopover');
    if (existing) existing.remove();

    var fromRole = getRoleByName(edge.from);
    var toRole = getRoleByName(edge.to);

    var popover = document.createElement('div');
    popover.id = 'pipelinePopover';
    popover.className = 'pipeline-popover';
    popover.style.position = 'fixed';
    popover.style.left = event.clientX + 'px';
    popover.style.top = event.clientY + 'px';
    popover.style.zIndex = '10000';

    var taskTypesStr = (edge.task_types || []).join(', ') || '(none)';

    popover.innerHTML = ''
        + '<div class="pipeline-popover-header">'
        + '<span>' + escapeHtml(edge.from) + ' &rarr; ' + escapeHtml(edge.to) + '</span>'
        + '<button class="pipeline-popover-close" onclick="this.closest(\'.pipeline-popover\').remove()">&times;</button>'
        + '</div>'
        + '<div class="pipeline-popover-body">'
        + '<label class="pipeline-popover-label">Task Types</label>'
        + '<input type="text" class="pipeline-popover-input" id="edgeTaskTypes" value="' + escapeHtml(taskTypesStr === '(none)' ? '' : taskTypesStr) + '" placeholder="e.g. implementation, verification">'
        + '<p class="pipeline-popover-hint">Comma-separated list of task types this edge carries</p>'
        + '<label class="pipeline-popover-label">On Failure</label>'
        + '<select class="pipeline-popover-select" id="edgeOnFailure">'
        + '<option value="block"' + (edge.on_failure === 'block' ? ' selected' : '') + '>Block (wait for source)</option>'
        + '<option value="continue_partial"' + (edge.on_failure === 'continue_partial' ? ' selected' : '') + '>Continue Partial</option>'
        + '<option value="cancel_pipeline"' + (edge.on_failure === 'cancel_pipeline' ? ' selected' : '') + '>Cancel Pipeline</option>'
        + '</select>'
        + '<div class="pipeline-popover-actions">'
        + '<button class="btn btn-sm btn-danger" id="edgeDeleteBtn">Delete Edge</button>'
        + '<button class="btn btn-sm btn-primary" id="edgeSaveBtn">Apply</button>'
        + '</div>'
        + '</div>';

    document.body.appendChild(popover);

    // Keep popover on screen
    var rect = popover.getBoundingClientRect();
    if (rect.right > window.innerWidth) {
        popover.style.left = (window.innerWidth - rect.width - 8) + 'px';
    }
    if (rect.bottom > window.innerHeight) {
        popover.style.top = (window.innerHeight - rect.height - 8) + 'px';
    }

    document.getElementById('edgeSaveBtn').addEventListener('click', function() {
        pushPipelineUndo();
        var typesInput = document.getElementById('edgeTaskTypes').value.trim();
        edge.task_types = typesInput ? typesInput.split(',').map(function(s) { return s.trim(); }).filter(Boolean) : [];
        edge.on_failure = document.getElementById('edgeOnFailure').value;
        markUnsaved();
        popover.remove();
        renderPipeline();
    });

    document.getElementById('edgeDeleteBtn').addEventListener('click', function() {
        removeRoute(edge.from, edge.to, edge.id);
        popover.remove();
    });

    // Close on click outside
    function closePopover(e) {
        if (!popover.contains(e.target) && !e.target.closest('.pipeline-connection')) {
            popover.remove();
            document.removeEventListener('mousedown', closePopover);
        }
    }
    setTimeout(function() { document.addEventListener('mousedown', closePopover); }, 10);
}
```

- [ ] **Step 2: Add node popover function**

Add after `showEdgePopover()`:

```javascript
function showNodePopover(event, roleName) {
    var role = getRoleByName(roleName);
    if (!role) return;

    // Remove any existing popover
    var existing = document.getElementById('pipelinePopover');
    if (existing) existing.remove();

    var nodeConfig = (pipelineData.node_config || {})[roleName] || {};
    var joinStrategy = nodeConfig.join_strategy || 'wait_all';
    var isStart = (pipelineData.start_agent === roleName);

    var popover = document.createElement('div');
    popover.id = 'pipelinePopover';
    popover.className = 'pipeline-popover';
    popover.style.position = 'fixed';
    popover.style.left = event.clientX + 'px';
    popover.style.top = event.clientY + 'px';
    popover.style.zIndex = '10000';

    popover.innerHTML = ''
        + '<div class="pipeline-popover-header">'
        + '<span>' + escapeHtml(role.emoji || '') + ' ' + escapeHtml(role.display_name || roleName) + '</span>'
        + '<button class="pipeline-popover-close" onclick="this.closest(\'.pipeline-popover\').remove()">&times;</button>'
        + '</div>'
        + '<div class="pipeline-popover-body">'
        + '<label class="pipeline-popover-label">Join Strategy</label>'
        + '<select class="pipeline-popover-select" id="nodeJoinStrategy">'
        + '<option value="wait_all"' + (joinStrategy === 'wait_all' ? ' selected' : '') + '>Wait All (default)</option>'
        + '<option value="stream"' + (joinStrategy === 'stream' ? ' selected' : '') + '>Stream</option>'
        + '</select>'
        + '<p class="pipeline-popover-hint">How this node handles multiple incoming edges</p>'
        + '<hr style="border-color:var(--border-subtle);margin:8px 0">'
        + '<label class="pipeline-popover-label">'
        + '<input type="checkbox" id="nodeIsStart"' + (isStart ? ' checked' : '') + '> Start Agent'
        + '</label>'
        + '<p class="pipeline-popover-hint">Receives the user\'s initial prompt</p>'
        + '<div class="pipeline-popover-actions">'
        + '<button class="btn btn-sm btn-primary" id="nodeSaveBtn">Apply</button>'
        + '</div>'
        + '</div>';

    document.body.appendChild(popover);

    // Keep popover on screen
    var rect = popover.getBoundingClientRect();
    if (rect.right > window.innerWidth) {
        popover.style.left = (window.innerWidth - rect.width - 8) + 'px';
    }
    if (rect.bottom > window.innerHeight) {
        popover.style.top = (window.innerHeight - rect.height - 8) + 'px';
    }

    document.getElementById('nodeSaveBtn').addEventListener('click', function() {
        pushPipelineUndo();
        var newStrategy = document.getElementById('nodeJoinStrategy').value;
        if (!pipelineData.node_config) pipelineData.node_config = {};
        pipelineData.node_config[roleName] = { join_strategy: newStrategy };

        var wantsStart = document.getElementById('nodeIsStart').checked;
        if (wantsStart) {
            pipelineData.start_agent = roleName;
        } else if (pipelineData.start_agent === roleName) {
            pipelineData.start_agent = null;
        }

        markUnsaved();
        popover.remove();
        renderPipeline();
    });

    // Close on click outside
    function closePopover(e) {
        if (!popover.contains(e.target) && !e.target.closest('.pipeline-node')) {
            popover.remove();
            document.removeEventListener('mousedown', closePopover);
        }
    }
    setTimeout(function() { document.addEventListener('mousedown', closePopover); }, 10);
}
```

- [ ] **Step 3: Wire node double-click to show node popover**

In `attachPipelineDragHandlers()`, in the node click handler section, add a double-click handler alongside the existing click handler. After the `nodeEl.addEventListener('contextmenu', ...)` block, add:

```javascript
        // Double-click node for config popover
        nodeEl.addEventListener('dblclick', function(e) {
            e.stopPropagation();
            e.preventDefault();
            pipelineSelectedSource = null;
            var role = nodeEl.getAttribute('data-role');
            showNodePopover(e, role);
        });
```

- [ ] **Step 4: Add popover CSS styles**

Append to `src/taskbrew/dashboard/static/css/settings.css`:

```css
/* Pipeline popover */
.pipeline-popover {
    background: var(--bg-secondary);
    border: 1px solid var(--border-subtle);
    border-radius: 10px;
    min-width: 260px;
    max-width: 340px;
    box-shadow: 0 12px 36px rgba(0,0,0,0.4);
    overflow: hidden;
}
.pipeline-popover-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 10px 14px;
    font-size: 13px;
    font-weight: 600;
    color: var(--text-primary);
    border-bottom: 1px solid var(--border-subtle);
}
.pipeline-popover-close {
    background: none;
    border: none;
    color: var(--text-muted);
    font-size: 18px;
    cursor: pointer;
    padding: 0 4px;
    line-height: 1;
}
.pipeline-popover-close:hover {
    color: var(--text-primary);
}
.pipeline-popover-body {
    padding: 12px 14px;
}
.pipeline-popover-label {
    display: block;
    font-size: 12px;
    font-weight: 600;
    color: var(--text-secondary);
    margin-bottom: 4px;
    margin-top: 8px;
}
.pipeline-popover-label:first-child {
    margin-top: 0;
}
.pipeline-popover-input {
    width: 100%;
    padding: 6px 10px;
    font-size: 13px;
    background: var(--bg-primary);
    border: 1px solid var(--border-subtle);
    border-radius: 6px;
    color: var(--text-primary);
    box-sizing: border-box;
}
.pipeline-popover-input:focus {
    outline: none;
    border-color: var(--accent-cyan);
}
.pipeline-popover-select {
    width: 100%;
    padding: 6px 10px;
    font-size: 13px;
    background: var(--bg-primary);
    border: 1px solid var(--border-subtle);
    border-radius: 6px;
    color: var(--text-primary);
    cursor: pointer;
}
.pipeline-popover-hint {
    font-size: 11px;
    color: var(--text-muted);
    margin: 2px 0 0 0;
}
.pipeline-popover-actions {
    display: flex;
    justify-content: flex-end;
    gap: 8px;
    margin-top: 12px;
}
```

- [ ] **Step 5: Commit**

```bash
git add src/taskbrew/dashboard/static/js/settings.js src/taskbrew/dashboard/static/css/settings.css
git commit -m "feat: add edge/node config popovers for pipeline editor"
```

---

## Task 8: Frontend -- Pipeline Validation Indicators

**Files:**
- Modify: `src/taskbrew/dashboard/static/js/settings.js`
- Modify: `src/taskbrew/dashboard/static/css/settings.css`

- [ ] **Step 1: Add validation banner rendering**

In `src/taskbrew/dashboard/static/js/settings.js`, add after the `showNodePopover()` function:

```javascript
function renderPipelineValidation() {
    var container = document.getElementById('pipelineValidation');
    if (!container) return;

    var errors = [];
    var warnings = [];
    var infos = [];
    var roles = settingsData.roles || [];
    var edges = pipelineData.edges || [];

    // Error: No start agent when agents or edges exist
    if (!pipelineData.start_agent && (edges.length > 0 || roles.length > 0)) {
        errors.push('No start agent marked. Right-click a node or use the node popover to set one.');
    }

    // Warning: Start agent has incoming edges
    if (pipelineData.start_agent) {
        var incomingToStart = edges.filter(function(e) {
            return e.to === pipelineData.start_agent && e.from !== pipelineData.start_agent;
        });
        if (incomingToStart.length > 0) {
            warnings.push('Start agent has incoming edges from other agents.');
        }
    }

    // Info: Disconnected agents
    var connectedRoles = new Set();
    edges.forEach(function(e) { connectedRoles.add(e.from); connectedRoles.add(e.to); });
    if (pipelineData.start_agent) connectedRoles.add(pipelineData.start_agent);
    roles.forEach(function(r) {
        if (!connectedRoles.has(r.role)) {
            infos.push(escapeHtml(r.display_name || r.role) + ' is disconnected.');
        }
    });

    // Warning: Revision edges without cap
    edges.forEach(function(e) {
        if (e.task_types && e.task_types.indexOf('revision') !== -1) {
            var target = getRoleByName(e.to);
            if (target && (target.max_revision_cycles === 0 || target.max_revision_cycles === undefined)) {
                warnings.push('Revision loop to ' + escapeHtml(e.to) + ' has no max_revision_cycles cap.');
            }
        }
    });

    var html = '';
    errors.forEach(function(msg) {
        html += '<div class="pipeline-validation-msg error"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:14px;height:14px;flex-shrink:0"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg><span>' + msg + '</span></div>';
    });
    warnings.forEach(function(msg) {
        html += '<div class="pipeline-validation-msg warning"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:14px;height:14px;flex-shrink:0"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg><span>' + msg + '</span></div>';
    });
    infos.forEach(function(msg) {
        html += '<div class="pipeline-validation-msg info"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:14px;height:14px;flex-shrink:0"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg><span>' + msg + '</span></div>';
    });

    container.innerHTML = html;
}
```

- [ ] **Step 2: Call validation rendering from renderPipeline()**

At the end of the `renderPipeline()` function, just before the closing `}`, add:

```javascript
    renderPipelineValidation();
```

- [ ] **Step 3: Add validation container to settings.html**

In `src/taskbrew/dashboard/templates/settings.html`, find the pipeline SVG container. Add before the `<svg>`:

```html
<div id="pipelineValidation" class="pipeline-validation"></div>
```

- [ ] **Step 4: Add validation CSS styles**

Append to `src/taskbrew/dashboard/static/css/settings.css`:

```css
/* Pipeline validation indicators */
.pipeline-validation {
    display: flex;
    flex-direction: column;
    gap: 4px;
    padding: 0 12px;
    margin-bottom: 8px;
}
.pipeline-validation:empty {
    display: none;
}
.pipeline-validation-msg {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 6px 10px;
    font-size: 12px;
    border-radius: 6px;
}
.pipeline-validation-msg.error {
    background: rgba(239, 68, 68, 0.1);
    color: #ef4444;
    border: 1px solid rgba(239, 68, 68, 0.2);
}
.pipeline-validation-msg.warning {
    background: rgba(245, 158, 11, 0.1);
    color: #f59e0b;
    border: 1px solid rgba(245, 158, 11, 0.2);
}
.pipeline-validation-msg.info {
    background: rgba(99, 102, 241, 0.1);
    color: #818cf8;
    border: 1px solid rgba(99, 102, 241, 0.2);
}
```

- [ ] **Step 5: Update `renderAll()` to rename section header**

In `renderAll()`, the pipeline section header text should read "Pipeline" instead of "Pipeline Visualizer" (per spec section 2.1). If the header text is rendered in `settings.html` rather than JS, update the template. If it is in JS, find and replace `Pipeline Visualizer` with `Pipeline`.

- [ ] **Step 6: Commit**

```bash
git add src/taskbrew/dashboard/static/js/settings.js src/taskbrew/dashboard/static/css/settings.css src/taskbrew/dashboard/templates/settings.html
git commit -m "feat: add pipeline validation indicators (errors, warnings, info)"
```

---

## Task 9: Integration Tests

**Files:**
- Modify: `tests/test_pipeline_editor.py`

- [ ] **Step 1: Add end-to-end integration tests**

Append to `tests/test_pipeline_editor.py`:

```python
class TestPipelineEditorIntegration:
    """End-to-end tests: migration + API + validation together."""

    def test_auto_migration_then_api(self):
        """Simulate full flow: roles with routes_to -> auto-migrate -> API reads correct pipeline."""
        roles = {
            "pm": RoleConfig(
                role="pm", display_name="PM", prefix="PM", color="#f00",
                emoji="P", system_prompt="PM.",
                routes_to=[RouteTarget(role="arch", task_types=["design"])],
                produces=["design"],
            ),
            "arch": RoleConfig(
                role="arch", display_name="Arch", prefix="AR", color="#0f0",
                emoji="A", system_prompt="Arch.",
                routes_to=[RouteTarget(role="coder", task_types=["impl"])],
                produces=["impl"], accepts=["design"],
            ),
            "coder": RoleConfig(
                role="coder", display_name="Coder", prefix="CD", color="#00f",
                emoji="C", system_prompt="Coder.",
                routes_to=[],
                accepts=["impl"],
            ),
        }
        pc = migrate_routes_to_pipeline(roles)
        assert pc.start_agent == "pm"
        assert len(pc.edges) == 2

        app = _make_test_app(roles, pc)
        client = TestClient(app)

        # GET pipeline
        resp = client.get("/api/pipeline")
        assert resp.status_code == 200
        data = resp.json()
        assert data["start_agent"] == "pm"
        assert len(data["edges"]) == 2

        # Validate pipeline
        resp = client.post("/api/pipeline/validate")
        data = resp.json()
        assert data["valid"] is True

    def test_add_edge_then_validate_task_types_warning(self):
        """Adding an edge with mismatched task types produces a validation warning."""
        pc = PipelineConfig(id="p1", start_agent="pm")
        roles = {
            "pm": RoleConfig(
                role="pm", display_name="PM", prefix="PM", color="#f00",
                emoji="P", system_prompt="PM.",
                produces=["task_list"],
            ),
            "coder": RoleConfig(
                role="coder", display_name="Coder", prefix="CD", color="#00f",
                emoji="C", system_prompt="Coder.",
                accepts=["implementation"],
            ),
        }
        tmpdir = tempfile.mkdtemp()
        team_yaml = RealPath(tmpdir) / "config" / "team.yaml"
        team_yaml.parent.mkdir(parents=True)
        team_yaml.write_text(yaml.dump({"team_name": "Test"}))
        try:
            app = _make_test_app(roles, pc, project_dir=tmpdir)
            client = TestClient(app)

            # Add edge with task_type not in target accepts
            client.post("/api/pipeline/edges", json={
                "from_agent": "pm", "to_agent": "coder",
                "task_types": ["task_list"],
            })

            # Validate
            resp = client.post("/api/pipeline/validate")
            data = resp.json()
            assert data["valid"] is True  # mismatched types are warnings, not errors
            assert any("task_list" in w and "accepts" in w for w in data["warnings"])
        finally:
            shutil.rmtree(tmpdir)

    def test_full_crud_lifecycle(self):
        """Test complete edge lifecycle: add -> update -> delete."""
        pc = PipelineConfig(id="p1", start_agent="pm")
        roles = {
            "pm": RoleConfig(role="pm", display_name="PM", prefix="PM",
                             color="#f00", emoji="P", system_prompt="PM."),
            "arch": RoleConfig(role="arch", display_name="Arch", prefix="AR",
                               color="#0f0", emoji="A", system_prompt="Arch."),
        }
        tmpdir = tempfile.mkdtemp()
        team_yaml = RealPath(tmpdir) / "config" / "team.yaml"
        team_yaml.parent.mkdir(parents=True)
        team_yaml.write_text(yaml.dump({"team_name": "Test"}))
        try:
            app = _make_test_app(roles, pc, project_dir=tmpdir)
            client = TestClient(app)

            # 1. Add edge
            resp = client.post("/api/pipeline/edges", json={
                "from_agent": "pm", "to_agent": "arch", "task_types": ["design"],
            })
            edge_id = resp.json()["edge_id"]

            # 2. Update edge
            resp = client.put(f"/api/pipeline/edges/{edge_id}", json={
                "task_types": ["design", "review"],
                "on_failure": "continue_partial",
            })
            assert resp.status_code == 200

            # Verify update
            resp = client.get("/api/pipeline")
            edge = resp.json()["edges"][0]
            assert edge["task_types"] == ["design", "review"]
            assert edge["on_failure"] == "continue_partial"

            # 3. Set node config
            resp = client.put("/api/pipeline/node-config/arch", json={
                "join_strategy": "stream",
            })
            assert resp.status_code == 200

            # 4. Delete edge
            resp = client.delete(f"/api/pipeline/edges/{edge_id}")
            assert resp.status_code == 200
            resp = client.get("/api/pipeline")
            assert len(resp.json()["edges"]) == 0

            # 5. Verify persisted to YAML
            with open(team_yaml) as f:
                saved = yaml.safe_load(f)
            assert "pipeline" in saved
            assert saved["pipeline"]["start_agent"] == "pm"
        finally:
            shutil.rmtree(tmpdir)

    def test_pipeline_validation_blocks_save_without_start(self):
        """Pipeline validation returns valid=False when no start agent is set."""
        pc = PipelineConfig(
            id="p1", start_agent=None,
            edges=[PipelineEdge(id="e1", from_agent="a", to_agent="b")],
        )
        roles = {
            "a": RoleConfig(role="a", display_name="A", prefix="AA",
                            color="#f00", emoji="A", system_prompt="A."),
            "b": RoleConfig(role="b", display_name="B", prefix="BB",
                            color="#0f0", emoji="B", system_prompt="B."),
        }
        app = _make_test_app(roles, pc)
        client = TestClient(app)

        resp = client.post("/api/pipeline/validate")
        data = resp.json()
        assert data["valid"] is False
        assert len(data["errors"]) > 0
```

- [ ] **Step 2: Run all tests**

Run: `cd "/Users/nikhilchatragadda/Personal Projects/taskbrew" && .venv/bin/pytest tests/test_pipeline_editor.py -v`
Expected: All tests PASS.

- [ ] **Step 3: Run full existing test suite to check for regressions**

Run: `cd "/Users/nikhilchatragadda/Personal Projects/taskbrew" && .venv/bin/pytest tests/ -x --timeout=60 2>&1 | tail -30`
Expected: No regressions. If existing tests that check `routes_to` behavior fail because the pipeline editor is now modifying state, fix the test setup to initialize pipeline state.

- [ ] **Step 4: Commit**

```bash
git add tests/test_pipeline_editor.py
git commit -m "test: add integration tests for pipeline editor full lifecycle"
```

---

## Summary of All Changes

| File | Change Type | Description |
|------|-------------|-------------|
| `src/taskbrew/config_loader.py` | Modified | Add `PipelineEdge`, `PipelineNodeConfig`, `PipelineConfig` dataclasses; `load_pipeline()`, `save_pipeline()`, `migrate_routes_to_pipeline()` |
| `src/taskbrew/dashboard/routers/pipeline_editor.py` | **New** | CRUD API router: GET/PUT pipeline, POST/DELETE/PUT edges, PUT start-agent, PUT node-config, POST validate |
| `src/taskbrew/dashboard/models.py` | Modified | Add `PipelineEdgeBody`, `UpdatePipelineEdgeBody`, `UpdatePipelineBody`, `SetStartAgentBody`, `SetNodeConfigBody`, `ValidatePipelineBody` |
| `src/taskbrew/dashboard/app.py` | Modified | Register `pipeline_editor` router; initialize pipeline from team.yaml with auto-migration |
| `src/taskbrew/dashboard/routers/system.py` | Modified | Call `_cleanup_role_from_pipeline()` in `delete_role()` |
| `src/taskbrew/dashboard/static/js/settings.js` | Modified | Add `pipelineData` state; update `loadSettings()` to fetch `/api/pipeline`; update `computeGraphLayout()` to use pipeline edges; update `renderPipeline()` for interactive nodes (selection, start badge, dashed unconnected, edge delete buttons); add `attachPipelineDragHandlers()` with click-to-connect and context menu; add `showEdgePopover()`, `showNodePopover()`, `showPipelineContextMenu()`; add undo/redo; add `renderPipelineValidation()`; update `saveAll()` to PUT `/api/pipeline`; update `addRouteFromPipeline()` and `removeRoute()` to use pipeline edges |
| `src/taskbrew/dashboard/static/css/settings.css` | Modified | Add styles for selected nodes, start agent badge, edge delete buttons, context menu, popovers, validation banners |
| `src/taskbrew/dashboard/templates/settings.html` | Modified | Add `pipelineValidation` container div; rename section header to "Pipeline" |
| `tests/test_pipeline_editor.py` | **New** | 30+ tests covering data model, load/save, migration, API CRUD, validation, integration |
| `config/team.yaml` | Schema | `pipeline` key auto-added at runtime by migration (no manual edit needed) |

## Execution Order

Tasks 1-4 are backend (can be parallelized: 1+2 together, then 3+4 together).
Tasks 5-8 are frontend (must be sequential: 5 -> 6 -> 7 -> 8).
Task 9 runs after all others.

Recommended execution:
1. **Task 1** (data model) + **Task 2** (migration) -- parallel
2. **Task 3** (API router) + **Task 4** (delete cleanup) -- parallel, depends on 1+2
3. **Task 5** (frontend data source) -- depends on 3
4. **Task 6** (interactive editing) -- depends on 5
5. **Task 7** (popovers) -- depends on 6
6. **Task 8** (validation indicators) -- depends on 7
7. **Task 9** (integration tests) -- depends on all
