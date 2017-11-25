import asyncio
from concurrent.futures import ThreadPoolExecutor, Executor
from itertools import count
from threading import current_thread
from unittest.mock import patch

import pytest
from async_generator import yield_

from asphalt.core import (
    ResourceConflict, ResourceNotFound, Context, context_teardown, callable_name, executor)
from asphalt.core.context import ResourceContainer, TeardownError


@pytest.fixture
def context(event_loop):
    return Context()


@pytest.fixture
def special_executor(context):
    executor = ThreadPoolExecutor(1)
    context.add_resource(executor, 'special', types=[Executor])
    yield executor
    executor.shutdown()


class TestResourceContainer:
    @pytest.mark.parametrize('thread', [False, True], ids=['eventloop', 'worker'])
    @pytest.mark.parametrize('context_attr', [None, 'attrname'], ids=['no_attr', 'has_attr'])
    @pytest.mark.asyncio
    async def test_generate_value(self, thread, context_attr):
        container = ResourceContainer(lambda ctx: 'foo', (str,), 'default', context_attr, True)
        context = Context()
        if thread:
            value = await context.call_in_executor(container.generate_value, context)
        else:
            value = container.generate_value(context)

        assert value == 'foo'
        assert context.get_resource(str) == 'foo'
        if context_attr:
            assert getattr(context, context_attr) == 'foo'

    def test_repr(self):
        container = ResourceContainer('foo', (str,), 'default', 'attrname', False)
        assert repr(container) == ("ResourceContainer(value='foo', types=[str], name='default', "
                                   "context_attr='attrname')")

    def test_repr_factory(self):
        container = ResourceContainer(lambda ctx: 'foo', (str,), 'default', 'attrname', True)
        assert repr(container) == (
            "ResourceContainer(factory=test_context.TestResourceContainer.test_repr_factory."
            "<locals>.<lambda>, types=[str], name='default', context_attr='attrname')")


