# Design: Open-Source Release — ai-team v1.0

**Date:** 2026-02-27
**Status:** Approved
**Approach:** Extensibility-first refactor (Option B) with hybrid routing (Option C)

---

## 1. Project Structure (Post-Cleanup)

```
ai-team/
├── config/
│   ├── team.yaml                # Team settings, MCP servers, guardrails
│   ├── roles/                   # Drop-in role definitions
│   │   ├── pm.yaml
│   │   ├── architect.yaml
│   │   ├── coder.yaml
│   │   └── verifier.yaml
│   └── providers/               # CLI provider definitions (YAML)
│       ├── claude.yaml
│       └── gemini.yaml
├── plugins/                     # Python plugins (advanced extensibility)
│   └── README.md                # How to write a plugin
├── src/taskbrew/                 # Core library
│   ├── agents/
│   │   ├── base.py              # AgentRunner
│   │   ├── agent_loop.py        # Task polling, context building, execution
│   │   ├── auto_scaler.py       # Auto-scaling logic
│   │   ├── provider.py          # Provider registry (refactored)
│   │   ├── gemini_cli.py        # Gemini CLI direct integration
│   │   └── provider_base.py     # NEW: Abstract provider interface
│   ├── orchestrator/
│   │   ├── database.py          # SQLite + migrations
│   │   ├── task_board.py        # Task state machine
│   │   ├── event_bus.py         # Pub/sub events
│   │   └── ...
│   ├── dashboard/               # FastAPI web UI
│   │   ├── app.py
│   │   ├── routers/             # 19 API routers
│   │   ├── templates/           # Jinja2 HTML
│   │   └── static/              # CSS/JS
│   ├── tools/                   # MCP tool servers
│   │   ├── task_tools.py        # Core task management tools
│   │   └── intelligence_tools.py
│   ├── intelligence/            # Intelligence modules
│   ├── main.py                  # Entry point, orchestrator bootstrap
│   ├── config.py                # Dataclasses
│   ├── config_loader.py         # YAML parsing + validation
│   ├── project_manager.py       # Multi-project support
│   ├── plugin_system.py         # Plugin registry (wire into app)
│   └── logging_config.py        # Structured logging
├── tests/                       # 78 test files, 1200+ tests
├── docs/
│   ├── getting-started.md       # NEW: Quick start guide
│   ├── configuration.md         # NEW: Full config reference
│   ├── extending.md             # NEW: How to add providers, roles, tools, plugins
│   ├── architecture.md          # NEW: System design overview
│   └── plans/                   # Design documents (kept)
├── README.md                    # NEW
├── LICENSE                      # NEW (MIT)
├── CONTRIBUTING.md              # NEW
├── CHANGELOG.md                 # NEW
├── .env.example                 # NEW
├── .gitignore                   # Updated
├── pyproject.toml               # Updated (metadata, version)
├── Dockerfile
└── docker-compose.yaml
```

### Files to REMOVE (clean slate)

```
# Experimental / internal artifacts
flappy-bird/                     # Test game project (1.4 MB)
analysis/                        # Internal module analysis (12 files)
ANALYSIS-COMPLETE.md
CD-197-BRANCH-CLEANUP.md
IMPLEMENTATION-GUIDE-BRANCH-ISOLATION.md
RV-170-CODE-REVIEW.md
RV-220-ANALYSIS-README.md
RV-220-ROOT-CAUSE-SUMMARY.md
TECHNICAL_ANALYSIS_RV-220.md
serve_output.log
node_modules/                    # Should be gitignored
package.json                     # Not needed (Claude SDK is Python)
package-lock.json

# Internal review/audit docs (keep docs/plans/ only)
docs/audits/
docs/AR-057-*.md
docs/CD-141-*.md
docs/CD-166-*.md
docs/RV-190-*.md
docs/RV-227-*.md
docs/ARCHITECTURE-INVESTIGATION-AR-053.md
docs/AR-053-INVESTIGATION-SUMMARY.md
docs/ADR-001-PIPELINE-INVENTORY.md
docs/ADR-002-RELEASE-PIPELINE.md
```

---

## 2. Extension Point 1: Providers (YAML + Plugin)

