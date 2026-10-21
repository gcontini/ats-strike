"""ats-strike client: the CLI that turns job descriptions into CV folders.

It holds your data and does the filing; the server holds the API keys and does
the writing. Nothing here calls a model.

- ``cli`` / ``modes`` — the four things it does: clipboard, watch, submit, render.
- ``sources`` — where postings come from. The modes differ only in this.
- ``runner`` — the order of steps, once, for all of them.
- ``workspace`` — the output tree and every move inside it.
- ``api`` — the only module that knows HTTP.
- ``config`` / ``discovery`` — settings, and finding your files in the folder
  you run it in.
- ``tracking`` — applications.xlsx.
- ``joblog`` — log.log, including what each model call cost.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("ats-strike-client")
except PackageNotFoundError:  # a source tree that was never installed
    __version__ = "unknown"

__all__ = ["api", "cli", "config", "discovery", "joblog", "modes", "runner",
           "sources", "tracking", "ui", "workspace"]
