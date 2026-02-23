# tests/test_main.py
import pytest
from ai_team.main import build_orchestrator


async def test_build_orchestrator(tmp_path):
    orch = await build_orchestrator(project_dir=tmp_path)
    assert orch.team_manager is not None
    assert orch.task_queue is not None
    assert orch.event_bus is not None
    assert orch.workflow_engine is not None
    await orch.shutdown()
