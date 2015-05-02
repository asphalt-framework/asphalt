from asyncio import coroutine

import pytest

from asphalt.core.router import RoutingError, Endpoint, BaseRouter


def test_routing_error():
    exc = RoutingError('errormsg', '/')
    assert str(exc) == 'errormsg'
    assert exc.path == '/'


class TestEndpoint:
    @pytest.mark.parametrize('func, blocking, expected_blocking', [
        (lambda ctx: None, None, True),
        (coroutine(lambda ctx: None), None, False),
        (lambda ctx: None, False, False)
    ], ids=['auto', 'explicit-async', 'explicit-blocking'])
    def test_constructor(self, func, blocking, expected_blocking):
        endpoint = Endpoint(func, blocking)
        assert endpoint.blocking == expected_blocking

    def test_not_enough_args(self):
        def func():
            pass

        exc = pytest.raises(TypeError, Endpoint, func)
        assert str(exc.value) == (
            'test_router.TestEndpoint.test_not_enough_args.<locals>.func cannot accept the '
            'context argument. Please add one positional parameter in its definition.')

    def test_generator_endpoint(self):
        def func(ctx):
            yield 1

        exc = pytest.raises(TypeError, Endpoint, func)
        assert str(exc.value) == (
            'test_router.TestEndpoint.test_generator_endpoint.<locals>.func is a generator but '
            'not a coroutine. Either mark it as a coroutine or remove the yield statements.')


class TestRouter:
    @pytest.fixture
    def router(self):
        return BaseRouter()

    @pytest.fixture
    def root_router(self):
        root = BaseRouter()
        foo = BaseRouter()
        baz = BaseRouter()
        bar = BaseRouter()
        foo.add_router(baz, 'baz')
        root.add_router(foo, 'foo')
        root.add_router(bar, 'bar')

        root.endpoints = [
            Endpoint(lambda ctx: None),
            Endpoint(lambda ctx: 1)
        ]
        baz.endpoints = [
            Endpoint(lambda ctx: None)
        ]
        bar.endpoints = [
            Endpoint(lambda ctx: 2)
        ]

        return root

    @pytest.mark.parametrize('subrouter_path, join_path', [
        (None, 'foo'),
        ('foo', None)
    ], ids=['subrouterpath', 'joinpath'])
    def test_add_router(self, router, subrouter_path, join_path):
        subrouter = BaseRouter(subrouter_path)
        router.add_router(subrouter, join_path)
        assert subrouter.parent is router
        assert subrouter.path is (subrouter_path or join_path)
        assert router.routers[0] is subrouter

    def test_add_router_no_path(self, router):
        subrouter = BaseRouter()
        exc = pytest.raises(ValueError, router.add_router, subrouter)
        assert str(exc.value) == \
            'path not specified and the given router does not have a path defined'

    def test_full_path(self, root_router):
        assert root_router.routers[0].full_path == 'foo'
        assert root_router.routers[0].routers[0].full_path == 'foo.baz'
        assert root_router.routers[1].full_path == 'bar'

    def test_full_path_none(self, root_router):
        exc = pytest.raises(ValueError, getattr, root_router, 'full_path')
        assert str(exc.value) == 'router has no path defined'

    def test_all_routers(self, root_router):
        foo = root_router.routers[0]
        baz = foo.routers[0]
        bar = root_router.routers[1]
        assert root_router.all_routers == (root_router, foo, baz, bar)

    def test_all_endpoints(self, root_router):
        foo = root_router.routers[0]
        baz = foo.routers[0]
        bar = root_router.routers[1]
        endpoints = tuple(root_router.endpoints + baz.endpoints + bar.endpoints)
        assert root_router.all_endpoints == endpoints

    def test_repr(self, router):
        assert repr(router) == 'BaseRouter(path=None)'
