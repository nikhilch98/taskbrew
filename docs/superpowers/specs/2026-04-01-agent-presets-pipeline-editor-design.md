# Agent Presets, Editable Pipeline & Human-in-the-Loop Design

**Date:** 2026-04-01
**Status:** Approved
**Scope:** Redesign of the "Add New Agent" flow, editable pipeline editor, approval/clarification system, and hybrid routing engine.

---

## 1. Agent Preset System

### 1.1 "Add New Agent" Modal — Two Paths

When the user clicks "Add New Agent", a modal opens with two tabs:

**Tab 1: "Presets" (default)**

- Grid of preset agent cards showing: icon, name, short description.
- Click a preset to expand a **read-only detail view** showing:
  - System prompt (non-editable)
  - Tools this agent uses
  - `approval_mode` default
  - `max_revision_cycles` default
- Click "Select" to advance to **Step 2: Model Picker**.
  - Dropdown to pick AI model (Claude Opus 4.6, Sonnet 4.6, Haiku 4.5, Gemini Pro, Gemini Flash).
  - Click "Create" to add the agent to the Agent Roles list.

**Tab 2: "Custom"**

- The current full wizard minus the routing step (Identity + Config only).
- Maximum configurability: display name, role ID, prefix, color, emoji, model, system prompt, tools, `approval_mode`, `max_revision_cycles`.
- No routing/connections step. Routing is handled exclusively in the Pipeline editor.

### 1.2 Preset Agent Catalog (22 agents)

#### Planning

| Template | Description | approval_mode | max_revision_cycles |
|----------|-------------|---------------|---------------------|
| PM | Decomposes user prompts into exhaustive task lists covering Infra, BE, FE, Testing, Research, UI, UX, etc. | auto | — |

#### Architecture

| Template | Description | approval_mode | max_revision_cycles |
|----------|-------------|---------------|---------------------|
| Architect | Creates HLD/LLD with extensive implementation details, methods, functions, and logic | first_run | — |

#### Review

| Template | Description | approval_mode | max_revision_cycles |
|----------|-------------|---------------|---------------------|
| Architect Reviewer | Reviews code changes, approves or rejects with specific feedback | auto | 5 |

#### Coding (6 variants)

| Template | Description | approval_mode | max_revision_cycles |
|----------|-------------|---------------|---------------------|
| Coder BE | Backend implementation (APIs, services, databases) | auto | 5 |
| Coder FE | Frontend implementation (React, Vue, vanilla JS) | auto | 5 |
| Coder UI/UX Web | Web UI/UX implementation (CSS, layouts, responsive design) | auto | 5 |
| Coder Swift | iOS native implementation (Swift, UIKit, SwiftUI) | auto | 5 |
| Coder Flutter | Flutter cross-platform implementation | auto | 5 |
| Coder Infra | Infrastructure code (Docker, Terraform, CI/CD configs) | auto | 5 |

#### Design (4 variants)

| Template | Description | approval_mode | max_revision_cycles |
|----------|-------------|---------------|---------------------|
| Designer Web | Web mockups and design assets | manual | 5 |
| Designer iOS Swift | iOS native design mockups | manual | 5 |
| Designer Flutter iOS | Flutter iOS design mockups | manual | 5 |
| Designer Flutter iOS+Android | Flutter cross-platform design mockups | manual | 5 |

#### Testing (3 variants)

| Template | Description | approval_mode | max_revision_cycles |
|----------|-------------|---------------|---------------------|
| QA Tester Unit | Unit test writing and execution | auto | — |
| QA Tester Integration | Integration test writing and execution | auto | — |
| QA Tester E2E | End-to-end test writing and execution | auto | — |

#### Security

| Template | Description | approval_mode | max_revision_cycles |
|----------|-------------|---------------|---------------------|
| Security Auditor | OWASP scans, dependency audits, secrets detection | first_run | — |

#### Operations

