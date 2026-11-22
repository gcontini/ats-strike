"""The varnish (S8): compose, headline and sentence, terms in, with code's guards.

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
from pipeline_v2.pool_store import Frame, Rung, StoryPool
from pipeline_v2.varnish import chosen

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
#: e0's third story, which the plan leaves out: one line at rung 1, two at rung 2.
THIRD = "Ran the Kubernetes upgrades of the platform on weekends."
THIRD_R2 = THIRD[:-1] + ", with a rollback plan for each cluster and a test run on staging the week before each one."


def make_job(third: bool = False) -> tuple:
    """The plan (e0 and e2 full, two bullets each; e1 compact), its pool, items and context."""
    items, tree, pool, allocation = store()
    pool = pool.model_copy(update={"frames": [Frame(id="f1", family="Platform", text=FRAME, uses=["e1.i02"])]})
    next(r for r in pool.experiences[0].stories[0].ladder if r.id == "e0.s01.r2.b").text = WORDING_B
    if third:
        ladder = [
            Rung(id=f"e0.s03.r{r}.a", rung=r, wording="a", text=t, uses=["e0.i02"], covers=["e0.i02"])
            for r, t in ((1, THIRD), (2, THIRD_R2))
        ]
        pool.experiences[0].stories.append(StoryPool(story="e0.s03", ladder=ladder))
    # a profile title unlike the job's: the plan has no headline, the headline call writes it
    ctx = sel.context(items, tree, pool, allocation, ["Site Reliability Engineer"], {})
    slots = {"e0.s01": 2, "e0.s02": 1, "e2.s01": 1, "e2.s02": 1}
    plan = sel.plan_of({"forms": {0: "full", 1: "compact", 2: "full"}, "slots": slots}, ctx, 0.0)
    assert plan.headline is None
    return plan, pool, items, ctx


@pytest.fixture
def job():
    return make_job()


def varnisher(*replies: dict) -> tuple:
    llm = FakeLLM(roles=ROLES)
    llm["varnish"].replies = [json.dumps(r) for r in replies]
    prompts = {
        f"{n}_prompt": (resources_dir() / f"sys_prompt_varnish_{n}.txt").read_text(encoding="utf-8")
        for n in ("compose", "headline", "terms")
    }
    return Varnisher(llm=llm, **prompts), llm


def run(job, compose=None, headline=None, terms=None):
    """The varnish's result, the composed plan's document, the fake model and the composed plan."""
    plan, pool, items, ctx = job
    v, llm = varnisher(
        compose or {},
        headline or {"job_title": "Platform Engineer", "keywords": ["Terraform"], "sentence": ""},
        terms or {"replacements": []},
    )
    composed, out = v.varnish(plan, ctx, PROFILE, pool, items, ALLOCATION, DATA)
    return out, document(composed, PROFILE, pool, "Site Reliability Engineer", out), llm, composed


# --- compose -------------------------------------------------------------------------------------
def test_the_composition_orders_the_bullets_and_picks_a_wording_and_a_title(job):
    reply = {"experiences": {"e0": ["e0.s02.r1.a", "e0.s01.r2.b"], "e9": ["x"]}, "titles": {"e0": "b"}}
    out, doc, llm, composed = run(job, compose=reply)
    assert [s.story for s in composed.experiences[0].slots] == ["e0.s02", "e0.s01"]
    assert out.picks == {"e0.s02": "e0.s02.r1.a", "e0.s01": "e0.s01.r2.b"} and out.kept == {}
    assert out.titles == {0: "Ops Engineer"}
    e0 = doc["experiences"][0]
    assert e0["title"] == "Ops Engineer" and e0["duties"][1] == WORDING_B
    assert doc["experiences"][2]["title"] == "Engineer 2"  # one title: no choice
    # the call saw the lines, the role summary, the scored candidates with their wording ids, the titles
    asked = user_text(llm["varnish"].calls[0])
    assert "FULL, at most 3 LINES" in asked and "  ROLE SUMMARY: Worked with Kubernetes" in asked
    assert "  e0.s01.r2 PICKED (2 lines, score " in asked
    assert "    e0.s01.r2.b: " + WORDING_B in asked
    assert "TITLES: a) Platform Engineer; b) Ops Engineer" in asked
    assert len(llm["varnish"].calls) == 3


def test_a_candidate_the_plan_left_out_is_swapped_in_within_the_lines():
    job = make_job(third=True)
    out, doc, llm, composed = run(job, compose={"experiences": {"e0": ["e0.s03.r1.a", "e0.s01.r2.a"]}})
    assert [(s.story, s.rung) for s in composed.experiences[0].slots] == [("e0.s03", 1), ("e0.s01", 2)]
    assert doc["experiences"][0]["duties"][0] == THIRD
    asked = user_text(llm["varnish"].calls[0])
    assert "  e0.s03.r1 (1 line, score " in asked and "  e0.s03.r2 (2 lines, score " in asked
    view = varnish_markdown(out, job[0], composed)
    assert "- e0: e0.s03.r1.a (swapped in), e0.s01.r2.a; left out e0.s02" in view
    # two lines more than the plan's three
    out, *_ = run(job, compose={"experiences": {"e0": ["e0.s03.r2.a", "e0.s01.r2.a"]}})
    assert out.kept == {0: "4 lines, over 3"}


@pytest.mark.parametrize(
    "ids, reason",
    [
        (["e0.s01.r2.a", "e0.s09.r1.a"], "e0.s09.r1.a is not a candidate"),
        (["e0.s01.r2.a", "e0.s02.r2.a"], "e0.s02.r2.a is not a candidate"),  # a picked story at its rung only
        (["e0.s01.r2.a", "e0.s01.r2.b"], "two of e0.s01"),
        (["e0.s01.r2.a"], "1 bullet(s), fewer than 2"),
    ],
)
def test_a_composition_code_refuses_keeps_the_plan_slots(job, ids, reason):
    out, doc, _, composed = run(job, compose={"experiences": {"e0": ids}})
    assert out.kept == {0: reason} and out.picks == {}
    assert composed.experiences == job[0].experiences
    assert f"  - kept the plan's slots: {reason}" in varnish_markdown(out, job[0], composed)


def test_a_group_is_named_by_its_id_and_counts_its_bullets():
    group = sel.Candidate("e0.s02.g1", "e0.s02", "g1", ("e0.s02.g1.1", "e0.s02.g1.2"), 4, 0.0, True)
    assert chosen(["e0.s02.g1"], [group], 4) == ([("e0.s02", "g1")], {}, "")
    assert chosen(["e0.s02.g1.1"], [group], 4)[2] == "e0.s02.g1.1 is not a candidate"


def test_an_edit_applies_and_one_that_adds_a_line_is_refused(job):
    edits = [
        {"target": "e0.s01.r2.a", "old": "Kubernetes on the platform", "new": "Kubernetes on this platform"},
        # e2.s01 at rung 1 holds 80 characters: this takes it to a second line
        {"target": "e2.s01.r1.a", "old": "on the platform", "new": "on the platform of the bank and its insurer"},
    ]
    out, doc, _, _ = run(job, compose={"edits": edits})
    assert [r.target for r in out.edits] == ["e0.s01.r2.a"]
    assert {r.target: r.reason for r in out.edits_refused} == {"e2.s01.r1.a": "2 lines, was 1"}
    assert doc["experiences"][0]["duties"][0].startswith("Worked with Kubernetes on this platform")


def test_the_terms_step_edits_the_composed_text(job):
    edit = {"target": "e0.s01.r2.a", "old": "Kubernetes on the platform", "new": "Kubernetes on this platform"}
    term = {"target": "e0.s01.r2.a", "old": "on this platform", "new": "on this IaC platform"}
    out, doc, llm, _ = run(job, compose={"edits": [edit]}, terms={"replacements": [term]})
    assert "Worked with Kubernetes on this platform" in user_text(llm["varnish"].calls[2])
    assert doc["experiences"][0]["duties"][0].startswith("Worked with Kubernetes on this IaC platform")
    assert [r.target for r in out.edits] == [r.target for r in out.applied] == ["e0.s01.r2.a"]


# --- headline, sentence, terms in ------------------------------------------------------------------


def test_the_headline_and_a_sentence_fill_the_summary(job):
    reply = {"job_title": "Cloud Platform Engineer", "keywords": ["Terraform"], "sentence": "I keep releases calm."}
    out, doc, llm, _ = run(job, headline=reply)
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
    out, doc, _, _ = run(job, headline=reply)
    assert out.sentence == "" and "7" in out.sentence_refused and "20" not in out.sentence_refused.split("(")[1]
    assert doc["summary"] == "Platform Engineer with 20 years in platforms, covering Terraform."


def test_a_terms_in_replacement_is_applied(job):
    plan, pool, _, _ = job
    old = document(plan, PROFILE, pool, "x")["experiences"][0]["duties"][0]
    assert old.startswith("Worked with Kubernetes on the platform")
    edit = {"target": "e0.s01.r2.a", "old": "on the platform", "new": "on the IaC platform"}
    out, doc, llm, composed = run(job, terms={"replacements": [edit]})
    # the offer: the backed requirement the CV does not name, not the ones it names or that landed nowhere
    asked = user_text(llm["varnish"].calls[2])
    assert "- Infrastructure as code (80): IaC, Infrastructure as Code" in asked
    assert "Ansible" not in asked and "Kubernetes (50)" not in asked
    assert "[e0.s01.r2.a] (at most 192 characters, now 150)" in asked
    assert out.offered == ["IaC", "Infrastructure as Code"] and [r.target for r in out.applied] == ["e0.s01.r2.a"]
    assert doc["experiences"][0]["duties"][0] == old.replace("on the platform", "on the IaC platform", 1)
    assert "Applied:\n\n- **e0.s01.r2.a**" in varnish_markdown(out, plan, composed)


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
    out, doc, _, _ = run(job, terms={"replacements": edits})
    reasons = {r.target: r.reason for r in out.refused}
    assert reasons == {
        "e2.s01.r1.a": "2 lines, was 1",
        "e0.s01.r2.a": "adds Ansible, not offered",
        "summary": "not a printed bullet",
    }
    assert out.applied == [] and "IaC" not in doc["summary"]
    assert doc["experiences"][2]["duties"][0].startswith("Worked with Java on the platform")
