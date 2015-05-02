from asyncio import coroutine

import pytest

from asphalt.core.context import (ApplicationContext, TransportContext, HandlerContext,
                                  ContextScope, ContextEventType, CallbackPriority, EventCallback)
from asphalt.core.resource import ResourceConflict, ResourceNotFoundError
from asphalt.core.router import Endpoint


@pytest.fixture
def app_context():
    return ApplicationContext({'setting': 'blah'})


@pytest.fixture
def transport_context(app_context):
    return TransportContext(app_context)


@pytest.fixture
def handler_context(transport_context):
    endpoint = Endpoint(lambda ctx: None)
    return HandlerContext(transport_context, endpoint)


class TestEventCallback:
    def test_repr_(self):
        def func(ctx):
            pass

        callback = EventCallback(ContextEventType.started, func, (1,), {'a': 4},
                                 CallbackPriority.last)
        assert (repr(callback) ==
                "EventCallback(event=started, "
                "func=test_context.TestEventCallback.test_repr_.<locals>.func, args=(1,), "
                "kwargs={'a': 4}, position=last)")


class TestApplicationContext:
    @pytest.mark.asyncio
    @pytest.mark.parametrize('event, results1, results2', [
        (ContextEventType.started, ((7, 9), {'b': 4}), ((1, 2), {'a': 3})),
        (ContextEventType.finished, ((6, 1), {'a': 5}), ((4, 5), {'a': 2}))
    ], ids=['started', 'finished'])
    def test_callbacks(self, app_context: ApplicationContext, event, results1, results2):
        """Tests that both default and local callbacks are run and they're sorted by priority."""

        @coroutine
        def callback(ctx, *args, **kwargs):
            nonlocal events
            assert ctx is context
            events.append((args, kwargs))

        events = []
        context = TransportContext(app_context)
        app_context.add_default_callback(ContextScope.transport, ContextEventType.started,
                                         callback, [1, 2], {'a': 3}, CallbackPriority.last)
        context.add_callback(ContextEventType.finished, callback, [4, 5], {'a': 2})
        context.add_callback(ContextEventType.started, callback, [7, 9], {'b': 4})
        app_context.add_default_callback(ContextScope.transport, ContextEventType.finished,
                                         callback, [6, 1], {'a': 5}, CallbackPriority.first)
        yield from context.run_callbacks(event)

        assert len(events) == 2
        assert events[0] == results1
        assert events[1] == results2

    def test_no_default_callbacks(self, app_context: ApplicationContext):
        exc = pytest.raises(AssertionError, app_context.add_default_callback,
                            ContextScope.application, ContextEventType.started, lambda ctx: None)
        assert str(exc.value) == ('cannot add default callbacks on the application scope -- '
                                  'use add_callback() instead')

    @pytest.mark.asyncio
    def test_add_already_handled_event(self, app_context: ApplicationContext):
        """Tests that you can't add a callback to an event that's already been handled."""

        yield from app_context.run_callbacks(ContextEventType.started)
        exc = pytest.raises(ValueError, app_context.add_callback, ContextEventType.started,
                            lambda ctx: None)
        assert str(exc.value) == 'cannot add started callbacks to this context any more'

    @pytest.mark.asyncio
    def test_run_already_handled_event(self, app_context: ApplicationContext):
        """Tests that you can't run the same types of callbacks twice."""

        yield from app_context.run_callbacks(ContextEventType.started)
        with pytest.raises(ValueError) as exc:
            yield from app_context.run_callbacks(ContextEventType.started)
        assert str(exc.value) == 'the started callbacks for this context have already been run'

    def test_add_lazy_resource(self, app_context):
        """Tests that lazy resources are only created once per context instance."""

        def creator(ctx):
            nonlocal value
            assert ctx is app_context
            value += 1
            return value

        value = 0
        app_context.add_lazy_property(app_context.scope, 'foo', creator)
        assert app_context.foo == 1
        assert app_context.foo == 1
        assert 'foo' in app_context.__dict__

    def test_resource_added_removed(self, app_context):
        """
        Tests that when resources are added, they are also set as properties of the application
        context. Likewise, when they are removed, they are deleted from the application context.
        """

        resource = app_context.resources.add(1, context_var='foo')
        assert app_context.foo == 1
        app_context.resources.remove(resource)
        assert 'foo' not in app_context.__dict__

    @pytest.mark.asyncio
    def test_add_member_conflicting_resource(self, app_context):
        app_context.a = 2
        exc = pytest.raises(ResourceConflict, app_context.resources.add, 2, context_var='a')
        assert str(exc.value) == (
            "Resource(types=('int',), alias='default', value=2, context_var='a') "
            "conflicts with an application context property")

        with pytest.raises(ResourceNotFoundError):
            yield from app_context.resources.request(int, timeout=0)

    @pytest.mark.asyncio
    def test_add_lazy_property_conflicting_resource(self, app_context: ApplicationContext):
        app_context.add_lazy_property(ContextScope.application, 'a', lambda ctx: 2)
        exc = pytest.raises(ResourceConflict, app_context.resources.add, 2, context_var='a')
        assert str(exc.value) == (
            "Resource(types=('int',), alias='default', value=2, context_var='a') "
            "conflicts with an application scoped lazy property")

        with pytest.raises(ResourceNotFoundError):
            yield from app_context.resources.request(int, timeout=0)

    def test_lazy_property_coroutine(self, app_context: ApplicationContext):
        exc = pytest.raises(AssertionError, app_context.add_lazy_property,
                            ContextScope.application, 'foo', coroutine(lambda ctx: None))
        assert str(exc.value) == 'creator cannot be a coroutine function'

    def test_lazy_property_duplicate(self, app_context: ApplicationContext):
        app_context.add_lazy_property(ContextScope.application, 'foo', lambda ctx: None)
        exc = pytest.raises(ValueError, app_context.add_lazy_property, ContextScope.application,
                            'foo', lambda ctx: None)
        assert (str(exc.value) ==
                'there is already a lazy property for "foo" on the application scope')


class TestTransportContext:
    def test_getattr_parent_attr(self, app_context, transport_context):
        app_context.a = 6
        assert transport_context.a == 6

    def test_lazy_property(self, transport_context):
        transport_context.add_lazy_property(ContextScope.transport, 'a', lambda ctx: ctx)
        assert transport_context.a is transport_context

    def test_getattr_error(self, transport_context):
        exc = pytest.raises(AttributeError, getattr, transport_context, 'nonexistent')
        assert str(exc.value) == 'no such context property: nonexistent'


class TestHandlerContext:
    def test_getattr_parent_attr(self, transport_context, handler_context):
        transport_context.a = 6
        assert handler_context.a == 6

    def test_lazy_property(self, handler_context):
        handler_context.add_lazy_property(ContextScope.handler, 'a', lambda ctx: ctx)
        assert handler_context.a is handler_context

    def test_getattr_error(self, handler_context):
        exc = pytest.raises(AttributeError, getattr, handler_context, 'nonexistent')
        assert str(exc.value) == 'no such context property: nonexistent'
