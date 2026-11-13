"""ats-strike client: the CLI that turns job descriptions into CV folders.

It holds your data and does the filing; the servers hold the API keys and do
the writing. Nothing here calls a model, and nothing here knows what a
server's files mean: it sends each server its folder and stores what it is
told to store.

- ``cli`` / ``modes`` — what it does: clipboard, watch, submit, submit-raw,
  analysis, render, reset, version.
- ``sources`` — where postings come from. The modes differ only in this.
- ``stages`` — the steps a posting goes through, once, for all of them.
- ``workspace`` — the output tree, every move inside it, and each server's
  files in a job folder.
- ``api`` — the only module that knows HTTP; ``cvjob`` follows a CV session.
- ``config`` / ``servers`` — cv.toml, and the server folders it lists.
- ``jdanalysis`` — what the client reads of an analysis.
- ``tracking`` — applications.xlsx.
- ``joblog`` — log.log, including what each model call cost.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("ats-strike-client")
except PackageNotFoundError:  # a source tree that was never installed
    __version__ = "unknown"

__all__ = ["api", "cli", "config", "cvjob", "jdanalysis", "joblog", "modes", "servers",
           "sources", "stages", "tracking", "ui", "workspace"]
