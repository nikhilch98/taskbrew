"""Serialized branch integration broker for approved verifier tasks."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import time

from taskbrew.orchestrator.merge_queue import MergeQueue

logger = logging.getLogger(__name__)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class MergeBroker:
    """Process durable merge queue rows one at a time."""

    def __init__(
        self,
        *,
        merge_queue: MergeQueue,
        task_board,
        event_bus,
        repo_dir: str,
        poll_interval: float = 2.0,
        worker_id: str = "merge-broker",
    ) -> None:
        self.merge_queue = merge_queue
        self.task_board = task_board
        self.event_bus = event_bus
        self.repo_dir = Path(repo_dir)
        self.poll_interval = poll_interval
        self.worker_id = worker_id
        self._stop = asyncio.Event()
        self._integration_base = self.repo_dir / ".worktrees" / ".integration"
        self._lock_path = self.repo_dir / ".taskbrew" / "locks" / "integration.lock"

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        logger.info("Merge broker %s started", self.worker_id)
        while not self._stop.is_set():
            try:
                await self.merge_queue.release_expired_leases()
                await self._cleanup_abandoned_integration_worktrees()
                row = await self.merge_queue.claim_ready(worker_id=self.worker_id)
                if row is None:
                    await asyncio.wait_for(
                        self._stop.wait(), timeout=self.poll_interval,
                    )
                    continue
                await self._process_row(row)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Merge broker loop failed")
                await asyncio.sleep(self.poll_interval)
        logger.info("Merge broker %s stopped", self.worker_id)

    async def process_once(self) -> bool:
        """Process at most one row. Intended for tests and maintenance."""
        await self.merge_queue.release_expired_leases()
        row = await self.merge_queue.claim_ready(worker_id=self.worker_id)
        if row is None:
            return False
        await self._process_row(row)
        return True

    async def _process_row(self, row: dict) -> None:
        await self.event_bus.emit(
            "task.merge_started",
            {
                "queue_id": row["id"],
                "task_id": row["parent_task_id"],
                "verification_task_id": row["verifier_task_id"],
                "source_branch": row["source_branch"],
                "target_branch": row["target_branch"],
            },
        )
        try:
            with self._repo_lock():
                result = await self._attempt_merge(row)
        except BlockingIOError as exc:
            await self._retry(row, f"integration lock unavailable: {exc}", 5)
            return
        except Exception as exc:
            logger.exception("Merge queue row %s failed", row["id"])
            await self._fail(row, "failed", str(exc))
            return

        status = result["status"]
        details = result.get("details") or ""
        if status in {"merged", "already_merged"}:
            await self._mark_merged(row, result)
        elif status == "conflict":
            await self._mark_conflict(row, details)
        elif status == "retry_pending":
            delay = min(60, 2 ** max(int(row.get("attempts") or 1), 1))
            await self._retry(row, details or "transient merge failure", delay)
        elif status == "root_refresh_blocked":
            await self._fail(row, "root_refresh_blocked", details)
        else:
            await self._fail(row, status if status in {"blocked", "failed"} else "blocked", details)

    @contextmanager
    def _repo_lock(self):
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(str(self._lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            if self._remove_stale_repo_lock():
                fd = os.open(str(self._lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            else:
                raise BlockingIOError(str(self._lock_path)) from exc
        try:
            os.write(fd, f"{os.getpid()} {self.worker_id} {_utcnow()}\n".encode())
            yield
        finally:
            os.close(fd)
            try:
                self._lock_path.unlink()
            except FileNotFoundError:
                pass

    def _remove_stale_repo_lock(self) -> bool:
        try:
            raw = self._lock_path.read_text().strip().split()
            pid = int(raw[0]) if raw else None
            age = time.time() - self._lock_path.stat().st_mtime
        except (OSError, ValueError):
            return False
        if age < 120:
            return False
        if pid is not None:
            try:
                os.kill(pid, 0)
                return False
            except ProcessLookupError:
                pass
            except PermissionError:
                return False
        try:
            self._lock_path.unlink()
            return True
        except OSError:
            return False

    async def _attempt_merge(self, row: dict) -> dict:
        source_branch = row["source_branch"]
        target_branch = row["target_branch"] or "main"
        queue_id = row["id"]
        worktree_path = self._integration_base / queue_id

        source_rc, _, _ = await self._git("rev-parse", "--verify", source_branch)
        if source_rc != 0:
            return {"status": "blocked", "details": f"Source branch {source_branch!r} not found"}

        target_rc, target_sha, target_err = await self._git("rev-parse", "--verify", target_branch)
        if target_rc != 0:
            return {
                "status": "blocked",
                "details": f"Target branch {target_branch!r} not found: {target_err.strip()}",
            }
        expected_target_sha = target_sha.strip()

        ancestor_rc, _, _ = await self._git(
            "merge-base", "--is-ancestor", source_branch, target_branch,
        )
        if ancestor_rc == 0:
            refresh_plan = await self._prepare_primary_checkout_refresh(
                target_branch=target_branch,
                queue_id=queue_id,
            )
            if refresh_plan["status"] != "ok":
                return {
                    "status": "root_refresh_blocked",
                    "details": refresh_plan["details"],
                    "target_sha_before": expected_target_sha,
                    "target_sha_after": expected_target_sha,
                    "root_refresh_status": refresh_plan["root_refresh_status"],
                }
            refresh = await self._finish_primary_checkout_refresh(
                target_sha=expected_target_sha,
                refresh_plan=refresh_plan,
            )
            if refresh["status"] != "ok":
                return {
                    "status": "root_refresh_blocked",
                    "details": refresh["details"],
                    "target_sha_before": expected_target_sha,
                    "target_sha_after": expected_target_sha,
                    "root_refresh_status": refresh["root_refresh_status"],
                }
            return {
                "status": "already_merged",
                "details": "Source branch is already merged",
                "target_sha_before": expected_target_sha,
                "target_sha_after": expected_target_sha,
                "root_refresh_status": refresh["root_refresh_status"],
            }

        await self._remove_integration_worktree(worktree_path)
        self._integration_base.mkdir(parents=True, exist_ok=True)
        rc, out, err = await self._git(
            "worktree", "add", "--detach", str(worktree_path), expected_target_sha,
        )
        if rc != 0:
            return {"status": "retry_pending", "details": (err or out).strip()}

        try:
            stale = await self._handle_integration_lock(worktree_path)
            if stale:
                return {"status": "retry_pending", "details": stale}

            rc, out, err = await self._git(
                "merge", "--no-edit", source_branch, cwd=worktree_path,
            )
            details = (out + err).strip()
            if rc != 0:
                if "CONFLICT" in details or "Automatic merge failed" in details:
                    await self._git("merge", "--abort", cwd=worktree_path)
                    return {"status": "conflict", "details": details}
                await self._git("merge", "--abort", cwd=worktree_path)
                return {"status": "retry_pending", "details": details}

            rc, merged_sha, err = await self._git("rev-parse", "HEAD", cwd=worktree_path)
            if rc != 0:
                return {"status": "retry_pending", "details": err.strip()}
            target_sha_after = merged_sha.strip()

            refresh_plan = await self._prepare_primary_checkout_refresh(
                target_branch=target_branch,
                queue_id=queue_id,
            )
            if refresh_plan["status"] != "ok":
                return {
                    "status": "root_refresh_blocked",
                    "details": refresh_plan["details"],
                    "target_sha_before": expected_target_sha,
                    "target_sha_after": target_sha_after,
                    "root_refresh_status": refresh_plan["root_refresh_status"],
                }

            rc, out, err = await self._git(
                "update-ref",
                f"refs/heads/{target_branch}",
                target_sha_after,
                expected_target_sha,
            )
            if rc != 0:
                await self._restore_prepared_stash(refresh_plan)
                return {"status": "retry_pending", "details": (err or out).strip()}

            refresh = await self._finish_primary_checkout_refresh(
                target_sha=target_sha_after,
                refresh_plan=refresh_plan,
            )
            if refresh["status"] != "ok":
                return {
                    "status": "root_refresh_blocked",
                    "details": refresh["details"],
                    "target_sha_before": expected_target_sha,
                    "target_sha_after": target_sha_after,
                    "root_refresh_status": refresh["root_refresh_status"],
                }

            return {
                "status": "merged",
                "details": details,
                "target_sha_before": expected_target_sha,
                "target_sha_after": target_sha_after,
                "root_refresh_status": refresh["root_refresh_status"],
            }
        finally:
            await self._remove_integration_worktree(worktree_path)

    async def _prepare_primary_checkout_refresh(
        self,
        *,
        target_branch: str,
        queue_id: str,
    ) -> dict:
        rc, branch, err = await self._git("rev-parse", "--abbrev-ref", "HEAD")
        if rc != 0:
            return {
                "status": "blocked",
                "details": err.strip(),
                "root_refresh_status": "failed_branch_check",
            }
        dirty = await self._root_dirty()
        if branch.strip() != target_branch:
            return {
                "status": "ok",
                "refresh": False,
                "stashed": False,
                "root_refresh_status": "skipped_not_on_target",
            }
        if not dirty:
            return {"status": "ok", "refresh": True, "stashed": False}

        stash_msg = f"taskbrew-merge-{queue_id}"
        rc, out, err = await self._git(
            "stash",
            "push",
            "--include-untracked",
            "-m",
            stash_msg,
            "--",
            ".",
            ":(exclude).worktrees",
            ":(exclude).taskbrew",
        )
        if rc != 0:
            return {
                "status": "blocked",
                "details": (err or out).strip(),
                "root_refresh_status": "stash_failed",
            }
        return {"status": "ok", "refresh": True, "stashed": True}

    async def _finish_primary_checkout_refresh(
        self,
        *,
        target_sha: str,
        refresh_plan: dict,
    ) -> dict:
        if not refresh_plan.get("refresh"):
            return {
                "status": "ok",
                "root_refresh_status": refresh_plan.get("root_refresh_status")
                or "skipped_not_on_target",
            }

        rc, out, err = await self._git("reset", "--hard", target_sha)
        if rc != 0:
            return {
                "status": "blocked",
                "details": (err or out).strip(),
                "root_refresh_status": "reset_failed_after_stash",
            }

        if not refresh_plan.get("stashed"):
            return {"status": "ok", "root_refresh_status": "refreshed_clean"}

        rc, out, err = await self._git("stash", "pop")
        if rc != 0:
            return {
                "status": "blocked",
                "details": (out + err).strip(),
                "root_refresh_status": "stash_pop_failed",
            }
        return {"status": "ok", "root_refresh_status": "refreshed_with_stash"}

    async def _restore_prepared_stash(self, refresh_plan: dict) -> None:
        if refresh_plan.get("stashed"):
            await self._git("stash", "pop")

    async def _root_dirty(self) -> bool:
        rc, out, _ = await self._git(
            "status", "--porcelain", "--", ".",
            ":(exclude).worktrees", ":(exclude).taskbrew",
        )
        return rc == 0 and bool(out.strip())

    async def _handle_integration_lock(self, worktree_path: Path) -> str | None:
        git_dir_rc, git_dir, _ = await self._git("rev-parse", "--git-dir", cwd=worktree_path)
        if git_dir_rc != 0:
            return "Unable to locate integration worktree git dir"
        lock_path = Path(git_dir.strip()) / "index.lock"
        if not lock_path.is_absolute():
            lock_path = worktree_path / lock_path
        if not lock_path.exists():
            return None
        age = datetime.now().timestamp() - lock_path.stat().st_mtime
        if age < 120:
            return f"Active integration index lock: {lock_path}"
        try:
            lock_path.unlink()
            return None
        except OSError as exc:
            return f"Unable to remove stale integration index lock {lock_path}: {exc}"

    async def _mark_merged(self, row: dict, result: dict) -> None:
        status = result["status"]
        await self.merge_queue.complete(
            row["id"],
            status=status,
            details=result.get("details"),
            target_sha_before=result.get("target_sha_before"),
            target_sha_after=result.get("target_sha_after"),
            root_refresh_status=result.get("root_refresh_status"),
        )
        await self.merge_queue.supersede_open_for_parent(
            parent_task_id=row["parent_task_id"],
            except_row_id=row["id"],
            details=(
                f"Superseded after {row['source_branch']} landed on "
                f"{row['target_branch']}"
            ),
        )
        if row.get("work_package_id"):
            await self.merge_queue.supersede_open_for_package(
                work_package_id=row["work_package_id"],
                except_row_id=row["id"],
                details=(
                    f"Superseded after package branch {row['source_branch']} "
                    f"landed on {row['target_branch']}"
                ),
            )
            await self.task_board._finalize_integrated_work_packages_for_group(
                row["group_id"]
            )
        await self.task_board._db.execute(
            "UPDATE tasks SET merge_status = ? WHERE id IN (?, ?)",
            ("merged", row["parent_task_id"], row["verifier_task_id"]),
        )
        await self.event_bus.emit(
            "task.branch_merged",
            {
                "task_id": row["parent_task_id"],
                "verification_task_id": row["verifier_task_id"],
                "queue_id": row["id"],
                "source_branch": row["source_branch"],
                "target_branch": row["target_branch"],
                "status": status,
                "agent_id": self.worker_id,
            },
        )
        await self.task_board._check_group_completion(row["parent_task_id"])

    async def _mark_conflict(self, row: dict, details: str) -> None:
        await self.merge_queue.complete(row["id"], status="conflict", details=details)
        await self.task_board._db.execute(
            "UPDATE tasks SET merge_status = ? WHERE id IN (?, ?)",
            ("merge_conflict", row["parent_task_id"], row["verifier_task_id"]),
        )
        await self._create_merge_conflict_task(row, details)
        await self.event_bus.emit(
            "task.merge_conflict",
            {
                "task_id": row["parent_task_id"],
                "verification_task_id": row["verifier_task_id"],
                "queue_id": row["id"],
                "reason": details,
            },
        )
        await self.task_board._check_group_completion(row["parent_task_id"])

    async def _retry(self, row: dict, details: str, delay_seconds: float) -> None:
        await self.merge_queue.retry(row["id"], details=details, delay_seconds=delay_seconds)
        await self.event_bus.emit(
            "task.merge_retry_pending",
            {
                "task_id": row["parent_task_id"],
                "verification_task_id": row["verifier_task_id"],
                "queue_id": row["id"],
                "reason": details,
                "delay_seconds": delay_seconds,
            },
        )

    async def _fail(self, row: dict, status: str, details: str) -> None:
        queue_status = status if status in {"blocked", "failed", "root_refresh_blocked"} else "blocked"
        await self.merge_queue.complete(row["id"], status=queue_status, details=details)
        merge_status = (
            "merge_root_refresh_blocked"
            if queue_status == "root_refresh_blocked"
            else f"merge_{queue_status}"
        )
        await self.task_board._db.execute(
            "UPDATE tasks SET merge_status = ? WHERE id IN (?, ?)",
            (merge_status, row["parent_task_id"], row["verifier_task_id"]),
        )
        await self._record_merge_escalation(row, details, severity="high")
        event_name = (
            "task.merge_root_refresh_blocked"
            if queue_status == "root_refresh_blocked"
            else "task.merge_blocked"
        )
        await self.event_bus.emit(
            event_name,
            {
                "task_id": row["parent_task_id"],
                "verification_task_id": row["verifier_task_id"],
                "queue_id": row["id"],
                "status": queue_status,
                "reason": details,
            },
        )
        await self.task_board._check_group_completion(row["parent_task_id"])

    async def _create_merge_conflict_task(self, row: dict, reason: str) -> None:
        package_id = row.get("work_package_id")
        source_label = (
            f"work package {package_id}"
            if package_id
            else f"verification task {row['verifier_task_id']}"
        )
        existing = await self.task_board._db.execute_fetchone(
            "SELECT id FROM tasks WHERE revision_of = ? AND status != 'cancelled' "
            "AND task_type = 'revision' "
            "AND (? IS NULL OR work_package_id = ?) LIMIT 1",
            (row["parent_task_id"], package_id, package_id),
        )
        if existing:
            return
        await self.task_board.create_task(
            group_id=row["group_id"],
            title=(
                f"Resolve package integration conflict for {package_id}"
                if package_id
                else f"Resolve merge conflict for {row['parent_task_id']}"
            ),
            task_type="revision",
            assigned_to="coder",
            created_by=self.worker_id,
            parent_id=row["parent_task_id"],
            revision_of=row["parent_task_id"],
            work_package_id=package_id,
            priority="high",
            description=(
                f"TaskBrew attempted to merge `{row['source_branch']}` into "
                f"`{row['target_branch']}` for {source_label}, but git reported "
                f"a merge conflict.\n\n"
                f"Work Package: {package_id or 'n/a'}\n"
                f"Merge Queue: {row['id']}\n\n"
                f"Conflict details:\n{reason}\n\n"
                "Resolve the conflict on the task branch, run the relevant tests, "
                "commit the fix, and let package review/integration retry the branch."
            ),
        )

    async def _record_merge_escalation(
        self, row: dict, reason: str, severity: str,
    ) -> None:
        await self.task_board._db.execute(
            "INSERT INTO escalations (task_id, from_agent, to_agent, reason, severity, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, 'open', ?)",
            (
                row["verifier_task_id"],
                self.worker_id,
                None,
                (
                    f"Brokered merge of {row['source_branch']} into "
                    f"{row['target_branch']} failed: {reason}"
                ),
                severity,
                _utcnow(),
            ),
        )

    async def _cleanup_abandoned_integration_worktrees(self) -> None:
        if not self._integration_base.exists():
            return
        keep_running = {
            row["id"]
            for row in await self.task_board._db.execute_fetchall(
                "SELECT id FROM merge_queue WHERE status = 'running'",
            )
        }
        for child in self._integration_base.iterdir():
            if child.name in keep_running:
                continue
            await self._remove_integration_worktree(child)

    async def _remove_integration_worktree(self, path: Path) -> None:
        if not path.exists():
            return
        await self._git("worktree", "remove", "--force", str(path))
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)

    async def _git(self, *args: str, cwd: Path | None = None) -> tuple[int, str, str]:
        env = dict(os.environ)
        env["GIT_TERMINAL_PROMPT"] = "0"
        env.setdefault("GIT_ASKPASS", "/bin/true")
        env.setdefault("SSH_ASKPASS", "/bin/true")
        proc = await asyncio.create_subprocess_exec(
            "git",
            *args,
            cwd=str(cwd or self.repo_dir),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            await proc.wait()
            return 124, "", f"git {' '.join(args)} timed out"
        return (
            int(proc.returncode or 0),
            stdout.decode(errors="replace"),
            stderr.decode(errors="replace"),
        )
