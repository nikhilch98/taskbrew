# Configuration Reference

All configuration lives in the `config/` directory. This document covers every
field in `team.yaml`, role YAML files, provider YAML files, and environment
variables.

## team.yaml

The top-level team configuration file at `config/team.yaml`.

```yaml
team_name: "AI Development Team"

database:
  path: "~/.taskbrew/data/taskbrew.db"

dashboard:
  host: "0.0.0.0"
  port: 8420

artifacts:
  base_dir: "artifacts"

cli_provider: "claude"

defaults:
  max_instances: 1
  poll_interval_seconds: 5
  idle_timeout_minutes: 30
  auto_scale:
    enabled: false
    scale_up_threshold: 3
    scale_down_idle: 15

group_prefixes:
  pm: "FEAT"
  architect: "DEBT"

auth:
  enabled: false
  tokens: []

cost_budgets:
  enabled: false

webhooks:
  enabled: false

guardrails:
  max_task_depth: 10
  max_tasks_per_group: 50
  rejection_cycle_limit: 3

mcp_servers: {}
```

### Field reference

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `team_name` | string | *required* | Display name for the team |
| `database.path` | string | *required* | Path to the SQLite database. Supports `~` expansion |
| `dashboard.host` | string | *required* | Bind address for the dashboard server |
| `dashboard.port` | integer | *required* | Port for the dashboard server |
| `artifacts.base_dir` | string | *required* | Directory for storing task artifacts |
| `cli_provider` | string | `"claude"` | Default CLI provider (`"claude"` or `"gemini"`) |
| `defaults.max_instances` | integer | `1` | Default max agent instances per role |
| `defaults.poll_interval_seconds` | integer | `5` | Seconds between task poll cycles |
| `defaults.idle_timeout_minutes` | integer | `30` | Minutes before idle agents are considered stale |
| `defaults.auto_scale.enabled` | boolean | `false` | Enable auto-scaling by default |
| `defaults.auto_scale.scale_up_threshold` | integer | `3` | Pending tasks needed to trigger scale-up |
| `defaults.auto_scale.scale_down_idle` | integer | `15` | Idle minutes before scaling down |
| `group_prefixes` | map | `{}` | Maps role names to group ID prefixes |
| `auth.enabled` | boolean | `false` | Enable API authentication |
| `auth.tokens` | list | `[]` | Valid bearer tokens for API access |
| `cost_budgets.enabled` | boolean | `false` | Enable cost budget tracking |
| `webhooks.enabled` | boolean | `false` | Enable webhook delivery |
| `guardrails` | object | see below | Limits to prevent runaway behavior |
| `mcp_servers` | map | `{}` | Custom MCP tool server definitions |

### Guardrails

Guardrails prevent runaway agent behavior:

| Field | Default | Description |
|-------|---------|-------------|
| `max_task_depth` | `10` | Maximum depth of parent-child task chains |
| `max_tasks_per_group` | `50` | Maximum tasks allowed in a single group |
| `rejection_cycle_limit` | `3` | Max reject-revise cycles before escalation |

---

## Role YAML

Each file in `config/roles/` defines one agent role. The filename should match
the role name (e.g., `pm.yaml` for role `pm`).

### Example role

```yaml
role: pm
display_name: "Product Manager"
prefix: "PM"
emoji: "\U0001F4CB"
color: "#3b82f6"

system_prompt: |
  You are a Product Manager on an AI development team.
  Your responsibilities:
  1. Decompose high-level goals into detailed PRDs
  2. Read the codebase to understand scope and dependencies
  3. Create well-scoped architect tasks using the create_task tool
  4. You NEVER write code -- only analysis and documentation

tools: [Read, Glob, Grep, WebSearch, mcp__task-tools__create_task]
model: claude-opus-4-6

produces: [prd, goal_decomposition, requirement]
accepts: [goal, revision]

routes_to:
  - role: architect
    task_types: [tech_design, architecture_review]

routing_mode: open

can_create_groups: true
group_type: "FEAT"

max_instances: 1
max_turns: 30
max_execution_time: 1800

auto_scale:
  enabled: false
  scale_up_threshold: 3
  scale_down_idle: 15

context_includes:
  - parent_artifact
  - root_artifact
  - sibling_summary
```

