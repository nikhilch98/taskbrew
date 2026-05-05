# System Agent Profile Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a per-project TaskBrew system AI profile with provider/model/reasoning settings and a locked admin prompt.

**Architecture:** Extend team configuration with `SystemAgentConfig`, reuse the existing provider-aware model catalog for defaults and normalization, and expose the profile through project creation plus team settings APIs/UI. Runtime code gets a narrow helper that builds an `AgentConfig` for future system intelligence calls without creating a visible team role.

**Tech Stack:** Python dataclasses, FastAPI/Pydantic, YAML config, pytest, vanilla dashboard JavaScript.

---

### Task 1: Config And Catalog Defaults

**Files:**
- Modify: `src/taskbrew/model_catalog.py`
- Modify: `src/taskbrew/config_loader.py`
- Test: `tests/test_config_loader.py`

- [ ] **Step 1: Write failing config tests**

Add tests asserting `load_team_config` parses:

```python
system_agent:
  provider: "codex"
  model: "gpt-5.5"
  reasoning_effort: "high"
```

and resolves defaults when missing.

- [ ] **Step 2: Run red test**

Run: `uv run pytest tests/test_config_loader.py::TestSystemAgentConfig -v`

Expected: fail because `TeamConfig.system_agent` does not exist.

- [ ] **Step 3: Implement config support**

Add `SystemAgentConfig(provider, model, reasoning_effort)` and helper functions in `model_catalog.py`:

```python
SYSTEM_AGENT_MODEL_TIER = "balanced"
def system_agent_setting(provider: str, raw: dict | None = None) -> dict[str, str]:
    model = raw.get("model") or model_for_tier(provider, SYSTEM_AGENT_MODEL_TIER)
    effort = normalize_reasoning_effort(model, raw.get("reasoning_effort"))
    return {"provider": provider, "model": model, "reasoning_effort": effort}
```

Parse `data.get("system_agent")` in `load_team_config`.

- [ ] **Step 4: Run green test**

Run: `uv run pytest tests/test_config_loader.py::TestSystemAgentConfig -v`

Expected: pass.

### Task 2: Scaffolding And Project API

**Files:**
- Modify: `src/taskbrew/project_manager.py`
- Modify: `src/taskbrew/dashboard/models.py`
- Modify: `src/taskbrew/dashboard/routers/system.py`
- Test: `tests/test_project_manager.py`
- Test: `tests/test_dashboard_api.py`

- [ ] **Step 1: Write failing tests**

Add tests for:

```python
assert team_data["system_agent"]["provider"] == "codex"
assert team_data["system_agent"]["model"] == "gpt-5.4"
assert team_data["system_agent"]["reasoning_effort"] == "medium"
```

and create-project API persistence when `system_agent` is supplied.

- [ ] **Step 2: Run red tests**

Run: `uv run pytest tests/test_project_manager.py::TestScaffolding tests/test_dashboard_api.py::test_create_project_with_system_agent_profile -v`

Expected: fail because scaffolding/API ignore `system_agent`.

- [ ] **Step 3: Implement API/scaffold support**

Add a Pydantic `SystemAgentProfile` with `provider`, `model`, and `reasoning_effort`. Thread it through `ProjectManager.create_project` to `_default_team_yaml`, writing `system_agent` into `team.yaml`.

- [ ] **Step 4: Run green tests**

Run: `uv run pytest tests/test_project_manager.py::TestScaffolding tests/test_dashboard_api.py::test_create_project_with_system_agent_profile -v`

Expected: pass.

### Task 3: Runtime Helper And Startup Validation

**Files:**
- Create: `src/taskbrew/system_agent.py`
- Modify: `src/taskbrew/main.py`
- Test: `tests/test_system_agent.py`
- Test: `tests/test_startup_validation.py`

- [ ] **Step 1: Write failing tests**

