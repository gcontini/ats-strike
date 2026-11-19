"""Fixtures of the pipeline 2.0 tests.

They share v1's test double: :class:`FakeLLM`, a real ``ModelSelector`` over a
fake transport, from ``tests/pipeline_v1/pipeline_helpers.py``.
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline_v1"))

from pipeline_helpers import EXAMPLE_CANDIDATE, FakeLLM  # noqa: E402
from v2_helpers import cut  # noqa: E402

from pipeline_v2 import ROLES, ItemCompiler, Items, resources_dir  # noqa: E402
from pipeline_v2.store import DutyWeight, ExperienceItems, Item, Story  # noqa: E402
from pipeline_v2.tree_store import ExperienceLinks, Node, Tree, items_sha256  # noqa: E402


@pytest.fixture
def profile() -> dict:
    """The fictional example candidate's profile, fresh for each test."""
    return json.loads((EXAMPLE_CANDIDATE / "candidate_profile.json").read_text())


@pytest.fixture(scope="session")
def candidate_data() -> dict:
    return json.loads((EXAMPLE_CANDIDATE / "candidate_data.json").read_text())


@pytest.fixture(scope="session")
def example_items() -> Items:
    """The example candidate's items, built by a fake compile: the role summary as context, each
    duty one action, two duties per story. Copy it before changing it."""
    llm = FakeLLM(roles=ROLES)
    llm["compile"].replies = [cut]
    prompt = (resources_dir() / "sys_prompt_items.txt").read_text(encoding="utf-8")
    profile = json.loads((EXAMPLE_CANDIDATE / "candidate_profile.json").read_text())
    return ItemCompiler(llm=llm, system_prompt=prompt).compile(profile, today=date(2026, 11, 18))


# --- a hand-made store with one long story, for the pool ----------------------------------------
def _item(iid: str, duty: int, type_: str, text: str, story=None, agency=None, tech=()) -> Item:
    return Item(
        id=iid,
        experience=int(iid[1]),
        duty=duty,
        type=type_,
        text=text,
        source=["x"],
        agency=agency if agency is not None else ("" if type_ == "context" else "did"),
        tech=list(tech),
        tags=["t"],
        story=story,
    )


def long_story_items() -> Items:
    """Two experiences. e0 has two experience-level context items and three stories: e0.s01 is long
    (one context item, five actions and results), e0.s02 has two items, e0.s03 one. e1 has one
    context item and one story. Duty weights: e0 3, 2, 2, 1; e1 2, 1."""
    items = [
        _item("e0.i01", -1, "context", "A team of five ran the payments platform of a retail bank."),
        _item("e0.i02", -1, "context", "The platform served the bank's mobile app and its branches."),
        _item("e0.i03", 0, "context", "The old platform ran on hand-built virtual machines.", "e0.s01"),
        _item("e0.i04", 0, "action", "Led the move of the platform to Kubernetes.", "e0.s01", "led", ["Kubernetes"]),
        _item("e0.i05", 0, "action", "Packaged the services as Docker images.", "e0.s01", tech=["Docker"]),
        _item("e0.i06", 1, "action", "Wrote the Terraform modules for the new clusters.", "e0.s01", tech=["Terraform"]),
        _item("e0.i07", 1, "result", "Cut the release time from 3 hours to 40 minutes.", "e0.s01"),
        _item("e0.i08", 1, "action", "Trained the developers on the new release process.", "e0.s01"),
        _item("e0.i09", 2, "action", "Set up the monitoring of the payment flows.", "e0.s02"),
        _item("e0.i10", 2, "result", "Found two outages before the customers did.", "e0.s02"),
        _item("e0.i11", 3, "action", "Helped the security team with the yearly audit.", "e0.s03", "supported"),
        _item("e1.i01", -1, "context", "An online shop selling garden tools."),
        _item("e1.i02", 0, "action", "Built the checkout pages in Java.", "e1.s01", tech=["Java"]),
        _item("e1.i03", 1, "action", "Ran the weekly Scrum planning of the shop team.", "e1.s01"),
    ]
    e0 = ExperienceItems(
        id=0,
        sha256="",
        months=83,
        coverage=1.0,
        context=["e0.i01", "e0.i02"],
        weights=[DutyWeight(duty=d, weight=w, why="x") for d, w in enumerate((3, 2, 2, 1))],
        stories=[
            Story(
                id="e0.s01",
                experience=0,
                title="Move to Kubernetes",
                items=["e0.i03", "e0.i04", "e0.i05", "e0.i06", "e0.i07", "e0.i08"],
                anchor=["e0.i04"],
            ),
            Story(id="e0.s02", experience=0, title="Payment monitoring", items=["e0.i09", "e0.i10"], anchor=["e0.i09"]),
            Story(id="e0.s03", experience=0, title="Yearly audit", items=["e0.i11"], anchor=["e0.i11"]),
        ],
    )
    e1 = ExperienceItems(
        id=1,
        sha256="",
        months=58,
        coverage=1.0,
        context=["e1.i01"],
        weights=[DutyWeight(duty=d, weight=w, why="x") for d, w in enumerate((2, 1))],
        stories=[Story(id="e1.s01", experience=1, title="Checkout", items=["e1.i02", "e1.i03"], anchor=["e1.i02"])],
    )
    return Items(profile_sha256="", experiences=[e0, e1], items=items)


