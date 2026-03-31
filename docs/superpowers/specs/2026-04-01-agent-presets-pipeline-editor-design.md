# Agent Presets, Editable Pipeline & Human-in-the-Loop Design

**Date:** 2026-04-01
**Status:** Approved (rev 2 — post 10-pass review)
**Scope:** Redesign of the "Add New Agent" flow, editable pipeline editor, approval/clarification system, and hybrid routing engine.

---

## 1. Agent Preset System

### 1.1 "Add New Agent" Modal — Two Paths

When the user clicks "Add New Agent", a modal opens with two tabs:

**Tab 1: "Presets" (default)**

- Grid of preset agent cards organized by category tabs (Planning, Architecture, Coding, Design, Testing, Security, Ops, Docs, Research, API). A search/filter bar is also available for quick lookup across all 22 presets.
- Click a preset to expand a **read-only detail view** showing:
  - System prompt (non-editable)
  - Tools this agent uses
  - `approval_mode` default
  - `max_revision_cycles` default
- Click "Select" to advance to **Step 2: Model Picker**.
  - Dropdown to pick AI model, pre-selected to the preset's `default_model`.
  - Available models: Claude Opus 4.6, Sonnet 4.6, Haiku 4.5, Gemini Pro, Gemini Flash.
  - Click "Create" to add the agent to the Agent Roles list.
- **Duplicate handling:** If the user creates a second instance of the same preset (e.g., two "Coder BE"), the role_id is auto-suffixed: `coder_be`, `coder_be_2`, `coder_be_3`. Display name is suffixed similarly: "Coder BE", "Coder BE 2".

**Tab 2: "Custom"**

- The current full wizard minus the routing step (Identity + Config only).
- Maximum configurability: display name, role ID, prefix, color, emoji, model, system prompt, tools, `approval_mode` (dropdown: auto/manual/first_run), `max_revision_cycles` (number input, 0 = unlimited), `max_clarification_requests` (number input, default 10).
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
max_clarification_requests: 10

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

# produces/accepts are for pipeline edge validation only, NOT for routing.
# Routing is defined exclusively in the pipeline edges.
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

**Note on `produces`/`accepts`:** These fields are used for **validation only** — when the user creates a pipeline edge, the system checks that the edge's `task_types` are a subset of the source's `produces` AND the target's `accepts`. They do not control routing. Routing is defined exclusively in the pipeline topology.

### 1.4 Preset Versioning

- Presets ship with the application and may be updated in future releases.
- Agents created from a preset are **snapshots** — they copy the preset config at creation time.
- Future preset updates do NOT retroactively change existing agents.
- Users can manually update an existing agent's config if they want to adopt new preset changes.

### 1.5 What Changes in the Agent Card (Agent Roles section)

- **Remove** the "Routing" accordion from each agent card entirely.
- All routing is done in the Pipeline editor.
- **Add** `approval_mode` and `max_revision_cycles` fields to the "Advanced" accordion.
- Keep: Identity, Model & Execution, Tools, System Prompt, Advanced.

### 1.6 New Agent Appearance in Pipeline

- When a new agent is created (preset or custom), it auto-appears in the Pipeline section as an **unconnected node** (dashed border, floating on the right).
- The user must manually draw edges to connect it.

---

## 2. Editable Pipeline

### 2.1 Renaming

"Pipeline Visualizer" is renamed to **"Pipeline"**.

### 2.2 Pipeline Identity

Each pipeline has:
- `id`: auto-generated UUID
- `name`: defaults to "Default Pipeline", user-editable
- `start_agent`: role_id of the agent that receives the user's initial prompt

For now, **one pipeline per project**. The data model includes an `id` field to support multiple pipelines per project in the future without migration.

### 2.3 Interactions