### Field reference

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `role` | string | *required* | Unique identifier for the role |
| `display_name` | string | *required* | Human-readable name shown in the dashboard |
| `prefix` | string | *required* | ID prefix for tasks created by this role (e.g., `"CD"` generates `CD-001`) |
| `emoji` | string | *required* | Emoji displayed in the dashboard UI |
| `color` | string | *required* | Hex color code for the dashboard UI |
| `system_prompt` | string | *required* | System prompt injected into every agent invocation |
| `tools` | list | `[]` | Tools the agent is allowed to use. Includes SDK tools (`Read`, `Write`, `Edit`, `Bash`, `Glob`, `Grep`) and MCP tools (`mcp__task-tools__create_task`, etc.) |
| `model` | string | `"claude-opus-4-6"` | Model to use for this role |
| `produces` | list | `[]` | Task types this role can produce as output |
| `accepts` | list | `[]` | Task types this role can be assigned |
| `routes_to` | list | `[]` | Downstream roles and their accepted task types |
| `routing_mode` | string | `"open"` | `"open"` injects a full agent manifest; `"restricted"` only shows `routes_to` targets |
| `can_create_groups` | boolean | `false` | Whether this role can create new task groups |
| `group_type` | string | `null` | Prefix for groups created by this role |
| `max_instances` | integer | `1` | Maximum concurrent agent instances for this role |
| `max_turns` | integer | `null` | Maximum SDK conversation turns per task |
| `max_execution_time` | integer | `1800` | Task timeout in seconds (default 30 minutes) |
| `auto_scale` | object | `null` | Per-role auto-scaling overrides |
| `context_includes` | list | `[]` | Additional context to inject into prompts |

### Routing mode

- **`open`**: The agent receives a manifest of all other roles (name, prefix,
  accepted task types). It can create tasks for any role.
- **`restricted`**: The agent only sees the roles listed in `routes_to`. It can
  only delegate to those specific roles and task types.

### Context includes

Available context providers that can be listed in `context_includes`:

| Provider | Description |
|----------|-------------|
| `parent_artifact` | Output from the parent task |
| `root_artifact` | Output from the root task in the chain |
| `sibling_summary` | Summary of completed/in-progress tasks in the same group |
| `rejection_history` | Prior rejection reasons for revision tasks |
| `agent_memory` | Recalled lessons from the agent's memory store |
| `git_history` | Recent git commits and changes |
| `coverage` | Code coverage data |
| `dependency_graph` | Module dependency information |
| `cross_task` | Context from related tasks |
| `ci_cd` | CI/CD pipeline status |
| `documentation` | Project documentation context |
| `issue_tracker` | Issue tracker data |
| `runtime` | Runtime context and metrics |

---

## Provider YAML

Provider YAML files in `config/providers/` define CLI agent providers. Two
built-in providers are included: `claude.yaml` and `gemini.yaml`.

### Example: claude.yaml

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

### Example: gemini.yaml

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

### Field reference

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Short identifier used in `cli_provider` settings |
| `display_name` | string | Human-readable provider name |
| `binary` | string | CLI binary name expected on PATH |
| `detect_models` | list | fnmatch patterns to auto-detect this provider from model names |
| `command_template` | object | CLI flag mappings for non-SDK providers |
| `output_parser` | string | How to parse CLI output (e.g., `"stream-json"`) |
| `system_prompt_mode` | string | How system prompts are delivered (e.g., `"xml-inject"`) |
| `models` | list | Available models with `id` and `tier` |

---

## MCP Server Configuration

MCP (Model Context Protocol) servers provide tools to agents. Two built-in
servers are always available:

- **`task-tools`**: Task CRUD operations (`create_task`, `list_tasks`, etc.)
- **`intelligence-tools`**: Intelligence and memory operations

### Adding custom MCP servers

Add entries under `mcp_servers` in `team.yaml`:

```yaml
mcp_servers:
  my-tool:
    command: "python"
    args: ["-m", "my_mcp_tool"]
    env:
      MY_API_KEY: "${MY_API_KEY}"
    transport: "stdio"
```

### MCP server fields

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `builtin` | boolean | `false` | Whether this is a built-in server (managed automatically) |
| `command` | string | `""` | Command to start the server |
| `args` | list | `[]` | Command-line arguments |
| `env` | map | `{}` | Environment variables passed to the server process |
| `transport` | string | `"stdio"` | Transport protocol |

### Environment variable interpolation

Values in the `env` map support `${VAR}` syntax to reference environment
variables at runtime:

```yaml
mcp_servers:
  github-tools:
    command: "npx"
    args: ["-y", "@modelcontextprotocol/server-github"]
    env:
      GITHUB_TOKEN: "${GITHUB_TOKEN}"
```

If `GITHUB_TOKEN` is set to `ghp_abc123` in the environment, the server
process will receive `GITHUB_TOKEN=ghp_abc123`.

### Referencing MCP tools in roles

To allow an agent to use a tool from an MCP server, add it to the role's
`tools` list using the `mcp__<server>__<tool>` naming convention:

```yaml
tools:
  - Read
  - Write
  - mcp__task-tools__create_task
  - mcp__github-tools__create_issue
```

---

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `TASKBREW_API_URL` | No | Override the dashboard API URL (default: `http://127.0.0.1:8420`) |
| `TASKBREW_DB_PATH` | No | Override the SQLite database path (default: `data/tasks.db`) |
| `LOG_LEVEL` | No | Logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |

Additional environment variables can be set for MCP servers via the `env`
field in their configuration (see above).
