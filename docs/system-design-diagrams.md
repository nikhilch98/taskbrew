# TaskBrew System Design Diagrams

This document captures the current TaskBrew architecture as Mermaid diagrams.
It is intended to be the high-level and low-level system map for developers,
operators, and future design work.

The diagrams reflect the current `main` implementation, including the
per-project system agent, backlog and review gates, work packages, package
review, and durable merge queue.

## Source Map

Primary source modules:

- Runtime entrypoint: `src/taskbrew/main.py`
- Project registry and scaffolding: `src/taskbrew/project_manager.py`
- Dashboard app and routers: `src/taskbrew/dashboard/`
- Agent runtime: `src/taskbrew/agents/`
- Orchestrator services: `src/taskbrew/orchestrator/`
- Intelligence managers: `src/taskbrew/intelligence/`
- MCP tools: `src/taskbrew/tools/`
- Model catalog and provider defaults: `src/taskbrew/model_catalog.py`
- Project configuration: `config/team.yaml`, `config/roles/*.yaml`, `pipelines/*.yaml`

---

## 1. High-Level System Context

```mermaid
flowchart LR
    Operator["Human operator"]
    Browser["Browser dashboard"]
    CLI["taskbrew CLI"]
    Daemon["TaskBrew daemon<br/>FastAPI plus background workers"]
    Project["Active project checkout"]
    Config["Project config<br/>team.yaml roles pipeline"]
    SQLite["SQLite database"]
    Artifacts["Artifacts directory"]
    Worktrees["Git worktrees"]
    Git["Git repository<br/>branches refs commits"]
    Providers["CLI providers<br/>Claude Code Gemini CLI Codex CLI"]
    MCP["MCP tool servers<br/>task-tools intelligence-tools custom"]
    Webhooks["Optional webhooks"]

    Operator --> Browser
    Operator --> CLI
    Browser <--> Daemon
    CLI --> Daemon
    Daemon <--> Project
    Project --> Config
    Daemon <--> SQLite
    Daemon <--> Artifacts
    Daemon <--> Worktrees
    Worktrees <--> Git
    Daemon <--> Providers
    Providers <--> MCP
    MCP --> Daemon
    Daemon --> Webhooks
```

TaskBrew is primarily a local orchestration daemon. The dashboard and CLI are
control surfaces; the daemon owns the project lifecycle, task board,
background workers, agent processes, system gate analysis, and branch
integration.

---

## 2. High-Level Container Architecture

```mermaid
flowchart TB
    subgraph Control["Control Plane"]
        CLI["CLI commands<br/>start serve goal status init doctor"]
        Dashboard["FastAPI dashboard<br/>HTML CSS JS APIs WebSockets"]
        ProjectManager["ProjectManager<br/>registry activation scaffolding"]
    end

    subgraph Core["Orchestrator Container"]
        Orchestrator["Orchestrator<br/>shared runtime container"]
        TaskBoard["TaskBoard<br/>groups tasks packages dependencies"]
        EventBus["EventBus<br/>in-process pub-sub"]
        DB["Database<br/>SQLite aiosqlite migrations"]
        InstanceManager["InstanceManager<br/>agent state heartbeats"]
        ArtifactStore["ArtifactStore<br/>task outputs and files"]
        Memory["Memory and context providers"]
        Intelligence["Intelligence managers<br/>quality planning security observability"]
        Plugins["PluginRegistry"]
    end

    subgraph Workers["Background Workers"]
        AgentLoops["AgentLoop instances<br/>PM Architect Coder optional roles"]
        SystemGates["SystemGateManager<br/>backlog review package review"]
        MergeBroker["MergeBroker<br/>serialized branch integration"]
        AutoScaler["AutoScaler<br/>role instance scaling"]
        Recovery["Orphan recovery<br/>stale agents stuck blockers"]
        Escalation["Escalation monitor"]
    end

    subgraph Execution["Execution Plane"]
        ProviderLayer["Provider abstraction"]
        Claude["Claude Code"]
        Gemini["Gemini CLI"]
        Codex["Codex CLI"]
        MCPTools["MCP tools"]
        WorktreeManager["WorktreeManager"]
        GitRepo["Git repository"]
    end

    subgraph Persistence["Persistent State"]
        SQLiteFile["Project SQLite DB"]
        ArtifactsDir["Artifacts"]
        WorktreeDir[".worktrees"]
        Registry["~/.taskbrew/projects.yaml"]
        ConfigFiles["config and pipeline YAML"]
    end

    CLI --> ProjectManager
    Dashboard --> ProjectManager
    ProjectManager --> Orchestrator
    Orchestrator --> TaskBoard
    Orchestrator --> EventBus
    Orchestrator --> DB
    Orchestrator --> InstanceManager
    Orchestrator --> ArtifactStore
    Orchestrator --> Memory
    Orchestrator --> Intelligence
    Orchestrator --> Plugins
    Orchestrator --> AgentLoops
    Orchestrator --> SystemGates
    Orchestrator --> MergeBroker
    Orchestrator --> AutoScaler
    Orchestrator --> Recovery
    Orchestrator --> Escalation
    AgentLoops --> ProviderLayer
    SystemGates --> ProviderLayer
    ProviderLayer --> Claude
    ProviderLayer --> Gemini
    ProviderLayer --> Codex
    ProviderLayer --> MCPTools
    AgentLoops --> WorktreeManager
    MergeBroker --> GitRepo
    WorktreeManager --> GitRepo
    DB --> SQLiteFile
    ArtifactStore --> ArtifactsDir
    WorktreeManager --> WorktreeDir
    ProjectManager --> Registry
    ProjectManager --> ConfigFiles
```

