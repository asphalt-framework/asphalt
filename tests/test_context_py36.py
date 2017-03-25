import pytest

from asphalt.core.context import Context, context_teardown


class TestContextFinisher:
    @pytest.mark.parametrize('expected_exc', [
        None, Exception('foo')
    ], ids=['no_exception', 'exception'])
    @pytest.mark.asyncio
    async def test_context_teardown(self, expected_exc):
        @context_teardown
        async def start(ctx: Context):
            nonlocal phase, received_exception
            phase = 'started'
            exc = yield
            phase = 'finished'
            received_exception = exc

        phase = received_exception = None
        context = Context()
        await start(context)
        assert phase == 'started'

        await context.close(expected_exc)
        assert phase == 'finished'
        assert received_exception == expected_exc
