from asyncio import coroutine, async
from itertools import count
import asyncio
import sys

import pytest

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
                                  "value=6, context_attr='bar.foo', creator=None)")

    def test_str(self, resource: Resource):
        assert str(resource) == ("types=('int', 'object'), alias='foo', "
                                 "value=6, context_attr='bar.foo', creator=None")


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
        return Context(default_timeout=2)

    @pytest.mark.parametrize('async_contextmanager', [
        pytest.mark.skipif('sys.version_info < (3, 5)')(True),
        False
    ], ids=['async', 'normal'])
    @pytest.mark.parametrize('raise_exception', [True, False], ids=['exception', 'no_exception'])
    def test_contextmanager(self, event_loop, context, async_contextmanager, raise_exception):
        """
        Tests that "with context:" dispatches both started and finished events and sets the
        exception variable when an exception is raised during the context lifetime.
        """

        exception = RuntimeError('test') if raise_exception else None
        events = []
        context.add_listener('finished', events.append)
        try:
            if async_contextmanager:
                event_loop.run_until_complete(use_contextmanager(context, exception))
            else:
                with context:
                    if exception:
                        raise exception
        except RuntimeError:
            pass

        assert len(events) == 1
        assert events[0].exception is exception

    @pytest.mark.asyncio
    @pytest.mark.parametrize('delay', [False, True], ids=['immediate', 'delayed'])
    def test_publish_resource(self, context, event_loop, delay):
        """
        Tests that a resource is properly published in the context and listeners are
        notified.
        """

        events = []
        context.add_listener('resource_published', events.append)
        if delay:
            async(context.publish_resource(6, 'foo', 'foo.bar', types=(int, float)))
        else:
            yield from context.publish_resource(6, 'foo', 'foo.bar', types=(int, float))

        value = yield from context.request_resource(int, 'foo')
        assert value == 6

        assert len(events) == 1
        resource = events[0].resource
        assert resource.types == ('int', 'float')
        assert resource.alias == 'foo'

    @pytest.mark.asyncio
    def test_add_name_conflict(self, context):
        """Tests that publishing a resource won't replace any existing resources."""

        yield from context.publish_resource(5, 'foo')
        with pytest.raises(ResourceConflict) as exc:
            yield from context.publish_resource(4, 'foo')

        assert str(exc.value) == (
            'this context has an existing resource of type int using the alias "foo"')

    @pytest.mark.asyncio
    def test_remove_resource(self, context):
        """Tests that resources can be removed and that the listeners are notified."""

        events = []
        context.add_listener('resource_removed', events.append)
        resource = yield from context.publish_resource(4)
        yield from context.remove_resource(resource)

        assert len(events) == 1
        assert events[0].resource.types == ('int',)

        with pytest.raises(ResourceNotFound):
            yield from context.request_resource(int, timeout=0)

    @pytest.mark.asyncio
    def test_remove_nonexistent(self, context):
        resource = Resource(5, ('int',), 'default', None)
        with pytest.raises(LookupError) as exc:
            yield from context.remove_resource(resource)

        assert str(exc.value) == ("Resource(types=('int',), alias='default', value=5, "
                                  "context_attr=None, creator=None) not found in this context")

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

    @pytest.mark.asyncio
    def test_publish_lazy_resource(self, context):
        """Tests that lazy resources are only created once per context instance."""

        def creator(ctx):
            assert ctx is context
            return next(counter)

        counter = count(1)
        yield from context.publish_lazy_resource(creator, int, context_attr='foo')
        assert context.foo == 1
        assert context.foo == 1
        assert context.__dict__['foo'] == 1

    @pytest.mark.asyncio
    def test_resource_added_removed(self, context):
        """
        Tests that when resources are published, they are also set as properties of the context.
        Likewise, when they are removed, they are deleted from the context.
        """

        resource = yield from context.publish_resource(1, context_attr='foo')
        assert context.foo == 1
        yield from context.remove_resource(resource)
        assert 'foo' not in context.__dict__

    def test_publish_lazy_resource_coroutine(self, context):
        """Tests that coroutine functions are not accepted as lazy resource creators."""

        exc = pytest.raises(AssertionError, context.publish_lazy_resource,
                            coroutine(lambda ctx: None), 'foo')
        assert str(exc.value) == 'creator cannot be a coroutine function'

    @pytest.mark.asyncio
    def test_publish_resource_conflicting_attribute(self, context):
        context.a = 2
        with pytest.raises(ResourceConflict) as exc:
            yield from context.publish_resource(2, context_attr='a')

        assert str(exc.value) == 'this context already has an attribute "a"'

        with pytest.raises(ResourceNotFound):
            yield from context.request_resource(int, timeout=0)

    @pytest.mark.asyncio
    def test_publish_lazy_resource_conflicting_resource(self, context):
        yield from context.publish_lazy_resource(lambda ctx: 2, int, context_attr='a')
        with pytest.raises(ResourceConflict) as exc:
            yield from context.publish_resource(2, 'foo', context_attr='a')

        assert str(exc.value) == (
            'this context has an existing lazy resource using the attribute "a"')

    @pytest.mark.asyncio
    def test_publish_lazy_resource_duplicate(self, context):
        yield from context.publish_lazy_resource(lambda ctx: None, str, context_attr='foo')
        with pytest.raises(ResourceConflict) as exc:
            yield from context.publish_lazy_resource(lambda ctx: None, str, context_attr='foo')

        assert (str(exc.value) ==
                'this context has an existing resource of type str using the alias "default"')

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
        Tests that publishing a resource to the parent context will satisfy a resource request in a
        child context.
        """

        child_context = Context(context)
        request = asyncio.async(child_context.request_resource(int))
        yield from context.publish_resource(6)
        resource = yield from request
        assert resource == 6

    @pytest.mark.asyncio
    def test_request_lazy_resource_context_attr(self, context):
        """Tests that requesting a lazy resource also sets the context variable."""

        yield from context.publish_lazy_resource(lambda ctx: 6, int, context_attr='foo')
        yield from context.request_resource(int)
        assert context.__dict__['foo'] == 6

    @pytest.mark.asyncio
    def test_remove_lazy_resource(self, context):
        """
        Tests that the lazy resource is no longer created when it has been removed and its
        context variable is accessed.
        """

        resource = yield from context.publish_lazy_resource(lambda ctx: 6, int, context_attr='foo')
        yield from context.remove_resource(resource)
        exc = pytest.raises(AttributeError, getattr, context, 'foo')
        assert str(exc.value) == 'no such context variable: foo'