class TestContext:
    def test_parent(self):
        """Test that the parent property points to the parent context instance, if any."""
        parent = Context()
        child = Context(parent)
        assert parent.parent is None
        assert child.parent is parent

    @pytest.mark.parametrize('exception', [None, Exception('foo')],
                             ids=['noexception', 'exception'])
    @pytest.mark.asyncio
    async def test_close(self, context, exception):
        """
        Test that teardown callbacks are called in reverse order when a context is closed.

        """
        def callback(exception=None):
            called_functions.append((callback, exception))

        async def async_callback(exception=None):
            called_functions.append((async_callback, exception))

        called_functions = []
        context.add_teardown_callback(callback, pass_exception=True)
        context.add_teardown_callback(async_callback, pass_exception=True)
        await context.close(exception)

        assert called_functions == [(async_callback, exception), (callback, exception)]

    @pytest.mark.asyncio
    async def test_teardown_callback_exception(self, context):
        """
        Test that all callbacks are called even when some teardown callbacks raise exceptions,
        and that a TeardownError is raised in such a case, containing the exception objects.

        """
        def callback1():
            items.append(1)

        def callback2():
            raise Exception('foo')

        context.add_teardown_callback(callback1)
        context.add_teardown_callback(callback2)
        context.add_teardown_callback(callback1)
        context.add_teardown_callback(callback2)
        items = []
        with pytest.raises(TeardownError) as exc:
            await context.close()

        assert 'foo' in str(exc.value)
        assert items == [1, 1]
        assert len(exc.value.exceptions) == 2

    @pytest.mark.asyncio
    async def test_close_closed(self, context):
        """Test that closing an already closed context raises a RuntimeError."""
        assert not context.closed
        await context.close()
        assert context.closed

        with pytest.raises(RuntimeError) as exc:
            await context.close()

        exc.match('this context has already been closed')

    def test_contextmanager_exception(self, context, event_loop):
        close_future = event_loop.create_future()
        close_future.set_result(None)
        exception = Exception('foo')
        with patch.object(context, 'close', return_value=close_future) as close:
            with pytest.raises(Exception) as exc:
                with context:
                    raise exception

        close.assert_called_once_with(exception)
        assert exc.value is exception

    @pytest.mark.asyncio
    async def test_async_contextmanager_exception(self, event_loop, context):
        """Test that "async with context:" calls close() with the exception raised in the block."""
        close_future = event_loop.create_future()
        close_future.set_result(None)
        exception = Exception('foo')
        with patch.object(context, 'close', return_value=close_future) as close:
            with pytest.raises(Exception) as exc:
                async with context:
                    raise exception

        close.assert_called_once_with(exception)
        assert exc.value is exception

    @pytest.mark.parametrize('types', [int, (int,), ()], ids=['type', 'tuple', 'empty'])
    @pytest.mark.asyncio
    async def test_add_resource(self, context, event_loop, types):
        """Test that a resource is properly added in the context and listeners are notified."""
        event_loop.call_soon(context.add_resource, 6, 'foo', 'foo.bar', types)
        event = await context.resource_added.wait_event()

        assert event.resource_types == (int,)
        assert event.resource_name == 'foo'
        assert not event.is_factory
        assert context.get_resource(int, 'foo') == 6

    @pytest.mark.asyncio
    async def test_add_resource_name_conflict(self, context):
        """Test that adding a resource won't replace any existing resources."""
        context.add_resource(5, 'foo')
        with pytest.raises(ResourceConflict) as exc:
            context.add_resource(4, 'foo')

        exc.match("this context already contains a resource of type int using the name 'foo'")

    @pytest.mark.asyncio
    async def test_add_resource_none_value(self, context):
        """Test that None is not accepted as a resource value."""
        exc = pytest.raises(ValueError, context.add_resource, None)
        exc.match('"value" must not be None')

    @pytest.mark.asyncio
    async def test_add_resource_context_attr(self, context):
        """Test that when resources are added, they are also set as properties of the context."""
        context.add_resource(1, context_attr='foo')
        assert context.foo == 1

    def test_add_resource_context_attr_conflict(self, context):
        """
        Test that the context won't allow adding a resource with an attribute name that conflicts
        with an existing attribute.

        """
        context.a = 2
        with pytest.raises(ResourceConflict) as exc:
            context.add_resource(2, context_attr='a')

        exc.match("this context already has an attribute 'a'")
        assert context.get_resource(int) is None

    @pytest.mark.asyncio
    async def test_add_resource_type_conflict(self, context):
        context.add_resource(5)
        with pytest.raises(ResourceConflict) as exc:
            await context.add_resource(6)

        exc.match("this context already contains a resource of type int using the name 'default'")

    @pytest.mark.parametrize('name', ['a.b', 'a:b', 'a b'], ids=['dot', 'colon', 'space'])
    @pytest.mark.asyncio
    async def test_add_resource_bad_name(self, context, name):
        with pytest.raises(ValueError) as exc:
            context.add_resource(1, name)

        exc.match('"name" must be a nonempty string consisting only of alphanumeric characters '
                  'and underscores')

    @pytest.mark.asyncio
    async def test_add_resource_factory(self, context):
        """Test that resources factory callbacks are only called once for each context."""
        def factory(ctx):
            assert ctx is context
            return next(counter)

        counter = count(1)
        context.add_resource_factory(factory, int, context_attr='foo')
        assert context.foo == 1
        assert context.foo == 1
        assert context.__dict__['foo'] == 1

    @pytest.mark.parametrize('name', ['a.b', 'a:b', 'a b'], ids=['dot', 'colon', 'space'])
    @pytest.mark.asyncio
    async def test_add_resource_factory_bad_name(self, context, name):
        with pytest.raises(ValueError) as exc:
            context.add_resource_factory(lambda ctx: 1, int, name)

        exc.match('"name" must be a nonempty string consisting only of alphanumeric characters '
                  'and underscores')

    @pytest.mark.asyncio
    async def test_add_resource_factory_coroutine_callback(self, context):
        async def factory(ctx):
            return 1

        with pytest.raises(TypeError) as exc:
            context.add_resource_factory(factory, int)

        exc.match('"factory_callback" must not be a coroutine function')

    @pytest.mark.asyncio
    async def test_add_resource_factory_empty_types(self, context):
        with pytest.raises(ValueError) as exc:
            context.add_resource_factory(lambda ctx: 1, ())

        exc.match('"types" must not be empty')

    @pytest.mark.asyncio
    async def test_add_resource_factory_context_attr_conflict(self, context):
        context.add_resource_factory(lambda ctx: None, str, context_attr='foo')
        with pytest.raises(ResourceConflict) as exc:
            await context.add_resource_factory(lambda ctx: None, str, context_attr='foo')

        exc.match(
            "this context already contains a resource factory for the context attribute 'foo'")

    @pytest.mark.asyncio
    async def test_add_resource_factory_type_conflict(self, context):
        context.add_resource_factory(lambda ctx: None, (str, int))
        with pytest.raises(ResourceConflict) as exc:
            await context.add_resource_factory(lambda ctx: None, int)

        exc.match('this context already contains a resource factory for the type int')

    @pytest.mark.asyncio
    async def test_add_resource_factory_no_inherit(self, context):
        """
        Test that a subcontext gets its own version of a factory-generated resource even if a
        parent context has one already.

        """
        context.add_resource_factory(id, int, context_attr='foo')
        subcontext = Context(context)
        assert context.foo == id(context)
        assert subcontext.foo == id(subcontext)

    def test_getattr_attribute_error(self, context):
        child_context = Context(context)
        pytest.raises(AttributeError, getattr, child_context, 'foo').\
            match('no such context variable: foo')

    def test_getattr_parent(self, context):
        """
        Test that accessing a nonexistent attribute on a context retrieves the value from parent.

        """
        child_context = Context(context)
        context.a = 2
        assert child_context.a == 2

    def test_get_resources(self, context):
        context.add_resource(9, 'foo')
        context.add_resource_factory(lambda ctx: len(ctx.context_chain), int, 'bar')
        context.require_resource(int, 'bar')
        subctx = Context(context)
        subctx.add_resource(4, 'foo')
        assert subctx.get_resources(int) == {1, 4}

    def test_require_resource(self, context):
        context.add_resource(1)
        assert context.require_resource(int) == 1

    def test_require_resource_not_found(self, context):
        """Test that ResourceNotFound is raised when a required resource is not found."""
        exc = pytest.raises(ResourceNotFound, context.require_resource, int, 'foo')
        exc.match("no matching resource was found for type=int name='foo'")
        assert exc.value.type == int
        assert exc.value.name == 'foo'

    @pytest.mark.asyncio
    async def test_request_resource_parent_add(self, context, event_loop):
        """
        Test that adding a resource to the parent context will satisfy a resource request in a
        child context.

        """
        child_context = Context(context)
        task = event_loop.create_task(child_context.request_resource(int))
        event_loop.call_soon(context.add_resource, 6)
        resource = await task
        assert resource == 6

    @pytest.mark.asyncio
    async def test_request_resource_factory_context_attr(self, context):
        """Test that requesting a factory-generated resource also sets the context variable."""
        context.add_resource_factory(lambda ctx: 6, int, context_attr='foo')
        await context.request_resource(int)
        assert context.__dict__['foo'] == 6

    @pytest.mark.asyncio
    async def test_call_async_plain(self, context):
        def runs_in_event_loop(worker_thread, x, y):
            assert current_thread() is not worker_thread
            return x + y

        def runs_in_worker_thread():
            worker_thread = current_thread()
            return context.call_async(runs_in_event_loop, worker_thread, 1, y=2)

        assert await context.call_in_executor(runs_in_worker_thread) == 3

    @pytest.mark.asyncio
    async def test_call_async_coroutine(self, context):
        async def runs_in_event_loop(worker_thread, x, y):
            assert current_thread() is not worker_thread
            await asyncio.sleep(0.1)
            return x + y

        def runs_in_worker_thread():
            worker_thread = current_thread()
            return context.call_async(runs_in_event_loop, worker_thread, 1, y=2)

        assert await context.call_in_executor(runs_in_worker_thread) == 3

    @pytest.mark.asyncio
    async def test_call_async_exception(self, context):
        def runs_in_event_loop():
            raise ValueError('foo')

        with pytest.raises(ValueError) as exc:
            await context.call_in_executor(context.call_async, runs_in_event_loop)

        assert exc.match('foo')

    @pytest.mark.asyncio
    async def test_call_in_executor(self, context):
        """Test that call_in_executor actually runs the target in a worker thread."""
        worker_thread = await context.call_in_executor(current_thread)
        assert worker_thread is not current_thread()

    @pytest.mark.parametrize('use_resource_name', [True, False], ids=['direct', 'resource'])
    @pytest.mark.asyncio
    async def test_call_in_executor_explicit(self, context, use_resource_name):
        executor = ThreadPoolExecutor(1)
        context.add_resource(executor, types=[Executor])
        context.add_teardown_callback(executor.shutdown)
        executor_arg = 'default' if use_resource_name else executor
        worker_thread = await context.call_in_executor(current_thread, executor=executor_arg)
        assert worker_thread is not current_thread()

    @pytest.mark.asyncio
    async def test_threadpool(self, context):
        event_loop_thread = current_thread()
        async with context.threadpool():
            assert current_thread() is not event_loop_thread

    @pytest.mark.asyncio
    async def test_threadpool_named_executor(self, context, special_executor):
        special_executor_thread = special_executor.submit(current_thread).result()
        async with context.threadpool('special'):
            assert current_thread() is special_executor_thread


