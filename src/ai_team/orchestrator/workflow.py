"""Pipeline-based workflow engine for orchestrating agent tasks."""

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite
import yaml


@dataclass
class PipelineStep:
    agent: str
    action: str
    description: str
    retry_count: int = 0
    max_retries: int = 1


@dataclass
class Pipeline:
    name: str
    description: str
    steps: list[PipelineStep] = field(default_factory=list)

    def get_next_step(self, current_index: int) -> PipelineStep | None:
        next_idx = current_index + 1
        return self.steps[next_idx] if next_idx < len(self.steps) else None

    @classmethod
    def from_dict(cls, data: dict) -> "Pipeline":
        steps = [
            PipelineStep(
                agent=s["agent"],
                action=s["action"],
                description=s["description"],
                max_retries=s.get("max_retries", 1),
            )
            for s in data["steps"]
        ]
        return cls(name=data["name"], description=data["description"], steps=steps)

    @classmethod
    def from_yaml(cls, path: Path) -> "Pipeline":
        with open(path) as f:
            data = yaml.safe_load(f)
        return cls.from_dict(data)


@dataclass
class PipelineRun:
    pipeline_name: str
    run_id: str
    current_step: int = 0
    status: str = "running"
    context: dict = field(default_factory=dict)


class WorkflowEngine:
    def __init__(self, db_path: str | None = None):
        self.pipelines: dict[str, Pipeline] = {}
        self.active_runs: dict[str, PipelineRun] = {}
        self.db_path = db_path

    async def initialize_db(self) -> None:
        """Create the pipeline_runs table if it doesn't exist."""
        if self.db_path is None:
            return
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """CREATE TABLE IF NOT EXISTS pipeline_runs (
                    run_id TEXT PRIMARY KEY,
                    pipeline_name TEXT NOT NULL,
                    current_step INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    context TEXT,
                    started_at TEXT,
                    updated_at TEXT
                )"""
            )
            await db.commit()

    async def save_run(self, run_id: str) -> None:
        """Persist a run's current state to SQLite."""
        if self.db_path is None:
            return
        run = self.active_runs.get(run_id)
        if not run:
            return
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """INSERT OR REPLACE INTO pipeline_runs
                   (run_id, pipeline_name, current_step, status, context, started_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, COALESCE(
                       (SELECT started_at FROM pipeline_runs WHERE run_id = ?), ?
                   ), ?)""",
                (
                    run.run_id,
                    run.pipeline_name,
                    run.current_step,
                    run.status,
                    json.dumps(run.context),
                    run.run_id,
                    now,
                    now,
                ),
            )
            await db.commit()

    async def load_runs(self) -> None:
        """Load all non-completed/non-failed runs from SQLite into active_runs."""
        if self.db_path is None:
            return
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM pipeline_runs WHERE status NOT IN ('completed', 'failed')"
            ) as cursor:
                async for row in cursor:
                    run = PipelineRun(
                        pipeline_name=row["pipeline_name"],
                        run_id=row["run_id"],
                        current_step=row["current_step"],
                        status=row["status"],
                        context=json.loads(row["context"]) if row["context"] else {},
                    )
                    self.active_runs[run.run_id] = run

    def register_pipeline(self, pipeline: Pipeline) -> None:
        self.pipelines[pipeline.name] = pipeline

    def load_pipelines(self, directory: Path) -> None:
        for yaml_file in directory.glob("*.yaml"):
            pipeline = Pipeline.from_yaml(yaml_file)
            self.register_pipeline(pipeline)
        for yml_file in directory.glob("*.yml"):
            pipeline = Pipeline.from_yaml(yml_file)
            self.register_pipeline(pipeline)

    def start_run(
        self, pipeline_name: str, run_id: str, initial_context: dict | None = None
    ) -> PipelineRun:
        if pipeline_name not in self.pipelines:
            raise KeyError(f"Pipeline '{pipeline_name}' not found")
        run = PipelineRun(
            pipeline_name=pipeline_name, run_id=run_id, context=initial_context or {}
        )
        self.active_runs[run_id] = run
        return run

    def get_current_step(self, run_id: str) -> PipelineStep | None:
        run = self.active_runs.get(run_id)
        if not run:
            return None
        pipeline = self.pipelines[run.pipeline_name]
        return pipeline.steps[run.current_step] if run.current_step < len(pipeline.steps) else None

    def advance_run(self, run_id: str) -> PipelineStep | None:
        run = self.active_runs.get(run_id)
        if not run:
            return None
        pipeline = self.pipelines[run.pipeline_name]
        # Reset retry_count on the current step before advancing
        current_step = pipeline.steps[run.current_step] if run.current_step < len(pipeline.steps) else None
        if current_step:
            current_step.retry_count = 0
        next_step = pipeline.get_next_step(run.current_step)
        if next_step:
            run.current_step += 1
            return next_step
        else:
            run.status = "completed"
            return None

    def fail_step(self, run_id: str) -> None:
        run = self.active_runs.get(run_id)
        if not run:
            return
        pipeline = self.pipelines[run.pipeline_name]
        if run.current_step >= len(pipeline.steps):
            return
        step = pipeline.steps[run.current_step]
        step.retry_count += 1
        if step.retry_count >= step.max_retries:
            run.status = "failed"
