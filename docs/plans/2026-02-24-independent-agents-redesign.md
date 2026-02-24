# Independent Agents Redesign

## Problem

The current system uses rigid sequential pipelines (PM -> Researcher -> Architect -> Coder -> Tester -> Reviewer). This doesn't reflect how real software teams operate. Real teams work independently, create tasks for each other, and have multiple concurrent initiatives. The current model also lacks task hierarchy, audit trails across teams, and the ability to send work backwards (e.g., reviewer rejecting code).

## Design Decisions

These decisions were made during brainstorming:

- **Continuous agents**: Agents run as long-lived processes polling their task queue (like employees checking their inbox)
- **Rejection creates new linked tasks**: Preserves full audit trail; original task stays in "rejected" status
- **Horizontal scaling per role**: Multiple instances of the same role (e.g., 3 coders), each in its own worktree
- **Configurable human checkpoints**: Each role definition specifies which task types need human approval
- **Direct task creation**: Agents create tasks for other agents directly (no separate queue system) — the task board IS the system
- **Explicit "blocked" status**: Tasks with unresolved dependencies sit in "blocked" status and auto-transition to "pending" when dependencies clear
- **Config-driven extensibility**: New roles, routing changes, and behavior modifications via YAML files only

## Architecture Overview

### Core Concept

There are no pipelines. The task board is the single source of truth. Agents create tasks for each other based on configurable routing rules. Tasks flow through a DAG (Directed Acyclic Graph) with a group ID tying all related work together.

```
Human gives goal
  -> PM picks it up, creates PRD
  -> PM creates tasks for Architect
  -> Architect creates tech design, creates tasks for Coder
  -> Coder implements, creates tasks for Tester + Reviewer (chained)
  -> Tester verifies, Reviewer auto-unblocks
  -> Reviewer approves (done) or rejects (creates revision task back to Coder/Architect)
```

Meanwhile, the Architect can independently identify tech debt and create a separate group of tasks that flow through Coder -> Tester -> Reviewer on a parallel track.

## Task Model & Graph Structure

### Task Fields

```
Task:
  id:               "CD-001"           # auto-generated, prefixed by role
  group_id:         "FEAT-001"         # root initiative this belongs to
  parent_id:        "AR-001"           # task that created this task
  blocked_by:       ["TS-001"]         # tasks that must complete first
  title:            "Implement CSS variable system"
  description:      "..."
  task_type:        "implementation"
  priority:         "high"             # low | medium | high | critical
  assigned_to:      "coder"            # role name
  claimed_by:       "coder-1"          # specific instance (NULL until claimed)
  status:           "in_progress"      # pending | blocked | in_progress | completed | failed | rejected
  rejection_reason: null
  revision_of:      null               # if revision, points to the rejected task
  artifacts:        [...]
  created_by:       "architect-1"
  created_at:       "2026-02-24T10:30:00Z"
  started_at:       "2026-02-24T10:35:00Z"
  completed_at:     null
```

### Task Status Lifecycle

```
                 ┌──────────┐
                 │ blocked  │ (has unresolved blocked_by)
                 └────┬─────┘
                      │ (all dependencies completed)
                      v
┌─────────┐    ┌──────────┐    ┌─────────────┐    ┌───────────┐
│ (start) │───>│ pending  │───>│ in_progress │───>│ completed │
└─────────┘    └──────────┘    └──────┬──────┘    └───────────┘
                                      │
                                      ├──────────>┌──────────┐
                                      │           │  failed  │
                                      │           └──────────┘
                                      │
                                      └──────────>┌──────────┐
                                                  │ rejected │
                                                  └──────────┘
```

### Group IDs

A group represents an entire initiative from origin to completion.

| Origin | Prefix | Example |
|--------|--------|---------|
| PM creates PRD from a goal | FEAT-xxx | FEAT-001 "Add dark mode" |
| Architect identifies tech debt | DEBT-xxx | DEBT-001 "Migrate to CSS variables" |

Every downstream task carries the same group_id. This enables filtering the task board by group and rendering the full DAG for audit.

### Task Graph Example

For group FEAT-001 ("Add dark mode"):

