# isort: off
from __future__ import annotations

from dataclasses import dataclass

from asphalt.core import Event


@dataclass
class WebPageChangeEvent(Event):
    old_lines: list[str]
    new_lines: list[str]
