from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from typing import Any, Callable, NoReturn
from unittest.mock import Mock

import anyio
import pytest
from anyio import Event, fail_after, get_current_task, sleep
from anyio.abc import TaskStatus
from pytest import LogCaptureFixture, MonkeyPatch

from asphalt.core import (
    CLIApplicationComponent,
    Component,
    ComponentStartError,
    Context,
    add_resource,
    add_resource_factory,
    get_resource,
    get_resource_nowait,
    get_resources,
    run_application,
    start_background_task_factory,
    start_component,
    start_service_task,
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
    async def test_add_component(
        self, component_type: type[Component] | str, caplog: LogCaptureFixture
    ) -> None:
        """
        Test that add_component works with an without an entry point and that external
        configuration overriddes directly supplied configuration values.

        """
        caplog.set_level(logging.DEBUG, "asphalt.core")
        components_container: dict[str, DummyComponent] = {}

        class ContainerComponent(Component):
            def __init__(self) -> None:
                self.add_component(
                    "dummy/alt",
                    alias="dummy/alt",
                    container=components_container,
                    a=8,
                    b=7,
                )

        async with Context():
            await start_component(ContainerComponent)

        assert len(components_container) == 1
        assert components_container["dummy/alt"].kwargs == {"a": 8, "b": 7}
        assert caplog.messages == [
            "Creating the root component (test_component.TestComplexComponent"
            ".test_add_component.<locals>.ContainerComponent)",
            "Created the root component (test_component.TestComplexComponent"
            ".test_add_component.<locals>.ContainerComponent)",
            "Creating component 'dummy/alt' (test_component.DummyComponent)",
            "Created component 'dummy/alt' (test_component.DummyComponent)",
            "Starting the child components of the root component",
            "Calling start() of component 'dummy/alt'",
            "Returned from start() of component 'dummy/alt'",
        ]

    async def test_child_components_from_config(self) -> None:
        container: dict[str, Component] = {}
        async with Context():
            await start_component(
                Component,
                {
                    "components": {
                        "dummy": {"alias": "dummy", "container": container},
                        "dummy/2": None,
                    }
                },
            )

        assert isinstance(container["dummy"], DummyComponent)

    async def test_type_from_partitioned_alias(self) -> None:
        container: dict[str, Component] = {}
        async with Context():
            await start_component(
                Component,
                {
                    "components": {
                        "dummy/first": {"alias": "first", "container": container},
                        "dummy/second": {"alias": "second", "container": container},
                    }
                },
            )

        assert isinstance(container["first"], DummyComponent)
        assert isinstance(container["second"], DummyComponent)

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
            with pytest.raises(
                ComponentStartError, match="child components cannot be added"
            ):
                await start_component(BadContainerComponent)


class TestCLIApplicationComponent:
    def test_run_return_none(self, anyio_backend_name: str) -> None:
        class DummyCLIComponent(CLIApplicationComponent):
            async def run(self) -> None:
                pass

        # No exception should be raised here
        run_application(DummyCLIComponent, backend=anyio_backend_name)

    def test_run_return_5(self, anyio_backend_name: str) -> None:
        class DummyCLIComponent(CLIApplicationComponent):
            async def run(self) -> int:
                return 5

        with pytest.raises(SystemExit) as exc:
            run_application(DummyCLIComponent, backend=anyio_backend_name)

        assert exc.value.code == 5

    def test_run_return_invalid_value(self, anyio_backend_name: str) -> None:
        class DummyCLIComponent(CLIApplicationComponent):
            async def run(self) -> int:
                return 128

        with pytest.raises(SystemExit) as exc:
            with pytest.warns(UserWarning) as record:
                run_application(DummyCLIComponent, backend=anyio_backend_name)

        assert exc.value.code == 1
        print(str(record[0].message))
        assert len(record) == 1
        assert str(record[0].message) == "exit code out of range: 128"

    def test_run_return_invalid_type(self, anyio_backend_name: str) -> None:
        class DummyCLIComponent(CLIApplicationComponent):
            async def run(self) -> int:
                return "foo"  # type: ignore[return-value]

        with pytest.raises(SystemExit) as exc:
            with pytest.warns(UserWarning) as record:
                run_application(DummyCLIComponent, backend=anyio_backend_name)

        assert exc.value.code == 1
        assert len(record) == 1
        assert str(record[0].message) == "run() must return an integer or None, not str"

    def test_run_exception(self, anyio_backend_name: str) -> None:
        class DummyCLIComponent(CLIApplicationComponent):
            async def run(self) -> NoReturn:
                raise Exception("blah")

        with pytest.raises(Exception, match="blah"):
            run_application(DummyCLIComponent, backend=anyio_backend_name)


@pytest.mark.parametrize("alias", ["", 6])
def test_component_bad_alias(alias: object) -> None:
    component = Component()
    with pytest.raises(TypeError, match="alias must be a nonempty string"):
        component.add_component(alias, Component)  # type: ignore[arg-type]


async def test_start_component_bad_root_config_type() -> None:
    async with Context():
        with pytest.raises(
            TypeError,
            match=r"config must be a dict \(or any other mutable mapping\) or None",
        ):
            await start_component(Component, "foo")  # type: ignore[call-overload]


async def test_start_component_bad_child_config_type() -> None:
    config = {"components": {"child": "foo"}}
    async with Context():
        with pytest.raises(
            TypeError,
            match=r"child: component configuration must be either None or a dict \(or "
            r"any other mutable mapping type\), not str",
        ):
            await start_component(Component, config)


async def test_start_component_bad_class() -> None:
    config = {"components": {"child": {"type": 5}}}
    async with Context():
        with pytest.raises(
            TypeError,
            match=r"child: the declared component type \(5\) resolved to 5 which is "
            r"not a subclass of Component",
        ):
            await start_component(Component, config)


async def test_start_component_error_during_init() -> None:
    class BadComponent(Component):
        def __init__(self) -> None:
            raise RuntimeError("component fail")

    async with Context():
        with pytest.raises(
            ComponentStartError,
            match=rf"error creating the root component \({__name__}"
            rf".test_start_component_error_during_init.<locals>.BadComponent\): "
            rf"RuntimeError: component fail",
        ) as exc:
            await start_component(BadComponent, {})

    assert isinstance(exc.value.__cause__, RuntimeError)
    assert str(exc.value.__cause__) == "component fail"


async def test_start_component_error_during_prepare() -> None:
    class BadComponent(Component):
        async def prepare(self) -> None:
            raise RuntimeError("component fail")

    async with Context():
        with pytest.raises(
            ComponentStartError,
            match=rf"error preparing the root component \({__name__}"
            rf".test_start_component_error_during_prepare.<locals>"
            rf".BadComponent\): RuntimeError: component fail",
        ) as exc:
            await start_component(BadComponent, {})

    assert isinstance(exc.value.__cause__, RuntimeError)
    assert str(exc.value.__cause__) == "component fail"


async def test_start_component_no_context() -> None:
    with pytest.raises(
        RuntimeError, match=r"start_component\(\) requires an active Asphalt context"
    ):
        await start_component(DummyComponent)


async def test_start_component_timeout() -> None:
    class StallingComponent(Component):
        async def start(self) -> None:
            await sleep(3)
            pytest.fail("Shouldn't reach this point")

    async with Context():
        with pytest.raises(TimeoutError, match="timeout starting component"):
            await start_component(StallingComponent, timeout=0.01)


async def test_prepare(caplog: LogCaptureFixture) -> None:
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

    caplog.set_level(logging.DEBUG, "asphalt.core")
    async with Context():
        await start_component(ParentComponent)
        assert get_resource_nowait(str, "bar") == "foobar"

    assert caplog.messages == [
        "Creating the root component "
        "(test_component.test_prepare.<locals>.ParentComponent)",
        "Created the root component "
        "(test_component.test_prepare.<locals>.ParentComponent)",
        "Creating component 'child' "
        "(test_component.test_prepare.<locals>.ChildComponent)",
        "Created component 'child' "
        "(test_component.test_prepare.<locals>.ChildComponent)",
        "Calling prepare() of the root component",
        "The root component added a resource (type=str, name='default')",
        "Returned from prepare() of the root component",
        "Starting the child components of the root component",
        "Calling start() of component 'child'",
        "Component 'child' added a resource (type=str, name='bar')",
        "Returned from start() of component 'child'",
        "Calling start() of the root component",
        "Returned from start() of the root component",
    ]


async def test_resource_descriptions(caplog: LogCaptureFixture) -> None:
    class CustomComponent(Component):
        async def start(self) -> None:
            add_resource("foo", "bar", description="sample string")
            add_resource_factory(
                lambda: 3,
                "bar",
                types=[int, float],
                description="sample integer factory",
            )
            assert await get_resource(float, optional=True) is None
            assert get_resource_nowait(float, optional=True) is None
            assert get_resources(str) == {"bar": "foo"}

    caplog.set_level(logging.DEBUG, "asphalt.core")
    async with Context():
        await start_component(CustomComponent)
        assert get_resource_nowait(str, "bar") == "foo"
        assert get_resource_nowait(int, "bar") == 3
        assert get_resource_nowait(float, "bar") == 3

    assert caplog.messages == [
        "Creating the root component "
        "(test_component.test_resource_descriptions.<locals>.CustomComponent)",
        "Created the root component "
        "(test_component.test_resource_descriptions.<locals>.CustomComponent)",
        "Calling start() of the root component",
        "The root component added a resource (type=str, name='bar', "
        "description='sample string')",
        "The root component added a resource factory (types=[int, float], name='bar', "
        "description='sample integer factory')",
        "Returned from start() of the root component",
    ]


async def test_wait_for_resource(caplog: LogCaptureFixture) -> None:
    class ParentComponent(Component):
        def __init__(self) -> None:
            self.add_component("child1", Child1Component)
            self.add_component("child2", Child2Component)

    class Child1Component(Component):
        async def start(self) -> None:
            child1_ready_event.set()
            await child2_ready_event.wait()
            add_resource("from_child1", "special")

    class Child2Component(Component):
        async def start(self) -> None:
            await child1_ready_event.wait()
            child2_ready_event.set()
            with fail_after(3):
                assert await get_resource(str, "special") == "from_child1"

    caplog.set_level(logging.DEBUG, "asphalt.core")
    child1_ready_event = Event()
    child2_ready_event = Event()
    async with Context():
        await start_component(ParentComponent)

    assert caplog.messages[:7] == [
        "Creating the root component "
        "(test_component.test_wait_for_resource.<locals>.ParentComponent)",
        "Created the root component "
        "(test_component.test_wait_for_resource.<locals>.ParentComponent)",
        "Creating component 'child1' "
        "(test_component.test_wait_for_resource.<locals>.Child1Component)",
        "Created component 'child1' "
        "(test_component.test_wait_for_resource.<locals>.Child1Component)",
        "Creating component 'child2' "
        "(test_component.test_wait_for_resource.<locals>.Child2Component)",
        "Created component 'child2' "
        "(test_component.test_wait_for_resource.<locals>.Child2Component)",
        "Starting the child components of the root component",
    ]
    # Trio's nondeterministic scheduling forces us to do this
    assert sorted(caplog.messages[7:9]) == [
        "Calling start() of component 'child1'",
        "Calling start() of component 'child2'",
    ]
    assert caplog.messages[9:] == [
        "Component 'child2' is waiting for another component to provide a resource "
        "(type=str, name='special')",
        "Component 'child1' added a resource (type=str, name='special')",
        "Returned from start() of component 'child1'",
        "Component 'child2' got the resource it was waiting for (type=str, "
        "name='special')",
        "Returned from start() of component 'child2'",
    ]


async def test_default_resource_names(caplog: LogCaptureFixture) -> None:
    class ParentComponent(Component):
        def __init__(self) -> None:
            self.add_component("child/1", ChildComponent, name="child1")
            self.add_component("child/2", ChildComponent, name="child2")

    @dataclass
    class ChildComponent(Component):
        name: str

        async def start(self) -> None:
            add_resource("default_resource")
            add_resource(f"special_resource_{self.name}", self.name)
            add_resource_factory(lambda: 7, types=[int])

    caplog.set_level(logging.DEBUG, "asphalt.core")
    async with Context():
        await start_component(ParentComponent)
        assert get_resource_nowait(int, "1") == 7
        assert get_resource_nowait(int, "2") == 7

    assert caplog.messages[:7] == [
        "Creating the root component "
        "(test_component.test_default_resource_names.<locals>.ParentComponent)",
        "Created the root component "
        "(test_component.test_default_resource_names.<locals>.ParentComponent)",
        "Creating component 'child/1' "
        "(test_component.test_default_resource_names.<locals>.ChildComponent)",
        "Created component 'child/1' "
        "(test_component.test_default_resource_names.<locals>.ChildComponent)",
        "Creating component 'child/2' "
        "(test_component.test_default_resource_names.<locals>.ChildComponent)",
        "Created component 'child/2' "
        "(test_component.test_default_resource_names.<locals>.ChildComponent)",
        "Starting the child components of the root component",
    ]
    assert sorted(caplog.messages[7:], key=lambda line: "child/2" in line) == [
        "Calling start() of component 'child/1'",
        "Component 'child/1' added a resource (type=str, name='1')",
        "Component 'child/1' added a resource (type=str, name='child1')",
        "Component 'child/1' added a resource factory (types=[int], name='1')",
        "Returned from start() of component 'child/1'",
        "Calling start() of component 'child/2'",
        "Component 'child/2' added a resource (type=str, name='2')",
        "Component 'child/2' added a resource (type=str, name='child2')",
        "Component 'child/2' added a resource factory (types=[int], name='2')",
        "Returned from start() of component 'child/2'",
    ]


async def test_start_background_task_factory(caplog: LogCaptureFixture) -> None:
    def handle_exception(exception: Exception) -> bool:
        return False

    class DummyComponent(Component):
        async def start(self) -> None:
            factory = await start_background_task_factory(
                exception_handler=handle_exception
            )
            assert factory.exception_handler is handle_exception

    caplog.set_level(logging.DEBUG, "asphalt.core")
    async with Context():
        await start_component(DummyComponent)

    assert "The root component started a background task factory" in caplog.messages


async def test_start_service_task(caplog: LogCaptureFixture) -> None:
    async def taskfunc(*, task_status: TaskStatus[None]) -> None:
        assert get_current_task().name == "Service task: servicetask"
        task_status.started()

    class DummyComponent(Component):
        async def start(self) -> None:
            await start_service_task(taskfunc, "servicetask")

    caplog.set_level(logging.DEBUG, "asphalt.core")
    async with Context():
        await start_component(DummyComponent)

    assert "The root component started a service task (servicetask)" in caplog.messages


async def test_component_generic_resource(caplog) -> None:
    resource0 = lambda: "foo"
    resource1 = lambda: 3

    class ChildComponent(Component):
        async def start(self) -> None:
            add_resource(resource0, types=Callable[[], str])
            add_resource(resource1, types=Callable[[], int])

    class ParentComponent(Component):
        def __init__(self) -> None:
            self.add_component("child", ChildComponent)

        async def start(self) -> None:
            _resource0 = await get_resource(Callable[[], str])
            _resource1 = await get_resource(Callable[[], int])
            assert _resource0 == resource0
            assert _resource1 == resource1

    async with Context():
        with caplog.at_level(logging.DEBUG, logger="asphalt.core"):
            await start_component(ParentComponent)

    assert "Component 'child' added a resource (type=typing._CallableGenericAlias, name='default')" in caplog.text
