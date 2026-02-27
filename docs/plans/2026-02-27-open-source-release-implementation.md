# Open-Source Release Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Prepare the ai-team project for public open-source release with full extensibility for providers, MCP tools, roles, plugins, and hybrid agent routing.

**Architecture:** Config-driven extensibility across 4 extension points (providers, MCP tools, roles, plugins) with hybrid agent routing (open discovery + optional restriction). Clean slate cleanup, comprehensive docs, and fail-fast error handling.

**Tech Stack:** Python 3.10+, FastAPI, SQLAlchemy/aiosqlite, PyYAML, claude-agent-sdk, asyncio

---

## Phase 1: Foundation (Critical for Release)

### Task 1: Create LICENSE and .env.example

**Files:**
- Create: `LICENSE`
- Create: `.env.example`

**Step 1: Create MIT LICENSE file**

```
MIT License

Copyright (c) 2026 Nikhil Chatragadda

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

**Step 2: Create .env.example**

```env
# Required — at least one provider API key
ANTHROPIC_API_KEY=your-anthropic-api-key-here

# Optional — Gemini provider
GOOGLE_API_KEY=your-google-api-key-here

# Optional — Server
AI_TEAM_API_URL=http://127.0.0.1:8420
AI_TEAM_DB_PATH=~/.ai-team/data/ai-team.db

# Optional — Logging
LOG_LEVEL=INFO
LOG_FORMAT=dev

# Optional — Auth
AUTH_ENABLED=false
CORS_ORIGINS=http://localhost:8000,http://localhost:3000
```

**Step 3: Commit**

```bash
git add LICENSE .env.example
git commit -m "feat: add MIT LICENSE and .env.example"
```

---

### Task 2: Fix hardcoded DB path and update .gitignore

**Files:**
- Modify: `config/team.yaml:4`
- Modify: `src/taskbrew/config_loader.py:77`
- Modify: `.gitignore`

**Step 1: Write failing test for path expansion**

```python
# tests/test_config_loader.py — add to existing file

def test_load_team_config_expands_tilde(tmp_path):
    """DB path with ~ should be expanded to full home directory."""
    team_yaml = tmp_path / "team.yaml"
    team_yaml.write_text(
        'team_name: test\n'
        'database:\n  path: "~/.ai-team/data/test.db"\n'
        'dashboard:\n  host: "0.0.0.0"\n  port: 8420\n'
        'artifacts:\n  base_dir: "artifacts"\n'
    )
    cfg = load_team_config(team_yaml)
    assert "~" not in cfg.db_path
    assert cfg.db_path.startswith("/")
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_config_loader.py::test_load_team_config_expands_tilde -v`
Expected: FAIL — `~` is still in the path (no expansion)

**Step 3: Fix config_loader.py to expand tilde**

In `src/taskbrew/config_loader.py:77`, change:

```python
# Before (line 77):
db_path=data["database"]["path"],

# After:
db_path=str(Path(data["database"]["path"]).expanduser()),
```

**Step 4: Fix config/team.yaml hardcoded path**

Replace line 4:
```yaml
# Before:
  path: "/Users/nikhilchatragadda/.ai-team/data/ai-team.db"
# After:
  path: "~/.ai-team/data/ai-team.db"
```

**Step 5: Run test to verify it passes**

Run: `pytest tests/test_config_loader.py::test_load_team_config_expands_tilde -v`
Expected: PASS

**Step 6: Update .gitignore**

Replace full contents of `.gitignore` with:

```gitignore
# Python
__pycache__/
*.egg-info/
.eggs/
dist/
build/
*.pyc
*.pyo

# Virtual environments
.venv/
env/

# Data (user-specific)
data/
*.db
artifacts/
.worktrees/

# IDE
.vscode/
.idea/
*.swp
*.swo

# OS
.DS_Store
Thumbs.db

# Environment
.env

# Node
node_modules/

# Coverage
.coverage
htmlcov/

# Logs
*.log
```

**Step 7: Commit**

```bash
git add config/team.yaml src/taskbrew/config_loader.py .gitignore tests/test_config_loader.py
git commit -m "fix: replace hardcoded DB path with ~/.ai-team, update .gitignore"
```

---

### Task 3: Update pyproject.toml metadata

**Files:**
- Modify: `pyproject.toml`

**Step 1: Replace pyproject.toml contents**

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "ai-team"
version = "1.0.0"
description = "Multi-agent AI team orchestrator — coordinate Claude Code, Gemini CLI, and custom AI agents into collaborative development workflows"
license = {text = "MIT"}
readme = "README.md"
requires-python = ">=3.10"
authors = [
    {name = "Nikhil Chatragadda"},
]
keywords = ["ai", "agents", "orchestrator", "multi-agent", "claude", "gemini", "mcp"]
classifiers = [
    "Development Status :: 4 - Beta",
    "Intended Audience :: Developers",
    "License :: OSI Approved :: MIT License",
    "Programming Language :: Python :: 3",
    "Programming Language :: Python :: 3.10",
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.12",
    "Topic :: Software Development :: Libraries",
]
dependencies = [
    "claude-agent-sdk>=0.1.0",
    "fastapi>=0.115.0",
    "uvicorn[standard]>=0.34.0",
    "sqlalchemy>=2.0",
    "aiosqlite>=0.20.0",
    "jinja2>=3.1",
    "pyyaml>=6.0",
    "websockets>=14.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.24.0",
    "pytest-cov>=6.0",
    "ruff>=0.8.0",
    "httpx>=0.28.0",
]

[project.scripts]
ai-team = "taskbrew.main:cli_main"

[project.urls]
Homepage = "https://github.com/nikhilchatragadda/ai-team"
Documentation = "https://github.com/nikhilchatragadda/ai-team/tree/main/docs"
Repository = "https://github.com/nikhilchatragadda/ai-team"
Issues = "https://github.com/nikhilchatragadda/ai-team/issues"

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[tool.ruff]
target-version = "py310"
line-length = 100
```

**Step 2: Verify install still works**

Run: `pip install -e ".[dev]"`
Expected: Installs successfully

