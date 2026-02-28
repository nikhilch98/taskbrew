"""TaskBrew — Multi-agent AI team orchestrator. Main entry point."""

from __future__ import annotations

import asyncio
import argparse
import logging
import os
from pathlib import Path

from taskbrew.agents.agent_loop import AgentLoop
from taskbrew.agents.instance_manager import InstanceManager
from taskbrew.config_loader import RoleConfig, load_team_config, load_roles, validate_routing
from taskbrew.orchestrator.artifact_store import ArtifactStore
from taskbrew.orchestrator.database import Database
from taskbrew.orchestrator.event_bus import EventBus
from taskbrew.orchestrator.task_board import TaskBoard
from taskbrew.tools.worktree_manager import WorktreeManager


def _validate_startup(project_dir: Path, team_config, roles: dict, cli_provider: str):
    """Validate configuration before starting. Raises SystemExit on failure."""
    import shutil

    errors = []

    # Check CLI binary exists
    if cli_provider == "claude" and not shutil.which("claude"):
        errors.append(
            f"Claude CLI not found (provider: '{cli_provider}').\n"
            "  -> Install: npm install -g @anthropic-ai/claude-code"
        )
    if cli_provider == "gemini" and not shutil.which("gemini"):
        errors.append(
            f"Gemini CLI not found (provider: '{cli_provider}').\n"
            "  -> Install: npm install -g @google/gemini-cli"
        )

    # Check roles exist
    if not roles:
        errors.append(
            "No role files found in config/roles/\n"
            "  -> Create role YAML files or run: taskbrew init"
        )

    if errors:
        print("\nStartup validation failed:\n")
        for i, err in enumerate(errors, 1):
            print(f"  {i}. {err}\n")
        raise SystemExit(1)


