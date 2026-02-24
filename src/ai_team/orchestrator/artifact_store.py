"""Artifact store organized by group_id/task_id."""

from __future__ import annotations

import os


class ArtifactStore:
    """Manages artifacts organized by group and task.

    Directory layout::

        base_dir/
          <group_id>/
            <task_id>/
              <filename>

    Parameters
    ----------
    base_dir:
        Root directory for all artifact storage.
    """

    def __init__(self, base_dir: str) -> None:
        self.base_dir = base_dir

    def get_artifact_dir(self, group_id: str, task_id: str) -> str:
        """Return (and create) the directory for a specific task's artifacts.

        Path: ``base_dir/group_id/task_id/``
        """
        path = os.path.join(self.base_dir, group_id, task_id)
        os.makedirs(path, exist_ok=True)
        return path

    def save_artifact(
        self, group_id: str, task_id: str, filename: str, content: str
    ) -> str:
        """Save an artifact file for a task.

        Creates directories as needed. Returns the full file path.
        """
        dir_path = self.get_artifact_dir(group_id, task_id)
        file_path = os.path.join(dir_path, filename)
        with open(file_path, "w") as f:
            f.write(content)
        return file_path

    def load_artifact(
        self, group_id: str, task_id: str, filename: str
    ) -> str:
        """Load an artifact file's content.

        Returns an empty string if the file does not exist.
        """
        file_path = os.path.join(self.base_dir, group_id, task_id, filename)
        if not os.path.isfile(file_path):
            return ""
        with open(file_path) as f:
            return f.read()

    def get_task_artifacts(self, group_id: str, task_id: str) -> list[str]:
        """List all artifact filenames for a given task."""
        dir_path = os.path.join(self.base_dir, group_id, task_id)
        if not os.path.isdir(dir_path):
            return []
        return sorted(os.listdir(dir_path))

    def get_group_artifacts(self, group_id: str) -> dict[str, list[str]]:
        """Return all artifacts for a group, organized by task_id.

        Returns a dict mapping ``task_id -> [filenames]`` for every task
        directory found under the group.
        """
        group_dir = os.path.join(self.base_dir, group_id)
        if not os.path.isdir(group_dir):
            return {}
        result: dict[str, list[str]] = {}
        for entry in sorted(os.listdir(group_dir)):
            entry_path = os.path.join(group_dir, entry)
            if os.path.isdir(entry_path):
                files = sorted(os.listdir(entry_path))
                result[entry] = files
        return result