---

## 3. Runtime Topology

```mermaid
flowchart LR
    subgraph Process["TaskBrew daemon process"]
        Uvicorn["Uvicorn server"]
        App["FastAPI app"]
        WS["WebSocket manager"]
        PMgr["ProjectManager"]
        Orch["Active Orchestrator"]
        Workers["Async background tasks"]
    end

    subgraph AsyncWorkers["Async background tasks"]
        A1["AgentLoop pm-1"]
        A2["AgentLoop architect-*"]
        A3["AgentLoop coder-*"]
        SG["SystemGateManager"]
        MB["MergeBroker"]
        OrphanWorker["OrphanRecovery"]
        AS["AutoScaler"]
        EM["EscalationMonitor"]
    end

    subgraph Subprocesses["Child processes"]
        ProviderProc["Provider CLI process"]
        MCPProc["MCP stdio server"]
        GitProc["git subprocess"]
    end

    Uvicorn --> App
    App --> WS
    App --> PMgr
    PMgr --> Orch
    Orch --> Workers
    Workers --> A1
    Workers --> A2
    Workers --> A3
    Workers --> SG
    Workers --> MB
    Workers --> OrphanWorker
    Workers --> AS
    Workers --> EM
    A1 --> ProviderProc
    A2 --> ProviderProc
    A3 --> ProviderProc
    SG --> ProviderProc
    ProviderProc --> MCPProc
    MB --> GitProc
```

---

## 4. Project Startup and Activation

```mermaid
sequenceDiagram
    participant CLI as taskbrew start or serve
    participant Daemon as Daemon bootstrap
    participant PM as ProjectManager
    participant Builder as build_orchestrator
    participant Config as Config loader
    participant DB as SQLite Database
    participant Orch as Orchestrator
    participant App as FastAPI app
    participant Workers as Background workers

    CLI->>Daemon: Start foreground or daemon process
    Daemon->>PM: Load project registry
    PM->>PM: Resolve active project
    PM->>Builder: Activate project
    Builder->>Config: Load team.yaml roles pipeline
    Config-->>Builder: TeamConfig RoleConfig PipelineConfig
    Builder->>Builder: Validate routing and provider CLIs
    Builder->>DB: Open DB, create schema, run migrations
    Builder->>Orch: Construct shared services
    Builder->>Orch: Wire TaskBoard, EventBus, stores, managers
    Builder->>Orch: Wire MergeQueue, MergeBroker, SystemGateManager
    Builder->>Orch: Instantiate intelligence managers and plugins
    Builder-->>PM: Active Orchestrator
    Daemon->>App: create_app(project_manager)
    App->>App: Mount routers, templates, auth, CORS, WebSockets
    Daemon->>Workers: start_agents(active orchestrator)
    Workers->>Workers: recover orphan tasks and stuck blockers
    Workers->>Workers: start agents, gates, merge broker, monitors
    Daemon-->>CLI: Dashboard available
```

---

## 5. Goal-to-Merge Delivery Flow

