"""Plain helpers for the server tests (fixtures live in conftest.py)."""

from __future__ import annotations

import copy
import json
import os
import re
import threading
from pathlib import Path

import httpx

from cv_server.models import MODEL_ROLES, ModelConfig, ModelSelector, ModelSpec

REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_CANDIDATE = REPO_ROOT / "examples" / "candidate"
GOLDEN = Path(__file__).parent / "golden"

os.environ.setdefault("ATS_FAKE_KEY", "test-key")


#: What the cv role writes for the body, in the scripted run.
SAMPLE_BODY = {
    "job_title": "Staff Platform Engineer",
    "summary": "Engineer with 12 years of experience building distributed systems.",
    "skills": ["Kubernetes", "Terraform", "Go", "PostgreSQL", "AWS", "Observability"],
    "certifications": [{"date": "July 2023", "name": "Certified Kubernetes Administrator"}],
}

#: What the jd_evaluation role answers, in the scripted run.
KEY_POINTS = [
    {"id": 1, "requirement": "Kubernetes platforms", "keywords": ["Kubernetes"], "importance": 90},
    {"id": 2, "requirement": "Project delivery", "keywords": ["delivery"], "importance": 60},
]


def sample_cv(**overrides) -> dict:
    """Plain, valid CV content — no escaping edge cases — as the template reads it."""
    data = dict(
        company_name="Globex",
        job_title="Staff Platform Engineer",
        summary="Engineer with 12 years of experience building distributed systems.",
        skills=["Kubernetes", "Terraform", "Go", "PostgreSQL", "AWS", "Observability"],
        certifications=[],
        experiences=[
            {
                "title": "Principal Platform Engineer",
                "company": "Northwind Logistics",
                "location": "Lisbon, Portugal",
                "dates": "March 2022 -- Present",
                "project_name": "Freight tracking",
                "role_summary": "Platform team owning freight event ingestion.",
                "duties": ["Owned the **event ingestion** architecture."],
            },
            {
                "title": "Senior SRE",
                "company": "Meridian Health",
                "location": "Barcelona, Spain",
                "dates": "August 2018 -- February 2022",
                "project_name": None,
                "role_summary": "Reliability lead for a clinical records platform.",
                "duties": ["Designed multi-region failover."],
            },
            {
                "title": "Backend Engineer",
                "company": "Cobalt Analytics",
                "location": "Berlin, Germany",
                "dates": "May 2014 -- July 2018",
                "project_name": None,
                "role_summary": "Backend work on a customer-facing analytics product.",
                "duties": ["Built a Go query engine."],
            },
        ],
    )
    data.update(overrides)
    return copy.deepcopy(data)


class FakeRole:
    """How one role answers, and what it was asked.

    ``replies`` is consumed in order and the last one repeats, so a retry loop
    can be handed one failure then a success. ``finish_reason`` is reported on
    every reply — ``"length"`` plays a reply the token cap cut off. A reply
    that is a dict is sent as the whole response body; one that is a function
    is called with the request body and answers with either — the way to
    script calls that run side by side and arrive in any order. ``error``,
    when set, is raised instead of answering; ``gate`` holds the reply until it
    is opened. ``calls`` are the request bodies, as JSON, plus the URL ``path``.
    """

    def __init__(self) -> None:
        self.replies = ["{}"]
        self.finish_reason = "stop"
        self.error: Exception | None = None
        self.gate: threading.Event | None = None
        self.calls: list[dict] = []

    @property
    def last_prompt(self) -> str:
        return "\n".join(m["content"] or "" for m in self.calls[-1]["messages"])


