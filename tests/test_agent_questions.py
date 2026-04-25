"""Tests for the structured ask_question flow.

Design:
docs/superpowers/specs/2026-04-25-agent-questions-design.md
"""

import asyncio
import json
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from taskbrew.config_loader import RoleConfig
from taskbrew.orchestrator.agent_questions import AgentQuestionManager
from taskbrew.orchestrator.database import Database
from taskbrew.orchestrator.event_bus import EventBus
from taskbrew.orchestrator.task_board import TaskBoard


def _role(
    name: str = "coder",
    *,
    clarification_mode: str = "auto",
    max_clarification_requests: int = 10,
) -> RoleConfig:
    return RoleConfig(
        role=name,
        display_name=name.title(),
        prefix=name[:2].upper(),
        color="#000",
        emoji="X",
        system_prompt="prompt",
        tools=["Read"],
        model="claude-sonnet-4-6",
        clarification_mode=clarification_mode,
        max_clarification_requests=max_clarification_requests,
    )


@pytest.fixture
async def env(tmp_path):
    db = Database(":memory:")
    await db.initialize()
    event_bus = EventBus()
    board = TaskBoard(db, event_bus=event_bus)
    qmgr = AgentQuestionManager(db, event_bus=event_bus)

    group = await board.create_group(
        title="G", origin="pm", created_by="human",
    )
    task = await board.create_task(
        group_id=group["id"], title="Impl",
        task_type="implementation", assigned_to="coder",
        created_by="human",
    )
    yield {
        "db": db,
        "event_bus": event_bus,
        "board": board,
        "qmgr": qmgr,
        "group_id": group["id"],
        "task_id": task["id"],
    }
    await db.close()


# ------------------------------------------------------------------
# AgentQuestionManager unit tests
# ------------------------------------------------------------------


async def test_auto_mode_records_agents_preferred_and_returns(env):
    """In auto mode, the manager records the agent's preferred answer
    with selected_by=agent and returns immediately."""
    qmgr = env["qmgr"]
    result = await qmgr.ask(
        task_id=env["task_id"], group_id=env["group_id"],
        agent_role="coder", instance_id="coder-1",
        question="Should I use SQLite or Postgres?",
        options=["SQLite", "Postgres"],
        preferred_answer="SQLite",
        reasoning="Single-process, no extra infra.",
        mode="auto",
    )
    assert result["status"] == "answered"
    assert result["selected_answer"] == "SQLite"
    assert result["selected_by"] == "agent"
    assert result["request_id"].startswith("qst-")

    row = await qmgr.get(result["request_id"])
    assert row["status"] == "resolved"
    assert row["selected_by"] == "agent"
    assert row["options"] == ["SQLite", "Postgres"]


async def test_manual_mode_blocks_until_user_answers(env):
    """Manual mode: the ask call sleeps until answer() fires; the row
    starts in 'pending', tasks.awaiting_input_since gets set, then both
    are reset on answer."""
    qmgr = env["qmgr"]
    db = env["db"]

    ask_task = asyncio.create_task(qmgr.ask(
        task_id=env["task_id"], group_id=env["group_id"],
        agent_role="architect", instance_id="architect-1",
        question="REST or GraphQL?",
        options=["REST", "GraphQL"],
        preferred_answer="REST",
        reasoning="Simpler tooling for this team.",
        mode="manual",
    ))
    # Give the manager a tick to register the wait + set awaiting_input.
    await asyncio.sleep(0.05)
    assert not ask_task.done()

    row = await db.execute_fetchone(
        "SELECT awaiting_input_since FROM tasks WHERE id = ?",
        (env["task_id"],),
    )
    assert row["awaiting_input_since"] is not None

    pending = await qmgr.get_pending()
    assert len(pending) == 1
    qid = pending[0]["id"]

    # Human answers.
    await qmgr.answer(qid, "GraphQL")

    result = await asyncio.wait_for(ask_task, timeout=2.0)
    assert result["status"] == "answered"
    assert result["selected_answer"] == "GraphQL"
    assert result["selected_by"] == "user"

    # awaiting_input_since cleared after resolve.
    row = await db.execute_fetchone(
        "SELECT awaiting_input_since FROM tasks WHERE id = ?",
        (env["task_id"],),
    )
    assert row["awaiting_input_since"] is None


