# Audit Findings — Core Bootstrap & Auth

**Files reviewed:**
- src/taskbrew/__init__.py
- src/taskbrew/main.py
- src/taskbrew/auth.py
- src/taskbrew/config.py
- src/taskbrew/config_loader.py
- src/taskbrew/logging_config.py
- src/taskbrew/plugin_system.py
- src/taskbrew/project_manager.py
- pyproject.toml (for version cross-check)

**Reviewer:** audit-agent-01

---

## Finding 1 — Token comparison is not constant-time (timing attack)
- **Severity:** HIGH
- **Category:** security
- **Location:** src/taskbrew/auth.py:200 (inside `AuthManager.verify`, `if self._hash_token(token) in self._tokens:`)
- **Finding:** Authentication hashes the submitted token with SHA-256 and then checks membership in a Python `set` via `in`. Python's set/string `__eq__` short-circuits on the first mismatched byte, which is not constant-time. Although hashing the input first largely defeats classic per-character-byte timing, the `in set` lookup plus the presence of `_hash_token`/set-contains work before rate-limit recording still yields measurable time differences that can leak cache-hit vs. miss. Additionally, the token is kept only as an unsalted SHA-256 of a URL-safe string, not a password hash.
- **Impact:** Combined with lax rate limiting (see Finding 2), a remote attacker can mount a network-observable distinguisher against valid vs. invalid tokens. SHA-256 of a token (no salt, no KDF) is also vulnerable to a full dump of `auth_tokens` table being reversed for low-entropy user-supplied tokens.
- **Fix:** Use `hmac.compare_digest` on the hash (or on the token itself) against each stored hash; consider `secrets.compare_digest` semantics and salted KDF storage for user-supplied tokens.

## Finding 2 — Rate limiter keys on `request.client.host`, bypassable and does not consult `X-Forwarded-For`
- **Severity:** HIGH
- **Category:** security
- **Location:** src/taskbrew/auth.py:188–194 (`client_ip = getattr(getattr(request, "client", None), "host", "unknown")`)
- **Finding:** The rate limiter keys on the raw TCP peer address. When TaskBrew runs behind a reverse proxy (common with `dashboard_host: 0.0.0.0`), `request.client.host` is the proxy's IP, so every incoming request shares a single key — a legitimate user will be locked out by an attacker, or one attacker appears as many IPs by rotating X-Forwarded-For values. The code does not read `X-Forwarded-For`/`Forwarded` at all, so it cannot be spoofed, but it is also effectively unusable behind any proxy. There is also no per-token or global failure cap, only per-IP.
- **Impact:** In production deployments behind a proxy, a single malicious client can lock out every other user by exhausting the failure budget on the proxy's IP; direct-exposure deployments let an attacker brute force tokens by spreading attempts across spoofed source IPs (if the attacker controls the network path) or simply accept one IP having the same 10-attempts / 60s budget forever while they try ~ 2^256 hashes (impractical, so DoS is the real risk).
- **Fix:** Either refuse to run behind a proxy without an explicit `trusted_proxies` config + `X-Forwarded-For` parsing, or add a global failure cap and exponential backoff; document the assumption clearly.

## Finding 3 — Auto-generated API token printed to stdout and logged at WARNING
- **Severity:** HIGH
- **Category:** security
- **Location:** src/taskbrew/auth.py:63–70 (`print(f"\n  API Token: {token}\n ...")` and the `logger.warning("... %s...", token[:8])`)
- **Finding:** When auth is enabled but no tokens are pre-configured, `AuthManager.__init__` auto-generates a bearer token and (a) prints the full secret to stdout and (b) logs the first 8 chars at WARNING. The stdout print bypasses the structured logger entirely — in daemon mode stdout is redirected to `/dev/null` so the operator *never sees the token* (see `_cmd_start` at src/taskbrew/main.py:735 where stdout/stderr=DEVNULL), but under systemd or a TTY it's persisted in shell history/log files. The DB-stored hash alone is then the only way to ever authenticate, and the token is unrecoverable.
- **Impact:** In the normal daemon launch path (`taskbrew start`), enabling auth makes the server unusable: the generated token is silently discarded. In foreground mode, the secret is written to terminal scrollback and potentially to log-capturing tools.
- **Fix:** Write the auto-generated token to a mode-0600 file under `~/.taskbrew/` and surface that path in the log/warning; never print the secret to stdout in daemon mode.