```
FEAT-001 (group)
|
+-- PM-001 "Create PRD" (root, created by human goal input) [completed]
|   |
|   +-- AR-001 "Design theme architecture" [completed]
|   |   |
|   |   +-- CD-001 "Implement CSS variables" [completed]
|   |   |   +-- TS-001 "Test CSS variables" [completed]
|   |   |   +-- RV-001 "Review CSS variables" (blocked_by: TS-001) [completed]
|   |   |
|   |   +-- CD-002 "Implement theme toggle" [in_progress]
|   |   |   +-- TS-002 "Test theme toggle" [blocked]
|   |   |   +-- RV-002 "Review theme toggle" (blocked_by: TS-002) [blocked]
|   |   |
|   |   +-- CD-003 "Apply dark styles to components" [pending]
|   |
|   +-- AR-002 "Review theme architecture" (peer review) [completed]
```

### Cycle Detection

When creating a blocked_by dependency, walk the graph from the dependency upward. If the walk reaches the task being created, reject it.

```python
def has_cycle(task_id: str, blocked_by_id: str) -> bool:
    visited = set()
    queue = [blocked_by_id]
    while queue:
        current = queue.pop(0)
        if current == task_id:
            return True
        if current in visited:
            continue
        visited.add(current)
        deps = db.query(
            "SELECT blocked_by FROM task_dependencies "
            "WHERE task_id = ? AND resolved = 0",
            current
        )
        queue.extend(dep.blocked_by for dep in deps)
    return False
```

## Role System & Configurable Routing

### Role Definition (YAML)

Each role is a YAML file in `config/roles/`. Adding a new role = adding a file.

```yaml
# config/roles/architect.yaml
role: architect
display_name: "Architect"
prefix: "AR"
color: "#8b5cf6"
emoji: "..."

system_prompt: |
  You are a Software Architect on an AI development team.
  Your responsibilities:
  1. Create technical design documents for PRDs assigned to you
  2. Identify and document tech debt with concrete fix plans
  3. Review architecture docs created by peer architects
  4. You do NOT write implementation code

tools: [Read, Glob, Grep, Write, WebSearch]

produces: [tech_design, tech_debt, architecture_review]

accepts: [prd, architecture_review_request, rejection]

routes_to:
  - role: coder
    task_types: [implementation, bug_fix]
  - role: architect
    task_types: [architecture_review]

can_create_groups: true
group_type: "DEBT"

max_instances: 2
auto_scale:
  enabled: true
  scale_up_threshold: 4
  scale_down_idle: 20

requires_approval: [tech_design]

context_includes:
  - parent_artifact
  - root_artifact
  - sibling_summary
  - rejection_history
```

### All Default Roles

| Role | Prefix | Tools | Routes To | Accepts | Creates Groups |
|------|--------|-------|-----------|---------|----------------|
| PM | PM- | Read, Glob, Grep, WebSearch | architect | goals (human), revisions | FEAT-xxx |
| Architect | AR- | Read, Glob, Grep, Write, WebSearch | coder, architect (peer) | prd, rejections | DEBT-xxx |
| Coder | CD- | Read, Write, Edit, Bash, Glob, Grep | tester + reviewer (chained) | implementation, revisions | No |
| Tester | TS- | Read, Write, Edit, Bash, Glob, Grep | (auto-unblocks reviewer) | qa_verification | No |
| Reviewer | RV- | Read, Glob, Grep | coder, architect (rejections) | code_review | No |

### Routing Validation at Startup

The system validates:
- All routes_to targets reference existing roles
- All task_types in routes_to are listed in the target's accepts
- At least one role has can_create_groups (entry point exists)
- No unreachable roles
- Role prefixes and group prefixes are unique
- produces and routes_to task_types are consistent

### Adding a New Role

Create `config/roles/devops.yaml` with role definition. Update an existing role's `routes_to` to include the new role. Restart. No Python code changes.

## Agent Lifecycle & Task Processing

### Agent Loop

Each agent instance runs a continuous loop:

```
1. Poll: check task board for tasks where assigned_to = my_role
         AND status = "pending" AND claimed_by IS NULL
2. Claim: atomic UPDATE (only one instance wins if multiple compete)
3. Execute: load context from parent artifacts, run Claude SDK session
4. Handoff: create downstream tasks per routing config, set blocked_by
5. Complete: mark task "completed", trigger dependency resolution
6. Repeat from step 1
```

### Atomic Task Claiming

