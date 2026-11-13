"""``cv reset`` — delete every CV session a server still keeps.

A session lasts until the client that started it has saved its CV, or until
it is older than the server keeps them; this drops the rest at once, on the
``--server_ver`` servers or else the default, so the next run of any posting
writes its CV again. A job still running is left alone: its worker is still
writing there.
"""

from __future__ import annotations

from ..api import CVError
from ..config import Config
from ..ui import warn
from . import connect


def run(config: Config) -> int:
    failed = 0
    for name, calls in connect(config.targets, verbose=config.verbose).items():
        try:
            cleanup = calls.make(calls.api.reset)
        except CVError as exc:
            warn(f"{name}: {exc}")
            failed += 1
            continue
        kept = f"; {cleanup.running} still running, kept" if cleanup.running else ""
        print(f"🧹 {name}: deleted {cleanup.deleted} session(s){kept}", flush=True)
    return 1 if failed else 0