```mermaid
flowchart TD
    Goal["Human submits goal"]
    Group["Create group"]
    PMTask["Create PM goal task<br/>status backlog"]
    PMBacklogGate["PM backlog intake<br/>needs_review decision"]
    PMPending["PM task pending"]
    PMExec["PM creates PRD<br/>and architect tasks"]
    ArchitectTask["Architect task<br/>status backlog then pending"]
    ArchitectBacklogGate["Architect backlog intake"]
    ArchitectExec["Architect designs work packages<br/>and coder tasks"]
    Package["Work Package<br/>visible dashboard entity"]
    CoderTask["Coder task<br/>status backlog then pending"]
    CoderBacklogGate["Coder backlog intake"]
    CoderExec["Coder runs in worktree<br/>implements tests commits"]
    TaskReviewDecision{"Explicit task review?<br/>rare high-risk override"}
    TaskReview["Task review gate<br/>system agent analysis"]
    TaskDone["Task completed"]
    PackageRecon["Package status reconcile"]
    PackageReview["Package review gate<br/>system agent analyzes package"]
    Approved{"Package approved?"}
    Revision["Create revision tasks<br/>or wait for revision"]
    MergeQueue["Queue package integration"]
    MergeBroker["MergeBroker lands branches<br/>on target branch"]
    PackageDone["Package completed"]
    PackageTerminal["Package terminal<br/>completed rejected or failed"]
    GroupCheck["Group completion check"]
    GroupDone["Group completed<br/>only after tasks packages merges"]

    Goal --> Group --> PMTask --> PMBacklogGate --> PMPending --> PMExec
    PMExec --> ArchitectTask --> ArchitectBacklogGate --> ArchitectExec
    ArchitectExec --> Package
    ArchitectExec --> CoderTask
    CoderTask --> CoderBacklogGate --> CoderExec
    CoderExec --> TaskReviewDecision
    TaskReviewDecision -- "review_scope=task" --> TaskReview
    TaskReviewDecision -- "default package-backed task" --> TaskDone
    TaskReview -- approved --> TaskDone
    TaskReview -- needs revision --> Revision
    Revision --> CoderTask
    TaskDone --> PackageRecon
    PackageRecon --> PackageReview
    PackageReview --> Approved
    Approved -- needs revision --> Revision
    Approved -- rejected --> PackageTerminal
    Approved -- approved --> MergeQueue --> MergeBroker --> PackageDone --> PackageTerminal
    PackageTerminal --> GroupCheck --> GroupDone
```

In the default work-package flow, backlog intake does **not** send every
package-backed task through task review. It records `needs_review=false` and
lets the Work Package review gate be the main semantic quality gate. Task-level
review is reserved for explicit `review_scope=task` exceptions.

---

## 6. Task State Machine

```mermaid
stateDiagram-v2
    [*] --> Backlog: create_task
    Backlog --> Pending: backlog intake complete no blockers
    Backlog --> Blocked: backlog intake complete blockers remain
    Backlog --> Failed: failed blocker detected
    Backlog --> Backlog: intake running or failed retry

    Pending --> InProgress: agent claims task
    Pending --> Blocked: dependency added
    Blocked --> Pending: dependencies resolved
    Blocked --> Failed: blocker failed or rejected

    InProgress --> Review: complete and needs_review true
    InProgress --> Completed: complete and no review needed
    InProgress --> Failed: execution failure
    InProgress --> Pending: retryable verification or orphan recovery

    Review --> Completed: system review approved
    Review --> Rejected: system review rejected or round limit reached
    Review --> Review: running failed retry or needs revision
    Review --> Rejected: required revision failed

    Completed --> [*]
    Failed --> [*]
    Rejected --> [*]
    Cancelled --> [*]
```

Notes:

- Every newly created task starts in `backlog`.
- `intended_status` stores the state that should follow backlog intake.
- The system agent may set only `needs_review`, `needs_review_reason`, and
  structured decision metadata during backlog intake. For package-backed tasks,
  the default decision is `needs_review=false` because package review is the
  primary gate.
- Review gates are analysis-only. They approve, reject, fail review, or request
  revision work. They do not patch code directly.
- A `needs_revision` outcome creates separate revision tasks. Those tasks start
  in `backlog`; the original review item remains in review until revision work
  resolves it or the review round limit rejects it.

---

## 7. Work Package State Machine

