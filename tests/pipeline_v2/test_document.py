"""The document and the render (S7): a plan into the template's namespace, the fit step, and one
real render of a small document.

No key, no network, no model: S6's hand-made store of three experiences.
"""

from __future__ import annotations

import json
import shutil

import pytest
from pipeline_helpers import EXAMPLE_CANDIDATE
from test_select import store

from common.cv_renderer import CVRenderer
from pipeline_v2 import resources_dir
from pipeline_v2 import select as sel
from pipeline_v2.document import SUMMARY_MAX, document
from pipeline_v2.pool_store import Frame
from pipeline_v2.render import render, room_left

needs_latex = pytest.mark.skipif(shutil.which("pdflatex") is None, reason="pdflatex not installed")

FRAME = "<<job_title>> with 20 years in platforms, covering <<keywords>>. <<sentence>>"

PROFILE = {
    "job_title": ["Site Reliability Engineer", "Platform Engineer"],
    "education": "MSc in Computer Engineering (2003).",
    "certifications": [{"date": "May 2024", "name": "CKA"}],
    "experiences": [
        {
            "id": e,
            "title": ["Platform Engineer", "Ops Engineer"] if e == 0 else f"Engineer {e}",
            "company": f"Company {e}",
            "location": "Milan, Italy",
            "dates": f"{2020 - 5 * e} -- {2024 - 5 * e}",
            "context": "Platform team" if e == 0 else "",
        }
        for e in range(3)
    ],
}


@pytest.fixture
def parts():
    items, tree, pool, allocation = store()
    pool = pool.model_copy(update={"frames": [Frame(id="f1", family="Platform", text=FRAME, uses=["e1.i02"])]})
    return sel.context(items, tree, pool, allocation, ["Platform Engineer"], {}), pool


def plan_of(ctx, forms, slots):
    return sel.plan_of({"forms": forms, "slots": slots}, ctx, 0.0)


# --- the document ---------------------------------------------------------------------------------
def test_full_and_compact_experiences(parts):
    ctx, pool = parts
    plan = plan_of(ctx, {0: "full", 1: "compact", 2: "full"}, {"e0.s01": 2, "e0.s02": 1, "e2.s01": 3, "e2.s02": 3})
    doc = document(plan, PROFILE, pool, "Site Reliability Engineer")
    e0, e1, e2 = doc["experiences"]
    texts = {b.id: b.text for blk in pool.experiences for s in blk.stories for b in s.ladder}
    # full: the role summary, then the slots' bullets in the plan's order, wording a
    assert e0["role_summary"] == pool.experiences[0].role_summary.text and e0["compact"] == ""
    assert e0["duties"] == [texts[s.bullets[0]] for s in plan.experiences[0].slots]
    assert all(s.bullets[0].endswith(".a") for x in plan.experiences for s in x.slots)
    assert len(e2["duties"]) == 2
    # compact: its one line, no role summary, no bullets
    assert e1["compact"] == pool.experiences[1].compact.text
    assert e1["role_summary"] == "" and e1["duties"] == []
    # the profile's fixed parts; a listed job title prints its first
    assert e0["title"] == "Platform Engineer" and e0["project_name"] == "Platform team" and e1["title"] == "Engineer 1"
    assert doc["education"] == PROFILE["education"] and doc["certifications"] == PROFILE["certifications"]
    assert doc["skills"] == plan.skills


def test_the_frame_is_filled_in_and_the_sentence_left_out(parts):
    ctx, pool = parts
    plan = plan_of(ctx, {0: "compact", 2: "compact"}, {})
    plan = plan.model_copy(update={"headline": "Platform Engineer", "keywords": ["Terraform", "Kubernetes"]})
    doc = document(plan, PROFILE, pool, "Site Reliability Engineer")
    assert doc["job_title"] == "Platform Engineer"
    assert doc["summary"] == "Platform Engineer with 20 years in platforms, covering Terraform and Kubernetes."
    # no headline: the title given
    doc = document(plan.model_copy(update={"headline": None}), PROFILE, pool, "Site Reliability Engineer")
    assert doc["job_title"] == "Site Reliability Engineer" and doc["summary"].startswith(
        "Site Reliability Engineer with"
    )


def test_a_summary_over_the_limit_is_refused(parts):
    ctx, pool = parts
    plan = plan_of(ctx, {0: "compact", 2: "compact"}, {}).model_copy(update={"headline": "X", "keywords": ["Go"]})
    room = SUMMARY_MAX - len(document(plan, PROFILE, pool, "X")["summary"])
    plan = plan.model_copy(update={"keywords": ["Go", "a" * room]})  # " and " + room: over by 5
    with pytest.raises(ValueError, match="over 500"):
        document(plan, PROFILE, pool, "X")


# --- the fit step and the render --------------------------------------------------------------------
def test_a_fit_step_steps_a_story_down_before_removing_one(parts):
    ctx, _ = parts
    draft = {"forms": {0: "full", 2: "full"}, "slots": {"e0.s01": 2, "e0.s02": 1, "e2.s01": 1, "e2.s02": 1}}
    assert sel.fit(draft, ctx)["slots"] == {"e0.s01": 1, "e0.s02": 1, "e2.s01": 1, "e2.s02": 1}
    # nothing left to step down: a removal, and an experience left with one bullet goes compact
    after = sel.fit(sel.fit(draft, ctx), ctx)
    assert len(after["slots"]) < 4 and "compact" in after["forms"].values()


@needs_latex
def test_a_small_document_renders_and_fits(parts, tmp_path):
    """Everything full at rung 3 runs over one page; with a one-page limit the fit steps it down
    until it fits."""
    ctx, pool = parts
    data = json.loads((EXAMPLE_CANDIDATE / "candidate_data.json").read_text())
    renderer = CVRenderer(
        template_source=(resources_dir() / "resume.tex.jinja").read_text(encoding="utf-8"),
        assets={"candidate_signature.png": (EXAMPLE_CANDIDATE / "candidate_signature.png").read_bytes()},
        work_dir=tmp_path,
        page_limit=1,
    )
    slots = {s: 3 for s in ("e0.s01", "e0.s02", "e1.s01", "e1.s02", "e2.s01", "e2.s02")}
    plan = plan_of(ctx, {0: "full", 1: "full", 2: "compact"}, slots)
    out = render(plan, ctx, PROFILE, pool, data, renderer)
    assert out.first_pages == 2 and out.result.pages == 1
    assert out.steps and out.steps[0].split(": ", 1)[1].startswith("down")
    assert room_left(out.result.pdf, data["location"], sel.PITCH_PT) >= 0
    assert out.document["name"] == data["name"]  # the candidate's data over the document
