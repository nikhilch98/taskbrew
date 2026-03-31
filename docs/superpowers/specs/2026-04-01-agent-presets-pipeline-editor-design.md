# Agent Presets, Editable Pipeline & Human-in-the-Loop Design

**Date:** 2026-04-01
**Status:** Approved (rev 3 — post 20-pass review)
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
- Maximum configurability: display name, role ID, prefix, color, emoji, model, system prompt, tools, `approval_mode` (dropdown: auto/manual/first_run), `max_revision_cycles` (number input, 0 = unlimited), `max_clarification_requests` (number input, default 10), `uses_worktree` (toggle, default true for coding agents, false for non-coding).
- No routing/connections step. Routing is handled exclusively in the Pipeline editor.

### 1.2 Preset Agent Catalog (22 agents)

#### Planning

| Template | Description | approval_mode | max_revision_cycles | uses_worktree |
|----------|-------------|---------------|---------------------|---------------|
| PM | Decomposes user prompts into exhaustive task lists covering Infra, BE, FE, Testing, Research, UI, UX, etc. | auto | — | false |

#### Architecture

| Template | Description | approval_mode | max_revision_cycles | uses_worktree |
|----------|-------------|---------------|---------------------|---------------|
| Architect | Creates HLD/LLD with extensive implementation details, methods, functions, and logic | first_run | — | false |

#### Review

| Template | Description | approval_mode | max_revision_cycles | uses_worktree |
|----------|-------------|---------------|---------------------|---------------|
| Architect Reviewer | Reviews code changes, approves or rejects with specific feedback | auto | 5 | true |

#### Coding (6 variants)

| Template | Description | approval_mode | max_revision_cycles | uses_worktree |
|----------|-------------|---------------|---------------------|---------------|
| Coder BE | Backend implementation (APIs, services, databases) | auto | 5 | true |
| Coder FE | Frontend implementation (React, Vue, vanilla JS) | auto | 5 | true |
| Coder UI/UX Web | Web UI/UX implementation (CSS, layouts, responsive design) | auto | 5 | true |
| Coder Swift | iOS native implementation (Swift, UIKit, SwiftUI) | auto | 5 | true |
| Coder Flutter | Flutter cross-platform implementation | auto | 5 | true |
| Coder Infra | Infrastructure code (Docker, Terraform, CI/CD configs) | auto | 5 | true |

#### Design (4 variants)

| Template | Description | approval_mode | max_revision_cycles | uses_worktree |
|----------|-------------|---------------|---------------------|---------------|
| Designer Web | Web mockups and design assets | manual | 5 | true |
| Designer iOS Swift | iOS native design mockups | manual | 5 | true |
| Designer Flutter iOS | Flutter iOS design mockups | manual | 5 | true |
| Designer Flutter iOS+Android | Flutter cross-platform design mockups | manual | 5 | true |

#### Testing (3 variants)

| Template | Description | approval_mode | max_revision_cycles | uses_worktree |
|----------|-------------|---------------|---------------------|---------------|
| QA Tester Unit | Unit test writing and execution | auto | — | true |
| QA Tester Integration | Integration test writing and execution | auto | — | true |
| QA Tester E2E | End-to-end test writing and execution | auto | — | true |

#### Security

| Template | Description | approval_mode | max_revision_cycles | uses_worktree |
|----------|-------------|---------------|---------------------|---------------|
| Security Auditor | OWASP scans, dependency audits, secrets detection | first_run | — | true |

#### Operations

| Template | Description | approval_mode | max_revision_cycles | uses_worktree |
|----------|-------------|---------------|---------------------|---------------|
| DevOps Engineer | CI/CD pipelines, deployment configs, IaC | auto | 5 | true |
| Database Architect | Schema design, migrations, query optimization | first_run | — | false |

#### Documentation

| Template | Description | approval_mode | max_revision_cycles | uses_worktree |
|----------|-------------|---------------|---------------------|---------------|
| Technical Writer | API docs, READMEs, changelogs | auto | — | false |

#### Research

| Template | Description | approval_mode | max_revision_cycles | uses_worktree |
|----------|-------------|---------------|---------------------|---------------|
| Research Agent | Investigates libraries, APIs, feasibility; produces research reports | auto | — | false |

#### API

| Template | Description | approval_mode | max_revision_cycles | uses_worktree |
|----------|-------------|---------------|---------------------|---------------|
| API Designer | OpenAPI specs, contract-first design, endpoint validation | first_run | — | false |

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
uses_worktree: true

