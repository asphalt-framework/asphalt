from asyncio import coroutine
from itertools import count
from functools import partial
import asyncio

import pytest
import sys

from asphalt.core.context import ResourceConflict, ResourceNotFound, Resource, Context


class TestResource:
    @pytest.fixture
    def resource(self):
        return Resource(6, ('int', 'object'), 'foo', 'bar.foo')

    @pytest.fixture
    def lazy_resource(self):
        return Resource(None, ('int',), 'foo', 'bar.foo', lambda ctx: (ctx, 6))

    def test_get(self, resource):
        ctx = Context()
        assert resource.get_value(ctx) == 6

    def test_get_lazy(self):
        resource = Resource(None, ('int',), 'foo', None, lambda ctx: (ctx, 6))
        ctx = Context()
        assert resource.get_value(ctx) == (ctx, 6)

    def test_repr(self, resource: Resource):
        assert repr(resource) == ("Resource(types=('int', 'object'), alias='foo', "
                                  "value=6, context_var='bar.foo', lazy=False)")

    def test_str(self, resource: Resource):
        assert str(resource) == ("types=('int', 'object'), alias='foo', "
                                 "value=6, context_var='bar.foo', lazy=False")


use_contextmanager = None
if sys.version_info >= (3, 5):
    exec("""
async def use_contextmanager(context, exception):
    async with context:
        if exception:
            raise exception
    """)


