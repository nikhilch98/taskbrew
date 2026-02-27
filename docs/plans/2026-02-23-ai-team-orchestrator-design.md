# AI Team Orchestrator - Design Document

**Date:** 2026-02-23
**Status:** Approved

## Problem Statement

Claude Code CLI requires interactive input for decisions during execution. We want to automate this by building an AI orchestration layer that:
1. Programmatically controls multiple Claude Code instances via the Claude Agent SDK
2. Creates AI teams (PM, Researcher, Architect, Coder, Tester, Reviewer)
3. Automates the full development lifecycle with real-time dashboard monitoring
4. Uses the local Claude Code Max subscription (no API keys needed)

## Key Constraint: Authentication

The Claude Agent SDK for Python (`claude-agent-sdk`) spawns Claude Code CLI as a subprocess. It inherits the CLI's own authentication. Since the local CLI is authenticated via `claude.ai` with a Max subscription (`authMethod: "claude.ai"`, `subscriptionType: "max"`), no `ANTHROPIC_API_KEY` is needed.

The SDK resolves the CLI binary automatically (checks `~/.local/bin/claude`, PATH, etc.) or can be pointed explicitly via `ClaudeAgentOptions(cli_path="/path/to/claude")`.

## Architecture

```
+----------------------------------------------------------+
|                  Web Dashboard (FastAPI)                   |
|  - Real-time agent status via WebSocket                   |
|  - Task board view (Kanban)                               |
|  - Agent logs/output streaming                            |
|  - Manual intervention controls (pause/resume/kill)       |
+----------------------------+-----------------------------+
                             | WebSocket + REST API
+----------------------------v-----------------------------+
|              Orchestrator (Python async core)              |
|                                                           |
|  +-------------+  +--------------+  +------------------+  |
|  | Team Manager |  | Task Queue   |  | Event Bus        |  |
|  | (spawn/stop  |  | (SQLite +    |  | (asyncio         |  |
|  |  agents)     |  |  in-memory)  |  |  pub/sub)        |  |
|  +------+------+  +------+-------+  +--------+---------+  |
|         |                |                    |            |
|  +------v----------------v--------------------v---------+  |
|  |              Workflow Engine                          |  |
|  |  - Defines pipelines (brainstorm->code->review)      |  |
|  |  - Routes tasks between agents                       |  |
|  |  - Handles retries, failures, escalation             |  |
|  +------------------------------------------------------+  |
+----------------------------+-----------------------------+
                             | claude-agent-sdk
         +-------------------+-------------------+
         |                   |                   |
    +----v----+        +-----v-----+       +-----v------+
    |Architect |       |  Coder    |       |  Reviewer  |
    | Agent    |       |  Agent    |       |  Agent     |
    |ClaudeSDK |       |ClaudeSDK  |       |ClaudeSDK   |
    |Client    |       |Client     |       |Client      |
    +----------+       +-----------+       +------------+
         |                   |                   |
         +-------------------+-------------------+
                             |
                     +-------v-------+
                     |   Git Repo    |
                     | (shared state)|
                     +---------------+
```

### Core Components

1. **Team Manager** - Spawns/stops `ClaudeSDKClient` instances. Each agent gets its own system prompt, allowed tools, hooks, and working directory.

2. **Task Queue (SQLite)** - Single source of truth for what needs to be done.
   - Schema: `id, pipeline_id, type, status, assigned_to, input_context, output_artifact, parent_task_id, created_at, started_at, completed_at`
   - Status flow: `pending -> assigned -> in_progress -> review -> completed | failed`

3. **Event Bus (asyncio pub/sub)** - Real-time notifications between orchestrator components. Agents emit events (task_started, file_changed, commit_made, review_requested). Dashboard and workflow engine subscribe.

4. **Workflow Engine** - Defines pipelines as directed graphs. Routes tasks between agents. Handles branching, retries, human checkpoints.

5. **Web Dashboard (FastAPI + WebSocket)** - Real-time monitoring: pipeline view, agent cards, task board, log stream, manual controls.

## Agent Definitions

| Agent | Role | Tools | Key Hooks |
|-------|------|-------|-----------|
| **PM** | Decomposes goals into tasks, prioritizes, tracks progress | Read, Glob, Grep, WebSearch | PostToolUse: log task changes |
| **Researcher** | Gathers context - docs, APIs, codebase analysis | Read, Glob, Grep, WebSearch, WebFetch | Stop: summarize findings |
| **Architect** | Designs solutions, writes technical plans | Read, Glob, Grep, Write | PostToolUse: emit plan_ready event |
| **Coder** | Implements code, commits to feature branches | Read, Write, Edit, Bash, Glob, Grep | PreToolUse: block force-push; PostToolUse: log file changes |
| **Tester** | Writes tests, runs test suites, validates correctness | Read, Write, Edit, Bash, Glob, Grep | PostToolUse: report test results |
| **Reviewer** | Reviews code quality, security, suggests improvements | Read, Glob, Grep | Stop: emit review_complete event |

