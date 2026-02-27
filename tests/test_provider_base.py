"""Tests for the abstract provider interface."""
import pytest
from taskbrew.agents.provider_base import ProviderPlugin


def test_provider_plugin_is_abstract():
    """Cannot instantiate ProviderPlugin directly."""
    with pytest.raises(TypeError):
        ProviderPlugin()


def test_concrete_provider_must_implement_query():
    """A concrete provider missing query() cannot be instantiated."""
    class IncompleteProvider(ProviderPlugin):
        name = "incomplete"
        detect_patterns = ["inc-*"]
        def build_options(self, **kwargs):
            return {}

    with pytest.raises(TypeError):
        IncompleteProvider()


def test_concrete_provider_works():
    """A fully implemented provider can be instantiated."""
    class MockProvider(ProviderPlugin):
        name = "mock"
        detect_patterns = ["mock-*"]
        def build_options(self, **kwargs):
            return {}
        async def query(self, prompt, options):
            yield None
        def get_message_types(self):
            return {}

    p = MockProvider()
    assert p.name == "mock"
    assert p.detect_patterns == ["mock-*"]


def test_default_message_types():
    """Default get_message_types returns the base message classes."""
    from taskbrew.agents.provider_base import (
        AssistantMessage, ResultMessage, TextBlock, ToolUseBlock,
    )

    class TestProvider(ProviderPlugin):
        name = "test"
        detect_patterns = []
        def build_options(self, **kwargs):
            return {}
        async def query(self, prompt, options):
            yield None

    p = TestProvider()
    types = p.get_message_types()
    assert types["AssistantMessage"] is AssistantMessage
    assert types["ResultMessage"] is ResultMessage
    assert types["TextBlock"] is TextBlock
    assert types["ToolUseBlock"] is ToolUseBlock
