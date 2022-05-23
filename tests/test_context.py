from __future__ import annotations

import asyncio
import sys
from collections.abc import Callable
from concurrent.futures import Executor, ThreadPoolExecutor
from inspect import isawaitable
from itertools import count
from threading import Thread, current_thread
from typing import AsyncGenerator, AsyncIterator, Dict, NoReturn, Optional, Tuple, Union
from unittest.mock import patch

import pytest
import pytest_asyncio
from async_generator import yield_

from asphalt.core import (
    Context,
    Dependency,
    NoCurrentContext,
    ResourceConflict,
    ResourceNotFound,
    TeardownError,
    callable_name,
    context_teardown,
    current_context,
    executor,
    get_resource,
    inject,
    resource,
)
from asphalt.core.context import ResourceContainer, require_resource


@pytest.fixture
def context() -> Context:
    return Context()


@pytest_asyncio.fixture
async def special_executor(context: Context) -> AsyncIterator[ThreadPoolExecutor]:
    executor = ThreadPoolExecutor(1)
    context.add_resource(executor, "special", types=[Executor])
    yield executor
    executor.shutdown()


class TestResourceContainer:
    @pytest.mark.parametrize("thread", [False, True], ids=["eventloop", "worker"])
    @pytest.mark.parametrize(
        "context_attr", [None, "attrname"], ids=["no_attr", "has_attr"]
    )
    @pytest.mark.asyncio
    async def test_generate_value(self, thread: bool, context_attr: str | None) -> None:
        container = ResourceContainer(
            lambda ctx: "foo", (str,), "default", context_attr, True
        )
        context = Context()
        if thread:
            value = await context.call_in_executor(container.generate_value, context)
        else:
            value = container.generate_value(context)

        assert value == "foo"
        assert context.get_resource(str) == "foo"
        if context_attr:
            assert getattr(context, context_attr) == "foo"

    def test_repr(self) -> None:
        container = ResourceContainer("foo", (str,), "default", "attrname", False)
        assert repr(container) == (
            "ResourceContainer(value='foo', types=[str], name='default', "
            "context_attr='attrname')"
        )

    def test_repr_factory(self) -> None:
        container = ResourceContainer(
            lambda ctx: "foo", (str,), "default", "attrname", True
        )
        assert repr(container) == (
            "ResourceContainer(factory=test_context.TestResourceContainer"
            ".test_repr_factory.<locals>.<lambda>, types=[str], name='default', "
            "context_attr='attrname')"
        )


