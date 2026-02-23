# tests/test_workflow.py
import pytest
from ai_team.orchestrator.workflow import Pipeline, PipelineStep, WorkflowEngine

def test_pipeline_from_dict():
    data = {
        "name": "feature_dev",
        "description": "Feature development pipeline",
        "steps": [
            {"agent": "pm", "action": "decompose", "description": "Break down the goal"},
            {"agent": "researcher", "action": "research", "description": "Gather context"},
            {"agent": "coder", "action": "implement", "description": "Write code"},
        ],
    }
    pipeline = Pipeline.from_dict(data)
    assert pipeline.name == "feature_dev"
    assert len(pipeline.steps) == 3
    assert pipeline.steps[0].agent == "pm"
    assert pipeline.steps[2].agent == "coder"

def test_pipeline_step_order():
    pipeline = Pipeline(
        name="test", description="test",
        steps=[
            PipelineStep(agent="pm", action="plan", description="Plan"),
            PipelineStep(agent="coder", action="code", description="Code"),
        ],
    )
    assert pipeline.get_next_step(0) == pipeline.steps[1]
    assert pipeline.get_next_step(1) is None

def test_pipeline_from_yaml(tmp_path):
    yaml_content = """
name: bugfix
description: Bug fix pipeline
steps:
  - agent: researcher
    action: analyze
    description: Analyze the bug
  - agent: coder
    action: fix
    description: Fix the bug
"""
    yaml_file = tmp_path / "bugfix.yaml"
    yaml_file.write_text(yaml_content)
    pipeline = Pipeline.from_yaml(yaml_file)
    assert pipeline.name == "bugfix"
    assert len(pipeline.steps) == 2

def test_workflow_engine_registers_pipeline():
    engine = WorkflowEngine()
    pipeline = Pipeline(
        name="test", description="test",
        steps=[PipelineStep(agent="pm", action="plan", description="Plan")],
    )
    engine.register_pipeline(pipeline)
    assert "test" in engine.pipelines

def test_workflow_engine_load_from_directory(tmp_path):
    yaml_content = """
name: test_pipe
description: Test
steps:
  - agent: coder
    action: implement
    description: Code
"""
    (tmp_path / "test.yaml").write_text(yaml_content)
    engine = WorkflowEngine()
    engine.load_pipelines(tmp_path)
    assert "test_pipe" in engine.pipelines