Multiple instances of the same role compete for tasks. The claim uses an atomic SQL update:

```sql
UPDATE tasks
SET status = 'in_progress', claimed_by = ?, started_at = ?
WHERE id = (
    SELECT id FROM tasks
    WHERE assigned_to = ?
      AND status = 'pending'
      AND claimed_by IS NULL
    ORDER BY
        CASE priority
            WHEN 'critical' THEN 0 WHEN 'high' THEN 1
            WHEN 'medium' THEN 2 WHEN 'low' THEN 3
        END,
        created_at ASC
    LIMIT 1
)
RETURNING *;
```

If 0 rows affected, another instance claimed it first. Move on.

### Dependency Resolution

When a task completes:

```sql
-- Mark dependencies resolved
UPDATE task_dependencies
SET resolved = 1, resolved_at = ?
WHERE blocked_by = ?completed_task_id;

-- Unblock tasks with all dependencies resolved
UPDATE tasks SET status = 'pending'
WHERE status = 'blocked'
  AND id NOT IN (
    SELECT task_id FROM task_dependencies WHERE resolved = 0
  );
```

This auto-transitions blocked tasks to pending. For example, when TS-001 completes, RV-001 moves from blocked to pending automatically.

### Context Building

When an agent picks up a task, it receives:

1. The task itself (title, description, type)
2. Group context (initiative title and description)
3. Parent task's artifact (the work that spawned this task)
4. Root artifact (original PRD or tech debt assessment)
5. Sibling context (other tasks from same parent, for awareness of parallel work)
6. Rejection history (if revision: what went wrong and why)

### Instance Scaling

Configured per role:

```yaml
max_instances: 3
auto_scale:
  enabled: true
  scale_up_threshold: 3    # spawn new instance when 3+ pending tasks
  scale_down_idle: 15       # pause instance after 15 min idle
```

## Database Schema

### Tables

```sql
CREATE TABLE groups (
    id           TEXT PRIMARY KEY,
    title        TEXT NOT NULL,
    origin       TEXT NOT NULL,
    status       TEXT DEFAULT 'active',
    created_by   TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE tasks (
    id               TEXT PRIMARY KEY,
    group_id         TEXT NOT NULL REFERENCES groups(id),
    parent_id        TEXT REFERENCES tasks(id),
    title            TEXT NOT NULL,
    description      TEXT,
    task_type        TEXT NOT NULL,
    priority         TEXT DEFAULT 'medium',
    assigned_to      TEXT NOT NULL,
    claimed_by       TEXT,
    status           TEXT DEFAULT 'pending',
    created_by       TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    started_at       TEXT,
    completed_at     TEXT,
    rejection_reason TEXT,
    revision_of      TEXT REFERENCES tasks(id)
);

CREATE TABLE task_dependencies (
    task_id    TEXT NOT NULL REFERENCES tasks(id),
    blocked_by TEXT NOT NULL REFERENCES tasks(id),
    resolved   INTEGER DEFAULT 0,
    resolved_at TEXT,
    PRIMARY KEY (task_id, blocked_by),
    CHECK (task_id != blocked_by)
);

CREATE TABLE artifacts (
    id            TEXT PRIMARY KEY,
    task_id       TEXT NOT NULL REFERENCES tasks(id),
    file_path     TEXT NOT NULL,
    artifact_type TEXT DEFAULT 'output',
    created_at    TEXT NOT NULL
);

CREATE TABLE agent_instances (
    instance_id    TEXT PRIMARY KEY,
    role           TEXT NOT NULL,
    status         TEXT DEFAULT 'idle',
    current_task   TEXT REFERENCES tasks(id),
    started_at     TEXT NOT NULL,
    last_heartbeat TEXT
);

CREATE TABLE id_sequences (
    prefix   TEXT PRIMARY KEY,
    next_val INTEGER DEFAULT 1
);

CREATE TABLE events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    group_id   TEXT,
    task_id    TEXT,
    agent_id   TEXT,
    data       TEXT,
    created_at TEXT NOT NULL
);
```

### Key Indexes