class TestContext:
    @pytest.fixture
    def context(self):
        return Context()

    @pytest.mark.parametrize('raise_exception', [True, False], ids=['exception', 'no_exception'])
    def test_contextmanager(self, context, raise_exception):
        """
        Tests that "with context:" dispatches both started and finished events and sets the
        exception variable when an exception is raised during the context lifetime.
        """

        def finished_listener(event):
            nonlocal finished_called
            finished_called = True

        exception = RuntimeError('test') if raise_exception else None
        finished_called = False
        context.add_listener('finished', finished_listener)
        try:
            with context:
                if exception:
                    raise exception
        except RuntimeError:
            pass

        assert finished_called
        assert context.exception == exception

    @pytest.mark.asyncio
    @pytest.mark.skipif('sys.version_info < (3, 5)')
    @pytest.mark.parametrize('raise_exception', [True, False], ids=['exception', 'no_exception'])
    @coroutine
    def test_async_contextmanager(self, context, raise_exception):
        """
        Tests that "async with context:" dispatches both started and finished events and sets the
        exception variable when an exception is raised during the context lifetime.
        """

        def finished_listener(event):
            nonlocal finished_called
            finished_called = True

        exception = RuntimeError('test') if raise_exception else None
        finished_called = False
        context.add_listener('finished', finished_listener)
        try:
            yield from use_contextmanager(context, exception)
        except RuntimeError:
            pass

        assert finished_called
        assert context.exception == exception

    @pytest.mark.asyncio
    @pytest.mark.parametrize('delay', [False, True], ids=['immediate', 'delayed'])
    def test_add_resource(self, context, event_loop, delay):
        """Tests that a resource is properly added to the collection and listeners are notified."""

        events = []
        trigger = asyncio.Event()
        context.add_listener('resource_added', events.append)
        context.add_listener('resource_added', lambda evt: trigger.set())
        if delay:
            call = partial(context.add_resource, 6, 'foo', 'foo.bar', types=(int, float))
            event_loop.call_soon(call)
        else:
            context.add_resource(6, 'foo', 'foo.bar', types=(int, float))

        value = yield from context.request_resource(int, 'foo', timeout=2)
        assert value == 6

        yield from trigger.wait()
        assert len(events) == 1
        event = events[0]
        assert event.types == ('int', 'float')
        assert event.alias == 'foo'

    def test_add_name_conflict(self, context):
        """Tests that add() won't let replace existing resources."""

        context.add_resource(5, 'foo')
        exc = pytest.raises(ResourceConflict, context.add_resource, 4, 'foo')
        assert str(exc.value) == ('"foo" conflicts with Resource(types=(\'int\',), alias=\'foo\', '
                                  'value=5, context_var=None, lazy=False)')

    @pytest.mark.asyncio
    def test_remove_resource(self, context):
        """Tests that resources can be removed and that the listeners are notified."""

        resource = context.add_resource(4)

        events = []
        trigger = asyncio.Event()
        context.add_listener('resource_removed', events.append)
        context.add_listener('resource_removed', lambda evt: trigger.set())
        context.remove_resource(resource)

        yield from trigger.wait()
        assert len(events) == 1
        assert events[0].types == ('int',)

        with pytest.raises(ResourceNotFound):
            yield from context.request_resource(int, timeout=0)

    def test_remove_nonexistent(self, context):
        resource = Resource(5, ('int',), 'default', None)
        exc = pytest.raises(LookupError, context.remove_resource, resource)
        assert str(exc.value) == ("Resource(types=('int',), alias='default', value=5, "
                                  "context_var=None, lazy=False) not found in this context")

    @pytest.mark.asyncio
    def test_request_timeout(self, context):
        with pytest.raises(ResourceNotFound) as exc:
            yield from context.request_resource(int, timeout=0.2)

        assert str(exc.value) == "no matching resource was found for type='int' alias='default'"

    @pytest.mark.asyncio
    @pytest.mark.parametrize('bad_arg, errormsg', [
        ('type', 'type must be a type or a nonempty string'),
        ('alias', 'alias must be a nonempty string')
    ], ids=['bad_type', 'bad_alias'])
    def test_bad_request(self, context, bad_arg, errormsg):
        type_ = None if bad_arg == 'type' else 'foo'
        alias = None if bad_arg == 'alias' else 'foo'
        with pytest.raises(ValueError) as exc:
            yield from context.request_resource(type_, alias)
        assert str(exc.value) == errormsg

    def test_add_lazy_resource(self, context):
        """Tests that lazy resources are only created once per context instance."""

        def creator(ctx):
            assert ctx is context
            return next(counter)

        counter = count(1)
        context.add_lazy_resource(creator, int, context_var='foo')
        assert context.foo == 1
        assert context.foo == 1
        assert context.__dict__['foo'] == 1

    def test_resource_added_removed(self, context):
        """
        Tests that when resources are added, they are also set as properties of the context.
        Likewise, when they are removed, they are deleted from the context.
        """

        resource = context.add_resource(1, context_var='foo')
        assert context.foo == 1
        context.remove_resource(resource)
        assert 'foo' not in context.__dict__

    def test_add_lazy_resource_coroutine(self, context):
        """Tests that coroutine functions are not accepted as lazy resource creators."""

        exc = pytest.raises(AssertionError, context.add_lazy_resource,
                            coroutine(lambda ctx: None), 'foo')
        assert str(exc.value) == 'creator cannot be a coroutine function'

    @pytest.mark.asyncio
    def test_add_resource_conflicting_attribute(self, context):
        context.a = 2
        exc = pytest.raises(ResourceConflict, context.add_resource, 2, context_var='a')
        assert str(exc.value) == (
            "Resource(types=('int',), alias='default', value=2, context_var='a', lazy=False) "
            "conflicts with an existing context attribute")

        with pytest.raises(ResourceNotFound):
            yield from context.request_resource(int, timeout=0)

    def test_add_lazy_resource_conflicting_resource(self, context):
        context.add_lazy_resource(lambda ctx: 2, int, context_var='a')
        exc = pytest.raises(ResourceConflict, context.add_resource, 2, 'foo', context_var='a')
        assert str(exc.value) == (
            "Resource(types=('int',), alias='foo', value=2, context_var='a', lazy=False) "
            "conflicts with an existing lazy resource")

    def test_add_lazy_resource_duplicate(self, context):
        context.add_lazy_resource(lambda ctx: None, str, context_var='foo')
        exc = pytest.raises(ResourceConflict, context.add_lazy_resource, lambda ctx: None,
                            str, context_var='foo')
        assert (str(exc.value) ==
                "\"default\" conflicts with Resource(types=('str',), alias='default', value=None, "
                "context_var='foo', lazy=True)")

    def test_attribute_error(self, context):
        exc = pytest.raises(AttributeError, getattr, context, 'foo')
        assert str(exc.value) == 'no such context variable: foo'

    def test_get_parent_attribute(self, context):
        """
        Tests that accessing a nonexistent attribute on a context retrieves the value from parent.
        """

        child_context = Context(context)
        context.a = 2
        assert child_context.a == 2

    @pytest.mark.asyncio
    def test_request_optional(self, context):
        """
        Tests that requesting a nonexistent resource with optional=True returns None instead of
        raising an exception.
        """

        resource = yield from context.request_resource(str, timeout=0, optional=True)
        assert resource is None

    @pytest.mark.asyncio
    def test_request_resource_parent_add(self, context):
        """
        Tests that adding a resource to the parent context will satisfy a resource request in a
        child context.
        """

        child_context = Context(context)
        request = asyncio.async(child_context.request_resource(int, timeout=1))
        context.add_resource(6)
        resource = yield from request
        assert resource == 6

    @pytest.mark.asyncio
    def test_request_lazy_resource_context_var(self, context):
        """Tests that requesting a lazy resource also sets the context variable."""

        context.add_lazy_resource(lambda ctx: 6, int, context_var='foo')
        yield from context.request_resource(int)
        assert context.__dict__['foo'] == 6

    def test_remove_lazy_resource(self, context):
        """
        Tests that the lazy resource is no longer created when it has been removed and its
        context variable is accessed.
        """

        resource = context.add_lazy_resource(lambda ctx: 6, int, context_var='foo')
        context.remove_resource(resource)
        exc = pytest.raises(AttributeError, getattr, context, 'foo')
        assert str(exc.value) == 'no such context variable: foo'
