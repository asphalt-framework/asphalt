import asyncio
from asyncio.futures import Future
from itertools import count

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


class TestContext:
    @pytest.fixture
    def context(self):
        return Context(default_timeout=2)

    def test_parent(self):
        """Test that the parent property points to the parent context instance, if any."""
        parent = Context()
        child = Context(parent)
        assert parent.parent is None
        assert child.parent is parent

    @pytest.mark.asyncio
    async def test_contextmanager(self, context):
        """
        Test that "async with context:" dispatches both started and finished events and sets the
        exception variable when an exception is raised during the context lifetime.

        """
        events = []
        context.finished.connect(events.append)

        async with context:
            pass

        assert len(events) == 1

    @pytest.mark.asyncio
    async def test_contextmanager_exception(self, context):
        """
        Test that "async with context:" dispatches both started and finished events and sets the
        exception variable when an exception is raised during the context lifetime.

        """
        events = []
        context.finished.connect(events.append)

        with pytest.raises(RuntimeError) as exc:
            async with context:
                raise RuntimeError('test')

        assert len(events) == 1
        assert exc.value is events[0].exception

    @pytest.mark.asyncio
    async def test_publish_resource(self, context):
        """Test that a resource is properly published in the context and listeners are notified."""
        future = Future()
        context.resource_published.connect(future.set_result)
        context.publish_resource(6, 'foo', 'foo.bar', types=(int, float))

        value = await context.request_resource(int, 'foo')
        assert value == 6

        event = await future
        assert event.resource.types == ('int', 'float')
        assert event.resource.alias == 'foo'

    @pytest.mark.asyncio
    async def test_publish_resource_name_conflict(self, context):
        """Test that publishing a resource won't replace any existing resources."""
        context.publish_resource(5, 'foo')
        with pytest.raises(ResourceConflict) as exc:
            context.publish_resource(4, 'foo')

        assert str(exc.value) == (
            'this context has an existing resource of type int using the alias "foo"')

    @pytest.mark.asyncio
    async def test_publish_resource_context_attr(self, context):
        """
        Test that when resources are published, they are also set as properties of the context.

        Likewise, when they are removed, they are deleted from the context.
        """
        resource = context.publish_resource(1, context_attr='foo')
        assert context.foo == 1
        context.remove_resource(resource)
        assert 'foo' not in context.__dict__

    @pytest.mark.asyncio
    async def test_publish_resource_conflicting_attribute(self, context):
        """
        Test that the context won't allow publishing a resource with an attribute name that
        conflicts with an existing attribute.

        """
        context.a = 2
        with pytest.raises(ResourceConflict) as exc:
            context.publish_resource(2, context_attr='a')

        assert str(exc.value) == 'this context already has an attribute "a"'

        with pytest.raises(ResourceNotFound):
            await context.request_resource(int, timeout=0)

    @pytest.mark.parametrize('alias', ['a.b', 'a:b', 'a b'], ids=['dot', 'colon', 'space'])
    @pytest.mark.asyncio
    async def test_publish_resource_bad_alias(self, context, alias):
        with pytest.raises(AssertionError) as exc:
            context.publish_resource(1, alias)

        assert str(exc.value) == 'alias can only contain alphanumeric characters and underscores'

    @pytest.mark.asyncio
    async def test_publish_lazy_resource(self, context):
        """Test that lazy resources are only created once per context instance."""
        def creator(ctx):
            assert ctx is context
            return next(counter)

        counter = count(1)
        context.publish_lazy_resource(creator, int, context_attr='foo')
        assert context.foo == 1
        assert context.foo == 1
        assert context.__dict__['foo'] == 1

    @pytest.mark.asyncio
    async def test_publish_lazy_resource_coroutine(self, context):
        """
        Test that a coroutine function can be a resource creator and that it returns a Future.

        """
        async def async_creator(context):
            return 'hello'

        context.publish_lazy_resource(async_creator, str, 'foo', 'foo')
        hello = context.foo
        assert isinstance(hello, Future)
        assert await context.request_resource(str, 'foo') == 'hello'

    @pytest.mark.asyncio
    async def test_publish_lazy_resource_coroutine_delayed(self, event_loop, context):
        """
        Test that a coroutine resource creator works even when the resource isn't immediately
        available.

        """
        async def async_creator(context):
            return 'hello'

        future = event_loop.create_task(context.request_resource(str, 'foo'))
        await asyncio.sleep(0.2)
        context.publish_lazy_resource(async_creator, str, alias='foo')
        assert await future == 'hello'

    @pytest.mark.asyncio
    async def test_publish_lazy_resource_conflicting_resource(self, context):
        context.publish_lazy_resource(lambda ctx: 2, int, context_attr='a')
        with pytest.raises(ResourceConflict) as exc:
            context.publish_resource(2, 'foo', context_attr='a')

        assert str(exc.value) == (
            'this context has an existing lazy resource using the attribute "a"')

    @pytest.mark.asyncio
    async def test_publish_lazy_resource_duplicate(self, context):
        context.publish_lazy_resource(lambda ctx: None, str, context_attr='foo')
        with pytest.raises(ResourceConflict) as exc:
            await context.publish_lazy_resource(lambda ctx: None, str, context_attr='foo')

        assert (str(exc.value) ==
                'this context has an existing resource of type str using the alias "default"')

    @pytest.mark.asyncio
    async def test_remove_resource(self, context):
        """Test that resources can be removed and that the listeners are notified."""
        future = Future()
        context.resource_removed.connect(future.set_result)
        resource = context.publish_resource(4)
        context.remove_resource(resource)

        event = await future
        assert event.resource.types == ('int',)

        with pytest.raises(ResourceNotFound):
            await context.request_resource(int, timeout=0)

    @pytest.mark.asyncio
    async def test_remove_nonexistent(self, context):
        resource = Resource(5, ('int',), 'default', None)
        with pytest.raises(LookupError) as exc:
            context.remove_resource(resource)

        assert str(exc.value) == ("Resource(types=('int',), alias='default', value=5, "
                                  "context_attr=None, creator=None) not found in this context")

    @pytest.mark.asyncio
    async def test_request_timeout(self, context):
        with pytest.raises(ResourceNotFound) as exc:
            await context.request_resource(int, timeout=0.2)

        assert str(exc.value) == "no matching resource was found for type='int' alias='default'"

    @pytest.mark.asyncio
    @pytest.mark.parametrize('bad_arg, errormsg', [
        ('type', 'type must be a type or a nonempty string'),
        ('alias', 'alias must be a nonempty string')
    ], ids=['bad_type', 'bad_alias'])
    async def test_bad_request(self, context, bad_arg, errormsg):
        type_ = '' if bad_arg == 'type' else 'foo'
        alias = '' if bad_arg == 'alias' else 'foo'
        with pytest.raises(ValueError) as exc:
            await context.request_resource(type_, alias)
        assert str(exc.value) == errormsg

    def test_attribute_error(self, context):
        exc = pytest.raises(AttributeError, getattr, context, 'foo')
        assert str(exc.value) == 'no such context variable: foo'

    def test_get_parent_attribute(self, context):
        """
        Test that accessing a nonexistent attribute on a context retrieves the value from parent.

        """
        child_context = Context(context)
        context.a = 2
        assert child_context.a == 2

    @pytest.mark.asyncio
    async def test_request_resource_parent_add(self, context, event_loop):
        """
        Test that publishing a resource to the parent context will satisfy a resource request in a
        child context.

        """
        child_context = Context(context)
        task = event_loop.create_task(child_context.request_resource(int))
        context.publish_resource(6)
        resource = await task
        assert resource == 6

    @pytest.mark.asyncio
    async def test_request_lazy_resource_context_attr(self, context):
        """Test that requesting a lazy resource also sets the context variable."""
        context.publish_lazy_resource(lambda ctx: 6, int, context_attr='foo')
        await context.request_resource(int)
        assert context.__dict__['foo'] == 6

    @pytest.mark.asyncio
    async def test_remove_lazy_resource(self, context):
        """
        Test that the lazy resource is no longer created when it has been removed and its
        context variable is accessed.

        """
        resource = context.publish_lazy_resource(lambda ctx: 6, int, context_attr='foo')
        context.remove_resource(resource)
        exc = pytest.raises(AttributeError, getattr, context, 'foo')
        assert str(exc.value) == 'no such context variable: foo'

    @pytest.mark.asyncio
    async def test_get_resources(self, context):
        resource1 = context.publish_resource(6, 'int1')
        resource2 = context.publish_resource(8, 'int2')
        resource3 = context.publish_resource('foo', types=(str, 'collections.abc.Iterable'))
        resource4 = context.publish_resource(
            (5, 4), 'sometuple', types=(tuple, 'collections.abc.Iterable'))

        assert context.get_resources() == [resource1, resource2, resource3, resource4]
        assert context.get_resources(int) == [resource1, resource2]
        assert context.get_resources(str) == [resource3]
        assert context.get_resources('collections.abc.Iterable') == [resource3, resource4]

    @pytest.mark.asyncio
    async def test_get_resources_include_parents(self, context):
        subcontext = Context(context)
        resource1 = context.publish_resource(6, 'int1')
        resource2 = subcontext.publish_resource(8, 'int2')
        resource3 = context.publish_resource('foo', 'str')

        assert subcontext.get_resources() == [resource1, resource2, resource3]
        assert subcontext.get_resources(include_parents=False) == [resource2]
