# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2026-02-27

### Added
- Multi-agent orchestration with PM, Architect, Coder, and Verifier roles
- Claude Code and Gemini CLI provider support
- Config-driven MCP tool server registration
- Provider extensibility via YAML config and Python plugin interface
- Hybrid agent routing (open discovery + restricted mode)
- Task guardrails (depth limits, group limits, rejection cycle detection)
- Plugin system with startup hooks and event bus integration
- Web dashboard with real-time task monitoring
- Multi-project support with per-project database isolation
- `taskbrew init` command for project scaffolding
- `taskbrew doctor` command for system diagnostics
- Fail-fast startup validation with actionable error messages
- MIT license for open-source distribution

### Infrastructure
- Comprehensive test suite (1250+ tests)
- SQLite-based task persistence
- FastAPI dashboard with WebSocket updates
- Git worktree isolation for concurrent agent work
