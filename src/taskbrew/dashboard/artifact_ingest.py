"""Shared artifact ingestion helpers for agent-declared artifact paths."""

from __future__ import annotations

import logging
import os
from typing import Any

from taskbrew.orchestrator.artifact_store import ArtifactStore

logger = logging.getLogger(__name__)

MAX_INGEST_ARTIFACT_COUNT = 50


async def ingest_artifact_paths(
    *,
    task_board: Any,
    orch: Any,
    task_id: str,
    group_id: str | None,
    artifact_paths: list[str] | None,
) -> list[str]:
    """Copy declared artifact paths from the claimant worktree into storage.

    The dashboard viewer reads from ``<artifacts>/<group>/<task>/<filename>``.
    Agents usually write files inside their worktree, so completion/check tools
    must copy declared files before the worktree is reused or deleted.
    """
    if not artifact_paths or not isinstance(artifact_paths, list):
        return []
    if not task_board or not orch:
        return []

    task = await task_board.get_task(task_id)
    if not task:
        return []

    effective_group_id = group_id or task.get("group_id")
    if not effective_group_id:
        return []

    worktree_mgr = getattr(orch, "worktree_manager", None)
    if not worktree_mgr:
        return []

    claimed_by = task.get("claimed_by") or ""
    worktree_path = worktree_mgr.get_worktree_path(claimed_by)
    if not worktree_path or not os.path.isdir(worktree_path):
        return []

    artifact_store = getattr(orch, "artifact_store", None)
    base = getattr(artifact_store, "base_dir", None)
    if not base:
        team_config = getattr(orch, "team_config", None)
        artifacts_subdir = (
            getattr(team_config, "artifacts_base_dir", "artifacts")
            if team_config
            else "artifacts"
        )
        base = os.path.join(getattr(orch, "project_dir", "."), artifacts_subdir)
    store = ArtifactStore(base_dir=str(base))

    worktree_real = os.path.realpath(worktree_path)
    ingested: list[str] = []
    for path_entry in artifact_paths[:MAX_INGEST_ARTIFACT_COUNT]:
        if not isinstance(path_entry, str) or not path_entry.strip():
            continue
        normalized = path_entry.replace("\\", "/")
        if os.path.isabs(path_entry) or ".." in normalized.split("/"):
            logger.warning(
                "Rejecting artifact_path %r for task %s: not relative or contains '..'",
                path_entry,
                task_id,
            )
            continue

        full = os.path.realpath(os.path.join(worktree_path, path_entry))
        if full != worktree_real and not full.startswith(worktree_real + os.sep):
            logger.warning(
                "Rejecting artifact_path %r for task %s: outside worktree",
                path_entry,
                task_id,
            )
            continue
        try:
            dest = store.ingest_file(effective_group_id, task_id, full)
        except Exception as exc:
            logger.warning(
                "Failed to ingest artifact %r for task %s: %s",
                path_entry,
                task_id,
                exc,
            )
            continue
        if dest:
            ingested.append(os.path.basename(dest))

    if ingested:
        logger.info(
            "Ingested %d artifact(s) for task %s: %s",
            len(ingested),
            task_id,
            ingested,
        )
    return ingested