class TestContext:
    @pytest.mark.asyncio
    async def test_parent(self) -> None:
        """Test that the parent property points to the parent context instance, if any."""
        async with Context() as parent:
            async with Context() as child:
                assert parent.parent is None
                assert child.parent is parent

    @pytest.mark.parametrize(
        "exception", [None, Exception("foo")], ids=["noexception", "exception"]
    )
    @pytest.mark.asyncio
    async def test_close(self, context: Context, exception: Exception | None) -> None:
        """
        Test that teardown callbacks are called in reverse order when a context is closed.

        """

        def callback(exception=None):
            called_functions.append((callback, exception))

        async def async_callback(exception=None):
            called_functions.append((async_callback, exception))

        called_functions: list[tuple[Callable, BaseException | None]] = []
        context.add_teardown_callback(callback, pass_exception=True)
        context.add_teardown_callback(async_callback, pass_exception=True)
        await context.close(exception)

        assert called_functions == [(async_callback, exception), (callback, exception)]

    @pytest.mark.asyncio
    async def test_close_while_running_teardown(self, context: Context) -> None:
        """
        Test that trying to close the context from a teardown callback raises a
        RuntimeError.
        """

        async def try_close_context() -> None:
            with pytest.raises(RuntimeError, match="this context is already closing"):
                await context.close()

        context.add_teardown_callback(try_close_context)
        await context.close()

    @pytest.mark.asyncio
    async def test_teardown_callback_exception(self, context: Context) -> None:
        """
        Test that all callbacks are called even when some teardown callbacks raise
        exceptions, and that a TeardownError is raised in such a case, containing the
        exception objects.

        """

        def callback1() -> None:
            items.append(1)

        def callback2() -> NoReturn:
            raise Exception("foo")

        context.add_teardown_callback(callback1)
        context.add_teardown_callback(callback2)
        context.add_teardown_callback(callback1)
        context.add_teardown_callback(callback2)
        items: list[int] = []
        with pytest.raises(TeardownError) as exc:
            await context.close()

        assert "foo" in str(exc.value)
        assert items == [1, 1]
        assert len(exc.value.exceptions) == 2

    @pytest.mark.asyncio
    async def test_close_closed(self, context: Context) -> None:
        """Test that closing an already closed context raises a RuntimeError."""
        assert not context.closed
        await context.close()
        assert context.closed

        with pytest.raises(RuntimeError) as exc:
            await context.close()

        exc.match("this context has already been closed")

    def test_contextmanager_exception(self, context, event_loop):
        close_future = event_loop.create_future()
        close_future.set_result(None)
        exception = Exception("foo")
        with patch.object(context, "close", return_value=close_future):
            with pytest.raises(Exception) as exc, pytest.deprecated_call():
                with context:
                    raise exception

        # close.assert_called_once_with(exception)
        assert exc.value is exception

    @pytest.mark.asyncio
    async def test_async_contextmanager_exception(self, event_loop, context):
        """Test that "async with context:" calls close() with the exception raised in the block."""
        close_future = event_loop.create_future()
        close_future.set_result(None)
        exception = Exception("foo")
        with patch.object(context, "close", return_value=close_future) as close:
            with pytest.raises(Exception) as exc:
                async with context:
                    raise exception

        close.assert_called_once_with(exception)
        assert exc.value is exception

    @pytest.mark.parametrize("types", [int, (int,), ()], ids=["type", "tuple", "empty"])
    @pytest.mark.asyncio
    async def test_add_resource(self, context, event_loop, types):
        """Test that a resource is properly added in the context and listeners are notified."""
        event_loop.call_soon(context.add_resource, 6, "foo", None, types)
        event = await context.resource_added.wait_event()

        assert event.resource_types == (int,)
        assert event.resource_name == "foo"
        assert not event.is_factory
        assert context.get_resource(int, "foo") == 6

    @pytest.mark.asyncio
    async def test_add_resource_name_conflict(self, context: Context) -> None:
        """Test that adding a resource won't replace any existing resources."""
        context.add_resource(5, "foo")
        with pytest.raises(ResourceConflict) as exc:
            context.add_resource(4, "foo")

        exc.match(
            "this context already contains a resource of type int using the name 'foo'"
        )

    @pytest.mark.asyncio
    async def test_add_resource_none_value(self, context: Context) -> None:
        """Test that None is not accepted as a resource value."""
        exc = pytest.raises(ValueError, context.add_resource, None)
        exc.match('"value" must not be None')

    @pytest.mark.asyncio
    async def test_add_resource_context_attr(self, context: Context) -> None:
        """Test that when resources are added, they are also set as properties of the context."""
        with pytest.deprecated_call():
            context.add_resource(1, context_attr="foo")

        assert context.foo == 1

    def test_add_resource_context_attr_conflict(self, context: Context) -> None:
        """
        Test that the context won't allow adding a resource with an attribute name that conflicts
        with an existing attribute.

        """
        context.a = 2
        with pytest.raises(ResourceConflict) as exc, pytest.deprecated_call():
            context.add_resource(2, context_attr="a")

        exc.match("this context already has an attribute 'a'")
        assert context.get_resource(int) is None

    @pytest.mark.asyncio
    async def test_add_resource_type_conflict(self, context: Context) -> None:
        context.add_resource(5)
        with pytest.raises(ResourceConflict) as exc:
            context.add_resource(6)

        exc.match(
            "this context already contains a resource of type int using the name 'default'"
        )

    @pytest.mark.parametrize(
        "name", ["a.b", "a:b", "a b"], ids=["dot", "colon", "space"]
    )
    @pytest.mark.asyncio
    async def test_add_resource_bad_name(self, context, name):
        with pytest.raises(ValueError) as exc:
            context.add_resource(1, name)

        exc.match(
            '"name" must be a nonempty string consisting only of alphanumeric characters '
            "and underscores"
        )

    @pytest.mark.asyncio
    async def test_add_resource_parametrized_generic_type(
        self, context: Context
    ) -> None:
        resource = {"a": 1}
        resource_type = Dict[str, int]
        context.add_resource(resource, types=[resource_type])
        assert context.require_resource(resource_type) is resource
        assert context.get_resource(resource_type) is resource
        assert await context.request_resource(resource_type) is resource
        assert context.get_resource(Dict) is None
        assert context.get_resource(dict) is None

    @pytest.mark.asyncio
    async def test_add_resource_factory(self, context: Context) -> None:
        """Test that resources factory callbacks are only called once for each context."""

        def factory(ctx):
            assert ctx is context
            return next(counter)

        counter = count(1)
        with pytest.deprecated_call():
            context.add_resource_factory(factory, int, context_attr="foo")

        assert context.foo == 1
        assert context.foo == 1
        assert context.__dict__["foo"] == 1

    @pytest.mark.asyncio
    async def test_add_resource_factory_parametrized_generic_type(
        self, context: Context
    ) -> None:
        resource = {"a": 1}
        resource_type = Dict[str, int]
        context.add_resource_factory(lambda ctx: resource, types=[resource_type])
        assert context.require_resource(resource_type) is resource
        assert context.get_resource(resource_type) is resource
        assert await context.request_resource(resource_type) is resource
        assert context.get_resource(Dict) is None
        assert context.get_resource(dict) is None

    @pytest.mark.parametrize(
        "name", ["a.b", "a:b", "a b"], ids=["dot", "colon", "space"]
    )
    @pytest.mark.asyncio
    async def test_add_resource_factory_bad_name(self, context, name):
        with pytest.raises(ValueError) as exc:
            context.add_resource_factory(lambda ctx: 1, int, name)

        exc.match(
            '"name" must be a nonempty string consisting only of alphanumeric characters '
            "and underscores"
        )

    @pytest.mark.asyncio
    async def test_add_resource_factory_coroutine_callback(
        self, context: Context
    ) -> None:
        async def factory(ctx):
            return 1

        with pytest.raises(TypeError) as exc:
            context.add_resource_factory(factory, int)

        exc.match('"factory_callback" must not be a coroutine function')

    @pytest.mark.asyncio
    async def test_add_resource_factory_empty_types(self, context: Context) -> None:
        with pytest.raises(ValueError) as exc:
            context.add_resource_factory(lambda ctx: 1, ())

        exc.match("no resource types were specified")

    @pytest.mark.asyncio
    async def test_add_resource_factory_context_attr_conflict(
        self, context: Context
    ) -> None:
        with pytest.deprecated_call():
            context.add_resource_factory(lambda ctx: None, str, context_attr="foo")

        with pytest.raises(ResourceConflict) as exc, pytest.deprecated_call():
            await context.add_resource_factory(
                lambda ctx: None, str, context_attr="foo"
            )

        exc.match(
            "this context already contains a resource factory for the context attribute 'foo'"
        )

    @pytest.mark.asyncio
    async def test_add_resource_factory_type_conflict(self, context: Context) -> None:
        context.add_resource_factory(lambda ctx: None, (str, int))
        with pytest.raises(ResourceConflict) as exc:
            await context.add_resource_factory(lambda ctx: None, int)

        exc.match("this context already contains a resource factory for the type int")

    @pytest.mark.asyncio
    async def test_add_resource_factory_no_inherit(self, context: Context) -> None:
        """
        Test that a subcontext gets its own version of a factory-generated resource even if a
        parent context has one already.

        """
        with pytest.deprecated_call():
            context.add_resource_factory(id, int, context_attr="foo")

        async with context, Context() as subcontext:
            assert context.foo == id(context)
            assert subcontext.foo == id(subcontext)

    @pytest.mark.asyncio
    async def test_add_resource_return_type_single(self, context: Context) -> None:
        def factory(ctx: Context) -> str:
            return "foo"

        async with context:
            context.add_resource_factory(factory)
            assert context.require_resource(str) == "foo"

    @pytest.mark.asyncio
    async def test_add_resource_return_type_union(self, context: Context) -> None:
        def factory(ctx: Context) -> Union[int, float]:
            return 5

        async with context:
            context.add_resource_factory(factory)
            assert context.require_resource(int) == 5
            assert context.require_resource(float) == 5

    @pytest.mark.skipif(sys.version_info < (3, 10), reason="Requires Python 3.10+")
    @pytest.mark.asyncio
    async def test_add_resource_return_type_uniontype(self, context: Context) -> None:
        def factory(ctx: Context) -> int | float:
            return 5

        async with context:
            context.add_resource_factory(factory)
            assert context.require_resource(int) == 5
            assert context.require_resource(float) == 5

    @pytest.mark.asyncio
    async def test_add_resource_return_type_optional(self, context: Context) -> None:
        def factory(ctx: Context) -> Optional[str]:
            return "foo"

        async with context:
            context.add_resource_factory(factory)
            assert context.require_resource(str) == "foo"

    @pytest.mark.asyncio
    async def test_getattr_attribute_error(self, context: Context) -> None:
        async with context, Context() as child_context:
            pytest.raises(AttributeError, getattr, child_context, "foo").match(
                "no such context variable: foo"
            )

    @pytest.mark.asyncio
    async def test_getattr_parent(self, context: Context) -> None:
        """
        Test that accessing a nonexistent attribute on a context retrieves the value from parent.

        """
        async with context, Context() as child_context:
            context.a = 2
            assert child_context.a == 2

    @pytest.mark.asyncio
    async def test_get_resources(self, context: Context) -> None:
        context.add_resource(9, "foo")
        context.add_resource_factory(lambda ctx: len(ctx.context_chain), int, "bar")
        context.require_resource(int, "bar")
        async with context, Context() as subctx:
            subctx.add_resource(4, "foo")
            assert subctx.get_resources(int) == {1, 4}

    @pytest.mark.asyncio
    async def test_require_resource(self, context: Context) -> None:
        context.add_resource(1)
        assert context.require_resource(int) == 1

    def test_require_resource_not_found(self, context: Context) -> None:
        """Test that ResourceNotFound is raised when a required resource is not found."""
        exc = pytest.raises(ResourceNotFound, context.require_resource, int, "foo")
        exc.match("no matching resource was found for type=int name='foo'")
        assert exc.value.type == int
        assert exc.value.name == "foo"

    @pytest.mark.asyncio
    async def test_request_resource_parent_add(self, context, event_loop):
        """
        Test that adding a resource to the parent context will satisfy a resource request in a
        child context.

        """
        async with context, Context() as child_context:
            task = event_loop.create_task(child_context.request_resource(int))
            event_loop.call_soon(context.add_resource, 6)
            resource = await task
            assert resource == 6

    @pytest.mark.asyncio
    async def test_request_resource_factory_context_attr(
        self, context: Context
    ) -> None:
        """Test that requesting a factory-generated resource also sets the context variable."""
        with pytest.deprecated_call():
            context.add_resource_factory(lambda ctx: 6, int, context_attr="foo")

        await context.request_resource(int)
        assert context.__dict__["foo"] == 6

    @pytest.mark.asyncio
    async def test_call_async_plain(self, context: Context) -> None:
        def runs_in_event_loop(worker_thread: Thread, x: int, y: int) -> int:
            assert current_thread() is not worker_thread
            return x + y

        def runs_in_worker_thread() -> int:
            worker_thread = current_thread()
            return context.call_async(runs_in_event_loop, worker_thread, 1, y=2)

        assert await context.call_in_executor(runs_in_worker_thread) == 3

    @pytest.mark.asyncio
    async def test_call_async_coroutine(self, context: Context) -> None:
        async def runs_in_event_loop(worker_thread, x, y):
            assert current_thread() is not worker_thread
            await asyncio.sleep(0.1)
            return x + y

        def runs_in_worker_thread() -> int:
            worker_thread = current_thread()
            return context.call_async(runs_in_event_loop, worker_thread, 1, y=2)

        assert await context.call_in_executor(runs_in_worker_thread) == 3

    @pytest.mark.asyncio
    async def test_call_async_exception(self, context: Context) -> None:
        def runs_in_event_loop() -> NoReturn:
            raise ValueError("foo")

        with pytest.raises(ValueError) as exc:
            await context.call_in_executor(context.call_async, runs_in_event_loop)

        assert exc.match("foo")

    @pytest.mark.asyncio
    async def test_call_in_executor(self, context: Context) -> None:
        """Test that call_in_executor actually runs the target in a worker thread."""
        worker_thread = await context.call_in_executor(current_thread)
        assert worker_thread is not current_thread()

    @pytest.mark.parametrize(
        "use_resource_name", [True, False], ids=["direct", "resource"]
    )
    @pytest.mark.asyncio
    async def test_call_in_executor_explicit(self, context, use_resource_name):
        executor = ThreadPoolExecutor(1)
        context.add_resource(executor, types=[Executor])
        context.add_teardown_callback(executor.shutdown)
        executor_arg = "default" if use_resource_name else executor
        worker_thread = await context.call_in_executor(
            current_thread, executor=executor_arg
        )
        assert worker_thread is not current_thread()

    @pytest.mark.asyncio
    async def test_call_in_executor_context_preserved(self, context: Context) -> None:
        """
        Test that call_in_executor runs the callable in a copy of the current (PEP 567)
        context.
        """

        async with Context() as ctx:
            assert await context.call_in_executor(current_context) is ctx

    @pytest.mark.asyncio
    async def test_threadpool(self, context: Context) -> None:
        event_loop_thread = current_thread()
        async with context.threadpool():
            assert current_thread() is not event_loop_thread

    @pytest.mark.asyncio
    async def test_threadpool_named_executor(
        self, context: Context, special_executor: Executor
    ) -> None:
        special_executor_thread = special_executor.submit(current_thread).result()
        async with context.threadpool("special"):
            assert current_thread() is special_executor_thread


