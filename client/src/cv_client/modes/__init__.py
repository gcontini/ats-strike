"""The things the client does, one module per mode.

Each is thin on purpose: check what it was given, build the pieces, run them.
``watch``, ``clipboard`` and ``submit`` build theirs from
:mod:`cv_client.stages` — one pipeline, run three ways. ``submit-raw``
writes one CV and nothing around it; ``analysis``, ``render`` and ``reset``
make one call.
"""

from __future__ import annotations

from typing import Dict, Iterable

from ..api import HttpApi
from ..servers import Server
from ..stages.calls import Calls


def connect(servers: Iterable[Server], *, verbose: bool = False) -> Dict[str, Calls]:
    """Server name -> its calls, over HTTP."""
    return {
        server.name: Calls(server, HttpApi(server.url, token=server.token, verbose=verbose))
        for server in servers
    }
