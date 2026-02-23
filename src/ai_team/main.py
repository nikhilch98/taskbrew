"""Main entry point for the AI Team Orchestrator."""

import asyncio
import argparse
from pathlib import Path

import uvicorn

from ai_team.config import OrchestratorConfig
from ai_team.orchestrator.event_bus import EventBus
from ai_team.orchestrator.task_queue import TaskQueue
from ai_team.orchestrator.team_manager import TeamManager
from ai_team.orchestrator.workflow import WorkflowEngine
from ai_team.dashboard.app import create_app


class Orchestrator:
    """Top-level orchestrator combining all components."""

    def __init__(self, event_bus, task_queue, team_manager, workflow_engine, config):
        self.event_bus = event_bus
        self.task_queue = task_queue
        self.team_manager = team_manager
        self.workflow_engine = workflow_engine
        self.config = config

    async def shutdown(self):
        await self.task_queue.close()


async def build_orchestrator(project_dir=None, cli_path=None):
    config = OrchestratorConfig(
        project_dir=project_dir or Path.cwd(),
        cli_path=cli_path,
    )
    event_bus = EventBus()
    task_queue = TaskQueue(db_path=config.db_path)
    await task_queue.initialize()
    team_manager = TeamManager(event_bus=event_bus, cli_path=config.cli_path)
    workflow_engine = WorkflowEngine()

    pipelines_dir = config.project_dir / "pipelines"
    if pipelines_dir.exists():
        workflow_engine.load_pipelines(pipelines_dir)

    return Orchestrator(
        event_bus=event_bus, task_queue=task_queue,
        team_manager=team_manager, workflow_engine=workflow_engine,
        config=config,
    )


async def run_server(orch):
    app = create_app(
        event_bus=orch.event_bus, team_manager=orch.team_manager,
        task_queue=orch.task_queue, workflow_engine=orch.workflow_engine,
    )
    config = uvicorn.Config(
        app=app, host=orch.config.dashboard_host,
        port=orch.config.dashboard_port, log_level="info",
    )
    server = uvicorn.Server(config)
    await server.serve()


async def async_main(args):
    orch = await build_orchestrator(
        project_dir=Path(args.project_dir) if args.project_dir else None,
        cli_path=args.cli_path,
    )

    if args.command == "serve":
        orch.team_manager.spawn_default_team()
        print(f"Dashboard: http://{orch.config.dashboard_host}:{orch.config.dashboard_port}")
        await run_server(orch)

    elif args.command == "run":
        orch.team_manager.spawn_default_team()
        if not args.pipeline:
            print("Error: --pipeline required for 'run' command")
            return
        import uuid
        run_id = str(uuid.uuid4())[:8]
        run = orch.workflow_engine.start_run(
            args.pipeline, run_id, initial_context={"goal": args.goal or ""}
        )
        print(f"Started pipeline '{args.pipeline}' (run: {run_id})")

        while True:
            step = orch.workflow_engine.get_current_step(run_id)
            if not step:
                print("Pipeline completed!")
                break
            print(f"\n--- Step: {step.agent} -> {step.action} ---")
            print(f"Description: {step.description}")

            prompt = (
                f"You are executing step '{step.action}' of the "
                f"'{args.pipeline}' pipeline.\n\n"
                f"Goal: {args.goal or 'No goal specified'}\n\n"
                f"Your task: {step.description}\n\n"
                f"Context from previous steps: {run.context}\n\n"
                f"Work in the project directory. Produce your output and be thorough."
            )

            try:
                result = await orch.team_manager.run_agent_task(
                    step.agent, prompt, cwd=str(orch.config.project_dir)
                )
                run.context[f"step_{run.current_step}_{step.agent}"] = result[:2000]
                print(f"Result: {result[:500]}")
                orch.workflow_engine.advance_run(run_id)
            except Exception as e:
                print(f"Error in step {step.agent}: {e}")
                break

    elif args.command == "status":
        orch.team_manager.spawn_default_team()
        status = orch.team_manager.get_team_status()
        for name, state in status.items():
            print(f"  {name}: {state}")

    await orch.shutdown()


def cli_main():
    parser = argparse.ArgumentParser(description="AI Team Orchestrator")
    parser.add_argument(
        "command", choices=["serve", "run", "status"],
        help="Command to execute",
    )
    parser.add_argument("--project-dir", help="Project directory to work in")
    parser.add_argument("--cli-path", help="Path to Claude Code CLI binary")
    parser.add_argument("--pipeline", help="Pipeline to run (for 'run' command)")
    parser.add_argument("--goal", help="Goal description (for 'run' command)")
    args = parser.parse_args()
    asyncio.run(async_main(args))


if __name__ == "__main__":
    cli_main()