| Template | Description | approval_mode | max_revision_cycles |
|----------|-------------|---------------|---------------------|
| DevOps Engineer | CI/CD pipelines, deployment configs, IaC | auto | 5 |
| Database Architect | Schema design, migrations, query optimization | first_run | — |

#### Documentation

| Template | Description | approval_mode | max_revision_cycles |
|----------|-------------|---------------|---------------------|
| Technical Writer | API docs, READMEs, changelogs | auto | — |

#### Research

| Template | Description | approval_mode | max_revision_cycles |
|----------|-------------|---------------|---------------------|
| Research Agent | Investigates libraries, APIs, feasibility; produces research reports | auto | — |

#### API

| Template | Description | approval_mode | max_revision_cycles |
|----------|-------------|---------------|---------------------|
| API Designer | OpenAPI specs, contract-first design, endpoint validation | first_run | — |

### 1.3 Preset Data Structure

Each preset is a YAML file stored in `config/presets/` with the full agent config. The system prompt, tools, and defaults are pre-written and read-only during selection. Only the `model` field is user-chosen at creation time.

```yaml
# config/presets/coder_be.yaml
preset_id: coder_be
category: coding
display_name: "Coder BE"
description: "Backend implementation — APIs, services, databases"
icon_emoji: "\U0001F4BB"
color: "#f59e0b"
prefix: "CB"
approval_mode: auto
max_revision_cycles: 5

system_prompt: |
  You are a Backend Engineer agent...
  (full system prompt here)

tools:
  - Read
  - Write
  - Edit
  - Bash
  - Glob
  - Grep

default_model: claude-sonnet-4-6
produces: [implementation, bug_fix, revision]
accepts: [implementation, bug_fix, revision]
can_create_groups: false
max_instances: 3
max_turns: 80
max_execution_time: 1800
context_includes:
  - parent_artifact
  - root_artifact
  - sibling_summary
  - rejection_history
```

### 1.4 What Changes in the Agent Card (Agent Roles section)

- **Remove** the "Routing" accordion from each agent card entirely.
- All routing is done in the Pipeline editor.
- Keep: Identity, Model & Execution, Tools, System Prompt, Advanced.

---

## 2. Editable Pipeline

### 2.1 Renaming

"Pipeline Visualizer" is renamed to **"Pipeline"**.

### 2.2 Interactions

**Adding connections:**
1. Click an agent node — enters "source selected" state (highlighted border).
2. Click a second agent node — arrow drawn from source to target.
3. Arrow uses the source agent's color gradient. Click empty space to cancel selection.

**Removing connections:**
1. Hover an arrow — it highlights and shows a small "x" button at the midpoint.
2. Click "x" — arrow is removed.

**Start Agent:**
- One agent is the "start agent" — receives the user's initial prompt.
- Shown with a distinctive play/star badge on the node.
- Right-click any agent node — context menu with "Set as Start Agent".
- Only one start agent at a time. Setting a new one removes the old.

**Per-edge configuration:**
- Click an arrow — small popover with:
  - **Join strategy:** `wait_all` (default) or `stream`
  - **On failure:** `block` (default), `continue_partial`, or `cancel_pipeline`
  - **Task types:** which task types this edge carries (e.g., `implementation`, `verification`)

**Node layout:**
- Auto-layout using the existing topological sort algorithm.
- Unconnected agents float on the right with a dashed border.

### 2.3 Pipeline Validation (real-time)

| Condition | Indicator |
|-----------|-----------|
| No start agent marked | Warning banner at top of pipeline section |
| Disconnected agents (no edges, not start) | Warning badge on the node (dashed border) |
| Revision loops without a `max_revision_cycles` cap | Warning on the cycle edges |
| Pipeline running | "Pipeline running" overlay, editing disabled |

### 2.4 Pipeline Data Model

Edges are stored in a `pipeline_edges` table (or in team.yaml):

