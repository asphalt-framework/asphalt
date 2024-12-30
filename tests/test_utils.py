from __future__ import annotations

import asyncio
import sys
from collections.abc import Callable
from functools import partial
from typing import Any
from unittest.mock import Mock

import pytest

from asphalt.core import (
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
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs


@pytest.mark.parametrize(
    "inputval",
    ["asphalt.core:resolve_reference", resolve_reference],
    ids=["reference", "object"],
)
def test_resolve_reference(inputval: Any) -> None:
    assert resolve_reference(inputval) is resolve_reference


@pytest.mark.parametrize(
    "inputval, error_type, error_text",
    [
        pytest.param(
            "x.y:foo",
            LookupError,
            "error resolving reference x.y:foo: could not import module",
            id="module_not_found",
        ),
        pytest.param(
            "asphalt.core:foo",
            LookupError,
            "error resolving reference asphalt.core:foo: error looking up object",
            id="object_not_found",
        ),
    ],
)
def test_resolve_reference_error(
    inputval: str, error_type: type[Exception], error_text: str
) -> None:
    exc = pytest.raises(error_type, resolve_reference, inputval)
    assert str(exc.value) == error_text


def test_merge_config() -> None:
    original = {"a": 1, "b": 2.5, "x.y.z": [3, 4]}
    overrides = {"a": 2, "foo": 6, "x.y.z": [6, 7]}
    expected = {"a": 2, "b": 2.5, "foo": 6, "x.y.z": [6, 7]}
    assert merge_config(original, overrides) == expected


@pytest.mark.parametrize(
    "original, overrides",
    [
        pytest.param(None, {"a": 1}, id="original_none"),
        pytest.param({"a": 1}, None, id="override_none"),
    ],
)
def test_merge_config_none_args(
    original: dict[str, Any] | None, overrides: dict[str, Any] | None
) -> None:
    assert merge_config(original, overrides) == {"a": 1}


@pytest.mark.parametrize(
    "inputval, expected",
    [
        pytest.param(qualified_name, "function", id="func"),
        pytest.param(asyncio.Event(), "asyncio.locks.Event", id="instance"),
        pytest.param(int, "int", id="builtintype"),
    ],
)
def test_qualified_name(inputval: object, expected: str) -> None:
    assert qualified_name(inputval) == expected


@pytest.mark.parametrize(
    "inputval, expected",
    [
        pytest.param(qualified_name, "asphalt.core.qualified_name", id="python"),
        pytest.param(len, "len", id="builtin"),
        pytest.param(partial(len, []), "len", id="partial"),
    ],
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
            pytest.param(f"{__name__}:DummyPlugin", id="reference"),
            pytest.param(DummyPlugin, id="arbitrary_object"),
        ],
    )
    def test_resolve(self, container: PluginContainer, inputvalue: type | str) -> None:
        assert container.resolve(inputvalue) is DummyPlugin

    def test_resolve_bad_entrypoint(self, container: PluginContainer) -> None:
        with pytest.raises(
            LookupError,
            match="no such entry point in asphalt.core.test_plugin_container: blah",
        ):
            container.resolve("blah")

    @pytest.mark.parametrize(
        "argument",
        [
            pytest.param(DummyPlugin, id="explicit_class"),
            pytest.param("dummy", id="entrypoint"),
        ],
    )
    def test_create_object(
        self, container: PluginContainer, argument: type | str
    ) -> None:
        """
        Test that create_object works with all three supported ways of passing a plugin
        class reference.

        """
        component = container.create_object(argument, a=5, b=2)

        assert isinstance(component, DummyPlugin)
        assert component.kwargs == {"a": 5, "b": 2}

    def test_create_object_bad_type(self, container: PluginContainer) -> None:
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

    def test_repr(self, container: PluginContainer) -> None:
        assert repr(container) == (
            "PluginContainer(namespace='asphalt.core.test_plugin_container', "
            "base_class=test_utils.BaseDummyPlugin)"
        )
