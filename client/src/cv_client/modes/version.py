"""``cv version`` — this build, and whether the server is one it can talk to.

The client's own version is printed before anything else is read, so a broken
``cv.toml`` or a server that is down never stops that answer. The server is
then asked once; only a major version the client would refuse to talk to is a
failure, because that is the one thing the check can prove.
"""

from __future__ import annotations

from typing import Optional

from .. import __version__
from ..api import CVError, HttpApi, version_compat
from ..config import Config


def run(config: Optional[Config], not_checked: str = "") -> int:
    print(f"version {__version__}")
    if config is None:
        print(f"server: not checked ({not_checked})")
        return 0

    try:
        server_version = HttpApi(config.server_url, token=config.token).status().data.version
    except CVError as exc:
        print(f"server: not checked ({exc})")
        return 0

    compat = version_compat(__version__, server_version)
    verdict = {
        "ok": "matches this client",
        "warn": "minor versions differ, it works",
        "stop": "major versions differ, this client refuses to talk to it",
    }[compat]
    print(f"server: v{server_version} at {config.server_url} — {verdict}")
    return 1 if compat == "stop" else 0