```mermaid
stateDiagram-v2
    [*] --> Pending: create_work_package
    Pending --> InProgress: attached task in progress
    Pending --> Blocked: attached tasks blocked
    Pending --> Review: all package tasks terminal and review scope active

    InProgress --> Review: implementation tasks complete
    InProgress --> Blocked: package has blockers
    Blocked --> Pending: blockers resolved
    Blocked --> Failed: unrecoverable task failure

    Review --> WaitingRevision: package review needs revision
    WaitingRevision --> Pending: revision tasks created
    Review --> Rejected: package review rejected
    Review --> Integrating: approved but branches not on target
    Review --> Completed: approved and deliverables already integrated

    Integrating --> Completed: merge queue merged or already merged
    Integrating --> Failed: integration cannot proceed

    Completed --> [*]
    Rejected --> [*]
    Failed --> [*]
```

Work packages are first-class dashboard objects. They group related tasks into
reviewable slices and prevent huge goals from waiting for one final goal-level
review.

---

## 8. Agent Task Execution Sequence

```mermaid
sequenceDiagram
    participant Loop as AgentLoop
    participant Board as TaskBoard
    participant Inst as InstanceManager
    participant WT as WorktreeManager
    participant Ctx as Context builders
    participant Runner as AgentRunner
    participant Provider as Provider adapter
    participant CLI as CLI provider
    participant MCP as MCP tools
    participant DB as SQLite
    participant Bus as EventBus

    Loop->>Board: claim_task(role, instance_id)
    Board->>DB: atomic UPDATE pending to in_progress
    Board-->>Loop: claimed task or none
    Loop->>Inst: status working and heartbeat
    Loop->>WT: create isolated worktree if role mutates files
    WT-->>Loop: worktree path and branch
    Loop->>Ctx: build task context
    Ctx->>DB: read parent artifacts, siblings, memory, conventions
    Ctx-->>Loop: full prompt context
    Loop->>Runner: run(prompt, cwd)
    Runner->>Provider: detect provider and build options
    Provider->>CLI: query with model and reasoning settings
    CLI->>MCP: call create_task, complete_task, record_check, ask_question
    MCP->>Board: dashboard API mutation
    Board->>DB: persist task board changes
    Board->>Bus: emit task events
    CLI-->>Provider: stream assistant and result messages
    Provider-->>Runner: normalized output and usage
    Runner-->>Loop: output text
    Loop->>Board: complete_task_with_output or fail_task
    Board->>DB: persist output, review status, dependencies
    Board->>Bus: emit completion or failure events
    Loop->>Inst: status idle
    Loop->>WT: cleanup worktree when appropriate
```

---

## 9. System Gate Sequence

```mermaid
sequenceDiagram
    participant SGM as SystemGateManager
    participant Board as TaskBoard
    participant DB as SQLite
    participant Analyzer as AgentRunnerSystemGateAnalyzer
    participant SystemAI as Locked system agent profile
    participant Bus as EventBus

    loop every interval
        SGM->>DB: select backlog tasks pending or stale running
        SGM->>Board: process_backlog_task(task_id)
        Board->>DB: mark backlog_intake_status running
        Board->>Bus: task.system_gate_started
        alt deterministic no-duplicate-review override
            SGM->>SGM: package-backed or planning task => needs_review=false
        else explicit task review or legacy standalone task
            SGM->>Analyzer: decide_needs_review(context)
            Analyzer->>SystemAI: run locked backlog prompt
            SystemAI-->>Analyzer: strict JSON decision
            Analyzer-->>SGM: BacklogIntakeResult
        end
        SGM->>Board: apply_backlog_intake_decision
        Board->>DB: store needs_review and reason, move to intended status
        Board->>Bus: task.system_gate_finished

        SGM->>DB: select explicit task review tasks pending or stale running
        SGM->>Analyzer: review_completed_task(context)
        Analyzer->>SystemAI: run locked review prompt
        SystemAI-->>Analyzer: strict JSON outcome
        SGM->>Board: approve, reject, fail, or create revision tasks
        Board->>DB: update task status and system_gate_runs
        Board->>Bus: task.system_gate_finished

        SGM->>DB: select package review_gates
        SGM->>Analyzer: review_completed_task(package context)
        Analyzer->>SystemAI: analyze package, tasks, branch diffs, merge queue
        SGM->>Board: approve, reject, fail, or wait revision
        Board->>DB: update work_packages and review_gates
    end
```

---

## 10. Package Review and Integration Sequence