class FakeLLM(ModelSelector):
    """A real :class:`ModelSelector` with a fake HTTP transport underneath.

    Only the transport is replaced, so everything the server relies on —
    request assembly, structured output, retries, usage — is the production
    code path. Role ``r`` calls model ``fake-r``; ``llm[r]`` is its
    :class:`FakeRole`. ``specs`` sets a role's ``ModelSpec`` fields.
    """

    def __init__(self, **specs: dict) -> None:
        self.roles = {role: FakeRole() for role in MODEL_ROLES}
        config = ModelConfig(
            provider={"api_key_env": "ATS_FAKE_KEY", "base_url": "http://fake.invalid/v1"},
            defaults={},
            models={
                role: ModelSpec(model=f"fake-{role}", **specs.get(role, {})) for role in MODEL_ROLES
            },
        )
        transport = httpx.MockTransport(self._answer)
        super().__init__(config, http_client=httpx.Client(transport=transport))

    def __getitem__(self, role: str) -> FakeRole:
        return self.roles[role]

    def _answer(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        role = self.roles[body["model"].removeprefix("fake-")]
        if role.gate is not None:
            assert role.gate.wait(timeout=10), "the gate was never opened"
        role.calls.append({**body, "path": request.url.path})
        if role.error is not None:
            raise role.error
        content = role.replies.pop(0) if len(role.replies) > 1 else role.replies[0]
        if callable(content):
            content = content(body)
        if isinstance(content, dict):
            return httpx.Response(200, json=content)
        return httpx.Response(200, json=completion(body["model"], content, role.finish_reason))


def completion(model: str, content: str, finish_reason: str = "stop") -> dict:
    """A chat.completion body: 11 tokens in, 22 out, 2 of them thinking."""
    return {
        "id": "fake", "object": "chat.completion", "created": 0, "model": model,
        "choices": [{
            "index": 0, "finish_reason": finish_reason,
            "message": {"role": "assistant", "content": content},
        }],
        "usage": {
            "prompt_tokens": 11, "completion_tokens": 22, "total_tokens": 33,
            "completion_tokens_details": {"reasoning_tokens": 2},
        },
    }


def tool_completion(model: str, calls: list[tuple[str, dict]]) -> dict:
    """A chat.completion body that calls tools: ``[(name, arguments), ...]``."""
    body = completion(model, None, "tool_calls")
    body["choices"][0]["message"]["tool_calls"] = [
        {"id": f"call_{n}", "type": "function",
         "function": {"name": name, "arguments": json.dumps(arguments)}}
        for n, (name, arguments) in enumerate(calls)
    ]
    return body


# --- a scripted CV run -------------------------------------------------------
def user_text(body: dict) -> str:
    """The first user message of a request body: what a step was given."""
    return next(m["content"] for m in body["messages"] if m["role"] == "user")


def section(body: dict, name: str):
    """The JSON a step's input carries under ``NAME:``."""
    after = user_text(body).split(f"{name}:\n", 1)[1]
    return json.JSONDecoder().raw_decode(after)[0]


def score_items(body: dict) -> str:
    """profile_scoring: every item matches key point 1, at 80."""
    return json.dumps([
        {"reason": "it matches", "matches": [{"key_point": 1, "score": 80, "evidence": "it"}]}
        for _ in section(body, "ITEMS")
    ])


def write_part(body: dict, written_body: dict | None = None) -> str:
    """cv: the body for the body prompt; for an experience, its first DUTY CAP
    duties, their scores kept."""
    text = user_text(body)
    if "DUTY CAP:" not in text:
        return json.dumps(written_body or SAMPLE_BODY)
    experience = section(body, "EXPERIENCE")
    cap = int(re.search(r"DUTY CAP: (\d+)", text).group(1))
    return json.dumps({
        "title": experience["title"], "company": experience["company"],
        "location": experience["location"], "dates": experience["dates"], "project_name": "",
        "role_summary": experience["role_summary"][:200], "duties": experience["duties"][:cap],
    })


def bold_summary(body: dict) -> str:
    """highlight: the CV as it was shown, its summary in bold."""
    cv = section(body, "CV")
    cv["summary"] = f"**{cv['summary']}**"
    return json.dumps(cv)


def script_cv(llm, *, ids=(0, 1, 2), caps=(2, 2, 2), fmt="A", body=None):
    """Every CV role answers, consistently, whatever order its calls come in:
    the validators find nothing to fix."""
    choice = {"reason": "the newest", "format": fmt,
              "experience_ids": list(ids), "duty_caps": list(caps)}
    llm["jd_evaluation"].replies = [json.dumps(KEY_POINTS)]
    llm["profile_scoring"].replies = [score_items]
    llm["format_selection"].replies = [json.dumps(choice)]
    llm["cv"].replies = [lambda request: write_part(request, body)]
    llm["validation"].replies = ["OK"]
    llm["highlight"].replies = [bold_summary]
    return llm


def start_cv(client, parts, *, request_id: str | None = None, **data) -> str:
    """POST /v1/cv and return the session id."""
    headers = {"X-Request-Id": request_id} if request_id else {}
    response = client.post("/v1/cv", data={"jd_text": "x" * 1200, **data}, files=parts,
                           headers=headers)
    assert response.status_code == 202, response.text
    return response.json()["request_id"]


def read_events(client, request_id: str, *, after: int = 0) -> list[tuple[int, str, dict]]:
    """Follow a session's stream to its end: ``[(id, type, data), ...]``.

    The stream ends with the job's last event, so this waits for the job; the
    fakes answer at once, and a closed gate gives up after ten seconds.
    """
    return [event for event in raw_events(client, request_id, after=after)
            if event[1] != "ping"]


def raw_events(client, request_id: str, *, after: int = 0) -> list[tuple[int, str, dict]]:
    """The same, with each keep-alive comment as a ``(0, "ping", {})``."""
    headers = {"Last-Event-ID": str(after)} if after else {}
    events, fields = [], {}
    with client.stream("GET", f"/v1/cv/{request_id}/events", headers=headers) as response:
        assert response.status_code == 200, response.read()
        assert response.headers["content-type"].startswith("text/event-stream")
        for line in response.iter_lines():
            if line.startswith(":"):
                events.append((0, "ping", {}))
            elif line:
                name, _, value = line.partition(": ")
                fields[name] = value
            elif fields:
                events.append((int(fields["id"]), fields["event"], json.loads(fields["data"])))
                fields = {}
    return events


def run_cv(client, parts, **data) -> list[tuple[int, str, dict]]:
    """Start a CV job and follow it to its end."""
    return read_events(client, start_cv(client, parts, **data))


def of_type(events, kind: str) -> list[dict]:
    """The payloads of one type of event, in order."""
    return [data for _, event, data in events if event == kind]