**Step 3: Commit**

```bash
git add pyproject.toml
git commit -m "feat: update pyproject.toml with v1.0.0 metadata, license, URLs"
```

---

### Task 4: Remove experimental files (clean slate)

**Files:**
- Delete: `flappy-bird/` directory
- Delete: `analysis/` directory
- Delete: Root-level review markdown files
- Delete: `node_modules/`, `package.json`, `package-lock.json`
- Delete: `serve_output.log`
- Delete: Internal docs (audits, review reports)

**Step 1: Remove files**

```bash
# Experimental
rm -rf flappy-bird/ analysis/

# Root-level internal docs
rm -f ANALYSIS-COMPLETE.md CD-197-BRANCH-CLEANUP.md IMPLEMENTATION-GUIDE-BRANCH-ISOLATION.md
rm -f RV-170-CODE-REVIEW.md RV-220-ANALYSIS-README.md RV-220-ROOT-CAUSE-SUMMARY.md
rm -f TECHNICAL_ANALYSIS_RV-220.md serve_output.log

# Node artifacts
rm -rf node_modules/
rm -f package.json package-lock.json

# Internal review docs
rm -rf docs/audits/
rm -f docs/AR-057-*.md docs/CD-141-*.md docs/CD-166-*.md
rm -f docs/RV-190-*.md docs/RV-227-*.md
rm -f docs/ARCHITECTURE-INVESTIGATION-AR-053.md docs/AR-053-INVESTIGATION-SUMMARY.md
rm -f docs/ADR-001-PIPELINE-INVENTORY.md docs/ADR-002-RELEASE-PIPELINE.md
```

**Step 2: Verify tests still pass**

Run: `pytest tests/ -x -q --no-header`
Expected: All tests pass (no dependency on deleted files)

**Step 3: Commit**

```bash
git add -A
git commit -m "chore: remove experimental files and internal docs for clean open-source release"
```

---

## Phase 2: Config-Driven MCP Tool Servers

### Task 5: Add MCPServerConfig dataclass and parse from team.yaml

**Files:**
- Modify: `src/taskbrew/config_loader.py`
- Modify: `src/taskbrew/config.py`
- Test: `tests/test_config_loader.py`

**Step 1: Write failing test**

```python
# tests/test_config_loader.py — add

def test_load_team_config_parses_mcp_servers(tmp_path):
    """MCP servers defined in team.yaml should be parsed into MCPServerConfig."""
    team_yaml = tmp_path / "team.yaml"
    team_yaml.write_text(
        'team_name: test\n'
        'database:\n  path: "~/.ai-team/data/test.db"\n'
        'dashboard:\n  host: "0.0.0.0"\n  port: 8420\n'
        'artifacts:\n  base_dir: "artifacts"\n'
        'mcp_servers:\n'
        '  task-tools:\n'
        '    builtin: true\n'
        '  my-custom-tool:\n'
        '    command: "python"\n'
        '    args: ["-m", "my_tool"]\n'
        '    env:\n'
        '      MY_VAR: "hello"\n'
        '    transport: stdio\n'
    )
    cfg = load_team_config(team_yaml)
    assert len(cfg.mcp_servers) == 2
    assert cfg.mcp_servers["task-tools"].builtin is True
    assert cfg.mcp_servers["my-custom-tool"].command == "python"
    assert cfg.mcp_servers["my-custom-tool"].args == ["-m", "my_tool"]
    assert cfg.mcp_servers["my-custom-tool"].env == {"MY_VAR": "hello"}
    assert cfg.mcp_servers["my-custom-tool"].transport == "stdio"


def test_load_team_config_default_mcp_servers(tmp_path):
    """If no mcp_servers in YAML, defaults should include built-in servers."""
    team_yaml = tmp_path / "team.yaml"
    team_yaml.write_text(
        'team_name: test\n'
        'database:\n  path: "~/.ai-team/data/test.db"\n'
        'dashboard:\n  host: "0.0.0.0"\n  port: 8420\n'
        'artifacts:\n  base_dir: "artifacts"\n'
    )
    cfg = load_team_config(team_yaml)
    assert "task-tools" in cfg.mcp_servers
    assert "intelligence-tools" in cfg.mcp_servers
    assert cfg.mcp_servers["task-tools"].builtin is True
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_config_loader.py::test_load_team_config_parses_mcp_servers tests/test_config_loader.py::test_load_team_config_default_mcp_servers -v`
Expected: FAIL — `MCPServerConfig` doesn't exist, `mcp_servers` not on `TeamConfig`

**Step 3: Add MCPServerConfig dataclass to config_loader.py**

Add after `AutoScaleDefaults` (after line 23):

```python
@dataclass
class MCPServerConfig:
    """Configuration for a single MCP tool server."""

    builtin: bool = False
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    transport: str = "stdio"
```

**Step 4: Add mcp_servers field to TeamConfig**

Add to `TeamConfig` dataclass (after line 43):

```python
    mcp_servers: dict[str, MCPServerConfig] = field(default_factory=dict)
```

**Step 5: Parse mcp_servers in load_team_config()**

Add parsing logic to `load_team_config()` before the `return TeamConfig(...)` at line 75. Insert after line 73:

```python
    # Parse MCP servers
    mcp_raw = data.get("mcp_servers", {})
    mcp_servers: dict[str, MCPServerConfig] = {}
    for name, cfg in mcp_raw.items():
        if isinstance(cfg, dict):
            mcp_servers[name] = MCPServerConfig(
                builtin=cfg.get("builtin", False),
                command=cfg.get("command", ""),
                args=cfg.get("args", []),
                env=cfg.get("env", {}),
                transport=cfg.get("transport", "stdio"),
            )

    # Ensure built-in servers always exist
    if "task-tools" not in mcp_servers:
        mcp_servers["task-tools"] = MCPServerConfig(builtin=True)
    if "intelligence-tools" not in mcp_servers:
        mcp_servers["intelligence-tools"] = MCPServerConfig(builtin=True)
```

Add `mcp_servers=mcp_servers,` to the `return TeamConfig(...)` call.

