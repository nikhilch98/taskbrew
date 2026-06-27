"""Read-only idle views for stopped projects (home-redesign Phase A).

A registered-but-not-running project must be resolvable so its board/detail
pages render from its own DB without starting it. These tests pin:
  - ensure_readonly_view builds+caches for a registered, stopped project,
  - it refuses (None) an unregistered id (so a stale header never fabricates
    a board — the strict cross-project-write guarantee),
  - the running orchestrator supersedes the idle view,
  - starting a project drops/closes its idle view,
  - the _deps resolver serves the idle view via the read-only getter, while an
    unregistered scoped header still resolves to None.
"""

from unittest.mock import AsyncMock, MagicMock, patch

from taskbrew.project_manager import ProjectManager, _slugify


def _registry(tmp_path, name, project_dir):
    """A ProjectManager whose registry holds one project pointing at a real
    (existing) directory, with nothing running."""
    pm = ProjectManager(registry_path=tmp_path / "projects.yaml")
    pid = _slugify(name)
    pm._write_registry({
        "projects": [{
            "id": pid,
            "name": name,
            "directory": str(project_dir),
            "created_at": "2026-01-01T00:00:00+00:00",
        }],
        "active_project": None,
    })
    return pm, pid


async def test_ensure_readonly_view_builds_and_caches_for_stopped_project(tmp_path):
    proj_dir = tmp_path / "proj"
    proj_dir.mkdir()
    pm, pid = _registry(tmp_path, "Demo Proj", proj_dir)

    fake_orch = MagicMock(name="orch")
    with patch(
        "taskbrew.main.build_orchestrator",
        new=AsyncMock(return_value=fake_orch),
    ) as build:
        view = await pm.ensure_readonly_view(pid)
        # built once, with the registry's dir + the registry id (identity invariant)
        assert view is fake_orch
        build.assert_awaited_once()
        assert build.await_args.kwargs["project_id"] == pid
        assert build.await_args.kwargs["project_dir"] == proj_dir
        # cached: a second call does not rebuild
        again = await pm.ensure_readonly_view(pid)
        assert again is fake_orch
        build.assert_awaited_once()

    # sync cache lookup used by the resolver finds it
    assert pm.get_readonly_cached(pid) is fake_orch
    # ...and it is NOT counted as running
    assert pm.is_running(pid) is False
    assert pid not in pm.running_ids()


async def test_ensure_readonly_view_refuses_unregistered_id(tmp_path):
    pm = ProjectManager(registry_path=tmp_path / "projects.yaml")
    pm._write_registry({"projects": [], "active_project": None})
    with patch(
        "taskbrew.main.build_orchestrator",
        new=AsyncMock(return_value=MagicMock()),
    ) as build:
        view = await pm.ensure_readonly_view("ghost-project")
    assert view is None
    build.assert_not_awaited()
    assert pm.get_readonly_cached("ghost-project") is None


async def test_running_orchestrator_supersedes_idle_view(tmp_path):
    proj_dir = tmp_path / "proj"
    proj_dir.mkdir()
    pm, pid = _registry(tmp_path, "Demo Proj", proj_dir)
    live = MagicMock(name="live")
    pm._orchestrators[pid] = live  # pretend it's running
    # ensure returns the live orch and never builds an idle view
    with patch(
        "taskbrew.main.build_orchestrator",
        new=AsyncMock(return_value=MagicMock()),
    ) as build:
        assert await pm.ensure_readonly_view(pid) is live
        build.assert_not_awaited()
    assert pm.get_readonly_cached(pid) is live


async def test_start_project_drops_idle_view(tmp_path):
    proj_dir = tmp_path / "proj"
    proj_dir.mkdir()
    pm, pid = _registry(tmp_path, "Demo Proj", proj_dir)

    idle = MagicMock(name="idle")
    idle.shutdown = AsyncMock()
    pm._readonly_views[pid] = idle

    live = MagicMock(name="live")
    live.project_id = pid
    with patch("taskbrew.main.build_orchestrator", new=AsyncMock(return_value=live)):
        with patch.object(pm, "_validate_project_dir", return_value=None):
            await pm.start_project(pid, focus=True)

    # idle view was closed and removed; the live orchestrator is the only one
    idle.shutdown.assert_awaited_once()
    assert pid not in pm._readonly_views
    assert pm.get_readonly_cached(pid) is live


def test_deps_resolver_serves_idle_view_and_refuses_unknown(tmp_path):
    from taskbrew.dashboard.routers import _deps

    proj_dir = tmp_path / "proj"
    proj_dir.mkdir()
    pm, pid = _registry(tmp_path, "Demo Proj", proj_dir)
    idle = MagicMock(name="idle")
    pm._readonly_views[pid] = idle

    _deps.set_readonly_getter(pm.get_readonly_cached)
    try:
        # Scoped to the stopped project -> idle view (its own DB), not None.
        tok = _deps.set_current_project(pid)
        try:
            assert _deps.get_orch_optional() is idle
        finally:
            _deps.reset_current_project(tok)
        # Scoped to an unregistered id -> still None (no cross-write fallback).
        tok = _deps.set_current_project("ghost-project")
        try:
            assert _deps.get_orch_optional() is None
        finally:
            _deps.reset_current_project(tok)
    finally:
        _deps.set_readonly_getter(None)
