# Architecture Overview

This document describes the internal architecture of taskbrew: how components
connect, how tasks flow through the system, and how agents execute work.

## System Overview

```
                    +------------------+
                    |   Dashboard UI   |
                    |  (FastAPI + WS)  |
                    +--------+---------+
                             |
                    +--------+---------+
                    |   Orchestrator   |
                    |                  |
                    |  - TaskBoard     |
                    |  - EventBus      |
                    |  - Database      |
                    |  - ArtifactStore |
                    |  - PluginRegistry|
                    +--------+---------+
                             |
              +--------------+--------------+
              |              |              |
        +-----+----+  +-----+----+  +------+---+
        | AgentLoop |  | AgentLoop |  | AgentLoop|
        |  (PM-1)   |  | (CD-1)   |  | (VR-1)  |
        +-----+----+  +-----+----+  +------+---+
              |              |              |
        +-----+----+  +-----+----+  +------+---+
        |AgentRunner|  |AgentRunner|  |AgentRunner|
        |  (Claude) |  |  (Claude) |  | (Gemini) |
        +-----------+  +-----------+  +----------+
```

The **Orchestrator** (`main.py:Orchestrator`) is the central container that
holds all shared components. It is created by `build_orchestrator()` and
passed to the dashboard app and agent loops.

Key components:

- **TaskBoard**: CRUD operations for groups and tasks, dependency resolution,
  priority-based claiming.
- **EventBus**: Async pub/sub for decoupled inter-component communication.
- **Database**: SQLite via aiosqlite with schema auto-migration.
- **ArtifactStore**: File-based storage for task outputs.
- **InstanceManager**: Tracks agent instance status and heartbeats.
- **WorktreeManager**: Creates and cleans up isolated git worktrees.
- **PluginRegistry**: Loads and manages plugins from the `plugins/` directory.
- **Intelligence Managers**: Quality, collaboration, planning, security, and
  other intelligence modules that enhance agent behavior.

---

## Task Lifecycle

Tasks move through a state machine with the following transitions:

```
                    +----------+
          +-------->| blocked  |<--------+
          |         +----+-----+         |
          |              |               |
   (deps resolved)      |          (created with
          |              |           blocked_by)
          |              v               |
          |         +----+-----+         |
  +-------+-------->| pending  +---------+
  |                 +----+-----+
  |                      |
  |              (agent claims)
  |                      |
  |                      v
  |                +-----+------+
  |                | in_progress |
  |                +-----+------+
  |                      |
  |           +----------+----------+
  |           |          |          |
  |           v          v          v
  |     +-----------+ +------+ +--------+
  |     | completed | | failed| |rejected|
  |     +-----------+ +------+ +--------+
  |                      |          |
  |              (retry) |   (retry)|
  +----------------------+----------+
```

### States

| State | Description |
|-------|-------------|
| `pending` | Ready to be claimed by an agent |
| `blocked` | Waiting for dependencies (`blocked_by`) to complete |
| `in_progress` | Claimed by an agent and actively being executed |
| `completed` | Successfully finished; output stored |
| `failed` | Execution failed (timeout, error, or cascaded failure) |
| `rejected` | Explicitly rejected by a verifier with feedback |
| `cancelled` | Manually or programmatically cancelled |

### Dependency resolution

When a task completes, the TaskBoard:

1. Marks all `task_dependencies` rows referencing the completed task as
   `resolved = 1`
2. Finds blocked tasks with zero remaining unresolved dependencies
3. Transitions those tasks from `blocked` to `pending`

When a task fails, failure **cascades**: all tasks blocked by the failed task
are also marked as `failed` via BFS traversal.

### Group completion

After any task reaches a terminal state, the TaskBoard checks whether all
tasks in the group are terminal (`completed`, `failed`, or `cancelled`). If
so, the group is marked as `completed`.

---

## Agent Lifecycle

Each agent role has one or more `AgentLoop` instances running as asyncio
tasks.

```
  register instance
         |
         v
  +------+------+
  |    idle     |  <---+
  +------+------+      |
         |              |
   (poll_for_task)      |
         |              |
         v              |
  +------+------+      |
  |   working   |      |
  +------+------+      |
         |              |
   (execute_task)       |
         |              |
    +----+----+         |
    |         |         |
 success   failure      |
    |         |         |
    v         v         |
 complete  fail_task    |
    |         |         |
    +---------+---------+
         |
   (update status -> idle)
```

### Poll/Claim/Execute/Complete cycle

Each `AgentLoop.run_once()` performs one cycle:

1. **Poll**: Checks if the role is paused. If not, calls
   `TaskBoard.claim_task()` which atomically selects the highest-priority
   pending task assigned to this role and sets it to `in_progress`.

2. **Worktree setup**: If the role uses Bash tools, an isolated git worktree
   is created on a feature branch (`feat/<task-id>`).

3. **Context building**: `build_context()` assembles the prompt from:
   - Role identity and task metadata
   - Task description and parent artifacts
   - Revision context (if this is a re-do of a rejected task)
   - Sibling task summary (group progress)
   - Agent manifest (available roles for delegation)
   - Agent memory (past lessons)
   - Additional context providers (git history, coverage, etc.)
   - Learned conventions and error patterns