class Orchestrator:
    """Central container for all orchestrator components."""

    def __init__(self, db, task_board, event_bus, artifact_store, instance_manager,
                 roles, team_config, project_dir, worktree_manager, memory_manager=None,
                 context_registry=None):
        self.db = db
        self.task_board = task_board
        self.event_bus = event_bus
        self.artifact_store = artifact_store
        self.instance_manager = instance_manager
        self.roles = roles
        self.team_config = team_config
        self.project_dir = project_dir
        self.worktree_manager = worktree_manager
        self.memory_manager = memory_manager
        self.context_registry = context_registry
        self.agent_tasks: list[asyncio.Task] = []

        # Intelligence managers (set during build)
        self.quality_manager = None
        self.collaboration_manager = None
        self.specialization_manager = None
        self.planning_manager = None
        self.preflight_checker = None
        self.impact_analyzer = None
        self.escalation_manager = None
        self.checkpoint_manager = None
        self.messaging_manager = None
        self.knowledge_graph = None
        self.review_learning = None
        self.tool_router = None

        # Intelligence managers v2 (set during build)
        self.autonomous_manager = None
        self.code_intel_manager = None
        self.learning_manager = None
        self.coordination_manager = None
        self.testing_quality_manager = None
        self.security_intel_manager = None
        self.observability_manager = None
        self.advanced_planning_manager = None

        # Intelligence managers v3 (set during build)
        self.self_improvement_manager = None
        self.social_intelligence_manager = None
        self.code_reasoning_manager = None
        self.task_intelligence_manager = None
        self.verification_manager = None
        self.process_intelligence_manager = None
        self.knowledge_manager = None
        self.compliance_manager = None

        # Plugin registry (set during build)
        self.plugin_registry = None

        # Shutdown state
        self._shutting_down = False
        self._agent_loops: list = []
        self._logger = logging.getLogger(__name__)

    @property
    def shutting_down(self) -> bool:
        return self._shutting_down

    async def shutdown(self, timeout: float = 30.0):
        """Gracefully shut down the orchestrator.

        Phases:
        1. Signal agent loops and escalation monitor to stop.
        2. Wait for agent tasks to complete (up to *timeout* seconds),
           then force-cancel any that remain.
        3. Clean up worktrees.
        4. Close the database connection.

        The method is idempotent — calling it a second time is a no-op.
        """
        if self._shutting_down:
            return

        self._shutting_down = True
        self._logger.info("Graceful shutdown initiated")

        # Phase 1 — signal agent loops and escalation monitor to stop
        self._logger.info("Phase 1: Signalling agent loops to stop")
        for loop in self._agent_loops:
            loop.stop()

        if hasattr(self, '_escalation_stop'):
            self._escalation_stop.set()

        # Phase 2 — wait for agent tasks, then force-cancel stragglers
        if self.agent_tasks:
            self._logger.info("Phase 2: Waiting for %d agent tasks (timeout=%.1fs)",
                              len(self.agent_tasks), timeout)
            done, pending = await asyncio.wait(
                self.agent_tasks, timeout=timeout,
                return_when=asyncio.ALL_COMPLETED,
            )
            if pending:
                self._logger.warning("Phase 2: Force-cancelling %d timed-out tasks", len(pending))
                for task in pending:
                    task.cancel()
                # Allow cancellation to propagate
                await asyncio.gather(*pending, return_exceptions=True)

        # Phase 3 — clean up worktrees
        try:
            if self.worktree_manager:
                self._logger.info("Phase 3: Cleaning up worktrees")
                await self.worktree_manager.cleanup_all()
        except Exception:
            self._logger.exception("Error during worktree cleanup")

        # Phase 4 — close database
        try:
            self._logger.info("Closing database connection")
            await self.db.close()
        except Exception:
            self._logger.exception("Error closing database")


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

    # Fail-fast startup validation
    cli_provider = getattr(team_config, "cli_provider", "claude") or "claude"
    _validate_startup(
        project_dir=project_dir,
        team_config=team_config,
        roles=roles,
        cli_provider=cli_provider,
    )

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
    worktree_manager = WorktreeManager(
        repo_dir=str(project_dir),
        worktree_base=str(project_dir / ".worktrees"),
    )
    await worktree_manager.prune_stale()

    from taskbrew.intelligence.memory import MemoryManager
    memory_manager = MemoryManager(db)

    from taskbrew.intelligence.context_providers import (
        ContextProviderRegistry, GitHistoryProvider, CoverageContextProvider,
        DependencyGraphProvider, CrossTaskProvider, CICDProvider,
        DocumentationProvider, IssueTrackerProvider, RuntimeContextProvider,
    )
    context_registry = ContextProviderRegistry(db, project_dir=str(project_dir))
    context_registry.register(GitHistoryProvider(str(project_dir)))
    context_registry.register(CoverageContextProvider(str(project_dir)))
    context_registry.register(DependencyGraphProvider(str(project_dir)))
    context_registry.register(CrossTaskProvider(db))
    context_registry.register(CICDProvider(str(project_dir)))
    context_registry.register(DocumentationProvider(str(project_dir)))
    context_registry.register(IssueTrackerProvider(db))
    context_registry.register(RuntimeContextProvider(db))

    orch = Orchestrator(
        db=db,
        task_board=task_board,
        event_bus=event_bus,
        artifact_store=artifact_store,
        instance_manager=instance_manager,
        roles=roles,
        team_config=team_config,
        project_dir=str(project_dir),
        worktree_manager=worktree_manager,
        memory_manager=memory_manager,
        context_registry=context_registry,
    )

    # Instantiate intelligence managers centrally
    from taskbrew.intelligence.quality import QualityManager
    from taskbrew.intelligence.collaboration import CollaborationManager
    from taskbrew.intelligence.specialization import SpecializationManager
    from taskbrew.intelligence.planning import PlanningManager
    from taskbrew.intelligence.preflight import PreflightChecker
    from taskbrew.intelligence.impact import ImpactAnalyzer
    from taskbrew.intelligence.escalation import EscalationManager
    from taskbrew.intelligence.checkpoints import CheckpointManager
    from taskbrew.intelligence.messaging import MessagingManager
    from taskbrew.intelligence.knowledge_graph import KnowledgeGraphBuilder
    from taskbrew.intelligence.review_learning import ReviewLearningManager
    from taskbrew.intelligence.tool_router import ToolRouter

    orch.quality_manager = QualityManager(db, memory_manager=memory_manager)
    orch.collaboration_manager = CollaborationManager(db, task_board=task_board, event_bus=event_bus)
    orch.specialization_manager = SpecializationManager(db)
    orch.planning_manager = PlanningManager(db, task_board=task_board)
    orch.preflight_checker = PreflightChecker(db)
    orch.impact_analyzer = ImpactAnalyzer(db, project_dir=str(project_dir))
    orch.escalation_manager = EscalationManager(db, task_board=task_board, event_bus=event_bus, instance_manager=instance_manager)
    orch.checkpoint_manager = CheckpointManager(db, event_bus=event_bus)
    orch.messaging_manager = MessagingManager(db, event_bus=event_bus)
    orch.knowledge_graph = KnowledgeGraphBuilder(db, project_dir=str(project_dir))
    orch.review_learning = ReviewLearningManager(db)
    orch.tool_router = ToolRouter(db)

    # Intelligence managers v2
    from taskbrew.intelligence.autonomous import AutonomousManager
    from taskbrew.intelligence.code_intel import CodeIntelligenceManager
    from taskbrew.intelligence.learning import LearningManager
    from taskbrew.intelligence.coordination import CoordinationManager
    from taskbrew.intelligence.testing_quality import TestingQualityManager
    from taskbrew.intelligence.security_intel import SecurityIntelManager
    from taskbrew.intelligence.observability import ObservabilityManager
    from taskbrew.intelligence.advanced_planning import AdvancedPlanningManager

    orch.autonomous_manager = AutonomousManager(db, task_board=task_board, memory_manager=memory_manager)
    orch.code_intel_manager = CodeIntelligenceManager(db, project_dir=str(project_dir))
    orch.learning_manager = LearningManager(db, memory_manager=memory_manager)
    orch.coordination_manager = CoordinationManager(db, task_board=task_board, event_bus=event_bus, instance_manager=instance_manager)
    orch.testing_quality_manager = TestingQualityManager(db, project_dir=str(project_dir))
    orch.security_intel_manager = SecurityIntelManager(db, project_dir=str(project_dir))
    orch.observability_manager = ObservabilityManager(db, event_bus=event_bus)
    orch.advanced_planning_manager = AdvancedPlanningManager(db)

    # Intelligence managers v3
    from taskbrew.intelligence.self_improvement import SelfImprovementManager
    from taskbrew.intelligence.social_intelligence import SocialIntelligenceManager
    from taskbrew.intelligence.code_reasoning import CodeReasoningManager
    from taskbrew.intelligence.task_intelligence import TaskIntelligenceManager
    from taskbrew.intelligence.verification import VerificationManager
    from taskbrew.intelligence.process_intelligence import ProcessIntelligenceManager
    from taskbrew.intelligence.knowledge_management import KnowledgeManager
    from taskbrew.intelligence.compliance import ComplianceManager

    orch.self_improvement_manager = SelfImprovementManager(db, memory_manager=memory_manager)
    orch.social_intelligence_manager = SocialIntelligenceManager(db, event_bus=event_bus, instance_manager=instance_manager)
    orch.code_reasoning_manager = CodeReasoningManager(db, project_dir=str(project_dir))
    orch.task_intelligence_manager = TaskIntelligenceManager(db, task_board=task_board, memory_manager=memory_manager)
    orch.verification_manager = VerificationManager(db, project_dir=str(project_dir))
    orch.process_intelligence_manager = ProcessIntelligenceManager(db, task_board=task_board)
    orch.knowledge_manager = KnowledgeManager(db, project_dir=str(project_dir))
    orch.compliance_manager = ComplianceManager(db, project_dir=str(project_dir))

    # Ensure V3 manager tables exist
    await orch.self_improvement_manager.ensure_tables()
    await orch.social_intelligence_manager.ensure_tables()
    await orch.code_reasoning_manager.ensure_tables()
    await orch.task_intelligence_manager.ensure_tables()
    await orch.verification_manager.ensure_tables()
    await orch.process_intelligence_manager.ensure_tables()
    await orch.knowledge_manager.ensure_tables()
    await orch.compliance_manager.ensure_tables()

    # Load plugins
    from taskbrew.plugin_system import PluginRegistry

    plugin_registry = PluginRegistry()
    plugins_dir = Path(project_dir) / "plugins"
    if plugins_dir.is_dir():
        loaded = plugin_registry.load_plugins(plugins_dir)
        logging.getLogger(__name__).info(
            "Loaded %d plugin(s): %s", len(loaded), loaded
        )
    orch.plugin_registry = plugin_registry

    return orch


