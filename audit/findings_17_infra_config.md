# Audit Findings — Infrastructure & Config

**Files reviewed:**
- `Dockerfile`
- `docker-compose.yaml`
- `.github/workflows/ci.yml`
- `.githooks/pre-commit`
- `.githooks/pre-merge-commit`
- `scripts/check-merge-scope.sh`
- `Makefile`
- `pyproject.toml`
- `.env.example`
- `plugins/README.md`
- `pipelines/bugfix.yaml`, `pipelines/code_review.yaml`, `pipelines/feature_dev.yaml`
- `config/team.yaml`
- `config/providers/{claude,gemini}.yaml`
- `config/roles/{architect,coder,pm,verifier}.yaml`
- `config/presets/*.yaml` (24 files, skim-audited)
- Cross-check: `src/taskbrew/__init__.py`, `src/taskbrew/main.py`

**Reviewer:** audit-agent-17

---

## Finding 1 — Dockerfile references files that do not exist; build is broken
- **Severity:** CRITICAL
- **Category:** correctness-bug
- **Location:** `Dockerfile:8` (`COPY pyproject.toml setup.cfg setup.py ./`)
- **Finding:** The builder stage copies `setup.cfg` and `setup.py`, but neither file exists in the repository (confirmed by filesystem scan). The `COPY` directive will fail immediately, so `docker build` / `docker compose up` cannot succeed against a clean checkout.
- **Impact:** Containerized deployment is impossible for v1.0.6 — anyone following the published README's `docker compose up -d` instructions will hit an immediate, unrecoverable build failure.
- **Fix:** Remove `setup.cfg setup.py` from the `COPY`; move the `COPY . .` before `pip install --no-cache-dir .` so the full source tree is present when hatchling builds (`pyproject.toml` alone is not enough — hatchling needs `src/taskbrew/` to build the wheel).

## Finding 2 — `pyproject.toml` version 1.0.6 vs `__init__.py` version 0.1.0
- **Severity:** HIGH
- **Category:** docs-mismatch
- **Location:** `pyproject.toml:7` (`version = "1.0.6"`) vs `src/taskbrew/__init__.py:3` (`__version__ = "0.1.0"`)
- **Finding:** The PyPI-published version is 1.0.6, but the package-exposed `__version__` constant is still `0.1.0`. Any programmatic consumer (`importlib.metadata`, bug report, `--version` flag) will see inconsistent values.
- **Impact:** Undermines release hygiene and makes field bug triage unreliable (user-reported `__version__` will not match their `pip show` version).
- **Fix:** Either bump `__init__.py` to `1.0.6` as part of release tooling, or replace the string literal with `importlib.metadata.version("taskbrew")`.

## Finding 3 — `.githooks/pre-commit` is a no-op (wrong hook signature)
- **Severity:** HIGH
- **Category:** correctness-bug
- **Location:** `.githooks/pre-commit:31-35`
- **Finding:** The script reads `COMMIT_MSG_FILE="${1:-}"` and exits 0 when it's empty or missing. Git's `pre-commit` hook is invoked with **no arguments** — the commit-message file path is only passed to the `commit-msg` hook. As written, this hook's validation path is never reached; every commit silently passes the task-id gate.
- **Impact:** The declared guardrail against cross-task commit contamination (the rationale for the hook) is non-functional. Devs and CI believe they are protected; they are not.
- **Fix:** Rename the file to `.githooks/commit-msg` (and update `setup-hooks` / docs), or reimplement as a true pre-commit that reads the prepared message from `.git/COMMIT_EDITMSG`.

## Finding 4 — GitHub Actions pinned by floating tag, not by SHA
- **Severity:** HIGH
- **Category:** supply-chain
- **Location:** `.github/workflows/ci.yml:24, 27, 32` (`actions/checkout@v4`, `actions/setup-python@v5`, `actions/cache@v4`)
- **Finding:** All third-party Actions are pinned to major-version tags. Tags are mutable — a compromised or malicious maintainer can retag `v4` to point at code that exfiltrates `GITHUB_TOKEN` or injects backdoors into the distribution build.
- **Impact:** Classic supply-chain attack surface (tj-actions/changed-files Mar-2025 pattern). Even with no secrets in this workflow today, any future secret added is immediately at risk.
- **Fix:** Pin each action to a full 40-char commit SHA with the tag as a trailing comment (e.g. `uses: actions/checkout@b4ffde6...  # v4.2.2`), and adopt Dependabot `package-ecosystem: github-actions` for managed bumps.

