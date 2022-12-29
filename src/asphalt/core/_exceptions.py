from __future__ import annotations


class ApplicationExit(Exception):
    """
    Signals the Asphalt runner that the application should be shut down.
    If this exception propagates to the runner, it will turn it into a :exc:`SystemExit`
    exception with the same code.

    :param code: an exit code (0-127) or a termination reason
    """

    def __init__(self, code: int | str | None = None):
        super().__init__(code)
        self.code = code
