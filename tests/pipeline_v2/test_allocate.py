"""The allocation (S5): code's rules on the reply, the points, and the one call.

No key, no network, no terminal: the allocator runs on :class:`FakeLLM`, whose fake transport sits
inside a real ``ModelSelector``. The tree is ``long_tree`` of ``conftest.py`` (Kubernetes, Docker,
Terraform and English proven, Agile weak) with one ghost leaf added, Pulumi.
"""

from __future__ import annotations

import json

import pytest

from common.errors import ModelOutputError
from pipeline_v2 import ROLES, Allocator, allocation_markdown, points, resources_dir
from pipeline_v2.allocate import Reply, Requirement, placed
from pipeline_v2.tree_store import Node, leaf_status

from pipeline_helpers import FakeLLM, user_text

JD = "Platform Engineer. Kubernetes is a must; Pulumi a plus."


@pytest.fixture
def tree(long_tree):
    return long_tree.model_copy(update={"nodes": [*long_tree.nodes, Node(id="1.2.2", name="Pulumi")]})


def req(n, node, relation, importance=100, name=None) -> dict:
    return {
        "id": n,
        "requirement": name or f"requirement {n}",
        "keywords": [f"term {n}"],
        "importance": importance,
        "node": node,
        "relation": relation,
    }


def reply(*requirements) -> dict:
    return {"title": "Platform Engineer", "requirements": list(requirements)}


def test_code_rules(tree, long_items):
    got = placed(
        Reply.model_validate(
            reply(
                req(1, "1.1.1", "same"),
                req(2, "9.9.9", "same"),  # unknown node
                req(3, "1.2.2", "same"),  # ghost leaf
                req(4, "1.2.2", "narrower"),
                req(5, "2.1.1", "Broader"),
                req(6, "1.1", "close"),  # not a relation
                req(7, None, "none"),
                req(8, None, "none", 40, "Success stories"),
                req(9, None, "none", 30, "concrete examples"),
            )
        ),
        tree,
        leaf_status(tree, long_items),
    )
    by_id = {r.id: (r.node, r.relation, r.landing) for r in got.requirements}
    assert by_id == {
        1: ("1.1.1", "same", "proven"),
        2: (None, "none", "nowhere"),
        3: ("1.2.2", "sibling", "ghost"),
        4: ("1.2.2", "narrower", "ghost"),
        5: ("2.1.1", "broader", "weak"),
        6: ("1.1", "none", "proven"),
        7: (None, "none", "nowhere"),
    }
    assert got.title == "Platform Engineer"
    assert got.requirements[0].keywords == ["term 1"]


def test_points(tree):
    def r(node, relation, importance):
        return Requirement(id=1, name="x", importance=importance, node=node, relation=relation)

    got = points(
        tree,
        [
            r("1.1.1", "same", 80),  # 0.8; 1.1 gets 0.4, 1 gets 0.2
            r("1.1.2", "narrower", 60),  # half: 0.3; 1.1 keeps the larger 0.4
            r("1.2.1", "broader", 100),  # 1.0; 1.2 gets 0.5, 1 the larger 0.25
            r("1.2.1", "sibling", 20),  # adds to its own node, 1.2; above, the largest single value stays
            r("2.1.1", "none", 90),  # counts nothing
            r(None, "none", 50),
        ],
    )
    assert got == {"1.1.1": 0.8, "1.1.2": 0.3, "1.1": 0.4, "1.2.1": 1.2, "1.2": 0.5, "1": 0.25}


def test_one_call(tree, long_items):
    llm = FakeLLM(roles=ROLES)
    answer = reply(req(1, "1.1.1", "same", 100, "Kubernetes"), req(2, "1.2.2", "same", 50, "Pulumi"))
    llm["allocate"].replies = ["```json\n" + json.dumps(answer) + "\n```"]
    prompt = (resources_dir() / "sys_prompt_allocate.txt").read_text(encoding="utf-8")
    allocator = Allocator(llm=llm, prompt=prompt)

    got = allocator.allocate(JD, tree, long_items)

    [call] = llm["allocate"].calls
    system = next(m["content"] for m in call["messages"] if m["role"] == "system")
    assert system.startswith(prompt.rstrip("\n") + "\n1 Cloud platforms\n")
    assert "\n1.1.1 Kubernetes | aliases: K8s\n" in system
    assert "\n2.1.1 Agile | counts as: Scrum\n" in system
    assert user_text(call) == JD
    assert [(r.name, r.relation, r.landing) for r in got.requirements] == [
        ("Kubernetes", "same", "proven"),
        ("Pulumi", "sibling", "ghost"),
    ]
    assert allocator.usage.calls == 1
    page = allocation_markdown(got, tree)
    assert "| 2 | Pulumi | 50 | term 2 | 1.2.2 Pulumi | sibling | ghost |" in page
    assert "| 1.1.1 Kubernetes | 1.000 |" in page


def test_broken_reply_goes_back_once(tree, long_items):
    llm = FakeLLM(roles=ROLES)
    llm["allocate"].replies = ['{"title": "x", "requirements": [', json.dumps(reply(req(1, "1.1.1", "same")))]
    got = Allocator(llm=llm, prompt="PROMPT\nTREE\n").allocate(JD, tree, long_items)

    assert len(got.requirements) == 1
    second = llm["allocate"].calls[1]["messages"]
    assert second[-1]["content"].startswith("Your previous reply was rejected:\n- the JSON breaks at character")

    llm["allocate"].replies = ['{"title": "x"}']
    with pytest.raises(ModelOutputError, match="requirements"):
        Allocator(llm=llm, prompt="PROMPT\nTREE\n").allocate(JD, tree, long_items)
