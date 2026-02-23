# Extending taskbrew

This guide covers how to add new roles, MCP tool servers, CLI providers, and
plugins to customize taskbrew for your workflow.

## Adding a New Role

Roles are defined as YAML files in `config/roles/`. To add a new role, create
a new file and restart the server.

### Step-by-step

1. **Create the YAML file** in `config/roles/`:

```yaml
# config/roles/security-reviewer.yaml
role: security_reviewer
display_name: "Security Reviewer"
prefix: "SR"
emoji: "\U0001F512"
color: "#ef4444"

system_prompt: |
  You are a Security Reviewer on an AI development team.
  Your responsibilities:
  1. Review code changes for security vulnerabilities
  2. Check for common issues: injection, auth bypass, data exposure
  3. Verify that secrets are not committed to the repository
  4. Either approve or create revision tasks for issues found

tools:
  - Read
  - Glob
  - Grep
  - Bash
  - mcp__task-tools__create_task
  - mcp__task-tools__list_tasks

model: claude-sonnet-4-6

produces: [security_review, approval, rejection]
accepts: [security_review]

routes_to:
  - role: coder
    task_types: [revision, bug_fix]

routing_mode: restricted

max_instances: 1
max_turns: 40
max_execution_time: 1200

context_includes:
  - parent_artifact
  - sibling_summary
```

2. **Choose the right fields**:
   - `prefix`: Must be unique across all roles. Used to generate task IDs
     (e.g., `SR-001`).
   - `routing_mode`: Use `"restricted"` if the role should only delegate to
     specific roles via `routes_to`. Use `"open"` if it needs to see all
     available agents.
   - `tools`: Only grant the tools the role needs. Roles that should not
     modify code should omit `Write`, `Edit`, and `Bash`.
   - `produces` / `accepts`: Define the task type contract. A role can only
     be assigned tasks whose type appears in its `accepts` list.

3. **Restart the server**: `taskbrew start`

The new role's agent loop will start automatically. Tasks assigned to
`security_reviewer` will be picked up by the new agent.

### Adding auto-scaling

For roles that may have variable workloads, enable auto-scaling:

```yaml
max_instances: 1       # Minimum (always running)
auto_scale:
  enabled: true
  scale_up_threshold: 3   # Spawn new instance after 3 pending tasks
  scale_down_idle: 15      # Remove extra instances after 15 idle minutes
```

The auto-scaler monitors pending task counts and adjusts instances
dynamically. It never scales below `max_instances`.

---

## Adding an MCP Tool Server

MCP (Model Context Protocol) servers expose tools that agents can call during
task execution. You can add any MCP-compatible server.

### In team.yaml

Add the server definition under `mcp_servers`:

```yaml
mcp_servers:
  github-tools:
    command: "npx"
    args: ["-y", "@modelcontextprotocol/server-github"]
    env:
      GITHUB_TOKEN: "${GITHUB_TOKEN}"

  postgres-tools:
    command: "python"
    args: ["-m", "mcp_postgres", "--connection-string", "${DATABASE_URL}"]
    env:
      DATABASE_URL: "${DATABASE_URL}"
```

### Grant tools to roles

Add the MCP tool names to the role's `tools` list:

```yaml
# In config/roles/coder.yaml
tools:
  - Read
  - Write
  - Edit
  - Bash
  - mcp__task-tools__create_task
  - mcp__github-tools__create_pull_request
  - mcp__postgres-tools__query
```

The naming convention is `mcp__<server-name>__<tool-name>`.

### Environment variable interpolation

Use `${VAR}` syntax in `env` values. At startup, these are replaced with
values from `os.environ`:

```yaml
env:
  API_KEY: "${MY_SERVICE_API_KEY}"    # Reads MY_SERVICE_API_KEY from environment
  STATIC_VALUE: "hardcoded"           # Not interpolated
```

If the environment variable is not set, the `${VAR}` placeholder is left
as-is, which typically causes the MCP server to fail with a clear error.

