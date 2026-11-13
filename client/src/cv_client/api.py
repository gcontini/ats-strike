"""The only module that knows a server exists.

Everything else in the client talks to :class:`CVApi`, the protocol
below, which is why the modes can be tested without a server and why swapping
transport would touch one file.

Every pipeline call sends the same three parts — the ``pack`` (the server
folder's files), the ``job`` (the posting as that server sees it) and the
``settings`` — and the client reads none of them: what they mean is the
server's business. What a reply wants kept comes back as things to store.

Every call returns the server's :class:`Envelope` rather than just its
payload, because the ``request_id`` in it is what a failure is reported
under. A CV job is the exception: it is a session the client follows, as a
stream of events, until its last.
"""

from __future__ import annotations

import json
import mimetypes
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Optional, Protocol, Tuple

import httpx
from pydantic import BaseModel
from cv_contracts import (
    EVENT_PAYLOADS,
    PROTOCOL,
    Analysed,
    Cleanup,
    CVSession,
    Detection,
    Envelope,
    Letter,
    Rendered,
    ServerStatus,
)

#: Files by their path relative to the folder they come from, ``/``-separated.
Files = Mapping[str, bytes]

#: One multipart part: the field name, then the file name, bytes and type.
Part = Tuple[str, Tuple[str, bytes, str]]

#: One event of a CV session: its number, its type, and its payload — the
#: model :data:`cv_contracts.EVENT_PAYLOADS` names for that type.
Event = Tuple[int, str, BaseModel]

#: Analysis, letters and renders are waited on, and a model call with retries
#: can take minutes. A CV's stream is never this quiet: the server pings it.
REQUEST_TIMEOUT = 500.0
CONNECT_TIMEOUT = 10.0

#: How many times to knock on /healthz before giving up on a cold server.
HEALTH_ATTEMPTS = 3
HEALTH_RETRY_DELAY = 2.0


