from collections import Iterable
from itertools import count
from unittest.mock import patch

import pytest
from async_generator import yield_

from asphalt.core import (
    ResourceConflict, ResourceNotFound, Context, context_cleanup, ResourceContainer)
from asphalt.core.utils import callable_name


class TestResourceContainer:
    @pytest.mark.parametrize('context_attr', [None, 'attrname'], ids=['no_attr', 'has_attr'])
    def test_generate_value(self, context_attr):
        container = ResourceContainer(lambda ctx: 'foo', (str,), 'default', context_attr, True)
        context = Context()
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
    @pytest.fixture
    def context(self, event_loop):
        return Context(default_timeout=2)

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
        Test that resource cleanup callbacks are called in reverse order when a context is closed.

        """
        def callback(exception=None):
            called_functions.append((callback, exception))

        async def async_callback(exception=None):
            called_functions.append((async_callback, exception))

        called_functions = []
        context.add_cleanup_callback(callback, pass_exception=True)
        context.add_cleanup_callback(async_callback, pass_exception=True)
        await context.close(exception)

        assert called_functions == [(async_callback, exception), (callback, exception)]

    @pytest.mark.asyncio
    async def test_close_callback_exception(self, context, caplog):
        """Test that exceptions raised by cleanup callbacks are logged."""
        def callback():
            raise Exception('foo')

        context.add_cleanup_callback(callback)
        await context.close()

        callback_name = callable_name(callback)
        records = [record for record in caplog.records if record.name == 'asphalt.core.context']
        assert len(records) == 1
        assert records[0].message == 'Error calling cleanup callback ' + callback_name

    @pytest.mark.asyncio
    async def test_close_closed(self, context):
        """Test that closing an already closed context raises a RuntimeError."""
        await context.close()
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

        assert event.resource.value_or_factory == 6
        assert event.resource.types == (int,)
        assert event.resource.name == 'foo'
        assert event.resource.context_attr == 'foo.bar'
        assert not event.resource.is_factory

    @pytest.mark.asyncio
    async def test_add_resource_name_conflict(self, context):
        """Test that adding a resource won't replace any existing resources."""
        context.add_resource(5, 'foo')
        with pytest.raises(ResourceConflict) as exc:
            context.add_resource(4, 'foo')

        exc.match("this context has an existing resource of type int using the name 'foo'")

    @pytest.mark.asyncio
    async def test_add_resource_name_conflict(self, context):
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

    @pytest.mark.asyncio
    async def test_get_resources(self, context):
        resource1 = context.add_resource(6, 'int1')
        resource2 = context.add_resource(8, 'int2')
        resource3 = context.add_resource('foo', types=[str, Iterable])
        resource4 = context.add_resource((5, 4), 'sometuple', types=(tuple, Iterable))

        assert context.get_resources() == {resource1, resource2, resource3, resource4}
        assert context.get_resources(int) == {resource1, resource2}
        assert context.get_resources(str) == {resource3}
        assert context.get_resources(Iterable) == {resource3, resource4}

    @pytest.mark.asyncio
    async def test_get_resources_include_parents(self, context):
        subcontext = Context(context)
        resource1 = context.add_resource(6, 'int1')
        resource2 = subcontext.add_resource(8, 'int2')
        resource3 = context.add_resource('foo', 'str')

        assert subcontext.get_resources() == {resource1, resource2, resource3}
        assert subcontext.get_resources(include_parents=False) == {resource2}

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


class TestContextCleanup:
    @pytest.mark.parametrize('expected_exc', [
        None, Exception('foo')
    ], ids=['no_exception', 'exception'])
    @pytest.mark.asyncio
    async def test_function(self, expected_exc):
        @context_cleanup
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
            @context_cleanup
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

        pytest.raises(TypeError, context_cleanup, start).\
            match(' must be an async generator function')

    @pytest.mark.asyncio
    async def test_bad_args(self):
        @context_cleanup
        async def start(ctx):
            pass

        with pytest.raises(RuntimeError) as exc_info:
            await start(None)

        exc_info.match('either the first or second positional argument needs to be a Context '
                       'instance')

    @pytest.mark.asyncio
    async def test_exception(self):
        @context_cleanup
        async def start(ctx):
            raise Exception('dummy error')

        context = Context()
        with pytest.raises(Exception) as exc_info:
            await start(context)

        exc_info.match('dummy error')

    @pytest.mark.asyncio
    async def test_missing_yield(self):
        @context_cleanup
        async def start(ctx: Context):
            pass

        with pytest.raises(RuntimeError) as exc_info:
            await start(Context())

        exc_info.match(' did not do "await yield_\(\)"$')
