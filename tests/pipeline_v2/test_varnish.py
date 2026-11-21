"""The varnish (S8): pick, headline and sentence, terms in, with code's guards.

No key, no network: the fake HTTP client inside a real ``ModelSelector`` answers the three calls in
order, on S6's hand-made store of three experiences.
"""

from __future__ import annotations

import json

import pytest
from pipeline_helpers import FakeLLM, user_text
from test_document import FRAME, PROFILE
from test_select import store

from pipeline_v2 import ROLES, Varnisher, resources_dir, varnish_markdown
from pipeline_v2 import select as sel
from pipeline_v2.allocate import Allocation, Requirement
from pipeline_v2.document import document
from pipeline_v2.pool_store import Frame

#: The job: Terraform and Kubernetes are in the CV already; infrastructure as code is backed but
#: not named, so it is offered; Ansible landed nowhere, so it is not.
ALLOCATION = Allocation(
    title="Platform Engineer",
    requirements=[
        Requirement(id=1, name="Terraform", importance=100, keywords=["Terraform"], node="1.1.2", landing="proven"),
        Requirement(
            id=2,
            name="Infrastructure as code",
            importance=80,
            keywords=["IaC", "Infrastructure as Code"],
            node="1.1.2",
            landing="proven",
        ),
        Requirement(id=3, name="Configuration management", importance=60, keywords=["Ansible"]),
        Requirement(id=4, name="Kubernetes", importance=50, keywords=["Kubernetes"], node="1.1.1", landing="weak"),
    ],
)
DATA = {"name": "Ada", "languages": "English: native."}
#: e0's story 1 at rung 2 in wording b: what a pick of "b" prints.
WORDING_B = "Moved the Kubernetes platform to new clusters with the team, then ran the releases on them."


@pytest.fixture
def job():
    items, tree, pool, allocation = store()
    pool = pool.model_copy(update={"frames": [Frame(id="f1", family="Platform", text=FRAME, uses=["e1.i02"])]})
    next(r for r in pool.experiences[0].stories[0].ladder if r.id == "e0.s01.r2.b").text = WORDING_B
    ctx = sel.context(items, tree, pool, allocation, ["Platform Engineer"], {})
    slots = {"e0.s01": 2, "e0.s02": 1, "e2.s01": 1, "e2.s02": 1}
    plan = sel.plan_of({"forms": {0: "full", 1: "compact", 2: "full"}, "slots": slots}, ctx, 0.0)
    return plan.model_copy(update={"headline": None}), pool, items


def varnisher(*replies: dict) -> tuple:
    llm = FakeLLM(roles=ROLES)
    llm["varnish"].replies = [json.dumps(r) for r in replies]
    prompts = {
        f"{n}_prompt": (resources_dir() / f"sys_prompt_varnish_{n}.txt").read_text(encoding="utf-8")
        for n in ("pick", "headline", "terms")
    }
    return Varnisher(llm=llm, **prompts), llm


def run(job, pick=None, headline=None, terms=None):
    plan, pool, items = job
    v, llm = varnisher(
        pick or {"picks": {}, "titles": {}},
        headline or {"job_title": "Platform Engineer", "keywords": ["Terraform"], "sentence": ""},
        terms or {"replacements": []},
    )
    out = v.varnish(plan, PROFILE, pool, items, ALLOCATION, DATA)
    return out, document(plan, PROFILE, pool, "Site Reliability Engineer", out), llm


def test_a_pick_and_a_title_are_printed_and_a_pick_the_slot_lacks_is_dropped(job):
    out, doc, llm = run(job, pick={"picks": {"e0.s01": "b", "e0.s02": "z", "e9.s01": "a"}, "titles": {"e0": "b"}})
    assert out.picks == {"e0.s01": "e0.s01.r2.b"}
    assert out.titles == {0: "Ops Engineer"}
    e0 = doc["experiences"][0]
    assert e0["title"] == "Ops Engineer" and e0["duties"][0] == WORDING_B
    assert doc["experiences"][2]["title"] == "Engineer 2"  # one title: no choice
    # the call saw the wordings lettered and e0's titles; three calls on the varnish role
    assert "    b) " + WORDING_B in user_text(llm["varnish"].calls[0])
    assert "TITLES: a) Platform Engineer; b) Ops Engineer" in user_text(llm["varnish"].calls[0])
    assert len(llm["varnish"].calls) == 3