**Step 6: Run tests to verify they pass**

Run: `pytest tests/test_config_loader.py::test_load_team_config_parses_mcp_servers tests/test_config_loader.py::test_load_team_config_default_mcp_servers -v`
Expected: PASS

**Step 7: Commit**

```bash
git add src/taskbrew/config_loader.py tests/test_config_loader.py
git commit -m "feat: add MCPServerConfig dataclass and parse mcp_servers from team.yaml"
```

---

### Task 6: Refactor provider.py to use config-driven MCP servers

**Files:**
- Modify: `src/taskbrew/agents/provider.py:41-106`
- Modify: `src/taskbrew/agents/base.py` (pass mcp config through)
- Test: `tests/test_provider_mcp.py` (new)

**Step 1: Write failing test**

```python
# tests/test_provider_mcp.py (new file)
"""Tests for config-driven MCP server registration."""

from __future__ import annotations

from taskbrew.config_loader import MCPServerConfig


def test_build_mcp_dict_builtin():
    """Built-in MCP servers should use sys.executable with module args."""
    from taskbrew.agents.provider import _build_mcp_dict

    servers = {
        "task-tools": MCPServerConfig(builtin=True),
    }
    result = _build_mcp_dict(servers, api_url="http://localhost:8420", db_path="data/test.db")
    assert "task-tools" in result
    assert result["task-tools"]["type"] == "stdio"
    assert "-m" in result["task-tools"]["args"]
    assert "taskbrew.tools.task_tools" in result["task-tools"]["args"]


def test_build_mcp_dict_custom():
    """Custom MCP servers should use command/args/env from config."""
    from taskbrew.agents.provider import _build_mcp_dict

    servers = {
        "my-tool": MCPServerConfig(
            command="python",
            args=["-m", "my_tool"],
            env={"MY_VAR": "hello"},
            transport="stdio",
        ),
    }
    result = _build_mcp_dict(servers, api_url="http://localhost:8420", db_path="data/test.db")
    assert "my-tool" in result
    assert result["my-tool"]["command"] == "python"
    assert result["my-tool"]["args"] == ["-m", "my_tool"]
    assert result["my-tool"]["env"]["MY_VAR"] == "hello"


def test_build_mcp_dict_env_interpolation():
    """${VAR} syntax in env values should be interpolated from os.environ."""
    import os
    from taskbrew.agents.provider import _build_mcp_dict

    os.environ["TEST_TOKEN_XYZ"] = "secret123"
    servers = {
        "github": MCPServerConfig(
            command="npx",
            args=["-y", "@anthropic/mcp-github"],
            env={"GITHUB_TOKEN": "${TEST_TOKEN_XYZ}", "STATIC": "value"},
        ),
    }
    result = _build_mcp_dict(servers, api_url="http://localhost:8420", db_path="data/test.db")
    assert result["github"]["env"]["GITHUB_TOKEN"] == "secret123"
    assert result["github"]["env"]["STATIC"] == "value"
    del os.environ["TEST_TOKEN_XYZ"]
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_provider_mcp.py -v`
Expected: FAIL — `_build_mcp_dict` doesn't exist

**Step 3: Add _build_mcp_dict to provider.py**

Add before `build_sdk_options()` (before line 41):

```python
import os
import re

from taskbrew.config_loader import MCPServerConfig


def _interpolate_env(env: dict[str, str]) -> dict[str, str]:
    """Replace ${VAR} placeholders with values from os.environ."""
    result = {}
    for key, value in env.items():
        if isinstance(value, str) and "${" in value:
            result[key] = re.sub(
                r'\$\{(\w+)\}',
                lambda m: os.environ.get(m.group(1), m.group(0)),
                value,
            )
        else:
            result[key] = value
    return result


_BUILTIN_MCP_SERVERS = {
    "task-tools": {
        "module": "taskbrew.tools.task_tools",
        "env_key": "AI_TEAM_API_URL",
        "env_source": "api_url",
    },
    "intelligence-tools": {
        "module": "taskbrew.tools.intelligence_tools",
        "env_key": "AI_TEAM_DB_PATH",
        "env_source": "db_path",
    },
}


def _build_mcp_dict(
    servers: dict[str, MCPServerConfig],
    api_url: str = "http://127.0.0.1:8420",
    db_path: str = "data/tasks.db",
) -> dict[str, dict]:
    """Convert MCPServerConfig objects into SDK-compatible dicts."""
    env_sources = {"api_url": api_url, "db_path": db_path}
    result = {}
    for name, cfg in servers.items():
        if cfg.builtin and name in _BUILTIN_MCP_SERVERS:
            builtin = _BUILTIN_MCP_SERVERS[name]
            result[name] = {
                "type": "stdio",
                "command": sys.executable,
                "args": ["-m", builtin["module"]],
                "env": {builtin["env_key"]: env_sources[builtin["env_source"]]},
            }
        elif not cfg.builtin:
            result[name] = {
                "type": cfg.transport,
                "command": cfg.command,
                "args": cfg.args,
                "env": _interpolate_env(cfg.env),
            }
    return result
```

**Step 4: Update build_sdk_options() to accept mcp_servers param**

Modify `build_sdk_options()` signature — add `mcp_servers` parameter:

```python
def build_sdk_options(
    *,
    provider: str,
    system_prompt: str,
    model: str | None = None,
    max_turns: int | None = None,
    cwd: str | None = None,
    allowed_tools: list[str] | None = None,
    permission_mode: str = "default",
    api_url: str = "http://127.0.0.1:8420",
    db_path: str = "data/tasks.db",
    cli_path: str | None = None,
    mcp_servers: dict[str, MCPServerConfig] | None = None,
) -> Any:
```

Replace the hardcoded `mcp_servers={}` block (lines 83-96) with:

```python
        mcp_servers=_build_mcp_dict(
            mcp_servers or {},
            api_url=api_url,
            db_path=db_path,
        ),
```

**Step 5: Run tests**

Run: `pytest tests/test_provider_mcp.py -v`
Expected: PASS

**Step 6: Run full test suite to check nothing broke**