class TestExecutor:
    @pytest.mark.asyncio
    async def test_no_arguments(self, context: Context) -> None:
        @executor
        def runs_in_default_worker() -> None:
            assert current_thread() is not event_loop_thread
            current_context()

        event_loop_thread = current_thread()
        async with context:
            await runs_in_default_worker()

    @pytest.mark.asyncio
    async def test_named_executor(
        self, context: Context, special_executor: Executor
    ) -> None:
        @executor("special")
        def runs_in_default_worker(ctx: Context) -> None:
            assert current_thread() is special_executor_thread
            assert current_context() is ctx

        special_executor_thread = special_executor.submit(current_thread).result()
        async with context:
            await runs_in_default_worker(context)

    @pytest.mark.asyncio
    async def test_executor_missing_context(self, context: Context):
        @executor("special")
        def runs_in_default_worker() -> None:
            current_context()

        with pytest.raises(RuntimeError) as exc:
            async with context:
                await runs_in_default_worker()

        exc.match(
            r"the first positional argument to %s\(\) has to be a Context instance"
            % callable_name(runs_in_default_worker)
        )


class TestContextTeardown:
    @pytest.mark.parametrize(
        "expected_exc", [None, Exception("foo")], ids=["no_exception", "exception"]
    )
    @pytest.mark.asyncio
    async def test_function(self, expected_exc: Exception | None) -> None:
        phase = received_exception = None

        @context_teardown
        async def start(ctx: Context) -> AsyncIterator[None]:
            nonlocal phase, received_exception
            phase = "started"
            exc = yield
            phase = "finished"
            received_exception = exc

        context = Context()
        await start(context)
        assert phase == "started"

        await context.close(expected_exc)
        assert phase == "finished"
        assert received_exception == expected_exc

    @pytest.mark.parametrize(
        "expected_exc", [None, Exception("foo")], ids=["no_exception", "exception"]
    )
    @pytest.mark.asyncio
    async def test_method(self, expected_exc: Exception | None) -> None:
        phase = received_exception = None

        class SomeComponent:
            @context_teardown
            async def start(self, ctx: Context) -> AsyncIterator[None]:
                nonlocal phase, received_exception
                phase = "started"
                exc = yield
                phase = "finished"
                received_exception = exc

        context = Context()
        await SomeComponent().start(context)
        assert phase == "started"

        await context.close(expected_exc)
        assert phase == "finished"
        assert received_exception == expected_exc

    def test_plain_function(self) -> None:
        def start(ctx) -> None:
            pass

        pytest.raises(TypeError, context_teardown, start).match(
            " must be an async generator function"
        )

    @pytest.mark.asyncio
    async def test_bad_args(self) -> None:
        with pytest.deprecated_call():

            @context_teardown
            async def start(ctx: Context) -> None:
                pass

        with pytest.raises(RuntimeError) as exc:
            await start(None)

        exc.match(
            r"the first positional argument to %s\(\) has to be a Context instance"
            % callable_name(start)
        )

    @pytest.mark.asyncio
    async def test_exception(self) -> None:
        @context_teardown
        async def start(ctx: Context) -> AsyncIterator[None]:
            raise Exception("dummy error")
            yield

        context = Context()
        with pytest.raises(Exception) as exc_info:
            await start(context)

        exc_info.match("dummy error")

    @pytest.mark.asyncio
    async def test_missing_yield(self) -> None:
        with pytest.deprecated_call():

            @context_teardown
            async def start(ctx: Context) -> None:
                pass

        await start(Context())

    @pytest.mark.asyncio
    async def test_py35_generator(self) -> None:
        with pytest.deprecated_call():

            @context_teardown
            async def start(ctx: Context) -> None:
                await yield_()

        await start(Context())

    @pytest.mark.parametrize(
        "resource_func",
        [
            pytest.param(Context.get_resource, id="get_resource"),
            pytest.param(Context.require_resource, id="require_resource"),
            pytest.param(Context.request_resource, id="request_resource"),
        ],
    )
    @pytest.mark.asyncio
    async def test_get_resource_at_teardown(self, resource_func) -> None:
        resource: str

        async def teardown_callback() -> None:
            nonlocal resource
            resource = resource_func(ctx, str)
            if isawaitable(resource):
                resource = await resource

        async with Context() as ctx:
            ctx.add_resource("blah")
            ctx.add_teardown_callback(teardown_callback)

        assert resource == "blah"

    @pytest.mark.parametrize(
        "resource_func",
        [
            pytest.param(Context.get_resource, id="get_resource"),
            pytest.param(Context.require_resource, id="require_resource"),
            pytest.param(Context.request_resource, id="request_resource"),
        ],
    )
    @pytest.mark.asyncio
    async def test_generate_resource_at_teardown(self, resource_func) -> None:
        resource: str

        async def teardown_callback() -> None:
            nonlocal resource
            resource = resource_func(ctx, str)
            if isawaitable(resource):
                resource = await resource

        async with Context() as ctx:
            ctx.add_resource_factory(lambda context: "blah", [str])
            ctx.add_teardown_callback(teardown_callback)

        assert resource == "blah"