### Built-in MCP servers

Two servers are always registered automatically:

| Server | Module | Purpose |
|--------|--------|---------|
| `task-tools` | `taskbrew.tools.task_tools` | Task CRUD: `create_task`, `list_tasks`, `complete_task`, `update_task` |
| `intelligence-tools` | `taskbrew.tools.intelligence_tools` | Memory, quality, and collaboration tools |

These run as stdio subprocesses using the same Python interpreter as the
main process. You do not need to declare them in `mcp_servers`.

---

## Adding a New CLI Provider

taskbrew supports multiple CLI agent backends. There are two ways to add a
new provider: YAML-only for simple cases, or a Python plugin for full control.

### YAML-only provider

Create a file in `config/providers/`:

```yaml
# config/providers/codex.yaml
name: codex
display_name: "Codex CLI"
binary: codex
detect_models: ["codex-*", "gpt-4o-*"]
command_template:
  prompt_flag: "--prompt"
  model_flag: "--model"
  output_format_flag: "--format"
  output_format_value: "json"
models:
  - id: "codex-mini"
    tier: fast
  - id: "gpt-4o"
    tier: flagship
```

The `detect_models` patterns use fnmatch-style matching. When a role's
`model` field matches one of these patterns, taskbrew automatically selects
this provider.

### Python ProviderPlugin

For providers that need custom SDK integration, subclass `ProviderPlugin`
from `taskbrew.agents.provider_base`:

```python
# plugins/ollama_provider.py
from taskbrew.agents.provider_base import (
    ProviderPlugin,
    AssistantMessage,
    ResultMessage,
    TextBlock,
)


class OllamaProvider(ProviderPlugin):
    name = "ollama"
    detect_patterns = ["ollama-*", "llama-*"]

    def build_options(self, **kwargs):
        """Build options for the Ollama API."""
        return {
            "model": kwargs.get("model", "llama3"),
            "system_prompt": kwargs.get("system_prompt", ""),
            "temperature": 0.7,
        }

    async def query(self, prompt, options):
        """Run a query against the local Ollama server."""
        import httpx

        async with httpx.AsyncClient() as client:
            response = await client.post(
                "http://localhost:11434/api/generate",
                json={
                    "model": options["model"],
                    "system": options["system_prompt"],
                    "prompt": prompt,
                    "stream": False,
                },
            )
            data = response.json()

        yield AssistantMessage(content=[TextBlock(text=data["response"])])
        yield ResultMessage(result=data["response"])
```

The `ProviderPlugin` abstract base class requires two methods:
- `build_options(**kwargs) -> Any` -- build provider-specific options
- `async query(prompt, options) -> AsyncIterator` -- yield `AssistantMessage`
  and `ResultMessage` objects

The base class also provides `get_message_types()` which returns the standard
message dataclasses for isinstance checks.

### Using the provider

Set `cli_provider` in `team.yaml` or use the provider's model name in a
role's `model` field:

```yaml
# team.yaml
cli_provider: "ollama"

# Or in a role YAML
model: "ollama-llama3"   # Auto-detected via detect_patterns
```

---

## Writing a Plugin

Plugins extend taskbrew with custom hooks, API routes, and tools. They live
in the `plugins/` directory at the project root.

### Plugin structure

A plugin is a Python file (or package) with a `register(registry)` function:

```python
# plugins/my_plugin.py
from taskbrew.plugin_system import PluginMetadata


def register(registry):
    """Called by the plugin loader at startup."""
    # 1. Register metadata
    plugin = registry.register_plugin(PluginMetadata(
        name="my-plugin",
        version="1.0.0",
        description="Sends Slack notifications on task completion",
        author="Your Name",
    ))

    # 2. Register hooks
    registry.register_hook(
        "task.completed",
        on_task_completed,
        plugin_name="my-plugin",
    )
    registry.register_hook(
        "task.failed",
        on_task_failed,
        plugin_name="my-plugin",
    )


async def on_task_completed(data):
    """Called when any task completes."""
    task_id = data.get("task_id")
    agent_id = data.get("agent_id")
    # Send notification, update external system, etc.
    print(f"Task {task_id} completed by {agent_id}")


async def on_task_failed(data):
    """Called when a task fails."""
    task_id = data.get("task_id")
    error = data.get("error", "unknown")
    print(f"Task {task_id} failed: {error}")
```