4. **Execute**: The built context is passed to `AgentRunner.run()`, which
   dispatches to the appropriate SDK (Claude or Gemini) via the provider
   abstraction layer. A heartbeat loop runs in the background to keep the
   instance alive.

5. **Complete or fail**: On success, `complete_and_handoff()` stores the
   output, resolves dependencies, and emits events. On failure, the task is
   marked as failed. Retries happen automatically (up to 3 attempts with
   exponential backoff) for transient errors.

6. **Cleanup**: The worktree is removed and the agent returns to idle.

### Resilience

- **Heartbeats**: Each agent sends a heartbeat every 15 seconds while working.
- **Orphan recovery**: On startup, all `in_progress` tasks are reset to
  `pending` since no agents from the previous session are still alive.
- **Stale instance detection**: A background loop detects agents whose
  heartbeats have expired and recovers their tasks.
- **Stuck blocked tasks**: A background loop finds blocked tasks whose
  dependencies are all in terminal states and resolves them.

---

## Event Bus

The `EventBus` provides async pub/sub for decoupled communication:

```python
# Any component can subscribe
event_bus.subscribe("task.completed", handler)

# Any component can emit
await event_bus.emit("task.completed", {"task_id": "CD-042", ...})
```

- Handlers are dispatched as fire-and-forget asyncio tasks
- Errors in handlers are caught and logged (never crash the emitter)
- A wildcard subscription (`"*"`) receives all events
- History of the last 10,000 events is retained in memory
- The dashboard uses the event bus to push real-time updates via WebSocket

---

## Provider Abstraction Layer

The provider layer allows taskbrew to work with different CLI agent backends
through a unified interface.

```
  AgentRunner
       |
       v
  detect_provider(model) --> "claude" | "gemini" | custom
       |
       v
  build_sdk_options(provider, ...) --> ClaudeAgentOptions | GeminiOptions
       |
       v
  sdk_query(prompt, options, provider) --> AsyncIterator[Message]
```

### Flow

1. **Detection**: `detect_provider()` examines the model name. Models
   starting with `claude-` route to the Claude SDK; models starting with
   `gemini-` route to the Gemini CLI wrapper.

2. **Options building**: `build_sdk_options()` creates provider-specific
   option objects. For Claude, this includes MCP server configs, allowed
   tools, and permission mode. For Gemini, it sets the system prompt and
   model.

3. **Query dispatch**: `sdk_query()` is an async generator that delegates to
   the correct SDK's `query()` function, yielding `AssistantMessage` and
   `ResultMessage` objects.

4. **Message types**: `get_message_types()` returns the correct dataclasses
   for isinstance checks, abstracting SDK-specific types.

### Custom providers

For providers beyond Claude and Gemini, subclass `ProviderPlugin` from
`taskbrew.agents.provider_base`. See [extending.md](extending.md) for a
detailed guide.

---

## Hybrid Routing

taskbrew supports two routing modes that control how agents discover and
delegate to other agents:

### Open routing

When `routing_mode: open`, the agent's context includes a full **agent
manifest** listing all other roles:

```
## Available Agents
You may create tasks for any of these agents:

- **Architect** (AR): assigned_to="architect", accepts: [tech_design, ...]
- **Coder** (CD): assigned_to="coder", accepts: [implementation, bug_fix, ...]
- **Verifier** (VR): assigned_to="verifier", accepts: [verification]
```

This allows the agent to dynamically choose which role to delegate to based
on the task at hand.

### Restricted routing

When `routing_mode: restricted`, the agent only sees the roles listed in its
`routes_to` configuration. This enforces a strict pipeline where each role
can only delegate to its designated downstream roles.

---

## Database Schema Overview

taskbrew uses SQLite (via aiosqlite) with the following core tables:

| Table | Purpose |
|-------|---------|
| `groups` | Task groups with status tracking |
| `tasks` | Individual tasks with full lifecycle state |
| `task_dependencies` | Directed dependency edges between tasks |
| `artifacts` | File references linked to tasks |
| `agent_instances` | Running agent status and heartbeats |
| `id_sequences` | Auto-incrementing ID counters per prefix |
| `events` | Persistent event log |
| `task_usage` | Token counts, costs, and timing per task |
| `approvals` | Approval workflow for gated tasks |
| `cost_budgets` | Budget tracking by scope and period |
| `notifications` | In-app notification queue |
| `webhooks` | Registered webhook endpoints |
| `webhook_deliveries` | Delivery attempts and status |
| `task_templates` | Reusable task templates |

### ID generation

Task and group IDs are generated with a prefix and auto-incrementing counter:
`PM-001`, `CD-042`, `FEAT-003`. Each prefix has its own counter in the
`id_sequences` table. The prefix comes from the role's `prefix` field (for
tasks) or the role's `group_type` (for groups).

### Transactions

Critical operations like `claim_task` use explicit transactions with
`SELECT ... FOR UPDATE`-style locking (via aiosqlite's transaction context
manager) to prevent race conditions when multiple agents poll simultaneously.
