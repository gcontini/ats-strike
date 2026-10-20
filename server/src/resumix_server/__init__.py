"""resumix server: the HTTP API that writes and renders CVs.

Layers, outermost first:

- ``api`` — FastAPI routers, multipart handling, auth, error mapping. Knows
  about HTTP and nothing about LaTeX or prompts.
- ``pipeline`` — :class:`CVGenerator` (the CV's six steps, with
  ``cv_review`` for its validation), :class:`CVRenderer`,
  :class:`JDValidator`, :class:`LetterGenerator`. Pure library code: every
  input arrives in memory, every output is returned, nothing is read from a
  configured path.
- ``models`` — one long-lived chat model per role, from
  ``resources/models.toml``, and ``call_llm`` / ``call_tools`` to ask it.
- ``bundle`` / ``defaults`` — where the impersonal defaults come from, and the
  per-request split between them and the personal inputs.
- ``observability`` / ``jobstore`` — what a CV job says about itself (every
  log line, every model call and its cost) and where it goes: the job's
  session, a directory of events under ``RESUMIX_WORK_DIR``.

The wire format lives in :mod:`resumix_contracts`, which both this and the
client depend on; neither imports the other.
"""

__all__ = ["api", "bundle", "defaults", "models", "observability", "pipeline"]
