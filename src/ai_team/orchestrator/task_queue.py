"""SQLite-backed task queue for agent coordination."""

import uuid
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path

import aiosqlite


class TaskStatus(StrEnum):
    PENDING = "pending"
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    REVIEW = "review"
    COMPLETED = "completed"
    FAILED = "failed"


class TaskQueue:
    """Async SQLite-backed task queue."""

    def __init__(self, db_path: Path):
        self._db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def initialize(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self._db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                pipeline_id TEXT NOT NULL,
                task_type TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                assigned_to TEXT,
                input_context TEXT,
                output_artifact TEXT,
                parent_task_id TEXT,
                error TEXT,
                session_id TEXT,
                created_at TEXT NOT NULL,
                started_at TEXT,
                completed_at TEXT
            )
        """)
        await self._db.commit()

    async def close(self) -> None:
        if self._db:
            await self._db.close()

    async def create_task(
        self,
        pipeline_id: str,
        task_type: str,
        input_context: str,
        parent_task_id: str | None = None,
    ) -> str:
        task_id = str(uuid.uuid4())[:8]
        now = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            """INSERT INTO tasks (id, pipeline_id, task_type, status, input_context,
               parent_task_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (task_id, pipeline_id, task_type, TaskStatus.PENDING, input_context,
             parent_task_id, now),
        )
        await self._db.commit()
        return task_id

    async def get_task(self, task_id: str) -> dict | None:
        cursor = await self._db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def assign_task(self, task_id: str, agent_name: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            "UPDATE tasks SET status = ?, assigned_to = ?, started_at = ? WHERE id = ?",
            (TaskStatus.ASSIGNED, agent_name, now, task_id),
        )
        await self._db.commit()

    async def update_status(self, task_id: str, status: TaskStatus) -> None:
        await self._db.execute(
            "UPDATE tasks SET status = ? WHERE id = ?", (status, task_id)
        )
        await self._db.commit()

    async def complete_task(self, task_id: str, output_artifact: str | None = None) -> None:
        now = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            "UPDATE tasks SET status = ?, output_artifact = ?, completed_at = ? WHERE id = ?",
            (TaskStatus.COMPLETED, output_artifact, now, task_id),
        )
        await self._db.commit()

    async def fail_task(self, task_id: str, error: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            "UPDATE tasks SET status = ?, error = ?, completed_at = ? WHERE id = ?",
            (TaskStatus.FAILED, error, now, task_id),
        )
        await self._db.commit()

    async def get_pending_tasks(self, pipeline_id: str | None = None) -> list[dict]:
        if pipeline_id:
            cursor = await self._db.execute(
                "SELECT * FROM tasks WHERE status = ? AND pipeline_id = ? ORDER BY created_at",
                (TaskStatus.PENDING, pipeline_id),
            )
        else:
            cursor = await self._db.execute(
                "SELECT * FROM tasks WHERE status = ? ORDER BY created_at",
                (TaskStatus.PENDING,),
            )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def get_tasks_by_pipeline(self, pipeline_id: str) -> list[dict]:
        cursor = await self._db.execute(
            "SELECT * FROM tasks WHERE pipeline_id = ? ORDER BY created_at",
            (pipeline_id,),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]