system_prompt: |
  You are a Backend Engineer agent.

  ## Your Task
  You will receive a task with a title, description, and context from upstream agents.
  Read the task description carefully and implement the required changes.

  ## Available Tools
  Use `route_task` to send work to connected agents (listed in your connections below).
  Use `request_clarification` if you need human input to proceed.
  Use `complete_task` when your work is done — pass file paths of all artifacts you produced.
  If `route_task` fails, check the error and use `get_my_connections()` to verify available targets.

  ## Artifact Format
  Save all output files to your working directory. When calling `complete_task`, pass the
  relative file paths as `artifact_paths`. The orchestrator will collect and store them.

  ## Routing Rules
  You can ONLY route tasks to agents listed in your injected connections section below.
  You CANNOT create tasks for agents you are not directly connected to.
  Call `route_task` BEFORE calling `complete_task`. After `complete_task` is called,
  no further `route_task` calls are accepted.

  ## Task Flow
  1. Read your task and all provided context/artifacts
  2. Implement the required changes
  3. Route any downstream tasks via `route_task`
  4. Call `complete_task` with your artifact paths and a summary

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
- **Add** `approval_mode`, `max_revision_cycles`, `max_clarification_requests`, and `uses_worktree` fields to the "Advanced" accordion.
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
5. **Default task_types on new edge:** Auto-populated with the intersection of source's `produces` and target's `accepts`. If empty (no overlap), defaults to all of source's `produces` with a validation warning. An empty `task_types` array is never stored — it always resolves to a concrete list.

**Removing connections:**
1. Hover an arrow — it highlights and shows a small "x" button at the midpoint.
2. Click "x" — edge is removed. If pipeline is saved and there are in-flight tasks using this edge, show a confirmation warning.

**Parallel edges:**
- Multiple edges between the same two agents (with different task_types) are allowed.
- Rendered as parallel arrows with a slight offset and labeled with their task_types.
- Example: Architect→Coder BE edge for `implementation` and a separate edge for `bug_fix`.

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
  - Note: `approval_mode` and `max_revision_cycles` are per-agent (in role YAML), not per-pipeline-node. This is because the same agent always has the same approval behavior regardless of pipeline topology. Future multi-pipeline support may revisit this.

**Node layout:**
- Auto-layout using the existing topological sort algorithm.
- Unconnected agents float on the right with a dashed border.

**Empty pipeline state:**
- When no agents exist or no edges are drawn, show: "Add agents and draw connections to create your pipeline."
- Start agent indicator only appears once at least one agent exists and is marked.

**Undo/redo:**
- Pipeline editor maintains an undo stack (in-memory, up to 50 operations).
- Ctrl+Z / Cmd+Z to undo, Ctrl+Shift+Z / Cmd+Shift+Z to redo.
- Applies to: edge add/remove, start agent change, edge config changes, node config changes.

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
| Pipeline running | "Pipeline running" overlay, editing disabled (see 2.7) | Info |
| Single agent, no edges | Valid — agent operates standalone | None |

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
- `cancel_pipeline` on any edge cancels the entire pipeline run (all tasks in the group → `cancelled`).
- `block` on an edge means the receiving node waits for that specific edge's source (even if other edges have completed).
- `continue_partial` means the receiving node proceeds without that edge's contribution. The downstream task description includes: "Note: upstream task from {agent_name} ({task_type}) failed/timed out. Proceeding without its output."

### 2.7 Pipeline Locking

- Pipeline topology is locked during active execution (any group has tasks in `pending`, `in_progress`, `awaiting_approval`, or `awaiting_clarification` status).
- **What's locked:** Edge add/remove, edge config changes, start agent changes, node config changes.
- **What's NOT locked:** Agent creation (adding new nodes) — these appear as unconnected and don't affect running pipelines. Agent deletion is also blocked if the agent has in-flight tasks.
- User sees a "Pipeline running" overlay on the edge-editing area.
- Must cancel all active runs to fully edit the pipeline.

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

**`first_run` with multiple instances:** `first_run` approval is tracked per **agent role per group** (not per instance). If Coder BE has 3 instances and `first_run` mode, approving ANY one instance's first task unlocks all instances of Coder BE for that group.

**`first_run` and revisions:** If a `first_run`-approved agent receives a revision task (rejected by reviewer), the revision does NOT reset the first_run flag. The agent was already trusted for this group.

### 3.2 MCP Tools — Simplified Agent Interface