async def test_cancel_for_task_wakes_pending_question_with_cancelled(env):
    """Cancelling the task wakes any blocked ask call with
    {status: cancelled} so the agent fails out cleanly."""
    qmgr = env["qmgr"]

    ask_task = asyncio.create_task(qmgr.ask(
        task_id=env["task_id"], group_id=env["group_id"],
        agent_role="coder", instance_id="coder-1",
        question="What now?",
        options=["A", "B"],
        preferred_answer="A",
        reasoning="reasoning",
        mode="manual",
    ))
    await asyncio.sleep(0.05)
    assert not ask_task.done()

    cancelled = await qmgr.cancel_for_task(env["task_id"])
    assert cancelled == 1

    result = await asyncio.wait_for(ask_task, timeout=2.0)
    assert result["status"] == "cancelled"
    assert result["selected_answer"] is None


async def test_validation_rejects_bad_inputs(env):
    qmgr = env["qmgr"]
    common = dict(
        task_id=env["task_id"], group_id=env["group_id"],
        agent_role="coder", instance_id="coder-1",
        question="Q?", reasoning="why", mode="auto",
    )
    with pytest.raises(ValueError, match="options must contain"):
        await qmgr.ask(
            options=["only one"], preferred_answer="only one", **common,
        )
    with pytest.raises(ValueError, match="preferred_answer must be one of"):
        await qmgr.ask(
            options=["A", "B"], preferred_answer="C", **common,
        )
    with pytest.raises(ValueError, match="duplicate option"):
        await qmgr.ask(
            options=["A", "A"], preferred_answer="A", **common,
        )


async def test_count_for_task_supports_budget(env):
    qmgr = env["qmgr"]
    for _ in range(3):
        await qmgr.ask(
            task_id=env["task_id"], group_id=env["group_id"],
            agent_role="coder", instance_id="coder-1",
            question="Q?", options=["A", "B"],
            preferred_answer="A", reasoning="r",
            mode="auto",
        )
    assert await qmgr.count_for_task(env["task_id"], "coder") == 3
    # Different role doesn't share the counter.
    assert await qmgr.count_for_task(env["task_id"], "architect") == 0


# ------------------------------------------------------------------
# MCP endpoint tests (ask_question)
# ------------------------------------------------------------------


@pytest.fixture
async def mcp_client(env):
    """FastAPI app with mcp_tools wired against env's TaskBoard +
    AgentQuestionManager."""
    orch = MagicMock()
    orch.roles = {
        "coder": _role("coder", clarification_mode="auto"),
        "architect": _role(
            "architect", clarification_mode="manual",
            max_clarification_requests=2,
        ),
    }
    orch.agent_question_manager = env["qmgr"]

    from taskbrew.dashboard.routers.mcp_tools import router, set_mcp_deps
    set_mcp_deps(
        interaction_mgr=None,
        pipeline_getter=None,
        task_board=env["board"],
        auth_manager=None,
        event_bus=env["event_bus"],
        orchestrator_getter=lambda: orch,
    )
    app = FastAPI()
    app.include_router(router)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c, env


