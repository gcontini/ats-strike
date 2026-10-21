"""``cv reset`` — delete every CV session the server still keeps.

A session lasts until the client that started it has saved its CV, or until
it is older than the server keeps them; this drops the rest at once, so the
next run of any posting writes its CV again. A job still running is left
alone: its worker is still writing there.
"""

from __future__ import annotations

from ..api import HttpApi, CVError
from ..config import Config
from ..ui import fail


def run(config: Config) -> int:
    api = HttpApi(config.server_url, token=config.token, verbose=config.verbose)
    try:
        cleanup = api.reset().data
    except CVError as exc:
        fail(f"{exc}")
    kept = f"; {cleanup.running} still running, kept" if cleanup.running else ""
    print(f"🧹 deleted {cleanup.deleted} session(s) on the server{kept}", flush=True)
    return 0
