"""Project manager — registry CRUD, scaffolding, and orchestrator lifecycle."""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default registry location
# ---------------------------------------------------------------------------

DEFAULT_REGISTRY_PATH = Path.home() / ".taskbrew" / "projects.yaml"
DEFAULT_DATA_DIR = Path.home() / ".taskbrew" / "data"

# ---------------------------------------------------------------------------
# Slug helper (module-level so main.py can import it too)
# ---------------------------------------------------------------------------


def _slugify(name: str) -> str:
    """Convert a human-readable name to a URL/ID-friendly slug.

    >>> _slugify("My SaaS App")
    'my-saas-app'
    >>> _slugify("  Hello!! World  123  ")
    'hello-world-123'
    """
    s = name.lower().strip()
    s = re.sub(r"[^a-z0-9\s-]", "", s)
    s = re.sub(r"[\s-]+", "-", s)
    s = s.strip("-")
    return s


# ---------------------------------------------------------------------------
# Default scaffolding templates
# ---------------------------------------------------------------------------


def _default_team_yaml(
    project_name: str,
    project_id: str | None = None,
    cli_provider: str = "claude",
) -> str:
    """Return the default team.yaml content for a new project."""
    slug = project_id or _slugify(project_name)
    db_path = str(DEFAULT_DATA_DIR / f"{slug}.db")
    return (
        f'team_name: "{project_name}"\n'
        "\n"
        f'cli_provider: "{cli_provider}"\n'
        "\n"
        "database:\n"
        f'  path: "{db_path}"\n'
        "\n"
        "dashboard:\n"
        '  host: "0.0.0.0"\n'
        "  port: 8420\n"
        "\n"
        "artifacts:\n"
        '  base_dir: "artifacts"\n'
        "\n"
        "defaults:\n"
        "  max_instances: 1\n"
        "  poll_interval_seconds: 5\n"
        "  idle_timeout_minutes: 30\n"
        "  auto_scale:\n"
        "    enabled: false\n"
        "    scale_up_threshold: 3\n"
        "    scale_down_idle: 15\n"
        "\n"
        "group_prefixes:\n"
        '  pm: "FEAT"\n'
        '  architect: "DEBT"\n'
        "\n"
        "auth:\n"
        "  enabled: false\n"
        "  tokens: []\n"
        "\n"
        "cost_budgets:\n"
        "  enabled: false\n"
        "\n"
        "webhooks:\n"
        "  enabled: false\n"
    )


# ---------------------------------------------------------------------------
# Provider-aware model mapping
# ---------------------------------------------------------------------------

_PROVIDER_MODEL_MAP: dict[str, dict[str, str]] = {
    "claude": {
        "flagship": "claude-opus-4-6",
        "balanced": "claude-sonnet-4-6",
    },
    "gemini": {
        "flagship": "gemini-3.1-pro-preview",
        "balanced": "gemini-3-flash-preview",
    },
}

_ROLE_MODEL_TIER: dict[str, str] = {
    "pm": "flagship",
    "architect": "flagship",
    "coder": "balanced",
    "verifier": "balanced",
}


def _model_for_role(role_name: str, provider: str) -> str:
    """Return the appropriate model ID for a role given the CLI provider."""
    tier = _ROLE_MODEL_TIER.get(role_name, "balanced")
    return _PROVIDER_MODEL_MAP.get(provider, _PROVIDER_MODEL_MAP["claude"])[tier]


