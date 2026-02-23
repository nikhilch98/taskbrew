# tests/test_task_queue.py
import pytest
from pathlib import Path
from ai_team.orchestrator.task_queue import TaskQueue, TaskStatus


@pytest.fixture
async def queue(tmp_path):
    q = TaskQueue(db_path=tmp_path / "test.db")
    await q.initialize()
    yield q
    await q.close()


async def test_create_task(queue):
    task_id = await queue.create_task(
        pipeline_id="pipe-1",
        task_type="implement",
        input_context="Build auth module",
    )
    assert task_id is not None
    task = await queue.get_task(task_id)
    assert task["status"] == TaskStatus.PENDING
    assert task["input_context"] == "Build auth module"


async def test_assign_task(queue):
    task_id = await queue.create_task(
        pipeline_id="pipe-1",
        task_type="implement",
        input_context="Build auth",
    )
    await queue.assign_task(task_id, "coder")
    task = await queue.get_task(task_id)
    assert task["status"] == TaskStatus.ASSIGNED
    assert task["assigned_to"] == "coder"


async def test_update_status(queue):
    task_id = await queue.create_task(
        pipeline_id="pipe-1",
        task_type="review",
        input_context="Review PR",
    )
    await queue.assign_task(task_id, "reviewer")
    await queue.update_status(task_id, TaskStatus.IN_PROGRESS)
    task = await queue.get_task(task_id)
    assert task["status"] == TaskStatus.IN_PROGRESS


async def test_complete_task_with_artifact(queue):
    task_id = await queue.create_task(
        pipeline_id="pipe-1",
        task_type="research",
        input_context="Research auth patterns",
    )
    await queue.complete_task(task_id, output_artifact="artifacts/1/research.md")
    task = await queue.get_task(task_id)
    assert task["status"] == TaskStatus.COMPLETED
    assert task["output_artifact"] == "artifacts/1/research.md"


async def test_get_pending_tasks(queue):
    await queue.create_task(pipeline_id="p1", task_type="a", input_context="ctx")
    t2 = await queue.create_task(pipeline_id="p1", task_type="b", input_context="ctx")
    await queue.assign_task(t2, "coder")
    pending = await queue.get_pending_tasks()
    assert len(pending) == 1
    assert pending[0]["task_type"] == "a"


async def test_get_tasks_by_pipeline(queue):
    await queue.create_task(pipeline_id="p1", task_type="a", input_context="ctx")
    await queue.create_task(pipeline_id="p2", task_type="b", input_context="ctx")
    tasks = await queue.get_tasks_by_pipeline("p1")
    assert len(tasks) == 1


async def test_fail_task(queue):
    task_id = await queue.create_task(
        pipeline_id="p1", task_type="test", input_context="Run tests"
    )
    await queue.fail_task(task_id, error="Tests failed: 3 failures")
    task = await queue.get_task(task_id)
    assert task["status"] == TaskStatus.FAILED
    assert "3 failures" in task["error"]