## Finding 5 — Git hooks are advisory only; no automatic install
- **Severity:** HIGH
- **Category:** config-bug
- **Location:** `Makefile:14-19` (`setup-hooks` target); `.githooks/` has no auto-registration
- **Finding:** The hooks only activate after a developer runs `make setup-hooks` (which sets `core.hooksPath`). There is no CI job verifying the hooks would have blocked a given merge, and no repo-level mechanism (e.g. `pre-commit` framework, husky-equivalent) to enforce them. A contributor who never runs `make setup-hooks` bypasses both the task-id check and the merge-scope check entirely.
- **Impact:** The scope guard described in AR-015 §4.6 (block feature merges >25 files, fix merges >10 files) is enforceable only on cooperating laptops; a non-cooperating or automated merge bypasses it silently.
- **Fix:** Mirror `scripts/check-merge-scope.sh` into a CI job on `pull_request` (not just `pull_request_target`) that fails the check-run when thresholds are exceeded. Keep the git hook as a fast local aid.

## Finding 6 — CI test matrix omits Python 3.11 despite declared support
- **Severity:** MEDIUM
- **Category:** config-bug
- **Location:** `.github/workflows/ci.yml:20` (`python-version: ["3.10", "3.12"]`) vs `pyproject.toml:18-20` (classifiers list 3.10/3.11/3.12)
- **Finding:** Project classifiers advertise 3.10, 3.11, and 3.12, and `requires-python = ">=3.10"`. CI only tests 3.10 and 3.12 — 3.11-specific regressions (e.g. `asyncio` semantics, `tomllib` fallback paths) are never caught before release.
- **Impact:** 1/3 of advertised runtime matrix is untested; users on 3.11 (default in many LTS distros through 2027) may hit release-breaking bugs.
- **Fix:** Add `"3.11"` to the matrix. Consider also adding `3.13` if the project intends to keep up with current CPython.

## Finding 7 — Pipelines reference agents that do not exist
- **Severity:** MEDIUM
- **Category:** correctness-bug
- **Location:** `pipelines/bugfix.yaml` (agents `researcher`, `tester`, `reviewer`), `pipelines/feature_dev.yaml` (same plus unmapped `tester`/`reviewer`), `pipelines/code_review.yaml` (`reviewer`)
- **Finding:** Pipeline step `agent:` values name `researcher`, `tester`, and `reviewer`, but `config/roles/` defines only `architect`, `coder`, `pm`, `verifier`. The agent presets directory includes `research_agent`, `qa_tester_*`, and `architect_reviewer` — names that differ from what the pipelines cite. There is no role or preset literally named `researcher`, `tester`, or `reviewer`.
- **Impact:** Any user who selects one of these shipped pipelines will have it fail to bind an agent for three of the four (feature_dev) or three of four (bugfix) steps, silently stalling the workflow or producing misleading errors.
- **Fix:** Rename pipeline steps to match existing role/preset IDs (`research_agent`, `qa_tester_integration`, `architect_reviewer`) or ship the referenced presets with those exact names.

## Finding 8 — Dockerfile builder stage pip-installs before source is copied
- **Severity:** MEDIUM
- **Category:** correctness-bug
- **Location:** `Dockerfile:8-11`
- **Finding:** `RUN pip install --no-cache-dir .` runs immediately after copying only `pyproject.toml` (and the two missing files). Hatchling requires `src/taskbrew/` to exist at build time to produce the wheel. Even if Finding 1 is fixed, this ordering means hatchling has no package content to install. The subsequent `COPY . .` is dead weight — nothing is installed against it.
- **Impact:** Even a fixed `COPY` line (Finding 1) leaves the runtime stage without the `taskbrew` distribution installed; `python -m taskbrew.main serve` will fail on missing `importlib.metadata` entries and any code that reads its own version.
- **Fix:** Reorder to `COPY . .` before `pip install --no-cache-dir .`, or switch to a wheel-build pattern (`pip wheel . -w /wheels` then install from wheels in the final stage).