class TestContextFinisher:
    @pytest.mark.parametrize(
        "expected_exc", [None, Exception("foo")], ids=["no_exception", "exception"]
    )
    @pytest.mark.asyncio
    async def test_context_teardown(self, expected_exc: Exception | None) -> None:
        phase = received_exception = None

        @context_teardown
        async def start(ctx: Context) -> AsyncIterator[None]:
            nonlocal phase, received_exception
            phase = "started"
            exc = yield
            phase = "finished"
            received_exception = exc

        context = Context()
        await start(context)
        assert phase == "started"

        await context.close(expected_exc)
        assert phase == "finished"
        assert received_exception == expected_exc


@pytest.mark.asyncio
async def test_current_context() -> None:
    pytest.raises(NoCurrentContext, current_context)

    async with Context() as parent_ctx:
        assert current_context() is parent_ctx
        async with Context() as child_ctx:
            assert current_context() is child_ctx

        assert current_context() is parent_ctx

    pytest.raises(NoCurrentContext, current_context)


@pytest.mark.asyncio
async def test_get_resource() -> None:
    async with Context() as ctx:
        ctx.add_resource("foo")
        assert get_resource(str) == "foo"
        assert get_resource(int) is None


@pytest.mark.asyncio
async def test_require_resource() -> None:
    async with Context() as ctx:
        ctx.add_resource("foo")
        assert require_resource(str) == "foo"
        pytest.raises(ResourceNotFound, require_resource, int)


