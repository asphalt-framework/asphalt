from concurrent.futures import ThreadPoolExecutor, Executor
from threading import current_thread

import pytest

from asphalt.core import Context
from asphalt.core.concurrent import executor


@pytest.fixture
def context(event_loop):
    ctx = Context()
    yield ctx
    event_loop.run_until_complete(ctx.close())


@pytest.fixture
def special_executor(context):
    executor = ThreadPoolExecutor(1)
    context.add_resource(executor, 'special', types=[Executor])
    context.add_teardown_callback(executor.shutdown)
    return executor


@pytest.mark.parametrize('use_resource_name', [False, True], ids=['instance', 'resource_name'])
@pytest.mark.asyncio
async def test_executor_special(context, use_resource_name, special_executor):
    @executor('special' if use_resource_name else special_executor)
    def check_thread(ctx):
        assert current_thread() is executor_thread

    executor_thread = special_executor.submit(current_thread).result()
    await check_thread(context)


@pytest.mark.asyncio
async def test_executor_default(event_loop, context):
    @executor
    def check_thread(ctx):
        assert current_thread() is not event_loop_thread

    event_loop_thread = current_thread()
    await check_thread(context)


@pytest.mark.asyncio
async def test_executor_worker_thread(event_loop, context, special_executor):
    @executor('special')
    def runs_in_special_worker(ctx, worker_thread):
        assert current_thread() is worker_thread
        return 'foo'

    @executor
    def runs_in_default_worker(ctx):
        assert current_thread() is not event_loop_thread
        assert current_thread() is not special_executor_thread
        return runs_in_special_worker(ctx, current_thread())

    event_loop_thread = current_thread()
    special_executor_thread = special_executor.submit(current_thread).result()
    retval = await runs_in_default_worker(context)
    assert retval == 'foo'


@pytest.mark.asyncio
async def test_executor_missing_context(event_loop, context):
    @executor('special')
    def runs_in_default_worker():
        pass

    with pytest.raises(RuntimeError) as exc:
        await runs_in_default_worker()

    exc.match('the callable needs to be called with a Context as the first or second positional '
              'argument')