```sql
CREATE INDEX idx_tasks_assignee_status ON tasks(assigned_to, status)
    WHERE status = 'pending' AND claimed_by IS NULL;
CREATE INDEX idx_tasks_group ON tasks(group_id, status);
CREATE INDEX idx_deps_blocked ON task_dependencies(blocked_by)
    WHERE resolved = 0;
CREATE INDEX idx_tasks_parent ON tasks(parent_id);
CREATE INDEX idx_events_group ON events(group_id, created_at);
CREATE INDEX idx_events_type ON events(event_type, created_at);
```

### Task ID Generation

Atomic per-prefix auto-increment:

```sql
UPDATE id_sequences SET next_val = next_val + 1
WHERE prefix = 'CD' RETURNING next_val;
-- Returns 4 -> task ID is "CD-004"
```

## Event System

### Event Types

| Event | Payload | Dashboard Effect |
|-------|---------|-----------------|
| group.created | group_id, title, origin | Stats bar, filter dropdown |
| group.completed | group_id | Stats bar, graph view |
| task.created | task_id, group_id, assigned_to, status, parent_id | New card on board |
| task.claimed | task_id, claimed_by | Card updates with instance name |
| task.status_changed | task_id, old_status, new_status | Card moves columns |
| task.unblocked | task_id, resolved_dependency | Card: blocked -> pending |
| task.completed | task_id, artifact_id | Card moves to Completed |
| task.rejected | task_id, reason, revision_task_id | Card shows rejection + new card |
| agent.status_changed | instance_id, old_status, new_status, current_task | Agent sidebar |
| agent.scaled_up | role, new_instance_id | New agent in sidebar |
| agent.heartbeat | instance_id, timestamp | Liveness indicator |

### WebSocket Flow

All events emit through the EventBus and broadcast to connected dashboards via WebSocket. The dashboard JS listens and updates the relevant UI component based on event type.

## Dashboard UI

### Three View Modes

**Board View** (default): Kanban columns by status (Blocked, Pending, In Progress, Completed, Rejected). Each card shows task ID, title, role badge, group badge, claimed_by, blocked_by indicator.

**List View**: Flat sortable table with columns: ID, Title, Assignee, Status, Group, Blocked By, Priority, Created. Sortable and filterable.

**Graph View**: Visual DAG for a selected group. Nodes colored by role, edges show parent->child (solid) and blocked_by (dashed). Rejection paths shown as red backward edges. Progress bar shows percentage complete.

### Filters

All views share the same filter bar:

| Filter | Values | Use Case |
|--------|--------|----------|
| Group | FEAT-001, DEBT-001, All | Everything related to dark mode |
| Assignee | pm, architect, coder, tester, reviewer, All | What's on coder's plate |
| Claimed By | coder-1, coder-2, All | What's coder-1 doing |
| Type | prd, tech_design, implementation, qa, code_review, All | Show all PRDs |
| Priority | Critical, High, Medium, Low, All | What's urgent |
| Status | Blocked, Pending, In Progress, Completed, Rejected, Failed, All | What's blocked |

Filters persist in URL query params for bookmarking.

### Stats Bar

```
| Agents      | Active Tasks | Blocked | Groups     | Events |
| 6 online    | 4 tasks      | 3 tasks | 2 active   | 147    |
| 2 idle      |              |         | 1 complete |        |
```

### Agent Sidebar

Collapsible right sidebar showing each agent instance, its status, and current task (clickable). Quick actions: Chat, Pause, Resume, Kill.

## Configuration Directory Structure

```
ai-team/
+-- config/
|   +-- team.yaml                # Global settings
|   +-- roles/
|       +-- pm.yaml
|       +-- architect.yaml
|       +-- coder.yaml
|       +-- tester.yaml
|       +-- reviewer.yaml
+-- artifacts/                   # Organized by group_id/task_id
+-- data/
|   +-- ai_team.db
+-- src/ai_team/
    +-- ...
```

## Extensibility

| Action | Method | Code Changes |
|--------|--------|-------------|
| Add a new role | Create config/roles/newrole.yaml | None |
| Change routing | Edit routes_to in role YAML | None |
| Add human checkpoint | Add task type to requires_approval | None |
| Scale a role | Change max_instances in role YAML | None |
| Change agent behavior | Edit system_prompt in role YAML | None |
| Restrict tools | Edit tools list in role YAML | None |
| New group origin type | Add can_create_groups + group_type | None |
| New task type | Add to produces and target's accepts | None |

Python code changes are only needed for fundamentally new capabilities (new visualization modes, new database features, new integrations).
