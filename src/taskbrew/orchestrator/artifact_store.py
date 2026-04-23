"""Artifact store organized by group_id/task_id.

Audit 03 F#13 / 11a F#8: the previous implementation validated the
final file path in save_artifact / load_artifact, but the listing
methods (``get_task_artifacts``, ``get_group_artifacts``, the scan
in ``get_all_artifacts``) joined ``group_id`` and ``task_id`` straight
into ``os.path.join(self.base_dir, group_id, task_id)`` with no shape
check. A caller passing ``group_id='..'`` could enumerate or escape the
sandbox via ``os.listdir``.

Fix: centralize containment in :meth:`_safe_artifact_path` and reject
any ``group_id`` / ``task_id`` / ``filename`` that doesn't match the
conservative shape ``[A-Za-z0-9_.-]+`` with no leading dot. All public
methods route through it.
"""

from __future__ import annotations

import os
import re


_COMPONENT_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")
_DISALLOWED = ("/", "\\", "\x00", "..")


def _validate_component(value: str, label: str) -> str:
    """Validate that *value* is safe for use as a path component.

    Rejects empty strings, values starting with ``.`` or ``-``, slashes,
    backslashes, NUL, and anything not matching the conservative
    character class.
    """
    if not isinstance(value, str) or not value:
        raise ValueError(f"Invalid {label}: empty")
    for bad in _DISALLOWED:
        if bad in value:
            raise ValueError(f"Invalid {label}: contains {bad!r}")
    if value in (".", ".."):
        raise ValueError(f"Invalid {label}: reserved name")
    if not _COMPONENT_RE.match(value):
        raise ValueError(
            f"Invalid {label} {value!r}: must match "
            "[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}"
        )
    return value


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
        self._base_real: str | None = None

    def _resolved_base(self) -> str:
        if self._base_real is None:
            os.makedirs(self.base_dir, exist_ok=True)
            self._base_real = os.path.realpath(self.base_dir)
        return self._base_real

    def _safe_artifact_path(
        self,
        group_id: str,
        task_id: str | None = None,
        filename: str | None = None,
    ) -> str:
        """Build an artifact path, validating every component.

        Returns the absolute realpath; also verifies the result stays
        inside the resolved base. Rejects traversal both structurally
        (regex) and geometrically (realpath containment).
        """
        base = self._resolved_base()
        _validate_component(group_id, "group_id")
        components = [base, group_id]
        if task_id is not None:
            _validate_component(task_id, "task_id")
            components.append(task_id)
        if filename is not None:
            _validate_component(filename, "filename")
            components.append(filename)
        joined = os.path.join(*components)
        resolved = os.path.realpath(joined)
        if resolved != base and not resolved.startswith(base + os.sep):
            # Symlinked component escaped the sandbox.
            raise ValueError("Path traversal detected")
        return resolved

    def get_artifact_dir(self, group_id: str, task_id: str) -> str:
        """Return (and create) the directory for a specific task's artifacts.

        Path: ``base_dir/group_id/task_id/``
        """
        path = self._safe_artifact_path(group_id, task_id)
        os.makedirs(path, exist_ok=True)
        return path

    def save_artifact(
        self, group_id: str, task_id: str, filename: str, content: str
    ) -> str:
        """Save an artifact file for a task.

        Creates directories as needed. Returns the full file path.
        """
        dir_path = self.get_artifact_dir(group_id, task_id)
        file_path = self._safe_artifact_path(group_id, task_id, filename)
        with open(file_path, "w") as f:
            f.write(content)
        return file_path

    def load_artifact(
        self, group_id: str, task_id: str, filename: str
    ) -> str:
        """Load an artifact file's content.

        Returns an empty string if the file does not exist.
        """
        file_path = self._safe_artifact_path(group_id, task_id, filename)
        if not os.path.isfile(file_path):
            return ""
        with open(file_path) as f:
            return f.read()

    def get_task_artifacts(self, group_id: str, task_id: str) -> list[str]:
        """List all artifact filenames for a given task."""
        try:
            dir_path = self._safe_artifact_path(group_id, task_id)
        except ValueError:
            return []
        if not os.path.isdir(dir_path):
            return []
        return sorted(os.listdir(dir_path))

    def get_group_artifacts(self, group_id: str) -> dict[str, list[str]]:
        """Return all artifacts for a group, organized by task_id.

        Returns a dict mapping ``task_id -> [filenames]`` for every task
        directory found under the group. Each sub-entry is re-validated
        to refuse any symlinked directory that points outside the sandbox.
        """
        try:
            group_dir = self._safe_artifact_path(group_id)
        except ValueError:
            return {}
        if not os.path.isdir(group_dir):
            return {}
        result: dict[str, list[str]] = {}
        for entry in sorted(os.listdir(group_dir)):
            try:
                _validate_component(entry, "task_id")
                entry_path = self._safe_artifact_path(group_id, entry)
            except ValueError:
                continue
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
        # agent_id flows into a filename; validate it via the same rules.
        _validate_component(agent_id, "agent_id")
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
            return results

        base = self._resolved_base()
        if not os.path.isdir(base):
            return []
        for group_entry in sorted(os.listdir(base)):
            try:
                _validate_component(group_entry, "group_id")
            except ValueError:
                continue
            try:
                group_path = self._safe_artifact_path(group_entry)
            except ValueError:
                continue
            if os.path.isdir(group_path):
                group_artifacts = self.get_group_artifacts(group_entry)
                for task_id, files in group_artifacts.items():
                    results.append({
                        "group_id": group_entry,
                        "task_id": task_id,
                        "files": files,
                    })

        return results