## Finding 4 — `plugin_system.load_plugins` executes arbitrary Python with no sandboxing or path checks
- **Severity:** HIGH
- **Category:** security
- **Location:** src/taskbrew/plugin_system.py:148–163 (`spec_from_file_location(...)` + `spec.loader.exec_module(module)`)
- **Finding:** `load_plugins` walks `plugins/` via `iterdir()` and executes every `*.py` file or package via `exec_module`. It does not resolve symlinks, does not check that the resolved path is still inside `plugins_dir`, and does not verify ownership or permissions. Combined with `build_orchestrator` calling `Path(project_dir) / "plugins"` where `project_dir` can come from CLI (`--project-dir`) or from the projects registry (attacker-supplied file on disk), a hostile `plugins/symlink.py → /etc/evil.py` would be executed as the user running taskbrew.
- **Impact:** RCE via any process that can create a file inside the project's `plugins/` directory. Because plugins are loaded during orchestrator startup for every project that is activated, simply activating an attacker-controlled project directory runs their code.
- **Fix:** Resolve each candidate path with `resolve(strict=True)`, ensure `is_relative_to(plugins_dir.resolve())`, reject symlinks, and at minimum document that `plugins/` is trust-equivalent to the taskbrew user.

## Finding 5 — PID file race / TOCTOU in `_cmd_start`
- **Severity:** HIGH
- **Category:** concurrency
- **Location:** src/taskbrew/main.py:707–743 (`_cmd_start`: `_read_pid()` check, then `subprocess.Popen`, then `_write_pid(proc.pid)`) together with src/taskbrew/main.py:612–624 (the `--_serve_foreground` branch also calls `_write_pid(os.getpid())`)
- **Finding:** Two separate writers race to write the PID file. `_cmd_start` writes `proc.pid` after Popen, but the child subprocess (in the `--_serve_foreground` branch of `cli_main`) *also* writes its own `os.getpid()` — both call `PID_FILE.write_text(...)` with no locking or `os.O_EXCL`. If two `taskbrew start` calls race, both `_read_pid()` checks can return `None` (no file), both Popens succeed, and the PID file ends up referencing whichever child wrote last while the other daemon silently runs with no PID record. There is also no `fsync` / atomic rename, so a crash mid-write leaves a partial file that `_read_pid` will correctly treat as stale, but `int()` on truncated content triggers the ValueError branch and produces confusing UX.
- **Impact:** Double-start of the daemon; stop-command only targets one; port conflict on 8420 surfaces as an opaque uvicorn error.
- **Fix:** Use `os.open(PID_FILE, O_CREAT|O_EXCL|O_WRONLY, 0o600)` for exclusive creation, or `fcntl.flock` on a lockfile separate from the PID file; let the child alone own the PID file (parent should not pre-write it).

## Finding 6 — `taskbrew stop` kill loop re-uses old PID after process exit (PID reuse)
- **Severity:** MEDIUM
- **Category:** correctness-bug
- **Location:** src/taskbrew/main.py:752–777 (`_cmd_stop`)
- **Finding:** `_cmd_stop` reads the PID, sends SIGTERM, polls `_is_running(pid)` for 5 s, then sends SIGKILL. `_is_running` only checks `os.kill(pid, 0)`; it does not verify that the PID still belongs to taskbrew. On a busy system a PID can be reused by an unrelated process within the 5-second window, and the SIGKILL path unconditionally sends to whatever now holds that PID.
- **Impact:** `taskbrew stop` can kill an unrelated user process whose PID was recycled.
- **Fix:** Record a start-time or cookie alongside the PID file and re-verify before SIGKILL; or use the process's own cmdline under `/proc` (Linux) / `ps` (macOS).