**Adding connections:**
1. Click an agent node — enters "source selected" state (highlighted border, cursor changes).
2. Click a second agent node — arrow drawn from source to target.
3. Arrow uses the source agent's color gradient. Click empty space to cancel selection.
4. **Validation on draw:** If the edge's implied task types don't match (source doesn't `produce` anything the target `accepts`), show a warning but still allow the edge (user may configure task_types manually).

**Removing connections:**
1. Hover an arrow — it highlights and shows a small "x" button at the midpoint.
2. Click "x" — edge is removed. If pipeline is saved and there are in-flight tasks using this edge, show a confirmation warning.

**Self-loops:**
- An agent can route to itself (e.g., Architect Reviewer re-reviews after minor self-fixes).
- Self-loops are rendered as a curved arc below the node (already supported in the current SVG renderer).

**Start Agent:**
- One agent is the "start agent" — receives the user's initial prompt.
- Shown with a distinctive play/star badge on the node.
- Right-click (desktop) or long-press (touch) any agent node — context menu with "Set as Start Agent".
- Only one start agent at a time. Setting a new one removes the old.
- Start agents should not have incoming edges from other agents (only from the user). Show a warning if they do.

**Per-edge configuration:**
- Click an arrow — small popover with:
  - **Task types:** which task types this edge carries (e.g., `implementation`, `verification`). Validated against source `produces` and target `accepts`.
  - **On failure:** `block` (default), `continue_partial`, or `cancel_pipeline`. See section 2.6 for scoping.

**Per-node configuration (receiving side):**
- Click an agent node — node settings popover with:
  - **Join strategy:** `wait_all` (default) or `stream`. Applies to ALL incoming edges for this node (not per-edge, since conflicting per-edge join strategies are nonsensical).

**Node layout:**
- Auto-layout using the existing topological sort algorithm.
- Unconnected agents float on the right with a dashed border.

**Undo/redo:**
- Pipeline editor maintains an undo stack (in-memory, up to 50 operations).
- Ctrl+Z / Cmd+Z to undo, Ctrl+Shift+Z / Cmd+Shift+Z to redo.
- Applies to: edge add/remove, start agent change, edge config changes.

**Save behavior:**
- Pipeline changes are **not** auto-saved. They are included in the existing "Save All Changes" button at the top of the settings page.
- Unsaved changes are visually indicated (e.g., the Pipeline section header shows a dot).

### 2.4 Pipeline Validation (real-time)

| Condition | Indicator | Severity |
|-----------|-----------|----------|
| No start agent marked | Warning banner at top of pipeline section | Error (blocks save) |
| Start agent has incoming edges | Warning badge on the start node | Warning |
| Disconnected agents (no edges, not start) | Dashed border on the node | Info |
| Revision loops without a `max_revision_cycles` cap on involved agents | Warning on the cycle edges | Warning |
| Edge task_types not in source's `produces` or target's `accepts` | Warning icon on the edge | Warning |
| Pipeline running | "Pipeline running" overlay, editing disabled | Info |

### 2.5 Pipeline Data Model

Stored in `team.yaml` (file-based, consistent with role YAMLs). Each edge has a unique `id` for API reference.

```yaml
pipeline:
  id: "default-pipeline"
  name: "Default Pipeline"
  start_agent: pm
  edges:
    - id: "edge-1"
      from: pm
      to: architect
      task_types: [tech_design]
      on_failure: block
    - id: "edge-2"
      from: architect
      to: coder_be
      task_types: [implementation]
      on_failure: block
    - id: "edge-3"
      from: coder_be
      to: architect_reviewer
      task_types: [verification]
      on_failure: block
    - id: "edge-4"
      from: architect_reviewer
      to: coder_be
      task_types: [revision]
      on_failure: block
  node_config:
    architect_reviewer:
      join_strategy: stream
    # Nodes not listed here default to wait_all
```

This replaces the per-agent `routes_to` field. The pipeline is the single source of truth for all routing.

**Note:** `join_strategy` is per-node (in `node_config`), not per-edge, because a node's join behavior must be consistent across all incoming edges. `on_failure` is per-edge because different upstream agents may warrant different failure handling.

### 2.6 On-Failure Scoping

`on_failure` is per-edge. When multiple edges feed into the same node:
- Each edge's policy is evaluated independently when its source fails.
- `cancel_pipeline` on any edge cancels the entire pipeline run.
- `block` on an edge means the receiving node waits for that specific edge's source (even if other edges have completed).
- `continue_partial` means the receiving node proceeds without that edge's contribution.

### 2.7 Pipeline Locking

- Pipeline topology is locked during active execution (any group has tasks in `pending`, `in_progress`, `awaiting_approval`, or `awaiting_clarification` status).
- User sees a "Pipeline running" overlay — cannot edit edges or start agent.
- Must cancel all active runs to edit.

### 2.8 Graceful Handling of Missing Roles

If `team.yaml` pipeline references a role that no longer exists (e.g., deleted):
- Edges involving the missing role are skipped during rendering.
- A warning is shown: "Pipeline references unknown agent: {role_name}. Remove or re-create this agent."
- The pipeline is still functional for valid edges.

---

## 3. Approval, Clarification & Human-in-the-Loop

### 3.1 approval_mode (per agent)

| Mode | Behavior |
|------|----------|
| `auto` | Agent completes tasks autonomously, no human gate |
| `manual` | Every task completion goes to `awaiting_approval` — user must approve/reject via dashboard |
| `first_run` | First task requires approval; if approved, subsequent tasks auto-complete. Resets per group (each new goal/group submission resets the counter) |

**Definition of "pipeline run":** A pipeline run corresponds to a **group** — created when the user submits a goal. `first_run` mode resets when a new group is created. Within the same group, once approved, subsequent tasks auto-complete.

### 3.2 MCP Tools — Simplified Agent Interface

Agents are given a **minimal, approval-mode-agnostic tool set.** The agent does not know about its approval_mode — the orchestrator handles this transparently.

**MCP tools provided to agents:**

| Tool | Purpose | Blocks? |
|------|---------|---------|
| `complete_task(artifact_paths[], summary)` | Mark current task as done with output artifacts | Depends on approval_mode (see below) |
| `request_clarification(question, context, suggested_options[])` | Ask user a question mid-task | Yes — blocks until user responds |
| `route_task(target_agent, task_type, title, description, priority, blocked_by[])` | Create a downstream task for a connected agent | No (fire-and-forget) |
| `get_my_connections()` | Get pipeline connections for this agent | No |

**`complete_task` behavior by approval_mode:**
- `auto`: returns immediately, task marked `completed`.
- `manual`: blocks until user approves/rejects. Returns `{status: "approved"}` or `{status: "rejected", feedback: "..."}`.
- `first_run`: blocks on the first task in the group. If approved, all subsequent `complete_task` calls in the same group return immediately.

This eliminates the ambiguity of having both `complete_task` and `submit_for_approval`. The agent always calls `complete_task()` — the orchestrator decides whether to gate it.

**Clarification limits:**
- Max `request_clarification` calls per task: configurable via `max_clarification_requests` (default 10).
- After the limit, the tool returns an error: "Clarification limit reached. Make your best judgment or escalate via complete_task."

### 3.3 Blocking Implementation

**Long-polling (not short-polling):**
- MCP tool makes an HTTP request with a 30-second timeout.
- If no response within 30s, server returns `{status: "pending"}` and the tool re-polls.
- This reduces request volume from ~43,200/day to ~2,880/day per blocked agent.
- Overall timeout: 24 hours. After that, task → `timed_out`.

**Persistence:**
- All pending approval/clarification requests are stored in the database (`human_interaction_requests` table), not in-memory.
- Server restarts do not lose pending requests. Agent re-polls and picks up where it left off.

**Authentication:**
- Each agent instance receives a unique `instance_token` at launch (UUID, generated by the orchestrator).
- All MCP tool HTTP requests include this token in the `Authorization` header.
- Server validates the token against active instances. Invalid tokens are rejected with 401.

### 3.4 Dashboard Notification Cards

**Notification list view:**
- A dedicated "Action Required" panel in the dashboard showing all pending human interactions.
- Sorted by urgency: `awaiting_human_intervention` (escalated) > `awaiting_approval` > `awaiting_clarification`.
- Each card is compact in list view, expandable to full view on click.
- Badge count shown in the sidebar nav.

**Approval card (expanded):**
- Shows agent name, task summary, artifact links
- "View Artifact" button opens the artifact viewer
- Optional feedback text field
- "Approve" and "Reject" buttons
- Reject sends feedback back to the agent (returned as the `complete_task` response)

**Clarification card (expanded):**
- Shows agent name, question, context reference
- Free-text response field
- Optional quick-pick buttons from `suggested_options[]`
- "Respond" button sends the answer back

**Escalation card (for `awaiting_human_intervention`):**
- Shows full revision history (all previous attempts + feedback)
- Options: "Provide guidance and retry" (creates a new task with human notes), "Re-assign to different agent" (dropdown of compatible agents), "Force close task" (marks as failed)

**History:**
- Resolved notifications move to a "Resolved" tab for audit trail.
- Kept for the lifetime of the group.

**Browser notifications:**
- When a new `awaiting_approval` or `awaiting_clarification` item arrives and the dashboard tab is in the background, a browser Notification API alert is fired (with user permission).

**Bulk operations:**
- "Approve all" button for multiple pending approvals from the same agent type.
- "Respond to all" with a single response for similar clarification requests.

### 3.5 Artifact Viewer

Generic viewer that renders based on content type:
- Markdown/text: rendered inline
- Images (mockups): image viewer with zoom
- Code diffs: syntax-highlighted diff view
- HTML: sandboxed iframe preview
- JSON/YAML: syntax-highlighted with collapsible sections

**Path sanitization:** Artifact paths are validated to be within `artifacts/{group_id}/`. Paths containing `..` or absolute paths outside the project are rejected.

Available for any agent that produces artifacts, not just Designer.

### 3.6 Revision Loop

- Revision count is tracked per **task chain** (the original task + all revision re-creations), not per task ID. The chain is identified by a `chain_id` field stored on each task, set to the original task's ID.
- On rejection, count increments, a new task is created for the originating agent with the feedback and the same `chain_id`.
- The **orchestrator** handles revision routing, not the agent. When `complete_task` returns `{status: "rejected", feedback: "..."}`, the orchestrator:
  1. Increments the chain's `revision_count`.
  2. If under `max_revision_cycles`: creates a new task for the originating agent with the feedback.
  3. If at limit: sets task status to `awaiting_human_intervention`.
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
Use the `complete_task` tool when your work is done.
Do NOT attempt to route to agents not listed above.
```

**Staleness note:** The system prompt is generated at agent launch and may become stale if the pipeline is edited mid-execution (though pipeline editing is blocked during execution — see 2.7). The system prompt is **best-effort guidance** for the LLM. The `route_task` MCP tool is the **authoritative validator** — it checks the current pipeline topology at call time and rejects invalid routes.

### 4.2 MCP Tool Enforcement

`route_task` validates at runtime:
- Target agent exists in the pipeline.
- An edge exists from the calling agent to the target agent.
- The task type is allowed on that edge.
- Rejects with a descriptive error if any validation fails (e.g., "No edge from 'coder_be' to 'designer_web'. Available targets: architect_reviewer").
- The calling agent's `instance_token` is validated.

### 4.3 Initial Goal → Start Agent

When the user submits a goal via the dashboard:
1. Orchestrator creates a new **group** (the "pipeline run").
2. Orchestrator creates the first task:
   - `title`: the goal text (truncated to 200 chars)
   - `description`: the full goal text
   - `task_type`: `goal`
   - `priority`: `high`
   - `assigned_to`: the pipeline's `start_agent`
   - `group_id`: the new group's ID
3. Start agent is launched (or picks up the task from its queue if already running).

### 4.4 Context Passing Between Agents

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
- **Context recovery:** On retry, the orchestrator injects a summary of the previous attempt into the new session's system prompt: "You are retrying task {id}. Previous attempt ended at: {last_checkpoint_summary}. Continue from where you left off." Full conversation replay is not feasible due to token limits.
- 3rd failure — task status becomes `failed`, human notification sent, follows edge's `on_failure` policy.

### 5.3 API Concurrency

- Orchestrator-level semaphore: max concurrent API calls configurable in `team.yaml` under `execution.max_concurrent_api_calls` (default 5).
- Scope: per-project. Each project has its own semaphore.
- Agents queue for a slot before making API calls.
- Prevents 429 rate limit errors.

### 5.4 Cancellation Cascade

- User cancels a task — all downstream tasks in the same group auto-cancel (status → `cancelled`).
- Pipeline execution stops for that group.
- Other groups/pipeline runs are unaffected.
- **In-flight agent tasks:** If an agent is actively working on a cancelled task, the orchestrator sends a kill signal to the agent process. The agent's worktree is preserved for inspection.

### 5.5 Agent Config Versioning

- In-flight tasks use the config that was active when they started (snapshot stored per-task).
- New tasks pick up the latest config.
- No hot-swap mid-execution.

### 5.6 Git Worktrees

- Each agent instance gets its own git worktree and branch.
- Branch naming: `task/{group_id}-{task_id}-{short_description}`
- Worktree branched from: latest `main` at task start time.
- On task completion: attempt fast-forward merge to `main`.
- **Merge queue:** If multiple agents complete simultaneously, merges are serialized (FIFO). The second agent's worktree is rebased onto the new main before merging.
- On merge conflict: task status becomes `awaiting_human_intervention` with a diff viewer in the dashboard. The worktree is preserved.
- **Cleanup:** Worktrees are deleted after successful merge. Failed/cancelled task worktrees are kept for 7 days, then auto-cleaned. Configurable via `execution.worktree_retention_days` in `team.yaml`.

### 5.7 Fan-out / Fan-in

**Join strategies (per node, applies to all incoming edges):**

| Strategy | Behavior |
|----------|----------|
| `wait_all` (default) | Agent starts only when ALL upstream tasks targeting it are complete |
| `stream` | Each upstream task completion independently triggers a new task for this agent |

**On upstream failure (per incoming edge):**

| Policy | Behavior |
|--------|----------|
| `block` (default) | Wait indefinitely until human intervenes |
| `continue_partial` | Proceed with completed tasks, note the gap in the downstream task's context |
| `cancel_pipeline` | Abort the entire pipeline run (all tasks in the group → `cancelled`) |

**Priority inheritance:**
- Child tasks inherit the priority of the parent task that created them.
- Agents process higher-priority tasks first from their queue.

### 5.8 Pipeline Execution Progress

When a pipeline is running, the Pipeline section in the dashboard shows:
- Active agent nodes pulse/glow with their accent color.
- Completed nodes show a checkmark overlay.
- Failed/timed-out nodes show a red warning overlay.
- Edges carrying in-flight tasks show animated particles (already present in the current SVG renderer).
- A progress bar at the top of the pipeline section: `{completed_tasks} / {total_tasks} ({percentage}%)`.

---

## 6. Migration from Current System

### 6.1 What Changes

| Current | New |
|---------|-----|
| `routes_to` field in each agent YAML | Pipeline edges in `team.yaml` (single source of truth) |
| 3-step wizard (Identity → Config → Pipeline) | 2-tab modal (Presets / Custom), no routing step |
| "Pipeline Visualizer" (read-only) | "Pipeline" (fully editable) |
| No approval system | `approval_mode` flag + blocking MCP tools + dashboard cards |
| No clarification mechanism | `request_clarification` MCP tool + dashboard cards |
| Agent Routing accordion in agent cards | Removed — routing is pipeline-only |
| `delete_role` API cleans `routes_to` on other roles | `delete_role` API cleans pipeline edges referencing the deleted role |

### 6.2 Backward Compatibility & Auto-Migration

On first load after upgrade:
1. If `team.yaml` has no `pipeline` section but roles have `routes_to` fields:
   - Auto-generate pipeline edges from existing `routes_to` data.
   - Set `start_agent` to the first role with `can_create_groups: true` (typically PM).
   - Write the generated pipeline section to `team.yaml`.
2. Existing `routes_to` fields in role YAMLs are **ignored** after migration (not deleted, for rollback safety).
3. A one-time migration log entry is written to the server logs.

### 6.3 Deletion Cascade

When an agent is deleted from Agent Roles:
- All pipeline edges to/from that agent are removed from `team.yaml`.
- If the deleted agent is the `start_agent`, the `start_agent` field is cleared and a validation error is shown.
- **In-flight tasks** assigned to the deleted agent are cancelled with a notification to the user.
- The role YAML file is deleted from `config/roles/`.

### 6.4 What Stays the Same

- Agent YAML files in `config/roles/` for custom agents
- Existing API endpoints for agent CRUD (`/api/settings/roles`), extended with new fields
- Task board, task dependencies, group system
- Artifact store
- WorktreeManager (formalized for per-agent-instance pattern)
- Dashboard layout and styling (dark theme, existing CSS system)

---

## 7. Future Considerations (Not In Scope, But Data-Model-Ready)

These are explicitly deferred but the data model supports them:

1. **Multiple pipelines per project** — `pipeline.id` field exists. Future: user can create/switch pipelines.
2. **Pipeline templates** — "Full-stack Web App", "iOS App", "API Service" as pre-built pipeline + agent combinations. Future: a `config/pipeline_templates/` directory.
3. **`wait_threshold(n)` join strategy** — Start when N of M upstream tasks complete. Future: add to `join_strategy` enum.
4. **Cost budget per pipeline run** — Pause execution when spend exceeds a threshold. Future: add `budget` field to pipeline config.

---

## 8. New Database Tables

### 8.1 `human_interaction_requests`

Stores pending and resolved approval/clarification requests.

| Column | Type | Description |
|--------|------|-------------|
| `id` | TEXT PK | UUID |
| `task_id` | TEXT FK | References tasks(id) |
| `group_id` | TEXT FK | References groups(id) |
| `agent_role` | TEXT | Role that initiated the request |
| `instance_token` | TEXT | Agent instance token for auth |
| `type` | TEXT | `approval` or `clarification` |
| `status` | TEXT | `pending`, `approved`, `rejected`, `responded` |
| `request_data` | JSON | Question, context, suggested_options, artifact_paths, summary |
| `response_data` | JSON | User's response text, feedback |
| `created_at` | TEXT | ISO timestamp |
| `resolved_at` | TEXT | ISO timestamp, NULL if pending |

### 8.2 `task_chains`

Tracks revision chains across task re-creations.

| Column | Type | Description |
|--------|------|-------------|
| `chain_id` | TEXT PK | Original task ID |
| `task_id` | TEXT FK | Current task in the chain |
| `revision_count` | INTEGER | Number of revisions so far |
| `max_revisions` | INTEGER | Limit from agent config |
| `created_at` | TEXT | ISO timestamp |

### 8.3 Changes to existing `tasks` table

| New Column | Type | Description |
|------------|------|-------------|
| `chain_id` | TEXT | Links to task_chains, NULL for non-revisable tasks |
| `approval_mode` | TEXT | Snapshot of agent's approval_mode at task creation |
| `instance_token` | TEXT | Token of the agent instance working on this task |