```yaml
pipeline:
  start_agent: pm
  edges:
    - from: pm
      to: architect
      task_types: [tech_design]
      join_strategy: wait_all
      on_failure: block
    - from: architect
      to: coder_be
      task_types: [implementation]
      join_strategy: wait_all
      on_failure: block
    - from: coder_be
      to: architect_reviewer
      task_types: [verification]
      join_strategy: stream
      on_failure: block
    - from: architect_reviewer
      to: coder_be
      task_types: [revision]
      join_strategy: wait_all
      on_failure: block
```

This replaces the per-agent `routes_to` field. The pipeline is the single source of truth for all routing.

### 2.5 Pipeline Locking

- Pipeline topology is locked during active execution.
- User sees a "Pipeline running" overlay — cannot edit edges or start agent.
- Must cancel active runs to edit.

---

## 3. Approval, Clarification & Human-in-the-Loop

### 3.1 approval_mode (per agent)

| Mode | Behavior |
|------|----------|
| `auto` | Agent completes tasks autonomously, no human gate |
| `manual` | Every task completion goes to `awaiting_approval` — user must approve/reject via dashboard |
| `first_run` | First task requires approval; if approved, subsequent tasks auto-complete. Resets per pipeline run |

### 3.2 Blocking MCP Tool Pattern

Both approval and clarification use the same mechanism — a blocking MCP tool call.

**MCP tools provided to agents:**

| Tool | Purpose | Blocks until |
|------|---------|-------------|
| `submit_for_approval(artifact_paths[], summary)` | Submit work for human review | User approves or rejects |
| `request_clarification(question, context, suggested_options[])` | Ask user a question | User responds |
| `route_task(target_agent, task_type, title, description, priority, blocked_by[])` | Create a downstream task | Does not block (fire-and-forget) |
| `get_my_connections()` | Get pipeline connections for this agent | Does not block |
| `complete_task()` | Mark current task as done | Does not block (or triggers approval if mode != auto) |

**Server-side blocking flow:**
1. Agent calls MCP tool (e.g., `submit_for_approval`) — HTTP POST to TaskBrew server.
2. Server stores the request, sets task status to `awaiting_approval` or `awaiting_clarification`.
3. WebSocket event emitted — dashboard shows notification card.
4. MCP tool polls server every 2 seconds waiting for response (timeout: 24 hours).
5. User responds via dashboard — server stores response.
6. Next poll picks up response — MCP tool returns it to the agent.
7. Agent resumes with the response in its conversation context.

### 3.3 Dashboard Notification Cards

**Approval card:**
- Shows agent name, task summary, artifact links
- "View Artifact" button opens the artifact viewer
- Optional feedback text field
- "Approve" and "Reject" buttons
- Reject sends feedback back to the agent

**Clarification card:**
- Shows agent name, question, context reference
- Free-text response field
- Optional quick-pick buttons from `suggested_options[]`
- "Respond" button sends the answer back

### 3.4 Artifact Viewer

Generic viewer that renders based on content type:
- Markdown/text: rendered inline
- Images (mockups): image viewer with zoom
- Code diffs: syntax-highlighted diff view
- HTML: sandboxed iframe preview

Available for any agent that produces artifacts, not just Designer.

### 3.5 Revision Loop

- Each task tracks a `revision_count`.
- On rejection, count increments, task routes back to the originating agent with feedback.
- At `max_revision_cycles` (default 5), task status becomes `awaiting_human_intervention` instead of looping back.
- Dashboard shows escalation card with full revision history.

---

## 4. Hybrid Routing Engine

### 4.1 System Prompt Injection

At agent launch, the orchestrator dynamically injects connection info into the system prompt:

```
You can route tasks to these connected agents:
- Architect (accepts: tech_design, architecture_review)
- Coder BE (accepts: implementation, bug_fix)
- Coder FE (accepts: implementation, bug_fix)

Use the `route_task` tool to send work to connected agents.
Use the `request_clarification` tool if you need human input.
Use the `submit_for_approval` tool when your work is ready for review.
Do NOT attempt to route to agents not listed above.
```

The injection is generated from the pipeline edges at launch time. If the pipeline is edited, the next agent launch picks up the new connections automatically.

### 4.2 MCP Tool Enforcement

