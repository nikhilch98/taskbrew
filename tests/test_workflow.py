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

def test_retry_increments_count_on_failure():
    """When a step fails, retry_count increments and step stays current."""
    engine = WorkflowEngine()
    engine.register_pipeline(Pipeline(name="retry-test", description="test", steps=[
        PipelineStep(agent="coder", action="implement", description="Code it", max_retries=3)
    ]))
    run = engine.start_run("retry-test", run_id="r1")
    step = engine.get_current_step("r1")
    engine.fail_step("r1")
    assert step.retry_count == 1
    assert run.status == "running"
    assert run.current_step == 0

def test_retry_exhausted_marks_run_failed():
    """After max_retries, run status becomes failed."""
    engine = WorkflowEngine()
    engine.register_pipeline(Pipeline(name="retry-exhaust", description="test", steps=[
        PipelineStep(agent="coder", action="implement", description="Code it", max_retries=2)
    ]))
    run = engine.start_run("retry-exhaust", run_id="r2")
    engine.fail_step("r2")  # retry 1
    engine.fail_step("r2")  # retry 2 — exhausted
    assert run.status == "failed"

def test_successful_advance_resets_retry_count():
    """Advancing past a step resets retry_count."""
    engine = WorkflowEngine()
    engine.register_pipeline(Pipeline(name="retry-success", description="test", steps=[
        PipelineStep(agent="coder", action="implement", description="Code it", max_retries=3),
        PipelineStep(agent="tester", action="test", description="Test it"),
    ]))
    run = engine.start_run("retry-success", run_id="r3")
    engine.fail_step("r3")  # retry 1
    step = engine.get_current_step("r3")
    assert step.retry_count == 1
    engine.advance_run("r3")  # succeeds this time
    assert step.retry_count == 0  # reset
    assert run.current_step == 1
    assert run.status == "running"
