"""Collaboration features: comments, presence, activity feed, mentions."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

from taskbrew.dashboard.routers._deps import get_orch

router = APIRouter(prefix="/api/collaboration", tags=["Collaboration"])

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class AddCommentBody(BaseModel):
    author: str
    content: str


class MentionBody(BaseModel):
    author: str
    mentioned_user: str
    task_id: Optional[str] = None
    content: str


# ---------------------------------------------------------------------------
# Table initialisation (idempotent)
# ---------------------------------------------------------------------------

_tables_created = False


async def _ensure_tables():
    """Create collaboration tables if they do not exist yet."""
    global _tables_created
    if _tables_created:
        return
    orch = get_orch()
    db = orch.task_board._db
    await db.executescript("""
        CREATE TABLE IF NOT EXISTS collab_comments (
            id         TEXT PRIMARY KEY,
            task_id    TEXT NOT NULL,
            author     TEXT NOT NULL,
            content    TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_collab_comments_task
            ON collab_comments(task_id, created_at);

        CREATE TABLE IF NOT EXISTS collab_activity (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            actor      TEXT NOT NULL,
            action     TEXT NOT NULL,
            target_id  TEXT,
            detail     TEXT,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_collab_activity_time
            ON collab_activity(created_at);

        CREATE TABLE IF NOT EXISTS collab_presence (
            user_id      TEXT PRIMARY KEY,
            display_name TEXT,
            last_seen    TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS collab_mentions (
            id             TEXT PRIMARY KEY,
            author         TEXT NOT NULL,
            mentioned_user TEXT NOT NULL,
            task_id        TEXT,
            content        TEXT NOT NULL,
            read           INTEGER NOT NULL DEFAULT 0,
            created_at     TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_collab_mentions_user
            ON collab_mentions(mentioned_user, read);
    """)
    _tables_created = True


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _record_activity(db, actor: str, action: str, target_id: str | None = None, detail: str | None = None):
    """Insert an activity record."""
    await db.execute(
        "INSERT INTO collab_activity (actor, action, target_id, detail, created_at) VALUES (?, ?, ?, ?, ?)",
        (actor, action, target_id, detail, _utcnow()),
    )


# ---------------------------------------------------------------------------
# Presence
# ---------------------------------------------------------------------------


@router.get("/presence")
async def get_presence():
    """Return list of users seen within the last 5 minutes."""
    await _ensure_tables()
    orch = get_orch()
    db = orch.task_board._db
    rows = await db.execute_fetchall(
        "SELECT user_id, display_name, last_seen FROM collab_presence "
        "WHERE datetime(last_seen) >= datetime('now', '-5 minutes') "
        "ORDER BY last_seen DESC"
    )
    return {"online": rows}


@router.post("/presence/{user_id}")
async def heartbeat_presence(user_id: str):
    """Record or update presence for a user."""
    await _ensure_tables()
    orch = get_orch()
    db = orch.task_board._db
    now = _utcnow()
    await db.execute(
        "INSERT INTO collab_presence (user_id, display_name, last_seen) "
        "VALUES (?, ?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET last_seen = excluded.last_seen",
        (user_id, user_id, now),
    )
    return {"status": "ok", "user_id": user_id, "last_seen": now}


# ---------------------------------------------------------------------------
# Comments
# ---------------------------------------------------------------------------


@router.get("/comments/{task_id}")
async def get_comments(task_id: str):
    """Get all comments for a task."""
    await _ensure_tables()
    orch = get_orch()
    db = orch.task_board._db
    rows = await db.execute_fetchall(
        "SELECT id, task_id, author, content, created_at "
        "FROM collab_comments WHERE task_id = ? ORDER BY created_at ASC",
        (task_id,),
    )
    return {"task_id": task_id, "comments": rows}


@router.post("/comments/{task_id}")
async def add_comment(task_id: str, body: AddCommentBody):
    """Add a comment to a task."""
    await _ensure_tables()
    if not body.content.strip():
        raise HTTPException(400, "Comment content cannot be empty")
    orch = get_orch()
    db = orch.task_board._db
    comment_id = str(uuid.uuid4())[:12]
    now = _utcnow()
    await db.execute(
        "INSERT INTO collab_comments (id, task_id, author, content, created_at) VALUES (?, ?, ?, ?, ?)",
        (comment_id, task_id, body.author, body.content.strip(), now),
    )
    await _record_activity(db, body.author, "comment", task_id, body.content.strip()[:100])
    return {
        "id": comment_id,
        "task_id": task_id,
        "author": body.author,
        "content": body.content.strip(),
        "created_at": now,
    }


@router.delete("/comments/{task_id}/{comment_id}")
async def delete_comment(task_id: str, comment_id: str):
    """Delete a specific comment."""
    await _ensure_tables()
    orch = get_orch()
    db = orch.task_board._db
    existing = await db.execute_fetchone(
        "SELECT id FROM collab_comments WHERE id = ? AND task_id = ?",
        (comment_id, task_id),
    )
    if not existing:
        raise HTTPException(404, "Comment not found")
    await db.execute(
        "DELETE FROM collab_comments WHERE id = ? AND task_id = ?",
        (comment_id, task_id),
    )
    return {"status": "deleted", "comment_id": comment_id}


# ---------------------------------------------------------------------------
# Activity feed
# ---------------------------------------------------------------------------


@router.get("/activity")
async def get_activity(limit: int = 50):
    """Return recent activity feed entries."""
    await _ensure_tables()
    orch = get_orch()
    db = orch.task_board._db
    rows = await db.execute_fetchall(
        "SELECT id, actor, action, target_id, detail, created_at "
        "FROM collab_activity ORDER BY created_at DESC LIMIT ?",
        (limit,),
    )
    return {"activity": rows}


# ---------------------------------------------------------------------------
# Mentions
# ---------------------------------------------------------------------------


@router.post("/mentions")
async def create_mention(body: MentionBody):
    """Create an @mention notification."""
    await _ensure_tables()
    if not body.content.strip():
        raise HTTPException(400, "Mention content cannot be empty")
    orch = get_orch()
    db = orch.task_board._db
    mention_id = str(uuid.uuid4())[:12]
    now = _utcnow()
    await db.execute(
        "INSERT INTO collab_mentions (id, author, mentioned_user, task_id, content, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (mention_id, body.author, body.mentioned_user, body.task_id, body.content.strip(), now),
    )
    await _record_activity(db, body.author, "mention", body.task_id, f"@{body.mentioned_user}: {body.content.strip()[:80]}")
    return {
        "id": mention_id,
        "author": body.author,
        "mentioned_user": body.mentioned_user,
        "task_id": body.task_id,
        "content": body.content.strip(),
        "created_at": now,
    }


@router.get("/mentions/{user_id}")
async def get_mentions(user_id: str, unread_only: bool = False):
    """Get mentions for a specific user."""
    await _ensure_tables()
    orch = get_orch()
    db = orch.task_board._db
    if unread_only:
        rows = await db.execute_fetchall(
            "SELECT id, author, mentioned_user, task_id, content, read, created_at "
            "FROM collab_mentions WHERE mentioned_user = ? AND read = 0 "
            "ORDER BY created_at DESC",
            (user_id,),
        )
    else:
        rows = await db.execute_fetchall(
            "SELECT id, author, mentioned_user, task_id, content, read, created_at "
            "FROM collab_mentions WHERE mentioned_user = ? "
            "ORDER BY created_at DESC",
            (user_id,),
        )
    return {"user_id": user_id, "mentions": rows}


@router.post("/mentions/{mention_id}/read")
async def mark_mention_read(mention_id: str):
    """Mark a mention as read."""
    await _ensure_tables()
    orch = get_orch()
    db = orch.task_board._db
    await db.execute(
        "UPDATE collab_mentions SET read = 1 WHERE id = ?",
        (mention_id,),
    )
    return {"status": "ok", "mention_id": mention_id}