```mermaid
sequenceDiagram
    participant Board as TaskBoard
    participant SGM as SystemGateManager
    participant Sys as System agent
    participant MQ as MergeQueue
    participant MB as MergeBroker
    participant Git as Git repository
    participant Bus as EventBus

    Board->>Board: reconcile_work_package_status(package_id)
    Board->>Board: all package tasks terminal
    Board->>Board: create or reuse review_gate
    Board->>Bus: package enters review
    SGM->>Sys: package review context
    Sys-->>SGM: approved or needs revision or rejected

    alt approved
        SGM->>Board: approve_work_package_review
        Board->>MQ: enqueue_package_integration for completed task branches
        Board->>Board: set package integrating or completed
        MB->>MQ: claim_ready
        MB->>Git: create integration worktree
        MB->>Git: merge source branch into target branch
        MB->>Git: update target ref and refresh checkout
        MB->>MQ: complete merged or already_merged
        MB->>Board: finalize integrated packages
        MB->>Bus: merge events
    else needs revision
        SGM->>Board: mark package waiting_revision
        Board->>Board: revision tasks are created by agents or follow-up flow
    else rejected
        SGM->>Board: reject_work_package_review
        Board->>Bus: package rejected
    end
```

---

## 11. Durable Merge Queue Design

```mermaid
flowchart TD
    Approval["Optional verifier approval<br/>or package approval"]
    Enqueue["MergeQueue.enqueue<br/>dedupe by source entity and branch"]
    Queued["status queued"]
    Claim["MergeBroker.claim_ready<br/>lease row"]
    Running["status running"]
    RepoLock["Repository integration lock"]
    IntegrationWT["Detached integration worktree"]
    Merge["git merge source into target"]
    Conflict{"Conflict?"}
    TargetRefresh{"Primary checkout refresh ok?"}
    Complete["status merged or already_merged"]
    Retry["status retry_pending<br/>backoff"]
    Blocked["status conflict blocked<br/>root_refresh_blocked failed"]

    Approval --> Enqueue --> Queued --> Claim --> Running --> RepoLock --> IntegrationWT --> Merge --> Conflict
    Conflict -- yes --> Blocked
    Conflict -- no --> TargetRefresh
    TargetRefresh -- yes --> Complete
    TargetRefresh -- transient failure --> Retry --> Queued
    TargetRefresh -- unsafe checkout --> Blocked
```

The broker is provider-neutral: agents perform semantic review, but TaskBrew
owns branch integration so provider sandbox differences cannot skip merges.

---

## 12. Dashboard and Realtime Update Flow

```mermaid
flowchart LR
    UI["Dashboard UI<br/>templates plus static JS"]
    HTTP["REST APIs<br/>/api/tasks /api/work-packages /api/agents"]
    WS["WebSocket<br/>/ws"]
    RouterDeps["Router dependency<br/>current orchestrator"]
    Board["TaskBoard"]
    Agents["InstanceManager"]
    EventBus["EventBus"]
    ConnMgr["ConnectionManager"]
    BrowserState["Browser board state"]

    UI --> HTTP
    UI --> WS
    HTTP --> RouterDeps
    RouterDeps --> Board
    RouterDeps --> Agents
    Board --> EventBus
    Agents --> EventBus
    EventBus --> ConnMgr
    ConnMgr --> WS
    WS --> BrowserState
    HTTP --> BrowserState
```

The browser uses REST for initial state and commands, and WebSocket events for
live updates. If WebSocket reconnects, the UI can refresh state through the
REST board endpoints.

---

## 13. Provider, Model, and MCP Wiring

```mermaid
flowchart TB
    RoleConfig["RoleConfig<br/>model tools system_prompt reasoning"]
    SystemProfile["SystemAgentConfig<br/>provider model reasoning locked prompt"]
    ModelCatalog["model_catalog.py<br/>provider model reasoning defaults"]
    AgentConfig["AgentConfig"]
    ProviderDetect["detect_provider or explicit provider"]
    Options["build_sdk_options"]
    ClaudeOpts["ClaudeAgentOptions"]
    GeminiOpts["GeminiOptions"]
    CodexOpts["CodexOptions"]
    MCPDict["MCP server config<br/>allowed tool policy role identity"]
    TaskTools["task-tools MCP<br/>create task package complete ask question"]
    IntelTools["intelligence-tools MCP"]
    DashboardAPI["Dashboard API"]

    ModelCatalog --> RoleConfig
    ModelCatalog --> SystemProfile
    RoleConfig --> AgentConfig
    SystemProfile --> AgentConfig
    AgentConfig --> ProviderDetect --> Options
    Options --> ClaudeOpts
    Options --> GeminiOpts
    Options --> CodexOpts
    Options --> MCPDict
    MCPDict --> TaskTools
    MCPDict --> IntelTools
    TaskTools --> DashboardAPI
    IntelTools --> DashboardAPI
```

