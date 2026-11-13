"""ats-strike's CV pipeline: everything that makes a CV, and nothing about HTTP.

Takes its inputs in memory and returns its outputs; reads no configuration
but what :meth:`Pipeline.build` reads once at startup, and writes nothing
outside the scratch directory it is handed.

- ``facade`` — :class:`Pipeline`, the only thing a server calls, and the
  :class:`Output` / :class:`Sink` it reports through.
- ``pack`` — what the request's files and settings mean, as pure functions.
- ``cv_generator``, ``cv_review``, ``cv_renderer``, ``jd_validator``,
  ``letter_generator`` — the work itself.
- ``models`` — one long-lived chat model per role, from
  ``resources/models.toml``.
- ``bundle`` / ``defaults`` — the shipped defaults, and the per-request split
  between them and the personal inputs.
- ``observability`` — what a run says about itself (every log line, every
  model call) and where it goes: :class:`Run`, set up by the caller.

A server imports from here and never from a submodule.
"""

from .errors import PipelineError
from .facade import Output, Pipeline, Sink
from .observability import Run, configure_logging, current_run, use_run

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
