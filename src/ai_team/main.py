"""AI Team Orchestrator — Main entry point."""

from __future__ import annotations

import asyncio
import argparse
import logging
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)

from ai_team.config_loader import load_team_config, load_roles, validate_routing
from ai_team.orchestrator.database import Database
from ai_team.orchestrator.task_board import TaskBoard
from ai_team.orchestrator.event_bus import EventBus
from ai_team.orchestrator.artifact_store import ArtifactStore
from ai_team.agents.instance_manager import InstanceManager
from ai_team.agents.agent_loop import AgentLoop


class Orchestrator:
    """Central container for all orchestrator components."""

    def __init__(self, db, task_board, event_bus, artifact_store, instance_manager,
                 roles, team_config, project_dir):
        self.db = db
        self.task_board = task_board
        self.event_bus = event_bus
        self.artifact_store = artifact_store
        self.instance_manager = instance_manager
        self.roles = roles
        self.team_config = team_config
        self.project_dir = project_dir
        self.agent_tasks: list[asyncio.Task] = []

    async def shutdown(self):
        for task in self.agent_tasks:
            task.cancel()
        await self.db.close()


async def build_orchestrator(project_dir: Path | None = None, cli_path: str | None = None) -> Orchestrator:
    project_dir = project_dir or Path.cwd()
    config_dir = project_dir / "config"

    # Load configs
    team_config = load_team_config(config_dir / "team.yaml")
    roles = load_roles(config_dir / "roles")
    errors = validate_routing(roles)
    if errors:
        for err in errors:
            print(f"  Routing error: {err}")
        raise SystemExit(1)

    # Initialize components
    db_path = str(project_dir / team_config.db_path)
    db = Database(db_path)
    await db.initialize()

    event_bus = EventBus()

    # Build group prefixes from roles
    group_prefixes = {}
    for name, role in roles.items():
        if role.can_create_groups and role.group_type:
            group_prefixes[name] = role.group_type

    task_board = TaskBoard(db, group_prefixes=group_prefixes)

    # Register role prefixes
    role_prefixes = {name: role.prefix for name, role in roles.items()}
    await task_board.register_prefixes(role_prefixes)

    artifact_store = ArtifactStore(base_dir=str(project_dir / team_config.artifacts_base_dir))
    instance_manager = InstanceManager(db)

    return Orchestrator(
        db=db,
        task_board=task_board,
        event_bus=event_bus,
        artifact_store=artifact_store,
        instance_manager=instance_manager,
        roles=roles,
        team_config=team_config,
        project_dir=str(project_dir),
    )


async def run_server(orch: Orchestrator):
    """Start the dashboard server and agent loops."""
    import uvicorn
    from ai_team.dashboard.app import create_app

    app = create_app(
        event_bus=orch.event_bus,
        task_board=orch.task_board,
        instance_manager=orch.instance_manager,
        roles=orch.roles,
    )

    # Spawn agent loops
    for role_name, role_config in orch.roles.items():
        for i in range(1, role_config.max_instances + 1):
            instance_id = f"{role_name}-{i}"
            loop = AgentLoop(
                instance_id=instance_id,
                role_config=role_config,
                board=orch.task_board,
                event_bus=orch.event_bus,
                instance_manager=orch.instance_manager,
                all_roles=orch.roles,
                project_dir=orch.project_dir,
                poll_interval=orch.team_config.default_poll_interval,
            )
            task = asyncio.create_task(loop.run())
            orch.agent_tasks.append(task)

    config = uvicorn.Config(
        app,
        host=orch.team_config.dashboard_host,
        port=orch.team_config.dashboard_port,
        log_level="info",
    )
    server = uvicorn.Server(config)
    await server.serve()


async def submit_goal(orch: Orchestrator, title: str, description: str = ""):
    """Submit a new goal to the PM."""
    group = await orch.task_board.create_group(title=title, origin="pm", created_by="human")
    task = await orch.task_board.create_task(
        group_id=group["id"],
        title=f"Create PRD: {title}",
        description=description,
        task_type="goal",
        assigned_to="pm",
        created_by="human",
        priority="high",
    )
    print(f"Goal submitted: {group['id']}")
    print(f"  Group: {group['id']} — {title}")
    print(f"  Task:  {task['id']} — assigned to PM")
    return group, task


async def show_status(orch: Orchestrator):
    """Print team status."""
    instances = await orch.instance_manager.get_all_instances()
    board = await orch.task_board.get_board()
    groups = await orch.task_board.get_groups()

    print("\n=== AI Team Status ===\n")

    print(f"Groups: {len(groups)} active")
    for g in groups:
        print(f"  {g['id']}: {g['title']} ({g['status']})")

    print(f"\nAgents: {len(instances)}")
    for inst in instances:
        task_info = f" → {inst['current_task']}" if inst.get('current_task') else ""
        print(f"  {inst['instance_id']}: {inst['status']}{task_info}")

    print(f"\nTask Board:")
    for status, tasks in board.items():
        if tasks:
            print(f"  {status}: {len(tasks)}")
            for t in tasks:
                print(f"    {t['id']}: {t['title']}")


async def async_main(args):
    if args.command == "serve":
        orch = await build_orchestrator(
            project_dir=Path(args.project_dir) if args.project_dir else None,
        )
        try:
            await run_server(orch)
        finally:
            await orch.shutdown()

    elif args.command == "goal":
        orch = await build_orchestrator(
            project_dir=Path(args.project_dir) if args.project_dir else None,
        )
        try:
            await submit_goal(orch, title=args.title, description=args.description or "")
        finally:
            await orch.shutdown()

    elif args.command == "status":
        orch = await build_orchestrator(
            project_dir=Path(args.project_dir) if args.project_dir else None,
        )
        try:
            await show_status(orch)
        finally:
            await orch.shutdown()


def cli_main():
    parser = argparse.ArgumentParser(description="AI Team Orchestrator")
    parser.add_argument("--project-dir", help="Project directory", default=None)
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("serve", help="Start dashboard and agent loops")

    goal_parser = sub.add_parser("goal", help="Submit a new goal")
    goal_parser.add_argument("title", help="Goal title")
    goal_parser.add_argument("--description", "-d", help="Goal description", default="")

    sub.add_parser("status", help="Show team status")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    asyncio.run(async_main(args))


if __name__ == "__main__":
    cli_main()