async def _orphan_recovery_loop(
    orch: Orchestrator,
    interval: float = 30.0,
    heartbeat_timeout: float = 90.0,
) -> None:
    """Periodically recover orphaned and stuck tasks.

    Runs every *interval* seconds.  Handles two failure modes:

    1. **Stale in_progress tasks** — an agent claimed a task but died (heartbeat
       older than *heartbeat_timeout* while still in ``'working'`` status).
       Resets the task to ``pending`` so another agent can pick it up.

    2. **Stuck blocked tasks** — a task is ``blocked`` but all its dependencies
       are already in terminal states (``completed`` / ``failed``).  The
       dependency resolution was missed (crash, race).  Resolves the deps and
       moves the task to ``pending`` or ``failed`` as appropriate.
    """
    _logger = logging.getLogger(__name__ + ".orphan_recovery")
    while True:
        await asyncio.sleep(interval)
        try:
            # 1. Recover tasks stuck on dead agent instances
            stale = await orch.instance_manager.get_stale_instances(
                timeout_seconds=heartbeat_timeout
            )
            if stale:
                stale_ids = [inst["instance_id"] for inst in stale]
                _logger.warning(
                    "Detected %d stale agent instances: %s", len(stale_ids), stale_ids
                )
                recovered = await orch.task_board.recover_stale_in_progress_tasks(
                    stale_ids
                )
                for t in recovered:
                    _logger.info("Recovered orphaned task %s", t["id"])
                    await orch.event_bus.emit(
                        "task.recovered", {"task_id": t["id"]}
                    )
                # Reset stale instances to idle
                for inst_id in stale_ids:
                    await orch.instance_manager.update_status(inst_id, "idle")

            # 2. Recover blocked tasks whose deps are all terminal
            stuck = await orch.task_board.recover_stuck_blocked_tasks()
            if stuck:
                _logger.info("Recovered %d stuck blocked tasks", len(stuck))
                for t in stuck:
                    await orch.event_bus.emit(
                        "task.recovered", {"task_id": t["id"]}
                    )
        except Exception:
            _logger.exception("Error in orphan recovery loop")