Test that `build_system_agent_config(team_config)` returns an `AgentConfig` with name `system`, locked prompt text, configured model/reasoning/provider, and no user-editable prompt input.

Add a startup validation test where no role uses Codex but `team_config.system_agent.provider == "codex"` and the Codex CLI must be checked.

- [ ] **Step 2: Run red tests**

Run: `uv run pytest tests/test_system_agent.py tests/test_startup_validation.py::test_validate_startup_checks_system_agent_provider -v`

Expected: fail because helper and startup provider inclusion do not exist.

- [ ] **Step 3: Implement helper and validation**

Create:

```python
SYSTEM_AGENT_PROMPT = (
    "You are TaskBrew's locked system administrator. You support platform-level "
    "analysis, coordination, and intelligence features across TaskBrew. You are "
    "not a user-created team role, pipeline participant, or task assignee."
)
def build_system_agent_config(team_config, project_dir=None) -> AgentConfig:
    system_agent = team_config.system_agent
    return AgentConfig(
        name="system",
        role="TaskBrew System Admin",
        system_prompt=SYSTEM_AGENT_PROMPT,
        model=system_agent.model,
        reasoning_effort=system_agent.reasoning_effort,
        cli_provider=system_agent.provider,
        cwd=project_dir,
        permission_mode="default",
        mcp_servers=team_config.mcp_servers,
    )
```

Include `team_config.system_agent.provider` in `_validate_startup` required providers.

- [ ] **Step 4: Run green tests**

Run: `uv run pytest tests/test_system_agent.py tests/test_startup_validation.py::test_validate_startup_checks_system_agent_provider -v`

Expected: pass.

### Task 4: Team Settings And Wizard UI

**Files:**
- Modify: `src/taskbrew/dashboard/static/js/dashboard-ui.js`
- Modify: `src/taskbrew/dashboard/templates/index.html`
- Modify: `src/taskbrew/dashboard/templates/settings.html`
- Modify: `src/taskbrew/dashboard/static/css/main.css`
- Test: `tests/test_create_project_single_step.py`
- Test: `tests/test_dashboard_api.py`

- [ ] **Step 1: Write failing tests**

Assert the wizard renderer contains `System AI Profile` and the submitted body contains `system_agent`. Add API GET/PUT tests for `system_agent` in team settings.

- [ ] **Step 2: Run red tests**

Run: `uv run pytest tests/test_create_project_single_step.py tests/test_dashboard_api.py::test_team_settings_system_agent_round_trip -v`

Expected: fail because UI/API team settings do not expose `system_agent`.

- [ ] **Step 3: Implement UI/API controls**

Add a system profile object to wizard state, provider/model/reasoning helpers, a `System AI Profile` section, and Team Settings controls. Persist `system_agent` in `PUT /api/settings/team` and return it from `GET /api/settings/team`.

- [ ] **Step 4: Run green tests**

Run: `uv run pytest tests/test_create_project_single_step.py tests/test_dashboard_api.py::test_team_settings_system_agent_round_trip -v`

Expected: pass.

### Task 5: Final Verification

**Files:**
- All touched files.

- [ ] **Step 1: Run focused tests**

Run: `uv run pytest tests/test_config_loader.py tests/test_project_manager.py::TestScaffolding tests/test_dashboard_api.py::test_create_project_with_system_agent_profile tests/test_dashboard_api.py::test_team_settings_system_agent_round_trip tests/test_system_agent.py tests/test_startup_validation.py::test_validate_startup_checks_system_agent_provider tests/test_create_project_single_step.py -v`

- [ ] **Step 2: Run lint**

Run: `uv run ruff check src/ tests/`

- [ ] **Step 3: Run full suite**

Run: `uv run pytest tests/ -x`

- [ ] **Step 4: Browser smoke test**

Reload `http://127.0.0.1:8421/`, verify the create-project wizard shows `System AI Profile`, provider switching updates model/reasoning defaults, and console has no new errors.
