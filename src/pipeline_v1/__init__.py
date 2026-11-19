"""ats-strike's CV pipeline: everything that makes a CV, and nothing about HTTP.

Takes its inputs in memory and returns its outputs; reads no configuration
but what :meth:`Pipeline.build` reads once at startup, and writes nothing
outside the scratch directory it is handed.

- ``facade`` — :class:`Pipeline`, the only thing a server calls, and the
  :class:`Output` / :class:`Sink` it reports through.
- ``pack`` — what the request's files and settings mean, as pure functions.
- ``cv_generator``, ``jd_validator``, ``letter_generator`` — the work itself.
- ``bundle`` / ``defaults`` — the shipped defaults, and the per-request split
  between them and the personal inputs.

It is built on :mod:`common`: the models (one long-lived chat model per role,
from ``resources/v1/models.toml``), the run's observability, the errors, the
highlighter, the validation loop and the renderer. :class:`PipelineError`,
:class:`Run`, :func:`use_run`, :func:`current_run` and
:func:`configure_logging` are re-exported from there.

A server imports from here and never from a submodule.
"""

from common.errors import PipelineError
from common.observability import Run, configure_logging, current_run, use_run

from .facade import Output, Pipeline, Sink

__all__ = [
    "Pipeline",
    "Output",
    "Sink",
    "PipelineError",
    "Run",
    "use_run",
    "current_run",
    "configure_logging",
]