Run: `pytest tests/ -x -q --no-header`
Expected: All tests pass

**Step 7: Commit**

```bash
git add src/taskbrew/agents/provider.py tests/test_provider_mcp.py
git commit -m "feat: config-driven MCP server registration with env interpolation"
```

---

### Task 7: Thread MCP config from team_config through agent creation

**Files:**
- Modify: `src/taskbrew/agents/base.py` (AgentRunner.build_options)
- Modify: `src/taskbrew/agents/agent_loop.py` (pass mcp_servers)
- Modify: `src/taskbrew/main.py` (pass team_config.mcp_servers to agent loops)

**Step 1: Add mcp_servers to AgentConfig**

In `src/taskbrew/config.py`, add to `AgentConfig` dataclass after line 20:

```python
    mcp_servers: dict | None = None
```

**Step 2: Pass mcp_servers through AgentRunner.build_options()**

In `src/taskbrew/agents/base.py`, modify `build_options()` (around line 64) to include:

```python
        return build_sdk_options(
            provider=self.provider,
            system_prompt=self.config.system_prompt,
            model=self.config.model,
            max_turns=self.config.max_turns,
            cwd=effective_cwd,
            allowed_tools=self.config.allowed_tools,
            permission_mode=self.config.permission_mode,
            api_url=self.config.api_url,
            db_path=self.config.db_path,
            cli_path=self.cli_path,
            mcp_servers=self.config.mcp_servers,
        )
```

**Step 3: Pass mcp_servers when creating AgentConfig in agent_loop.py**

In `src/taskbrew/agents/agent_loop.py`, in the `execute_task()` method where `AgentConfig` is created, add `mcp_servers=self.mcp_servers` to the constructor. This requires adding `mcp_servers` as a parameter to the `AgentLoop.__init__()`.

**Step 4: Wire in main.py**

Where agent loops are created in `main.py`, pass `mcp_servers=team_config.mcp_servers` to each `AgentLoop`.

**Step 5: Run full test suite**

Run: `pytest tests/ -x -q --no-header`
Expected: All pass

**Step 6: Commit**

```bash
git add src/taskbrew/config.py src/taskbrew/agents/base.py src/taskbrew/agents/agent_loop.py src/taskbrew/main.py
git commit -m "feat: thread MCP server config from team.yaml through agent creation pipeline"
```

---

## Phase 3: Provider Registry

### Task 8: Create provider_base.py abstract interface

**Files:**
- Create: `src/taskbrew/agents/provider_base.py`
- Test: `tests/test_provider_base.py`

**Step 1: Write failing test**

```python
# tests/test_provider_base.py
"""Tests for the abstract provider interface."""

import pytest
from taskbrew.agents.provider_base import ProviderPlugin


def test_provider_plugin_is_abstract():
    """Cannot instantiate ProviderPlugin directly."""
    with pytest.raises(TypeError):
        ProviderPlugin()


def test_concrete_provider_must_implement_query():
    """A concrete provider must implement query()."""
    class IncompleteProvider(ProviderPlugin):
        name = "incomplete"
        detect_patterns = ["inc-*"]

        def build_options(self, **kwargs):
            return {}

    with pytest.raises(TypeError):
        IncompleteProvider()


def test_concrete_provider_works():
    """A fully implemented provider can be instantiated."""
    class MockProvider(ProviderPlugin):
        name = "mock"
        detect_patterns = ["mock-*"]

        def build_options(self, **kwargs):
            return {}

        async def query(self, prompt, options):
            yield None

        def get_message_types(self):
            return {}

    p = MockProvider()
    assert p.name == "mock"
    assert p.detect_patterns == ["mock-*"]
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_provider_base.py -v`
Expected: FAIL — module not found

**Step 3: Create provider_base.py**

```python
# src/taskbrew/agents/provider_base.py
"""Abstract base for CLI provider plugins."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, AsyncIterator


@dataclass
class TextBlock:
    """A text content block in an assistant message."""
    text: str
    type: str = "text"


@dataclass
class ToolUseBlock:
    """A tool-use content block in an assistant message."""
    id: str
    name: str
    input: dict[str, Any] = field(default_factory=dict)
    type: str = "tool_use"


@dataclass
class AssistantMessage:
    """An assistant response message."""
    content: list[TextBlock | ToolUseBlock] = field(default_factory=list)
    session_id: str | None = None
    type: str = "assistant"


@dataclass
class ResultMessage:
    """Final result message from a provider query."""
    result: str = ""
    subtype: str = "success"
    is_error: bool = False
    session_id: str = ""
    num_turns: int = 1
    total_cost_usd: float | None = None
    usage: dict[str, Any] | None = None
    duration_ms: int = 0
    duration_api_ms: int = 0
    type: str = "result"


class ProviderPlugin(ABC):
    """Abstract base class for CLI agent providers.

    Subclass this to add support for a new CLI agent (e.g., Codex, Ollama).

    Required attributes:
        name: Short identifier (e.g., "codex")
        detect_patterns: List of fnmatch patterns for model names

    Required methods:
        build_options(): Build provider-specific options
        query(): Async generator yielding AssistantMessage/ResultMessage
    """

    name: str = ""
    detect_patterns: list[str] = []

    @abstractmethod
    def build_options(self, **kwargs) -> Any:
        """Build provider-specific options from common parameters."""
        ...

    @abstractmethod
    async def query(
        self, prompt: str, options: Any,
    ) -> AsyncIterator[AssistantMessage | ResultMessage]:
        """Run a query and yield structured messages."""
        ...

    def get_message_types(self) -> dict[str, type]:
        """Return message type classes for isinstance checks."""
        return {
            "AssistantMessage": AssistantMessage,
            "ResultMessage": ResultMessage,
            "TextBlock": TextBlock,
            "ToolUseBlock": ToolUseBlock,
        }
```

**Step 4: Run tests**

Run: `pytest tests/test_provider_base.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/taskbrew/agents/provider_base.py tests/test_provider_base.py
git commit -m "feat: add ProviderPlugin abstract base class for extensible providers"
```

---

### Task 9: Create ProviderRegistry and refactor provider.py

