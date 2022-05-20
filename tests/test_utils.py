from __future__ import annotations

import asyncio
import sys
from collections.abc import Callable
from typing import Any
from unittest.mock import Mock

import pytest

from asphalt.core.utils import (
    PluginContainer,
    callable_name,
    merge_config,
    qualified_name,
    resolve_reference,
)

if sys.version_info >= (3, 10):
    from importlib.metadata import EntryPoint
else:
    from importlib_metadata import EntryPoint


class BaseDummyPlugin:
    pass


class DummyPlugin(BaseDummyPlugin):
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs


@pytest.mark.parametrize(
    "inputval",
    ["asphalt.core.utils:resolve_reference", resolve_reference],
    ids=["reference", "object"],
)
def test_resolve_reference(inputval: Any) -> None:
    assert resolve_reference(inputval) is resolve_reference


@pytest.mark.parametrize(
    "inputval, error_type, error_text",
    [
        (
            "x.y:foo",
            LookupError,
            "error resolving reference x.y:foo: could not import module",
        ),
        (
            "asphalt.core:foo",
            LookupError,
            "error resolving reference asphalt.core:foo: error looking up object",
        ),
    ],
    ids=["module_not_found", "object_not_found"],
)
def test_resolve_reference_error(inputval, error_type, error_text) -> None:
    exc = pytest.raises(error_type, resolve_reference, inputval)
    assert str(exc.value) == error_text


@pytest.mark.parametrize(
    "overrides",
    [
        {"a": 2, "foo": 6, "b": {"x": 5, "z": {"r": "bar", "s": [6, 7]}}},
        {"a": 2, "foo": 6, "b.x": 5, "b.z": {"r": "bar", "s": [6, 7]}},
        {"a": 2, "foo": 6, "b.x": 5, "b.z.r": "bar", "b.z.s": [6, 7]},
    ],
    ids=["nested_dicts", "part_nested", "dotted_paths"],
)
def test_merge_config(overrides) -> None:
    original = {"a": 1, "b": {"x": 2, "y": 3, "z": {"r": [1, 2], "s": [3, 4]}}}
    expected = {"a": 2, "foo": 6, "b": {"x": 5, "y": 3, "z": {"r": "bar", "s": [6, 7]}}}
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
    [(qualified_name, "asphalt.core.utils.qualified_name"), (len, "len")],
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
        ["dummy", "test_utils:DummyPlugin", DummyPlugin],
        ids=["entrypoint", "reference", "arbitrary_object"],
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
        [DummyPlugin, "dummy", "test_utils:DummyPlugin"],
        ids=["explicit_class", "entrypoint", "class_reference"],
    )
    def test_create_object(self, container: PluginContainer, argument) -> None:
        """
        Test that create_object works with all three supported ways of passing a plugin class
        reference.

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
        Test that all() returns the same results before and after the entry points have been
        loaded.

        """
        assert container.all() == [DummyPlugin]
        assert container.all() == [DummyPlugin]

    def test_repr(self, container) -> None:
        assert repr(container) == (
            "PluginContainer(namespace='asphalt.core.test_plugin_container', "
            "base_class=test_utils.BaseDummyPlugin)"
        )
