"""What every ats-strike pipeline is built on, and nothing about HTTP.

A library, as the pipelines are: it takes its inputs in memory and returns its
outputs, reads no configuration but the files it is handed, and writes nothing
outside the scratch directory it is handed.

- ``models`` — one long-lived chat model per role, from the ``models.toml`` a
  pipeline passes in.
- ``observability`` — what a run says about itself (every log line, every
  model call) and where it goes: :class:`Run`, set up by the caller.
- ``errors`` — the failures a pipeline raises, each with a kind and a stage.
- ``parsing`` — a rejected reply, logged short.
- ``highlight`` — :class:`Highlighter`: a CV's keywords marked bold, its
  stacks italic.
- ``cv_renderer`` — structured CV data in, a compiled PDF out, in a sandbox.

Its one prompt is ``resources/common``, packaged as ``common.resources``.
"""