Agents are given a **minimal, approval-mode-agnostic tool set.** The agent does not know about its approval_mode — the orchestrator handles this transparently.

**MCP tools provided to agents:**

| Tool | Purpose | Blocks? |
|------|---------|---------|
| `complete_task(artifact_paths[], summary)` | Mark current task as done with output artifacts | Depends on approval_mode (see below) |
| `request_clarification(question, context, suggested_options[])` | Ask user a question mid-task | Yes — blocks until user responds |
| `route_task(target_agent, task_type, title, description, priority, blocked_by[])` | Create a downstream task for a connected agent | No (fire-and-forget, but see 3.8) |
| `get_my_connections()` | Get pipeline connections for this agent | No |

**Tool ordering constraint:** `route_task` must be called BEFORE `complete_task`. Once `complete_task` is called, the agent's `instance_token` is invalidated for `route_task` (but the `complete_task` blocking poll remains active). This prevents agents from creating downstream tasks after they've finished.

**Only one blocking call at a time.** An agent instance is single-threaded — it cannot have both a `request_clarification` and a `complete_task` blocking simultaneously. If `request_clarification` is pending, `complete_task` will return an error: "Resolve pending clarification first."

**`complete_task` behavior by approval_mode:**
- `auto`: returns immediately with `{status: "approved"}`, task marked `completed`.
- `manual`: blocks until user approves/rejects. Returns `{status: "approved"}` or `{status: "rejected", feedback: "..."}`.
- `first_run`: checks `first_run_approved_roles` on the group. If this role is already approved, returns immediately. Otherwise blocks like `manual`.

**`complete_task` artifact validation:** Before accepting, the orchestrator validates that all `artifact_paths` exist on disk. If any are missing, returns an error: "Artifact not found: {path}". Paths are sanitized — must be within the agent's working directory (worktree or project root). Paths containing `..` or absolute paths outside the project are rejected.

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
- **Idempotency:** Each request has a `request_key` = `{task_id}:{type}:{sequence_number}`. If a duplicate request arrives (e.g., after agent retry), the existing pending request is returned instead of creating a new one.

**Authentication:**
- Each agent instance receives a unique `instance_token` at launch (UUID, generated by the orchestrator).
- All MCP tool HTTP requests include this token in the `Authorization` header.
- Server validates the token against active instances. Invalid tokens are rejected with 401.
- **Token lifecycle:** Token is valid from instance launch until `complete_task` returns or the task is cancelled/timed out. Crashed instances' tokens are invalidated when the crash is detected (after 3 failed retries).

### 3.4 MCP Server Configuration

Agents connect to the TaskBrew MCP server at launch. The orchestrator configures the CLI agent with the MCP server connection:

```json
{
  "mcpServers": {
    "taskbrew": {
      "url": "http://localhost:8420/mcp",
      "headers": {
        "Authorization": "Bearer {instance_token}"
      }
    }
  }
}
```

This is injected into the CLI agent's configuration (e.g., Claude Code's `.mcp.json` or equivalent). The MCP server exposes the four tools: `complete_task`, `request_clarification`, `route_task`, `get_my_connections`.

### 3.5 Dashboard Notification Cards

**Notification list view:**
- A dedicated "Action Required" panel in the dashboard showing all pending human interactions.
- Sorted by urgency: `awaiting_human_intervention` (escalated) > `awaiting_approval` > `awaiting_clarification`.
- Each card is compact in list view, expandable to full view on click.
- Badge count shown in the sidebar nav.

**Approval card (expanded):**
- Shows agent name, task summary, artifact links
- "View Artifact" button opens the artifact viewer
- Optional feedback text field
- Three actions:
  - **"Approve"** — marks task completed, unblocks agent
  - **"Approve with Notes"** — marks task completed AND creates a follow-up minor-fix task for the agent with the notes
  - **"Reject"** — sends feedback back to the agent (returned as the `complete_task` response), triggers revision loop

**Clarification card (expanded):**
- Shows agent name, question, context reference
- Free-text response field
- Optional quick-pick buttons from `suggested_options[]`
- Two actions:
  - **"Respond"** — sends the answer back
  - **"Skip"** — returns `{status: "skipped", message: "User chose to skip. Use your best judgment."}` to the agent

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

### 3.6 Artifact Viewer

Generic viewer that renders based on content type:
- Markdown/text: rendered inline
- Images (mockups): image viewer with zoom
- Code diffs: syntax-highlighted diff view
- HTML: sandboxed iframe preview
- JSON/YAML: syntax-highlighted with collapsible sections