Provider selection is configured per role and separately for the locked
system agent profile. Claude, Gemini, and Codex models have provider-specific
reasoning or thinking options in the model catalog.

---

## 14. Low-Level Module Dependency Map

```mermaid
flowchart TB
    Main["main.py<br/>daemon CLI orchestrator builder"]
    ProjectManager["project_manager.py<br/>registry scaffolding activation"]
    ConfigLoader["config_loader.py<br/>TeamConfig RoleConfig PipelineConfig"]
    Dashboard["dashboard/app.py<br/>FastAPI app wiring"]
    Routers["dashboard/routers/*"]
    TaskBoard["orchestrator/task_board.py"]
    Database["orchestrator/database.py"]
    EventBus["orchestrator/event_bus.py"]
    Agents["agents/agent_loop.py"]
    Provider["agents/provider.py"]
    ProviderImpl["agents/codex_cli.py<br/>agents/gemini_cli.py<br/>claude_agent_sdk"]
    SystemGates["orchestrator/system_gates.py"]
    MergeQueue["orchestrator/merge_queue.py"]
    MergeBroker["orchestrator/merge_broker.py"]
    Tools["tools/task_tools.py<br/>tools/intelligence_tools.py"]
    Intelligence["intelligence/*"]
    Worktrees["tools/worktree_manager.py"]
    Plugins["plugin_system.py"]

    Main --> ProjectManager
    Main --> ConfigLoader
    Main --> Dashboard
    Main --> TaskBoard
    Main --> Database
    Main --> EventBus
    Main --> Agents
    Main --> SystemGates
    Main --> MergeQueue
    Main --> MergeBroker
    Main --> Intelligence
    Main --> Plugins
    ProjectManager --> ConfigLoader
    Dashboard --> Routers
    Routers --> TaskBoard
    Routers --> Intelligence
    Routers --> ProjectManager
    Agents --> TaskBoard
    Agents --> Provider
    Agents --> Worktrees
    Provider --> ProviderImpl
    Provider --> Tools
    Tools --> Routers
    SystemGates --> TaskBoard
    SystemGates --> Provider
    MergeBroker --> MergeQueue
    MergeBroker --> TaskBoard
    TaskBoard --> Database
    TaskBoard --> EventBus
```

---

## 15. Core Data Model

```mermaid
erDiagram
    GROUPS {
        string id PK
        string title
        string origin
        string status
        string created_by
        string created_at
        string completed_at
    }

    MILESTONES {
        string id PK
        string group_id FK
        string title
        string status
        string review_scope
        string review_status
    }

    WORK_PACKAGES {
        string id PK
        string group_id FK
        string milestone_id FK
        string title
        string status
        string risk_level
        string review_scope
        string review_status
        string branch_name
    }

    TASKS {
        string id PK
        string group_id FK
        string work_package_id FK
        string milestone_id FK
        string parent_id FK
        string title
        string task_type
        string assigned_to
        string claimed_by
        string status
        string intended_status
        boolean needs_review
        string needs_review_reason
        string review_status
        string branch_name
        string parent_branch
        string merge_status
    }

    TASK_DEPENDENCIES {
        string task_id FK
        string blocked_by FK
        boolean resolved
        string resolved_at
    }

    REVIEW_GATES {
        string id PK
        string entity_type
        string entity_id
        string group_id FK
        string status
        string outcome
        string reason
        int review_round
        string system_gate_runs
    }

    MERGE_QUEUE {
        string id PK
        string group_id
        string parent_task_id FK
        string verifier_task_id FK
        string source_type
        string source_entity_id
        string work_package_id FK
        string source_branch
        string target_branch
        string status
    }

    ARTIFACTS {
        string id PK
        string task_id FK
        string file_path
        string artifact_type
    }

    AGENT_INSTANCES {
        string instance_id PK
        string role
        string status
        string current_task FK
        string last_heartbeat
    }

    GROUPS ||--o{ MILESTONES : contains
    GROUPS ||--o{ WORK_PACKAGES : contains
    GROUPS ||--o{ TASKS : contains
    MILESTONES ||--o{ WORK_PACKAGES : scopes
    WORK_PACKAGES ||--o{ TASKS : groups
    TASKS ||--o{ TASKS : parent
    TASKS ||--o{ TASK_DEPENDENCIES : waits
    TASKS ||--o{ TASK_DEPENDENCIES : blocks
    TASKS ||--o{ ARTIFACTS : produces
    TASKS ||--o{ MERGE_QUEUE : parent_task
    TASKS ||--o{ MERGE_QUEUE : verifier_task
    WORK_PACKAGES ||--o{ REVIEW_GATES : reviewed_by
    WORK_PACKAGES ||--o{ MERGE_QUEUE : integrates
    AGENT_INSTANCES }o--o| TASKS : works_on
```

