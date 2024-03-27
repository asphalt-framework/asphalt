from __future__ import annotations

import sys
from collections.abc import AsyncGenerator, Callable
from contextlib import AsyncExitStack, ExitStack
from itertools import count
from typing import Any, NoReturn, Optional, Tuple, Union

import anyio
import pytest
from anyio import (
    create_task_group,
    fail_after,
    get_current_task,
    sleep,
    wait_all_tasks_blocked,
)
from anyio.abc import TaskStatus
from anyio.lowlevel import checkpoint
from common import raises_in_exception_group

from asphalt.core import (
    AsyncResourceError,
    Context,
    NoCurrentContext,
    ResourceConflict,
    ResourceEvent,
    ResourceNotFound,
    context_teardown,
    current_context,
    get_resource,
    get_resource_nowait,
    inject,
    resource,
    start_service_task,
)

if sys.version_info < (3, 11):
    from exceptiongroup import ExceptionGroup

pytestmark = pytest.mark.anyio()


@pytest.fixture
async def context() -> AsyncGenerator[Context, None]:
    async with Context() as ctx:
        yield ctx


class TestContext:
    async def test_parent(self) -> None:
        """
        Test that the parent property points to the parent context instance, if any.
        """

        async with Context() as parent:
            async with Context() as child:
                assert parent.parent is None
                assert child.parent is parent

    @pytest.mark.parametrize(
        "exception", [None, Exception("foo")], ids=["noexception", "exception"]
    )
    async def test_close(self, exception: Exception | None) -> None:
        """
        Test that teardown callbacks are called in reverse order when a context is
        closed.
        """

        def callback(exception: BaseException | None = None) -> None:
            called_functions.append((callback, exception))

        async def async_callback(exception: BaseException | None = None) -> None:
            called_functions.append((async_callback, exception))

        called_functions: list[
            tuple[Callable[[BaseException | None], Any], BaseException | None]
        ] = []
        async with AsyncExitStack() as exit_stack:
            if exception:
                exit_stack.enter_context(pytest.raises(ExceptionGroup))

            context = await exit_stack.enter_async_context(Context())
            context.add_teardown_callback(callback, pass_exception=True)
            context.add_teardown_callback(async_callback, pass_exception=True)

            if exception:
                raise exception

        assert called_functions == [(async_callback, exception), (callback, exception)]

    async def test_teardown_callback_exception(self) -> None:
        """
        Test that all callbacks are called even when some teardown callbacks raise
        exceptions, and that those exceptions are reraised in such a case.
        """

        def callback1() -> None:
            items.append(1)

        def callback2() -> NoReturn:
            raise Exception("foo")

        items: list[int] = []
        with pytest.raises(ExceptionGroup) as exc:
            async with Context() as context:
                context.add_teardown_callback(callback1)
                context.add_teardown_callback(callback2)
                context.add_teardown_callback(callback1)
                context.add_teardown_callback(callback2)

        assert len(exc.value.exceptions) == 1
        assert isinstance(exc.value.exceptions[0], ExceptionGroup)
        assert len(exc.value.exceptions[0].exceptions) == 2

    @pytest.mark.parametrize("types", [int, (int,), ()], ids=["type", "tuple", "empty"])
    async def test_add_resource(
        self, context: Context, types: type | tuple[type, ...]
    ) -> None:
        """
        Test that a resource is properly added in the context and listeners are
        notified.
        """

        async def resource_adder() -> None:
            await wait_all_tasks_blocked()
            context.add_resource(6, "foo", types)

        async with create_task_group() as tg:
            tg.start_soon(resource_adder)
            with fail_after(1):
                event = await context.resource_added.wait_event()

        assert event.resource_types == (int,)
        assert event.resource_name == "foo"
        assert not event.is_factory
        assert context.get_resource_nowait(int, "foo") == 6

    async def test_add_resource_name_conflict(self, context: Context) -> None:
        """Test that adding a resource won't replace any existing resources."""
        context.add_resource(5, "foo")
        with pytest.raises(ResourceConflict) as exc:
            context.add_resource(4, "foo")

        exc.match(
            "this context already contains a resource of type int using the name 'foo'"
        )

    async def test_add_resource_none_value(self, context: Context) -> None:
        """Test that None is not accepted as a resource value."""
        with pytest.raises(ValueError, match='"value" must not be None'):
            context.add_resource(None)

    async def test_add_resource_type_conflict(self, context: Context) -> None:
        context.add_resource(5)
        with pytest.raises(ResourceConflict) as exc:
            context.add_resource(6)

        exc.match(
            "this context already contains a resource of type int using the name "
            "'default'"
        )

    @pytest.mark.parametrize(
        "name", ["a.b", "a:b", "a b"], ids=["dot", "colon", "space"]
    )
    async def test_add_resource_bad_name(self, context: Context, name: str) -> None:
        with pytest.raises(ValueError) as exc:
            context.add_resource(1, name)

        exc.match(
            '"name" must be a nonempty string consisting only of alphanumeric '
            "characters and underscores"
        )

    @pytest.mark.parametrize("nowait", [True, False])
    async def test_add_resource_factory(self, context: Context, nowait: bool) -> None:
        """
        Test that resource factory callbacks are only called once for each context, and
        that when the factory is triggered, an appropriate resource event is dispatched.

        """
        events: list[ResourceEvent] = []

        async def resource_added_listener(task_status: TaskStatus[None]) -> None:
            async with context.resource_added.stream_events() as stream:
                task_status.started()
                async for event in stream:
                    events.append(event)

        def factory() -> Union[int, float]:  # noqa: UP007
            return next(counter)

        counter = count(1)
        context.add_resource_factory(factory)

        async with create_task_group() as tg:
            await tg.start(resource_added_listener)
            if nowait:
                assert context.get_resource_nowait(int) == 1
                assert context.get_resource_nowait(int) == 1
            else:
                assert await context.get_resource(int) == 1
                assert await context.get_resource(int) == 1

            await checkpoint()
            tg.cancel_scope.cancel()

        assert len(events) == 1
        assert events[0].resource_types == (int, float)

    async def test_add_async_resource_factory(self, context: Context) -> None:
        """
        Test that async resource factory callbacks are only called once for each
        context, and that when the factory is triggered, an appropriate resource event
        is dispatched.

        """
        events: list[ResourceEvent] = []

        async def resource_added_listener(task_status: TaskStatus[None]) -> None:
            async with context.resource_added.stream_events() as stream:
                task_status.started()
                async for event in stream:
                    events.append(event)

        async def factory() -> Union[int, float]:  # noqa: UP007
            return next(counter)

        counter = count(1)
        context.add_resource_factory(factory)

        async with create_task_group() as tg:
            await tg.start(resource_added_listener)
            assert await context.get_resource(int) == 1
            assert await context.get_resource(int) == 1
            await checkpoint()
            tg.cancel_scope.cancel()

        assert len(events) == 1
        assert events[0].resource_types == (int, float)

    async def test_add_async_resource_factory_sync(self, context: Context) -> None:
        """
        Test that get_resource_nowait() raises AsyncResourceError when requesting a
        resource that would be generated by an async resource factory
        """

        async def factory() -> int:
            return 1

        context.add_resource_factory(factory)
        with pytest.raises(AsyncResourceError):
            context.get_resource_nowait(int)

    @pytest.mark.parametrize(
        "name", ["a.b", "a:b", "a b"], ids=["dot", "colon", "space"]
    )
    async def test_add_resource_factory_bad_name(
        self, context: Context, name: str
    ) -> None:
        with pytest.raises(ValueError) as exc:
            context.add_resource_factory(lambda: 1, name, types=[int])

        exc.match(
            '"name" must be a nonempty string consisting only of alphanumeric '
            "characters and underscores"
        )

    async def test_add_resource_factory_empty_types(self, context: Context) -> None:
        with pytest.raises(ValueError) as exc:
            context.add_resource_factory(lambda: 1, types=())

        exc.match("no resource types were specified")

    async def test_add_resource_factory_type_conflict(self, context: Context) -> None:
        context.add_resource_factory(lambda: None, types=(str, int))
        with pytest.raises(ResourceConflict) as exc:
            context.add_resource_factory(lambda: None, types=[int])

        exc.match("this context already contains a resource factory for the type int")

    async def test_add_resource_factory_no_inherit(self, context: Context) -> None:
        """
        Test that a subcontext gets its own version of a factory-generated resource even
        if a parent context has one already.

        """

        def factory() -> int:
            return next(counter)

        counter = count(1)
        context.add_resource_factory(factory)

        assert context.get_resource_nowait(int) == 1
        assert context.get_resource_nowait(int) == 1
        async with Context() as subcontext:
            assert subcontext.get_resource_nowait(int) == 2
            assert subcontext.get_resource_nowait(int) == 2

    async def test_add_resource_return_type_single(self, context: Context) -> None:
        def factory() -> str:
            return "foo"

        context.add_resource_factory(factory)
        assert context.get_resource_nowait(str) == "foo"

    async def test_add_resource_return_type_union(self, context: Context) -> None:
        def factory() -> Union[int, float]:  # noqa: UP007
            return 5

        context.add_resource_factory(factory)
        assert context.get_resource_nowait(int) == 5
        assert context.get_resource_nowait(float) == 5

    @pytest.mark.skipif(sys.version_info < (3, 10), reason="Requires Python 3.10+")
    async def test_add_resource_return_type_uniontype(self, context: Context) -> None:
        def factory() -> int | float:
            return 5

        context.add_resource_factory(factory)
        assert context.get_resource_nowait(int) == 5
        assert context.get_resource_nowait(float) == 5

    async def test_add_resource_return_type_optional(self, context: Context) -> None:
        def factory() -> Optional[str]:  # noqa: UP007
            return "foo"

        context.add_resource_factory(factory)
        assert context.get_resource_nowait(str) == "foo"

    async def test_get_resources(self, context: Context) -> None:
        context.add_resource(9, "foo")
        async with Context() as subctx:
            subctx.add_resource(1, "bar")
            subctx.add_resource(4, "foo")
            assert subctx.get_resources(int) == {"bar": 1, "foo": 4}

    async def test_get_resource_nowait(self, context: Context) -> None:
        context.add_resource(1)
        assert context.get_resource_nowait(int) == 1

    async def test_get_resource_not_found(self, context: Context) -> None:
        """
        Test that ResourceNotFound is raised when a required resource is not found.

        """
        exc = pytest.raises(ResourceNotFound, context.get_resource_nowait, int, "foo")
        exc.match("no matching resource was found for type=int name='foo'")
        assert exc.value.type == int
        assert exc.value.name == "foo"

    async def test_start_service_task_cancel_on_exit(self) -> None:
        started = False
        finished = False

        async def taskfunc() -> None:
            nonlocal started, finished
            assert get_current_task().name == "Service task: taskfunc"
            started = True
            await sleep(3)
            finished = True

        async with Context():
            await start_service_task(taskfunc, "taskfunc")

        assert started
        assert not finished

    async def test_start_service_task_custom_teardown_callback(self) -> None:
        started = False
        finished = False
        event = anyio.Event()

        async def taskfunc() -> None:
            nonlocal started, finished
            started = True
            with fail_after(3):
                await event.wait()

            finished = True

        async with Context():
            await start_service_task(taskfunc, "taskfunc", teardown_action=event.set)

        assert started
        assert finished

    async def test_start_service_task_status(self) -> None:
        started = False
        finished = False

        async def taskfunc(task_status: TaskStatus[str]) -> None:
            nonlocal started, finished
            assert get_current_task().name == "Service task: taskfunc"
            started = True
            task_status.started("startval")
            await sleep(3)
            finished = True

        async with Context():
            assert await start_service_task(taskfunc, "taskfunc") == "startval"

        assert started
        assert not finished