**Files:**
- Modify: `src/taskbrew/agents/provider.py`
- Create: `config/providers/claude.yaml`
- Create: `config/providers/gemini.yaml`
- Test: `tests/test_provider_registry.py`

**Step 1: Write failing test**

```python
# tests/test_provider_registry.py
"""Tests for the ProviderRegistry."""

from taskbrew.agents.provider import ProviderRegistry


def test_registry_detect_claude():
    registry = ProviderRegistry()
    registry.register_builtins()
    assert registry.detect("claude-opus-4-6") == "claude"
    assert registry.detect("claude-sonnet-4-6") == "claude"


def test_registry_detect_gemini():
    registry = ProviderRegistry()
    registry.register_builtins()
    assert registry.detect("gemini-3.1-pro-preview") == "gemini"


def test_registry_detect_default():
    registry = ProviderRegistry()
    registry.register_builtins()
    assert registry.detect("unknown-model") == "claude"


def test_registry_load_yaml_provider(tmp_path):
    providers_dir = tmp_path / "providers"
    providers_dir.mkdir()
    (providers_dir / "test.yaml").write_text(
        'name: test\n'
        'display_name: "Test Provider"\n'
        'binary: test-cli\n'
        'detect_models: ["test-*"]\n'
        'command_template:\n'
        '  prompt_flag: "-p"\n'
        '  output_format_flag: "--output-format"\n'
        '  output_format_value: "stream-json"\n'
        '  model_flag: "-m"\n'
        '  auto_approve_flag: "-y"\n'
        'output_parser: "stream-json"\n'
        'system_prompt_mode: "xml-inject"\n'
        'models:\n'
        '  - id: "test-latest"\n'
        '    tier: flagship\n'
    )
    registry = ProviderRegistry()
    registry.register_builtins()
    loaded = registry.load_yaml_providers(providers_dir)
    assert "test" in loaded
    assert registry.detect("test-latest") == "test"
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_provider_registry.py -v`
Expected: FAIL — `ProviderRegistry` doesn't exist

**Step 3: Add ProviderRegistry to provider.py**

Add to `src/taskbrew/agents/provider.py` — a `ProviderRegistry` class that wraps the existing functions. The existing `detect_provider`, `build_sdk_options`, `sdk_query`, `get_message_types` functions remain as module-level functions for backward compatibility, but delegate to a global registry instance internally.

```python
from fnmatch import fnmatch
from pathlib import Path
import yaml


class ProviderRegistry:
    """Registry for CLI agent providers."""

    def __init__(self):
        self._providers: dict[str, dict] = {}

    def register(self, name: str, detect_patterns: list[str], **kwargs):
        self._providers[name] = {"detect_patterns": detect_patterns, **kwargs}

    def register_builtins(self):
        self.register("claude", detect_patterns=["claude-*"], builtin=True)
        self.register("gemini", detect_patterns=["gemini-*"], builtin=True)

    def detect(self, model: str) -> str:
        for name, info in self._providers.items():
            if any(fnmatch(model, pat) for pat in info["detect_patterns"]):
                return name
        return "claude"

    def load_yaml_providers(self, providers_dir: Path) -> list[str]:
        loaded = []
        if not providers_dir.is_dir():
            return loaded
        for yaml_file in sorted(providers_dir.glob("*.yaml")):
            with open(yaml_file) as f:
                data = yaml.safe_load(f)
            if not data or "name" not in data:
                continue
            name = data["name"]
            detect = data.get("detect_models", [])
            self.register(name, detect_patterns=detect, yaml_config=data)
            loaded.append(name)
        return loaded

    def get(self, name: str) -> dict | None:
        return self._providers.get(name)
```

**Step 4: Create provider YAML files**

`config/providers/claude.yaml`:
```yaml
name: claude
display_name: "Claude Code"
binary: claude
detect_models: ["claude-*"]
models:
  - id: "claude-opus-4-6"
    tier: flagship
  - id: "claude-sonnet-4-6"
    tier: balanced
  - id: "claude-haiku-4-5-20251001"
    tier: fast
```

`config/providers/gemini.yaml`:
```yaml
name: gemini
display_name: "Gemini CLI"
binary: gemini
detect_models: ["gemini-*"]
command_template:
  prompt_flag: "-p"
  output_format_flag: "--output-format"
  output_format_value: "stream-json"
  model_flag: "-m"
  auto_approve_flag: "-y"
output_parser: "stream-json"
system_prompt_mode: "xml-inject"
models:
  - id: "gemini-3.1-pro-preview"
    tier: flagship
  - id: "gemini-3-flash-preview"
    tier: balanced
```

**Step 5: Run tests**

Run: `pytest tests/test_provider_registry.py -v`
Expected: PASS

**Step 6: Run full test suite**

Run: `pytest tests/ -x -q --no-header`
Expected: All pass

**Step 7: Commit**

```bash
git add src/taskbrew/agents/provider.py config/providers/ tests/test_provider_registry.py
git commit -m "feat: add ProviderRegistry with YAML provider loading"
```

---

## Phase 4: Hybrid Agent Routing

### Task 10: Add routing_mode to RoleConfig

**Files:**
- Modify: `src/taskbrew/config_loader.py:121-141` (RoleConfig)
- Test: `tests/test_config_loader.py`

**Step 1: Write failing test**

```python
# tests/test_config_loader.py — add

def test_parse_role_routing_mode_open():
    from taskbrew.config_loader import _parse_role
    data = {
        "role": "test", "display_name": "Test", "prefix": "TS",
        "color": "#000", "emoji": "T", "system_prompt": "test",
        "routing_mode": "open",
    }
    cfg = _parse_role(data)
    assert cfg.routing_mode == "open"


def test_parse_role_routing_mode_default():
    from taskbrew.config_loader import _parse_role
    data = {
        "role": "test", "display_name": "Test", "prefix": "TS",
        "color": "#000", "emoji": "T", "system_prompt": "test",
    }
    cfg = _parse_role(data)
    assert cfg.routing_mode == "open"  # default
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_config_loader.py::test_parse_role_routing_mode_open -v`
Expected: FAIL — `routing_mode` not on `RoleConfig`

