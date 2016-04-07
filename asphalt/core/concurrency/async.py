from collections import Coroutine, AsyncIterator
from functools import wraps
from inspect import iscoroutinefunction
from typing import Callable, Optional

__all__ = ('yield_async', 'async_generator', 'async_contextmanager')


async def _work_coroutine(
        coro: Coroutine, exception: BaseException = None) -> Optional['_AsyncYieldValue']:
    """
    Run the coroutine until it does ``await yield_async(...)``.

    :return: the value contained by :class:`_AsyncYieldValue`

    """
    value = None
    while True:
        try:
            if exception is not None:
                value = coro.throw(type(exception), exception, exception.__traceback__)
            else:
                value = coro.send(value)
        except StopIteration:
            return None

        if isinstance(value, _AsyncYieldValue):
            return value
        else:
            try:
                value = await value
            except Exception as e:
                exception = e
            else:
                exception = None


class _AsyncGeneratorWrapper:
    __slots__ = 'coroutine'

    def __init__(self, coroutine: Coroutine):
        self.coroutine = coroutine

    async def __aiter__(self):
        return self

    async def __anext__(self):
        value = await _work_coroutine(self.coroutine)
        if value is not None:
            return value.value
        else:
            raise StopAsyncIteration


class _AsyncContextManager:
    __slots__ = 'coroutine'

    def __init__(self, coroutine: Coroutine):
        self.coroutine = coroutine

    async def __aenter__(self):
        retval = await _work_coroutine(self.coroutine)
        if retval is not None:
            return retval.value
        else:
            raise RuntimeError('coroutine finished without yielding a value')

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        retval = await _work_coroutine(self.coroutine, exc_val)
        if retval is not None:
            raise RuntimeError('coroutine yielded a value in the exit phase: {!r}'.
                               format(retval.value))


class _AsyncYieldValue:
    __slots__ = 'value'

    def __init__(self, value):
        self.value = value

    def __await__(self):
        yield self


def yield_async(value):
    """The equivalent of ``yield`` in an asynchronous context manager or asynchronous generator."""
    return _AsyncYieldValue(value)


def async_generator(func: Callable[..., Coroutine]) -> Callable[..., AsyncIterator]:
    """
    Transform a generator function into something that works with the ``async for``.

    Any awaitable yielded by the given generator function will be awaited on and the result passed
    back to the generator. Any other yielded values will be yielded to the actual consumer of the
    asynchronous iterator.

    For example:
    >>> @async_generator
    >>> async def mygenerator(websites):
    >>>     for website in websites:
    >>>         page = await http_fetch(website)
    >>>         await yield_async(page)
    >>>
    >>> async def fetch_pages():
    >>>     websites = ('http://foo.bar', 'http://example.org')
    >>>     async for sanitized_page in mygenerator(websites):
    >>>         print(sanitized_page)

    :param func: a generator function
    :return: a callable that can be used with ``async for``

    """
    @wraps(func)
    def wrapper(*args, **kwargs):
        return _AsyncGeneratorWrapper(func(*args, **kwargs))

    assert iscoroutinefunction(func), '"func" must be a coroutine function'
    return wrapper


def async_contextmanager(func: Callable[..., Coroutine]) -> Callable:
    """
    Transform a generator function into something that works with ``async with``.

    The generator may yield any number of awaitables which are resolved and sent back to the
    generator. To indicate that the setup phase is complete, the generator must yield one
    non-awaitable value. The rest of the generator will then be processed after the context block
    has been executed. If the context was exited with an exception, this exception will be raised
    in the generator.

    For example:
    >>> @async_contextmanager
    >>> async def mycontextmanager(arg):
    >>>     context = await setup_remote_context(arg)
    >>>     await yield_async(context)
    >>>     await context.teardown()
    >>>
    >>> async def frobnicate(arg):
    >>>     async with mycontextmanager(arg) as context:
    >>>         do_something_with(context)

    :param func: a generator function
    :return: a callable that can be used with ``async with``

    """
    @wraps(func)
    def wrapper(*args, **kwargs):
        return _AsyncContextManager(func(*args, **kwargs))

    assert iscoroutinefunction(func), '"func" must be a coroutine function'
    return wrapper