Supporting tables not shown in the core ER diagram include:

- `events`, `task_usage`, `approvals`, `notifications`
- `webhooks`, `webhook_deliveries`
- `agent_questions`, `human_interaction_requests`
- `task_chains`, `first_run_approvals`
- intelligence tables created by v1, v2, and v3 managers
- `id_sequences`, `schema_migrations`

---

## 16. API and Router Surface

```mermaid
flowchart TB
    UI["Dashboard pages"]
    API["FastAPI application"]

    subgraph Routers["Routers"]
        Tasks["tasks.py<br/>tasks board groups packages"]
        Agents["agents.py<br/>pause resume instances"]
        System["system.py<br/>project settings health"]
        WS["ws.py<br/>board and chat WebSockets"]
        MCP["mcp_tools.py<br/>agent tool bridge"]
        Interactions["interactions.py<br/>HITL approvals questions"]
        Pipeline["pipeline_editor.py pipelines.py presets.py"]
        Intel["intelligence.py v2 v3<br/>analysis features"]
        Costs["costs.py usage.py analytics.py"]
        Git["git.py<br/>diffs commits branches"]
        Search["search.py exports.py comparison.py collaboration.py"]
    end

    subgraph Services["Shared services"]
        Orch["current orchestrator dependency"]
        Auth["AuthManager and verify_admin"]
        EventBus["EventBus"]
        Chat["ChatManager"]
    end

    UI --> API
    API --> Routers
    Routers --> Orch
    Routers --> Auth
    Routers --> EventBus
    WS --> Chat
```

Mutation-heavy routes are protected through middleware plus explicit
`verify_admin` dependencies on critical routers.

---

## 17. Background Worker Responsibilities

```mermaid
flowchart LR
    subgraph Workers["Workers"]
        AgentLoop["AgentLoop"]
        SystemGateManager["SystemGateManager"]
        MergeBroker["MergeBroker"]
        AutoScaler["AutoScaler"]
        OrphanRecovery["OrphanRecovery"]
        EscalationMonitor["EscalationMonitor"]
    end

    TaskBoard["TaskBoard"]
    DB["SQLite"]
    EventBus["EventBus"]
    InstanceManager["InstanceManager"]
    Provider["Provider CLIs"]
    Git["Git"]

    AgentLoop --> TaskBoard
    AgentLoop --> InstanceManager
    AgentLoop --> Provider
    AgentLoop --> EventBus
    SystemGateManager --> TaskBoard
    SystemGateManager --> Provider
    SystemGateManager --> EventBus
    MergeBroker --> DB
    MergeBroker --> Git
    MergeBroker --> EventBus
    AutoScaler --> TaskBoard
    AutoScaler --> InstanceManager
    OrphanRecovery --> TaskBoard
    OrphanRecovery --> InstanceManager
    EscalationMonitor --> DB
    EscalationMonitor --> EventBus
```

Worker summary:

| Worker | Purpose | Main failure mode handled |
| --- | --- | --- |
| `AgentLoop` | Claims and executes role tasks | retries transient provider errors and fails non-retryable tasks |
| `SystemGateManager` | Runs backlog, task review, and package review gates | retries failed/stale gate runs |
| `MergeBroker` | Serializes branch integration | handles conflicts, leases, stale locks, checkout refresh |
| `AutoScaler` | Adds or removes role instances | scales by queue depth and idle time |
| `OrphanRecovery` | Recovers dead agent work | resets stale `in_progress` tasks and stuck blockers |
| `EscalationMonitor` | Surfaces stuck or risky work | emits escalation events |

---

## 18. Security and Control Boundaries