class TestContextTeardown:
    @pytest.mark.parametrize(
        "expected_exc", [None, Exception("foo")], ids=["no_exception", "exception"]
    )
    async def test_function(self, expected_exc: Exception | None) -> None:
        phase = received_exception = None

        @context_teardown
        async def start(ctx: Context) -> AsyncGenerator[None, Any]:
            nonlocal phase, received_exception
            phase = "started"
            exc = yield
            phase = "finished"
            received_exception = exc

        with ExitStack() as exit_stack:
            async with Context() as context:
                await start(context)
                assert phase == "started"
                if expected_exc:
                    exit_stack.enter_context(pytest.raises(ExceptionGroup))
                    raise expected_exc

        assert phase == "finished"
        assert received_exception == expected_exc

    @pytest.mark.parametrize(
        "expected_exc", [None, Exception("foo")], ids=["no_exception", "exception"]
    )
    async def test_method(self, expected_exc: Exception | None) -> None:
        phase = received_exception = None

        class SomeComponent:
            @context_teardown
            async def start(self, ctx: Context) -> AsyncGenerator[None, Any]:
                nonlocal phase, received_exception
                phase = "started"
                exc = yield
                phase = "finished"
                received_exception = exc

        with ExitStack() as exit_stack:
            async with Context() as context:
                await SomeComponent().start(context)
                assert phase == "started"
                if expected_exc:
                    exit_stack.enter_context(pytest.raises(ExceptionGroup))
                    raise expected_exc

        assert phase == "finished"
        assert received_exception == expected_exc

    def test_plain_function(self) -> None:
        def start() -> None:
            pass

        pytest.raises(TypeError, context_teardown, start).match(
            " must be an async generator function"
        )

    async def test_exception(self) -> None:
        @context_teardown
        async def start() -> AsyncGenerator[None, Any]:
            raise Exception("dummy error")
            yield

        async with Context():
            with pytest.raises(Exception) as exc_info:
                await start()

        exc_info.match("dummy error")

    async def test_get_resource_at_teardown(self) -> None:
        resource = ""

        async def teardown_callback() -> None:
            nonlocal resource
            resource = get_resource_nowait(str)

        async with Context() as ctx:
            ctx.add_resource("blah")
            ctx.add_teardown_callback(teardown_callback)

        assert resource == "blah"

    async def test_generate_resource_at_teardown(self) -> None:
        resource = ""

        async def teardown_callback() -> None:
            nonlocal resource
            resource = get_resource_nowait(str)

        async with Context() as ctx:
            ctx.add_resource_factory(lambda: "blah", types=[str])
            ctx.add_teardown_callback(teardown_callback)

        assert resource == "blah"


