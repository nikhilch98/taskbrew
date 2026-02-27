"""Agent loop: poll/claim/execute/complete cycle for independent agents."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from taskbrew.agents.instance_manager import InstanceManager
from taskbrew.config_loader import RoleConfig
from taskbrew.intelligence.clarification import ClarificationDetector
from taskbrew.intelligence.execution import CommitPlanner, DebuggingHelper
from taskbrew.orchestrator.event_bus import EventBus
from taskbrew.orchestrator.task_board import TaskBoard

logger = logging.getLogger(__name__)

DEFAULT_TASK_TIMEOUT = 1800  # 30 minutes
MAX_RETRIES = 3
RETRY_BASE_DELAY = 5

if TYPE_CHECKING:
    from taskbrew.tools.worktree_manager import WorktreeManager


class AgentLoop:
    """Continuous loop that polls for tasks, executes them via the Claude SDK,
    and hands off results.

    Parameters
    ----------
    instance_id:
        Unique identifier for this agent instance.
    role_config:
        Role configuration for this agent.
    board:
        TaskBoard for claiming and completing tasks.
    event_bus:
        EventBus for emitting lifecycle events.
    instance_manager:
        InstanceManager for status tracking.
    all_roles:
        Full mapping of role name to RoleConfig (for routing context).
    cli_path:
        Optional path to the Claude CLI binary.
    project_dir:
        Working directory for the agent.
    poll_interval:
        Seconds between poll attempts when idle.
    worktree_manager:
        Optional WorktreeManager for git worktree isolation.  When provided
        the agent runs each task in its own worktree so it never touches the
        main checkout.
    """

    def __init__(
        self,
        instance_id: str,
        role_config: RoleConfig,
        board: TaskBoard,
        event_bus: EventBus,
        instance_manager: InstanceManager,
        all_roles: dict[str, RoleConfig],
        cli_path: str | None = None,
        project_dir: str = ".",
        poll_interval: float = 5.0,
        api_url: str = "http://127.0.0.1:8420",
        worktree_manager: WorktreeManager | None = None,
        memory_manager=None,
        context_registry=None,
        commit_planner: CommitPlanner | None = None,
        debugging_helper: DebuggingHelper | None = None,
        clarification_detector: ClarificationDetector | None = None,
        observability_manager=None,
        cli_provider: str = "claude",
        mcp_servers: dict | None = None,
    ) -> None:
        self.instance_id = instance_id
        self.role_config = role_config
        self.board = board
        self.event_bus = event_bus
        self.instance_manager = instance_manager
        self.all_roles = all_roles
        self.cli_path = cli_path
        self.project_dir = project_dir
        self.poll_interval = poll_interval
        self.api_url = api_url
        self.worktree_manager = worktree_manager
        self.memory_manager = memory_manager
        self.context_registry = context_registry
        self._commit_planner = commit_planner
        self._debugging_helper = debugging_helper
        self._clarification_detector = clarification_detector or ClarificationDetector()
        self._observability_manager = observability_manager
        self.cli_provider = cli_provider
        self.mcp_servers = mcp_servers
        self._running = False

    async def poll_for_task(self) -> dict | None:
        """Claim next pending task for this role."""
        return await self.board.claim_task(
            role=self.role_config.role, instance_id=self.instance_id
        )

    async def build_context(self, task: dict) -> str:
        """Build prompt context from task data and parent artifacts."""
        parts: list[str] = []
        parts.append(
            f"You are {self.role_config.display_name} (instance {self.instance_id}).\n"
        )
        parts.append("## Your Task")
        parts.append(f"**{task['id']}**: {task['title']}")
        parts.append(f"Type: {task['task_type']} | Priority: {task['priority']}")
        parts.append(f"Group: {task['group_id']}")

        if task.get("description"):
            parts.append(f"\n## Description\n{task['description']}")

        if (
            task.get("parent_id")
            and "parent_artifact" in self.role_config.context_includes
        ):
            parent = await self.board.get_task(task["parent_id"])
            if parent:
                parts.append(
                    f"\n## Parent Task ({parent['id']}): {parent['title']}"
                )
                if parent.get("description"):
                    parts.append(f"Description: {parent['description']}")
                if parent.get("output_text"):
                    parts.append(
                        f"\n### Parent Output:\n{parent['output_text']}"
                    )

        # --- Rejection Context Forwarding ---
        if task.get("revision_of"):
            original = await self.board.get_task(task["revision_of"])
            if original:
                reason = original.get("rejection_reason") or "No reason provided"
                parts.append("\n## Revision Context")
                parts.append(
                    f"This is a revision of task {original['id']}. "
                    f"The original was rejected/failed because:"
                )
                parts.append(reason)
                parts.append(
                    "Please address the feedback above in your implementation."
                )

        # --- Sibling Task Summary (capped for token efficiency) ---
        if "sibling_summary" in self.role_config.context_includes:
            group_id = task["group_id"]
            group_tasks = await self.board.get_group_tasks(group_id)
            completed = [t for t in group_tasks if t["status"] == "completed"]
            in_progress = [t for t in group_tasks if t["status"] == "in_progress"]
            pending = [
                t for t in group_tasks
                if t["status"] in ("pending", "blocked")
            ]
            parts.append(f"\n## Group Progress ({group_id})")
            parts.append(f"- Completed: {len(completed)} tasks")
            parts.append(f"- In Progress: {len(in_progress)} tasks")
            parts.append(f"- Pending: {len(pending)} tasks")
            if completed:
                recent = completed[-10:]  # cap at last 10 for token efficiency
                titles = ", ".join(t["title"] for t in recent)
                parts.append(f"Recently completed: {titles}")
            if in_progress:
                ip_titles = ", ".join(t["title"] for t in in_progress)
                parts.append(f"In progress: {ip_titles}")

        # --- Routing: Agent manifest or restricted hints ---
        routing_mode = getattr(self.role_config, "routing_mode", "open")
        if routing_mode == "open" and self.all_roles:
            parts.append("\n## Available Agents")
            parts.append("You may create tasks for any of these agents:\n")
            for name, role in self.all_roles.items():
                if name == self.role_config.role:
                    continue
                accepts = ", ".join(role.accepts) if role.accepts else "any"
                parts.append(
                    f"- **{role.display_name}** ({role.prefix}): "
                    f'assigned_to="{name}", accepts: [{accepts}]'
                )
            parts.append(
                '\nUse create_task(assigned_to="<role>", task_type="<type>") '
                "to delegate work."
            )
        elif self.role_config.routes_to:
            parts.append("\n## When Complete")
            parts.append("Create tasks for:")
            for route in self.role_config.routes_to:
                parts.append(
                    f"- **{route.role}** (types: {', '.join(route.task_types)})"
                )

        # --- Agent Memory ---
        if self.memory_manager and "agent_memory" in self.role_config.context_includes:
            try:
                memories = await self.memory_manager.recall(
                    self.role_config.role, task.get("title", "") + " " + task.get("description", "")[:200],
                    limit=5,
                )
                if memories:
                    parts.append("\n## Past Lessons & Knowledge")
                    for m in memories:
                        parts.append(f"- [{m['memory_type']}] {m['title']}: {m['content'][:150]}")
            except Exception:
                logger.warning("Memory recall failed, continuing without memory context", exc_info=True)

        # --- Context Providers ---
        if self.context_registry:
            try:
                provider_names = [
                    name for name in self.context_registry.get_available_providers()
                    if name in self.role_config.context_includes
                ]
                if provider_names:
                    extra_context = await self.context_registry.get_context(provider_names)
                    if extra_context:
                        parts.append(f"\n{extra_context}")
            except Exception:
                logger.warning("Context provider failed, continuing without provider context", exc_info=True)

        # --- Ambiguity Detection ---
        if self._clarification_detector:
            try:
                ambiguity = await self._clarification_detector.detect_ambiguity(
                    title=task.get("title", ""),
                    description=task.get("description", ""),
                )
                if ambiguity.get("is_ambiguous"):
                    issues = ambiguity.get("issues", [])
                    issues_text = "\n".join(f"- {issue}" for issue in issues)
                    parts.append(
                        f"\n## Ambiguity Warning\n"
                        f"Score: {ambiguity.get('ambiguity_score', 0)}\n"
                        f"{issues_text}"
                    )
            except Exception:
                logger.warning("Ambiguity detection failed, continuing without check", exc_info=True)

        # --- Learned Conventions Context ---
        try:
            conventions = await self.board._db.execute_fetchall(
                "SELECT convention_type, pattern, example FROM codebase_conventions LIMIT 10"
            )
            if conventions:
                parts.append("\n## Code Conventions (Learned)")
                for c in conventions:
                    example = f" (e.g., {c['example']})" if c.get('example') else ""
                    parts.append(f"- {c['convention_type']}: {c['pattern']}{example}")
        except Exception as e:
            logger.warning("Context provider failed: %s", e)

        # --- Error Prevention Hints ---
        try:
            task_type = task.get("task_type", "")
            if task_type:
                clusters = await self.board._db.execute_fetchall(
                    "SELECT cluster_name, prevention_hint FROM error_clusters "
                    "WHERE prevention_hint IS NOT NULL LIMIT 5"
                )
                if clusters:
                    parts.append("\n## Known Error Patterns")
                    for c in clusters:
                        parts.append(f"- **{c['cluster_name']}**: {c['prevention_hint']}")
        except Exception as e:
            logger.warning("Context provider failed: %s", e)

        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Structured logging helpers
    # ------------------------------------------------------------------

    def _make_logger(
        self, task: dict
    ) -> tuple[logging.LoggerAdapter, str]:
        """Return a ``LoggerAdapter`` with task and correlation IDs.

        The correlation ID is built from the task ID and the current
        timestamp so that every execution attempt is uniquely traceable.
        """
        correlation_id = f"{task['id']}-{int(time.time())}"
        adapter = logging.LoggerAdapter(logger, {
            "task_id": task["id"],
            "agent_id": self.instance_id,
            "correlation_id": correlation_id,
        })
        return adapter, correlation_id

    # ------------------------------------------------------------------
    # Task execution
    # ------------------------------------------------------------------

    async def execute_task(
        self,
        task: dict,
        worktree_path: str | None = None,
        branch_name: str | None = None,
    ) -> str:
        """Run Claude SDK agent. Returns output text.

        Parameters
        ----------
        task:
            The task dict to execute.
        worktree_path:
            Optional path to a pre-created git worktree.
        branch_name:
            Optional branch name for the worktree context.
        """
        from taskbrew.agents.base import AgentRunner
        from taskbrew.config import AgentConfig

        cwd = worktree_path or self.project_dir

        agent_config = AgentConfig(
            name=self.instance_id,
            role=self.role_config.role,
            system_prompt=self.role_config.system_prompt,
            allowed_tools=self.role_config.tools,
            model=self.role_config.model,
            max_turns=self.role_config.max_turns,
            cwd=cwd,
            api_url=self.api_url,
            db_path=str(self.board._db.db_path),
            cli_provider=self.cli_provider,
            mcp_servers=self.mcp_servers,
        )
        runner = AgentRunner(
            config=agent_config,
            cli_path=self.cli_path,
            event_bus=self.event_bus,
        )
        context = await self.build_context(task)

        if worktree_path:
            context += (
                f"\n\n## Git Worktree\n"
                f"You are working in an isolated git worktree on branch "
                f"`{branch_name}`.  Commit your changes directly to this "
                f"branch — do NOT create new branches or switch branches."
            )

        output = await runner.run(prompt=context, cwd=cwd)

        # Record usage from SDK
        if runner.last_usage:
            u = runner.last_usage.get("usage") or {}
            await self.board.record_task_usage(
                task_id=task["id"],
                agent_id=self.instance_id,
                input_tokens=u.get("input_tokens", 0),
                output_tokens=u.get("output_tokens", 0),
                cost_usd=runner.last_usage.get("cost_usd") or 0,
                duration_api_ms=runner.last_usage.get("duration_api_ms", 0),
                num_turns=runner.last_usage.get("num_turns", 0),
            )

        return output

    async def complete_and_handoff(self, task: dict, output: str) -> None:
        """Mark task complete, store output, and emit event.

        Before completing, checks for existing downstream/handoff tasks to
        prevent duplicates when this method is reached after a retry.
        """
        # Guard against duplicate handoff tasks created by retries
        existing = await self.board._db.execute_fetchone(
            "SELECT id FROM tasks WHERE parent_id = ? AND status != 'cancelled'",
            (task["id"],),
        )
        if existing:
            logger.warning(
                "Skipping duplicate handoff for %s (already exists: %s) — "
                "completing task but not creating new downstream tasks",
                task["id"],
                existing["id"],
            )

        # Always complete the current task, even if downstream tasks already exist
        await self.board.complete_task_with_output(task["id"], output)
        await self.event_bus.emit(
            "task.completed",
            {
                "task_id": task["id"],
                "group_id": task["group_id"],
                "agent_id": self.instance_id,
                "model": self.role_config.model,
            },
        )
        # Plan commit message from task output (optional)
        if self._commit_planner:
            try:
                await self._commit_planner.plan_commit(
                    task_id=task["id"],
                    files=[],  # Files are determined by the agent's actual changes
                    message=f"{task.get('task_type', 'feat')}({task['id']}): {task['title'][:60]}",
                )
            except Exception:
                logger.debug("Failed to plan commit for %s", task["id"], exc_info=True)

        # Store lesson from successful completion
        if self.memory_manager:
            try:
                await self.memory_manager.store_lesson(
                    role=self.role_config.role,
                    title=f"Completed: {task['title'][:80]}",
                    content=output[:500] if output else "",
                    source_task_id=task["id"],
                    tags=[task.get("task_type", "general")],
                )
            except Exception:
                logger.debug("Failed to store lesson for %s", task["id"], exc_info=True)

        # --- Decision Audit Trail (Observability) ---
        if self._observability_manager:
            try:
                await self._observability_manager.ensure_tables()
                await self._observability_manager.log_decision(
                    agent_id=self.instance_id,
                    decision_type="task_completion",
                    decision=f"Completed task {task['id']}",
                    reasoning=f"Task type: {task.get('task_type', 'unknown')}, "
                              f"output length: {len(output) if output else 0} chars",
                    task_id=task["id"],
                )
            except Exception:
                logger.debug("Decision audit logging failed for %s", task["id"], exc_info=True)

        # --- Cost Attribution (Observability) ---
        if self._observability_manager:
            try:
                # Get usage data if available
                usage_row = await self.board._db.execute_fetchone(
                    "SELECT input_tokens, output_tokens, cost_usd FROM task_usage WHERE task_id = ? ORDER BY created_at DESC LIMIT 1",
                    (task["id"],),
                )
                if usage_row:
                    await self._observability_manager.attribute_cost(
                        agent_id=self.instance_id,
                        cost_usd=usage_row.get("cost_usd", 0) or 0,
                        input_tokens=usage_row.get("input_tokens", 0) or 0,
                        output_tokens=usage_row.get("output_tokens", 0) or 0,
                        task_id=task["id"],
                        feature_tag=task.get("task_type"),
                    )
            except Exception:
                logger.debug("Cost attribution failed for %s", task["id"], exc_info=True)

    async def _heartbeat_loop(self):
        """Background heartbeat that runs during task execution."""
        while True:
            await asyncio.sleep(15)
            try:
                await self.instance_manager.heartbeat(self.instance_id)
            except Exception:
                logger.warning(
                    "Heartbeat failed for %s", self.instance_id, exc_info=True,
                )

    async def run_once(self) -> bool:
        """One poll/claim/execute/complete cycle. Returns True if task processed."""
        # Skip polling if role is paused
        if self.instance_manager.is_role_paused(self.role_config.role):
            current = await self.instance_manager.get_instance(self.instance_id)
            if current and current["status"] != "paused":
                await self.instance_manager.update_status(self.instance_id, "paused")
                await self.event_bus.emit("agent.status_changed", {
                    "instance_id": self.instance_id, "status": "paused", "role": self.role_config.role,
                    "model": self.role_config.model,
                })
            return False

        # If was paused but now resumed, set back to idle
        current = await self.instance_manager.get_instance(self.instance_id)
        if current and current["status"] == "paused":
            await self.instance_manager.update_status(self.instance_id, "idle")
            await self.event_bus.emit("agent.status_changed", {
                "instance_id": self.instance_id, "status": "idle", "role": self.role_config.role,
                "model": self.role_config.model,
            })

        task = await self.poll_for_task()
        if task is None:
            return False

        # Create a correlated logger for this task execution
        task_logger, correlation_id = self._make_logger(task)

        task_logger.info("Agent %s claimed task %s: %s", self.instance_id, task["id"], task["title"])
        await self.instance_manager.update_status(
            self.instance_id, "working", current_task=task["id"]
        )
        await self.event_bus.emit(
            "task.claimed",
            {
                "task_id": task["id"],
                "claimed_by": self.instance_id,
                "model": self.role_config.model,
                "correlation_id": correlation_id,
            },
        )

        # Create worktree ONCE, outside the retry loop
        worktree_path: str | None = None
        branch_name: str | None = None
        if self.worktree_manager:
            branch_name = f"feat/{task['id'].lower()}"
            worktree_path = await self.worktree_manager.create_worktree(
                agent_name=self.instance_id,
                branch_name=branch_name,
            )
            logger.info(
                "Agent %s using worktree %s (branch %s)",
                self.instance_id, worktree_path, branch_name,
            )

        try:
            timeout = getattr(self.role_config, 'max_execution_time', DEFAULT_TASK_TIMEOUT)
            hb_task = asyncio.create_task(self._heartbeat_loop())
            try:
                for attempt in range(MAX_RETRIES + 1):
                    try:
                        output = await asyncio.wait_for(
                            self.execute_task(
                                task,
                                worktree_path=worktree_path,
                                branch_name=branch_name,
                            ),
                            timeout=timeout,
                        )
                        break  # success
                    except asyncio.TimeoutError:
                        # Don't retry timeouts
                        task_logger.error("Task %s timed out after %ds", task["id"], timeout)
                        await self.board.fail_task(task["id"])
                        await self.event_bus.emit(
                            "task.failed",
                            {
                                "task_id": task["id"],
                                "instance_id": self.instance_id,
                                "reason": "timeout",
                                "model": self.role_config.model,
                                "correlation_id": correlation_id,
                            },
                        )
                        return True
                    except Exception as e:
                        if attempt < MAX_RETRIES:
                            delay = RETRY_BASE_DELAY * (3 ** attempt)
                            task_logger.warning(
                                "Task %s attempt %d failed, retrying in %ds: %s",
                                task["id"], attempt + 1, delay, e,
                            )
                            await asyncio.sleep(delay)
                        else:
                            raise  # let outer handler fail the task
            finally:
                hb_task.cancel()
                try:
                    await hb_task
                except (asyncio.CancelledError, Exception):
                    pass

            task_logger.info("Agent %s completed task %s", self.instance_id, task["id"])
            await self.complete_and_handoff(task, output)
        except Exception as e:
            task_logger.error("Agent %s failed task %s: %s", self.instance_id, task["id"], e, exc_info=True)
            await self.board.fail_task(task["id"])

            # Generate debugging context for the failure
            debug_context = None
            if self._debugging_helper:
                try:
                    debug_context = await self._debugging_helper.get_failure_context(task["id"])
                    suggestions = await self._debugging_helper.suggest_fix(task["id"])
                    task_logger.info(
                        "Debugging suggestions for %s: %s",
                        task["id"],
                        suggestions.get("suggestions", []),
                    )
                except Exception:
                    logger.debug("DebuggingHelper failed for %s", task["id"], exc_info=True)

            await self.event_bus.emit(
                "task.failed",
                {
                    "task_id": task["id"],
                    "instance_id": self.instance_id,
                    "error": str(e),
                    "model": self.role_config.model,
                    "correlation_id": correlation_id,
                    "debug_context": debug_context,
                },
            )
            # Store failure post-mortem
            if self.memory_manager:
                try:
                    await self.memory_manager.store_postmortem(
                        task_id=task["id"],
                        role=self.role_config.role,
                        analysis=str(e),
                        root_cause="Unhandled exception during execution",
                        prevention="Review error handling for this task type",
                    )
                except Exception:
                    logger.debug("Failed to store post-mortem for %s", task["id"], exc_info=True)
        finally:
            if self.worktree_manager:
                try:
                    await self.worktree_manager.cleanup_worktree(self.instance_id)
                except Exception:
                    logger.warning(
                        "Failed to cleanup worktree for %s", self.instance_id,
                        exc_info=True,
                    )
            await self.instance_manager.update_status(self.instance_id, "idle", current_task=None)

        return True

    async def run(self) -> None:
        """Main continuous loop."""
        self._running = True
        await self.instance_manager.register_instance(
            self.instance_id, self.role_config
        )
        await self.event_bus.emit(
            "agent.status_changed",
            {"instance_id": self.instance_id, "status": "idle", "model": self.role_config.model},
        )
        logger.info("Agent %s started, polling every %ss", self.instance_id, self.poll_interval)

        while self._running:
            try:
                processed = await self.run_once()
                if not processed:
                    await asyncio.sleep(self.poll_interval)
            except Exception:
                logger.exception("Agent %s crashed in run_once, recovering", self.instance_id)
                await self.instance_manager.update_status(self.instance_id, "idle")
                await asyncio.sleep(self.poll_interval)
            await self.instance_manager.heartbeat(self.instance_id)

        # Cleanup after loop exits
        await self.instance_manager.update_status(self.instance_id, "stopped")
        await self.event_bus.emit("agent.stopped", {"instance_id": self.instance_id, "model": self.role_config.model})

    def stop(self) -> None:
        """Signal the run loop to stop after the current iteration."""
        self._running = False
