"""Pipeline-based workflow engine for orchestrating agent tasks."""

from dataclasses import dataclass, field
from pathlib import Path

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
    def __init__(self):
        self.pipelines: dict[str, Pipeline] = {}
        self.active_runs: dict[str, PipelineRun] = {}

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
        next_step = pipeline.get_next_step(run.current_step)
        if next_step:
            run.current_step += 1
            return next_step
        else:
            run.status = "completed"
            return None