class TestContextFinisher:
    @pytest.mark.parametrize(
        "expected_exc", [None, Exception("foo")], ids=["no_exception", "exception"]
    )
    async def test_context_teardown(self, expected_exc: Exception | None) -> None:
        phase = received_exception = None

        @context_teardown
        async def start() -> AsyncGenerator[None, Any]:
            nonlocal phase, received_exception
            phase = "started"
            exc = yield
            phase = "finished"
            received_exception = exc

        with ExitStack() as exit_stack:
            async with Context():
                await start()
                assert phase == "started"
                if expected_exc:
                    exit_stack.enter_context(pytest.raises(ExceptionGroup))
                    raise expected_exc

        assert phase == "finished"
        assert received_exception == expected_exc


async def test_current_context() -> None:
    pytest.raises(NoCurrentContext, current_context)

    async with Context() as parent_ctx:
        assert current_context() is parent_ctx
        async with Context() as child_ctx:
            assert current_context() is child_ctx

        assert current_context() is parent_ctx

    pytest.raises(NoCurrentContext, current_context)


async def test_get_resource() -> None:
    async with Context() as ctx:
        ctx.add_resource("foo")
        assert await get_resource(str) == "foo"
        assert await get_resource(int, optional=True) is None


async def test_get_resource_nowait() -> None:
    async with Context() as ctx:
        ctx.add_resource("foo")
        assert get_resource_nowait(str) == "foo"
        pytest.raises(ResourceNotFound, get_resource_nowait, int)