**Step 3: Add routing_mode to RoleConfig**

In `src/taskbrew/config_loader.py`, add to `RoleConfig` after line 141:

```python
    routing_mode: str = "open"  # "open" or "restricted"
```

In `_parse_role()`, add to the return statement (after line 179):

```python
        routing_mode=data.get("routing_mode", "open"),
```

**Step 4: Run tests**

Run: `pytest tests/test_config_loader.py::test_parse_role_routing_mode_open tests/test_config_loader.py::test_parse_role_routing_mode_default -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/taskbrew/config_loader.py tests/test_config_loader.py
git commit -m "feat: add routing_mode field to RoleConfig (default: open)"
```

---

### Task 11: Add agent manifest injection to build_context()

**Files:**
- Modify: `src/taskbrew/agents/agent_loop.py:172-181`
- Test: `tests/test_agent_loop_unit.py`

**Step 1: Write failing test**

```python
# tests/test_agent_loop_unit.py — add

async def test_build_context_open_routing_injects_manifest(tmp_path):
    """Open routing mode should inject all available agents into context."""
    from taskbrew.config_loader import RoleConfig, RouteTarget

    pm_role = RoleConfig(
        role="pm", display_name="PM", prefix="PM", color="#000",
        emoji="P", system_prompt="test", routing_mode="open",
    )
    arch_role = RoleConfig(
        role="architect", display_name="Architect", prefix="AR",
        color="#000", emoji="A", system_prompt="test",
        accepts=["tech_design"], routing_mode="open",
    )
    coder_role = RoleConfig(
        role="coder", display_name="Coder", prefix="CD",
        color="#000", emoji="C", system_prompt="test",
        accepts=["implementation", "bug_fix"], routing_mode="open",
    )
    all_roles = {"pm": pm_role, "architect": arch_role, "coder": coder_role}

    # Create a minimal AgentLoop with mocked dependencies
    # and call build_context to check manifest injection
    # (Exact test depends on how AgentLoop is instantiated in your tests)
    # The key assertion:
    # context should contain "## Available Agents"
    # context should contain 'assigned_to="architect"'
    # context should contain 'assigned_to="coder"'
    # context should NOT contain 'assigned_to="pm"' (self)
```

**Step 2: Implement manifest injection**

In `src/taskbrew/agents/agent_loop.py`, replace lines 172-181:

```python
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
```

**Step 3: Run tests**

Run: `pytest tests/test_agent_loop_unit.py -v -x`
Expected: PASS

**Step 4: Commit**

```bash
git add src/taskbrew/agents/agent_loop.py tests/test_agent_loop_unit.py
git commit -m "feat: inject agent manifest for open routing mode in build_context()"
```

---

### Task 12: Update API route validation to respect routing_mode

**Files:**
- Modify: `src/taskbrew/dashboard/routers/tasks.py:162-177`
- Test: `tests/test_dashboard_api.py`

**Step 1: Write failing test**

```python
# tests/test_dashboard_api.py — add

async def test_create_task_open_routing_allows_any_role(client):
    """In open routing mode, any role can create tasks for any other role."""
    # This test creates a task from pm to verifier (not in pm's routes_to)
    # With routing_mode="open" on pm, this should succeed
    # (Previously would return 403)
```

**Step 2: Modify validation logic**

In `src/taskbrew/dashboard/routers/tasks.py`, modify lines 162-177. Wrap the route enforcement check:

```python
        # 3. Validate creator role is allowed to route to target
        m = re.match(r'^(.+)-\d+$', body.assigned_by)
        if m:
            creator_role = m.group(1)
            if creator_role in orch.roles:
                creator_cfg = orch.roles[creator_role]
                routing_mode = getattr(creator_cfg, "routing_mode", "open")
                if routing_mode == "restricted":
                    allowed = any(
                        r.role == body.assigned_to
                        and (not r.task_types or body.task_type in r.task_types)
                        for r in creator_cfg.routes_to
                    )
                    if not allowed:
                        raise HTTPException(
                            403,
                            f"Role '{creator_role}' is not allowed to create "
                            f"'{body.task_type}' tasks for role '{body.assigned_to}' "
                            f"(restricted routing mode)",
                        )
                # If "open", skip route enforcement (Level 1 & 2 still apply)
```

**Step 3: Run tests**

Run: `pytest tests/test_dashboard_api.py -v -x`
Expected: PASS

**Step 4: Commit**

```bash
git add src/taskbrew/dashboard/routers/tasks.py tests/test_dashboard_api.py
git commit -m "feat: respect routing_mode in task creation API validation"
```

---

### Task 13: Add guardrails config and enforcement

**Files:**
- Modify: `src/taskbrew/config_loader.py` (add GuardrailsConfig)
- Modify: `src/taskbrew/dashboard/routers/tasks.py` (enforce guardrails)
- Test: `tests/test_guardrails.py` (new)

**Step 1: Write failing test**

```python
# tests/test_guardrails.py
"""Tests for task guardrails enforcement."""

from taskbrew.config_loader import GuardrailsConfig


def test_guardrails_defaults():
    g = GuardrailsConfig()
    assert g.max_task_depth == 10
    assert g.max_tasks_per_group == 50
    assert g.rejection_cycle_limit == 3
```

**Step 2: Add GuardrailsConfig dataclass**

In `src/taskbrew/config_loader.py`, add after `AutoScaleDefaults`:

```python
@dataclass
class GuardrailsConfig:
    """Guardrails to prevent runaway agent behavior."""

    max_task_depth: int = 10
    max_tasks_per_group: int = 50
    rejection_cycle_limit: int = 3
```

Add to `TeamConfig`:
```python
    guardrails: GuardrailsConfig = field(default_factory=GuardrailsConfig)
```

Parse in `load_team_config()`:
```python
    guardrails_raw = data.get("guardrails", {})
    guardrails = GuardrailsConfig(
        max_task_depth=guardrails_raw.get("max_task_depth", 10),
        max_tasks_per_group=guardrails_raw.get("max_tasks_per_group", 50),
        rejection_cycle_limit=guardrails_raw.get("rejection_cycle_limit", 3),
    )
```

