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
        resolved = os.path.realpath(file_path)
        if not resolved.startswith(os.path.realpath(self.base_dir)):
            raise ValueError(f"Path traversal detected: {filename}")
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
        resolved = os.path.realpath(file_path)
        if not resolved.startswith(os.path.realpath(self.base_dir)):
            raise ValueError(f"Path traversal detected: {filename}")
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

    # ------------------------------------------------------------------
    # Output Persistence
    # ------------------------------------------------------------------

    def save_agent_output(
        self, group_id: str, task_id: str, agent_id: str, output_text: str
    ) -> str:
        """Save agent output as ``agent_output_{agent_id}.md``.

        Returns the full file path.
        """
        filename = f"agent_output_{agent_id}.md"
        return self.save_artifact(group_id, task_id, filename, output_text)

    def get_agent_output(
        self, group_id: str, task_id: str
    ) -> list[dict]:
        """Find and return all agent output files for a task.

        Returns a list of dicts with ``agent_id``, ``content``, and
        ``filename`` for each ``agent_output_*.md`` file found.
        """
        files = self.get_task_artifacts(group_id, task_id)
        results: list[dict] = []
        for fname in files:
            if fname.startswith("agent_output_") and fname.endswith(".md"):
                # Extract agent_id from filename: agent_output_{agent_id}.md
                agent_id = fname[len("agent_output_"):-len(".md")]
                content = self.load_artifact(group_id, task_id, fname)
                results.append({
                    "agent_id": agent_id,
                    "content": content,
                    "filename": fname,
                })
        return results

    # ------------------------------------------------------------------
    # Artifact Viewer Backend
    # ------------------------------------------------------------------

    def get_artifact_content(
        self, group_id: str, task_id: str, filename: str
    ) -> dict:
        """Load a single artifact with metadata.

        Returns a dict with ``filename``, ``content``, ``size``,
        ``group_id``, and ``task_id``.
        """
        content = self.load_artifact(group_id, task_id, filename)
        return {
            "filename": filename,
            "content": content,
            "size": len(content),
            "group_id": group_id,
            "task_id": task_id,
        }

    def get_all_artifacts(
        self, group_id: str | None = None
    ) -> list[dict]:
        """Return an organized listing of all artifacts.

        If *group_id* is provided, only returns artifacts for that group.
        Otherwise scans all group directories.

        Returns a list of dicts with ``group_id``, ``task_id``, and
        ``files`` (list of filenames).
        """
        results: list[dict] = []

        if group_id is not None:
            group_artifacts = self.get_group_artifacts(group_id)
            for task_id, files in group_artifacts.items():
                results.append({
                    "group_id": group_id,
                    "task_id": task_id,
                    "files": files,
                })
        else:
            # Scan all group directories under base_dir.
            if not os.path.isdir(self.base_dir):
                return []
            for group_entry in sorted(os.listdir(self.base_dir)):
                group_path = os.path.join(self.base_dir, group_entry)
                if os.path.isdir(group_path):
                    group_artifacts = self.get_group_artifacts(group_entry)
                    for task_id, files in group_artifacts.items():
                        results.append({
                            "group_id": group_entry,
                            "task_id": task_id,
                            "files": files,
                        })

        return results
