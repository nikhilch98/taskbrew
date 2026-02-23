# tests/conftest.py
import pytest

@pytest.fixture
def tmp_project(tmp_path):
    """Create a temporary project directory."""
    (tmp_path / "artifacts").mkdir()
    (tmp_path / "data").mkdir()
    return tmp_path