## Finding 7 — Subprocess calls in `create_project` run with no timeout and silently swallow failures
- **Severity:** MEDIUM
- **Category:** error-handling
- **Location:** src/taskbrew/project_manager.py:283–300 (three `subprocess.run(..., capture_output=True)` calls for `git init`, `git add .`, `git commit`)
- **Finding:** The three git subprocesses have no `timeout=`, no `check=True`, and their return codes / stderr are discarded. If `git` is missing, misconfigured (no user.email), or the directory is on an NFS share that hangs, the API request hangs forever (the whole event loop if called from the dashboard) or silently returns success with a broken project (no initial commit → agents later fail to branch from `main`).
- **Impact:** Creating a project returns "success" while leaving the repo in an unusable state, or hangs indefinitely.
- **Fix:** Add `timeout=30`, check return codes, and surface errors in the `ValueError` raised to the API caller.

## Finding 8 — Registry writes are not atomic; concurrent writers can corrupt `projects.yaml`
- **Severity:** MEDIUM
- **Category:** concurrency
- **Location:** src/taskbrew/project_manager.py:398–402 (`_write_registry`)
- **Finding:** `_write_registry` does `open(path, "w")` followed by `yaml.dump(...)`. There is no `fsync`, no write-to-temp + `os.replace`, and no file lock. When the dashboard API exposes project CRUD (and it does via `project_manager` methods reached from async handlers), two concurrent requests each read, mutate, and write the registry sequentially, but a crash / SIGKILL mid-write leaves a truncated YAML that `_read_registry` then detects as corrupt and **resets to the empty registry** (src/taskbrew/project_manager.py:416–422). Every project is silently un-registered.
- **Impact:** Silent loss of all project registrations if the process is killed (or the disk fills) during any registry write.
- **Fix:** Write to `projects.yaml.tmp`, fsync, then `os.replace`; hold a `fcntl.flock` across the read-modify-write.

## Finding 9 — `load_team_config` uses unvalidated path expansion that silently collapses relative paths
- **Severity:** MEDIUM
- **Category:** edge-case
- **Location:** src/taskbrew/config_loader.py:162 (`db_path=str(Path(_get_required(data, "database.path", "team.yaml")).expanduser())`)
- **Finding:** `db_path` is expanded but not resolved and not made absolute. Downstream, `build_orchestrator` does `db_path = str(project_dir / team_config.db_path)` — if a user writes an absolute `database.path` (e.g. the scaffold literally writes `~/.taskbrew/data/<slug>.db` which `expanduser` turns absolute), then `project_dir / "/Users/x/.taskbrew/data/..."` is actually still absolute (Python `pathlib` discards left operand for absolute right operand), which is correct. But if the user writes a relative path like `data/tasks.db`, it ends up under `project_dir`, which silently disagrees with what the scaffold writes. Meanwhile `_validate_startup` in `main.py` never checks the DB dir is writable, whereas `_cmd_doctor` does — the two paths diverge. Combined with config.py's separate `OrchestratorConfig` (unused / dead code path — see Finding 12), the actual DB path resolution is non-obvious.
- **Impact:** Hard-to-diagnose "No such file or directory" on a path the user never named; users editing `database.path` to a relative value get two different DBs depending on `cwd`.
- **Fix:** Resolve `db_path` to absolute at load time; validate writability at startup.

## Finding 10 — Version mismatch between distribution metadata and `__version__`
- **Severity:** MEDIUM
- **Category:** correctness-bug
- **Location:** src/taskbrew/__init__.py:3 (`__version__ = "0.1.0"`) vs pyproject.toml:7 (`version = "1.0.6"`)
- **Finding:** The package published to PyPI as 1.0.6 ships `taskbrew.__version__ == "0.1.0"`. Any user / dashboard / bug report reading the runtime version will see the wrong value. The scaffolding's model IDs (`claude-opus-4-6`, `claude-sonnet-4-6`, `gemini-3.1-pro-preview`, `gemini-3-flash-preview`) are hardcoded, so downstream tooling keying off `__version__` also misidentifies the feature set.
- **Impact:** Support/triage broken: `pip show taskbrew` and `python -c 'import taskbrew; print(taskbrew.__version__)'` disagree; automation that pins on `__version__` keeps running an unpatched version.
- **Fix:** Source `__version__` from `importlib.metadata.version("taskbrew")` or synchronise both.