**Path sanitization:** Artifact paths are validated to be within `artifacts/{group_id}/`. Paths containing `..` or absolute paths outside the project are rejected.

Available for any agent that produces artifacts, not just Designer.

### 3.7 Revision Loop

- Revision count is tracked per **task chain** (the original task + all revision re-creations), not per task ID. The chain is identified by a `chain_id` field stored on each task, set to the original task's ID.
- On rejection, count increments, a new task is created for the originating agent with the feedback and the same `chain_id`.
- The **orchestrator** handles revision routing, not the agent. When `complete_task` returns `{status: "rejected", feedback: "..."}`, the orchestrator:
  1. Increments the chain's `revision_count`.
  2. If under `max_revision_cycles`: creates a new task for the originating agent with the feedback.
  3. If at limit: sets task status to `awaiting_human_intervention`.
- Dashboard shows escalation card with full revision history.

### 3.8 Route-Task Deferred Activation

Tasks created via `route_task` are created with:
- `status: pending`
- `blocked_by: [current_task_id]` (the calling agent's task)

They only transition to `ready` when the parent task reaches `completed` status (i.e., after `complete_task` succeeds, including any approval gate). This prevents:
- Downstream agents starting work before upstream is approved.
- Wasted work if the upstream agent's task is rejected.
- Race conditions between routing and completion.

---

## 4. Hybrid Routing Engine

### 4.1 System Prompt Injection

At agent launch, the orchestrator dynamically injects connection info into the system prompt:

```
== TASK CONTEXT ==
Task ID: {task_id}
Title: {task_title}
Group: {group_id}
Priority: {priority}

== DESCRIPTION ==
{task_description}

== PARENT ARTIFACT ==
{parent_artifact_content or "None"}

== ROOT ARTIFACT ==
{root_artifact_summary or "None"}

== SIBLING SUMMARY ==
{sibling_task_summaries or "None"}

== REJECTION HISTORY ==
{previous_rejection_feedback or "None — first attempt"}

== CONNECTED AGENTS ==
You can route tasks to these agents:
- Architect (accepts: tech_design, architecture_review)
- Coder BE (accepts: implementation, bug_fix)
- Coder FE (accepts: implementation, bug_fix)

Use `route_task` to send work. Use `request_clarification` for human input.
Use `complete_task` when done. Do NOT route to agents not listed above.
```

**Staleness note:** The system prompt is generated at agent launch and may become stale if the pipeline is edited mid-execution (though pipeline editing is blocked during execution — see 2.7). The system prompt is **best-effort guidance** for the LLM. The `route_task` MCP tool is the **authoritative validator** — it checks the current pipeline topology at call time and rejects invalid routes.

**PM agent note:** The PM agent only sees its direct connections (e.g., Architect). It does NOT see the full pipeline. If PM needs to create tasks that eventually reach Coder, it creates tasks for Architect, and Architect creates tasks for Coder. Each agent only routes to its direct neighbors.

### 4.2 MCP Tool Enforcement

`route_task` validates at runtime:
- Target agent exists in the pipeline.
- An edge exists from the calling agent to the target agent.
- The task type is allowed on that edge.
- The calling agent's `instance_token` is valid and has not been invalidated (by `complete_task`).
- Rejects with a descriptive error if any validation fails (e.g., "No edge from 'coder_be' to 'designer_web'. Available targets: architect_reviewer").
- **Error handling guidance:** Agent system prompts instruct: "If `route_task` fails, check the error message and adjust. Use `get_my_connections()` to verify available routes and valid task_types."

### 4.3 Initial Goal → Start Agent

When the user submits a goal via the dashboard:
1. Orchestrator validates the pipeline has a `start_agent` set. If not, returns an error.
2. Orchestrator creates a new **group** (the "pipeline run").
3. Orchestrator creates the first task:
   - `title`: the goal text (truncated to 200 chars)
   - `description`: the full goal text
   - `task_type`: `goal`
   - `priority`: `high`
   - `assigned_to`: the pipeline's `start_agent`
   - `group_id`: the new group's ID
   - `status`: `ready` (not `pending` — no upstream dependency)
4. Start agent is launched (or picks up the task from its queue if already running).

### 4.4 Context Passing Between Agents

**Artifact store (large outputs):**
- Stored in `artifacts/{group_id}/{task_id}/`
- Referenced in the downstream agent's system prompt (see 4.1 template)
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

### 5.1 Agent Instance Lifecycle

Each task = new agent instance = new CLI process = new `instance_token`.

1. Task becomes `ready` (all `blocked_by` dependencies resolved).
2. Orchestrator checks `max_instances` for that agent role. If at capacity, task queues.
3. **Task queue ordering:** Priority-weighted FIFO. Higher priority tasks dequeue first. Within same priority, FIFO.
4. Orchestrator spawns a new CLI agent process (Claude Code, Gemini CLI, etc.).
5. Agent receives its `instance_token`, MCP server config, and task context via system prompt injection.
6. Agent works on the task (may call `route_task`, `request_clarification`).
7. Agent calls `complete_task` → instance_token invalidated for `route_task`.
8. CLI process exits. Instance is cleaned up.

### 5.2 Task Timeout

- Default: 30 minutes per task, configurable per agent template via `max_execution_time`.
- On timeout: task status becomes `timed_out`, follows the edge's `on_failure` policy.
- The agent process is killed.

### 5.3 Crash Retry

- Agent process dies — auto-retry up to 3 times with exponential backoff (5s, 30s, 120s).
- **Worktree reuse on retry:** If `uses_worktree: true` and the worktree/branch still exist from the crashed attempt, the retry reuses the same worktree (preserving any partial work). A fresh worktree is only created if none exists.
- **Context recovery:** On retry, the orchestrator injects a summary of the previous attempt into the new session's system prompt: "You are retrying task {id} (attempt {n}/3). Previous attempt crashed. Your worktree may contain partial changes from the previous attempt. Inspect the current state and continue." Full conversation replay is not feasible due to token limits.
- 3rd failure — task status becomes `failed`, human notification sent, follows edge's `on_failure` policy. Instance token invalidated.

### 5.4 API Concurrency

- Orchestrator-level semaphore: max concurrent API calls configurable in `team.yaml` under `execution.max_concurrent_api_calls` (default 5).
- Scope: per-project. Each project has its own semaphore.
- Agents queue for a slot before making API calls.
- Prevents 429 rate limit errors.
- **Disk space awareness:** If `max_instances` sum across all agents × average repo size exceeds available disk, show a warning in the dashboard settings page. This is informational, not blocking.

### 5.5 Cancellation Cascade

- User cancels a task — all downstream tasks in the same group auto-cancel (status → `cancelled`).
- Pipeline execution stops for that group.
- Other groups/pipeline runs are unaffected.
- **In-flight agent tasks:** If an agent is actively working on a cancelled task, the orchestrator sends a kill signal to the agent process. The agent's worktree is preserved for inspection.
- **Pending human interactions:** Any `awaiting_approval` or `awaiting_clarification` requests for cancelled tasks are auto-resolved with `{status: "cancelled"}`.

### 5.6 Agent Config Versioning

- In-flight tasks use the config that was active when they started (snapshot stored per-task in the `tasks` table as `config_snapshot` JSON column).
- New tasks pick up the latest config.
- No hot-swap mid-execution.

### 5.7 Git Worktrees

- Only agents with `uses_worktree: true` get worktrees. Others work in the main project directory (read-only access for non-coding agents like PM, Research).
- Each agent instance gets its own git worktree and branch.
- Branch naming: `task/{group_id}-{task_id}-{short_description}`
- Worktree path: `.worktrees/task-{group_id}-{task_id}/` (consistent with existing `WorktreeManager`).
- Worktree branched from: latest `{base_branch}` at task start time. Base branch configurable via `execution.base_branch` in `team.yaml` (default: `main`).
- On task completion: attempt fast-forward merge to `{base_branch}`.
- **Merge queue:** If multiple agents complete simultaneously, merges are serialized via a database-backed lock (FIFO). The second agent's worktree is rebased onto the new base branch before merging. If rebase itself conflicts, treated as a merge conflict.
- On merge conflict: task status becomes `awaiting_human_intervention` with a diff viewer in the dashboard. The worktree is preserved.
- **Cleanup:** Worktrees are deleted after successful merge. Failed/cancelled task worktrees are kept for 7 days, then auto-cleaned by a background job. Configurable via `execution.worktree_retention_days` in `team.yaml`.

### 5.8 Fan-out / Fan-in

**Join strategies (per node, applies to all incoming edges):**

| Strategy | Behavior |
|----------|----------|
| `wait_all` (default) | Agent starts only when ALL upstream tasks in the current group that target this node are `completed` or resolved via `on_failure` policy |
| `stream` | Each upstream task completion independently triggers a new task for this agent |

**`wait_all` dynamic count:** The orchestrator does NOT require a pre-declared count of upstream tasks. Instead, it tracks all tasks in the current group that target this node. The `wait_all` condition is met when every such task has reached a terminal state (`completed`, `failed` with `continue_partial`, or `cancelled`). This is evaluated atomically with a database lock to prevent duplicate downstream task creation from race conditions.

**On upstream failure (per incoming edge):**

| Policy | Behavior |
|--------|----------|
| `block` (default) | Wait indefinitely until human intervenes |
| `continue_partial` | Proceed with completed tasks, note the gap in the downstream task's context |
| `cancel_pipeline` | Abort the entire pipeline run (all tasks in the group → `cancelled`) |

**Priority inheritance:**
- Child tasks inherit the priority of the parent task that created them.
- Agents process higher-priority tasks first from their queue.

### 5.9 Pipeline Execution Progress

When a pipeline is running, the Pipeline section in the dashboard shows:
- Active agent nodes pulse/glow with their accent color.
- Completed nodes show a checkmark overlay.
- Failed/timed-out nodes show a red warning overlay.
- `awaiting_approval`/`awaiting_clarification` nodes show an orange pause overlay.
- Edges carrying in-flight tasks show animated particles (already present in the current SVG renderer).
- A progress bar at the top of the pipeline section: `{completed_tasks} / {total_tasks} ({percentage}%)`.
- **Group selector:** If multiple groups are running simultaneously, a dropdown in the pipeline header lets the user switch which group's progress is displayed.

### 5.10 WebSocket Events

The dashboard subscribes to the following WebSocket events for real-time updates:

| Event | Payload | Triggers |
|-------|---------|----------|
| `task.status_changed` | `{task_id, group_id, old_status, new_status, agent_role}` | Pipeline progress, task board |
| `interaction.created` | `{id, type, task_id, agent_role, request_data}` | Notification badge, action required panel |
| `interaction.resolved` | `{id, type, status, response_data}` | Remove from pending, add to history |
| `group.created` | `{group_id, title, start_agent}` | Group selector update |
| `group.completed` | `{group_id, stats}` | Pipeline progress complete state |
| `agent.instance_started` | `{agent_role, instance_token, task_id}` | Node pulse animation |
| `agent.instance_stopped` | `{agent_role, instance_token, reason}` | Node idle state |

**Reconnection:** On WebSocket disconnect, the dashboard reconnects with exponential backoff. On reconnect, it calls `GET /api/sync/state` to get all current pipeline state, pending interactions, and task statuses to reconcile any missed events.

---

## 6. API Endpoints

### 6.1 Pipeline CRUD

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/pipeline` | Get current pipeline config (edges, start_agent, node_config) |
| `PUT` | `/api/pipeline` | Full pipeline update (used by "Save All Changes") |
| `POST` | `/api/pipeline/edges` | Add a single edge |
| `PUT` | `/api/pipeline/edges/{edge_id}` | Update edge config (task_types, on_failure) |
| `DELETE` | `/api/pipeline/edges/{edge_id}` | Remove an edge |
| `PUT` | `/api/pipeline/start-agent` | Set start agent |
| `PUT` | `/api/pipeline/node-config/{role_name}` | Set node join strategy |
| `POST` | `/api/pipeline/validate` | Validate pipeline (replaces old routes_to validation) |

### 6.2 Human Interactions

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/interactions/pending` | List pending approval/clarification requests |
| `GET` | `/api/interactions/history?group_id=` | List resolved requests (filterable by group) |
| `POST` | `/api/interactions/{id}/approve` | Approve (optional: `notes` for "approve with notes") |
| `POST` | `/api/interactions/{id}/reject` | Reject with `feedback` body |
| `POST` | `/api/interactions/{id}/respond` | Respond to clarification with `response` body |
| `POST` | `/api/interactions/{id}/skip` | Skip clarification |
| `GET` | `/api/interactions/{id}/poll` | Long-poll endpoint for agent MCP tools (30s timeout) |
| `POST` | `/api/interactions/bulk-approve` | Bulk approve by `agent_role` and `group_id` |

### 6.3 Presets

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/presets` | List all presets (metadata only: id, category, display_name, description, icon) |
| `GET` | `/api/presets/{preset_id}` | Full preset detail (including system_prompt, tools, all config) |

### 6.4 Updated Existing Endpoints

| Endpoint | Change |
|----------|--------|
| `POST /api/settings/roles` | Accepts optional `preset_id` to copy preset config; accepts new fields: `approval_mode`, `max_revision_cycles`, `max_clarification_requests`, `uses_worktree` |
| `PUT /api/settings/roles/{role_name}` | Accepts new fields above |
| `DELETE /api/settings/roles/{role_name}` | Also removes pipeline edges and cancels in-flight tasks |
| `POST /api/settings/validate` | Validates pipeline edges instead of `routes_to` |
| `GET /api/sync/state` | New — returns full current state for dashboard reconnection |

### 6.5 MCP Tool Endpoints

Served at `/mcp` as an MCP-protocol-compliant server.

| Tool Name | Endpoint | Auth |
|-----------|----------|------|
| `complete_task` | `POST /mcp/tools/complete_task` | Bearer instance_token |
| `request_clarification` | `POST /mcp/tools/request_clarification` | Bearer instance_token |
| `route_task` | `POST /mcp/tools/route_task` | Bearer instance_token |
| `get_my_connections` | `POST /mcp/tools/get_my_connections` | Bearer instance_token |

---

## 7. Migration from Current System

### 7.1 What Changes

| Current | New |
|---------|-----|
| `routes_to` field in each agent YAML | Pipeline edges in `team.yaml` (single source of truth) |
| 3-step wizard (Identity → Config → Pipeline) | 2-tab modal (Presets / Custom), no routing step |
| "Pipeline Visualizer" (read-only) | "Pipeline" (fully editable) |
| No approval system | `approval_mode` flag + blocking MCP tools + dashboard cards |
| No clarification mechanism | `request_clarification` MCP tool + dashboard cards |
| Agent Routing accordion in agent cards | Removed — routing is pipeline-only |
| `delete_role` API cleans `routes_to` on other roles | `delete_role` API cleans pipeline edges and cancels in-flight tasks |

### 7.2 Backward Compatibility & Auto-Migration

On first load after upgrade:
1. If `team.yaml` has no `pipeline` section but roles have `routes_to` fields:
   - Auto-generate pipeline edges from existing `routes_to` data.
   - Set `start_agent` to the first role with `can_create_groups: true` (typically PM).
   - Edge IDs are auto-generated as `migrated-edge-{n}`.
   - Write the generated pipeline section to `team.yaml`.
2. Existing `routes_to` fields in role YAMLs are **ignored** after migration (not deleted, for rollback safety).
3. A one-time migration log entry is written to the server logs.

### 7.3 Deletion Cascade

When an agent is deleted from Agent Roles:
- All pipeline edges to/from that agent are removed from `team.yaml`.
- If the deleted agent is the `start_agent`, the `start_agent` field is cleared and a validation error is shown.
- **In-flight tasks** assigned to the deleted agent are cancelled with a notification to the user.
- **Pending interactions** for the deleted agent are auto-resolved as `cancelled`.
- The role YAML file is deleted from `config/roles/`.

### 7.4 What Stays the Same

- Agent YAML files in `config/roles/` for custom agents
- Existing API endpoints for agent CRUD (`/api/settings/roles`), extended with new fields
- Task board, task dependencies, group system
- Artifact store
- WorktreeManager (formalized for per-agent-instance pattern)
- Dashboard layout and styling (dark theme, existing CSS system)

---

## 8. Future Considerations (Not In Scope, But Data-Model-Ready)

These are explicitly deferred but the data model supports them:

1. **Multiple pipelines per project** — `pipeline.id` field exists. Future: user can create/switch pipelines.
2. **Pipeline templates** — "Full-stack Web App", "iOS App", "API Service" as pre-built pipeline + agent combinations. Future: a `config/pipeline_templates/` directory.
3. **`wait_threshold(n)` join strategy** — Start when N of M upstream tasks complete. Future: add to `join_strategy` enum.
4. **Cost budget per pipeline run** — Pause execution when spend exceeds a threshold. Future: add `budget` field to pipeline config.
5. **Per-pipeline-node approval_mode override** — Same agent with different approval behavior in different pipelines. Future: add to `node_config`.

---

## 9. New Database Tables

### 9.1 `human_interaction_requests`

Stores pending and resolved approval/clarification requests.

| Column | Type | Description |
|--------|------|-------------|
| `id` | TEXT PK | UUID |
| `request_key` | TEXT UNIQUE | Idempotency key: `{task_id}:{type}:{sequence_number}` |
| `task_id` | TEXT FK | References tasks(id) |
| `group_id` | TEXT FK | References groups(id) |
| `agent_role` | TEXT | Role that initiated the request |
| `instance_token` | TEXT | Agent instance token for auth |
| `type` | TEXT | `approval` or `clarification` |
| `status` | TEXT | `pending`, `approved`, `rejected`, `responded`, `skipped`, `cancelled` |
| `request_data` | JSON | Question, context, suggested_options, artifact_paths, summary |
| `response_data` | JSON | User's response text, feedback, notes |
| `created_at` | TEXT | ISO timestamp |
| `resolved_at` | TEXT | ISO timestamp, NULL if pending |

### 9.2 `task_chains`

Tracks revision chains across task re-creations. Multiple rows per chain (one per task in the chain).

| Column | Type | Description |
|--------|------|-------------|
| `chain_id` | TEXT | Original task ID (groups all revisions together) |
| `task_id` | TEXT FK | Task in the chain |
| `revision_number` | INTEGER | 0 for original, 1 for first revision, etc. |
| `max_revisions` | INTEGER | Limit from agent config |
| `created_at` | TEXT | ISO timestamp |

Primary key: `(chain_id, task_id)`.

### 9.3 `first_run_approvals`

Tracks which agent roles have been `first_run` approved per group.

| Column | Type | Description |
|--------|------|-------------|
| `group_id` | TEXT FK | References groups(id) |
| `agent_role` | TEXT | Role that was approved |
| `approved_at` | TEXT | ISO timestamp |

Primary key: `(group_id, agent_role)`.

### 9.4 Changes to existing `tasks` table

| New Column | Type | Description |
|------------|------|-------------|
| `chain_id` | TEXT | Links to task_chains, NULL for non-revisable tasks |
| `approval_mode` | TEXT | Snapshot of agent's approval_mode at task creation |
| `instance_token` | TEXT | Token of the agent instance working on this task |
| `config_snapshot` | JSON | Snapshot of agent config at task creation time |

---

## Changelog

### Rev 3 (post passes 11–20)
- Added `uses_worktree` flag per agent template; non-coding agents skip worktree creation
- Defined agent instance lifecycle: one CLI process per task, new instance_token per task
- Defined task queue ordering: priority-weighted FIFO
- Clarified `first_run` is per agent-role-per-group, not per instance
- Clarified `first_run` is not reset by revision tasks
- Added default `task_types` auto-population on edge creation
- Added parallel edges support between same agents
- Added empty pipeline state UI
- Defined `complete_task` → `route_task` ordering constraint
- Added single-blocking-call constraint per instance
- Added artifact path validation on `complete_task`
- Added "Skip" button for clarification cards
- Added "Approve with Notes" option for approval cards
- Added `request_key` for interaction request idempotency
- Added instance_token lifecycle (creation → invalidation)
- Added MCP server configuration details
- Added `wait_all` dynamic count resolution with atomic evaluation
- Added `continue_partial` downstream context format
- Added worktree reuse on crash retry
- Added configurable `base_branch` for worktrees
- Added rebase conflict handling (same as merge conflict)
- Added disk space awareness warning
- Added cancellation of pending interactions on task cancel
- Added `config_snapshot` column to tasks table
- Added `first_run_approvals` database table
- Changed `task_chains` to multi-row (one per task in chain)
- Added full system prompt injection template with all context sections
- Clarified PM agent only sees direct connections
- Added `route_task` error handling guidance in system prompts
- Added pipeline locking granularity (nodes can still be added)
- Added group selector for multi-group pipeline progress
- Added full WebSocket event schema
- Added dashboard reconnection sync endpoint
- Added complete API endpoint definitions (pipeline, interactions, presets, MCP)
- Added `route_task` deferred activation (pending + blocked_by parent)
- Added `wait_all` race condition prevention via database lock
- Added pipeline validation on goal submission

### Rev 2 (post passes 1–10)
- Moved `join_strategy` from per-edge to per-node
- Added pipeline `id` and edge `id` fields
- Changed polling to long-polling (30s)
- Unified `complete_task` (removed `submit_for_approval`)
- Added `chain_id` for revision tracking
- Added auto-migration from `routes_to`
- Added `instance_token` authentication
- Added clarification rate limit
- Added artifact path sanitization
- Added notification aggregation and bulk operations
- Added pipeline execution progress indicator
- Added browser notifications
- Added escalation options for `awaiting_human_intervention`
- Added preset versioning (snapshot model)
- Added category filtering in preset modal
- Added database tables schema

### Rev 1 (initial)
- Initial design covering agent presets, editable pipeline, approval system, and hybrid routing
