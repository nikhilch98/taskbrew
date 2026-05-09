from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "src/taskbrew/dashboard/templates/index.html"
STATIC_JS = ROOT / "src/taskbrew/dashboard/static/js/dashboard-ui.js"
SETTINGS_TEMPLATE = ROOT / "src/taskbrew/dashboard/templates/settings.html"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _function_block(source: str, name: str) -> str:
    start = source.index(f"function {name}")
    brace = source.index("{", start)
    depth = 0
    for index in range(brace, len(source)):
        char = source[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[start : index + 1]
    raise AssertionError(f"Could not find end of function {name}")


def test_create_project_modal_has_one_submit_step():
    source = _read(TEMPLATE)
    start = source.index("<!-- Create Project Wizard -->")
    end = source.index("<!-- Top Navigation Bar -->")
    modal_markup = source[start:end]

    assert "wizard-steps" not in modal_markup
    assert "wizardPrevBtn" not in modal_markup
    assert 'onclick="wizardNextStep()"' in modal_markup
    assert ">Create Project</button>" in modal_markup


def test_single_step_renderer_contains_all_project_setup_sections():
    for path in (TEMPLATE, STATIC_JS):
        block = _function_block(_read(path), "renderWizardStep")

        assert "Project Identity" in block
        assert "CLI Provider" in block
        assert "Default Agent Models" in block
        assert "System AI Profile" in block
        assert "Agent Setup" in block
        assert "projectWizardStep ===" not in block
        assert "step-dot" not in block


def test_create_project_wizard_tracks_system_agent_profile():
    for path in (TEMPLATE, STATIC_JS):
        source = _read(path)

        assert "system_agent" in source
        assert "renderProjectSystemAgentSection" in source
        assert "setProjectWizardSystemProvider" in source
        assert "setProjectWizardSystemMaxReviewRounds" in source


def test_create_project_wizard_defaults_to_codex_gpt55_without_verifier():
    for path in (TEMPLATE, STATIC_JS):
        source = _read(path)
        render_block = _function_block(source, "renderWizardStep")
        open_block = _function_block(source, "openCreateProjectWizard")

        assert "const DEFAULT_PROJECT_ROLES = ['pm', 'architect', 'coder'];" in source
        assert "verifier" not in render_block.lower()
        assert "cli_provider: 'codex'" in open_block
        assert "initializeProjectWizardRoleModels('codex')" in open_block
        assert "initializeProjectWizardSystemAgent('codex')" in open_block
        assert "Scaffolds PM, Architect, and Coder" in render_block


def test_settings_page_contains_system_agent_controls():
    source = _read(SETTINGS_TEMPLATE)

    assert "System AI Profile" in source
    assert "updateSystemAgentProvider" in source
    assert "System Review Rounds" in source
    assert "system_agent" in source


def test_create_project_submit_does_not_advance_between_steps():
    for path in (TEMPLATE, STATIC_JS):
        block = _function_block(_read(path), "wizardNextStep")

        assert "projectWizardStep = 2" not in block
        assert "projectWizardStep = 3" not in block
        assert "Create Project" in block