## Finding 11 — `cli_main` `--_serve_foreground` argv scan is fragile
- **Severity:** MEDIUM
- **Category:** correctness-bug
- **Location:** src/taskbrew/main.py:605–624 (`cli_main`, the `"--_serve_foreground" in sys.argv` branch)
- **Finding:** The foreground mode parses argv by manual string matching: `sys.argv.remove("--_serve_foreground")`, then a separate `if "--project-dir" in sys.argv: idx = sys.argv.index(...)`. The daemon launched in `_cmd_start` passes `[sys.executable, "-m", "taskbrew.main", "--_serve_foreground", "--project-dir", args.project_dir]`. If `args.project_dir` happens to be the literal string `--_serve_foreground` (unlikely but user-controlled), or contains `--project-dir`, the scan picks the wrong value. More importantly, because `_cmd_start` writes the PID file at line 741 (`_write_pid(proc.pid)`) while the child also writes it at line 621 (`_write_pid(os.getpid())`), either write can overwrite the other (see Finding 5), but also any ValueError from the ad-hoc parser kills the child *after* the parent already reported success.
- **Impact:** Corner-case silent mis-start; brittle coupling between parent flag construction and child parsing.
- **Fix:** Use `argparse` even for the internal flag; hide it with `SUPPRESS`.

## Finding 12 — `OrchestratorConfig`/`AgentConfig` in config.py is dead
- **Severity:** LOW
- **Category:** dead-code
- **Location:** src/taskbrew/config.py (entire module)
- **Finding:** The real config objects used throughout the code are `TeamConfig` / `RoleConfig` in `config_loader.py`. `config.py`'s `OrchestratorConfig` and `AgentConfig` are not imported anywhere in the slice (and appear to be legacy). `__post_init__` also concatenates relative paths in a way that conflicts with `team.yaml`-driven config.
- **Impact:** Misleading to new contributors; risk of accidental use diverging from real config.
- **Fix:** Remove, or route everything through a single config module.

## Finding 13 — `load_roles` swallows all role-file errors silently
- **Severity:** MEDIUM
- **Category:** error-handling
- **Location:** src/taskbrew/config_loader.py:460–476 (`load_roles`, `except (ValueError, KeyError, TypeError, yaml.YAMLError): logger.warning(...) continue`)
- **Finding:** When a role file is malformed, `load_roles` logs a warning and continues. `build_orchestrator` then calls `validate_routing(roles)` on the *reduced* set. A typo in one role's YAML quietly drops that role; `_validate_startup` only warns if roles is empty. If the dropped role is a pipeline entry point or the target of `routes_to` from other roles, validation errors change meaning or disappear entirely.
- **Impact:** A broken role file yields a silently-degraded team with no indication to the operator; tasks assigned to the missing role accumulate in the DB forever.
- **Fix:** Fail-fast on invalid role files unless an explicit `--ignore-invalid-roles` flag is set.

## Finding 14 — `_orphan_recovery_loop` uses naked `while True` with no cancellation / stop event
- **Severity:** MEDIUM
- **Category:** resource-leak
- **Location:** src/taskbrew/main.py:475–510 (`_orphan_recovery_loop`)
- **Finding:** Unlike `_escalation_task`, the orphan-recovery task has no stop event. `Orchestrator.shutdown` only waits for `self.agent_tasks` with a timeout, then cancels stragglers. Cancelling during `await asyncio.sleep(interval)` works, but if it's mid-DB call and the DB is closed in phase 4, a `CancelledError` may be swallowed by the bare `except Exception` and the task logs a scary traceback before exit.
- **Impact:** Noisy shutdown logs; in worst case, partial DB writes during the final recovery pass.
- **Fix:** Gate the loop on an `asyncio.Event` like the escalation monitor.

