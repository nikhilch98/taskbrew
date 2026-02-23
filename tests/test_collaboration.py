"""Tests for collaboration features: comments, presence, activity, mentions."""

import pytest
from httpx import AsyncClient, ASGITransport

from taskbrew.orchestrator.database import Database
from taskbrew.orchestrator.task_board import TaskBoard
from taskbrew.orchestrator.event_bus import EventBus
from taskbrew.agents.instance_manager import InstanceManager


@pytest.fixture
async def collab_client(tmp_path):
    """Create a test client with collaboration router available."""
    # Reset the module-level _tables_created flag so each test gets fresh tables
    import taskbrew.dashboard.routers.collaboration as collab_mod
    collab_mod._tables_created = False

    db = Database(str(tmp_path / "collab_test.db"))
    await db.initialize()
    board = TaskBoard(db, group_prefixes={"pm": "FEAT"})
    await board.register_prefixes({"pm": "PM", "coder": "CD"})
    event_bus = EventBus()
    instance_mgr = InstanceManager(db)

    from taskbrew.dashboard.app import create_app

    app = create_app(
        event_bus=event_bus,
        task_board=board,
        instance_manager=instance_mgr,
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield {
            "client": client,
            "board": board,
            "db": db,
            "event_bus": event_bus,
        }
    await db.close()


# ---------------------------------------------------------------------------
# Comments CRUD
# ---------------------------------------------------------------------------


async def test_add_and_get_comments(collab_client):
    client = collab_client["client"]

    # Add a comment
    resp = await client.post(
        "/api/collaboration/comments/TASK-001",
        json={"author": "alice", "content": "Looks good!"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["task_id"] == "TASK-001"
    assert data["author"] == "alice"
    assert data["content"] == "Looks good!"
    assert "id" in data
    comment_id = data["id"]

    # Retrieve comments
    resp = await client.get("/api/collaboration/comments/TASK-001")
    assert resp.status_code == 200
    body = resp.json()
    assert body["task_id"] == "TASK-001"
    assert len(body["comments"]) == 1
    assert body["comments"][0]["id"] == comment_id
    assert body["comments"][0]["content"] == "Looks good!"


async def test_get_comments_empty(collab_client):
    client = collab_client["client"]
    resp = await client.get("/api/collaboration/comments/NONEXIST")
    assert resp.status_code == 200
    body = resp.json()
    assert body["comments"] == []


async def test_add_comment_empty_content_rejected(collab_client):
    client = collab_client["client"]
    resp = await client.post(
        "/api/collaboration/comments/TASK-001",
        json={"author": "bob", "content": "   "},
    )
    assert resp.status_code == 400


async def test_delete_comment(collab_client):
    client = collab_client["client"]

    # Create comment
    resp = await client.post(
        "/api/collaboration/comments/TASK-002",
        json={"author": "alice", "content": "Remove me"},
    )
    assert resp.status_code == 200
    comment_id = resp.json()["id"]

    # Delete
    resp = await client.delete(f"/api/collaboration/comments/TASK-002/{comment_id}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "deleted"

    # Verify gone
    resp = await client.get("/api/collaboration/comments/TASK-002")
    assert len(resp.json()["comments"]) == 0


async def test_delete_nonexistent_comment(collab_client):
    client = collab_client["client"]
    resp = await client.delete("/api/collaboration/comments/TASK-001/nonexistent")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Activity feed
# ---------------------------------------------------------------------------


async def test_activity_feed(collab_client):
    client = collab_client["client"]

    # Activity starts empty
    resp = await client.get("/api/collaboration/activity")
    assert resp.status_code == 200
    assert resp.json()["activity"] == []

    # Adding a comment creates activity
    await client.post(
        "/api/collaboration/comments/TASK-010",
        json={"author": "charlie", "content": "Testing activity"},
    )

    resp = await client.get("/api/collaboration/activity")
    assert resp.status_code == 200
    activities = resp.json()["activity"]
    assert len(activities) == 1
    assert activities[0]["actor"] == "charlie"
    assert activities[0]["action"] == "comment"
    assert activities[0]["target_id"] == "TASK-010"


# ---------------------------------------------------------------------------
# Presence
# ---------------------------------------------------------------------------


async def test_presence_heartbeat_and_list(collab_client):
    client = collab_client["client"]

    # Initially no one online
    resp = await client.get("/api/collaboration/presence")
    assert resp.status_code == 200
    assert resp.json()["online"] == []

    # Heartbeat
    resp = await client.post("/api/collaboration/presence/alice")
    assert resp.status_code == 200
    assert resp.json()["user_id"] == "alice"

    # Now alice should appear
    resp = await client.get("/api/collaboration/presence")
    assert resp.status_code == 200
    online = resp.json()["online"]
    assert len(online) >= 1
    assert any(u["user_id"] == "alice" for u in online)


# ---------------------------------------------------------------------------
# Mentions
# ---------------------------------------------------------------------------


async def test_create_and_get_mentions(collab_client):
    client = collab_client["client"]

    # Create mention
    resp = await client.post(
        "/api/collaboration/mentions",
        json={
            "author": "bob",
            "mentioned_user": "alice",
            "task_id": "TASK-005",
            "content": "Hey @alice, can you review this?",
        },
    )
    assert resp.status_code == 200
    mention = resp.json()
    assert mention["author"] == "bob"
    assert mention["mentioned_user"] == "alice"
    mention_id = mention["id"]

    # Get mentions for alice
    resp = await client.get("/api/collaboration/mentions/alice")
    assert resp.status_code == 200
    mentions = resp.json()["mentions"]
    assert len(mentions) == 1
    assert mentions[0]["id"] == mention_id

    # Mark as read
    resp = await client.post(f"/api/collaboration/mentions/{mention_id}/read")
    assert resp.status_code == 200

    # Unread filter
    resp = await client.get("/api/collaboration/mentions/alice?unread_only=true")
    assert resp.status_code == 200
    assert len(resp.json()["mentions"]) == 0


async def test_mention_empty_content_rejected(collab_client):
    client = collab_client["client"]
    resp = await client.post(
        "/api/collaboration/mentions",
        json={
            "author": "bob",
            "mentioned_user": "alice",
            "content": "   ",
        },
    )
    assert resp.status_code == 400


async def test_mention_creates_activity(collab_client):
    client = collab_client["client"]

    await client.post(
        "/api/collaboration/mentions",
        json={
            "author": "dave",
            "mentioned_user": "eve",
            "content": "Check this out",
        },
    )

    resp = await client.get("/api/collaboration/activity")
    activities = resp.json()["activity"]
    mention_activities = [a for a in activities if a["action"] == "mention"]
    assert len(mention_activities) >= 1
    assert mention_activities[0]["actor"] == "dave"
