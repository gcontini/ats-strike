"""The only module that knows the server exists.

Everything else in the client talks to :class:`CVApi`, the protocol
below, which is why the modes can be tested without a server and why swapping
transport would touch one file.

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
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Optional, Protocol, Tuple

import httpx
from pydantic import BaseModel
from cv_contracts import (
    EVENT_PAYLOADS,
    Cleanup,
    CoverLetter,
    CVSession,
    Envelope,
    JDAnalysis,
    JDDetection,
    RenderedCV,
    ServerStatus,
)

from . import __version__

#: One multipart part: the field name, then the file name, bytes and type.
Part = Tuple[str, Tuple[str, Any, str]]

#: One event of a CV session: its number, its type, and its payload — the
#: model :data:`cv_contracts.EVENT_PAYLOADS` names for that type.
Event = Tuple[int, str, BaseModel]

#: Model role -> the request settings it runs on, instead of the server's.
ExtraBody = Mapping[str, Mapping[str, Any]]

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

    def detect(self, text: str) -> Envelope[JDDetection]: ...

    def analyze(
        self, text: str, *, profile: bytes, preferences: str, prompt: Optional[str] = None,
        extra_body: Optional[ExtraBody] = None,
    ) -> Envelope[JDAnalysis]: ...

    def create_cv(
        self, text: str, *, request_id: str, profile: bytes, candidate_data: bytes,
        prompts: Optional[Mapping[str, str]] = None, template: Optional[str] = None,
        images: Optional[Mapping[str, bytes]] = None, extra_body: Optional[ExtraBody] = None,
        pages: Optional[int] = None,
    ) -> Envelope[CVSession]: ...

    def events(self, request_id: str, *, after: int = 0) -> Iterator[Event]: ...

    def acknowledge(self, request_id: str) -> Envelope[None]: ...

    def reset(self) -> Envelope[Cleanup]: ...

    def render(
        self, *, document: Optional[Mapping[str, Any]] = None, tex: Optional[str] = None,
        template: Optional[str] = None, images: Optional[Mapping[str, bytes]] = None,
    ) -> Envelope[RenderedCV]: ...

    def letter(
        self, text: str, *, profile: bytes, candidate_data: bytes,
        analysis: Optional[str] = None, prompt: Optional[str] = None,
        extra_body: Optional[ExtraBody] = None,
    ) -> Envelope[CoverLetter]: ...


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
    def detect(self, text: str) -> Envelope[JDDetection]:
        self._ensure_healthy()
        return self._post("/v1/jd/detect", JDDetection, data={"jd_text": text})

    def analyze(self, text, *, profile, preferences, prompt=None, extra_body=None):
        files: List[Part] = [
            ("candidate_profile", ("candidate_profile.json", profile, "application/json")),
        ]
        if prompt is not None:
            files.append(("sys_prompt_analysis", ("sys_prompt_analysis.txt", prompt, "text/plain")))
        self._ensure_healthy()
        return self._post(
            "/v1/jd/analysis", JDAnalysis,
            data={"jd_text": text, "pers_preferences_text": preferences,
                  **_extra_bodies(extra_body)},
            files=files,
        )

    def create_cv(self, text, *, request_id, profile, candidate_data, prompts=None,
                  template=None, images=None, extra_body=None, pages=None):
        """Start a CV job as session ``request_id``, or find the one it already is.

        ``candidate_data`` goes with it because the server renders the CV to
        count its pages, and an empty contact block is not the page count of
        the CV you will send. No model is shown it.
        """
        files: List[Part] = [
            ("candidate_profile", ("candidate_profile.json", profile, "application/json")),
            ("candidate_data", ("candidate_data.json", candidate_data, "application/json")),
        ]
        for name, content in (prompts or {}).items():
            files.append((name, (f"{name}.txt", content, "text/plain")))
        if template is not None:
            files.append(("template", ("resume.tex.jinja", template, "text/plain")))
        files.extend(_image_parts(images))
        self._ensure_healthy()
        return self._post("/v1/cv", CVSession,
                          data={**_clean({"jd_text": text, "pages": pages}),
                                **_extra_bodies(extra_body)},
                          files=files, headers={"X-Request-Id": request_id},
                          request_id=request_id)

    def events(self, request_id: str, *, after: int = 0) -> Iterator[Event]:
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

    def render(self, *, document=None, tex=None, template=None, images=None):
        files: List[Part] = []
        if document is not None:
            files.append(("document", ("cv.json", json.dumps(document), "application/json")))
        if tex is not None:
            files.append(("tex", ("cv.tex", tex, "text/plain")))
        if template is not None:
            files.append(("template", ("resume.tex.jinja", template, "text/plain")))
        files.extend(_image_parts(images))
        return self._post("/v1/cv/render", RenderedCV, files=files)

    def letter(self, text, *, profile, candidate_data, analysis=None, prompt=None,
               extra_body=None):
        files: List[Part] = [
            ("candidate_profile", ("candidate_profile.json", profile, "application/json")),
            ("candidate_data", ("candidate_data.json", candidate_data, "application/json")),
        ]
        if analysis is not None:
            files.append(("analysis", ("analysis.json", analysis, "application/json")))
        if prompt is not None:
            files.append(("sys_prompt_letter", ("sys_prompt_letter.txt", prompt, "text/plain")))
        return self._post("/v1/letter", CoverLetter,
                          data={"jd_text": text, **_extra_bodies(extra_body)},
                          files=files)

    def status(self) -> Envelope[ServerStatus]:
        """What the server says about itself, once and without retries."""
        return self._get("/healthz", ServerStatus)

    # --- plumbing -----------------------------------------------------------
    def _ensure_healthy(self) -> None:
        """Before the first real call: make sure the server is up and speaks
        a version of the protocol this client understands.

        A freshly started server can take a moment to become reachable, so
        this knocks on ``/healthz`` a few times rather than failing on the
        first refused connection. Checked once per client, not once per call.
        A version mismatch is not a connectivity problem, so it is not
        retried — a major mismatch fails immediately.
        """
        if self._health_checked:
            return
        last_error: Optional[CVError] = None
        for attempt in range(1, HEALTH_ATTEMPTS + 1):
            if self.verbose:
                print(f"spinning up the server attempt {attempt}/{HEALTH_ATTEMPTS}",
                      file=sys.stderr)
            try:
                envelope = self.status()
            except CVError as exc:
                last_error = exc
                if attempt < HEALTH_ATTEMPTS:
                    time.sleep(HEALTH_RETRY_DELAY)
                continue
            self._check_server_version(envelope.data.version)
            self._health_checked = True
            return
        raise CVError(
            f"the ats-strike server at {self.base_url} did not come up after "
            f"{HEALTH_ATTEMPTS} attempts: {last_error}",
            status=last_error.status if last_error else 0,
        )

    def _check_server_version(self, server_version: str) -> None:
        """Warn on a minor mismatch, refuse to talk to a different major one."""
        compat = version_compat(__version__, server_version)
        if compat == "stop":
            raise CVError(
                f"client is v{__version__}, server at {self.base_url} is "
                f"v{server_version} — major versions differ, refusing to talk to it"
            )
        if compat == "warn":
            print(
                f"⚠ client is v{__version__}, server at {self.base_url} is "
                f"v{server_version} — minor versions differ, continuing anyway",
                file=sys.stderr, flush=True,
            )

    def _get(self, path: str, payload: type) -> Envelope:
        return self._send("GET", path, payload)

    def _post(self, path: str, payload: type, **kwargs) -> Envelope:
        return self._send("POST", path, payload, **kwargs)

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


def _image_parts(images: Optional[Mapping[str, bytes]]) -> List[Part]:
    """Every image as its own ``images`` part, named after the file it came from.

    A list of parts and not a mapping because they all share one field name:
    the server keys the assets off each part's file name, which is what the
    template includes them under.
    """
    return [
        ("images", (name, blob, mimetypes.guess_type(name)[0] or "application/octet-stream"))
        for name, blob in (images or {}).items()
    ]


def _clean(values: Dict[str, Any]) -> Dict[str, Any]:
    """Drop unset form fields so the server sees its own defaults."""
    return {k: v for k, v in values.items() if v is not None}


def _extra_bodies(extra_body: Optional[ExtraBody]) -> Dict[str, str]:
    """Every configured extra_body as a ``<role>_extra_body`` form field.

    All of them go with every request: each endpoint reads the roles it has
    and ignores the rest, so the client keeps no list of which is which.
    """
    return {f"{role}_extra_body": json.dumps(body) for role, body in (extra_body or {}).items()}


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


def version_compat(client_version: str, server_version: str) -> str:
    """'ok', 'warn' (minor differs) or 'stop' (major differs).

    A version that does not parse as ``MAJOR.MINOR[.PATCH]`` — ``"unknown"``,
    a source tree that was never installed — is not a real version to compare
    against, so it is always 'ok'.
    """
    client = _major_minor(client_version)
    server = _major_minor(server_version)
    if client is None or server is None:
        return "ok"
    if client[0] != server[0]:
        return "stop"
    if client[1] != server[1]:
        return "warn"
    return "ok"


def _major_minor(version: str) -> Optional[Tuple[int, int]]:
    parts = version.split(".")
    if len(parts) < 2:
        return None
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return None


def read_text(path: Optional[Path]) -> Optional[str]:
    return path.read_text(encoding="utf-8") if path is not None else None


__all__ = ["CVApi", "HttpApi", "CVError", "read_text", "version_compat"]
