from __future__ import annotations

import argparse
from types import SimpleNamespace

import pytest

from taskbrew import main as taskbrew_main
import taskbrew.project_manager as project_manager_module


@pytest.mark.asyncio
async def test_serve_auto_resumes_saved_project_once(monkeypatch):
    class FakeProjectManager:
        def __init__(self) -> None:
            self.orchestrator = SimpleNamespace(shutdown=lambda: None)
            self.consumed: list[str] = []

        def get_active(self):
            return {"id": "dailyvox-v13"}

        def should_auto_resume_on_activate(self, project_id: str) -> bool:
            return project_id == "dailyvox-v13"

        async def activate_project(self, project_id: str):
            assert project_id == "dailyvox-v13"
            return self.orchestrator

        def mark_auto_resume_consumed(self, project_id: str) -> None:
            self.consumed.append(project_id)

    fake_pm = FakeProjectManager()
    seen: dict[str, object] = {}

    async def fake_run_server(project_manager, *, start_paused=True, auto_resume_project_id=None):
        seen["project_manager"] = project_manager
        seen["start_paused"] = start_paused
        seen["auto_resume_project_id"] = auto_resume_project_id
        if auto_resume_project_id and not start_paused:
            project_manager.mark_auto_resume_consumed(auto_resume_project_id)

    async def fake_shutdown():
        seen["shutdown"] = True

    fake_pm.orchestrator.shutdown = fake_shutdown
    monkeypatch.setattr(project_manager_module, "ProjectManager", lambda: fake_pm)
    monkeypatch.setattr(taskbrew_main, "run_server", fake_run_server)

    await taskbrew_main.async_main(argparse.Namespace(command="serve", project_dir=None))

    assert seen["project_manager"] is fake_pm
    assert seen["start_paused"] is False
    assert seen["auto_resume_project_id"] == "dailyvox-v13"
    assert fake_pm.consumed == ["dailyvox-v13"]
    assert seen["shutdown"] is True


@pytest.mark.asyncio
async def test_serve_keeps_saved_project_paused_after_auto_resume_consumed(monkeypatch):
    class FakeProjectManager:
        def __init__(self) -> None:
            self.orchestrator = SimpleNamespace(shutdown=lambda: None)

        def get_active(self):
            return {"id": "dailyvox-v13"}

        def should_auto_resume_on_activate(self, project_id: str) -> bool:
            assert project_id == "dailyvox-v13"
            return False

        async def activate_project(self, project_id: str):
            assert project_id == "dailyvox-v13"
            return self.orchestrator

        def mark_auto_resume_consumed(self, project_id: str) -> None:
            raise AssertionError(f"should not consume {project_id}")

    fake_pm = FakeProjectManager()
    seen: dict[str, object] = {}

    async def fake_run_server(project_manager, *, start_paused=True, auto_resume_project_id=None):
        seen["start_paused"] = start_paused
        seen["auto_resume_project_id"] = auto_resume_project_id

    async def fake_shutdown():
        seen["shutdown"] = True

    fake_pm.orchestrator.shutdown = fake_shutdown
    monkeypatch.setattr(project_manager_module, "ProjectManager", lambda: fake_pm)
    monkeypatch.setattr(taskbrew_main, "run_server", fake_run_server)

    await taskbrew_main.async_main(argparse.Namespace(command="serve", project_dir=None))

    assert seen["start_paused"] is True
    assert seen["auto_resume_project_id"] is None
    assert seen["shutdown"] is True
