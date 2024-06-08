from __future__ import annotations

import sys
from typing import Any, NoReturn
from unittest.mock import Mock

import anyio
import pytest
from anyio import sleep
from common import raises_in_exception_group
from pytest import MonkeyPatch

from asphalt.core import (
    CLIApplicationComponent,
    Component,
    Context,
    add_resource,
    get_resource_nowait,
    run_application,
    start_component,
)
from asphalt.core._component import component_types

pytestmark = pytest.mark.anyio()

if sys.version_info >= (3, 10):
    from importlib.metadata import EntryPoint
else:
    from importlib_metadata import EntryPoint


class DummyComponent(Component):
    def __init__(
        self,
        alias: str | None = None,
        container: dict[str, DummyComponent] | None = None,
        **kwargs: Any,
    ):
        self.kwargs = kwargs
        self.alias = alias
        self.container = container

    async def start(self) -> None:
        await anyio.sleep(0.1)
        if self.alias and self.container is not None:
            self.container[self.alias] = self


@pytest.fixture(autouse=True)
def monkeypatch_plugins(monkeypatch: MonkeyPatch) -> None:
    entrypoint = Mock(EntryPoint)
    entrypoint.load.configure_mock(return_value=DummyComponent)
    monkeypatch.setattr(component_types, "_entrypoints", {"dummy": entrypoint})


class TestComplexComponent:
    @pytest.mark.parametrize(
        "component_type",
        [
            pytest.param(DummyComponent, id="class"),
            pytest.param("dummy", id="entrypoint"),
        ],
    )
    async def test_add_component(self, component_type: type[Component] | str) -> None:
        """
        Test that add_component works with an without an entry point and that external
        configuration overriddes directly supplied configuration values.

        """
        components_container: dict[str, DummyComponent] = {}
        container = Component()
        container.add_component(
            "dummy1",
            component_type,
            alias="dummy1",
            container=components_container,
            a=5,
            b=2,
        )
        container.add_component(
            "dummy2",
            component_type,
            alias="dummy2",
            container=components_container,
            a=8,
            b=7,
        )
        async with Context():
            await start_component(container)

        assert len(components_container) == 2
        assert components_container["dummy1"].kwargs == {"a": 5, "b": 2}
        assert components_container["dummy2"].kwargs == {"a": 8, "b": 7}

    @pytest.mark.parametrize(
        "alias, cls, exc_cls, message",
        [
            pytest.param(
                "", None, TypeError, "alias must be a nonempty string", id="empty_alias"
            ),
            pytest.param(
                "foo",
                None,
                LookupError,
                "no such entry point in asphalt.components: foo",
                id="bogus_entry_point",
            ),
            pytest.param(
                "foo",
                int,
                TypeError,
                "int is not a subclass of asphalt.core.Component",
                id="wrong_subclass",
            ),
            pytest.param(
                "foo",
                4,
                TypeError,
                "type must be either a subclass of asphalt.core.Component or a string",
                id="invalid_type",
            ),
        ],
    )
    def test_add_component_errors(
        self,
        alias: str,
        cls: type | None,
        exc_cls: type[Exception],
        message: str,
    ) -> None:
        container = Component()
        exc = pytest.raises(exc_cls, container.add_component, alias, cls)
        assert str(exc.value) == message

    def test_add_duplicate_component(self) -> None:
        container = Component()
        container.add_component("dummy")
        exc = pytest.raises(ValueError, container.add_component, "dummy")
        assert str(exc.value) == 'there is already a child component named "dummy"'

    async def test_add_component_during_start(self) -> None:
        class BadContainerComponent(Component):
            async def start(self) -> None:
                self.add_component("foo", DummyComponent)

        async with Context():
            with pytest.raises(RuntimeError, match="child components cannot be added"):
                await start_component(BadContainerComponent())


class TestCLIApplicationComponent:
    def test_run_return_none(self, anyio_backend_name: str) -> None:
        class DummyCLIComponent(CLIApplicationComponent):
            async def run(self) -> None:
                pass

        # No exception should be raised here
        run_application(DummyCLIComponent(), backend=anyio_backend_name)

    def test_run_return_5(self, anyio_backend_name: str) -> None:
        class DummyCLIComponent(CLIApplicationComponent):
            async def run(self) -> int:
                return 5

        with pytest.raises(SystemExit) as exc:
            run_application(DummyCLIComponent(), backend=anyio_backend_name)

        assert exc.value.code == 5

    def test_run_return_invalid_value(self, anyio_backend_name: str) -> None:
        class DummyCLIComponent(CLIApplicationComponent):
            async def run(self) -> int:
                return 128

        with pytest.raises(SystemExit) as exc:
            with pytest.warns(UserWarning) as record:
                run_application(DummyCLIComponent(), backend=anyio_backend_name)

        assert exc.value.code == 1
        assert len(record) == 1
        assert str(record[0].message) == "exit code out of range: 128"

    def test_run_return_invalid_type(self, anyio_backend_name: str) -> None:
        class DummyCLIComponent(CLIApplicationComponent):
            async def run(self) -> int:
                return "foo"  # type: ignore[return-value]

        with pytest.raises(SystemExit) as exc:
            with pytest.warns(UserWarning) as record:
                run_application(DummyCLIComponent(), backend=anyio_backend_name)

        assert exc.value.code == 1
        assert len(record) == 1
        assert str(record[0].message) == "run() must return an integer or None, not str"

    def test_run_exception(self, anyio_backend_name: str) -> None:
        class DummyCLIComponent(CLIApplicationComponent):
            async def run(self) -> NoReturn:
                raise Exception("blah")

        with raises_in_exception_group(Exception, match="blah"):
            run_application(DummyCLIComponent(), backend=anyio_backend_name)


async def test_start_component_no_context() -> None:
    with pytest.raises(
        RuntimeError, match=r"start_component\(\) requires an active Asphalt context"
    ):
        await start_component(DummyComponent())


async def test_start_component_timeout() -> None:
    class StallingComponent(Component):
        async def start(self) -> None:
            await sleep(3)
            pytest.fail("Shouldn't reach this point")

    async with Context():
        with pytest.raises(TimeoutError, match="timeout starting component"):
            await start_component(StallingComponent(), timeout=0.01)


async def test_prepare() -> None:
    class ParentComponent(Component):
        def __init__(self) -> None:
            self.add_component("child", ChildComponent)

        async def prepare(self) -> None:
            add_resource("foo")

        async def start(self) -> None:
            get_resource_nowait(str, "bar")

    class ChildComponent(Component):
        async def start(self) -> None:
            foo = get_resource_nowait(str)
            add_resource(foo + "bar", "bar")

    async with Context():
        await start_component(ParentComponent())
        assert get_resource_nowait(str, "bar") == "foobar"