def test_the_headline_and_a_sentence_fill_the_summary(job):
    reply = {"job_title": "Cloud Platform Engineer", "keywords": ["Terraform"], "sentence": "I keep releases calm."}
    out, doc, llm = run(job, headline=reply)
    assert out.headline == "Cloud Platform Engineer" and out.sentence == "I keep releases calm."
    assert doc["job_title"] == "Cloud Platform Engineer"
    assert doc["summary"] == (
        "Cloud Platform Engineer with 20 years in platforms, covering Terraform. I keep releases calm."
    )
    system = next(m["content"] for m in llm["varnish"].calls[1]["messages"] if m["role"] == "system")
    assert "{max_chars}" not in system and "at most 450 characters" in system


def test_the_keywords_are_cut_to_three(job):
    reply = {"job_title": "Platform Engineer", "keywords": ["A", "B", "C", "D"], "sentence": ""}
    assert run(job, headline=reply)[0].keywords == ["A", "B", "C"]


def test_a_sentence_with_a_number_the_cv_does_not_print_is_refused(job):
    reply = {"job_title": "Platform Engineer", "keywords": ["Terraform"], "sentence": "I ran 7 platforms for 20 years."}
    out, doc, _ = run(job, headline=reply)
    assert out.sentence == "" and "7" in out.sentence_refused and "20" not in out.sentence_refused.split("(")[1]
    assert doc["summary"] == "Platform Engineer with 20 years in platforms, covering Terraform."


def test_a_terms_in_replacement_is_applied(job):
    plan, pool, _ = job
    old = document(plan, PROFILE, pool, "x")["experiences"][0]["duties"][0]
    assert old.startswith("Worked with Kubernetes on the platform")
    edit = {"target": "e0.s01.r2.a", "old": "on the platform", "new": "on the IaC platform"}
    out, doc, llm = run(job, terms={"replacements": [edit]})
    # the offer: the backed requirement the CV does not name, not the ones it names or that landed nowhere
    asked = user_text(llm["varnish"].calls[2])
    assert "- Infrastructure as code (80): IaC, Infrastructure as Code" in asked
    assert "Ansible" not in asked and "Kubernetes (50)" not in asked
    assert "[e0.s01.r2.a] (at most 192 characters, now 150)" in asked
    assert out.offered == ["IaC", "Infrastructure as Code"] and [r.target for r in out.applied] == ["e0.s01.r2.a"]
    assert doc["experiences"][0]["duties"][0] == old.replace("on the platform", "on the IaC platform", 1)
    assert "Applied:\n\n- **e0.s01.r2.a**" in varnish_markdown(out, plan)


def test_replacements_that_add_a_line_or_an_unoffered_term_are_refused(job):
    edits = [
        # e2.s01 at rung 1 holds 80 characters: this takes it to a second line
        {
            "target": "e2.s01.r1.a",
            "old": "on the platform",
            "new": "on the Infrastructure as Code platform of the bank",
        },
        {"target": "e0.s01.r2.a", "old": "on the platform", "new": "on the Ansible platform"},
        {"target": "summary", "old": "in platforms", "new": "in IaC platforms"},
    ]  # the summary is not a target
    out, doc, _ = run(job, terms={"replacements": edits})
    reasons = {r.target: r.reason for r in out.refused}
    assert reasons == {
        "e2.s01.r1.a": "2 lines, was 1",
        "e0.s01.r2.a": "adds Ansible, not offered",
        "summary": "not a printed bullet",
    }
    assert out.applied == [] and "IaC" not in doc["summary"]
    assert doc["experiences"][2]["duties"][0].startswith("Worked with Java on the platform")


def test_replacements_that_take_out_a_job_term_are_refused(job):
    edits = [
        {"target": "e0.s01.r2.a", "old": "Worked with Kubernetes on", "new": "Worked with IaC on"},
    ]
    out, doc, _ = run(job, terms={"replacements": edits})
    assert [(r.target, r.reason) for r in out.refused] == [
        ("e0.s01.r2.a", "removes Kubernetes"),
    ]
    assert out.applied == [] and doc["summary"].startswith("Platform Engineer with")