class TestExecutor:
    @pytest.mark.asyncio
    async def test_no_arguments(self, context):
        @executor
        def runs_in_default_worker():
            assert current_thread() is not event_loop_thread

        event_loop_thread = current_thread()
        await runs_in_default_worker()

    @pytest.mark.asyncio
    async def test_named_executor(self, context, special_executor):
        @executor('special')
        def runs_in_default_worker(ctx):
            assert current_thread() is special_executor_thread

        special_executor_thread = special_executor.submit(current_thread).result()
        await runs_in_default_worker(context)

    @pytest.mark.asyncio
    async def test_executor_missing_context(self, event_loop, context):
        @executor('special')
        def runs_in_default_worker():
            pass

        with pytest.raises(RuntimeError) as exc:
            await runs_in_default_worker()

        exc.match('the first positional argument to %s\(\) has to be a Context instance' %
                  callable_name(runs_in_default_worker))


class TestContextTeardown:
    @pytest.mark.parametrize('expected_exc', [
        None, Exception('foo')
    ], ids=['no_exception', 'exception'])
    @pytest.mark.asyncio
    async def test_function(self, expected_exc):
        @context_teardown
        async def start(ctx: Context):
            nonlocal phase, received_exception
            phase = 'started'
            exc = await yield_()
            phase = 'finished'
            received_exception = exc

        phase = received_exception = None
        context = Context()
        await start(context)
        assert phase == 'started'

        await context.close(expected_exc)
        assert phase == 'finished'
        assert received_exception == expected_exc

    @pytest.mark.parametrize('expected_exc', [
        None, Exception('foo')
    ], ids=['no_exception', 'exception'])
    @pytest.mark.asyncio
    async def test_method(self, expected_exc):
        class SomeComponent:
            @context_teardown
            async def start(self, ctx: Context):
                nonlocal phase, received_exception
                phase = 'started'
                exc = await yield_()
                phase = 'finished'
                received_exception = exc

        phase = received_exception = None
        context = Context()
        await SomeComponent().start(context)
        assert phase == 'started'

        await context.close(expected_exc)
        assert phase == 'finished'
        assert received_exception == expected_exc

    def test_plain_function(self):
        def start(ctx):
            pass

        pytest.raises(TypeError, context_teardown, start).\
            match(' must be an async generator function')

    @pytest.mark.asyncio
    async def test_bad_args(self):
        @context_teardown
        async def start(ctx):
            pass

        with pytest.raises(RuntimeError) as exc:
            await start(None)

        exc.match('the first positional argument to %s\(\) has to be a Context instance' %
                  callable_name(start))

    @pytest.mark.asyncio
    async def test_exception(self):
        @context_teardown
        async def start(ctx):
            raise Exception('dummy error')

        context = Context()
        with pytest.raises(Exception) as exc_info:
            await start(context)

        exc_info.match('dummy error')

    @pytest.mark.asyncio
    async def test_missing_yield(self):
        @context_teardown
        async def start(ctx: Context):
            pass

        await start(Context())