**Step 3: Run test, verify pass, commit**

```bash
git add src/taskbrew/config_loader.py tests/test_guardrails.py
git commit -m "feat: add GuardrailsConfig for task depth/count limits"
```

---

## Phase 5: Plugin Wiring

### Task 14: Wire plugin_system.py into main.py and app.py

**Files:**
- Modify: `src/taskbrew/main.py`
- Modify: `src/taskbrew/dashboard/app.py`
- Create: `plugins/README.md`
- Test: `tests/test_plugin_wiring.py` (new)

**Step 1: Write failing test**

```python
# tests/test_plugin_wiring.py
"""Tests for plugin system integration."""

from pathlib import Path
from taskbrew.plugin_system import PluginRegistry


def test_plugin_registry_loads_from_directory(tmp_path):
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    (plugins_dir / "example.py").write_text(
        'metadata = {"name": "example", "version": "1.0"}\n'
        'def on_startup(data): pass\n'
    )
    registry = PluginRegistry()
    loaded = registry.load_plugins(plugins_dir)
    assert "example" in loaded
```

**Step 2: Add plugin loading to main.py**

In the `build_orchestrator` function, after all components are initialized:

```python
    # Load plugins
    from taskbrew.plugin_system import PluginRegistry
    plugin_registry = PluginRegistry()
    plugins_dir = Path(project_dir) / "plugins"
    if plugins_dir.is_dir():
        loaded = plugin_registry.load_plugins(plugins_dir)
        logger.info("Loaded %d plugins: %s", len(loaded), loaded)
    orch.plugin_registry = plugin_registry
```

**Step 3: Create plugins/README.md**

```markdown
# Plugins

Place Python plugin files in this directory to extend ai-team.

## Plugin Structure

Each plugin is a Python file with a `metadata` dict and hook functions:

    # plugins/my_plugin.py
    metadata = {
        "name": "my-plugin",
        "version": "1.0.0",
        "description": "What this plugin does",
    }

    async def on_startup(data):
        """Called when the orchestrator starts."""
        event_bus = data["event_bus"]
        event_bus.subscribe("task.completed", my_handler)

    async def my_handler(event):
        # React to task completion
        pass

## Available Hooks

- `on_startup` — Orchestrator initialized, receives `{orchestrator, event_bus}`
- `on_shutdown` — Orchestrator shutting down

## Event Types

Subscribe to any event via `event_bus.subscribe(event_type, handler)`:
- `task.created`, `task.completed`, `task.failed`, `task.claimed`
- `agent.text`, `agent.result`, `agent.status_changed`
- `tool.pre_use`, `tool.post_use`
```

**Step 4: Run test, verify pass, commit**

```bash
git add src/taskbrew/main.py src/taskbrew/dashboard/app.py plugins/README.md tests/test_plugin_wiring.py
git commit -m "feat: wire plugin system into main.py with startup hooks"
```

---

## Phase 6: Error Handling & DX

### Task 15: Enhanced health check with DB connectivity

**Files:**
- Modify: `src/taskbrew/dashboard/routers/tasks.py:37-39`
- Test: `tests/test_dashboard_api.py`

**Step 1: Write failing test**

```python
# tests/test_dashboard_api.py — add

async def test_health_check_returns_db_status(client):
    resp = await client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert "db" in data
    assert data["status"] == "ok"
```

**Step 2: Update health endpoint**

In `src/taskbrew/dashboard/routers/tasks.py`, replace lines 37-39:

```python
@router.get("/api/health")
async def health():
    orch = get_orch()
    try:
        await orch.task_board._db.execute_fetchone("SELECT 1")
        return {"status": "ok", "db": "connected"}
    except Exception as e:
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=503,
            content={"status": "degraded", "db": str(e)},
        )
```

**Step 3: Run test, verify pass, commit**

```bash
git add src/taskbrew/dashboard/routers/tasks.py tests/test_dashboard_api.py
git commit -m "feat: health check now verifies database connectivity"
```

---

### Task 16: Startup validation (fail fast)

**Files:**
- Modify: `src/taskbrew/main.py` (cli_main function)

**Step 1: Add validation to cli_main()**

Before starting the server in `cli_main()`, add validation:

```python
def _validate_startup(project_dir: Path, team_config, roles, cli_provider: str):
    """Validate configuration before starting. Raises SystemExit on failure."""
    import shutil

    errors = []

    # Check API key based on provider
    if cli_provider == "claude" and not os.environ.get("ANTHROPIC_API_KEY"):
        errors.append(
            "ANTHROPIC_API_KEY not set.\n"
            "  → Set it in your environment or .env file."
        )
    if cli_provider == "gemini" and not os.environ.get("GOOGLE_API_KEY"):
        errors.append(
            "GOOGLE_API_KEY not set.\n"
            "  → Set it in your environment or .env file."
        )

    # Check CLI binary exists
    if cli_provider == "claude" and not shutil.which("claude"):
        errors.append(
            "Claude CLI not found.\n"
            "  → Install: npm install -g @anthropic-ai/claude-code"
        )
    if cli_provider == "gemini" and not shutil.which("gemini"):
        errors.append(
            "Gemini CLI not found.\n"
            "  → Install: npm install -g @google/gemini-cli"
        )

    # Check roles exist
    if not roles:
        errors.append(
            "No role files found in config/roles/\n"
            "  → Create role YAML files or run: ai-team init"
        )

    # Validate routing
    from taskbrew.config_loader import validate_routing
    routing_errors = validate_routing(roles)
    for e in routing_errors:
        errors.append(f"Routing error: {e}")

    if errors:
        print("\n❌ Startup validation failed:\n")
        for i, err in enumerate(errors, 1):
            print(f"  {i}. {err}\n")
        raise SystemExit(1)
```

**Step 2: Run full test suite**

Run: `pytest tests/ -x -q --no-header`
Expected: All pass

**Step 3: Commit**

