"""``cv version`` — this build, and whether each server is one it can talk to.

The client's own version is printed before anything else is read, so a broken
configuration or a server that is down never stops that answer. Each server —
the ``--server_ver`` ones, else the default — is then asked once; only one
that speaks another protocol is a failure, because that is the one thing the
check can prove.
"""

from __future__ import annotations

from typing import Optional

from cv_contracts import PROTOCOL

from .. import __version__
from ..api import CVError, HttpApi
from ..config import Config


def run(config: Optional[Config], not_checked: str = "") -> int:
    print(f"version {__version__}")
    if config is None:
        print(f"server: not checked ({not_checked})")
        return 0

    refused = 0
    for server in config.targets:
        try:
            status = HttpApi(server.url, token=server.token).status().data
        except CVError as exc:
            print(f"server {server.name}: not checked ({exc})")
            continue
        if status.protocol == PROTOCOL:
            verdict = "speaks this client's protocol"
        else:
            verdict = f"speaks protocol {status.protocol}, this client refuses to talk to it"
            refused += 1
        print(f"server {server.name}: v{status.version} at {server.url} — {verdict}")
    return 1 if refused else 0