async def test_ask_question_auto_role(mcp_client):
    """coder role has clarification_mode=auto: tool returns
    selected_by=agent immediately."""
    c, env = mcp_client
    resp = await c.post(
        "/mcp/tools/ask_question",
        json={
            "task_id": env["task_id"],
            "group_id": env["group_id"],
            "agent_role": "coder",
            "question": "x?",
            "options": ["A", "B"],
            "preferred_answer": "A",
            "reasoning": "because",
        },
        headers={"Authorization": "Bearer test-token"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["selected_by"] == "agent"
    assert body["selected_answer"] == "A"
    assert body["status"] == "answered"


async def test_ask_question_budget_enforced(mcp_client):
    """architect role has max_clarification_requests=2: third call
    returns 429 with budget-exhausted message."""
    c, env = mcp_client
    body_template = {
        "task_id": env["task_id"],
        "group_id": env["group_id"],
        "agent_role": "architect",
        "question": "x?",
        "options": ["A", "B"],
        "preferred_answer": "A",
        "reasoning": "because",
    }
    # architect is manual mode -> would block. Avoid that by
    # answering as we go.
    qmgr = env["qmgr"]
    # Pre-fill the manager directly to exhaust the budget without
    # blocking on async I/O.
    for _ in range(2):
        await qmgr.ask(
            task_id=env["task_id"], group_id=env["group_id"],
            agent_role="architect", instance_id=None,
            question="prefilled", options=["A", "B"],
            preferred_answer="A", reasoning="r", mode="auto",
        )
    # Now the third call should hit 429.
    resp = await c.post(
        "/mcp/tools/ask_question",
        json=body_template,
        headers={"Authorization": "Bearer test-token"},
    )
    assert resp.status_code == 429
    assert "budget exhausted" in resp.text.lower()


async def test_ask_question_validation_400(mcp_client):
    """Bad options or wrong preferred_answer -> 400 from the MCP layer."""
    c, env = mcp_client
    resp = await c.post(
        "/mcp/tools/ask_question",
        json={
            "task_id": env["task_id"],
            "group_id": env["group_id"],
            "agent_role": "coder",
            "question": "x?",
            "options": ["A", "B"],
            "preferred_answer": "NOT_IN_LIST",
            "reasoning": "because",
        },
        headers={"Authorization": "Bearer test-token"},
    )
    assert resp.status_code == 400


# ------------------------------------------------------------------
# Dashboard endpoint tests
# ------------------------------------------------------------------


@pytest.fixture
async def dash_client(env):
    """FastAPI app with the tasks router mounted against the env
    fixture's TaskBoard + AgentQuestionManager."""
    from taskbrew.dashboard.routers.tasks import router
    from taskbrew.dashboard.routers._deps import set_orchestrator

    orch = MagicMock()
    orch.task_board = env["board"]
    orch.event_bus = env["event_bus"]
    orch.agent_question_manager = env["qmgr"]
    orch.team_config = MagicMock()
    orch.team_config.artifacts_base_dir = "artifacts"
    orch.project_dir = "/tmp/test"
    set_orchestrator(orch)

    app = FastAPI()
    app.include_router(router)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c, env
    set_orchestrator(None)


async def test_pending_endpoint_returns_pending_questions(dash_client):
    c, env = dash_client
    qmgr = env["qmgr"]
    # Auto-resolved row should NOT appear in pending list.
    await qmgr.ask(
        task_id=env["task_id"], group_id=env["group_id"],
        agent_role="coder", instance_id="coder-1",
        question="resolved", options=["A", "B"],
        preferred_answer="A", reasoning="r", mode="auto",
    )
    # A pending one (start a manual ask but don't await).
    pending_task = asyncio.create_task(qmgr.ask(
        task_id=env["task_id"], group_id=env["group_id"],
        agent_role="architect", instance_id=None,
        question="waiting?", options=["yes", "no"],
        preferred_answer="yes", reasoning="r", mode="manual",
    ))
    await asyncio.sleep(0.05)

    resp = await c.get("/api/questions/pending")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1
    assert body["questions"][0]["question"] == "waiting?"

    # Cleanup: cancel the pending so the test fixture closes cleanly.
    await qmgr.cancel_for_task(env["task_id"])
    await asyncio.wait_for(pending_task, timeout=2.0)


async def test_answer_endpoint_resolves_and_wakes_agent(dash_client):
    c, env = dash_client
    qmgr = env["qmgr"]
    pending_task = asyncio.create_task(qmgr.ask(
        task_id=env["task_id"], group_id=env["group_id"],
        agent_role="architect", instance_id=None,
        question="REST or GraphQL?",
        options=["REST", "GraphQL"],
        preferred_answer="REST", reasoning="r", mode="manual",
    ))
    await asyncio.sleep(0.05)

    pending = (await c.get("/api/questions/pending")).json()
    qid = pending["questions"][0]["id"]

    resp = await c.post(
        f"/api/questions/{qid}/answer",
        json={"selected_answer": "GraphQL"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["selected_answer"] == "GraphQL"
    assert body["selected_by"] == "user"

    # Agent's blocked call returns with the user's answer.
    result = await asyncio.wait_for(pending_task, timeout=2.0)
    assert result["selected_answer"] == "GraphQL"
    assert result["selected_by"] == "user"


async def test_answer_endpoint_404_on_unknown_id(dash_client):
    c, _env = dash_client
    resp = await c.post(
        "/api/questions/qst-doesnotexist/answer",
        json={"selected_answer": "A"},
    )
    assert resp.status_code == 404


async def test_answer_endpoint_400_on_invalid_option(dash_client):
    c, env = dash_client
    qmgr = env["qmgr"]
    pending_task = asyncio.create_task(qmgr.ask(
        task_id=env["task_id"], group_id=env["group_id"],
        agent_role="architect", instance_id=None,
        question="Pick:", options=["A", "B"],
        preferred_answer="A", reasoning="r", mode="manual",
    ))
    await asyncio.sleep(0.05)
    pending = (await c.get("/api/questions/pending")).json()
    qid = pending["questions"][0]["id"]

    resp = await c.post(
        f"/api/questions/{qid}/answer",
        json={"selected_answer": "NOT_AN_OPTION"},
    )
    assert resp.status_code == 400

    # Cleanup
    await qmgr.cancel_for_task(env["task_id"])
    await asyncio.wait_for(pending_task, timeout=2.0)
