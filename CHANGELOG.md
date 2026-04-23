# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Security
- Close all 15 CRITICAL findings from the Google-class OSS audit
  (see `AUDIT_REPORT.md`): default-on auth, atomic task claiming,
  idempotent migrations, webhook SSRF with DNS + IP pin, CSV formula-
  injection guard on exports, Gemini CLI wall-clock timeout, worktree
  path-traversal and prune safety, MCP bearer-token verification, and
  more.

### Fixed
- Single source of truth for the package version. `taskbrew.__version__`
  now reads from `importlib.metadata`; the FastAPI dashboard uses the
  same value. No more disagreement between pyproject.toml (1.0.6),
  `__init__.py` (was 0.1.0), and `dashboard/app.py` (was 2.0.0).
- `.githooks/pre-commit` was a silent no-op (wrong hook signature).
  Moved the branch/task-ID check to `.githooks/commit-msg` where `$1`
  is actually the prepared message file.
- Dockerfile referenced missing `setup.cfg`/`setup.py` and ran
  `pip install .` before copying the source tree. Rewrote COPY order
  and added `.dockerignore` so the container builds from a clean
  checkout and excludes internal review artifacts.

### Changed
- `AUTH_ENABLED` now defaults to `true` (fail-closed). Unset env emits
  a WARNING and defaults to on; `.env.example` and `docker-compose.yaml`
  both ship with `AUTH_ENABLED=true`.
- `VerificationManager.evaluate_gate` is renamed to
  `record_gate_claim`, with a `trusted: bool = False` parameter and an
  audit-trail WARNING log when metrics are claim-only (not
  independently verified). `evaluate_gate` remains as a deprecated
  alias.

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
