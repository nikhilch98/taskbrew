"""Integration tests verifying the full orchestrator wiring."""
import pytest
import asyncio
from pathlib import Path
from ai_team.main import build_orchestrator


@pytest.fixture
async def orch(tmp_path):
    pipelines_dir = tmp_path / "pipelines"
    pipelines_dir.mkdir()
    (pipelines_dir / "test.yaml").write_text("""
name: test_pipeline
description: Test pipeline
steps:
  - agent: researcher
    action: research
    description: Research the topic
  - agent: coder
    action: implement
    description: Implement the solution
""")
    o = await build_orchestrator(project_dir=tmp_path)
    yield o
    await o.shutdown()


async def test_full_wiring(orch):
    assert orch.event_bus is not None
    assert orch.task_queue is not None
    assert orch.team_manager is not None
    assert orch.workflow_engine is not None


async def test_pipeline_loaded(orch):
    assert "test_pipeline" in orch.workflow_engine.pipelines


async def test_team_spawn_and_status(orch):
    orch.team_manager.spawn_default_team()
    status = orch.team_manager.get_team_status()
    assert len(status) == 6
    assert all(s == "idle" for s in status.values())


async def test_task_creation_and_events(orch):
    events_received = []

    async def capture(event):
        events_received.append(event)

    orch.event_bus.subscribe("*", capture)
    task_id = await orch.task_queue.create_task(
        pipeline_id="run-1", task_type="research", input_context="Test context"
    )
    await orch.event_bus.emit("task_created", {"task_id": task_id})
    await asyncio.sleep(0.01)
    assert len(events_received) >= 1


async def test_workflow_start_creates_task(orch):
    run = orch.workflow_engine.start_run(
        "test_pipeline", "run-1", initial_context={"goal": "Test"}
    )
    step = orch.workflow_engine.get_current_step("run-1")
    assert step is not None
    assert step.agent == "researcher"
    assert step.action == "research"
