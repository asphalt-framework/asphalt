from __future__ import annotations

import sys
from typing import NoReturn
from unittest.mock import Mock

import anyio
import pytest
from asphalt.core import (
    CLIApplicationComponent,
    Component,
    ContainerComponent,
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
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.started = False

    async def start(self, ctx):
        await anyio.sleep(0.1)
        self.started = True


@pytest.fixture(autouse=True)
def monkeypatch_plugins(monkeypatch):
    entrypoint = Mock(EntryPoint)
    entrypoint.load.configure_mock(return_value=DummyComponent)
    monkeypatch.setattr(component_types, "_entrypoints", {"dummy": entrypoint})


class TestContainerComponent:
    @pytest.fixture
    def container(self) -> ContainerComponent:
        return ContainerComponent({"dummy": {"a": 1, "c": 3}})

    def test_add_component(self, container: ContainerComponent) -> None:
        """
        Test that add_component works with an without an entry point and that external
        configuration overriddes directly supplied configuration values.

        """
        container.add_component("dummy", DummyComponent, a=5, b=2)

        assert len(container.child_components) == 1
        component = container.child_components["dummy"]
        assert isinstance(component, DummyComponent)
        assert component.kwargs == {"a": 1, "b": 2, "c": 3}

    def test_add_component_with_type(self) -> None:
        """
        Test that add_component works with a `type` specified in a
        configuration overriddes directly supplied configuration values.

        """
        container = ContainerComponent({"dummy": {"type": DummyComponent}})
        container.add_component("dummy")
        assert len(container.child_components) == 1
        component = container.child_components["dummy"]
        assert isinstance(component, DummyComponent)

    @pytest.mark.parametrize(
        "alias, cls, exc_cls, message",
        [
            ("", None, TypeError, "component_alias must be a nonempty string"),
            (
                "foo",
                None,
                LookupError,
                "no such entry point in asphalt.components: foo",
            ),
            (
                "foo",
                int,
                TypeError,
                "int is not a subclass of asphalt.core.Component",
            ),
        ],
        ids=["empty_alias", "bogus_entry_point", "wrong_subclass"],
    )
    def test_add_component_errors(
        self,
        container: ContainerComponent,
        alias: str,
        cls: type | None,
        exc_cls: type[Exception],
        message: str,
    ) -> None:
        exc = pytest.raises(exc_cls, container.add_component, alias, cls)
        assert str(exc.value) == message

    def test_add_duplicate_component(self, container) -> None:
        container.add_component("dummy")
        exc = pytest.raises(ValueError, container.add_component, "dummy")
        assert str(exc.value) == 'there is already a child component named "dummy"'

    async def test_start(self, container) -> None:
        await start_component(container)
        assert container.child_components["dummy"].started


class TestCLIApplicationComponent:
    async def test_run_return_none(self) -> None:
        class DummyCLIComponent(CLIApplicationComponent):
            async def run(self) -> None:
                pass

        # No exception should be raised here
        await run_application(DummyCLIComponent())

    async def test_run_return_5(self) -> None:
        class DummyCLIComponent(CLIApplicationComponent):
            async def run(self) -> int:
                return 5

        with pytest.raises(SystemExit) as exc:
            await run_application(DummyCLIComponent())

        assert exc.value.code == 5

    async def test_run_return_invalid_value(self) -> None:
        class DummyCLIComponent(CLIApplicationComponent):
            async def run(self) -> int:
                return 128

        with pytest.raises(SystemExit) as exc:
            with pytest.warns(UserWarning) as record:
                await run_application(DummyCLIComponent())

        assert exc.value.code == 1
        assert len(record) == 1
        assert str(record[0].message) == "exit code out of range: 128"

    async def test_run_return_invalid_type(self) -> None:
        class DummyCLIComponent(CLIApplicationComponent):
            async def run(self) -> int:
                return "foo"  # type: ignore[return-value]

        with pytest.raises(SystemExit) as exc:
            with pytest.warns(UserWarning) as record:
                await run_application(DummyCLIComponent())

        assert exc.value.code == 1
        assert len(record) == 1
        assert str(record[0].message) == "run() must return an integer or None, not str"

    async def test_run_exception(self) -> None:
        class DummyCLIComponent(CLIApplicationComponent):
            async def run(self) -> NoReturn:
                raise Exception("blah")

        with pytest.raises(Exception, match="blah"):
            await run_application(DummyCLIComponent())
