"""Build the injected system prompt for agent task execution.

Implements the spec section 4.1 template. This is a pure function with no
I/O -- all data is passed in as arguments so it can be unit-tested without
a database or running orchestrator.
"""

from __future__ import annotations

from taskbrew.config_loader import PipelineConfig


def build_task_system_prompt(
    *,
    agent_role: str,
    task: dict,
    pipeline: PipelineConfig,
    context: dict[str, str | None],
) -> str:
    """Build the full system prompt injection for a task.

    Parameters
    ----------
    agent_role:
        The role ID of the agent executing the task.
    task:
        Task dict with at least: id, title, group_id, priority, task_type,
        description.  Optional: chain_id.
    pipeline:
        The current pipeline config (used to derive connected agents).
    context:
        Optional context sections.  Keys: ``parent_artifact``,
        ``root_artifact``, ``sibling_summary``, ``rejection_history``.
        Missing or None values render as "None".

    Returns
    -------
    str
        The fully rendered system prompt injection string.
    """
    parts: list[str] = []

    # -- Task Context --
    chain_id = task.get("chain_id") or "N/A"
    parts.append("== TASK CONTEXT ==")
    parts.append(f"Agent Role: {agent_role}")
    parts.append(f"Task ID: {task['id']}")
    parts.append(f"Chain ID: {chain_id}")
    parts.append(f"Title: {task['title']}")
    parts.append(f"Group: {task['group_id']}")
    parts.append(f"Priority: {task['priority']}")

    # -- Description --
    parts.append("")
    parts.append("== DESCRIPTION ==")
    parts.append(task.get("description") or "None")

    # -- Parent Artifact --
    parts.append("")
    parts.append("== PARENT ARTIFACT ==")
    parts.append(context.get("parent_artifact") or "None")

    # -- Root Artifact --
    parts.append("")
    parts.append("== ROOT ARTIFACT ==")
    parts.append(context.get("root_artifact") or "None")

    # -- Sibling Summary --
    parts.append("")
    parts.append("== SIBLING SUMMARY ==")
    parts.append(context.get("sibling_summary") or "None")

    # -- Rejection History --
    parts.append("")
    parts.append("== REJECTION HISTORY ==")
    rejection = context.get("rejection_history")
    parts.append(rejection if rejection else "None -- first attempt")

    # -- Connected Agents (outgoing pipeline edges) --
    parts.append("")
    parts.append("== CONNECTED AGENTS ==")
    outgoing = [
        edge for edge in pipeline.edges
        if edge.from_agent == agent_role
    ]
    if outgoing:
        parts.append("You can route tasks to these agents:")
        for edge in outgoing:
            types_str = ", ".join(edge.task_types) if edge.task_types else "any"
            parts.append(f"- {edge.to_agent} (accepts: {types_str})")
    else:
        parts.append("No outgoing connections. You are a terminal node.")

    parts.append("")
    parts.append(
        "Use `route_task` to send work. Use `request_clarification` for human input."
    )
    parts.append(
        "Use `complete_task` when done. Do NOT route to agents not listed above."
    )

    return "\n".join(parts)
