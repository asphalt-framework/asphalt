from __future__ import annotations

import sys
from collections.abc import Generator
from contextlib import contextmanager
from types import TracebackType
from typing import Any, cast

import pytest

if sys.version_info < (3, 11):
    from exceptiongroup import BaseExceptionGroup


@contextmanager
def raises_in_exception_group(
    exc_type: type[BaseException], match: str | None = None
) -> Generator[Any, None, None]:
    with pytest.raises(BaseExceptionGroup) as exc_match:
        excinfo: pytest.ExceptionInfo[Any] = pytest.ExceptionInfo.for_later()
        yield excinfo

    if exc_match:
        exc: BaseException = exc_match.value
        while isinstance(exc, BaseExceptionGroup) and len(exc.exceptions) == 1:
            exc = exc.exceptions[0]

        with pytest.raises(exc_type, match=match):
            raise exc

        excinfo.fill_unfilled((type(exc), exc, cast(TracebackType, exc.__traceback__)))
