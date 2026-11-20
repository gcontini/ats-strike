"""What a pipeline hands a server: things to store, and what runs now.

Both pipelines report through the same two calls, so the server needs one
:class:`Sink` for either.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Union


@dataclass(frozen=True)
class Output:
    """One thing to store: a step's result, a store file, or the CV."""

    location: str  # "CV" | "CACHE"
    type: str  # "step" | "cv"
    name: str
    content: Union[str, bytes]


class Sink(Protocol):
    """Where a long call reports: what runs now, and each output as it is ready."""

    def status(self, status: str, detail: str = "") -> None: ...

    def store(self, output: Output) -> None: ...


__all__ = ["Output", "Sink"]
