from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

import pytest
from exceptiongroup import BaseExceptionGroup


@contextmanager
def raises_in_exception_group(
    exc_type: type[BaseException], match: str | None = None
) -> Generator[Any, None, None]:
    with pytest.raises(BaseExceptionGroup) as exc_match:
        yield exc_match

    if exc_match:
        exc: BaseException = exc_match.value
        while isinstance(exc, BaseExceptionGroup) and len(exc.exceptions) == 1:
            exc = exc.exceptions[0]

        with pytest.raises(exc_type, match=match):
            raise exc
