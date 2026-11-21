"""The highlight (S9): common's highlighter fed v1-shaped inputs built from a v2 document.

No key, no network: S6's hand-made store of three experiences; the fake HTTP client inside a real
``ModelSelector`` answers the one highlight call.
"""

from __future__ import annotations

import json

import pytest
from pipeline_helpers import FakeLLM, section, user_text
from test_document import PROFILE
from test_select import store

from common.cv_renderer import RenderResult
from common.highlight import Highlighter
from pipeline_v2 import ROLES, resources_dir
from pipeline_v2 import select as sel
from pipeline_v2.allocate import Requirement
from pipeline_v2.document import document
from pipeline_v2.highlight import inputs, marked, marks
from pipeline_v2.render import render

#: Proven by every bullet: it lands on the parent of the three experiences' leaves.
TOOLS = Requirement(id=3, name="Tooling", importance=70, keywords=["tools"], node="1.1", relation="broader")


@pytest.fixture
def job():
    """e0 full (Kubernetes, requirement 2), e1 compact (Terraform, requirement 1), e2 full (Java, no
    requirement); every bullet uses an action at level 3."""
    items, tree, pool, allocation = store()
    allocation = allocation.model_copy(update={"requirements": [*allocation.requirements, TOOLS]})
    ctx = sel.context(items, tree, pool, allocation, ["Platform Engineer"], {})
    slots = {"e0.s01": 2, "e0.s02": 1, "e2.s01": 1}
    plan = sel.plan_of({"forms": {0: "full", 1: "compact", 2: "full"}, "slots": slots}, ctx, 0.0)
    doc = document(plan, PROFILE, pool, "Platform Engineer")
    return plan, ctx, pool, items, doc


def test_the_bullets_carry_the_requirements_they_prove_and_lose_them_after(job):
    plan, ctx, pool, items, doc = job
    key_points, cv, evidence = inputs(doc, plan, {}, ctx, pool, items)
    assert [k["id"] for k in key_points] == [1, 3, 2]
    assert key_points[0] == {"id": 1, "requirement": "Terraform", "keywords": ["Terraform"], "importance": 100}
    e0, e1, e2 = cv["experiences"]
    # level 3 on Kubernetes and on its parent: both at 90, the more important first
    assert e0["duties"] == [d + "|3-90,2-90" for d in doc["experiences"][0]["duties"]]
    assert e2["duties"] == [d + "|3-90" for d in doc["experiences"][2]["duties"]]
    assert e1 == {"compact": doc["experiences"][1]["compact"]}
    assert cv["summary"] == doc["summary"] and cv["skills"] == doc["skills"]
    # the evidence: per experience, each match with the text of the items the bullet uses
    assert [len(m) for m in evidence] == [4, 0, 1]
    assert evidence[0][0] == {"key_point": 3, "score": 90, "evidence": "x x"}
    # a reply that bolds a term in e0's first bullet: the scores go, the marker stays
    reply = json.loads(json.dumps(cv))
    reply["experiences"][0]["duties"][0] = e0["duties"][0].replace("Kubernetes", "**Kubernetes**", 1)
    found = marks(cv, reply)
    out = marked(doc, found)
    assert out["experiences"][0]["duties"][0] == doc["experiences"][0]["duties"][0].replace(
        "Kubernetes", "**Kubernetes**", 1
    )
    assert "|" not in json.dumps(out) and "|" not in json.dumps(found)


class Renderer:
    """Stands in for the LaTeX renderer: keeps the document it is given, always one page."""

    page_limit = 2

    def render_document(self, doc):
        self.document = doc
        return RenderResult(tex="", pdf=b"", pages=1, overflow_lines=0)


def test_one_highlight_call_puts_its_markers_into_the_rendered_document(job):
    plan, ctx, pool, items, doc = job
    llm = FakeLLM(roles=ROLES)

    def bold_skill_and_bullet(body):
        cv = section(body, "CV")
        cv["skills"][0] = f"**{cv['skills'][0]}**"
        cv["experiences"][2]["duties"][0] = cv["experiences"][2]["duties"][0].replace("Java", "**Java**", 1)
        return json.dumps(cv)

    llm["highlight"].replies = [bold_skill_and_bullet]
    sent = inputs(doc, plan, {}, ctx, pool, items)
    prompt = (resources_dir() / "sys_prompt_highlight.txt").read_text(encoding="utf-8")
    found = marks(sent[1], Highlighter(llm, prompt, "highlight").highlight(*sent))
    asked = user_text(llm["highlight"].calls[0])
    assert "KEY POINTS:" in asked and "|3-90,2-90" in asked and "EVIDENCE:" in asked
    renderer = Renderer()
    out = render(plan, ctx, PROFILE, pool, {"name": "Ada"}, renderer, None, found)
    assert out.document is renderer.document
    assert out.document["skills"][0] == f"**{doc['skills'][0]}**"
    assert out.document["experiences"][2]["duties"][0].startswith("Worked with **Java** on")
    assert out.document["name"] == "Ada" and "|" not in json.dumps(out.document)
