"""End-to-end concurrent-callback database isolation.

This is the proof of the feature, not a unit of it: two orchestrators, each
backed by its *own* real Database/TaskBoard, are registered in the _deps pool.
Requests flow through the real ``X-Taskbrew-Project`` header -> contextvar ->
``get_orch_optional`` resolution path and the real ``mcp_tools.record_check``
handler. It demonstrates that an agent callback scoped to project *beta* writes
beta's database and never the *focused* project alpha's -- the exact silent
cross-write that concurrent orchestrators exist to prevent.

The server half (header -> contextvar -> resolution) is also covered in
``test_concurrent_project_scope.py`` with fakes; here it runs against real
databases through a real callback so a broken threading can't hide behind a
stub.
"""

import json

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from taskbrew.dashboard.routers._deps import (
    get_orch_optional,
    register_orchestrator,
    reset_current_project,
    set_current_project,
    unregister_orchestrator,
)
from taskbrew.dashboard.routers.mcp_tools import router, set_mcp_deps
from taskbrew.orchestrator.database import Database
from taskbrew.orchestrator.event_bus import EventBus
from taskbrew.orchestrator.task_board import TaskBoard


class _Orch:
    """Carries exactly the surface ``record_check`` resolves through the
    orchestrator getter: a ``project_id`` and a real ``task_board`` (with its
    own DB) plus ``event_bus``. Mirrors the real Orchestrator's attributes so
    resolution doesn't silently fall back to a global."""

    def __init__(self, project_id, task_board, event_bus):
        self.project_id = project_id
        self.task_board = task_board
        self.event_bus = event_bus
        self.interaction_manager = None


async def _make_project(project_id):
    db = Database(":memory:")
    await db.initialize()
    event_bus = EventBus()
    board = TaskBoard(db, event_bus=event_bus)
    group = await board.create_group(title="G", origin="pm", created_by="human")
    task = await board.create_task(
        group_id=group["id"],
        title="Impl",
        task_type="bug_fix",
        assigned_to="coder",
        created_by="human",
    )
    return {
        "db": db,
        "board": board,
        "orch": _Orch(project_id, board, event_bus),
        "task_id": task["id"],
    }


async def _checks_in(db, task_id):
    row = await db.execute_fetchone(
        "SELECT completion_checks FROM tasks WHERE id = ?", (task_id,)
    )
    if row is None:
        return None
    raw = row.get("completion_checks") or "{}"
    return json.loads(raw) if isinstance(raw, str) else (raw or {})


@pytest.fixture
async def two_projects():
    a = await _make_project("alpha")
    b = await _make_project("beta")
    # alpha is focused (registered first, focus=True); beta is NOT focused, so a
    # missing or ignored header would resolve to alpha -- precisely the
    # cross-write this test must catch.
    register_orchestrator(a["orch"], focus=True)
    register_orchestrator(b["orch"], focus=False)
    # Mirror production wiring exactly: app.create_app passes the BOOT/focused
    # project's board+bus as the global fallback (app.py). Setting the fallback
    # to alpha's board makes the cross-write test honest -- a beta-headed
    # callback must reach beta's board THROUGH the getter, not silently fall
    # back to this alpha global.
    set_mcp_deps(
        interaction_mgr=None,
        pipeline_getter=None,
        task_board=a["board"],
        auth_manager=None,
        event_bus=a["orch"].event_bus,
        orchestrator_getter=get_orch_optional,
    )
    app = FastAPI()

    @app.middleware("http")
    async def _project_scope(request, call_next):
        # Mirrors app.create_app's real _project_scope middleware: bind the
        # request-scoped project from the header, reset after.
        token = set_current_project(request.headers.get("X-Taskbrew-Project"))
        try:
            return await call_next(request)
        finally:
            reset_current_project(token)

    app.include_router(router)
    try:
        yield {"app": app, "a": a, "b": b}
    finally:
        unregister_orchestrator(a["orch"])
        unregister_orchestrator(b["orch"])
        await a["db"].close()
        await b["db"].close()


async def _record_check(client, *, task_id, check_name, project_header):
    headers = {"Authorization": "Bearer test-token"}
    if project_header is not None:
        headers["X-Taskbrew-Project"] = project_header
    return await client.post(
        "/mcp/tools/record_check",
        json={"task_id": task_id, "check_name": check_name, "status": "pass"},
        headers=headers,
    )


async def test_scoped_callbacks_write_only_their_own_project_db(two_projects):
    """A check posted with header=alpha lands in alpha's DB and a check posted
    with header=beta lands in beta's DB -- never the other, even though alpha is
    both the focused project AND the global-fallback board."""
    env = two_projects
    a, b = env["a"], env["b"]
    transport = ASGITransport(app=env["app"])
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        ra = await _record_check(
            c, task_id=a["task_id"], check_name="buildA", project_header="alpha"
        )
        rb = await _record_check(
            c, task_id=b["task_id"], check_name="buildB", project_header="beta"
        )

    assert ra.status_code == 200, ra.text
    assert rb.status_code == 200, rb.text

    a_checks = await _checks_in(a["db"], a["task_id"])
    b_checks = await _checks_in(b["db"], b["task_id"])

    # Each check landed in its OWN project's DB, and ONLY there. set-equality is
    # razor sharp: a broken scope (beta header ignored -> alpha global fallback)
    # would put buildB in alpha (or 404 on beta's task), failing one of these.
    assert set(a_checks) == {"buildA"}
    assert set(b_checks) == {"buildB"}


async def test_headerless_callback_follows_focused_project(two_projects):
    """A request with no X-Taskbrew-Project header is the human-dashboard case:
    it resolves the focused project (alpha) -- not beta. Documents the intended
    fallback that the scoped tests deliberately override."""
    env = two_projects
    a, b = env["a"], env["b"]
    transport = ASGITransport(app=env["app"])
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await _record_check(
            c, task_id=a["task_id"], check_name="focusedcheck", project_header=None
        )

    assert resp.status_code == 200, resp.text
    # Landed in the focused project (alpha); beta untouched.
    assert set(await _checks_in(a["db"], a["task_id"])) == {"focusedcheck"}
    assert set(await _checks_in(b["db"], b["task_id"])) == set()


async def test_stale_or_unknown_header_never_cross_writes(two_projects):
    """An explicit-but-unresolved project header (e.g. a fire-and-forget agent
    callback that arrives just after its project was stopped/unregistered) must
    NOT fall back to the global/focused board. Strict resolution returns no
    board -> handler 503 -> neither database is touched. This is the headline
    promise of concurrent orchestrators: a stale header never cross-writes."""
    env = two_projects
    a, b = env["a"], env["b"]
    transport = ASGITransport(app=env["app"])
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await _record_check(
            c, task_id=a["task_id"], check_name="ghost", project_header="stopped-project"
        )

    # No live board resolved for the stale header -> safe refusal, not a write.
    assert resp.status_code == 503, resp.text
    assert set(await _checks_in(a["db"], a["task_id"])) == set()
    assert set(await _checks_in(b["db"], b["task_id"])) == set()
