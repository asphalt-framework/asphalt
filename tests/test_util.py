from asyncio import coroutine
import asyncio
import threading

import pytest

from asphalt.core.util import (
    resolve_reference, qualified_name, synchronous, wrap_blocking_api,
    wrap_async_api, asynchronous)


@pytest.mark.parametrize('inputval', [
    'asphalt.core.util:resolve_reference',
    resolve_reference
], ids=['reference', 'object'])
def test_resolve_reference(inputval):
    assert resolve_reference(inputval) is resolve_reference


@pytest.mark.parametrize('inputval, error_type, error_text', [
    ('x.y', ValueError, 'invalid reference (no ":" contained in the string)'),
    ('x.y:foo', LookupError, 'error resolving reference x.y:foo: could not import module'),
    ('asphalt.core:foo', LookupError,
     'error resolving reference asphalt.core:foo: error looking up object')
], ids=['invalid_ref', 'module_not_found', 'object_not_found'])
def test_resolve_reference_error(inputval, error_type, error_text):
    exc = pytest.raises(error_type, resolve_reference, inputval)
    assert str(exc.value) == error_text


@pytest.mark.parametrize('inputval, expected', [
    (qualified_name, 'asphalt.core.util.qualified_name'),
    (asyncio.Event(), 'asyncio.locks.Event'),
    (int, 'int')
], ids=['func', 'instance', 'builtintype'])
def test_qualified_name(inputval, expected):
    assert qualified_name(inputval) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize('run_in_threadpool', [False, True], ids=['eventloop', 'threadpool'])
def test_wrap_blocking_callable(event_loop, run_in_threadpool):
    @synchronous
    def func(x, y):
        assert threading.current_thread() is not threading.main_thread()
        return x + y

    if run_in_threadpool:
        retval = yield from event_loop.run_in_executor(None, func, 1, 2)
    else:
        retval = yield from func(1, 2)

    assert retval == 3


@pytest.mark.asyncio
@pytest.mark.parametrize('run_in_threadpool', [False, True], ids=['eventloop', 'threadpool'])
def test_wrap_async_callable(event_loop, run_in_threadpool):
    @asynchronous
    @coroutine
    def func(x, y):
        assert threading.current_thread() is threading.main_thread()
        yield from asyncio.sleep(0.2)
        return x + y

    if run_in_threadpool:
        retval = yield from event_loop.run_in_executor(None, func, 1, 2)
    else:
        retval = yield from func(1, 2)

    assert retval == 3


@pytest.mark.asyncio
@pytest.mark.parametrize('run_in_threadpool', [False, True], ids=['eventloop', 'threadpool'])
def test_wrap_blocking_api(event_loop, run_in_threadpool):
    class BlockingAPI:
        def method(self, x, y):
            assert threading.current_thread() is not threading.main_thread()
            return x + y

    wrapped_class = wrap_blocking_api(BlockingAPI, ['method'])
    if run_in_threadpool:
        retval = yield from event_loop.run_in_executor(None, wrapped_class().method, 1, 2)
    else:
        retval = yield from wrapped_class().method(1, 2)

    assert retval == 3


@pytest.mark.asyncio
@pytest.mark.parametrize('run_in_threadpool', [False, True], ids=['eventloop', 'threadpool'])
def test_wrap_async_api(event_loop, run_in_threadpool):
    class AsyncAPI:
        @coroutine
        def method(self, x, y):
            assert threading.current_thread() is threading.main_thread()
            yield from asyncio.sleep(0.2)
            return x + y

    wrapped_class = wrap_async_api(AsyncAPI, ['method'])
    if run_in_threadpool:
        retval = yield from event_loop.run_in_executor(None, wrapped_class().method, 1, 2)
    else:
        retval = yield from wrapped_class().method(1, 2)

    assert retval == 3


@pytest.mark.asyncio
def test_wrap_async_callable_exception(event_loop):
    @coroutine
    def async_func():
        yield from asyncio.sleep(0.2)
        raise ValueError('test')

    wrapped_async_func = asynchronous(async_func)
    with pytest.raises(ValueError) as exc:
        yield from event_loop.run_in_executor(None, wrapped_async_func)
    assert str(exc.value) == 'test'
