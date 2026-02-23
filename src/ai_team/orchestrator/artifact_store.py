"""Artifact store for passing data between pipeline steps."""

import os
from pathlib import Path


class ArtifactStore:
    """Manages artifacts produced by pipeline steps."""

    def __init__(self, base_dir: str):
        self.base_dir = base_dir

    def get_artifact_dir(self, run_id: str, step_index: int, agent_name: str) -> str:
        """Returns path: base_dir/run_id/step_index_agent_name/"""
        path = os.path.join(self.base_dir, run_id, f"{step_index}_{agent_name}")
        os.makedirs(path, exist_ok=True)
        return path

    def save_artifact(self, run_id: str, step_index: int, agent_name: str, filename: str, content: str) -> str:
        """Write artifact file, creating dirs as needed. Returns the file path."""
        dir_path = self.get_artifact_dir(run_id, step_index, agent_name)
        file_path = os.path.join(dir_path, filename)
        with open(file_path, "w") as f:
            f.write(content)
        return file_path

    def load_artifact(self, run_id: str, step_index: int, agent_name: str, filename: str) -> str:
        """Read artifact file content."""
        dir_path = self.get_artifact_dir(run_id, step_index, agent_name)
        file_path = os.path.join(dir_path, filename)
        with open(file_path) as f:
            return f.read()

    def get_step_artifacts(self, run_id: str, step_index: int, agent_name: str) -> list[str]:
        """List all artifact filenames for a step."""
        dir_path = os.path.join(self.base_dir, run_id, f"{step_index}_{agent_name}")
        if not os.path.isdir(dir_path):
            return []
        return sorted(os.listdir(dir_path))

    def get_previous_artifacts(self, run_id: str, step_index: int) -> list[dict]:
        """Get all artifacts from steps 0..step_index-1.
        Returns list of {"step_index": int, "agent": str, "files": list[str]}"""
        result = []
        run_dir = os.path.join(self.base_dir, run_id)
        if not os.path.isdir(run_dir):
            return result
        for entry in sorted(os.listdir(run_dir)):
            parts = entry.split("_", 1)
            if len(parts) != 2:
                continue
            try:
                idx = int(parts[0])
            except ValueError:
                continue
            if idx < step_index:
                agent = parts[1]
                files = sorted(os.listdir(os.path.join(run_dir, entry)))
                result.append({"step_index": idx, "agent": agent, "files": files})
        return result

    def build_context(self, run_id: str, step_index: int) -> str:
        """Build prompt-ready context string from all previous artifacts."""
        previous = self.get_previous_artifacts(run_id, step_index)
        if not previous:
            return ""
        sections = []
        for item in previous:
            agent = item["agent"]
            idx = item["step_index"]
            for filename in item["files"]:
                content = self.load_artifact(run_id, idx, agent, filename)
                sections.append(f"## Step {idx} ({agent}) - {filename}\n\n{content}")
        return "\n\n---\n\n".join(sections)