### Current Problem
- `provider.py` has hardcoded `if provider == "gemini" ... else ...` branches
- Adding a new provider (e.g., OpenAI Codex CLI, Ollama) requires modifying core code

### Design

**Tier 1 — YAML Provider (for CLIs with stream-json output):**

```yaml
# config/providers/codex.yaml
name: codex
display_name: "OpenAI Codex CLI"
binary: codex                     # Or absolute path
detect_models: ["codex-*", "o4-*"]  # Model name patterns
command_template:
  prompt_flag: "-p"
  output_format_flag: "--output-format"
  output_format_value: "stream-json"
  model_flag: "-m"
  auto_approve_flag: "-y"
  extra_flags: []
output_parser: "stream-json"      # Use built-in stream-json parser
system_prompt_mode: "xml-inject"  # Prepend <system> tags to prompt
models:
  - id: "codex-latest"
    tier: flagship
  - id: "o4-mini"
    tier: balanced
```

YAML providers reuse the existing stream-json parser from `gemini_cli.py` (extracted into a shared `StreamJsonParser` class).

**Tier 2 — Python Plugin Provider (for custom output formats):**

```python
# plugins/ollama_provider.py
from taskbrew.agents.provider_base import ProviderPlugin, ProviderOptions

class OllamaProvider(ProviderPlugin):
    name = "ollama"
    detect_patterns = ["llama-*", "mistral-*"]

    def build_options(self, system_prompt, model, **kwargs):
        return ProviderOptions(
            system_prompt=system_prompt,
            model=model,
            # custom fields...
        )

    async def query(self, prompt, options):
        # Custom subprocess + parsing logic
        async for message in self._run_ollama(prompt, options):
            yield message  # Must yield AssistantMessage or ResultMessage
```

### Implementation: `provider_base.py` (NEW)

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass

@dataclass
class ProviderOptions:
    system_prompt: str | None = None
    model: str | None = None
    max_turns: int | None = None
    cwd: str | None = None

class ProviderPlugin(ABC):
    name: str
    detect_patterns: list[str] = []

    @abstractmethod
    def build_options(self, **kwargs) -> ProviderOptions: ...

    @abstractmethod
    async def query(self, prompt: str, options: ProviderOptions): ...

    def get_message_types(self) -> dict[str, type]: ...
```

### Implementation: `provider.py` (REFACTORED)

Replace hardcoded if/else with a **provider registry**:

```python
class ProviderRegistry:
    def __init__(self):
        self._providers: dict[str, ProviderPlugin] = {}

    def register(self, provider: ProviderPlugin):
        self._providers[provider.name] = provider

    def detect(self, model: str) -> str:
        for name, p in self._providers.items():
            if any(fnmatch(model, pat) for pat in p.detect_patterns):
                return name
        return "claude"  # default

    def load_yaml_providers(self, config_dir: Path): ...
    def load_plugin_providers(self, plugins_dir: Path): ...
```

Built-in Claude and Gemini providers are registered at startup. YAML and plugin providers are discovered from `config/providers/` and `plugins/`.

---

## 3. Extension Point 2: MCP Tool Servers (Config-Driven)

### Current Problem
- MCP servers hardcoded in `provider.py` lines 83-96
- Only `task-tools` and `intelligence-tools` exist
- Adding custom MCP tools requires modifying Python code

### Design

**Define MCP servers in `team.yaml`:**

```yaml
# config/team.yaml
mcp_servers:
  # Built-in (auto-registered, always available)
  task-tools:
    builtin: true  # Signals this is a core server

  intelligence-tools:
    builtin: true

  # User-defined external MCP server
  my-database-tool:
    command: "python"
    args: ["/path/to/my_db_tool.py"]
    env:
      DATABASE_URL: "sqlite:///my.db"
    transport: stdio              # stdio (default) or sse

  # User-defined via npx
  github-tools:
    command: "npx"
    args: ["-y", "@anthropic/mcp-github"]
    env:
      GITHUB_TOKEN: "${GITHUB_TOKEN}"  # Env var interpolation
    transport: stdio