Each agent is a `ClaudeSDKClient` instance with:
- Custom system prompt defining its role and responsibilities
- Restricted tool set appropriate for its role
- Lifecycle hooks for monitoring and control
- Its own git worktree (for parallel branch work)

## Inter-Agent Communication

Agents do NOT communicate directly. They communicate through shared state:

1. **Task Queue** - Agents pick up tasks, produce output, complete tasks. Orchestrator routes next task.
2. **Artifact Store (filesystem)** - Each agent produces artifacts consumed by downstream agents:
   - Researcher -> `artifacts/{task_id}/research.md`
   - Architect -> `artifacts/{task_id}/design.md`
   - Coder -> git branch with commits
   - Tester -> `artifacts/{task_id}/test_results.json`
   - Reviewer -> `artifacts/{task_id}/review.md`
3. **Git as coordination layer** - Each agent works on its own worktree/branch. Merging only after review.

## Workflow Pipelines

### Feature Development
```
PM (decompose goal)
  -> Researcher (gather context)
    -> Architect (design solution)
      -> Coder (implement on feature branch)
        -> Tester (write & run tests)
          -> Reviewer (review code)
            -> Coder (address feedback) <- loop if needed
              -> PM (mark complete, merge)
```

### Bug Fix
```
Researcher (reproduce & analyze)
  -> Coder (fix on bugfix branch)
    -> Tester (verify + regression)
      -> Reviewer (review fix)
        -> merge
```

### Code Review (standalone)
```
Reviewer (review existing PR)
  -> Coder (address feedback)
    -> Reviewer (re-review)
```

## Session Management

- Each `ClaudeSDKClient` session can be **resumed** (via `session_id`) for continuing paused work
- Sessions can be **forked** for exploration (e.g., architect explores two design options)
- Session IDs stored in task queue for traceability

## Dashboard Features

- **Pipeline view**: Visual DAG of current pipeline, active stage highlighted
- **Agent cards**: Status (idle/working/blocked), current task, output snippets
- **Task board**: Kanban columns (pending/in-progress/review/done)
- **Log stream**: Real-time scrolling log of tool calls, file edits, git ops
- **Controls**: Pause/resume agents, cancel tasks, assign manually, inject instructions

## Project Structure

```
ai-team/
  pyproject.toml
  src/
    taskbrew/
      __init__.py
      main.py                 # Entry point
      config.py               # Agent configs, pipeline defs, settings
      orchestrator/
        __init__.py
        team_manager.py       # Spawn/stop ClaudeSDKClient instances
        task_queue.py         # SQLite-backed task queue
        event_bus.py          # asyncio pub/sub
        workflow.py           # Pipeline engine (DAG execution)
      agents/
        __init__.py
        base.py               # Base agent config
        pm.py
        researcher.py
        architect.py
        coder.py
        tester.py
        reviewer.py
      tools/
        __init__.py
        task_tools.py         # claim_task, complete_task, create_subtask
        git_tools.py          # create_branch, create_pr, merge_branch
      dashboard/
        __init__.py
        app.py                # FastAPI + WebSocket
        static/               # Frontend
        templates/
  artifacts/
  pipelines/                  # Pipeline YAML definitions
  docs/plans/
```

## Tech Stack

- **Python 3.10+**
- **claude-agent-sdk** - Agent spawning and control
- **FastAPI + uvicorn** - Dashboard backend
- **WebSocket** - Real-time updates
- **SQLAlchemy + SQLite** - Task queue persistence
- **Jinja2** - Dashboard templates
- **asyncio** - Async orchestration
- **Git** - Version control and inter-agent coordination

## Key Design Decisions

1. **SDK over raw CLI** - The Claude Agent SDK handles JSON streaming, process lifecycle, hooks, and session management. Building on it avoids reimplementing these.

2. **Multi-process over subagents** - True parallelism. Each agent is an independent Claude Code process. Subagents (SDK's built-in pattern) run sequentially within one process.

3. **SQLite over Redis/Postgres** - Simple, zero-dependency persistence. Sufficient for single-machine orchestration. Can upgrade later if needed.

4. **Filesystem artifacts over database blobs** - Agents naturally work with files. Git provides versioning. Simpler than storing in DB.

5. **Event bus over direct messaging** - Decouples agents. Orchestrator controls routing. Easier to add new agent types without changing existing ones.

6. **Git worktrees for isolation** - Each agent gets its own worktree so they can work on different branches simultaneously without file conflicts.
