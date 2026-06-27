"""Project-creation UI lives in the v6 calm console (home.html).

The dense original dashboard (index.html / /advanced) and its multi-step
Create Project wizard were removed; project creation is now a single-step
modal in home.html that POSTs to /api/projects with sensible defaults.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOME_TEMPLATE = ROOT / "src/taskbrew/dashboard/templates/home.html"
SETTINGS_TEMPLATE = ROOT / "src/taskbrew/dashboard/templates/settings.html"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_home_has_new_project_modal():
    source = _read(HOME_TEMPLATE)
    assert 'id="np-overlay"' in source
    assert "function openNewProject" in source
    assert "function submitNewProject" in source


def test_new_project_modal_collects_required_fields():
    source = _read(HOME_TEMPLATE)
    assert 'id="np-name"' in source
    assert 'id="np-dir"' in source
    assert 'id="np-cli"' in source


def test_new_project_submits_to_api_with_defaults():
    source = _read(HOME_TEMPLATE)
    assert "'/api/projects'" in source
    assert "with_defaults:true" in source
    assert "cli_provider" in source


def test_old_dashboard_template_is_gone():
    assert not (ROOT / "src/taskbrew/dashboard/templates/index.html").exists()


def test_home_no_longer_links_to_advanced():
    assert "/advanced" not in _read(HOME_TEMPLATE)


def test_settings_page_contains_system_agent_controls():
    source = _read(SETTINGS_TEMPLATE)

    assert "System AI Profile" in source
    assert "updateSystemAgentProvider" in source
    assert "System Review Rounds" in source
    assert "system_agent" in source