## Finding 15 — `_cmd_logs` follow-mode busy-waits and breaks on log rotation
- **Severity:** LOW
- **Category:** edge-case
- **Location:** src/taskbrew/main.py:797–815 (`_cmd_logs`, the follow loop)
- **Finding:** The tail-follow reopens the file once, seeks to end, and `readline()`s forever with `sleep(0.3)`. The daemon's `RotatingFileHandler` (src/taskbrew/logging_config.py:142–144, `maxBytes=10MB`, `backupCount=3`) will eventually rotate the file, leaving `_cmd_logs` reading an unlinked/renamed descriptor and showing no further output.
- **Impact:** `taskbrew logs -f` silently stops streaming after a log rotation.
- **Fix:** Detect rotation via inode change (`os.stat(path).st_ino != fd stat`) and reopen; or use `watchdog`.

## Finding 16 — `load_team_config` does not constrain `dashboard_host` or validate it
- **Severity:** LOW
- **Category:** security
- **Location:** src/taskbrew/config_loader.py:198 (`dashboard_host=_get_required(...)` with no validation) and src/taskbrew/main.py:528–531 (`host = orch.team_config.dashboard_host`)
- **Finding:** The scaffold defaults `dashboard_host: "0.0.0.0"` (project_manager.py:72, and main.py's `_cmd_init` writes `'host: "0.0.0.0"'` at line 582). Combined with auth being off by default (`auth.enabled: false`) and no warning on startup, every freshly initialised TaskBrew binds to all interfaces with no auth — the dashboard exposes the whole task board, subprocess-spawning endpoints, and arbitrary pipeline config to the LAN.
- **Impact:** Fresh installs expose the full control plane to any network-adjacent host by default; many home-office Wi-Fis are not isolated.
- **Fix:** Default to `127.0.0.1`; require an explicit `dashboard.host: 0.0.0.0` AND `auth.enabled: true` combination or refuse to start.

## Finding 17 — `setup_file_logging` ignores invalid `LOG_LEVEL` silently (drops warning)
- **Severity:** LOW
- **Category:** error-handling
- **Location:** src/taskbrew/logging_config.py:131–136 (`setup_file_logging`, `resolved = getattr(logging, level.upper(), None); level = resolved if resolved is not None else logging.INFO`)
- **Finding:** `setup_logging` prints a warning for invalid `LOG_LEVEL`; `setup_file_logging` (used by the daemon) silently falls back to INFO with no indication. The daemon path is precisely where the operator *cannot* see stderr (stdout/stderr DEVNULL in `_cmd_start`), so any typo in `LOG_LEVEL` is completely hidden.
- **Impact:** Misconfigured daemons appear to "ignore" the env var.
- **Fix:** Mirror the warning in `setup_file_logging`, written directly to the log file itself.

## Finding 18 — `plugin_system.register_hook` attaches attribute to arbitrary callables
- **Severity:** LOW
- **Category:** correctness-bug
- **Location:** src/taskbrew/plugin_system.py:101–104 (`callback._plugin_name = plugin_name`)
- **Finding:** `register_hook` mutates `callback._plugin_name` directly. If the callback is a `functools.partial`, a `bound method`, or a builtin (e.g. `print`), the attribute assignment raises `AttributeError`, aborting plugin load mid-registration and leaving the registry in an inconsistent state. The `unregister_plugin` filter (`getattr(cb, "_plugin_name", None) == name`) would then never remove hooks for this plugin either.
- **Impact:** A plugin using any non-assignable callable breaks registration and leaks hooks on unregister.
- **Fix:** Store `(callback, plugin_name)` tuples in `_hooks` instead of mutating the callable.

## Finding 19 — `artifact_exclude_patterns` default list is a mutable literal inside `field(default_factory=...)` for `ExecutionConfig` but hardcoded again at call site
- **Severity:** LOW
- **Category:** correctness-bug
- **Location:** src/taskbrew/config_loader.py:88–91 and src/taskbrew/config_loader.py:177–185 (`default_excludes = ["*.env", ...]` duplicated)
- **Finding:** Two independent default lists must stay in sync; `ExecutionConfig.artifact_exclude_patterns` uses a `default_factory`, but the parsing code at line 181 ignores that default and uses its own `default_excludes` local list. If one is updated without the other (e.g. adding `*.pfx` to the dataclass default), users who *omit* the `execution` block get one list and users who supply an empty `execution:` block get another.
- **Impact:** Secret-leaking glob list silently diverges across config variants; a user expecting their dataclass default to apply gets the parser's version.
- **Fix:** Have the parser fall back to `ExecutionConfig().artifact_exclude_patterns` directly.

## Finding 20 — `AuthManager._cleanup` throttle misses cleanup when attempts are sparse
- **Severity:** LOW
- **Category:** resource-leak
- **Location:** src/taskbrew/auth.py:183–185 (`self._call_count += 1; if self._call_count % 100 == 0: self._cleanup(now)`)
- **Finding:** Cleanup runs only every 100th `verify` call. On a lightly-used but long-running server with spiky traffic (e.g. a scanner briefly bursts 50 failed attempts then disappears), `_failed_attempts` and `_lockouts` can retain stale IPs for hours or days until the next flush, effectively leaking memory keyed by attacker-chosen IP. `_call_count` is also never reset and isn't `int`-overflow-safe in theory (Python ints are unbounded so not a crash, but reasoning about modulus over a huge int in hot path is a minor perf concern).
- **Impact:** Memory keyed on attacker-controlled IP grows under attack; not catastrophic but unbounded.
- **Fix:** Run cleanup on a timer (every N seconds elapsed) rather than call count.

## Finding 21 — `setup_logging` / `setup_file_logging` clear `root.handlers` — called twice silently removes earlier uvicorn handlers
- **Severity:** LOW
- **Category:** error-handling
- **Location:** src/taskbrew/logging_config.py:109–110 and src/taskbrew/logging_config.py:148–149 (`root.handlers.clear()`)
- **Finding:** Both setup functions unconditionally clear `root.handlers`. In the daemon path, `cli_main` calls `setup_file_logging()`, but if any imported module (uvicorn, sqlalchemy) registered handlers during import time, those are wiped. More importantly, if `setup_logging` is called first and then `setup_file_logging` second (as the code path for `--_serve_foreground` effectively does because `setup_logging()` is called unconditionally in `cli_main` before the `--_serve_foreground` check — actually looking more closely, the flag path `returns` before `setup_logging()`, so OK here), but the general pattern risks stomping third-party handlers.
- **Impact:** Fragile to import order; a future contributor adding handlers earlier loses them.
- **Fix:** Use `logging.basicConfig(force=True)` or add-only semantics guarded by an "already initialised" flag.

---

## Systemic issues observed across this slice

- **Default-insecure deployment:** scaffolded `team.yaml` binds to `0.0.0.0` with `auth.enabled: false` and no warning, and the auth path that *does* exist silently drops its auto-generated token when launched via the documented `taskbrew start` daemon entry. The net effect is that following the README verbatim yields a wide-open control plane.
- **Unsafe path handling:** plugin loading, project directory handling, DB path resolution, and registry writes all take attacker-influenced paths or content without `resolve(strict=True)` checks, atomic writes, or symlink rejection. Any one of these is a credible RCE / data-loss vector given that project directories come from a user-editable YAML registry.
- **Swallow-and-continue error handling:** `load_roles`, `git init/commit` in `create_project`, plugin `fire_hook`, and `setup_file_logging` all log-and-continue on errors. Combined with a daemon that sends stdout/stderr to DEVNULL, operators have no way to see that startup silently degraded.
- **Duplicate / drifted state writers:** two separate writers for the PID file, two separate defaults for `artifact_exclude_patterns`, two separate config dataclass modules (`config.py` vs `config_loader.py`), two code paths for `LOG_LEVEL` validation. Each pair has diverged at least slightly.
- **No concurrency primitives on shared files:** the project registry and the PID file are both read-modify-write without locking or atomic rename; the dashboard API exposes project CRUD, so concurrent requests racing with a `stop`/`start` can corrupt either file.
