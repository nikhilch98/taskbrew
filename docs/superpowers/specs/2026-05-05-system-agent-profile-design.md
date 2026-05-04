# System Agent Profile Design

## Summary

TaskBrew needs a per-project system AI profile for platform-level intelligence features that are not owned by an individual team role or task. This profile represents an admin TaskBrew agent: it can power smart product behavior across TaskBrew, but it is not a PM, architect, coder, verifier, chat persona, or pipeline participant.

The system agent is configured per project in `config/team.yaml`. Operators may choose its provider, model, and reasoning or thinking level. Its system prompt is locked in application code and is not editable from YAML or the dashboard.

## Goals

- Add a first-class per-project `system_agent` configuration block.
- Support provider, model, and reasoning/thinking settings using the same model catalog used for role defaults.
- Keep the system admin agent separate from user-created team roles.
- Expose the profile in project creation and team settings.
- Provide a stable runtime access point for future TaskBrew intelligence features.
- Preserve backward compatibility for existing projects that do not define `system_agent`.

## Non-Goals

- Do not create a visible role card, pipeline node, or task assignee named `system`.
- Do not allow users to edit the admin system prompt.
- Do not implement a specific smart feature in this change.
- Do not introduce global install-level defaults yet.
- Do not add new credential storage or account management.

## Configuration

New projects should scaffold this block in `config/team.yaml`. If the user does not choose a separate system profile, it should follow the selected project `cli_provider` and use that provider's system default from the model catalog:

```yaml
system_agent:
  provider: "claude"
  model: "claude-sonnet-4-6"
  reasoning_effort: "high"
```

Existing projects without this block should resolve the same default in memory:

```yaml
system_agent:
  provider: "<team cli_provider, defaulting to claude>"
  model: "claude-sonnet-4-6"
  reasoning_effort: "high"
```

Provider-specific system defaults should be centralized in the model catalog so the UI, scaffolding, and config loader agree. If a selected model does not support reasoning settings, `reasoning_effort` should be omitted or ignored.

## Runtime Model

Add a `SystemAgentConfig` dataclass to `taskbrew.config_loader` and attach it to `TeamConfig` as `system_agent`.

The locked admin prompt should live in code as a constant. It should identify the agent as TaskBrew's system administrator, restrict it to platform-level analysis and orchestration support, and make clear that it is not a user-created team member or task assignee.

Future intelligence features should access the profile through a narrow helper rather than reading raw YAML. A small helper such as `build_system_agent_config(team_config, project_dir)` can produce an `AgentConfig` with:

- `name`: `system`
- `role`: `TaskBrew System Admin`
- `system_prompt`: locked code constant
- `model`: `team_config.system_agent.model`
- `reasoning_effort`: `team_config.system_agent.reasoning_effort`
- `cli_provider`: `team_config.system_agent.provider`
- `permission_mode`: conservative default
- `mcp_servers`: built-in MCP servers when needed

This keeps future system intelligence code from duplicating provider/model wiring.

## Dashboard

The create-project wizard should add a "System AI Profile" section separate from "Default Agent Models". It should include:

- provider selector: Claude, Gemini, Codex
- model selector filtered by provider
- reasoning/thinking selector shown only when the model supports it

Team Settings should show the same controls in the Team tab. The system prompt should not be rendered as an editable text area. If we show it at all, it should be read-only explanatory text, but the first implementation can simply omit it.

Role Settings should remain unchanged: each team role can still have its own model and reasoning settings.

## API Changes

Extend `CreateProjectBody` with an optional `system_agent` object:

```json
{
  "provider": "codex",
  "model": "gpt-5.5",
  "reasoning_effort": "high"
}
```

Extend `UpdateTeamSettingsBody` with the same optional shape. `GET /api/settings/team` should return the resolved system agent profile.

Persist team settings updates into `config/team.yaml` under `system_agent`.

## Validation

Validation should be pragmatic and catalog-aware:

- unknown provider values fall back to `claude` only during default resolution, not when persisting explicit API input
- model choices are allowed as custom strings, matching existing role behavior
- reasoning values are normalized through the catalog when the model is known
- provider/model mismatch should not crash, but UI defaults should avoid creating mismatches

Startup validation should include the system provider in CLI availability checks, because future smart features may invoke it even if no role uses that provider.

## Testing

Add focused tests for:

- `load_team_config` parses `system_agent`
- missing `system_agent` gets default provider/model/reasoning
- project scaffolding writes `system_agent`
- create-project API persists requested system profile
- team settings GET/PUT round trips system profile to YAML
- startup validation checks the system provider
- dashboard model metadata still drives provider/model/reasoning selectors

Browser verification should cover:

- the create-project wizard shows "System AI Profile"
- switching providers updates system model and reasoning defaults
- Team Settings exposes the same system profile controls

## Migration

No file migration is required. Existing projects load defaults in memory. The `system_agent` block is written the next time the project is scaffolded or team settings are saved.