```mermaid
flowchart TB
    Browser["Browser"]
    AuthMiddleware["Auth middleware<br/>env token and team tokens"]
    VerifyAdmin["verify_admin dependency"]
    CORS["CORS allowlist"]
    Headers["Security headers<br/>CSP frame denial nosniff"]
    Routers["FastAPI routers"]
    Provider["Provider CLI"]
    MCP["MCP tool subprocess"]
    ToolPolicy["Tool gating env<br/>allowed tools role identity"]
    API["TaskBrew API"]
    DB["SQLite"]

    Browser --> CORS --> AuthMiddleware --> Headers --> Routers
    Routers --> VerifyAdmin
    Provider --> MCP
    MCP --> ToolPolicy
    ToolPolicy --> API
    API --> DB
```

Key boundaries:

- Dashboard mutations are gated by auth middleware and route dependencies.
- WebSockets validate origin and auth through the WebSocket router setup.
- MCP tools receive allowed tool lists and agent role identity through
  environment variables.
- Task tools reject role impersonation when `TASKBREW_AGENT_ROLE` or
  `TASKBREW_AGENT_INSTANCE` is bound.
- File-mutating roles run in isolated worktrees by default.

---

## 19. Configuration Architecture

```mermaid
flowchart TD
    TeamYaml["config/team.yaml"]
    RolesYaml["config/roles/*.yaml"]
    ProvidersYaml["config/providers/*.yaml"]
    PipelineYaml["pipeline section or pipelines/*.yaml"]
    Presets["config/presets/*.yaml"]
    ModelCatalog["model_catalog.py"]

    Loader["config_loader.py"]
    TeamConfig["TeamConfig"]
    RoleConfig["RoleConfig"]
    PipelineConfig["PipelineConfig"]
    ProviderRegistry["ProviderRegistry"]

    TeamYaml --> Loader
    RolesYaml --> Loader
    PipelineYaml --> Loader
    ProvidersYaml --> ProviderRegistry
    Presets --> Loader
    ModelCatalog --> Loader
    Loader --> TeamConfig
    Loader --> RoleConfig
    Loader --> PipelineConfig
    ProviderRegistry --> TeamConfig
```

Important defaults:

- Default CLI provider is `codex`.
- Default Codex model for team agents and system agent is `gpt-5.5`.
- Default Codex reasoning effort is `xhigh` for the system profile and
  flagship defaults.
- The system agent prompt is locked in application code, not editable in
  project config.

---

## 20. Deployment and File-System Layout

```mermaid
flowchart TB
    Home["~/.taskbrew"]
    Registry["projects.yaml"]
    Data["data/*.db"]
    Logs["taskbrew.log"]
    PID["taskbrew.pid"]

    Project["Project root"]
    Config["config"]
    Roles["config/roles"]
    Providers["config/providers"]
    Artifacts["artifacts"]
    Worktrees[".worktrees"]
    Locks[".taskbrew/locks"]
    Source["src/taskbrew"]
    Docs["docs"]

    Home --> Registry
    Home --> Data
    Home --> Logs
    Home --> PID
    Project --> Config
    Config --> Roles
    Config --> Providers
    Project --> Artifacts
    Project --> Worktrees
    Project --> Locks
    Project --> Source
    Project --> Docs
```

---

## 21. End-to-End Component Interaction Map

```mermaid
flowchart TD
    User["User"]
    UI["Dashboard or CLI"]
    PMgr["ProjectManager"]
    Orch["Orchestrator"]
    Board["TaskBoard"]
    DB["SQLite"]
    Bus["EventBus"]
    WS["WebSocket broadcast"]
    Agents["Agent loops"]
    Provider["Provider adapter"]
    CLIs["Claude Gemini Codex"]
    MCP["MCP tools"]
    Gates["System gates"]
    Packages["Work packages"]
    Merge["Merge queue and broker"]
    Git["Git target branch"]

    User --> UI --> PMgr --> Orch
    Orch --> Board --> DB
    Board --> Bus --> WS --> UI
    Orch --> Agents --> Provider --> CLIs
    CLIs --> MCP --> Board
    Orch --> Gates --> Provider
    Gates --> Board
    Board --> Packages
    Packages --> Merge --> Git
    Merge --> Board
    Merge --> Bus
```

This is the compressed mental model:

1. The user controls projects and goals through dashboard or CLI.
2. The active project owns one orchestrator.
3. The orchestrator owns shared services and background workers.
4. Agents and the system agent mutate the board only through controlled APIs.
5. The task board persists state, emits events, and reconciles dependencies.
6. Work packages define reviewable delivery slices.
7. The merge broker, not provider agents, lands approved branches.
