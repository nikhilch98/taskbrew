# Repository Guidelines

## Project Structure & Module Organization

TaskBrew uses a Python `src/` layout. Core package code lives under `src/taskbrew/`: `agents/` manages agent providers and loops, `orchestrator/` owns persistence and coordination, `dashboard/` contains the FastAPI app, routers, templates, and static assets, `intelligence/` contains planning and quality modules, and `tools/` holds shared tool integrations. Tests are in `tests/` with file names matching `test_*.py`. Default runtime configuration lives in `config/`, reusable pipeline definitions in `pipelines/`, plugin examples in `plugins/`, and design or implementation notes in `docs/`.

## Build, Test, and Development Commands

Create a local development environment with:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Use `taskbrew doctor` to verify Python, CLI dependencies, and config files. Run the app with `taskbrew start` or `taskbrew serve --project-dir <path>`. Run tests with `pytest tests/ -x` for the full suite stopping at the first failure, or `pytest tests/test_config_loader.py -v` for one file. Check style with `ruff check src/ tests/`; use `ruff check src/ tests/ --fix` for safe automatic fixes. Docker users can run `docker compose up -d`.

## Coding Style & Naming Conventions

Target Python 3.10+. Keep lines at or below 100 characters, use type hints for public functions, and follow Ruff diagnostics. Prefer clear module names that match the subsystem being changed. Use `snake_case` for functions, variables, and modules; `PascalCase` for classes; and `UPPER_SNAKE_CASE` for constants. Keep dashboard assets grouped by type under `src/taskbrew/dashboard/static/{css,js}` and templates under `src/taskbrew/dashboard/templates/`.

## Testing Guidelines

Pytest is the project test framework, with `pytest-asyncio` enabled in auto mode. Add or update tests beside the existing suite using `tests/test_<feature>.py` naming. For router or integration changes, cover the API behavior with focused tests; for orchestration or database changes, include edge cases and failure paths. Coverage checks can be run with `pytest tests/ --cov=taskbrew --cov-report=term-missing`.

## Commit & Pull Request Guidelines

Git history follows Conventional Commits, for example `feat: add provider config` or `fix: prevent duplicate chat sessions`. Branches should use prefixes such as `feat/`, `fix/`, `docs/`, or `refactor/`. Before opening a PR, run the relevant pytest target and Ruff, update docs when behavior changes, and include a clear description, linked issues when applicable, and screenshots for dashboard UI changes.

## Configuration & Extension Notes

Roles are YAML files under `config/roles/`, provider defaults under `config/providers/`, and agent presets under `config/presets/`. New plugins belong in `plugins/`; follow `plugins/README.md` for startup hooks and event bus integration.
