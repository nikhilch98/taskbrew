# Contributing to taskbrew

Thank you for your interest in contributing to taskbrew! This guide will help you
get started with development, testing, and submitting changes.

## Development Setup

```bash
git clone https://github.com/nikhilchatragadda/taskbrew.git
cd taskbrew
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Requires Python 3.10 or later.

## Running Tests

```bash
# Run full suite, stop on first failure
pytest tests/ -x

# Verbose output with short tracebacks
pytest tests/ -x -v --tb=short

# Run a single test file
pytest tests/test_specific.py -v
```

## Code Style

- **Type hints** -- use Python 3.10+ style annotations on all public functions.
- **Line length** -- 100 characters max.
- **Formatter / linter** -- [ruff](https://docs.astral.sh/ruff/).

Run the linter before every commit:

```bash
ruff check src/ tests/ --fix
```

## Branch Naming

| Prefix | Purpose |
|---|---|
| `feat/description` | New features |
| `fix/description` | Bug fixes |
| `docs/description` | Documentation changes |
| `refactor/description` | Refactoring (no behaviour change) |

## Commit Messages

We follow [Conventional Commits](https://www.conventionalcommits.org/):

```
feat: add new feature
fix: resolve bug
docs: update documentation
test: add tests
chore: maintenance
```

## Pull Request Checklist

Before opening a PR, make sure:

- [ ] Tests pass (`pytest tests/ -x`)
- [ ] Lint is clean (`ruff check src/ tests/`)
- [ ] New features have corresponding tests
- [ ] Documentation is updated if needed

## Extension Points

ai-team is designed to be extended in several ways. Here are the main places
where contributions are most welcome:

### New Roles

Add a YAML file under `config/roles/`. Each role defines system prompts,
allowed tools, and behavioural constraints. See existing roles (`pm.yaml`,
`architect.yaml`, `coder.yaml`, `verifier.yaml`) for the expected schema.

### New Providers

Add a provider config under `config/providers/` (see `claude.yaml` and
`gemini.yaml` for examples) or extend `src/taskbrew/agents/provider_base.py`
with a new Python provider class.

### New MCP Tools

Register additional MCP tool servers in your project's `team.yaml` under the
`mcp_servers` section. Each entry specifies the server command and arguments
that the orchestrator will launch.

### Plugins

Drop plugin modules into the `plugins/` directory. Plugins can register startup
hooks and subscribe to the internal event bus. See `plugins/README.md` for
details.

## Reporting Issues

Open an issue on [GitHub Issues](https://github.com/nikhilchatragadda/ai-team/issues)
with a clear description of the problem, steps to reproduce, and your
environment details (Python version, OS, ai-team version).

## License

By contributing, you agree that your contributions will be licensed under the
MIT License that covers this project.