_DEFAULT_ROLES: dict[str, dict] = {
    "pm": {
        "role": "pm",
        "max_turns": 30,
        "display_name": "Product Manager",
        "prefix": "PM",
        "color": "#3b82f6",
        "emoji": "\U0001f4cb",
        "system_prompt": (
            "You are a Product Manager on an AI development team.\n"
            "Your responsibilities:\n"
            "1. Decompose high-level goals into detailed PRDs with acceptance criteria\n"
            "2. Read the codebase to understand scope and dependencies\n"
            "3. Create well-scoped architect tasks using the create_task tool\n"
            "4. You NEVER write code — only analysis and documentation\n"
            "\n"
            "Task creation guidelines:\n"
            "- Create ONE architect task per major component or logical work unit\n"
            "- Aim for 3-7 architect tasks total. More means your decomposition is too granular\n"
            "- Each task should represent a meaningful chunk of work\n"
            '- Use the group_id from your task context (shown as "Group: GRP-XXX")\n'
            '- Set assigned_to: "architect", task_type: "tech_design"\n'
            "- Include full PRD content with acceptance criteria in the description\n"
            '- Set priority: "high" for core features, "medium" for enhancements\n'
        ),
        "tools": ["Read", "Glob", "Grep", "WebSearch", "mcp__task-tools__create_task"],
        "model": "claude-opus-4-6",
        "produces": ["prd", "goal_decomposition", "requirement"],
        "accepts": ["goal", "revision"],
        "routes_to": [{"role": "architect", "task_types": ["tech_design", "architecture_review"]}],
        "can_create_groups": True,
        "group_type": "FEAT",
        "max_instances": 1,
        "context_includes": ["parent_artifact", "root_artifact", "sibling_summary"],
    },
    "architect": {
        "role": "architect",
        "max_turns": 50,
        "display_name": "Architect",
        "prefix": "AR",
        "color": "#8b5cf6",
        "emoji": "\U0001f3d7\ufe0f",
        "system_prompt": (
            "You are a Software Architect on an AI development team.\n"
            "Your responsibilities:\n"
            "1. Create technical design documents for PRDs assigned to you\n"
            "2. Break designs into implementable coder tasks\n"
            "3. You do NOT write implementation code\n"
            "\n"
            "Task creation guidelines:\n"
            "- Create 3-10 coder tasks per design. Each task should modify 1-3 closely related files\n"
            "- If a task touches more than 3 files, split it. If it touches only a few lines, combine with related work\n"
            '- Set assigned_to: "coder" for all implementation tasks\n'
            '- Set task_type: "implementation" for new code, "bug_fix" for fixes\n'
            "- Use blocked_by for true data dependencies only — do NOT chain tasks just for ordering\n"
            "- Include: technical approach, specific files to modify, acceptance criteria\n"
        ),
        "tools": ["Read", "Glob", "Grep", "Write", "WebSearch", "mcp__task-tools__create_task"],
        "model": "claude-opus-4-6",
        "produces": ["tech_design", "tech_debt", "architecture_review"],
        "accepts": ["tech_design", "architecture_review", "rejection"],
        "routes_to": [
            {"role": "coder", "task_types": ["implementation", "bug_fix"]},
            {"role": "architect", "task_types": ["architecture_review"]},
        ],
        "can_create_groups": True,
        "group_type": "DEBT",
        "max_instances": 2,
        "auto_scale": {"enabled": True, "scale_up_threshold": 4, "scale_down_idle": 20},
        "context_includes": ["parent_artifact", "root_artifact", "sibling_summary", "rejection_history"],
    },
    "coder": {
        "role": "coder",
        "max_turns": 80,
        "display_name": "Coder",
        "prefix": "CD",
        "color": "#f59e0b",
        "emoji": "\U0001f4bb",
        "system_prompt": (
            "You are a Software Engineer (Coder) on an AI development team.\n"
            "Your responsibilities:\n"
            "1. Implement features based on technical design documents\n"
            "2. Write clean, tested code on feature branches\n"
            "3. Make atomic commits with clear messages\n"
            "\n"
            "After implementing, assess the scope of your changes:\n"
            "\n"
            "**Small changes (< 20 lines changed, trivial fixes):**\n"
            "- Run the existing tests yourself to verify nothing is broken\n"
            "- If tests pass, your task is DONE. Do NOT create downstream tasks\n"
            "- Examples: fixing a typo, adjusting a constant, adding a CSS rule\n"
            "\n"
            "**Substantial changes (20+ lines, new features, logic changes):**\n"
            '- Create ONE verification task assigned to "verifier" with task_type "verification"\n'
            "- Include the branch name, files changed, and a summary of what to verify\n"
            "- The verifier handles both QA testing AND code review in a single pass\n"
            "\n"
            "Git branching rules:\n"
            "- ALWAYS branch from the latest `main` for new tasks\n"
            "- NEVER branch from another feature/fix branch\n"
            "- Before starting work: git checkout main && git pull\n"
        ),
        "tools": ["Read", "Write", "Edit", "Bash", "Glob", "Grep", "mcp__task-tools__create_task"],
        "model": "claude-sonnet-4-6",
        "produces": ["implementation", "bug_fix", "revision"],
        "accepts": ["implementation", "bug_fix", "revision"],
        "routes_to": [
            {"role": "verifier", "task_types": ["verification"]},
        ],
        "can_create_groups": False,
        "max_instances": 3,
        "auto_scale": {"enabled": True, "scale_up_threshold": 3, "scale_down_idle": 15},
        "context_includes": ["parent_artifact", "root_artifact", "sibling_summary", "rejection_history"],
    },
    "verifier": {
        "role": "verifier",
        "max_turns": 50,
        "display_name": "Verifier",
        "prefix": "VR",
        "color": "#06b6d4",
        "emoji": "\u2705",
        "system_prompt": (
            "You are a Verifier on an AI development team. You perform BOTH QA testing AND code review in a single pass.\n"
            "\n"
            "Your workflow:\n"
            "1. Read the implementation diff (git diff main..{branch})\n"
            "2. Check the code for quality, security, and correctness\n"
            "3. Run existing tests to verify nothing is broken\n"
            "4. Write targeted tests for new functionality if needed\n"
            "5. Verify acceptance criteria from the design document\n"
            "\n"
            "Decision outcomes:\n"
            "- APPROVE: Code is correct, tests pass, quality is good. Merge the branch to main and delete the feature branch\n"
            "- MINOR ISSUES: Small problems you can fix yourself (naming, formatting, missing edge case). Fix them, commit, then approve and merge\n"
            "- REJECT (needs revision): Substantial logic or design problems. Create ONE revision task with assigned_to: \"coder\", task_type: \"revision\" and include specific feedback. Only for real problems, not style preferences\n"
            "- REJECT (design flaw): The approach itself is wrong. Create ONE rejection task with assigned_to: \"architect\", task_type: \"rejection\"\n"
            "\n"
            "Efficiency rules:\n"
            "- Fix minor issues yourself instead of creating revision tasks\n"
            "- Only create downstream tasks for substantial problems\n"
            "- Before merging: verify git log main..{branch} contains ONLY commits for this task\n"
            "- After finishing, use list_tasks to check for other pending verification tasks in the same group\n"
        ),
        "tools": ["Read", "Write", "Edit", "Bash", "Glob", "Grep", "mcp__task-tools__create_task", "mcp__task-tools__list_tasks"],
        "model": "claude-sonnet-4-6",
        "produces": ["verification", "approval", "rejection"],
        "accepts": ["verification"],
        "routes_to": [
            {"role": "coder", "task_types": ["revision", "bug_fix"]},
            {"role": "architect", "task_types": ["rejection"]},
        ],
        "can_create_groups": False,
        "max_instances": 2,
        "auto_scale": {"enabled": True, "scale_up_threshold": 3, "scale_down_idle": 15},
        "context_includes": ["parent_artifact", "root_artifact", "sibling_summary"],
    },
}