class CVError(RuntimeError):
    """A call the server refused, or could not be made at all."""

    def __init__(
        self,
        message: str,
        *,
        status: int = 0,
        request_id: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        #: The HTTP status the server answered with; 0 when no answer came.
        self.status = status
        #: The request the failure belongs to — what the server logged it under.
        self.request_id = request_id


class CVApi(Protocol):
    """What the client needs an ats-strike server to do."""

    def status(self) -> Envelope[ServerStatus]: ...

    def detect(self, job: Files) -> Envelope[Detection]: ...

    def analyze(self, pack: Files, job: Files, settings: Mapping[str, Any]
                ) -> Envelope[Analysed]: ...

    def create_cv(self, pack: Files, job: Files, settings: Mapping[str, Any],
                  request_id: str) -> Envelope[CVSession]: ...

    def events(self, request_id: str, after: int = 0) -> Iterator[Event]: ...

    def acknowledge(self, request_id: str) -> Envelope[None]: ...

    def reset(self) -> Envelope[Cleanup]: ...

    def render(self, pack: Files, job: Files, settings: Mapping[str, Any]
               ) -> Envelope[Rendered]: ...

    def letter(self, pack: Files, job: Files, settings: Mapping[str, Any]
               ) -> Envelope[Letter]: ...


@dataclass
class HttpApi:
    """:class:`CVApi` over HTTP."""

    base_url: str
    token: Optional[str] = None
    #: Injection point for tests: a client that reaches the app in-process,
    #: so both halves are exercised together with no socket.
    client: Optional[httpx.Client] = None
    #: --verbose: report each /healthz attempt while the server wakes up.
    verbose: bool = False
    _client: httpx.Client = field(init=False, repr=False)
    _health_checked: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        if self.client is not None:
            self.client.headers.update(headers)
            self._client = self.client
            return
        self._client = httpx.Client(
            base_url=self.base_url.rstrip("/"),
            headers=headers,
            timeout=httpx.Timeout(REQUEST_TIMEOUT, connect=CONNECT_TIMEOUT),
        )

    # --- the calls ----------------------------------------------------------
    def status(self) -> Envelope[ServerStatus]:
        """What the server says about itself, once and without retries."""
        return self._send("GET", "/healthz", ServerStatus)

    def detect(self, job: Files) -> Envelope[Detection]:
        self._ensure_healthy()
        return self._send("POST", "/v1/jd/detect", Detection, **_parts({}, job, {}))

    def analyze(self, pack: Files, job: Files, settings: Mapping[str, Any]) -> Envelope[Analysed]:
        self._ensure_healthy()
        return self._send("POST", "/v1/jd/analysis", Analysed, **_parts(pack, job, settings))

    def create_cv(self, pack: Files, job: Files, settings: Mapping[str, Any],
                  request_id: str) -> Envelope[CVSession]:
        """Start a CV job as session ``request_id``, or find the one it already is."""
        self._ensure_healthy()
        return self._send("POST", "/v1/cv", CVSession, **_parts(pack, job, settings),
                          headers={"X-Request-Id": request_id}, request_id=request_id)

    def events(self, request_id: str, after: int = 0) -> Iterator[Event]:
        """The session's events after number ``after``, as they come, until
        its last. A stream that breaks raises :class:`CVError` with
        ``status`` 0; one the server refuses, with its status."""
        headers = {"Last-Event-ID": str(after)} if after else {}
        try:
            with self._client.stream("GET", f"/v1/cv/{request_id}/events",
                                     headers=headers) as response:
                if not response.is_success:
                    response.read()
                    self._envelope(response, f"/v1/cv/{request_id}/events", type(None))
                yield from _parse_events(response.iter_lines())
        except httpx.HTTPError as exc:
            raise CVError(
                f"lost the stream from the ats-strike server at {self.base_url}: {exc}",
                request_id=request_id,
            ) from exc

    def acknowledge(self, request_id: str) -> Envelope[None]:
        """Everything is in: the server may delete the session."""
        return self._send("DELETE", f"/v1/cv/{request_id}", type(None), request_id=request_id)

    def reset(self) -> Envelope[Cleanup]:
        """Delete every session on the server whose job is not running."""
        self._ensure_healthy()
        return self._send("DELETE", "/v1/cv", Cleanup, params={"all": ""})

    def render(self, pack: Files, job: Files, settings: Mapping[str, Any]) -> Envelope[Rendered]:
        self._ensure_healthy()
        return self._send("POST", "/v1/cv/render", Rendered, **_parts(pack, job, settings))

    def letter(self, pack: Files, job: Files, settings: Mapping[str, Any]) -> Envelope[Letter]:
        self._ensure_healthy()
        return self._send("POST", "/v1/letter", Letter, **_parts(pack, job, settings))

    # --- plumbing -----------------------------------------------------------
    def _ensure_healthy(self) -> None:
        """Before the first real call: make sure the server is up and speaks
        this client's protocol.

        A freshly started server can take a moment to become reachable, so
        this knocks on ``/healthz`` a few times rather than failing on the
        first refused connection. Checked once per client, not once per call.
        A protocol mismatch is not a connectivity problem, so it is not
        retried: it fails at once.
        """
        if self._health_checked:
            return
        last_error: Optional[CVError] = None
        for attempt in range(1, HEALTH_ATTEMPTS + 1):
            if self.verbose:
                print(f"spinning up the server at {self.base_url}, attempt "
                      f"{attempt}/{HEALTH_ATTEMPTS}", file=sys.stderr)
            try:
                envelope = self.status()
            except CVError as exc:
                last_error = exc
                if attempt < HEALTH_ATTEMPTS:
                    time.sleep(HEALTH_RETRY_DELAY)
                continue
            if envelope.data.protocol != PROTOCOL:
                raise CVError(
                    f"the server at {self.base_url} speaks protocol {envelope.data.protocol}, "
                    f"this client speaks protocol {PROTOCOL}: refusing to talk to it"
                )
            self._health_checked = True
            return
        raise CVError(
            f"the ats-strike server at {self.base_url} did not come up after "
            f"{HEALTH_ATTEMPTS} attempts: {last_error}",
            status=last_error.status if last_error else 0,
        )

    def _send(
        self, method: str, path: str, payload: type,
        *, request_id: Optional[str] = None, **kwargs,
    ) -> Envelope:
        try:
            response = self._client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            # A network error (e.g. a timeout) means no response came back to
            # read a request_id from, so the id already known to the caller
            # (a CV session) is the only one there is to report.
            raise CVError(
                f"cannot reach the ats-strike server at {self.base_url}: {exc}",
                request_id=request_id,
            ) from exc
        return self._envelope(response, path, payload)

    def _envelope(self, response: httpx.Response, path: str, payload: type) -> Envelope:
        """The envelope a response carries, or the failure it reports."""
        try:
            body = response.json()
        except ValueError as exc:
            raise CVError(
                f"{path} returned {response.status_code} and not JSON "
                f"({response.text[:200]!r})",
                status=response.status_code,
                request_id=response.headers.get("x-request-id"),
            ) from exc

        envelope = Envelope[payload].model_validate(body)
        if response.is_success and envelope.ok:
            return envelope

        raise CVError(
            envelope.error or f"{path} failed with {response.status_code}",
            status=response.status_code,
            request_id=envelope.request_id,
        )


def _parts(pack: Files, job: Files, settings: Mapping[str, Any]) -> Dict[str, Any]:
    """A pipeline request's three parts, as httpx takes them.

    Every file is its own ``pack`` or ``job`` part, named by its path: a list
    and not a mapping, because they share their field name. ``settings`` goes
    as JSON, and only when there is something in it.
    """
    files: List[Part] = [
        (part, (name, blob, mimetypes.guess_type(name)[0] or "application/octet-stream"))
        for part, source in (("pack", pack), ("job", job))
        for name, blob in source.items()
    ]
    data = {"settings": json.dumps(dict(settings))} if settings else {}
    return {"files": files, "data": data}


def _parse_events(lines: Iterable[str]) -> Iterator[Event]:
    """Server-sent events, one per blank-line-terminated block of fields.

    A line starting with ``:`` is a keep-alive. A type this client does not
    know — a newer server's — is skipped, as the format intends.
    """
    fields: Dict[str, str] = {}
    for line in lines:
        if line.startswith(":"):
            continue
        if line:
            name, _, value = line.partition(":")
            fields[name] = value.removeprefix(" ")
            continue
        kind = fields.get("event")
        if kind in EVENT_PAYLOADS:
            payload = EVENT_PAYLOADS[kind].model_validate_json(fields["data"])
            yield int(fields["id"]), kind, payload
        fields = {}


__all__ = ["CVApi", "HttpApi", "CVError", "Files", "Event"]
