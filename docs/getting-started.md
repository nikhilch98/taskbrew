# Getting Started

This guide walks you through installing taskbrew, initializing a project,
and submitting your first task.

## Prerequisites

- **Python 3.10+** (check with `python --version`)
- **At least one CLI agent installed**:
  - Claude Code: `npm install -g @anthropic-ai/claude-code`
  - Gemini CLI: `npm install -g @google/gemini-cli`
- **An API key** for your chosen provider:
  - Claude: `ANTHROPIC_API_KEY`
  - Gemini: `GOOGLE_API_KEY`

## Installation

### From PyPI

```bash
pip install taskbrew
```

### Development install

```bash
git clone https://github.com/nikhilchatragadda/taskbrew.git
cd taskbrew
pip install -e ".[dev]"
```

After installation the `taskbrew` CLI is available in your PATH.

## Initialize a project

Run `taskbrew init` inside any directory to scaffold the configuration:

```bash
mkdir my-project && cd my-project
taskbrew init --name "My Project"
```

This creates:

```
my-project/
  config/
    team.yaml          # Team-level settings (database, dashboard, providers)
    roles/
      pm.yaml          # Default Project Manager role
    providers/         # Provider YAML definitions (optional)
  plugins/             # Custom plugins directory
  .env.example         # Template for required environment variables
```

You can choose a different default provider with the `--provider` flag:

```bash
taskbrew init --name "My Project" --provider gemini
```

## Configure your team

Open `config/team.yaml` and review the defaults. The key fields are:

```yaml
team_name: "My Project"

database:
  path: "~/.taskbrew/data/my-project.db"

dashboard:
  host: "0.0.0.0"
  port: 8420

artifacts:
  base_dir: "artifacts"

cli_provider: "claude"   # or "gemini"
```

### Add roles

Roles live in `config/roles/` as individual YAML files. The `init` command
creates a default PM role. A typical team has four roles:

| Role       | Prefix | Responsibility                        |
|------------|--------|---------------------------------------|
| pm         | PM     | Decompose goals into PRDs             |
| architect  | AR     | Technical designs, coder task creation|
| coder      | CD     | Implementation on feature branches    |
| verifier   | VR     | Testing, code review, merge to main   |

See [configuration.md](configuration.md) for a full reference of role fields.

### Set your API key

Copy `.env.example` to `.env` and fill in your key:

```bash
cp .env.example .env
# Edit .env and set ANTHROPIC_API_KEY=sk-ant-...
```

Or export directly:

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
```

## System diagnostics

Before starting, verify your setup with the built-in doctor command:

```bash
taskbrew doctor
```

Example output:

```
AI Team Doctor

Checking system requirements...

  [OK] Python 3.12.0
  [OK] Claude CLI found: /usr/local/bin/claude
  [WARN] Gemini CLI not found (install: npm install -g @google/gemini-cli)
  [OK] ANTHROPIC_API_KEY is set
  [WARN] GOOGLE_API_KEY not set
  [OK] config/team.yaml found
  [OK] 4 role(s) found in config/roles/

All checks passed!
```

The doctor checks:

- Python version (3.10+ required)
- CLI binaries on PATH (claude, gemini)
- API keys in environment
- Configuration files in the current directory

## Start the server

Launch the orchestrator with:

```bash
taskbrew start
```

Or equivalently:

```bash
taskbrew serve --project-dir /path/to/my-project
```

This starts:

1. **The dashboard** -- a web UI at `http://localhost:8420`
2. **Agent loops** -- one per role instance, polling for tasks
3. **Background services** -- orphan recovery, escalation monitoring, auto-scaling

Open `http://localhost:8420` in your browser to access the dashboard.

## Submit your first task

### Via the dashboard

1. Open `http://localhost:8420`
2. Use the task submission form to enter a goal title and description
3. The PM agent will pick it up, decompose it, and delegate to the team

### Via the CLI

```bash
taskbrew goal "Add user authentication" \
  --description "Implement JWT-based authentication with login/logout endpoints"
```

### Via the API

```bash
curl -X POST http://localhost:8420/api/tasks \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Create PRD: Add user authentication",
    "description": "Implement JWT-based auth with login/logout endpoints",
    "task_type": "goal",
    "assigned_to": "pm",
    "priority": "high"
  }'
```

### What happens next

1. The **PM** claims the goal, reads the codebase, and creates architect tasks
2. The **Architect** creates technical designs and breaks them into coder tasks
3. The **Coder** implements each task on an isolated git worktree branch
4. The **Verifier** reviews code, runs tests, and merges approved branches

You can monitor progress in real time through the dashboard or by running:

```bash
taskbrew status
```

## Stopping the server

Press `Ctrl+C` in the terminal. The orchestrator performs a graceful shutdown:

1. Signals all agent loops to stop
2. Waits up to 30 seconds for running tasks to finish
3. Cleans up git worktrees
4. Closes the database connection

## Next steps

- [Configuration reference](configuration.md) -- every field in team.yaml and role YAML
- [Extending taskbrew](extending.md) -- add roles, providers, plugins, and MCP tools
- [Architecture overview](architecture.md) -- how the system works internally