## Finding 9 — Dependencies pinned with lower-bounds only; no upper caps
- **Severity:** MEDIUM
- **Category:** supply-chain
- **Location:** `pyproject.toml:22-32`
- **Finding:** Every runtime dep uses `>=` with no upper bound (`fastapi>=0.115`, `sqlalchemy>=2.0`, `uvicorn[standard]>=0.34`, `pyyaml>=6.0`, etc.). SQLAlchemy 3.0 and FastAPI 1.0 (both imminent) will ship breaking changes. A `uv.lock` exists in the repo but is not consulted by `pip install -e ".[dev]"` in CI.
- **Impact:** A future transitive upgrade can silently break a user's install of `taskbrew==1.0.6` with no corresponding source change.
- **Fix:** Add conservative upper bounds (`fastapi>=0.115,<1.0`, `sqlalchemy>=2.0,<3.0`) and switch CI to `uv sync --frozen` (or `pip install` against a constraints file exported from `uv.lock`) so reproducible builds are verified.

## Finding 10 — Docker container runs without hardening flags
- **Severity:** MEDIUM
- **Category:** security
- **Location:** `docker-compose.yaml:12-63`, `Dockerfile:27-37`
- **Finding:** Good: `USER appuser` is set, volumes are `chown`ed. Missing: no `read_only: true`, no `tmpfs`/`/tmp` binding, no `cap_drop: [ALL]`, no `security_opt: ["no-new-privileges:true"]`, no user-namespace remap. Container also re-runs `git init && git add -A && git commit` at build (line 31-32) with `--allow-empty` fallback — but no `git config user.email/name`, so on some distros this fails silently.
- **Impact:** A plugin-loaded RCE (plugins auto-run on startup, see `plugins/README.md`) would execute with full container capabilities and write access to `/app`.
- **Fix:** Add `cap_drop: [ALL]`, `security_opt: ["no-new-privileges:true"]`, `read_only: true` with explicit writable volumes, and configure git identity in the builder stage.

## Finding 11 — `approval_mode: auto` on Bash-using presets with no tool-arg guardrails
- **Severity:** MEDIUM
- **Category:** security
- **Location:** `config/presets/coder_be.yaml`, `config/presets/coder_fe.yaml`, `config/presets/coder_infra.yaml`, `config/presets/coder_swift.yaml`, `config/presets/coder_flutter.yaml`, `config/presets/devops_engineer.yaml`, `config/presets/architect_reviewer.yaml` (every preset with `tools: Bash` and `approval_mode: auto`)
- **Finding:** These presets grant unrestricted `Bash` access with `approval_mode: auto`, meaning the agent can run arbitrary shell commands without human approval. There is no allow-list of Bash patterns (e.g. `Bash(git*:Read)` style), no `deny` for `rm -rf`, `curl | sh`, `ssh-keygen`, etc. `uses_worktree: true` limits fs scope but not network / process / secrets exfiltration.
- **Impact:** A prompt-injection payload in an issue/PR comment fed to the agent can execute arbitrary shell from the CI runner or developer laptop.
- **Fix:** Tighten each Bash-capable preset to a tool allow-list (`Bash(git:*)`, `Bash(pytest:*)`, `Bash(ruff:*)`) and/or flip `approval_mode: first_run` until such patterns are reviewed. Document the hardening in `plugins/README.md`.

## Finding 12 — Docker HEALTHCHECK has no timeout on the HTTP probe
- **Severity:** LOW
- **Category:** config-bug
- **Location:** `Dockerfile:39-40`, `docker-compose.yaml:18-23`
- **Finding:** `urllib.request.urlopen('http://localhost:8420/api/health')` has no `timeout=` argument. The outer `timeout: 5s` on the HEALTHCHECK directive covers wall-time, but Python `urlopen` with no timeout can block on TCP handshake long enough to mask the root cause. Also, `/api/health` response body is not validated — a 200 with a failure payload passes.
- **Impact:** Slow-hang bugs can lead to false-healthy containers restarting in unexpected windows.
- **Fix:** `urllib.request.urlopen('http://localhost:8420/api/health', timeout=3)` and check `.status == 200` plus a non-empty JSON body.