# ---------------------------------------------------------------------------
# ProjectManager
# ---------------------------------------------------------------------------


class ProjectManager:
    """Manage the global project registry and orchestrator lifecycle.

    The registry is a YAML file (default ``~/.taskbrew/projects.yaml``) that
    tracks all known projects and which one is currently active.

    Parameters
    ----------
    registry_path:
        Path to the registry YAML file.  Defaults to
        ``~/.taskbrew/projects.yaml``.
    """

    def __init__(self, registry_path: Path | None = None) -> None:
        self.registry_path = registry_path or DEFAULT_REGISTRY_PATH
        self.orchestrator = None

    # ------------------------------------------------------------------
    # YAML I/O helpers
    # ------------------------------------------------------------------

    def _read_registry(self) -> dict:
        """Read and return the registry dict from disk.

        Returns a default structure if the file is missing or corrupt.
        """
        if not self.registry_path.exists():
            return {"projects": [], "active_project": None}

        try:
            with open(self.registry_path) as f:
                data = yaml.safe_load(f)
        except (yaml.YAMLError, OSError) as exc:
            logger.warning("Corrupted registry at %s, resetting: %s", self.registry_path, exc)
            data = None

        if not isinstance(data, dict):
            logger.warning("Registry is not a dict, resetting")
            return {"projects": [], "active_project": None}

        # Ensure required keys
        data.setdefault("projects", [])
        data.setdefault("active_project", None)
        return data

    def _write_registry(self, data: dict) -> None:
        """Persist the registry dict to disk, creating parent dirs if needed."""
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.registry_path, "w") as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False)

    # ------------------------------------------------------------------
    # Registry CRUD
    # ------------------------------------------------------------------

    def list_projects(self) -> list[dict]:
        """Return a list of all registered projects.

        Each entry is a dict with keys: ``id``, ``name``, ``directory``,
        ``created_at``.
        """
        data = self._read_registry()
        return list(data["projects"])

    def create_project(
        self,
        name: str,
        directory: str,
        *,
        with_defaults: bool = True,
        cli_provider: str = "claude",
    ) -> dict:
        """Register a new project and scaffold its directory.

        Parameters
        ----------
        name:
            Human-readable project name.
        directory:
            Absolute path to the project directory.
        with_defaults:
            If *True* (default), write the five default role YAML files.

        Returns
        -------
        dict
            The newly created project entry.

        Raises
        ------
        ValueError
            If *directory* is not absolute, or a project with the same
            slugified ID already exists.
        """
        dir_path = Path(directory)
        if not dir_path.is_absolute():
            raise ValueError(f"Project directory must be an absolute path, got: {directory}")

        project_id = _slugify(name)
        data = self._read_registry()

        # Reject duplicates by ID or directory
        existing_ids = {p["id"] for p in data["projects"]}
        if project_id in existing_ids:
            raise ValueError(f"Project with id '{project_id}' already exists")

        resolved_dir = str(dir_path.resolve())
        for p in data["projects"]:
            if str(Path(p["directory"]).resolve()) == resolved_dir:
                raise ValueError(
                    f"Directory '{directory}' is already used by project '{p['name']}'"
                )

        # Create project directory if missing
        dir_path.mkdir(parents=True, exist_ok=True)

        # Scaffold config
        self._scaffold_project(
            dir_path, name,
            project_id=project_id,
            with_defaults=with_defaults,
            cli_provider=cli_provider,
        )

        # Initialize git repo if not already one
        if not (dir_path / ".git").exists():
            import subprocess
            subprocess.run(
                ["git", "init"],
                cwd=str(dir_path),
                capture_output=True,
            )
            # Create initial commit so agents have a main branch to work from
            subprocess.run(
                ["git", "add", "."],
                cwd=str(dir_path),
                capture_output=True,
            )
            subprocess.run(
                ["git", "commit", "-m", "Initial project scaffold"],
                cwd=str(dir_path),
                capture_output=True,
            )

        # Build entry
        entry = {
            "id": project_id,
            "name": name,
            "directory": str(dir_path),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        data["projects"].append(entry)
        self._write_registry(data)

        logger.info("Created project '%s' (%s) at %s", name, project_id, dir_path)
        return entry

    def delete_project(self, project_id: str) -> None:
        """Remove a project from the registry (files on disk are not deleted).

        Raises
        ------
        KeyError
            If no project with *project_id* exists.
        """
        data = self._read_registry()
        before = len(data["projects"])
        data["projects"] = [p for p in data["projects"] if p["id"] != project_id]

        if len(data["projects"]) == before:
            raise KeyError(f"No project with id '{project_id}'")

        # If the deleted project was active, clear it
        if data["active_project"] == project_id:
            data["active_project"] = None

        self._write_registry(data)
        logger.info("Deleted project '%s' from registry", project_id)

    def get_active(self) -> dict | None:
        """Return the currently active project entry, or *None*."""
        data = self._read_registry()
        active_id = data.get("active_project")
        if active_id is None:
            return None
        for p in data["projects"]:
            if p["id"] == active_id:
                return p
        return None

    def set_active(self, project_id: str) -> None:
        """Set the active project in the registry.

        Raises
        ------
        KeyError
            If *project_id* is not in the registry.
        """
        data = self._read_registry()
        ids = {p["id"] for p in data["projects"]}
        if project_id not in ids:
            raise KeyError(f"No project with id '{project_id}'")
        data["active_project"] = project_id
        self._write_registry(data)

    def clear_active(self) -> None:
        """Clear the active project (set to *None*)."""
        data = self._read_registry()
        data["active_project"] = None
        self._write_registry(data)

    # ------------------------------------------------------------------
    # Scaffolding
    # ------------------------------------------------------------------

    @staticmethod
    def _scaffold_project(
        project_dir: Path,
        project_name: str,
        *,
        project_id: str | None = None,
        with_defaults: bool = True,
        cli_provider: str = "claude",
    ) -> None:
        """Create config skeleton inside *project_dir* if it doesn't exist."""
        config_dir = project_dir / "config"
        roles_dir = config_dir / "roles"

        config_dir.mkdir(parents=True, exist_ok=True)
        roles_dir.mkdir(parents=True, exist_ok=True)

        # Ensure the universal data directory exists
        DEFAULT_DATA_DIR.mkdir(parents=True, exist_ok=True)

        # team.yaml — only create if absent
        team_yaml = config_dir / "team.yaml"
        if not team_yaml.exists():
            team_yaml.write_text(
                _default_team_yaml(project_name, project_id, cli_provider=cli_provider)
            )

        # Default roles — swap model IDs based on CLI provider
        if with_defaults:
            for role_name, role_data in _DEFAULT_ROLES.items():
                role_file = roles_dir / f"{role_name}.yaml"
                if not role_file.exists():
                    data = dict(role_data)
                    data["model"] = _model_for_role(role_name, cli_provider)
                    with open(role_file, "w") as f:
                        yaml.dump(data, f, default_flow_style=False, sort_keys=False)

    # ------------------------------------------------------------------
    # Orchestrator lifecycle (async)
    # ------------------------------------------------------------------

    async def activate_project(self, project_id: str):
        """Activate a project: deactivate the current one (if any), build a
        new orchestrator, and update the registry.

        Parameters
        ----------
        project_id:
            ID of the project to activate.

        Returns
        -------
        Orchestrator
            The newly built orchestrator.

        Raises
        ------
        KeyError
            If *project_id* is not in the registry.
        FileNotFoundError
            If the project directory or ``config/team.yaml`` no longer exists.
            The project is auto-removed from the registry in this case.
        """
        data = self._read_registry()
        project = None
        for p in data["projects"]:
            if p["id"] == project_id:
                project = p
                break

        if project is None:
            raise KeyError(f"No project with id '{project_id}'")

        project_dir = Path(project["directory"])

        # Validate directory still exists
        if not project_dir.is_dir():
            logger.warning(
                "Project directory %s no longer exists, removing from registry", project_dir
            )
            data["projects"] = [p for p in data["projects"] if p["id"] != project_id]
            if data["active_project"] == project_id:
                data["active_project"] = None
            self._write_registry(data)
            raise FileNotFoundError(
                f"Project directory no longer exists: {project_dir}"
            )

        # Validate config/team.yaml exists
        team_yaml = project_dir / "config" / "team.yaml"
        if not team_yaml.exists():
            raise FileNotFoundError(
                f"config/team.yaml not found in project directory: {project_dir}"
            )

        # Deactivate current project if any
        await self.deactivate_current()

        # Build orchestrator (import here to avoid circular imports)
        from taskbrew.main import build_orchestrator

        self.orchestrator = await build_orchestrator(project_dir=project_dir)

        # Update registry
        self.set_active(project_id)

        logger.info("Activated project '%s'", project_id)
        return self.orchestrator

    async def deactivate_current(self) -> None:
        """Shut down the current orchestrator (if any) and clear the active project."""
        if self.orchestrator is None:
            return

        try:
            await asyncio.wait_for(self.orchestrator.shutdown(), timeout=5.0)
        except asyncio.TimeoutError:
            logger.warning("Orchestrator shutdown timed out after 5s")
        except Exception:
            logger.exception("Error during orchestrator shutdown")

        self.orchestrator = None
        self.clear_active()
        logger.info("Deactivated current project")