async def start_agents(orch: Orchestrator):
    """Start agent loops, recovery tasks, and auto-scaler for *orch*.

    This is a module-level function so that ``app.py`` can import and call it
    when activating a project via the API.
    """
    # Recover orphaned tasks from previous crash
    recovered = await orch.task_board.recover_orphaned_tasks()
    if recovered:
        logger = logging.getLogger(__name__)
        logger.info("Recovered %d orphaned in_progress tasks to pending", len(recovered))
        for t in recovered:
            await orch.event_bus.emit("task.recovered", {"task_id": t["id"]})

    # Recover blocked tasks whose dependencies are all terminal
    stuck = await orch.task_board.recover_stuck_blocked_tasks()
    if stuck:
        logger = logging.getLogger(__name__)
        logger.info("Recovered %d stuck blocked tasks", len(stuck))

    # Start background orphan recovery loop
    recovery_task = asyncio.create_task(_orphan_recovery_loop(orch))
    orch.agent_tasks.append(recovery_task)

    # Start escalation monitor background task
    if orch.escalation_manager:
        from taskbrew.intelligence.monitors import escalation_monitor

        orch._escalation_stop = asyncio.Event()
        orch._escalation_task = asyncio.create_task(
            escalation_monitor(
                orch.escalation_manager, stop_event=orch._escalation_stop
            )
        )
        orch.agent_tasks.append(orch._escalation_task)

    # Spawn agent loops
    # Map bind host to connect host (0.0.0.0 binds all interfaces but can't be connected to)
    connect_host = "127.0.0.1" if orch.team_config.dashboard_host in ("0.0.0.0", "::") else orch.team_config.dashboard_host
    api_url = f"http://{connect_host}:{orch.team_config.dashboard_port}"
    # Dict to look up agent loops and their asyncio tasks by instance_id
    # (used by the auto-scaler stopper callback to cancel running agents)
    if not hasattr(orch, '_agent_tasks_by_id'):
        orch._agent_tasks_by_id = {}
    cli_provider = getattr(orch.team_config, "cli_provider", "claude") or "claude"
    for role_name, role_config in orch.roles.items():
        # Roles with Bash get worktree isolation so they can't mutate the main checkout
        needs_worktree = "Bash" in role_config.tools
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
                api_url=api_url,
                worktree_manager=orch.worktree_manager if needs_worktree else None,
                memory_manager=orch.memory_manager,
                context_registry=orch.context_registry,
                observability_manager=orch.observability_manager,
                cli_provider=cli_provider,
                mcp_servers=getattr(orch.team_config, "mcp_servers", None),
            )
            orch._agent_loops.append(loop)
            task = asyncio.create_task(loop.run())
            orch.agent_tasks.append(task)
            orch._agent_tasks_by_id[instance_id] = (loop, task)

    # Start auto-scaler if any role has auto_scale enabled
    has_auto_scale = any(
        r.auto_scale and r.auto_scale.enabled for r in orch.roles.values()
        if hasattr(r, 'auto_scale') and r.auto_scale
    )
    if has_auto_scale:
        from taskbrew.agents.auto_scaler import AutoScaler

        async def _agent_factory(instance_id: str, role_config: RoleConfig) -> asyncio.Task:
            needs_worktree = "Bash" in role_config.tools
            loop = AgentLoop(
                instance_id=instance_id,
                role_config=role_config,
                board=orch.task_board,
                event_bus=orch.event_bus,
                instance_manager=orch.instance_manager,
                all_roles=orch.roles,
                project_dir=orch.project_dir,
                poll_interval=orch.team_config.default_poll_interval,
                api_url=api_url,
                worktree_manager=orch.worktree_manager if needs_worktree else None,
                memory_manager=orch.memory_manager,
                context_registry=orch.context_registry,
                observability_manager=orch.observability_manager,
                cli_provider=cli_provider,
                mcp_servers=getattr(orch.team_config, "mcp_servers", None),
            )
            orch._agent_loops.append(loop)
            task = asyncio.create_task(loop.run())
            orch.agent_tasks.append(task)
            orch._agent_tasks_by_id[instance_id] = (loop, task)
            return task

        async def _agent_stopper(instance_id: str) -> None:
            # Stop the agent loop and cancel its asyncio task
            entry = orch._agent_tasks_by_id.pop(instance_id, None)
            if entry:
                agent_loop, agent_task = entry
                agent_loop.stop()
                agent_task.cancel()
                try:
                    await agent_task
                except (asyncio.CancelledError, Exception):
                    pass
                # Remove from the lists so shutdown doesn't try again
                if agent_loop in orch._agent_loops:
                    orch._agent_loops.remove(agent_loop)
                if agent_task in orch.agent_tasks:
                    orch.agent_tasks.remove(agent_task)
            await orch.instance_manager.remove_instance(instance_id)

        scaler = AutoScaler(
            orch.task_board, orch.instance_manager, orch.roles,
            agent_factory=_agent_factory, agent_stopper=_agent_stopper,
        )
        scaler_task = asyncio.create_task(scaler.run())
        orch.agent_tasks.append(scaler_task)