def test_explicit_parent_deprecation() -> None:
    parent_ctx = Context()
    pytest.warns(DeprecationWarning, Context, parent_ctx)


@pytest.mark.asyncio
async def test_context_stack_corruption(event_loop):
    async def generator() -> AsyncGenerator:
        async with Context():
            yield

    gen = generator()
    await event_loop.create_task(gen.asend(None))
    async with Context() as ctx:
        with pytest.warns(
            UserWarning, match="Potential context stack corruption detected"
        ):
            try:
                await event_loop.create_task(gen.asend(None))
            except StopAsyncIteration:
                pass

        assert current_context() is ctx

    pytest.raises(NoCurrentContext, current_context)


class TestDependencyInjection:
    @pytest.mark.asyncio
    async def test_static_resources(self) -> None:
        @inject
        async def injected(
            foo: int, bar: str = resource(), *, baz: str = resource("alt")
        ) -> Tuple[int, str, str]:
            return foo, bar, baz

        async with Context() as ctx:
            ctx.add_resource("bar_test")
            ctx.add_resource("baz_test", "alt")
            foo, bar, baz = await injected(2)

        assert foo == 2
        assert bar == "bar_test"
        assert baz == "baz_test"

    @pytest.mark.asyncio
    async def test_sync_injection(self) -> None:
        @inject
        def injected(
            foo: int, bar: str = resource(), *, baz: str = resource("alt")
        ) -> Tuple[int, str, str]:
            return foo, bar, baz

        async with Context() as ctx:
            ctx.add_resource("bar_test")
            ctx.add_resource("baz_test", "alt")
            foo, bar, baz = injected(2)

        assert foo == 2
        assert bar == "bar_test"
        assert baz == "baz_test"

    @pytest.mark.asyncio
    async def test_missing_annotation(self) -> None:
        async def injected(
            foo: int, bar: str = resource(), *, baz=resource("alt")
        ) -> None:
            pass

        pytest.raises(TypeError, inject, injected).match(
            f"Dependency for parameter 'baz' of function "
            f"'{__name__}.{self.__class__.__name__}.test_missing_annotation.<locals>"
            f".injected' is missing the type annotation"
        )

    @pytest.mark.asyncio
    async def test_missing_resource(self) -> None:
        @inject
        async def injected(foo: int, bar: str = resource()) -> None:
            pass

        with pytest.raises(ResourceNotFound) as exc:
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
    @pytest.mark.asyncio
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
            retval = injected() if sync else (await injected())
            assert retval is None
            ctx.add_resource("hello")
            retval = injected() if sync else (await injected())
            assert retval == "hello"

    def test_resource_function_not_called(self) -> None:
        async def injected(foo: int, bar: str = resource) -> None:
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


def test_dependency_deprecated() -> None:
    with pytest.deprecated_call():

        async def foo(res: str = Dependency()) -> None:
            pass