## Finding 13 — Role YAML schema diverges from preset YAML schema
- **Severity:** LOW
- **Category:** docs-mismatch
- **Location:** `config/roles/*.yaml` vs `config/presets/*.yaml`
- **Finding:** Roles use `tools: [Read, Glob, ...]` (flow list), `model:`, `routes_to:`. Presets use `tools:\n  - Read\n  - Glob` (block list), `default_model:`, and no `routes_to:` (they rely on pipeline edges, per commit 176b059). Both files live in `config/` but are parsed by different loaders. There is no schema file (JSON Schema / Pydantic model exported) and no CI `yamllint` job.
- **Impact:** Forking users who base a new preset on a role (or vice versa) silently produce malformed configs; bugs only surface at agent-spawn time.
- **Fix:** Define a versioned schema for each kind, run `yamllint` + a schema-validator in CI, and add a doc note distinguishing `role` vs `preset` in `config/README.md`.

## Finding 14 — `.env.example` under-documented vs. vars actually used
- **Severity:** LOW
- **Category:** docs-mismatch
- **Location:** `.env.example` (5 lines: `LOG_LEVEL`, `LOG_FORMAT`, `AUTH_ENABLED`, `CORS_ORIGINS`)
- **Finding:** `docker-compose.yaml` references `TASKBREW_API_URL`, `TASKBREW_DB_PATH`, `AUTH_ENABLED`, `CORS_ORIGINS`. `.env.example` omits `TASKBREW_API_URL` and `TASKBREW_DB_PATH`, and has no comments explaining each var's valid values / defaults. (Good: no secrets leak — the file contains no tokens.)
- **Impact:** First-run users following the compose README miss two tunables; downstream deployments default unexpectedly.
- **Fix:** Expand `.env.example` to mirror every variable read from `docker-compose.yaml` (and `main.py`), each with a one-line comment.

## Finding 15 — docker-compose `container_name` and absence of resource hardening
- **Severity:** LOW
- **Category:** config-bug
- **Location:** `docker-compose.yaml:13` (`container_name: taskbrew`)
- **Finding:** Hard-coding `container_name` prevents horizontal scaling (`docker compose up --scale taskbrew=3` fails with name conflict). `deploy.resources` is set but Compose only honors it under Swarm — on plain Compose V2, these limits are ignored unless `--compatibility` is passed. Logging rotation is configured (10 MB × 3) which is good.
- **Impact:** Scaling claims in docs don't hold; resource limits give false assurance.
- **Fix:** Drop `container_name`, or move limits to the top-level `x-taskbrew` anchor plus documented `--compatibility` usage; add a comment explaining the Swarm-vs-Compose behavior.

---

## Systemic issues observed across this slice
- **Pinning discipline is uneven:** runtime deps unbounded on the upper end (Finding 9), GitHub Actions pinned by mutable tags (Finding 4), base image pinned at minor (`python:3.12-slim` — acceptable), but no `uv.lock` enforcement in CI means the lockfile is decorative.
- **CI rigor is below Google-class bar:** no 3.11 in matrix (Finding 6), no SHA-pinned actions (Finding 4), no yaml-schema validation for roles/presets (Finding 13), scope-guard script lives only in a hook and not in CI (Finding 5). Bandit and ruff run without `continue-on-error` — the one bright spot.
- **Hook adoption is opt-in and partially broken:** `setup-hooks` must be run by every dev, the pre-commit hook is effectively a no-op (Finding 3), and the pre-merge-commit depends on `main` existing locally (handled gracefully) but is entirely bypassable with `git merge --no-verify`.
- **Preset schema drift and pipeline/role naming mismatch:** pipelines cite `researcher`/`tester`/`reviewer` which exist nowhere (Finding 7); 24 presets use a slightly different schema than 4 roles (Finding 13); Bash-tool presets default to `approval_mode: auto` without any tool-arg allow-list (Finding 11).
- **Docker pipeline is fundamentally broken for the published version:** Findings 1 and 8 together mean the published 1.0.6 cannot be built from source with the documented `docker compose up` workflow. This is the single highest-impact issue in the slice — the README promises Docker and Docker is broken.
