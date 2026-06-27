"""Home redesign: cross-project summary aggregation + WS project routing.

Covers the two pieces of novel logic added for the aggregated home and scoped
detail pages:
  - ConnectionManager routes a project-tagged event only to connections scoped
    to that project (plus unscoped/home connections); untagged events reach all.
  - /api/projects/summary aggregates per-project counts/spend/recent + live
    totals across the running pool.
"""

import json
from unittest.mock import AsyncMock, MagicMock

from taskbrew.dashboard.app import ConnectionManager


class _FakeWS:
    def __init__(self):
        self.sent = []

    async def accept(self, subprotocol=None):
        pass

    async def send_text(self, msg):
        self.sent.append(json.loads(msg))


async def test_ws_event_routes_only_to_its_project_scope():
    cm = ConnectionManager()
    home, alpha, beta = _FakeWS(), _FakeWS(), _FakeWS()
    await cm.connect(home)                    # unscoped (aggregated home)
    await cm.connect(alpha, project="alpha")
    await cm.connect(beta, project="beta")

    await cm.broadcast({"type": "task.created", "_project": "alpha"})
    # alpha's own page + the unscoped home see it; beta does NOT.
    assert [m["type"] for m in alpha.sent] == ["task.created"]
    assert [m["type"] for m in home.sent] == ["task.created"]
    assert beta.sent == []


async def test_ws_untagged_event_reaches_all():
    cm = ConnectionManager()
    home, scoped = _FakeWS(), _FakeWS()
    await cm.connect(home)
    await cm.connect(scoped, project="alpha")
    await cm.broadcast({"type": "agent.text"})  # legacy/test path, no _project
    assert home.sent and scoped.sent


async def test_ws_disconnect_stops_delivery():
    cm = ConnectionManager()
    ws = _FakeWS()
    await cm.connect(ws, project="x")
    await cm.disconnect(ws)
    await cm.broadcast({"type": "t", "_project": "x"})
    assert ws.sent == []


async def test_projects_summary_aggregates_running_project():
    from taskbrew.dashboard.routers import system as system_router
    from taskbrew.dashboard.routers._deps import (
        register_orchestrator, unregister_orchestrator,
    )

    db = MagicMock()
    db.execute_fetchall = AsyncMock(side_effect=[
        [{"status": "completed", "c": 3}, {"status": "in_progress", "c": 1}],
        [{"id": "T1", "title": "Do", "assigned_to": "coder",
          "status": "completed", "created_at": "2026-01-01",
          "completed_at": "2026-01-02"}],
    ])
    db.execute_fetchone = AsyncMock(side_effect=[{"s": 1.5}, {"c": 2}])
    board = MagicMock()
    board._db = db
    orch = MagicMock()
    orch.project_id = "alpha"
    orch.task_board = board
    orch.instance_manager.get_all_instances = AsyncMock(
        return_value=[{"status": "working"}, {"status": "idle"}]
    )
    orch.agent_question_manager.get_pending = AsyncMock(return_value=[{"q": 1}])

    register_orchestrator(orch)
    pm = MagicMock()
    pm.list_projects.return_value = [
        {"id": "alpha", "name": "Alpha", "directory": "/x"}
    ]
    system_router.set_project_deps(pm, AsyncMock(), None)
    try:
        out = await system_router.projects_summary()
    finally:
        unregister_orchestrator("alpha")

    T = out["totals"]
    assert T["projects"] == 1 and T["running"] == 1
    assert T["tasks_total"] == 4        # 3 completed + 1 in_progress
    assert T["spend_usd"] == 1.5
    assert T["agents_working"] == 1     # one instance 'working'
    assert T["needs_you"] == 1          # one pending question
    assert out["recent"][0]["project_name"] == "Alpha"
