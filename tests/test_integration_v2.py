import pytest
from taskbrew.orchestrator.database import Database
from taskbrew.orchestrator.task_board import TaskBoard
from taskbrew.orchestrator.event_bus import EventBus

@pytest.fixture
async def system(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    await db.initialize()
    board = TaskBoard(db, group_prefixes={"pm": "FEAT", "architect": "DEBT"})
    await board.register_prefixes({
        "pm": "PM", "architect": "AR", "coder": "CD",
        "tester": "TS", "reviewer": "RV"
    })
    event_bus = EventBus()
    yield {"db": db, "board": board, "event_bus": event_bus}
    await db.close()

@pytest.mark.asyncio
async def test_full_task_flow(system):
    board = system["board"]

    # 1. Human submits goal -> PM task
    group = await board.create_group(title="Add dark mode", origin="pm", created_by="human")
    pm_task = await board.create_task(
        group_id=group["id"], title="Create PRD for dark mode",
        task_type="goal", assigned_to="pm", created_by="human",
    )
    assert pm_task["status"] == "pending"

    # 2. PM claims and completes -> creates architect task
    claimed = await board.claim_task("pm", "pm-1")
    assert claimed["id"] == pm_task["id"]
    await board.complete_task(pm_task["id"])
    ar_task = await board.create_task(
        group_id=group["id"], title="Design theme architecture",
        task_type="tech_design", assigned_to="architect", created_by="pm-1",
        parent_id=pm_task["id"],
    )

    # 3. Architect completes -> creates coder task
    await board.claim_task("architect", "architect-1")
    await board.complete_task(ar_task["id"])
    cd_task = await board.create_task(
        group_id=group["id"], title="Implement CSS variables",
        task_type="implementation", assigned_to="coder", created_by="architect-1",
        parent_id=ar_task["id"],
    )

    # 4. Coder completes -> creates tester (pending) + reviewer (blocked)
    await board.claim_task("coder", "coder-1")
    await board.complete_task(cd_task["id"])
    ts_task = await board.create_task(
        group_id=group["id"], title="Test CSS variables",
        task_type="qa_verification", assigned_to="tester", created_by="coder-1",
        parent_id=cd_task["id"],
    )
    rv_task = await board.create_task(
        group_id=group["id"], title="Review CSS variables",
        task_type="code_review", assigned_to="reviewer", created_by="coder-1",
        parent_id=cd_task["id"], blocked_by=[ts_task["id"]],
    )
    assert rv_task["status"] == "blocked"

    # 5. Tester completes -> reviewer unblocks
    await board.claim_task("tester", "tester-1")
    await board.complete_task(ts_task["id"])
    rv_updated = await board.get_task(rv_task["id"])
    assert rv_updated["status"] == "pending"

    # 6. Reviewer completes -> done
    await board.claim_task("reviewer", "reviewer-1")
    await board.complete_task(rv_task["id"])

    # Verify all tasks completed with same group
    all_tasks = await board.get_group_tasks(group["id"])
    assert len(all_tasks) == 5
    assert all(t["status"] == "completed" for t in all_tasks)
    assert all(t["group_id"] == group["id"] for t in all_tasks)

@pytest.mark.asyncio
async def test_rejection_flow(system):
    board = system["board"]

    group = await board.create_group(title="Feature X", origin="pm", created_by="human")
    cd_task = await board.create_task(
        group_id=group["id"], title="Implement X",
        task_type="implementation", assigned_to="coder", created_by="architect-1",
    )
    await board.claim_task("coder", "coder-1")
    await board.complete_task(cd_task["id"])

    rv_task = await board.create_task(
        group_id=group["id"], title="Review X",
        task_type="code_review", assigned_to="reviewer", created_by="coder-1",
        parent_id=cd_task["id"],
    )
    await board.claim_task("reviewer", "reviewer-1")
    await board.reject_task(rv_task["id"], reason="Missing error handling")

    # Reviewer creates revision task back to coder
    revision = await board.create_task(
        group_id=group["id"], title="Fix: Missing error handling",
        task_type="revision", assigned_to="coder", created_by="reviewer-1",
        parent_id=rv_task["id"], revision_of=cd_task["id"],
    )
    assert revision["status"] == "pending"
    assert revision["revision_of"] == cd_task["id"]

    original = await board.get_task(rv_task["id"])
    assert original["status"] == "rejected"

@pytest.mark.asyncio
async def test_parallel_groups(system):
    """Two groups running independently at the same time."""
    board = system["board"]

    g1 = await board.create_group(title="Feature A", origin="pm", created_by="human")
    g2 = await board.create_group(title="Tech Debt B", origin="architect", created_by="architect-1")

    await board.create_task(group_id=g1["id"], title="PRD A", task_type="goal", assigned_to="pm", created_by="human")
    await board.create_task(group_id=g2["id"], title="Fix B", task_type="tech_debt", assigned_to="coder", created_by="architect-1")

    # Verify independent groups
    g1_tasks = await board.get_group_tasks(g1["id"])
    g2_tasks = await board.get_group_tasks(g2["id"])
    assert len(g1_tasks) == 1
    assert len(g2_tasks) == 1
    assert g1_tasks[0]["group_id"] != g2_tasks[0]["group_id"]

    # Verify board filtering
    board_g1 = await board.get_board(group_id=g1["id"])
    total_g1 = sum(len(v) for v in board_g1.values())
    assert total_g1 == 1
