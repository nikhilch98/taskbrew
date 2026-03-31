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
