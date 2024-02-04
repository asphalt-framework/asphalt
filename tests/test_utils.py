from __future__ import annotations

import asyncio
import sys
from collections.abc import Callable
from typing import Any
from unittest.mock import Mock

import pytest

from asphalt.core import PluginContainer, callable_name, merge_config, qualified_name

if sys.version_info >= (3, 10):
    from importlib.metadata import EntryPoint
else:
    from importlib_metadata import EntryPoint


class BaseDummyPlugin:
    pass


class DummyPlugin(BaseDummyPlugin):
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs


def test_merge_config() -> None:
    original = {"a": 1, "b": 2.5, "x.y.z": [3, 4]}
    overrides = {"a": 2, "foo": 6, "x.y.z": [6, 7]}
    expected = {"a": 2, "b": 2.5, "foo": 6, "x.y.z": [6, 7]}
    assert merge_config(original, overrides) == expected


@pytest.mark.parametrize(
    "original, overrides",
    [
        (None, {"a": 1}),
        ({"a": 1}, None),
    ],
    ids=["original_none", "override_none"],
)
def test_merge_config_none_args(original, overrides) -> None:
    assert merge_config(original, overrides) == {"a": 1}


@pytest.mark.parametrize(
    "inputval, expected",
    [
        (qualified_name, "function"),
        (asyncio.Event(), "asyncio.locks.Event"),
        (int, "int"),
    ],
    ids=["func", "instance", "builtintype"],
)
def test_qualified_name(inputval, expected) -> None:
    assert qualified_name(inputval) == expected


@pytest.mark.parametrize(
    "inputval, expected",
    [(qualified_name, "asphalt.core.qualified_name"), (len, "len")],
    ids=["python", "builtin"],
)
def test_callable_name(inputval: Callable[..., Any], expected: str) -> None:
    assert callable_name(inputval) == expected


class TestPluginContainer:
    @pytest.fixture
    def container(self) -> PluginContainer:
        container = PluginContainer(
            "asphalt.core.test_plugin_container", BaseDummyPlugin
        )
        entrypoint = Mock(EntryPoint)
        entrypoint.load.configure_mock(return_value=DummyPlugin)
        container._entrypoints = {"dummy": entrypoint}
        return container

    @pytest.mark.parametrize(
        "inputvalue",
        [
            pytest.param("dummy", id="entrypoint"),
            pytest.param(DummyPlugin, id="arbitrary_object"),
        ],
    )
    def test_resolve(self, container: PluginContainer, inputvalue) -> None:
        assert container.resolve(inputvalue) is DummyPlugin

    def test_resolve_bad_entrypoint(self, container):
        exc = pytest.raises(LookupError, container.resolve, "blah")
        assert (
            str(exc.value)
            == "no such entry point in asphalt.core.test_plugin_container: blah"
        )

    @pytest.mark.parametrize(
        "argument",
        [
            pytest.param(DummyPlugin, id="explicit_class"),
            pytest.param("dummy", id="entrypoint"),
        ],
    )
    def test_create_object(self, container: PluginContainer, argument) -> None:
        """
        Test that create_object works with all three supported ways of passing a plugin
        class reference.

        """
        component = container.create_object(argument, a=5, b=2)

        assert isinstance(component, DummyPlugin)
        assert component.kwargs == {"a": 5, "b": 2}

    def test_create_object_bad_type(self, container) -> None:
        exc = pytest.raises(TypeError, container.create_object, int)
        assert str(exc.value) == "int is not a subclass of test_utils.BaseDummyPlugin"

    def test_names(self, container: PluginContainer) -> None:
        assert container.names == ["dummy"]

    def test_all(self, container: PluginContainer) -> None:
        """
        Test that all() returns the same results before and after the entry points have
        been loaded.

        """
        assert container.all() == [DummyPlugin]
        assert container.all() == [DummyPlugin]

    def test_repr(self, container) -> None:
        assert repr(container) == (
            "PluginContainer(namespace='asphalt.core.test_plugin_container', "
            "base_class=test_utils.BaseDummyPlugin)"
        )