```

**Per-role tool access in role YAML:**

```yaml
# config/roles/coder.yaml
tools:
  - Read
  - Write
  - Bash
  - mcp__task-tools__create_task
  - mcp__task-tools__complete_task
  - mcp__my-database-tool__query    # Custom MCP tool
  - mcp__github-tools__create_pr    # External MCP tool
```

### Implementation Changes

1. **`config_loader.py`** — Parse `mcp_servers` from `team.yaml` into a `dict[str, MCPServerConfig]` dataclass
2. **`provider.py`** — `build_sdk_options()` reads MCP config from team config instead of hardcoding:
   ```python
   def build_sdk_options(*, mcp_servers: dict[str, MCPServerConfig], ...):
       mcp_dict = {}
       for name, cfg in mcp_servers.items():
           if cfg.builtin:
               mcp_dict[name] = _builtin_mcp_server(name, api_url, db_path)
           else:
               mcp_dict[name] = {
                   "type": cfg.transport,
                   "command": cfg.command,
                   "args": cfg.args,
                   "env": _interpolate_env(cfg.env),
               }
       return ClaudeAgentOptions(mcp_servers=mcp_dict, ...)
   ```
3. **Gemini provider** — For Gemini CLI, MCP servers are configured via `gemini mcp add` commands at project scope. The system will auto-configure these on project activation.

### Environment Variable Interpolation

MCP server env values support `${VAR_NAME}` syntax:
```yaml
env:
  GITHUB_TOKEN: "${GITHUB_TOKEN}"    # Resolved from process env
  STATIC_VALUE: "hardcoded"          # Used as-is
```

---

## 4. Extension Point 3: Roles (Drop-in YAML)

### Current State — Already Good

Roles are already extensible via YAML files in `config/roles/`. Adding a new role = create a new YAML file. The system auto-discovers all YAML files in the directory.

### Improvements Needed

1. **Role YAML schema documentation** — Document all fields with types and defaults
2. **Validation improvements** — Better error messages when a role references unknown tools or MCP servers
3. **Example custom roles** — Ship with commented-out example:

```yaml
# config/roles/security-reviewer.yaml.example
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

routes_to:
  - role: coder
    task_types: [bug_fix]

max_instances: 1
max_turns: 15
```

---

## 5. Extension Point 4: Plugin System (Wire Into App)

### Current Problem
- `plugin_system.py` has a full hook-based plugin architecture
- But it's NOT wired into `main.py` or the FastAPI app
- Plugin routes and tools are registered but never used

### Design — Wire Existing System

**Startup integration in `main.py`:**

```python
async def build_orchestrator(project_dir, ...):
    # ... existing setup ...

    # Load plugins
    plugins_dir = Path(project_dir) / "plugins"
    if plugins_dir.exists():
        loaded = plugin_registry.load_plugins(plugins_dir)
        logger.info("Loaded %d plugins: %s", len(loaded), loaded)

    # Fire startup hook
    await plugin_registry.fire_hook("on_startup", {
        "orchestrator": orch,
        "event_bus": event_bus,
    })

    return orch
```

**Dashboard integration in `app.py`:**

```python
# After standard router registration
for route in plugin_registry.get_all_routes():
    app.include_router(route.router, prefix=route.prefix, tags=[route.tag])
```

**Event bus integration:**

```python
# Plugins can subscribe to events via hooks
@plugin.hook("on_startup")
async def setup(data):
    event_bus = data["event_bus"]
    event_bus.subscribe("task.completed", my_handler)
```

### Plugin Example

```python
# plugins/slack_notifications.py
"""Send Slack notifications on task events."""

metadata = {
    "name": "slack-notifications",
    "version": "1.0.0",
    "description": "Posts task events to a Slack channel",
}

async def on_startup(data):
    event_bus = data["event_bus"]
    event_bus.subscribe("task.completed", _notify_complete)
    event_bus.subscribe("task.failed", _notify_failure)

async def _notify_complete(event):
    # Post to Slack webhook...
    pass
```

---

## 6. Agent Routing — Option C: Hybrid (Open + Restricted)

### Current Problem
- Agents learn about other roles only through hardcoded system prompt instructions
- Adding a new role requires updating system prompts of all roles that should route to it
- PM and Architect get routing hints in context; Coder and Verifier don't
- No dynamic discovery of available agents

### Design

**New config field in role YAML:**

```yaml
# config/roles/pm.yaml
routing_mode: "open"        # "open" (default) or "restricted"

