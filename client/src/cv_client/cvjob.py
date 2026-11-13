"""Starting a CV job and following it to the end.

The server answers at once and writes the CV as a session of events, which
this follows over one stream: what runs now, then each thing to store as it
is ready — a step's result, a line the server logged, the CV — and last
``done`` or ``error``. The session is keyed by the posting, so running the
same posting again finds it and replays it from the start: a client that
died, or lost its connection, loses nothing it paid for. Only once
everything is stored is the session acknowledged, and deleted on the server.
Both the full pipeline and ``submit-raw`` need exactly this, and neither
should have its own copy.
"""

from __future__ import annotations

import hashlib
import time
from typing import Callable, Union

from cv_contracts import Done, Failure, Status, StoreItem

from .api import CVApi, CVError, Files
from .servers import Server

#: How long to wait before reopening a stream that broke.
RECONNECT_SECONDS = 4.0

#: The job file a session is keyed by.
JD_FILE = "jd.txt"


def session_id(jd: bytes) -> str:
    """The posting's session on a server: the same text, the same session."""
    return hashlib.sha256(jd).hexdigest()


def write_cv(
    api: CVApi, server: Server, job: Files, *,
    say: Callable[[str], None], store: Callable[[StoreItem], None],
    new: bool = False, verbose: bool = False,
) -> None:
    """Write one CV with ``server``, reporting progress through ``say``.

    ``job`` is the posting as this server sees it (``jd.txt`` and what an
    earlier run stored); the pack is read from the server folder now.
    ``store`` is handed each thing to store as it arrives and must store it:
    only once the last event is in is the session acknowledged. A job that
    failed raises :class:`CVError` with the cause and status the work had,
    once its session is acknowledged too. ``new`` drops the posting's session
    first, so the CV is written again rather than replayed.
    """
    request_id = session_id(job[JD_FILE])
    if new:
        _drop(api, request_id)
    session = api.create_cv(server.pack(), job, server.settings, request_id).data
    if session.reattached:
        say("   ↺ this posting already has a run on the server: replaying it "
            "(--new starts over)")
    if verbose:
        say(f"   session: {request_id}")

    ending = _follow(api, server, request_id, say=say, store=store, verbose=verbose)
    if isinstance(ending, Done):
        if verbose and ending.detail:
            say(ending.detail)
        _acknowledge(api, request_id, say)
        return
    _acknowledge(api, request_id, say)
    raise CVError(ending.error, status=ending.http_status, request_id=request_id)


def _follow(
    api: CVApi, server: Server, request_id: str, *,
    say: Callable[[str], None], store: Callable[[StoreItem], None], verbose: bool,
) -> Union[Done, Failure]:
    """Every event up to the session's last, which is returned. A stream that
    breaks is reopened after the last event it delivered, until the timeout."""
    deadline = time.monotonic() + server.timeout
    after, last_status = 0, ""
    while True:
        try:
            for number, _, payload in api.events(request_id, after):
                after = number
                if isinstance(payload, (Done, Failure)):
                    return payload
                if isinstance(payload, Status):
                    if verbose and payload.detail:
                        say(payload.detail)
                    last_status = payload.status
                    say(f"   {payload.status}")
                elif isinstance(payload, StoreItem):
                    store(payload)
        except CVError as exc:
            if exc.status:  # the server answered: there is nothing to reconnect to
                raise
            reason = str(exc)
        else:
            reason = "the stream ended before the job did"
        if time.monotonic() > deadline:
            raise CVError(
                f"the CV job gave no answer within {server.timeout:.0f}s (last status: "
                f"{last_status or 'none'}); run the same posting again to pick it back up",
                request_id=request_id,
            )
        say(f"   ⚠ {reason}; reconnecting")
        time.sleep(RECONNECT_SECONDS)


def _drop(api: CVApi, request_id: str) -> None:
    """--new: acknowledge whatever the posting already has on the server."""
    try:
        api.acknowledge(request_id)
    except CVError as exc:
        if exc.status == 404:  # nothing there: nothing to drop
            return
        if exc.status == 409:
            raise CVError(
                "this posting's CV is still being written on the server — run it "
                "without --new to follow it, or once it has finished",
                status=409, request_id=request_id,
            ) from exc
        raise


def _acknowledge(api: CVApi, request_id: str, say: Callable[[str], None]) -> None:
    """The session has been dealt with. A failure here is not the job's: it
    only means the session stays until the server prunes it."""
    try:
        api.acknowledge(request_id)
    except CVError as exc:
        say(f"   ⚠ the server keeps this run for now (it could not be acknowledged: {exc})")


__all__ = ["write_cv", "session_id", "RECONNECT_SECONDS", "JD_FILE"]
