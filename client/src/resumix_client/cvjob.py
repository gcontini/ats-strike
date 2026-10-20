"""Starting a CV job and following it to the end.

The server answers at once and writes the CV as a session of events, which
this follows over one stream: each step, every line the server logs, what
each model call cost, then the CV or the error, with the total. The session
is keyed by the posting, so running the same posting again finds it and
replays it from the start: a client that died, or lost its connection, loses
nothing it paid for. Only once the CV is safely stored is the session
acknowledged, and deleted on the server. Both the full pipeline and
``submit-raw`` need exactly this, and neither should have its own copy.
"""

from __future__ import annotations

import hashlib
import time
from typing import Callable, Optional, Union

from resumix_contracts import (
    CVFailure,
    CVResult,
    CVStatus,
    LogEntry,
    ModelCall,
    RenderedCV,
    TokenUsage,
)

from .api import ResumixApi, ResumixError, read_text
from .config import Config

#: How long to wait before reopening a stream that broke.
RECONNECT_SECONDS = 4.0


def session_id(jd_text: str) -> str:
    """The posting's session on the server: the same text, the same session."""
    return hashlib.sha256(jd_text.encode("utf-8")).hexdigest()


def write_cv(
    api: ResumixApi, config: Config, jd_text: str, *,
    say: Callable[[str], None], keep: Callable[[RenderedCV], None],
    log_line: Optional[Callable[[LogEntry], None]] = None, new: bool = False,
) -> None:
    """Write one CV, reporting progress through ``say``.

    ``keep`` is handed the finished CV and must store it; only once it has
    returned is the session acknowledged. A job that failed raises
    :class:`ResumixError` with the cause and status the work had, once its
    session is acknowledged too. ``log_line`` receives every line the server
    logged for the job. ``new`` drops the posting's session first, so the CV
    is written again rather than replayed.
    """
    request_id = session_id(jd_text)
    if new:
        _drop(api, request_id)
    session = api.create_cv(
        jd_text,
        request_id=request_id,
        profile=config.require("candidate_profile.json").read_bytes(),
        candidate_data=config.require("candidate_data.json").read_bytes(),
        prompts=config.prompt_overrides(),
        template=read_text(config.path("resume.tex.jinja")),
        images=config.image_parts(),
        temperature=config.temperature,
        presence_penalty=config.presence_penalty,
        pages=config.pages,
    ).data
    if session.reattached:
        say("   ↺ this posting already has a run on the server: replaying it "
            "(--new starts over)")
    if config.verbose:
        say(f"   session: {request_id}")

    ending = _follow(api, config, request_id, say=say, log_line=log_line)
    say(f"   Σ {_cost(ending.total)}")
    if isinstance(ending, CVResult):
        if config.verbose and ending.detail:
            say(ending.detail)
        keep(ending.cv)
        _acknowledge(api, request_id, say)
        return
    _acknowledge(api, request_id, say)
    raise ResumixError(ending.error, status=ending.http_status, request_id=request_id)


def _follow(
    api: ResumixApi, config: Config, request_id: str, *,
    say: Callable[[str], None], log_line: Optional[Callable[[LogEntry], None]],
) -> Union[CVResult, CVFailure]:
    """Every event up to the session's last, which is returned. A stream that
    breaks is reopened after the last event it delivered, until the timeout."""
    deadline = time.monotonic() + config.timeout
    after, seen_status, seen_detail = 0, "", ""
    while True:
        try:
            for number, _, payload in api.events(request_id, after=after):
                after = number
                if isinstance(payload, (CVResult, CVFailure)):
                    return payload
                if isinstance(payload, CVStatus):
                    if config.verbose and payload.detail and payload.detail != seen_detail:
                        seen_detail = payload.detail
                        say(payload.detail)
                    if payload.status != seen_status:
                        seen_status = payload.status
                        say(f"   {payload.status}")
                elif isinstance(payload, ModelCall):
                    say(f"   ⏱ {payload.role} {payload.model} {payload.seconds:.1f}s | "
                        f"{_tokens(payload)}")
                elif isinstance(payload, LogEntry) and log_line is not None:
                    log_line(payload)
        except ResumixError as exc:
            if exc.status:  # the server answered: there is nothing to reconnect to
                raise
            reason = str(exc)
        else:
            reason = "the stream ended before the job did"
        if time.monotonic() > deadline:
            raise ResumixError(
                f"the CV job gave no answer within {config.timeout:.0f}s (last status: "
                f"{seen_status or 'none'}); run the same posting again to pick it back up",
                request_id=request_id,
            )
        say(f"   ⚠ {reason}; reconnecting")
        time.sleep(RECONNECT_SECONDS)


def _drop(api: ResumixApi, request_id: str) -> None:
    """--new: acknowledge whatever the posting already has on the server."""
    try:
        api.acknowledge(request_id)
    except ResumixError as exc:
        if exc.status == 404:  # nothing there: nothing to drop
            return
        if exc.status == 409:
            raise ResumixError(
                "this posting's CV is still being written on the server — run it "
                "without --new to follow it, or once it has finished",
                status=409, request_id=request_id,
            ) from exc
        raise


def _acknowledge(api: ResumixApi, request_id: str, say: Callable[[str], None]) -> None:
    """The session has been dealt with. A failure here is not the job's: it
    only means the session stays until the server prunes it."""
    try:
        api.acknowledge(request_id)
    except ResumixError as exc:
        say(f"   ⚠ the server keeps this run for now (it could not be acknowledged: {exc})")


def _tokens(usage: Union[ModelCall, TokenUsage]) -> str:
    return (f"input={usage.input_tokens}, thinking={usage.thinking_tokens}, "
            f"output={usage.output_tokens}")


def _cost(total: TokenUsage) -> str:
    return f"{total.calls} model call(s), {total.seconds:.1f}s | {_tokens(total)}"


__all__ = ["write_cv", "session_id", "RECONNECT_SECONDS"]