```bash
git add src/taskbrew/main.py
git commit -m "feat: fail-fast startup validation with user-friendly error messages"
```

---

### Task 17: Add ai-team init and ai-team doctor subcommands

**Files:**
- Modify: `src/taskbrew/main.py` (argparse setup)

**Step 1: Add argparse subcommands**

Modify `cli_main()` to use subcommands:

```python
def cli_main():
    parser = argparse.ArgumentParser(description="AI Team Orchestrator")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # start (default)
    start_parser = subparsers.add_parser("start", help="Start the orchestrator server")
    start_parser.add_argument("--project-dir", default=".", help="Project directory")
    start_parser.add_argument("--host", default=None)
    start_parser.add_argument("--port", type=int, default=None)

    # init
    init_parser = subparsers.add_parser("init", help="Initialize a new project")
    init_parser.add_argument("--name", help="Project name")
    init_parser.add_argument("--dir", default=".", help="Project directory")
    init_parser.add_argument("--provider", default="claude", help="CLI provider (claude/gemini)")

    # doctor
    subparsers.add_parser("doctor", help="Check system requirements")

    args = parser.parse_args()

    if args.command == "init":
        _cmd_init(args)
    elif args.command == "doctor":
        _cmd_doctor(args)
    else:
        _cmd_start(args)
```

Add `_cmd_init()` and `_cmd_doctor()` functions implementing the flows from the design doc.

**Step 2: Run test suite**

Run: `pytest tests/ -x -q --no-header`
Expected: All pass

**Step 3: Commit**

```bash
git add src/taskbrew/main.py
git commit -m "feat: add 'ai-team init' and 'ai-team doctor' CLI subcommands"
```

---

## Phase 7: Documentation

### Task 18: Create README.md

**Files:**
- Create: `README.md`

Write a comprehensive README with:
1. Project title + one-line description
2. Feature highlights (4 extension points, hybrid routing, multi-project)
3. Quick start (5 steps)
4. Architecture diagram (ASCII)
5. Configuration overview with links to docs/
6. Extension examples (new role, new provider, new MCP tool)
7. Contributing section
8. License (MIT)

**Commit:**

```bash
git add README.md
git commit -m "docs: add comprehensive README.md for open-source release"
```

---

### Task 19: Create CONTRIBUTING.md and CHANGELOG.md

**Files:**
- Create: `CONTRIBUTING.md`
- Create: `CHANGELOG.md`

**CONTRIBUTING.md contents:**
1. Development setup (`git clone`, `pip install -e ".[dev]"`)
2. Running tests (`pytest tests/ -x`)
3. Code style (ruff, line-length 100, Python 3.10+ hints)
4. Branch naming (`feat/`, `fix/`, `docs/`)
5. Commit messages (conventional commits)
6. PR checklist (tests pass, ruff clean, docs updated)

**CHANGELOG.md contents:**
See design doc section 9.

**Commit:**

```bash
git add CONTRIBUTING.md CHANGELOG.md
git commit -m "docs: add CONTRIBUTING.md and CHANGELOG.md"
```

---

### Task 20: Create docs/getting-started.md, configuration.md, extending.md, architecture.md

**Files:**
- Create: `docs/getting-started.md`
- Create: `docs/configuration.md`
- Create: `docs/extending.md`
- Create: `docs/architecture.md`

See design doc section 9 for content outlines. Each doc should be 200-500 lines covering:

- **getting-started.md**: Prerequisites, install, init, configure, start, first task
- **configuration.md**: Full team.yaml reference, role YAML reference, provider YAML, MCP servers, env vars
- **extending.md**: New role (YAML), new provider (YAML + plugin), new MCP tool, writing plugins
- **architecture.md**: System overview, task lifecycle, agent lifecycle, event bus, provider abstraction

**Commit:**

```bash
git add docs/getting-started.md docs/configuration.md docs/extending.md docs/architecture.md
git commit -m "docs: add getting-started, configuration, extending, and architecture guides"
```

---

## Phase 8: Polish

### Task 21: Run full test suite and fix any failures

**Step 1: Run full suite**

```bash
pytest tests/ -x -v --tb=short 2>&1 | head -100
```

**Step 2: Fix any failures**

Address each failure. Common issues:
- Tests that reference old file paths or removed files
- Tests that depend on old routing validation (now needs routing_mode)
- Import errors from refactored provider.py

**Step 3: Run ruff**

```bash
ruff check src/ tests/ --fix
```

**Step 4: Commit**

```bash
git add -A
git commit -m "fix: resolve test failures and lint issues from refactoring"
```

---

### Task 22: Create example custom role

**Files:**
- Create: `config/roles/security-reviewer.yaml.example`

```yaml
# Example custom role — rename to security-reviewer.yaml to activate
role: security-reviewer
display_name: "Security Reviewer"
prefix: "SR"
emoji: "🔒"
color: "#e74c3c"

system_prompt: |
  You are a security-focused code reviewer. Analyze code for:
  - OWASP Top 10 vulnerabilities
  - Injection attacks (SQL, command, XSS)
  - Authentication/authorization issues
  - Secrets and credential exposure
  Report findings with severity ratings.

model: claude-sonnet-4-6
tools: [Read, Glob, Grep, Bash, mcp__task-tools__create_task]

produces: [security_audit, security_approval]
accepts: [security_review]

routing_mode: open

max_instances: 1
max_turns: 15
max_execution_time: 900
```

**Commit:**

```bash
git add config/roles/security-reviewer.yaml.example
git commit -m "docs: add example security-reviewer custom role"
```

---

### Task 23: Final verification and tag

**Step 1: Run full test suite**

```bash
pytest tests/ -x -q --no-header
```

Expected: All tests pass

**Step 2: Run lint**

```bash
ruff check src/ tests/
```

Expected: No errors

**Step 3: Verify install from scratch**

```bash
pip install -e ".[dev]"
ai-team doctor
```

Expected: All checks pass (or clear messages for optional dependencies)

**Step 4: Final commit and tag**

```bash
git add -A
git commit -m "chore: v1.0.0 final polish for open-source release"
git tag v1.0.0
```
