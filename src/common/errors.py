"""Failures the pipeline raises, so the API can map them without guessing.

Each one names a stage and carries enough context for the client's ``log.log``
to say what went wrong. Anything not listed here reaching the API layer is an
unexpected bug and is reported as such.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Dict, Iterator, Optional

from openai import APIConnectionError, APIError, APITimeoutError


class PipelineError(RuntimeError):
    """Base: a stage failed for a reason worth reporting verbatim."""

    kind = "pipeline"

    def __init__(self, message: str, *, stage: Optional[str] = None, detail: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.stage = stage
        self.detail = detail or {}


class LatexCompileError(PipelineError):
    """pdflatex refused the document (usually a bad template or bad escaping).

    ``log_tail`` is the end of pdflatex's own output, when it ran: the only
    thing that says where the document breaks.
    """

    kind = "latex_compile"

    def __init__(self, message: str, *, log_tail: str = "", **kwargs: Any) -> None:
        super().__init__(message, **kwargs)
        self.log_tail = log_tail


class LatexTimeoutError(PipelineError):
    """pdflatex ran past its timeout — a loop in a supplied template, normally."""

    kind = "latex_timeout"


class ModelOutputError(PipelineError):
    """The model never produced something valid, after every retry."""

    kind = "model_output"


class ProviderTimeoutError(PipelineError):
    """The model endpoint did not answer in time."""

    kind = "provider_timeout"


class ProviderUnreachableError(PipelineError):
    """The model endpoint could not be reached."""

    kind = "provider_unreachable"


class ProviderError(PipelineError):
    """The model endpoint answered with an error."""

    kind = "provider_error"


class MissingPartError(PipelineError):
    """A file the call needs is not in the pack or the job."""

    kind = "missing_part"


class BadPartError(PipelineError):
    """A file, or a setting, that cannot be read as what it has to be."""

    kind = "bad_part"


@contextmanager
def provider_errors() -> Iterator[None]:
    """A model endpoint's failure, as a :class:`PipelineError` of its kind: its body never
    travels, it can carry the endpoint and the key."""
    try:
        yield
    except APIError as exc:
        # A timeout is a connection error too, so it is asked first.
        failure = (
            ProviderTimeoutError
            if isinstance(exc, APITimeoutError)
            else ProviderUnreachableError
            if isinstance(exc, APIConnectionError)
            else ProviderError
        )
        raise failure(f"the model endpoint failed: {type(exc).__name__}") from exc


__all__ = [
    "provider_errors",
    "PipelineError",
    "LatexCompileError",
    "LatexTimeoutError",
    "ModelOutputError",
    "ProviderTimeoutError",
    "ProviderUnreachableError",
    "ProviderError",
    "MissingPartError",
    "BadPartError",
]