# When "restricted", only these routes are allowed (current behavior):
routes_to:
  - role: architect
    task_types: [tech_design]

# When "open", routes_to is ignored and the agent can delegate to any role.
# The agent manifest is injected into context automatically.
```

**Default: `routing_mode: "open"`** — all agents discover all other agents.

**Agent manifest injection in `agent_loop.py` `build_context()`:**

```python
# Replace lines 172-181 with:
if self.role_config.routing_mode == "open":
    parts.append("\n## Available Agents")
    parts.append("You may create tasks for any of these agents:\n")
    for name, role in self.all_roles.items():
        if name == self.role_config.role:
            continue  # Don't list self
        accepts = ", ".join(role.accepts) if role.accepts else "any"
        parts.append(
            f"- **{role.display_name}** ({role.prefix}): "
            f"assigned_to=\"{name}\", accepts: [{accepts}]"
        )
    parts.append(
        "\nUse create_task(assigned_to=\"<role>\", task_type=\"<type>\") "
        "to delegate work."
    )
elif self.role_config.routes_to:
    # Restricted mode: current behavior
    parts.append("\n## When Complete")
    parts.append("Create tasks for:")
    for route in self.role_config.routes_to:
        parts.append(
            f"- **{route.role}** (types: {', '.join(route.task_types)})"
        )
```

**API validation update in `dashboard/routers/tasks.py`:**

```python
# Modify 3-level validation (lines 162-177):
if creator_role_config.routing_mode == "restricted":
    # Enforce routes_to rules (current behavior)
    allowed = any(
        r.role == body.assigned_to
        and (not r.task_types or body.task_type in r.task_types)
        for r in creator_role_config.routes_to
    )
    if not allowed:
        raise HTTPException(403, "Role not allowed to route here (restricted mode)")
# If "open", skip route enforcement — but still validate target role exists
# and target role accepts this task_type (Level 1 & Level 2 stay)
```

### Guardrails (in `team.yaml`)

```yaml
guardrails:
  max_task_depth: 5             # Max parent chain length
  max_tasks_per_group: 20       # Cap total tasks in one group
  require_approval_after: 10    # Pause for human review after N tasks
  rejection_cycle_limit: 3      # Max revision loops (already exists)
```

These are enforced at the task board level regardless of routing mode.

---

## 7. Configuration System

### Database Path Fix

**Change `config/team.yaml` default from:**
```yaml
database:
  path: "/Users/nikhilchatragadda/.ai-team/data/ai-team.db"
```

**To:**
```yaml
database:
  path: "~/.ai-team/data/ai-team.db"