def long_story_tree(items: Items) -> Tree:
    """Kubernetes, Docker and Terraform proven; Agile weak (a context item and one level-1 action);
    English proven by ``outside``."""
    nodes = [
        Node(id="1", name="Cloud platforms"),
        Node(id="1.1", name="Containers"),
        Node(id="1.1.1", name="Kubernetes", aliases=["K8s"]),
        Node(id="1.1.2", name="Docker"),
        Node(id="1.2", name="Infrastructure as code"),
        Node(id="1.2.1", name="Terraform"),
        Node(id="2", name="Delivery"),
        Node(id="2.1", name="Methods"),
        Node(id="2.1.1", name="Agile", counts_as=["Scrum"]),
        Node(id="2.2", name="Languages"),
        Node(id="2.2.1", name="English", outside="languages"),
    ]
    links = {
        0: {
            "e0.i01": {"2.1.1": 2},
            "e0.i04": {"1.1.1": 3},
            "e0.i05": {"1.1.1": 2, "1.1.2": 1},
            "e0.i06": {"1.2.1": 2},
            "e0.i08": {"1.2.1": 1},
            "e0.i09": {"1.1.2": 1},
        },
        1: {"e1.i02": {"1.1.1": 1}, "e1.i03": {"2.1.1": 1}},
    }
    return Tree(
        market_sha256="",
        nodes=nodes,
        experiences=[
            ExperienceLinks(experience=e, items_sha256=items_sha256(items, e), links=links[e]) for e in (0, 1)
        ],
    )


LONG_PROFILE = {
    "job_title": ["Platform Engineer", "Cloud Engineer"],
    "writing_style": "Plain English.",
    "summaries": ["<<job_title>> with years of platform work."],
    "skills": ["Kubernetes", "Terraform"],
    "experiences": [
        {"id": 0, "title": "Platform Engineer", "company": "Example Bank", "dates": "January 2020 -- present"},
        {"id": 1, "title": "Developer", "company": "Example Shop", "dates": "March 2015 -- December 2019"},
    ],
}


@pytest.fixture
def long_items() -> Items:
    """:func:`long_story_items`, fresh for each test."""
    return long_story_items()


@pytest.fixture
def long_tree(long_items) -> Tree:
    return long_story_tree(long_items)


@pytest.fixture
def long_profile() -> dict:
    return json.loads(json.dumps(LONG_PROFILE))