async def run_server(project_manager):
    """Start the dashboard server. Agents are started separately via start_agents()."""
    import uvicorn
    from taskbrew.dashboard.app import create_app
    from taskbrew.dashboard.chat_manager import ChatManager

    orch = project_manager.orchestrator
    chat_manager = ChatManager(
        project_dir=orch.project_dir if orch else None,
    )

    app = create_app(project_manager=project_manager, chat_manager=chat_manager)

    # If there's an active project, start its agents
    if project_manager.orchestrator:
        await start_agents(project_manager.orchestrator)

    # Use active project's config for host/port, or defaults
    orch = project_manager.orchestrator
    host = orch.team_config.dashboard_host if orch else "127.0.0.1"
    port = orch.team_config.dashboard_port if orch else 8420

    config = uvicorn.Config(app, host=host, port=port, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()


async def submit_goal(orch: Orchestrator, title: str, description: str = ""):
    """Submit a new goal to the PM."""
    group = await orch.task_board.create_group(title=title, origin="pm", created_by="pm")
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

    print("\n=== TaskBrew Status ===\n")

    print(f"Groups: {len(groups)} active")
    for g in groups:
        print(f"  {g['id']}: {g['title']} ({g['status']})")

    print(f"\nAgents: {len(instances)}")
    for inst in instances:
        task_info = f" → {inst['current_task']}" if inst.get('current_task') else ""
        print(f"  {inst['instance_id']}: {inst['status']}{task_info}")

    print("\nTask Board:")
    for status, tasks in board.items():
        if tasks:
            print(f"  {status}: {len(tasks)}")
            for t in tasks:
                print(f"    {t['id']}: {t['title']}")


async def async_main(args):
    if args.command == "serve":
        from taskbrew.project_manager import ProjectManager, _slugify

        pm = ProjectManager()

        # Auto-migration: if --project-dir passed, register and activate
        if args.project_dir:
            project_dir = Path(args.project_dir).resolve()
            name = project_dir.name.replace("-", " ").replace("_", " ").title()
            try:
                pm.create_project(name, str(project_dir))
            except ValueError:
                pass  # already registered
            slug = _slugify(name)
            await pm.activate_project(slug)
        else:
            # Check if CWD has config and no registry exists yet
            cwd = Path.cwd()
            if (cwd / "config" / "team.yaml").exists() and pm.get_active() is None:
                name = cwd.name.replace("-", " ").replace("_", " ").title()
                try:
                    pm.create_project(name, str(cwd))
                except ValueError:
                    pass
                slug = _slugify(name)
                try:
                    await pm.activate_project(slug)
                except Exception as e:
                    logging.getLogger(__name__).warning("Auto-migration failed: %s", e)
            else:
                # Try to activate last-used project
                active = pm.get_active()
                if active:
                    try:
                        await pm.activate_project(active["id"])
                    except Exception as e:
                        logging.getLogger(__name__).warning("Failed to activate project %s: %s", active["id"], e)

        try:
            await run_server(pm)
        finally:
            await pm.deactivate_current()

    elif args.command == "goal":
        # Keep using build_orchestrator directly for CLI commands
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


def _cmd_init(args):
    """Initialize a new taskbrew project."""
    project_dir = Path(args.dir).resolve()
    project_name = args.name or project_dir.name

    print(f"Initializing taskbrew project: {project_name}")
    print(f"Directory: {project_dir}\n")

    # Create config directories
    config_dir = project_dir / "config"
    roles_dir = config_dir / "roles"
    providers_dir = config_dir / "providers"
    plugins_dir = project_dir / "plugins"

    for d in [config_dir, roles_dir, providers_dir, plugins_dir]:
        d.mkdir(parents=True, exist_ok=True)
        print(f"  Created {d.relative_to(project_dir)}/")

    # Create team.yaml if it doesn't exist
    team_yaml = config_dir / "team.yaml"
    if not team_yaml.exists():
        team_yaml.write_text(
            f'team_name: "{project_name}"\n\n'
            f'database:\n  path: "~/.taskbrew/data/{project_name}.db"\n\n'
            f'dashboard:\n  host: "0.0.0.0"\n  port: 8420\n\n'
            f'artifacts:\n  base_dir: "artifacts"\n\n'
            f'defaults:\n'
            f'  max_instances: 1\n'
            f'  poll_interval_seconds: 5\n'
            f'  idle_timeout_minutes: 30\n\n'
            f'cli_provider: "{args.provider}"\n\n'
            f'# Uncomment to add MCP tool servers:\n'
            f'# mcp_servers:\n'
            f'#   my-tool:\n'
            f'#     command: "python"\n'
            f'#     args: ["-m", "my_tool"]\n'
            f'#     env:\n'
            f'#       MY_VAR: "value"\n\n'
            f'# Guardrails (optional):\n'
            f'# guardrails:\n'
            f'#   max_task_depth: 10\n'
            f'#   max_tasks_per_group: 50\n'
            f'#   rejection_cycle_limit: 3\n'
        )
        print("  Created config/team.yaml")

    # Create a default PM role if no roles exist
    if not list(roles_dir.glob("*.yaml")):
        pm_yaml = roles_dir / "pm.yaml"
        pm_yaml.write_text(
            'role: pm\n'
            'display_name: "Project Manager"\n'
            'prefix: "PM"\n'
            'emoji: "PM"\n'
            'color: "#2196F3"\n\n'
            'system_prompt: |\n'
            '  You are the Project Manager. Break down user requests into\n'
            '  clear, actionable tasks and delegate to the appropriate agents.\n\n'
            'model: claude-sonnet-4-6\n'
            'tools: [Read, Glob, Grep, Bash, mcp__task-tools__create_task]\n\n'
            'produces: [task_group, tech_design, implementation, verification]\n'
            'accepts: [task_group]\n\n'
            'routing_mode: open\n'
            'can_create_groups: true\n'
            'group_type: "FEAT"\n\n'
            'max_instances: 1\n'
            'max_turns: 30\n'
            'max_execution_time: 1800\n'
        )
        print("  Created config/roles/pm.yaml")

    # Create .env.example if not present
    env_example = project_dir / ".env.example"
    if not env_example.exists():
        env_example.write_text(
            '# Optional\n'
            'TASKBREW_API_URL=http://127.0.0.1:8420\n'
            'LOG_LEVEL=INFO\n'
        )
        print("  Created .env.example")

    print("\nProject initialized! Next steps:")
    print("  1. Add more roles in config/roles/")
    print("  2. Run: taskbrew start")


def _cmd_doctor(args):
    """Check system requirements and configuration."""
    import shutil
    import sys

    print("TaskBrew Doctor\n")
    print("Checking system requirements...\n")

    all_ok = True

    # Python version
    py_version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    if sys.version_info >= (3, 10):
        print(f"  [OK] Python {py_version}")
    else:
        print(f"  [FAIL] Python {py_version} (need 3.10+)")
        all_ok = False

    # Claude CLI
    claude_path = shutil.which("claude")
    if claude_path:
        print(f"  [OK] Claude CLI found: {claude_path}")
    else:
        print("  [WARN] Claude CLI not found (install: npm install -g @anthropic-ai/claude-code)")

    # Gemini CLI
    gemini_path = shutil.which("gemini")
    if gemini_path:
        print(f"  [OK] Gemini CLI found: {gemini_path}")
    else:
        print("  [WARN] Gemini CLI not found (install: npm install -g @google/gemini-cli)")

    # Config files
    config_dir = Path(".") / "config"
    if (config_dir / "team.yaml").exists():
        print("  [OK] config/team.yaml found")
    else:
        print("  [WARN] config/team.yaml not found (run: taskbrew init)")

    roles_dir = config_dir / "roles"
    if roles_dir.is_dir() and list(roles_dir.glob("*.yaml")):
        role_count = len(list(roles_dir.glob("*.yaml")))
        print(f"  [OK] {role_count} role(s) found in config/roles/")
    else:
        print("  [WARN] No roles found in config/roles/")

    # Validate team.yaml parses correctly
    tc = None
    if (config_dir / "team.yaml").exists():
        try:
            from taskbrew.config_loader import load_team_config
            tc = load_team_config(config_dir / "team.yaml")
            print("  [OK] team.yaml validates successfully")
        except Exception as e:
            print(f"  [FAIL] team.yaml invalid: {e}")
            all_ok = False

    # Validate each role file
    if roles_dir.is_dir():
        for role_file in sorted(roles_dir.glob("*.yaml")):
            try:
                import yaml as _yaml
                with open(role_file) as f:
                    rdata = _yaml.safe_load(f)
                if rdata:
                    from taskbrew.config_loader import _parse_role
                    _parse_role(rdata)
            except Exception as e:
                print(f"  [FAIL] Role {role_file.name}: {e}")
                all_ok = False

    # Check DB directory writability
    if tc is not None:
        try:
            from pathlib import Path as _P
            db_dir = _P(tc.db_path).expanduser().parent
            db_dir.mkdir(parents=True, exist_ok=True)
            print(f"  [OK] Database directory writable: {db_dir}")
        except Exception as e:
            print(f"  [FAIL] Database directory: {e}")
            all_ok = False

    print()
    if all_ok:
        print("All checks passed!")
    else:
        print("Some checks failed. Fix the issues above and run again.")


def cli_main():
    # Load .env before anything else so env vars are available immediately
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass  # python-dotenv is optional

    from taskbrew.logging_config import setup_logging
    setup_logging()

    parser = argparse.ArgumentParser(prog="taskbrew", description="TaskBrew — Multi-agent AI team orchestrator")
    sub = parser.add_subparsers(dest="command", help="Available commands")

    # start (default server command, alias for serve)
    start_parser = sub.add_parser("start", help="Start the orchestrator server")
    start_parser.add_argument("--project-dir", default=None, help="Project directory")
    start_parser.add_argument("--host", default=None)
    start_parser.add_argument("--port", type=int, default=None)

    # serve (kept for backwards compatibility)
    serve_parser = sub.add_parser("serve", help="Start dashboard and agent loops")
    serve_parser.add_argument("--project-dir", default=None, help="Project directory")

    # goal
    goal_parser = sub.add_parser("goal", help="Submit a new goal")
    goal_parser.add_argument("title", help="Goal title")
    goal_parser.add_argument("--description", "-d", help="Goal description", default="")
    goal_parser.add_argument("--project-dir", default=None, help="Project directory")

    # status
    status_parser = sub.add_parser("status", help="Show team status")
    status_parser.add_argument("--project-dir", default=None, help="Project directory")

    # init
    init_parser = sub.add_parser("init", help="Initialize a new project")
    init_parser.add_argument("--name", help="Project name")
    init_parser.add_argument("--dir", default=".", help="Project directory")
    init_parser.add_argument("--provider", default="claude", choices=["claude", "gemini"],
                             help="CLI provider")

    # doctor
    sub.add_parser("doctor", help="Check system requirements")

    args = parser.parse_args()

    if args.command == "init":
        _cmd_init(args)
    elif args.command == "doctor":
        _cmd_doctor(args)
    elif args.command in ("start", "serve"):
        # Normalize start/serve — both run the server via async_main
        args.command = "serve"
        if not hasattr(args, "project_dir"):
            args.project_dir = None
        asyncio.run(async_main(args))
    elif args.command in ("goal", "status"):
        asyncio.run(async_main(args))
    else:
        # No subcommand given — default to start behaviour
        args.command = "serve"
        args.project_dir = None
        asyncio.run(async_main(args))


if __name__ == "__main__":
    cli_main()