```

**`config_loader.py`** resolves `~` via `Path.expanduser()`.

**`project_manager.py`** already uses `Path.home() / ".ai-team" / "data"` — keep this pattern everywhere.

### Config Hierarchy (Documented)

```
Priority (highest to lowest):
1. Runtime API changes (PUT /api/settings/*)  → persisted to YAML
2. YAML config files (config/team.yaml, config/roles/*.yaml)
3. Environment variables (ANTHROPIC_API_KEY, AI_TEAM_DB_PATH, etc.)
4. Code defaults (config_loader.py hardcoded fallbacks)
```

### .env.example

```env
# Required
ANTHROPIC_API_KEY=your-anthropic-api-key-here

# Optional — Gemini provider
GOOGLE_API_KEY=your-google-api-key-here

# Optional — Server
AI_TEAM_API_URL=http://127.0.0.1:8420
AI_TEAM_DB_PATH=~/.ai-team/data/ai-team.db

# Optional — Logging
LOG_LEVEL=INFO
LOG_FORMAT=dev          # "dev" (human-readable) or "json" (structured)

# Optional — Auth
AUTH_ENABLED=false
CORS_ORIGINS=http://localhost:8000,http://localhost:3000
```

---

## 8. Error Handling Improvements

### Current State (Already Solid)

- Retry: exponential backoff (5s, 15s, 45s), 3 retries max
- Timeout: 30-minute default, no retry on timeout
- Failure cascade: blocked dependents auto-fail
- Orphan recovery: every 30s for stale agents (90s heartbeat timeout)
- Graceful shutdown: 4-phase with 30s timeout
- Structured logging with correlation IDs

### Improvements for Open Source

1. **Health check enhancement** — Add DB connectivity check:
   ```python
   @router.get("/api/health")
   async def health():
       try:
           await orch.task_board._db.execute_fetchone("SELECT 1")
           return {"status": "ok", "db": "connected"}
       except Exception as e:
           return JSONResponse(
               status_code=503,
               content={"status": "degraded", "db": str(e)},
           )
   ```

2. **Startup validation** — Fail fast on missing requirements:
   ```python
   # In main.py cli_main():
   # 1. Check ANTHROPIC_API_KEY or GOOGLE_API_KEY exists
   # 2. Check config/team.yaml exists and is valid
   # 3. Check at least one role YAML exists
   # 4. Validate routing before starting agents
   # 5. Check CLI binaries exist (claude/gemini) based on provider
   ```

3. **User-facing error messages** — Wrap common errors:
   ```
   Error: ANTHROPIC_API_KEY not set.
   → Set it in your environment or .env file. See docs/getting-started.md

   Error: No role files found in config/roles/
   → Create role YAML files or run: ai-team init

   Error: Gemini CLI not found.
   → Install it: npm install -g @google/gemini-cli
   ```

4. **`ai-team doctor` command** — Diagnostic subcommand:
   ```
   $ ai-team doctor
   ✓ Python 3.12.0
   ✓ config/team.yaml found
   ✓ 4 roles loaded (pm, architect, coder, verifier)
   ✓ Routing valid (pm → architect → coder → verifier)
   ✓ Claude CLI found at /usr/local/bin/claude
   ✗ Gemini CLI not found (optional)
   ✓ Database writable (~/.ai-team/data/ai-team.db)
   ✓ MCP servers: task-tools, intelligence-tools
   ✓ 0 plugins loaded
   ```

---

## 9. Documentation

### README.md

Structure:
1. Project name + one-line description
2. Architecture diagram (ASCII or mermaid)
3. Quick start (5 steps: install → configure → start → create task → watch)
4. Features list
5. Configuration overview (link to docs/configuration.md)
6. Extending (link to docs/extending.md)
7. Contributing (link to CONTRIBUTING.md)
8. License (MIT)

### docs/getting-started.md

1. Prerequisites (Python 3.10+, Claude CLI or Gemini CLI)
2. Installation (`pip install ai-team` or `git clone` + `pip install -e .`)
3. First project setup (`ai-team init`)
4. Configuration walkthrough (team.yaml, role YAMLs)
5. Starting the server (`ai-team start`)
6. Creating your first task (via dashboard or API)
7. Watching the pipeline run
8. Docker setup (alternative)

### docs/configuration.md

1. team.yaml reference (all fields, types, defaults)
2. Role YAML reference (all fields, types, defaults)
3. Provider YAML reference
4. MCP server configuration
5. Environment variables
6. Config hierarchy / precedence

### docs/extending.md

1. Adding a new role (YAML example)
2. Adding a new CLI provider (YAML for simple, Python plugin for complex)
3. Adding custom MCP tools (YAML config + tool server)
4. Writing a plugin (hooks, routes, tools)
5. Custom intelligence modules (plugin-based)

### docs/architecture.md

1. System overview (orchestrator, agents, dashboard, tools)
2. Task lifecycle (pending → claimed → in_progress → completed/failed)
3. Agent lifecycle (idle → working → idle)
4. Event bus (pub/sub, event types)
5. Provider abstraction (how Claude/Gemini/custom CLIs are wrapped)
6. MCP tool integration
7. Multi-project support

### CONTRIBUTING.md

1. Development setup
2. Running tests (`pytest tests/ -x`)
3. Code style (ruff, line length 100, Python 3.10+ type hints)
4. Branch naming (feat/, fix/, docs/)
5. Commit messages (conventional commits)
6. PR process
7. Adding tests for new features

### CHANGELOG.md

```markdown
# Changelog

## [1.0.0] - 2026-02-27

### Added
- Multi-agent orchestration with configurable roles
- Support for Claude Code and Gemini CLI as agent providers
- Config-driven MCP tool server registration
- YAML-based provider definitions for easy CLI additions
- Plugin system with hooks, routes, and tools
- Hybrid agent routing (open discovery + optional restrictions)
- Dashboard with real-time WebSocket updates
- Multi-project support with per-project isolation
- Auto-scaling agents based on task queue depth
- Structured logging (JSON + dev formats)
- Docker support with health checks
- 1200+ tests
```

---

## 10. pyproject.toml Updates

```toml
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
keywords = ["ai", "agents", "orchestrator", "multi-agent", "claude", "gemini"]
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

[project.urls]
Homepage = "https://github.com/nikhilchatragadda/ai-team"
Documentation = "https://github.com/nikhilchatragadda/ai-team/tree/main/docs"
Repository = "https://github.com/nikhilchatragadda/ai-team"
Issues = "https://github.com/nikhilchatragadda/ai-team/issues"
```

---

## 11. .gitignore Updates

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

# Node (if any)
node_modules/

# Coverage
.coverage
htmlcov/

# Logs
*.log
```

---

## 12. CLI Improvements

### Current: `ai-team` entry point calls `cli_main()`

### Add subcommands:

```
ai-team start              # Start the server (current default)
ai-team init               # Interactive project setup
ai-team doctor             # Diagnostic checks
ai-team status             # Show running agents and task counts
```

**`ai-team init` flow:**
```
$ ai-team init
Project name: my-saas-app
Project directory [.]: /path/to/project
CLI provider [claude]: gemini
Create default roles (pm, architect, coder, verifier)? [Y/n]: y

✓ Created config/team.yaml
✓ Created config/roles/pm.yaml
✓ Created config/roles/architect.yaml
✓ Created config/roles/coder.yaml
✓ Created config/roles/verifier.yaml
✓ Created .env.example

Ready! Run 'ai-team start' to begin.
```

---

## 13. Implementation Phases

### Phase 1: Foundation (Critical for Release)
1. Create LICENSE (MIT)
2. Fix hardcoded DB path → `~/.ai-team/data/{project}.db`
3. Create .env.example
4. Update .gitignore
5. Update pyproject.toml metadata
6. Remove experimental files (clean slate)
7. Create README.md
8. Create CONTRIBUTING.md
9. Create CHANGELOG.md

### Phase 2: Extensibility — MCP Tools
10. Add `mcp_servers` section to team.yaml schema
11. Refactor provider.py to read MCP config from team config
12. Add env var interpolation for MCP server env values
13. Update config_loader.py to parse MCPServerConfig
14. Tests for config-driven MCP registration

### Phase 3: Extensibility — Providers
15. Create provider_base.py (abstract interface)
16. Extract StreamJsonParser from gemini_cli.py (shared utility)
17. Create config/providers/ directory with claude.yaml and gemini.yaml
18. Refactor provider.py into ProviderRegistry
19. Add YAML provider loading
20. Add plugin provider loading
21. Tests for provider registry

### Phase 4: Agent Routing — Hybrid
22. Add `routing_mode` field to RoleConfig (default: "open")
23. Add agent manifest injection to agent_loop.py build_context()
24. Update API validation to respect routing_mode
25. Add guardrails config to team.yaml
26. Enforce guardrails in task_board.py
27. Tests for open routing, restricted routing, guardrails

### Phase 5: Plugin Wiring
28. Wire plugin_system.py into main.py (load on startup)
29. Wire plugin routes into FastAPI app
30. Wire plugin event hooks into event bus
31. Create example plugin (plugins/README.md)
32. Tests for plugin loading and hook firing

### Phase 6: Error Handling & DX
33. Enhanced health check (DB connectivity)
34. Startup validation (fail fast)
35. `ai-team doctor` command
36. `ai-team init` command
37. User-facing error messages

### Phase 7: Documentation
38. docs/getting-started.md
39. docs/configuration.md
40. docs/extending.md
41. docs/architecture.md
42. Update README.md with final content

### Phase 8: Polish
43. Run full test suite, fix any failures
44. Run ruff, fix all lint issues
45. Clean up git branches (delete 200+ feature branches)
46. Final review pass
