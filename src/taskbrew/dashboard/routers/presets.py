"""Agent preset listing and detail endpoints."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException

from taskbrew.config_loader import load_presets

router = APIRouter()

_PRESETS_DIR = Path(__file__).resolve().parents[4] / "config" / "presets"
_presets: dict[str, dict] | None = None


def _get_presets() -> dict[str, dict]:
    global _presets
    if _presets is None:
        _presets = load_presets(_PRESETS_DIR)
    return _presets


_LIST_FIELDS = {
    "preset_id", "category", "display_name", "description",
    "capabilities", "icon_emoji", "color", "prefix",
    "approval_mode", "max_revision_cycles", "uses_worktree",
    "default_model",
}


@router.get("/api/presets")
async def list_presets():
    """List all presets with metadata only (no system_prompt)."""
    presets = _get_presets()
    items = []
    for p in presets.values():
        items.append({k: v for k, v in p.items() if k in _LIST_FIELDS})
    items.sort(key=lambda x: (x.get("category", ""), x.get("display_name", "")))
    return {"presets": items, "count": len(items)}


@router.get("/api/presets/{preset_id}")
async def get_preset(preset_id: str):
    """Get full preset detail including system_prompt and tools."""
    presets = _get_presets()
    if preset_id not in presets:
        raise HTTPException(404, f"Preset not found: {preset_id}")
    return presets[preset_id]
