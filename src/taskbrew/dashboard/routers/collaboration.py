"""Collaboration features: comments, presence, activity feed, mentions.

Audit 10 F#15 / F#16: these routes used to accept ``author`` /
``mentioned_user`` / ``user_id`` as caller-supplied fields. Anyone with
access to the endpoint could impersonate any other user in the audit
trail, delete anyone's comment, or mark anyone's mention read.

The fix derives a stable ``actor`` ID from the bearer token (first 12
hex of SHA-256) and IGNORES the body-supplied ``author``. When auth is
disabled, actor is ``"anonymous"``. This closes the impersonation path
without requiring a user table. ``mentioned_user`` remains on the body
because the caller is targeting someone ELSE; the actor check runs on
the consumer side (mark-read) instead.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from starlette.requests import Request
from typing import Optional

from taskbrew.dashboard.routers._deps import get_orch

router = APIRouter(prefix="/api/collaboration", tags=["Collaboration"])

# ---------------------------------------------------------------------------
# Actor derivation (audit 10 F#15)
# ---------------------------------------------------------------------------


def _current_actor(request: Request) -> str:
    """Return a stable opaque actor ID derived from the bearer token.

    - With a bearer token: SHA-256(token)[:12] in hex
    - Without a token (auth disabled): ``"anonymous"``

    The value is purely for audit-trail authorship and ownership checks;
    it is not meant to be a user-visible ID.
    """
    auth_header = request.headers.get("authorization", "") or ""
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        if token:
            return hashlib.sha256(token.encode()).hexdigest()[:12]
    return "anonymous"


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class AddCommentBody(BaseModel):
    # ``author`` is retained for backwards compatibility in legacy
    # clients but is ignored server-side (audit 10 F#15). Actor is
    # derived from the bearer token.
    author: Optional[str] = None
    content: str


class MentionBody(BaseModel):
    # ``author`` likewise ignored; actor is derived from auth.
    author: Optional[str] = None
    mentioned_user: str
    task_id: Optional[str] = None
    content: str


# ---------------------------------------------------------------------------
# Table initialisation (idempotent)
# ---------------------------------------------------------------------------

_tables_created_for_db: object | None = None


async def _ensure_tables():
    """Create collaboration tables if they do not exist yet."""
    global _tables_created_for_db
    orch = get_orch()
    db = orch.task_board._db
    if _tables_created_for_db is db:
        return
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
    _tables_created_for_db = db


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
async def heartbeat_presence(user_id: str, request: Request):
    """Record or update presence for the caller.

    When a bearer token is present, *user_id* MUST equal the derived
    actor or match the configured token hash; otherwise the caller is
    trying to spoof another user's presence. When auth is disabled, any
    user_id is accepted (development convenience).
    """
    await _ensure_tables()
    actor = _current_actor(request)
    if actor != "anonymous" and user_id != actor:
        raise HTTPException(
            403,
            "user_id in URL does not match authenticated actor; cannot heartbeat for another user",
        )
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


def _resolved_author(request: Request, body_author: Optional[str]) -> str:
    """Authoritative author for a write operation.

    When auth is enabled the actor is derived from the bearer token and
    body-supplied ``author`` is ignored (the audit 10 F#15 fix). When
    auth is disabled (actor == ``"anonymous"``), fall back to the body
    value if provided so legacy single-tenant / dev deployments keep
    working. Empty / whitespace-only values are also treated as
    "anonymous".
    """
    actor = _current_actor(request)
    if actor != "anonymous":
        return actor
    if body_author and body_author.strip():
        return body_author.strip()
    return "anonymous"


@router.post("/comments/{task_id}")
async def add_comment(task_id: str, body: AddCommentBody, request: Request):
    """Add a comment to a task.

    audit 10 F#15: with auth enabled the author is derived from the
    bearer token and body.author is ignored. Dev deployments without
    auth still accept body.author so the existing UI flow continues
    to work.
    """
    await _ensure_tables()
    if not body.content.strip():
        raise HTTPException(400, "Comment content cannot be empty")
    orch = get_orch()
    db = orch.task_board._db
    comment_id = str(uuid.uuid4())[:12]
    now = _utcnow()
    author = _resolved_author(request, body.author)
    await db.execute(
        "INSERT INTO collab_comments (id, task_id, author, content, created_at) VALUES (?, ?, ?, ?, ?)",
        (comment_id, task_id, author, body.content.strip(), now),
    )
    await _record_activity(db, author, "comment", task_id, body.content.strip()[:100])
    return {
        "id": comment_id,
        "task_id": task_id,
        "author": author,
        "content": body.content.strip(),
        "created_at": now,
    }


@router.delete("/comments/{task_id}/{comment_id}")
async def delete_comment(task_id: str, comment_id: str, request: Request):
    """Delete a specific comment.

    audit 10 F#16: only the original author (or an anonymous caller
    when auth is off) may delete. Previously anyone could delete any
    comment by ID.
    """
    await _ensure_tables()
    orch = get_orch()
    db = orch.task_board._db
    existing = await db.execute_fetchone(
        "SELECT id, author FROM collab_comments WHERE id = ? AND task_id = ?",
        (comment_id, task_id),
    )
    if not existing:
        raise HTTPException(404, "Comment not found")
    actor = _current_actor(request)
    if actor != "anonymous" and existing["author"] != actor:
        raise HTTPException(
            403,
            "Only the original author may delete this comment",
        )
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
    # Cap limit to prevent pathological client requests (audit 10 F#14).
    try:
        limit_int = int(limit)
    except (TypeError, ValueError):
        limit_int = 50
    limit_int = max(1, min(limit_int, 500))
    orch = get_orch()
    db = orch.task_board._db
    rows = await db.execute_fetchall(
        "SELECT id, actor, action, target_id, detail, created_at "
        "FROM collab_activity ORDER BY created_at DESC LIMIT ?",
        (limit_int,),
    )
    return {"activity": rows}


# ---------------------------------------------------------------------------
# Mentions
# ---------------------------------------------------------------------------


@router.post("/mentions")
async def create_mention(body: MentionBody, request: Request):
    """Create an @mention notification.

    audit 10 F#15: with auth enabled, author is derived from the bearer
    token. Dev deployments without auth fall back to body.author. The
    caller can never spoof someone ELSE's author because the actor
    check always wins when auth is on; ``mentioned_user`` remains
    body-supplied because it targets another user.
    """
    await _ensure_tables()
    if not body.content.strip():
        raise HTTPException(400, "Mention content cannot be empty")
    orch = get_orch()
    db = orch.task_board._db
    mention_id = str(uuid.uuid4())[:12]
    now = _utcnow()
    author = _resolved_author(request, body.author)
    await db.execute(
        "INSERT INTO collab_mentions (id, author, mentioned_user, task_id, content, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (mention_id, author, body.mentioned_user, body.task_id, body.content.strip(), now),
    )
    await _record_activity(db, author, "mention", body.task_id, f"@{body.mentioned_user}: {body.content.strip()[:80]}")
    return {
        "id": mention_id,
        "author": author,
        "mentioned_user": body.mentioned_user,
        "task_id": body.task_id,
        "content": body.content.strip(),
        "created_at": now,
    }


@router.get("/mentions/{user_id}")
async def get_mentions(user_id: str, request: Request, unread_only: bool = False):
    """Get mentions for a specific user.

    Only the user themselves may list their mentions. Anonymous callers
    (auth disabled) see any.
    """
    await _ensure_tables()
    actor = _current_actor(request)
    if actor != "anonymous" and user_id != actor:
        raise HTTPException(
            403,
            "Cannot list another user's mentions",
        )
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
async def mark_mention_read(mention_id: str, request: Request):
    """Mark a mention as read.

    audit 10 F#16: only the user the mention targets may mark it read;
    previously anyone could.
    """
    await _ensure_tables()
    orch = get_orch()
    db = orch.task_board._db
    row = await db.execute_fetchone(
        "SELECT mentioned_user FROM collab_mentions WHERE id = ?",
        (mention_id,),
    )
    if not row:
        raise HTTPException(404, "Mention not found")
    actor = _current_actor(request)
    if actor != "anonymous" and row["mentioned_user"] != actor:
        raise HTTPException(403, "Cannot mark another user's mention as read")
    await db.execute(
        "UPDATE collab_mentions SET read = 1 WHERE id = ?",
        (mention_id,),
    )
    return {"status": "ok", "mention_id": mention_id}