### Plugin lifecycle

1. At startup, `build_orchestrator()` scans the `plugins/` directory
2. Each `.py` file (or package with `__init__.py`) is loaded
3. The `register(registry)` function is called with a `PluginRegistry` instance
4. Hooks are invoked whenever matching events fire through the event bus

### Plugin API

The `PluginRegistry` provides these methods:

| Method | Description |
|--------|-------------|
| `register_plugin(metadata)` | Register the plugin and return a `Plugin` handle |
| `register_hook(hook_name, callback, plugin_name)` | Subscribe to an event |
| `fire_hook(hook_name, data)` | Manually fire a hook (async) |
| `get_all_routes()` | Get API routes from all enabled plugins |
| `get_all_tools()` | Get tools from all enabled plugins |
| `enable_plugin(name)` | Enable a disabled plugin |
| `disable_plugin(name)` | Disable a plugin without unregistering |
| `get_plugin_info()` | Get metadata for all registered plugins |

### Adding API routes

Plugins can register FastAPI routes that are mounted at startup:

```python
from fastapi import APIRouter

router = APIRouter(prefix="/api/my-plugin", tags=["my-plugin"])

@router.get("/status")
async def plugin_status():
    return {"status": "ok", "version": "1.0.0"}

def register(registry):
    plugin = registry.register_plugin(PluginMetadata(
        name="my-plugin",
        version="1.0.0",
    ))
    plugin.routes.append(router)
```

---

## The Event Bus

The event bus provides decoupled pub/sub communication between all components.
It is the backbone for plugins, intelligence managers, and the dashboard's
real-time updates.

### Subscribing to events

```python
async def my_handler(event: dict):
    print(f"Got event: {event['type']}")

# Subscribe to a specific event
event_bus.subscribe("task.completed", my_handler)

# Subscribe to ALL events (wildcard)
event_bus.subscribe("*", my_handler)

# Unsubscribe
event_bus.unsubscribe("task.completed", my_handler)
```

### Available events

Events emitted by the core system:

| Event | Data fields | Emitted when |
|-------|-------------|--------------|
| `task.claimed` | `task_id`, `claimed_by`, `model`, `correlation_id` | An agent claims a task |
| `task.completed` | `task_id`, `group_id`, `agent_id`, `model` | A task finishes successfully |
| `task.failed` | `task_id`, `instance_id`, `error`, `model`, `correlation_id` | A task fails or times out |
| `task.recovered` | `task_id` | An orphaned task is reset to pending |
| `agent.status_changed` | `instance_id`, `status`, `role`, `model` | Agent transitions state |
| `agent.stopped` | `instance_id`, `model` | Agent loop exits |
| `agent.result` | `agent_name`, `result`, `model` | Agent produces final output |
| `agent.text` | `agent_name`, `text`, `model` | Agent produces intermediate text |
| `agent.message` | `from`, `to`, `content` | Direct message between agents |
| `tool.pre_use` | `agent_name`, `tool_name`, `tool_input`, `model` | Before a tool is invoked |
| `tool.post_use` | `agent_name`, `tool_name`, `model` | After a tool completes |

### Event history

The event bus retains the last 10,000 events in memory:

```python
# Get all events
all_events = event_bus.get_history()

# Filter by type
completions = event_bus.get_history("task.completed")
```

### Emitting custom events

Plugins and extensions can emit their own events:

```python
await event_bus.emit("my_plugin.alert", {
    "severity": "warning",
    "message": "Cost threshold exceeded",
})
```

Other plugins can subscribe to these custom events using the same
`subscribe()` mechanism.
