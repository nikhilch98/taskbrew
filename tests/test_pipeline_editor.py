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
    migrate_routes_to_pipeline,
    RoleConfig,
    RouteTarget,
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


# ---------------------------------------------------------------------------
# Pipeline API Tests (Task 3)
# ---------------------------------------------------------------------------

import json
import tempfile
import shutil
from unittest.mock import MagicMock
from fastapi.testclient import TestClient
from pathlib import Path as RealPath


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
                    for w in data.get("infos", []))


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
