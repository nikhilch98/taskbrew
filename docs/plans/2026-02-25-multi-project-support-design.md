# Multi-Project Support — Design Document

**Date:** 2026-02-25
**Status:** Approved

---

## Goal

Enable the AI Team orchestrator to manage multiple independent projects. Each project has its own directory, config, database, artifacts, and agents. Users create projects through a landing page wizard, switch between them via a dropdown, and only one project's agents run at a time.

## Key Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Runtime model | Single server, multi-project | One URL, one port, simple UX |
| Agent scope | Only active project runs agents | Saves resources, one focus at a time |
| Registry location | `~/.ai-team/projects.yaml` | Global, survives directory changes |
| First-time experience | Landing page with "Create Project" wizard | Guided onboarding, no CLI required |
| Architecture | ProjectManager layer (Approach A) | Clean separation, minimal changes to existing code |

---

## 1. Data Model & Registry

### Global Registry: `~/.ai-team/projects.yaml`

```yaml
active_project: my-saas-app
projects:
  my-saas-app:
    name: "My SaaS App"
    directory: "/Users/nikhil/projects/my-saas-app"
    created_at: "2026-02-25T10:00:00Z"
  flappy-bird:
    name: "Flappy Bird Game"
    directory: "/Users/nikhil/projects/flappy-bird"
    created_at: "2026-02-20T15:30:00Z"
```

### Per-Project Directory Structure

Each project directory follows the existing layout:

```
/path/to/project/
  config/
    team.yaml
    roles/
      pm.yaml
      coder.yaml
      ...
  data/
    taskbrew.db
  artifacts/
    <group_id>/<task_id>/<files>
```

When a new project is created, `config/` is scaffolded with a default `team.yaml` and an empty `roles/` directory. Optionally, 5 default agent role YAMLs are copied in.

---

## 2. ProjectManager Class

New file: `src/taskbrew/project_manager.py`

```
ProjectManager
  ├── registry_path: ~/.ai-team/projects.yaml
  ├── active_project: str | None
  ├── orchestrator: Orchestrator | None
  │
  ├── list_projects() → [{id, name, directory, created_at}]
  ├── create_project(name, directory, with_defaults=True) → project_id
  ├── delete_project(project_id)  # registry only, not files
  ├── activate_project(project_id) → orchestrator references
  ├── deactivate_current()  # stops agents, closes DB
  └── get_active() → {id, name, directory} | None
```

**Activation flow:**
1. If another project is active → `deactivate_current()` first
2. Read project's `config/team.yaml` and `config/roles/`
3. Open database, create task board, event bus, artifact store
4. Start agent loops
5. Update registry's `active_project`
6. Return new orchestrator references to dashboard

**Deactivation flow:**
1. Stop all agent loops gracefully (5s timeout, then force)
2. Close database connection
3. Set orchestrator to None

---

## 3. Server Startup & Migration

### New Startup Flow

```
ai-team serve
  → Create ProjectManager
  → Read ~/.ai-team/projects.yaml
  → If active_project set → activate it
  → If no projects exist → dashboard shows landing page
  → Start FastAPI server
```

### Backward Compatibility

`--project-dir /path` CLI flag still works: auto-registers the directory as a project if not already registered, and activates it.

### Auto-Migration for Existing Users

On first run, if `~/.ai-team/projects.yaml` doesn't exist but `config/team.yaml` exists in CWD:
1. Create `~/.ai-team/projects.yaml`
2. Register CWD as a project (id derived from directory name)
3. Set as `active_project`
4. User sees their existing dashboard unchanged

---

## 4. Dashboard UI Changes

### Three UI States

**State 1 — Landing Page (no projects):**
- Clean welcome page with "Create Your First Project" button
- Shown when `list_projects()` returns empty

**State 2 — Project Selector (in nav bar):**
- Dropdown in top nav on all pages (dashboard, settings, metrics)
- Shows active project name + colored dot
- Lists all projects with "Switch" action
- "New Project" option at bottom
- Brief loading state during switch

**State 3 — Normal Dashboard (project active):**
- Exactly today's UI with project selector added to nav

### Create Project Wizard

Modal with 2 steps:

**Step 1 — Identity:** Project name, directory path

**Step 2 — Setup:**
- "Start with default agents" (checked by default) — scaffolds PM, Architect, Coder, Tester, Reviewer
- "Start empty" — just scaffolds config dir, user adds agents in Settings

After creation: auto-activate, navigate to Settings (if empty) or Dashboard (if defaults).

### New API Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `GET /api/projects` | GET | List all projects from registry |
| `POST /api/projects` | POST | Create new project |
| `DELETE /api/projects/{id}` | DELETE | Remove from registry (not files) |
| `POST /api/projects/{id}/activate` | POST | Switch active project |
| `GET /api/projects/active` | GET | Get active project info |
| `POST /api/projects/active/deactivate` | POST | Stop active project |

All existing endpoints return 409 "No active project" when `orchestrator` is None.

---

## 5. Error Handling & Edge Cases

| Scenario | Behavior |
|----------|----------|
| Directory doesn't exist on create | Create it (mkdir -p) |
| Directory already has config/team.yaml | Import as-is, don't overwrite |
| Agents mid-task during project switch | Graceful stop (5s timeout, then force) |
| Project directory deleted/moved | Error toast, remove from registry |
| Registry YAML malformed | Recreate empty, show warning |
| active_project points to missing entry | Clear it, show project selector |
| Two server instances touch registry | File-level locking (fcntl) |
| Path not absolute | Reject with validation error |

---

## 6. What Changes vs. What Stays

### Changes

| Component | Change |
|-----------|--------|
| **New: ProjectManager** | `src/taskbrew/project_manager.py` |
| **New: Registry** | `~/.ai-team/projects.yaml` |
| **Modified: main.py** | Startup creates ProjectManager, auto-migration |
| **Modified: app.py** | Holds ProjectManager, 6 new endpoints, 409 guard |
| **Modified: index.html** | Landing page, project selector dropdown, create wizard |
| **Modified: settings.html** | Project selector in nav |
| **Modified: metrics.html** | Project selector in nav |
| **New: Default templates** | Scaffold YAML files for new projects |

### Unchanged

- `database.py` — already project-scoped via `db_path`
- `artifact_store.py` — already project-scoped via `base_dir`
- `agent_loop.py` — already project-scoped via `project_dir`
- `task_board.py` — operates on whichever DB is loaded
- `config_loader.py` — reads from whatever path is given
- `event_bus.py` — in-memory, per-orchestrator instance
- `instance_manager.py` — per-orchestrator instance