`route_task` validates at runtime:
- Target agent exists in the pipeline
- An edge exists from the calling agent to the target agent
- The task type is allowed on that edge
- Rejects with an error if any validation fails

This ensures agents cannot hallucinate connections that don't exist in the pipeline topology.

### 4.3 Context Passing Between Agents

**Artifact store (large outputs):**
- Stored in `artifacts/{group_id}/{task_id}/`
- Referenced in the downstream agent's system prompt
- Used for: design docs, mockups, test reports, research findings

**Task description (summary context):**
- Creating agent puts essential context in the task description
- Downstream agent receives it as part of its task assignment
- Used for: requirements, acceptance criteria, specific instructions

**Context includes (configurable per agent template):**
- `parent_artifact` — output from the agent that created this task
- `root_artifact` — the original PM decomposition document
- `sibling_summary` — summaries of parallel tasks in the same group
- `rejection_history` — previous rejection feedback for revision loops

---

## 5. Execution Policies

### 5.1 Task Timeout

- Default: 30 minutes per task, configurable per agent template via `max_execution_time`.
- On timeout: task status becomes `timed_out`, follows the edge's `on_failure` policy.

### 5.2 Crash Retry

- Agent process dies — auto-retry up to 3 times with exponential backoff (5s, 30s, 120s).
- Conversation checkpoint saved so retry resumes from last state, not from scratch.
- 3rd failure — task status becomes `failed`, human notification sent.

### 5.3 API Concurrency

- Orchestrator-level semaphore: max concurrent API calls (default 5, configurable in `team.yaml`).
- Agents queue for a slot before making API calls.
- Prevents 429 rate limit errors.

### 5.4 Cancellation Cascade

- User cancels a task — all downstream tasks in the same group auto-cancel.
- Pipeline execution stops for that group.
- Other pipeline runs are unaffected.

### 5.5 Agent Config Versioning

- In-flight tasks use the config that was active when they started.
- New tasks pick up the latest config.
- No hot-swap mid-execution.

### 5.6 Git Worktrees

- Each agent instance gets its own git worktree and branch.
- Branch naming: `task/{group_id}-{task_id}-{short_description}`
- On task completion: merge to `main` (fast-forward if clean).
- On merge conflict: task status becomes `awaiting_human_intervention` with a diff viewer in the dashboard.

### 5.7 Fan-out / Fan-in

**Join strategies (per incoming edge):**

| Strategy | Behavior |
|----------|----------|
| `wait_all` (default) | Agent starts only when ALL upstream tasks targeting it are complete |
| `stream` | Each upstream task completion independently triggers a new task for this agent |

**On upstream failure (per incoming edge):**

| Policy | Behavior |
|--------|----------|
| `block` (default) | Wait indefinitely until human intervenes |
| `continue_partial` | Proceed with completed tasks, note the gap |
| `cancel_pipeline` | Abort the entire pipeline run |

**Priority inheritance:**
- Child tasks inherit the priority of the parent task that created them.
- Agents process higher-priority tasks first from their queue.

---

## 6. Migration from Current System

### What Changes

| Current | New |
|---------|-----|
| `routes_to` field in each agent YAML | Pipeline edges in `team.yaml` (single source of truth) |
| 3-step wizard (Identity → Config → Pipeline) | 2-tab modal (Presets / Custom), no routing step |
| "Pipeline Visualizer" (read-only) | "Pipeline" (fully editable) |
| No approval system | `approval_mode` flag + blocking MCP tools + dashboard cards |
| No clarification mechanism | `request_clarification` MCP tool + dashboard cards |
| Agent Routing accordion in agent cards | Removed — routing is pipeline-only |

### What Stays the Same

- Agent YAML files in `config/roles/` for custom agents
- Existing API endpoints for agent CRUD (`/api/settings/roles`)
- Task board, task dependencies, group system
- Artifact store
- WorktreeManager (formalized for per-agent-instance pattern)
- Dashboard layout and styling (dark theme, existing CSS system)
