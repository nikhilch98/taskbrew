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


async def test_save_and_load_run(tmp_path):
    """Pipeline run state persists to SQLite."""
    db_path = str(tmp_path / "test.db")
    engine = WorkflowEngine(db_path=db_path)
    await engine.initialize_db()
    engine.register_pipeline(Pipeline(name="persist-test", description="test", steps=[
        PipelineStep(agent="coder", action="code", description="Code it"),
        PipelineStep(agent="tester", action="test", description="Test it"),
    ]))
    run = engine.start_run("persist-test", run_id="p1")
    await engine.save_run("p1")

    # Create new engine (simulating restart)
    engine2 = WorkflowEngine(db_path=db_path)
    await engine2.initialize_db()
    engine2.register_pipeline(Pipeline(name="persist-test", description="test", steps=[
        PipelineStep(agent="coder", action="code", description="Code it"),
        PipelineStep(agent="tester", action="test", description="Test it"),
    ]))
    await engine2.load_runs()
    assert "p1" in engine2.active_runs
    assert engine2.active_runs["p1"].current_step == 0
    assert engine2.active_runs["p1"].status == "running"


async def test_advance_persists_state(tmp_path):
    """Advancing a run persists the new step index."""
    db_path = str(tmp_path / "test.db")
    engine = WorkflowEngine(db_path=db_path)
    await engine.initialize_db()
    engine.register_pipeline(Pipeline(name="advance-test", description="test", steps=[
        PipelineStep(agent="coder", action="code", description="Code"),
        PipelineStep(agent="tester", action="test", description="Test"),
    ]))
    engine.start_run("advance-test", run_id="a1")
    engine.advance_run("a1")
    await engine.save_run("a1")

    engine2 = WorkflowEngine(db_path=db_path)
    await engine2.initialize_db()
    engine2.register_pipeline(Pipeline(name="advance-test", description="test", steps=[
        PipelineStep(agent="coder", action="code", description="Code"),
        PipelineStep(agent="tester", action="test", description="Test"),
    ]))
    await engine2.load_runs()
    assert engine2.active_runs["a1"].current_step == 1


def test_checkpoint_step_pauses_run():
    engine = WorkflowEngine()
    engine.register_pipeline(Pipeline(name="checkpoint-test", description="test", steps=[
        PipelineStep(agent="coder", action="code", description="Code it"),
        PipelineStep(agent="reviewer", action="review", description="Review", requires_approval=True),
    ]))
    run = engine.start_run("checkpoint-test", run_id="c1")
    engine.advance_run("c1")  # Move to review step (which requires approval)
    assert run.status == "awaiting_approval"


def test_approve_checkpoint_resumes_run():
    engine = WorkflowEngine()
    engine.register_pipeline(Pipeline(name="approve-test", description="test", steps=[
        PipelineStep(agent="coder", action="code", description="Code"),
        PipelineStep(agent="reviewer", action="review", description="Review", requires_approval=True),
        PipelineStep(agent="tester", action="test", description="Test"),
    ]))
    run = engine.start_run("approve-test", run_id="a1")
    engine.advance_run("a1")  # Pauses at review
    assert run.status == "awaiting_approval"
    engine.approve_checkpoint("a1")
    assert run.status == "running"
    engine.advance_run("a1")  # Move to test
    assert run.current_step == 2


def test_reject_checkpoint_fails_run():
    engine = WorkflowEngine()
    engine.register_pipeline(Pipeline(name="reject-test", description="test", steps=[
        PipelineStep(agent="coder", action="code", description="Code it"),
        PipelineStep(agent="reviewer", action="review", description="Review", requires_approval=True),
    ]))
    run = engine.start_run("reject-test", run_id="r1")
    engine.advance_run("r1")  # Pauses at review
    engine.reject_checkpoint("r1", reason="Code quality issues")
    assert run.status == "failed"
    assert run.context.get("rejection_reason") == "Code quality issues"
