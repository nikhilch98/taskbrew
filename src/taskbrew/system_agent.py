"""Helpers for TaskBrew-owned per-project AI features."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from taskbrew.config import AgentConfig
from taskbrew.model_catalog import system_agent_setting


SYSTEM_AGENT_PROMPT = """You are the TaskBrew system administrator.

You support project-wide TaskBrew intelligence features that are not tied to a
human-created task, visible team role, or individual team member. Work only on
TaskBrew administration, coordination, planning, diagnostics, and quality
support. Do not present yourself as a team role, do not alter role prompts, and
do not override user or project configuration outside the specific feature you
are running for.

Backlog intake can only decide whether a task needs review and explain that
decision. Do not rewrite, split, reprioritize, or otherwise alter the task.

The review gate must choose exactly one of approved, needs_revision, rejected,
or failed_review. Use rejected only when the task should not continue; fixable
work must become revision tasks.
"""


def _profile_dict(profile: Any) -> dict[str, Any]:
    if not profile:
        return {}
    if isinstance(profile, dict):
        return profile
    return {
        "provider": getattr(profile, "provider", None),
        "model": getattr(profile, "model", None),
        "reasoning_effort": getattr(profile, "reasoning_effort", None),
    }


def build_system_agent_config(
    team_config: Any,
    *,
    project_dir: Path | str | None = None,
    api_url: str = "http://127.0.0.1:8420",
) -> AgentConfig:
    """Build the immutable admin agent config for project-wide AI features."""
    cli_provider = getattr(team_config, "cli_provider", "codex") or "codex"
    profile = system_agent_setting(
        cli_provider,
        _profile_dict(getattr(team_config, "system_agent", None)),
    )
    cwd = Path(project_dir) if project_dir is not None else None
    return AgentConfig(
        name="system",
        role="TaskBrew System Admin",
        system_prompt=SYSTEM_AGENT_PROMPT,
        model=profile["model"],
        reasoning_effort=profile.get("reasoning_effort"),
        cwd=cwd,
        permission_mode="default",
        api_url=api_url,
        db_path=getattr(team_config, "db_path", "data/tasks.db"),
        cli_provider=profile["provider"],
        mcp_servers=getattr(team_config, "mcp_servers", None),
    )
