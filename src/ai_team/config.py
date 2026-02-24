"""Configuration for the AI Team Orchestrator."""

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class AgentConfig:
    """Configuration for a single agent."""
    name: str
    role: str
    system_prompt: str
    allowed_tools: list[str] = field(default_factory=list)
    max_turns: int | None = None
    cwd: Path | None = None
    api_url: str = "http://127.0.0.1:8420"


@dataclass
class OrchestratorConfig:
    """Top-level orchestrator configuration."""
    project_dir: Path = field(default_factory=lambda: Path.cwd())
    db_path: Path = field(default_factory=lambda: Path("data/tasks.db"))
    artifacts_dir: Path = field(default_factory=lambda: Path("artifacts"))
    cli_path: str | None = None  # Auto-detect if None
    dashboard_host: str = "127.0.0.1"
    dashboard_port: int = 8420
    max_concurrent_agents: int = 3

    def __post_init__(self):
        self.db_path = self.project_dir / self.db_path
        self.artifacts_dir = self.project_dir / self.artifacts_dir
