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


# Retry classification
# docs/superpowers/specs/2026-04-24-retry-classification-design.md
#
# Default is non-retryable. We opt in known-transient types here
# rather than opt out; unknown exceptions fail fast so hidden bugs
# surface immediately instead of hiding behind 65s of backoff.
_RETRYABLE_MESSAGE_SUBSTRINGS = (
    "rate limit",
    "429",
    "503",
    "504",
    "connection reset",
    "connection refused",
)


def _retryable_type_set() -> tuple[type, ...]:
    """Return the tuple of exception types treated as retryable.

    Deferred import so ``agent_loop`` doesn't hard-fail when the
    Anthropic SDK isn't installed; the stdlib set still works.
    """
    types: list[type] = [ConnectionError, OSError]
    try:
        from anthropic import (
            APIConnectionError,
            APITimeoutError,
            InternalServerError,
            RateLimitError,
        )
        types.extend([
            RateLimitError,
            APIConnectionError,
            APITimeoutError,
            InternalServerError,
        ])
    except ImportError:
        pass
    return tuple(types)


def _is_retryable(exc: BaseException) -> bool:
    """Classify an exception as retryable (transient) or not.

    Retryable: rate limits, connection resets, 5xx server errors --
    anything where trying the same prompt again might succeed.

    Non-retryable: schema / validation / tool / authentication errors
    -- retrying with the same prompt gets the same result.

    Unknown exceptions default to non-retryable. Operators who hit a
    false-positive on a genuinely transient error lose at most two
    retries; operators whose real bugs used to hide behind the retry
    loop see them immediately.
    """
    if isinstance(exc, _retryable_type_set()):
        return True
    msg = str(exc).lower()
    return any(token in msg for token in _RETRYABLE_MESSAGE_SUBSTRINGS)

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
        preflight_checker=None,
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
        # audit 06b F#12: PreflightChecker previously ran only from the
        # dashboard API (a human-triggered path). If supplied, we run it
        # between claim and execute; failures are logged and the task is
        # returned to the board rather than silently executed under
        # failing preconditions.
        self.preflight_checker = preflight_checker
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

        # If this is a verification retry, surface the previous failures
        # at the top of the prompt so the agent knows what to fix.
        # Design: docs/superpowers/specs/2026-04-24-per-task-completion-checks-design.md
        verif_retries = task.get("verification_retries") or 0
        if verif_retries > 0:
            import json as _json
            raw_checks = task.get("completion_checks") or "{}"
            try:
                prior = _json.loads(raw_checks) if isinstance(raw_checks, str) else (raw_checks or {})
            except _json.JSONDecodeError:
                prior = {}
            failed = {n: c for n, c in prior.items()
                      if isinstance(c, dict) and c.get("status") == "fail"}
            if failed:
                parts.append(
                    f"\n## Previous verification failed (attempt {verif_retries}/2)"
                )
                parts.append(
                    "The following checks failed on the last run. Fix these "
                    "and re-run `record_check` for each before completing:"
                )
                for name, entry in failed.items():
                    line = f"- **{name}**: {entry.get('details') or 'no details'}"
                    if entry.get("command"):
                        line += f" (command: `{entry['command']}`)"
                    parts.append(line)
                    # Structured failure feedback: if the agent saved
                    # full stderr / logs to artifact files, point the
                    # retry agent at them so it has the raw output
                    # instead of the summarised ``details`` string.
                    # Design:
                    # docs/superpowers/specs/2026-04-24-structured-failure-feedback-design.md
                    for ap in entry.get("artifact_paths") or []:
                        parts.append(f"  - Full output at: `{ap}`")

                # Also surface which files the previous attempt
                # modified, so the retry can Read them first rather
                # than re-discovering via Grep / Glob.
                modified = await self._list_modified_files(task)
                if modified:
                    parts.append("\n### Files you previously modified")
                    for path in modified[:30]:
                        parts.append(f"- `{path}`")
                    if len(modified) > 30:
                        parts.append(
                            f"- ... and {len(modified) - 30} more "
                            "(truncated)"
                        )

                parts.append(
                    "\nRead these files and any linked artifacts "
                    "before attempting the fix."
                )

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

        # --- Routing: Pipeline-based connections ---
        # Try to load pipeline edges for this agent's outbound connections
        pipeline_connections = []
        try:
            from taskbrew.dashboard.routers.pipeline_editor import get_pipeline
            pipeline = get_pipeline()
            if pipeline and pipeline.edges:
                pipeline_connections = [
                    e for e in pipeline.edges
                    if e.from_agent == self.role_config.role
                ]
        except Exception:
            pass  # Pipeline not initialized yet

        routing_mode = getattr(self.role_config, "routing_mode", "open")
        if routing_mode == "open" and self.all_roles:
            parts.append("\n## Available Agents")
            parts.append("You may create tasks for any of these agents:")
            for name, role in self.all_roles.items():
                if name == self.role_config.role:
                    continue
                accepts = ", ".join(role.accepts) if role.accepts else "any"
                prefix = f" ({role.prefix})" if getattr(role, "prefix", "") else ""
                parts.append(
                    f"- **{role.display_name}**{prefix}: "
                    f'assigned_to="{name}", accepts: [{accepts}]'
                )
            parts.append(
                '\nUse create_task(assigned_to="<role>", task_type="<type>") '
                "to delegate work."
            )
        elif pipeline_connections:
            parts.append("\n## Connected Agents")
            parts.append("You can route tasks to these agents:")
            for edge in pipeline_connections:
                target_role = self.all_roles.get(edge.to_agent)
                display = target_role.display_name if target_role else edge.to_agent
                task_types_str = ", ".join(edge.task_types) if edge.task_types else "any"
                parts.append(
                    f"- **{display}**: "
                    f'assigned_to="{edge.to_agent}", task_types: [{task_types_str}]'
                )
            parts.append(
                '\nUse create_task(assigned_to="<role>", task_type="<type>") '
                "to delegate work. Do NOT route to agents not listed above."
            )
        elif self.role_config.routes_to:
            # Fallback to legacy routes_to if no pipeline edges
            parts.append("\n## Connected Agents")
            parts.append("You can route tasks to:")
            for route in self.role_config.routes_to:
                parts.append(
                    f"- **{route.role}** (types: {', '.join(route.task_types)})"
                )
            parts.append(
                '\nUse create_task(assigned_to="<role>", task_type="<type>") '
                "to delegate work."
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
                "SELECT convention_type, pattern, examples FROM codebase_conventions LIMIT 10"
            )
            if conventions:
                parts.append("\n## Code Conventions (Learned)")
                for c in conventions:
                    example = f" (e.g., {c['examples']})" if c.get('examples') else ""
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

        # Activity callback for the idle watchdog. Each SDK message
        # (tool use / text block / result) bumps the timestamp so an
        # actively-working agent never trips the timeout.
        import time as _time

        def _on_activity():
            self._last_activity_ts = _time.monotonic()

        output = await runner.run(
            prompt=context, cwd=cwd, on_activity=_on_activity,
        )

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

    async def complete_and_handoff(
        self,
        task: dict,
        output: str,
        worktree_path: str | None = None,
        branch_name: str | None = None,
    ) -> None:
        """Mark task complete, store output, and emit event.

        Before completing, applies Stage-1 gates:
          * Fix #2 — if this was a tech_design that should have fanned out to
            coder/verifier children and didn't, re-queue it (up to 2 retries,
            then escalate).
          * Fix #1 — if this was a substantial implementation task with no
            verifier child task, auto-create one so the merge never slips
            past review.

        Before completing, checks for existing downstream/handoff tasks to
        prevent duplicates when this method is reached after a retry.
        """
        # --- Stage-1 Fix #2: Architect fan-out gate -------------------------
        if await self._should_require_fanout(task):
            actionable = await self._count_actionable_children(task["id"])
            if actionable == 0:
                retries = task.get("fanout_retries") or 0
                if retries < 2:
                    await self._requeue_for_fanout(task, retries)
                    return
                # Exhausted retries — surface for human, fall through to
                # complete so the queue doesn't stall; the event + parent-row
                # state marks the task as needing human attention.
                await self.event_bus.emit(
                    "task.escalation_required",
                    {
                        "task_id": task["id"],
                        "group_id": task["group_id"],
                        "reason": "fanout_missing_after_retries",
                        "retries": retries,
                    },
                )
                logger.error(
                    "Task %s still has no actionable children after %d "
                    "fan-out retries; completing anyway and escalating.",
                    task["id"], retries,
                )

        # --- Stage-1 Fix #1: Auto-create verifier task if one is required ---
        # Unlike the fan-out gate, we don't re-queue here — the coder already
        # did the work. We just make sure a VR row exists so the merge flow
        # can't be skipped. confido lost 17 branches because coder tasks
        # self-marked complete without VR.
        if await self._should_require_verification(
            task, worktree_path=worktree_path, branch_name=branch_name,
        ):
            if not await self._has_verification_child(task["id"]):
                try:
                    vr = await self.board.create_task(
                        group_id=task["group_id"],
                        title=f"Verify {task['id']}: {task['title'][:80]}",
                        task_type="verification",
                        assigned_to="verifier",
                        created_by=self.instance_id,
                        parent_id=task["id"],
                        priority=task.get("priority", "high"),
                        description=(
                            f"Auto-generated verification task for {task['id']} "
                            f"(coder did not create one). Review the branch "
                            f"`{branch_name or 'main'}` and merge if correct."
                        ),
                    )
                    await self.event_bus.emit(
                        "task.auto_verification_created",
                        {
                            "task_id": task["id"],
                            "vr_id": vr["id"],
                            "reason": "coder_omitted_verification",
                        },
                    )
                    logger.warning(
                        "Auto-created verification task %s for %s "
                        "(coder did not create one)",
                        vr["id"], task["id"],
                    )
                except Exception:
                    # The verification gate only works if the VR row
                    # actually exists. If create_task raises, we must
                    # NOT fall through to complete_task_with_output --
                    # that would mark the parent completed without a
                    # verification record, which is exactly the
                    # merge-skip bug this gate was added to prevent.
                    # Raise so the outer run_once handler runs
                    # fail_task on the parent; the next poll cycle (or
                    # orphan recovery) will re-attempt.
                    logger.error(
                        "Failed to auto-create verification task for %s; "
                        "failing the parent so the merge gate is preserved",
                        task["id"], exc_info=True,
                    )
                    raise

        # --- Verification gate (per-task completion_checks) ----------------
        # The agent records build/test/lint outcomes via the record_check
        # MCP tool during its work. Here we read them and decide:
        #   - any check == fail -> re-queue up to 2x, then escalate
        #   - empty -> merge as merged_unverified (fail-open default)
        #   - all pass -> merge as merged
        # Design: docs/superpowers/specs/2026-04-24-per-task-completion-checks-design.md
        import json as _json
        raw_checks = task.get("completion_checks") or "{}"
        try:
            checks_map = _json.loads(raw_checks) if isinstance(raw_checks, str) else (raw_checks or {})
        except _json.JSONDecodeError:
            checks_map = {}
        failed_checks = [
            name for name, c in checks_map.items()
            if isinstance(c, dict) and c.get("status") == "fail"
        ]

        merge_status: str | None = None
        if failed_checks:
            retries = task.get("verification_retries") or 0
            if retries < 2:
                await self._requeue_for_verification(
                    task, retries, failed_checks, checks_map,
                )
                return
            # Exhausted — escalate, fall through to complete so the queue
            # doesn't stall. Dashboard surfaces via merge_status.
            await self.event_bus.emit(
                "task.escalation_required",
                {
                    "task_id": task["id"],
                    "group_id": task["group_id"],
                    "reason": "verification_failed_after_retries",
                    "failed_checks": failed_checks,
                    "retries": retries,
                },
            )
            logger.error(
                "Task %s still has failing checks %s after %d retries; "
                "completing with merge_status=verification_failed and escalating.",
                task["id"], failed_checks, retries,
            )
            merge_status = "verification_failed"
        elif not checks_map:
            merge_status = "merged_unverified"
            await self.event_bus.emit(
                "task.unverified_merge",
                {
                    "task_id": task["id"],
                    "group_id": task["group_id"],
                    "agent_id": self.instance_id,
                },
            )
        else:
            merge_status = "merged"

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
        if merge_status is not None:
            # Persist the gate's decision on the task row so the dashboard
            # can filter / count without re-running the gate logic.
            await self.board._db.execute(
                "UPDATE tasks SET merge_status = ? WHERE id = ?",
                (merge_status, task["id"]),
            )
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

    # ------------------------------------------------------------------
    # Stage-1 completion gate helpers (Fix #1 + Fix #2)
    # ------------------------------------------------------------------

    _FANOUT_REQUIRED_TASK_TYPES = {"tech_design"}
    _VERIFICATION_REQUIRED_TASK_TYPES = {"implementation", "bug_fix", "revision"}
    _VR_DIFF_LOC_THRESHOLD = 20
    # Diff size at which VR is always required regardless of how clean
    # the coder's self-recorded checks are. Semantic review carries its
    # weight at scale; we don't trust "build+tests+lint pass" to cover
    # architectural regressions on a 500-line patch.
    # Design:
    # docs/superpowers/specs/2026-04-24-vr-gate-skip-clean-checks-design.md
    _VR_DIFF_LOC_HARD_CEILING = 200

    async def _should_require_fanout(self, task: dict) -> bool:
        """Return True when the fan-out gate should enforce a child task.

        Explicit ``requires_fanout`` wins over the task_type default so
        research/ADR/docs-only designs can opt out at creation time.
        """
        rf = task.get("requires_fanout")
        if rf is not None:
            # SQLite stores bool as INTEGER — 0/1 or already bool.
            return bool(rf)
        return task.get("task_type") in self._FANOUT_REQUIRED_TASK_TYPES

    async def _count_actionable_children(self, task_id: str) -> int:
        """Count non-cancelled children that represent actual downstream work.

        "Actionable" means the child is routed to a role that does work
        against the parent's design (coder, verifier, reviewer, integrator).
        Peer architect reviews don't count — they don't produce code.
        """
        row = await self.board._db.execute_fetchone(
            "SELECT COUNT(*) AS n FROM tasks "
            "WHERE parent_id = ? AND status != 'cancelled' "
            "AND assigned_to IN ('coder', 'verifier', 'reviewer', 'integrator')",
            (task_id,),
        )
        return int(row["n"] or 0) if row else 0

    async def _requeue_for_fanout(self, task: dict, current_retries: int) -> None:
        """Return a design task to ``pending`` so the architect gets another
        chance to create coder tasks. Bumps ``fanout_retries`` so we cap at 2.

        The predicate ``AND status = 'in_progress'`` is load-bearing:
        without it a concurrent ``cancel_task`` or ``fail_task`` during
        the architect's execution window gets silently resurrected
        back to ``pending``.
        """
        await self.board._db.execute(
            "UPDATE tasks "
            "SET status = 'pending', claimed_by = NULL, started_at = NULL, "
            "    fanout_retries = ? "
            "WHERE id = ? AND status = 'in_progress'",
            (current_retries + 1, task["id"]),
        )
        # Wake any idle architect so the re-queued design gets picked
        # up immediately rather than waiting out poll_interval.
        await self.event_bus.emit(
            "task.available",
            {
                "task_id": task["id"],
                "role": task.get("assigned_to"),
                "group_id": task["group_id"],
            },
        )

    async def _requeue_for_verification(
        self,
        task: dict,
        current_retries: int,
        failed_checks: list[str],
        checks_map: dict,
    ) -> None:
        """Return a task to ``pending`` so the coder can fix failing checks.

        Mirrors ``_requeue_for_fanout`` in every important respect: the
        ``AND status = 'in_progress'`` predicate prevents a concurrent
        cancel / fail from being silently resurrected, and
        ``verification_retries`` caps the loop at 2 before escalation.

        ``failed_checks`` is the list of check names that had
        ``status=fail``; ``checks_map`` is the full check dict.  Both
        are echoed into the emitted event and used when the next
        ``build_context`` call formats a ``## Previous verification
        failed`` block for the agent to read.
        """
        await self.board._db.execute(
            "UPDATE tasks "
            "SET status = 'pending', claimed_by = NULL, started_at = NULL, "
            "    verification_retries = ? "
            "WHERE id = ? AND status = 'in_progress'",
            (current_retries + 1, task["id"]),
        )
        await self.event_bus.emit(
            "task.completion_blocked",
            {
                "task_id": task["id"],
                "group_id": task["group_id"],
                "reason": "verification_failed",
                "failed_checks": failed_checks,
                "retries": current_retries + 1,
                "agent_id": self.instance_id,
            },
        )
        # Wake the coder immediately to fix the failing checks.
        await self.event_bus.emit(
            "task.available",
            {
                "task_id": task["id"],
                "role": task.get("assigned_to"),
                "group_id": task["group_id"],
            },
        )
        logger.warning(
            "Task %s has failing checks %s; re-queued for fix (attempt %d/2).",
            task["id"], failed_checks, current_retries + 1,
        )
        await self.event_bus.emit(
            "task.completion_blocked",
            {
                "task_id": task["id"],
                "group_id": task["group_id"],
                "reason": "fanout_required",
                "retries": current_retries + 1,
                "agent_id": self.instance_id,
            },
        )
        logger.warning(
            "Task %s returned without creating coder/verifier tasks; "
            "re-queued for fan-out (attempt %d/2).",
            task["id"], current_retries + 1,
        )

    async def _should_require_verification(
        self,
        task: dict,
        worktree_path: str | None,
        branch_name: str | None,
    ) -> bool:
        """Return True when the merge gate should demand a verifier child.

        Four-part decision (in order):

        1. Non-implementation task types (docs, research) are exempt.
        2. No verifier role configured -> skip (no point creating an
           orphan task that will never be claimed).
        3. Diff below the floor (``_VR_DIFF_LOC_THRESHOLD``) -> skip.
        4. Diff at or above the hard ceiling
           (``_VR_DIFF_LOC_HARD_CEILING``) -> always require.
        5. In between: trust the coder's completion_checks. If every
           recorded check is ``pass`` or ``skipped`` (and at least
           one was recorded), skip VR. Otherwise keep it as a safety
           net.

        Design:
        docs/superpowers/specs/2026-04-24-vr-gate-skip-clean-checks-design.md
        """
        if task.get("task_type") not in self._VERIFICATION_REQUIRED_TASK_TYPES:
            return False

        # (a) No-orphan VR: if the deployment has no verifier role, the
        # auto-created VR task would sit in pending forever.
        if not any(
            getattr(r, "role", None) == "verifier"
            for r in (self.all_roles or {}).values()
        ):
            logger.debug(
                "Skipping VR for %s: no verifier role configured",
                task["id"],
            )
            return False

        loc = await self._count_changed_loc(worktree_path, branch_name)
        if loc is None:
            # Unknown diff — assume substantial and require VR.
            return True
        if loc < self._VR_DIFF_LOC_THRESHOLD:
            return False
        if loc >= self._VR_DIFF_LOC_HARD_CEILING:
            return True

        # (b) Mid-range: trust completion_checks if they're clean.
        import json as _json
        raw = task.get("completion_checks") or "{}"
        try:
            checks = _json.loads(raw) if isinstance(raw, str) else (raw or {})
        except _json.JSONDecodeError:
            checks = {}
        all_clean = bool(checks) and all(
            isinstance(c, dict) and c.get("status") in {"pass", "skipped"}
            for c in checks.values()
        )
        if all_clean:
            logger.debug(
                "Skipping VR for %s: %d LOC diff with clean completion_checks",
                task["id"], loc,
            )
            return False
        return True

    async def _list_modified_files(self, task: dict) -> list[str]:
        """Return the files changed on this task's branch vs its parent_branch.

        Used by build_context on verification retries so the agent sees
        which files its previous attempt touched. Silent on error: no
        worktree, no branch, dirty state all just yield an empty list.
        Design:
        docs/superpowers/specs/2026-04-24-structured-failure-feedback-design.md
        """
        if not self.worktree_manager:
            return []
        cwd = self.worktree_manager.get_worktree_path(self.instance_id)
        if not cwd:
            return []
        parent_branch = task.get("parent_branch") or "main"
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "diff", "--name-only", f"{parent_branch}...HEAD",
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
            if proc.returncode != 0:
                return []
        except (asyncio.TimeoutError, FileNotFoundError, OSError):
            return []
        return [
            line for line in stdout.decode(errors="replace").splitlines()
            if line.strip()
        ]

    async def _count_changed_loc(
        self, worktree_path: str | None, branch_name: str | None,
    ) -> int | None:
        """Count added + removed lines on the current branch vs main.

        Returns None when the call fails (dirty worktree, detached head,
        no git, etc.) so the caller can decide the conservative default.
        """
        cwd = worktree_path or self.project_dir
        if not cwd:
            return None
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "diff", "--numstat", "main...HEAD",
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
            if proc.returncode != 0:
                return None
        except (asyncio.TimeoutError, FileNotFoundError, OSError):
            return None

        total = 0
        for line in stdout.decode(errors="replace").splitlines():
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            added, removed = parts[0], parts[1]
            # Binary files report "-" for both counts — skip them.
            if added == "-" or removed == "-":
                continue
            try:
                total += int(added) + int(removed)
            except ValueError:
                continue
        return total

    async def _has_verification_child(self, task_id: str) -> bool:
        """True when a non-cancelled verification/reviewer task already exists
        for this task. Also matches the ``reviewer``/``integrator`` split in
        case a project upgraded to the Stage-2 role layout.
        """
        row = await self.board._db.execute_fetchone(
            "SELECT id FROM tasks "
            "WHERE parent_id = ? AND status != 'cancelled' "
            "AND assigned_to IN ('verifier', 'reviewer', 'integrator') "
            "LIMIT 1",
            (task_id,),
        )
        return row is not None

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

    async def _idle_watchdog(
        self,
        *,
        task_id: str,
        idle_timeout: int,
        target: "asyncio.Task",
    ):
        """Activity-based watchdog: kill ``target`` if the agent goes
        ``idle_timeout`` seconds without any SDK activity.

        Pauses while ``tasks.awaiting_input_since`` is non-NULL so an
        agent legitimately waiting on a manual ask_question response
        doesn't get killed during overnight runs.

        Design:
        docs/superpowers/specs/2026-04-25-agent-questions-design.md
        """
        import time as _time
        # Initialised by the caller; set on the loop itself.
        check_interval = 15
        while not target.done():
            await asyncio.sleep(check_interval)
            if target.done():
                return
            try:
                row = await self.board._db.execute_fetchone(
                    "SELECT awaiting_input_since FROM tasks WHERE id = ?",
                    (task_id,),
                )
            except Exception:
                continue  # transient DB blip; try next tick
            awaiting = row.get("awaiting_input_since") if row else None
            if awaiting:
                # Agent is waiting on user input; don't penalise.
                # Slide the activity timestamp forward so that when
                # the wait clears, we don't immediately fire on a
                # stale baseline.
                self._last_activity_ts = _time.monotonic()
                continue
            elapsed = _time.monotonic() - self._last_activity_ts
            if elapsed > idle_timeout:
                logger.error(
                    "Task %s idle for %.0fs (limit %ds); cancelling",
                    task_id, elapsed, idle_timeout,
                )
                target.cancel()
                return

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

        # audit 06b F#12: run preflight before execute. On failure we
        # fail the task with a clear reason rather than let it proceed
        # under a blown budget / missing dependency / etc. Exceptions
        # from the checker itself are logged and treated as pass
        # (fail-open on checker bugs so a flaky checker does not block
        # the whole fleet).
        if self.preflight_checker is not None:
            try:
                result = await self.preflight_checker.run_checks(
                    task, self.role_config.role,
                )
            except Exception:
                task_logger.warning(
                    "preflight_checker raised for task %s; treating as pass",
                    task["id"], exc_info=True,
                )
                result = {"passed": True}
            if not result.get("passed", True):
                task_logger.warning(
                    "Task %s blocked by preflight: %s",
                    task["id"], result.get("checks", result),
                )
                await self.board.fail_task(task["id"])
                await self.event_bus.emit(
                    "task.failed",
                    {
                        "task_id": task["id"],
                        "instance_id": self.instance_id,
                        "reason": "preflight_failed",
                        "details": result,
                        "model": self.role_config.model,
                        "correlation_id": correlation_id,
                    },
                )
                await self.instance_manager.update_status(
                    self.instance_id, "idle", current_task=None,
                )
                return True

        # Create worktree ONCE, outside the retry loop. branch_name
        # and parent_branch are now authoritative fields on the task
        # row, minted by TaskBoard.create_task. We fall back to the
        # legacy computed name only for tasks created before
        # migration 30 where the column is NULL.
        worktree_path: str | None = None
        branch_name: str | None = task.get("branch_name") or (
            f"feat/{task['id'].lower()}" if self.worktree_manager else None
        )
        parent_branch: str | None = task.get("parent_branch") or "main"
        if self.worktree_manager:
            worktree_path = await self.worktree_manager.create_worktree(
                agent_name=self.instance_id,
                branch_name=branch_name,
                base_branch=parent_branch,
            )
            logger.info(
                "Agent %s using worktree %s (branch %s)",
                self.instance_id, worktree_path, branch_name,
            )

        try:
            # Activity-based idle watchdog. ``idle_timeout`` is per-role
            # (with back-compat fallback to legacy ``max_execution_time``)
            # and counts only time the agent isn't legitimately waiting
            # on user input.
            # Design:
            # docs/superpowers/specs/2026-04-25-agent-questions-design.md
            import time as _time
            idle_timeout = (
                getattr(self.role_config, "idle_timeout", None)
                or getattr(self.role_config, "max_execution_time", DEFAULT_TASK_TIMEOUT)
                or DEFAULT_TASK_TIMEOUT
            )
            self._last_activity_ts = _time.monotonic()
            hb_task = asyncio.create_task(self._heartbeat_loop())
            try:
                for attempt in range(MAX_RETRIES + 1):
                    try:
                        # Reset activity baseline at the start of each
                        # attempt so retry backoff doesn't count.
                        self._last_activity_ts = _time.monotonic()
                        exec_task = asyncio.create_task(self.execute_task(
                            task,
                            worktree_path=worktree_path,
                            branch_name=branch_name,
                        ))
                        watchdog_task = asyncio.create_task(
                            self._idle_watchdog(
                                task_id=task["id"],
                                idle_timeout=idle_timeout,
                                target=exec_task,
                            )
                        )
                        try:
                            output = await exec_task
                        finally:
                            watchdog_task.cancel()
                            try:
                                await watchdog_task
                            except (asyncio.CancelledError, Exception):
                                pass
                        break  # success
                    except asyncio.CancelledError:
                        # Watchdog killed us. Treat exactly like a timeout.
                        task_logger.error(
                            "Task %s killed by idle watchdog after %ds",
                            task["id"], idle_timeout,
                        )
                        await self.board.fail_task(task["id"])
                        await self.event_bus.emit(
                            "task.failed",
                            {
                                "task_id": task["id"],
                                "instance_id": self.instance_id,
                                "reason": "idle_timeout",
                                "model": self.role_config.model,
                                "correlation_id": correlation_id,
                            },
                        )
                        return True
                    except Exception as e:
                        # Retry classification: short-circuit on hard
                        # errors (schema/validation/tool) so they
                        # escalate in 1 × task_time instead of
                        # 3 × task_time + 65s of backoff.
                        # Design:
                        # docs/superpowers/specs/2026-04-24-retry-classification-design.md
                        retryable = _is_retryable(e)
                        if attempt < MAX_RETRIES and retryable:
                            # audit 02 F#6: backoff now carries random
                            # jitter so simultaneously-failing agents
                            # don't retry in lockstep (thundering herd).
                            # Keep the exponential base 3x but add
                            # 50-150% jitter around the nominal delay.
                            import random
                            nominal = RETRY_BASE_DELAY * (3 ** attempt)
                            delay = int(nominal * (0.5 + random.random()))
                            task_logger.warning(
                                "Task %s attempt %d failed (retryable), "
                                "retrying in %ds: %s",
                                task["id"], attempt + 1, delay, e,
                            )
                            await asyncio.sleep(delay)
                            # audit 02 F#5: reset the worktree between
                            # retries so a half-applied commit or
                            # partially-written file from the failed
                            # attempt doesn't poison the retry. Best-
                            # effort: if the reset itself fails we log
                            # and proceed (the retry can still work).
                            if worktree_path:
                                try:
                                    reset_proc = await asyncio.create_subprocess_exec(
                                        "git", "reset", "--hard",
                                        cwd=worktree_path,
                                        stdout=asyncio.subprocess.PIPE,
                                        stderr=asyncio.subprocess.PIPE,
                                    )
                                    await asyncio.wait_for(
                                        reset_proc.communicate(),
                                        timeout=15,
                                    )
                                    clean_proc = await asyncio.create_subprocess_exec(
                                        "git", "clean", "-fdx",
                                        cwd=worktree_path,
                                        stdout=asyncio.subprocess.PIPE,
                                        stderr=asyncio.subprocess.PIPE,
                                    )
                                    await asyncio.wait_for(
                                        clean_proc.communicate(),
                                        timeout=15,
                                    )
                                except (asyncio.TimeoutError, OSError, Exception) as reset_exc:
                                    task_logger.warning(
                                        "Worktree reset between retries failed for %s: %s",
                                        task["id"], reset_exc,
                                    )
                        else:
                            if not retryable:
                                # Loud signal so operators see the
                                # real error instead of inferring it
                                # from a silent 3-attempt retry burst.
                                task_logger.error(
                                    "Task %s failed with non-retryable "
                                    "error %s: %s — skipping remaining "
                                    "%d retries, failing immediately",
                                    task["id"], type(e).__name__, e,
                                    MAX_RETRIES - attempt,
                                )
                            raise  # let outer handler fail the task
            finally:
                hb_task.cancel()
                try:
                    await hb_task
                except (asyncio.CancelledError, Exception):
                    pass

            task_logger.info("Agent %s completed task %s", self.instance_id, task["id"])
            await self.complete_and_handoff(
                task, output,
                worktree_path=worktree_path,
                branch_name=branch_name,
            )
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
            # Worktree reuse: we no longer destroy the worktree per
            # task. It survives across tasks for this agent so untracked-
            # but-ignored state (node_modules, .venv) doesn't have to
            # be re-installed on every task claim. The worktree is
            # destroyed in run()'s outer finally when the agent itself
            # stops.
            # Design:
            # docs/superpowers/specs/2026-04-24-worktree-reuse-across-tasks-design.md
            await self.instance_manager.update_status(self.instance_id, "idle", current_task=None)

        return True

    async def run(self) -> None:
        """Main continuous loop.

        Event-driven claim: the loop subscribes to ``task.available``
        on the EventBus and uses an ``asyncio.Event`` to wake early
        when a task for this role lands. ``poll_interval`` stays as
        the crash-recovery backstop — if an emit is missed during
        startup or reconnect, the next poll catches it.

        Design:
        docs/superpowers/specs/2026-04-24-event-driven-task-claims-design.md
        """
        self._running = True
        self._wake_event = asyncio.Event()

        async def _wake_on_available(event):
            # Filter in the callback so agents for other roles only
            # pay a dict compare on each emit.
            if event.get("role") == self.role_config.role:
                self._wake_event.set()

        self._wake_handler = _wake_on_available
        self.event_bus.subscribe("task.available", _wake_on_available)

        try:
            await self.instance_manager.register_instance(
                self.instance_id, self.role_config
            )
            await self.event_bus.emit(
                "agent.status_changed",
                {"instance_id": self.instance_id, "status": "idle",
                 "model": self.role_config.model},
            )
            logger.info(
                "Agent %s started; subscribed to task.available, "
                "poll_interval=%ss (backstop)",
                self.instance_id, self.poll_interval,
            )

            while self._running:
                try:
                    processed = await self.run_once()
                    if not processed:
                        # Sleep until either the wake event fires or
                        # poll_interval elapses (backstop).
                        try:
                            await asyncio.wait_for(
                                self._wake_event.wait(),
                                timeout=self.poll_interval,
                            )
                        except asyncio.TimeoutError:
                            pass
                        self._wake_event.clear()
                except Exception:
                    logger.exception(
                        "Agent %s crashed in run_once, recovering",
                        self.instance_id,
                    )
                    # audit 02 F#8: reset current_task to None on crash so
                    # a stale reference doesn't persist on the instance
                    # row and confuse orphan recovery.
                    await self.instance_manager.update_status(
                        self.instance_id, "idle", current_task=None,
                    )
                    await asyncio.sleep(self.poll_interval)
                await self.instance_manager.heartbeat(self.instance_id)

            # Cleanup after loop exits
            await self.instance_manager.update_status(
                self.instance_id, "stopped",
            )
            await self.event_bus.emit(
                "agent.stopped",
                {"instance_id": self.instance_id,
                 "model": self.role_config.model},
            )
        finally:
            # Always unsubscribe so a stopped agent doesn't leave
            # a dangling callback in the event bus.
            self.event_bus.unsubscribe("task.available", self._wake_handler)
            # Destroy the agent's worktree at stop time, not per-task.
            # Per-task cleanup was removed so untracked-ignored state
            # (node_modules, .venv) survives across tasks on the same
            # agent. See _reuse_worktree in WorktreeManager.
            if self.worktree_manager:
                try:
                    await self.worktree_manager.cleanup_worktree(self.instance_id)
                except Exception:
                    logger.warning(
                        "Failed to cleanup worktree for %s at shutdown",
                        self.instance_id, exc_info=True,
                    )

    def stop(self) -> None:
        """Signal the run loop to stop after the current iteration.

        Also sets the wake event so a loop currently blocked in
        ``wait_for(wake_event.wait(), timeout=poll_interval)`` returns
        immediately instead of sitting idle for the remainder of the
        poll. Without this a stopped agent could take up to
        ``poll_interval`` seconds to actually exit.
        """
        self._running = False
        wake = getattr(self, "_wake_event", None)
        if wake is not None:
            wake.set()