class TestDependencyInjection:
    async def test_static_resources(self) -> None:
        @inject
        async def injected(
            foo: int, bar: str = resource(), *, baz: str = resource("alt")
        ) -> Tuple[int, str, str]:  # noqa: UP006
            return foo, bar, baz

        async with Context() as ctx:
            ctx.add_resource("bar_test")
            ctx.add_resource("baz_test", "alt")
            foo, bar, baz = await injected(2)

        assert foo == 2
        assert bar == "bar_test"
        assert baz == "baz_test"

    async def test_sync_injection(self) -> None:
        @inject
        def injected(
            foo: int, bar: str = resource(), *, baz: str = resource("alt")
        ) -> Tuple[int, str, str]:  # noqa: UP006
            return foo, bar, baz

        async with Context() as ctx:
            ctx.add_resource("bar_test")
            ctx.add_resource("baz_test", "alt")
            foo, bar, baz = injected(2)

        assert foo == 2
        assert bar == "bar_test"
        assert baz == "baz_test"

    async def test_missing_annotation(self) -> None:
        async def injected(  # type: ignore[no-untyped-def]
            foo: int, bar: str = resource(), *, baz=resource("alt")
        ) -> None:
            pass

        pytest.raises(TypeError, inject, injected).match(
            f"Dependency for parameter 'baz' of function "
            f"'{__name__}.{self.__class__.__name__}.test_missing_annotation.<locals>"
            f".injected' is missing the type annotation"
        )

    async def test_missing_resource(self) -> None:
        @inject
        async def injected(foo: int, bar: str = resource()) -> None:
            pass

        with raises_in_exception_group(ResourceNotFound) as exc:
            async with Context():
                await injected(2)

        exc.match("no matching resource was found for type=str name='default'")

    @pytest.mark.parametrize(
        "annotation",
        [
            pytest.param(Optional[str], id="optional"),
            # pytest.param(Union[str, int, None], id="union"),
            pytest.param(
                "str | None",
                id="uniontype.10",
                marks=[
                    pytest.mark.skipif(
                        sys.version_info < (3, 10), reason="Requires Python 3.10+"
                    )
                ],
            ),
        ],
    )
    @pytest.mark.parametrize(
        "sync",
        [
            pytest.param(True, id="sync"),
            pytest.param(False, id="async"),
        ],
    )
    async def test_inject_optional_resource_async(
        self, annotation: type, sync: bool
    ) -> None:
        if sync:

            @inject
            def injected(
                res: annotation = resource(),  # type: ignore[valid-type]
            ) -> annotation:  # type: ignore[valid-type]
                return res

        else:

            @inject
            async def injected(
                res: annotation = resource(),  # type: ignore[valid-type]
            ) -> annotation:  # type: ignore[valid-type]
                return res

        async with Context() as ctx:
            retval: Any = injected() if sync else (await injected())
            assert retval is None
            ctx.add_resource("hello")
            retval = injected() if sync else (await injected())
            assert retval == "hello"

    def test_resource_function_not_called(self) -> None:
        async def injected(
            foo: int,
            bar: str = resource,  # type: ignore[assignment]
        ) -> None:
            pass

        with pytest.raises(TypeError) as exc:
            inject(injected)

        exc.match(
            f"Default value for parameter 'bar' of function "
            f"{__name__}.{self.__class__.__name__}.test_resource_function_not_called"
            f".<locals>.injected was the 'resource' function – did you forget to add "
            f"the parentheses at the end\\?"
        )

    def test_missing_inject(self) -> None:
        def injected(foo: int, bar: str = resource()) -> None:
            bar.lower()

        with pytest.raises(AttributeError) as exc:
            injected(1)

        exc.match(
            r"Attempted to access an attribute in a resource\(\) marker – did you "
            r"forget to add the @inject decorator\?"
        )

    def test_posonly_argument(self) -> None:
        def injected(foo: int, bar: str = resource(), /) -> None:
            pass

        pytest.raises(TypeError, inject, injected).match(
            "Cannot inject dependency to positional-only parameter 'bar'"
        )

    def test_no_resources_declared(self) -> None:
        def injected(foo: int) -> None:
            pass

        match = (
            f"{__name__}.{self.__class__.__name__}.test_no_resources_declared.<locals>"
            f".injected does not have any injectable resources declared"
        )
        with pytest.warns(UserWarning, match=match):
            func = inject(injected)

        assert func is injected
