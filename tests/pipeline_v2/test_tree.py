"""The competence tree (S2): leaf status, outside notes, checks, validation, the view, and the graph.

No key, no network, no terminal: the graph runs on :class:`FakeLLM`, whose fake transport sits
inside a real ``ModelSelector``. The pure parts run on a small hand-made tree; the graph on the
example candidate's items built by a fake compile.
"""

from __future__ import annotations

import json
import re
import time

import pytest

from common.errors import ModelOutputError
from pipeline_v2 import ROLES, Items, TreeBuilder, leaf_status, resources_dir, tree_markdown, validate_tree
from pipeline_v2 import tree_store
from pipeline_v2.store import ExperienceItems, Item
from pipeline_v2.tree import attach_input, build_links, parse_shape
from pipeline_v2.tree_store import (
    ExperienceLinks,
    Market,
    MarketTerm,
    Node,
    Tree,
    checks,
    items_sha256,
    market_sha256,
    propose_outside,
    tree_dump,
)

from pipeline_helpers import FakeLLM, user_text


# --- a small hand-made store ------------------------------------------------------------
def it(iid: str, type_: str, text: str, tech=()) -> Item:
    return Item(
        id=iid,
        experience=int(iid[1]),
        duty=0,
        type=type_,
        text=text,
        source=["x"],
        agency="" if type_ == "context" else "did",
        tech=list(tech),
        tags=["t"],
    )


def hand_items() -> Items:
    items = [
        it("e0.i01", "context", "The team used Agile methodologies on a mixed Linux and Windows network."),
        it("e0.i02", "action", "Administered the network and its servers."),
        it("e0.i03", "action", "Deployed the services on Kubernetes.", tech=["Kubernetes", "Docker"]),
        it("e0.i04", "result", "Cut the release time by half."),
        it("e1.i01", "action", "Wrote Terraform modules for the staging stack.", tech=["Terraform"]),
        it("e1.i02", "action", "Presented the plan to management."),
        it("e1.i03", "action", "Answered the phone."),
    ]
    experiences = [
        ExperienceItems(id=e, sha256="", months=12, weights=[], context=[], stories=[], coverage=1.0) for e in (0, 1)
    ]
    return Items(profile_sha256="", experiences=experiences, items=items)


def n(id_: str, name: str, **fields) -> Node:
    return Node(id=id_, name=name, **fields)


NODES = [
    n("1", "Cloud infrastructure"),
    n("1.1", "Container orchestration"),
    n("1.1.1", "Kubernetes", aliases=["K8s"], shape_proven=True),
    n("1.1.2", "OpenShift", shape_proven=True),
    n("1.1.3", "Docker"),
    n("1.2", "Infrastructure as code"),
    n("1.2.1", "Terraform"),
    n("1.3", "System administration"),
    n("1.3.1", "Linux"),
    n("1.3.2", "Windows"),
    n("2", "Project management"),
    n("2.1", "Delivery methods"),
    n("2.1.1", "Agile", counts_as=["Scrum", "Kanban"]),
    n("2.2", "Communication"),
    n("2.2.1", "Client presentations"),
    n("2.2.2", "English", outside="languages"),
]

MARKET = Market(
    build_jobs=41,
    corpus_jobs=2700,
    excluded={"test": 50, "held_out": 10, "duplicates": 0},
    terms=[
        MarketTerm(term="Kubernetes", jobs=9, asked=0.05, share=0.2),
        MarketTerm(term="DevOps", jobs=2, asked=0.03, share=0.134),
        MarketTerm(term="Scrum", jobs=3, asked=0.01, share=0.04),
        MarketTerm(term="Fortran", jobs=1, asked=0.001, share=0.01),
    ],
    requirements=[{"name": "Kubernetes platforms", "importance": 90, "keywords": ["Kubernetes"]}],
)


def hand_tree(items: Items = None) -> Tree:
    items = items or hand_items()
    links = {
        0: {
            "e0.i01": {"2.1.1": 1, "1.3.1": 1},
            "e0.i02": {"1.3.2": 2},
            "e0.i03": {"1.1.1": 3},
            "e0.i04": {"1.1.1": 1, "2.2.1": 2},
        },
        1: {"e1.i01": {"1.2.1": 2}, "e1.i02": {"2.2.1": 1}},
    }
    return Tree(
        market_sha256=market_sha256(MARKET),
        nodes=[x.model_copy() for x in NODES],
        experiences=[
            ExperienceLinks(experience=e, items_sha256=items_sha256(items, e), links=links[e]) for e in (0, 1)
        ],
    )


# --- leaf_status --------------------------------------------------------------------------
def test_a_level_3_link_proves_and_two_weak_links_prove():
    status = leaf_status(hand_tree(), hand_items())
    assert status["1.1.1"] == "proven"  # e0.i03 at level 3
    assert status["2.2.1"] == "proven"  # e0.i04 at 2 and e1.i02 at 1


def test_one_weak_link_is_weak_and_nothing_is_a_ghost():
    status = leaf_status(hand_tree(), hand_items())
    assert status["1.2.1"] == "weak" and status["1.3.2"] == "weak"
    assert status["1.1.2"] == "ghost" and status["1.1.3"] == "ghost"


def test_context_links_alone_are_weak_even_above_level_1():
    tree = hand_tree()
    tree.experiences[0].links["e0.i01"]["1.3.1"] = 3  # a hand edit: counted as 1
    tree.experiences[0].links["e0.i01"]["1.1.2"] = 3
    status = leaf_status(tree, hand_items())
    assert status["2.1.1"] == "weak" and status["1.3.1"] == "weak" and status["1.1.2"] == "weak"


def test_outside_and_confirmed_prove():
    tree = hand_tree()
    assert leaf_status(tree, hand_items())["2.2.2"] == "proven"
    next(x for x in tree.nodes if x.id == "1.2.1").confirmed = True
    assert leaf_status(tree, hand_items())["1.2.1"] == "proven"


def test_parents_take_the_best_childs_status():
    status = leaf_status(hand_tree(), hand_items())
    assert (status["1.1"], status["1.2"], status["1.3"], status["1"]) == ("proven", "weak", "weak", "proven")
    assert (status["2.1"], status["2.2"], status["2"]) == ("weak", "proven", "proven")


# --- propose_outside -------------------------------------------------------------------------
def test_propose_outside_names_the_source():
    nodes = [
        n("1", "Languages"),
        n("1.1", "Spoken"),
        n("1.1.1", "English"),
        n("1.1.2", "Italian"),
        n("2", "Cloud"),
        n("2.1", "Platforms"),
        n("2.1.1", "AWS", aliases=["Amazon Web Services"]),
        n("2.1.2", "Helm"),
        n("2.1.3", "Terraform"),
        n("2.1.4", "Go"),
        n("2.1.5", "Azure"),
    ]
    found = propose_outside(
        nodes,
        skills=["Helm", "IaC(Terraform)", "always ready to go"],
        certifications=["AWS Cloud Practitioner"],
        languages="English and Chinese: full professional working proficiency.",
    )
    assert found == {
        "1.1.1": "languages",
        "2.1.1": "certification: AWS Cloud Practitioner",
        "2.1.2": "skills list",
        "2.1.3": "skills list",
    }


# --- checks ------------------------------------------------------------------------------------
def test_checks_on_a_hand_made_tree(monkeypatch):
    tree = hand_tree()
    nodes = {x.id: x for x in tree.nodes}
    nodes["1.3"].name = "Linux and Windows administration"
    nodes["2.1"].name = "CI/CD"  # a slash term, not a compound
    nodes["1.1.2"].aliases = ["K8s"]  # also on Kubernetes
    nodes["1.1"].counts_as = ["Docker"]  # a child in its parent's counts-as: redundant
    nodes["1"].aliases = ["Terraform"]  # a descendant's name as an alias: wrong
    found = checks(tree, hand_items(), MARKET)
    assert found["compound_names"] == ["1.3"]
    assert found["two_nodes"] == [
        {"term": "k8s", "nodes": ["1.1.1", "1.1.2"]},
        {"term": "terraform", "nodes": ["1", "1.2.1"]},
    ]
    assert [t["term"] for t in found["unasked"]] == ["DevOps"]  # Scrum is a counts-as term; Fortran is rare
    assert found["unlinked_items"] == ["e1.i03"]
    assert found["broad"] == []
    assert found["ghosts_expected"] == ["1.1.2"]
    assert found["ghosts_named"] == [{"leaf": "1.1.3", "items": ["e0.i03"]}]
    assert found["weak"] == ["1.2.1", "1.3.1", "1.3.2", "2.1.1"]
    assert found["market_changed"] == []
    monkeypatch.setattr(tree_store, "BROAD", 1)
    assert checks(tree, hand_items(), MARKET)["broad"] == ["1.1.1", "2.2.1"]


def test_a_changed_market_asks_for_a_reshape():
    market = MARKET.model_copy(update={"corpus_jobs": 2800})
    assert checks(hand_tree(), hand_items(), market)["market_changed"] == [
        "the market list changed since the shape: reshape to use it"
    ]


# --- validate_tree -------------------------------------------------------------------------------
def test_a_fresh_tree_validates_and_survives_its_file():
    tree = hand_tree()
    assert validate_tree(tree, hand_items()) == []
    again = Tree.model_validate_json(tree_dump(tree))
    assert again == tree


def test_validate_reports_a_hand_edited_file():
    tree = hand_tree()
    tree.nodes += [
        n("1.4", "Monitoring"),
        n("3.1.1", "Orphan"),
        n("1.1.1", "Kubernetes again"),
        n("1.1.1.1", "Too deep"),
    ]
    links = tree.experiences[0].links
    links["e9.i01"] = {"1.1.1": 3}
    links["e0.i02"] = {"1.1": 2, "9.9.9": 1, "1.3.2": 5}
    links["e0.i01"]["2.1.1"] = 2
    problems = validate_tree(tree, hand_items())
    for expected in [
        "3.1.1: no parent 3.1",
        "1.1.1: two nodes have this id",
        "1.1.1.1: deeper than three levels",
        "1.4 Monitoring: no children",
        "e0: links e9.i01, which is not one of its items",
        "e0.i02: linked to 1.1, which is not a leaf",
        "e0.i02: linked to 9.9.9, which does not exist",
        "e0.i02: level 5 on 1.3.2 (1-3)",
        "e0.i01: a context item at level 2 on 2.1.1: it counts as 1",
    ]:
        assert expected in problems


def test_validate_reports_items_changed_since_they_were_attached():
    items = hand_items()
    tree = hand_tree(items)
    items.items[4].text = "Wrote Terraform and Pulumi modules."
    items.experiences.append(
        ExperienceItems(id=2, sha256="", months=1, weights=[], context=[], stories=[], coverage=1.0)
    )
    assert validate_tree(tree, items) == [
        "e2: not attached",
        "e1: its items changed since they were attached: re-attach it",
    ]


def test_items_sha256_is_stable_and_follows_the_text():
    items = hand_items()
    assert items_sha256(items, 0) == items_sha256(hand_items(), 0)
    before = items_sha256(items, 1)
    items.items[5].tags = ["other"]  # tags are not what the attach call sees
    assert items_sha256(items, 1) == before
    items.items[5].text += " Twice."
    assert items_sha256(items, 1) != before
    assert items_sha256(items, 0) == items_sha256(hand_items(), 0)


# --- the view ---------------------------------------------------------------------------------------
def test_the_view_puts_the_checks_first_and_shows_what_a_weak_leaf_rests_on():
    page = tree_markdown(hand_tree(), hand_items(), MARKET)
    assert page.index("## To check") < page.index("## The tree")
    assert "2 disciplines, 5 competences, 9 leaves: 3 proven, 4 weak, 2 ghost." in page
    weak = page.split("### Weak leaves")[1].split("###")[0]
    assert "- **1.2.1 Terraform**" in weak
    assert "  - level 2: e1.i01 (action) Wrote Terraform modules for the staging stack." in weak
    ghosts = page.split("### Ghosts an item may prove")[1].split("###")[0]
    assert "- **1.1.2 OpenShift** (the shape expected it proven)" in ghosts
    assert "  - named in e0.i03 (action) Deployed the services on Kubernetes." in ghosts
    assert "- 2.2.2 English: languages" in page
    assert "- asked, no node carries it: DevOps (2 jobs, asked 3.0%, in 13.4% of JDs)" in page
    assert "- no leaf proves e1.i03 (action) Answered the phone." in page
    assert "  - 1.1.1 Kubernetes · proven _(also: K8s)_ · 9 jobs, asked 5.0%, in 20.0% of JDs" in page
    assert "  - 2.1.1 Agile · WEAK _(counts as: Scrum, Kanban)_" in page
    assert "    - 3 · e0.i03 (action) Deployed the services on Kubernetes." in page


# --- the replies ------------------------------------------------------------------------------------
def test_parse_shape_sorts_parents_first_and_refuses_structural_faults():
    nodes = parse_shape(
        {
            "nodes": [
                {"id": "1.1", "name": "B"},
                {"id": 1, "name": "A", "proven": True},
                {"id": "1.1.1", "name": "C", "aliases": ["c"], "counts_as": ["d"]},
            ]
        }
    )
    assert [(x.id, x.shape_proven) for x in nodes] == [("1", True), ("1.1", False), ("1.1.1", False)]
    with pytest.raises(ValueError, match="1.1: two nodes have this id"):
        parse_shape({"nodes": [{"id": "1", "name": "A"}, {"id": "1.1", "name": "B"}, {"id": "1.1", "name": "C"}]})
    with pytest.raises(ValueError, match="2.1: no parent 2"):
        parse_shape({"nodes": [{"id": "1", "name": "A"}, {"id": "2.1", "name": "B"}]})
    with pytest.raises(ValueError, match="deeper than three levels"):
        parse_shape({"nodes": [{"id": "1", "name": "A"}, {"id": "1.1.1.1", "name": "B"}]})
    with pytest.raises(ValueError, match="1.1 B: no children"):
        parse_shape(
            {
                "nodes": [
                    {"id": "1", "name": "A"},
                    {"id": "1.1", "name": "B"},
                    {"id": "1.2", "name": "C"},
                    {"id": "1.2.1", "name": "D"},
                ]
            }
        )
    with pytest.raises(ValueError, match="nodes"):
        parse_shape({"tree": []})


def test_build_links_drops_what_it_cannot_use_and_clamps_context():
    block = build_links(
        {
            "e0.i01": {"2.1.1": 3},
            "e0.i02": {"1.3.2": 2, "1.3": 2, "7.7.7": 1, "1.3.1": 4},
            "e1.i01": {"1.2.1": 3},
            "e0.i03": "1.1.1",
        },
        NODES,
        hand_items(),
        0,
    )
    assert block.links == {"e0.i01": {"2.1.1": 1}, "e0.i02": {"1.3.2": 2}}
    assert [(d["item"], d["leaf"], d["reason"]) for d in block.dropped] == [
        ("e0.i02", "1.3", "not a leaf"),
        ("e0.i02", "7.7.7", "unknown node"),
        ("e0.i02", "1.3.1", "bad level"),
        ("e1.i01", "1.2.1", "not an item of this experience"),
        ("e0.i03", None, "not an object of leaves"),
    ]


# --- the graph ----------------------------------------------------------------------------------------
#: The example's tree: every action item proves 1.1.1 at level 3, context touches 2.1.1 (asked at 2).
SHAPE = {
    "nodes": [
        {"id": x.id, "name": x.name, "aliases": x.aliases, "counts_as": x.counts_as, "proven": x.shape_proven}
        for x in NODES
    ]
}


#: The hand market without DevOps, which SHAPE does not carry: a first shape reply goes through.
GRAPH_MARKET = MARKET.model_copy(update={"terms": [t for t in MARKET.terms if t.term != "DevOps"]})


def item_lines(body: dict) -> list:
    return user_text(body).split("ITEMS:\n", 1)[1].splitlines()


def is_shape(body: dict) -> bool:
    return "MARKET TERMS" in user_text(body)


def answer(body: dict) -> str:
    """Shape: SHAPE. Attach: each action to Kubernetes at 3, each context item to Agile at 2."""
    if is_shape(body):
        return json.dumps(SHAPE)
    links = {}
    for line in item_lines(body):
        iid, kind = re.match(r"(\S+) \[(\w+)", line).groups()
        links[iid] = {"1.1.1": 3} if kind != "context" else {"2.1.1": 2}
    return json.dumps({"attach": links})


def fake(*replies) -> FakeLLM:
    llm = FakeLLM(roles=ROLES)
    llm["compile"].replies = list(replies) or [answer]
    return llm


def builder(llm) -> TreeBuilder:
    return TreeBuilder(
        llm=llm,
        shape_prompt=(resources_dir() / "sys_prompt_tree_shape.txt").read_text(encoding="utf-8"),
        attach_prompt=(resources_dir() / "sys_prompt_tree_attach.txt").read_text(encoding="utf-8"),
    )


def build(llm, items, **kwargs) -> Tree:
    return builder(llm).build(items, GRAPH_MARKET, **kwargs)


def attach_calls(llm) -> list:
    return [c for c in llm["compile"].calls if not is_shape(c)]


def copy(items: Items) -> Items:
    return Items.model_validate_json(items.model_dump_json())


def test_first_build_one_shape_call_then_one_attach_call_per_experience(example_items, profile, candidate_data):
    llm = fake()
    tree = build(
        llm,
        example_items,
        skills=profile["skills"],
        certifications=[c["name"] for c in profile["certifications"]],
        languages=candidate_data["languages"],
    )
    calls = llm["compile"].calls
    assert is_shape(calls[0]) and not any(is_shape(c) for c in calls[1:])
    assert len(calls) == 1 + len(example_items.experiences) == 6
    shape_text = user_text(calls[0])
    assert "FIXED SECTIONS:\nSkills list: " + "; ".join(profile["skills"]) in shape_text
    assert "Kubernetes | jobs 9 | asked 5.0% | share 20.0%" in shape_text
    assert all(i.text in shape_text for i in example_items.items)
    assert validate_tree(tree, example_items) == []
    outside = {x.id: x.outside for x in tree.nodes if x.outside}
    assert outside == {"1.1.1": "skills list", "1.2.1": "skills list", "2.2.2": "languages"}


def test_each_attach_call_sees_the_whole_tree_and_only_its_own_items(example_items):
    llm = fake()
    build(llm, example_items)
    tree_text = attach_input(parse_shape(SHAPE), example_items, 0).split("\n\nITEMS:")[0]
    seen = set()
    for call in attach_calls(llm):
        assert tree_text in user_text(call)
        ids = [line.split()[0] for line in item_lines(call)]
        e = int(ids[0][1:].split(".")[0])
        assert ids == [i.id for i in example_items.items if i.experience == e]
        assert any("[context" in line for line in item_lines(call))
        seen.add(e)
    assert seen == {0, 1, 2, 3, 4}


def test_answers_land_on_their_own_experience_whatever_order_the_calls_finish(example_items):
    def slow_first(body):
        if not is_shape(body):
            e = int(item_lines(body)[0].split()[0][1:].split(".")[0])
            time.sleep(0.05 * (5 - e))
        return answer(body)

    tree = build(fake(slow_first), example_items)
    assert [b.experience for b in tree.experiences] == [0, 1, 2, 3, 4]
    for block in tree.experiences:
        assert all(iid.startswith(f"e{block.experience}.") for iid in block.links)
        context = [i.id for i in example_items.items if i.experience == block.experience and i.type == "context"]
        assert all(block.links[iid] == {"2.1.1": 1} for iid in context)  # asked at 2, clamped
    assert validate_tree(tree, example_items) == []


def test_a_failed_call_fails_the_build_with_stage_skill_tree(example_items):
    with pytest.raises(ModelOutputError) as caught:
        build(fake(lambda body: answer(body) if is_shape(body) else "not json"), example_items)
    assert caught.value.stage == "Skill tree"


def test_every_call_is_counted(example_items):
    b = builder(fake())
    b.build(example_items, GRAPH_MARKET)
    assert b.usage.calls == 6
    assert (b.usage.input_tokens, b.usage.thinking_tokens, b.usage.output_tokens) == (66, 12, 120)


def test_a_shape_reply_with_an_id_twice_is_corrected_once(example_items):
    twice = json.dumps({"nodes": SHAPE["nodes"] + [SHAPE["nodes"][2]]})
    llm = fake(twice, answer)
    b = builder(llm)
    tree = b.build(example_items, GRAPH_MARKET)
    shapes = [c for c in llm["compile"].calls if is_shape(c)]
    assert len(shapes) == 2 and len(tree.nodes) == len(NODES)
    assert "Your previous reply was rejected:\n- 1.1.1: two nodes have this id" in "\n".join(
        m["content"] for m in shapes[1]["messages"]
    )
    assert b.usage.calls == 7


def test_a_shape_reply_that_stays_wrong_fails(example_items):
    twice = json.dumps({"nodes": SHAPE["nodes"] + [SHAPE["nodes"][2]]})
    llm = fake(twice)
    with pytest.raises(ModelOutputError) as caught:
        build(llm, example_items)
    assert caught.value.stage == "Skill tree" and len(llm["compile"].calls) == 2
    assert "two nodes have this id" in str(caught.value)


def test_a_bad_link_is_dropped_with_its_reason(example_items):
    def bad(body):
        if is_shape(body):
            return answer(body)
        first = item_lines(body)[0].split()[0]
        return json.dumps({"attach": {first: {"1.1": 3, "1.1.1": 3}, "e9.i99": {"1.1.1": 3}}})

    tree = build(fake(bad), example_items)
    block = tree.experiences[0]
    assert list(block.links.values()) == [{"1.1.1": 1}]  # the first item is context: clamped
    assert [d["reason"] for d in block.dropped] == ["not a leaf", "not an item of this experience"]
    assert "dropped link e9.i99 -> 1.1.1 (not an item of this experience)" in tree_markdown(
        tree, example_items, GRAPH_MARKET
    )


def test_unchanged_items_make_no_call(example_items):
    first = build(fake(), example_items)
    llm = fake()
    again = build(llm, example_items, previous=first)
    assert llm["compile"].calls == []
    assert again == first


def test_force_attaches_an_unchanged_experience_again(example_items):
    first = build(fake(), example_items)
    llm = fake()
    build(llm, example_items, previous=first, force=[3])
    assert len(llm["compile"].calls) == 1
    assert item_lines(llm["compile"].calls[0])[0].startswith("e3.")


def test_one_changed_experience_one_attach_call_and_its_hand_edits_listed(example_items):
    first = Tree.model_validate_json(tree_dump(build(fake(), example_items)))
    first.experiences[3].edited = True
    first.experiences[3].links[next(iter(first.experiences[3].links))] = {"1.2.1": 2}  # by hand
    first.experiences[1].edited = True
    first.experiences[1].links.popitem()
    items = copy(example_items)
    next(i for i in items.items if i.experience == 3 and i.type == "action").text += " Twice."
    llm = fake()
    again = build(llm, items, previous=first)
    assert len(llm["compile"].calls) == 1
    assert [b for k, b in enumerate(again.experiences) if k != 3] == [
        b for k, b in enumerate(first.experiences) if k != 3
    ]
    block = again.experiences[3]
    assert {"item": next(iter(first.experiences[3].links)), "leaf": "1.2.1", "level": 2} in block.previous
    assert not block.edited and validate_tree(again, items) == []
    assert "hand-edited link before the re-attach" in tree_markdown(again, items, GRAPH_MARKET)


def test_reshape_one_shape_call_and_every_attach_call(example_items):
    first = build(fake(), example_items)
    llm = fake()
    build(llm, example_items, previous=first, reshape=True)
    assert len(llm["compile"].calls) == 6 and is_shape(llm["compile"].calls[0])


def correction(llm) -> str:
    shapes = [c for c in llm["compile"].calls if is_shape(c)]
    return shapes[1]["messages"][-1]["content"] if len(shapes) > 1 else ""


def test_compound_names_and_asked_terms_no_node_carries_go_back_once(example_items):
    """The second reply is taken even though it still has them: only structural faults fail it."""
    compound = {"nodes": [dict(x, name="Delivery and methods") if x["id"] == "2.1" else x for x in SHAPE["nodes"]]}
    llm = fake(json.dumps(compound), answer)
    tree = builder(llm).build(example_items, MARKET)  # MARKET asks DevOps, which SHAPE lacks
    assert len([c for c in llm["compile"].calls if is_shape(c)]) == 2
    text = correction(llm)
    assert "Compound names" in text and "- 2.1 Delivery and methods" in text
    assert "- DevOps | jobs 2 | asked 3.0% | share 13.4%" in text
    assert "Skip degrees, job titles, language levels and travel" in text
    assert "Kubernetes |" not in text and "Fortran" not in text
    assert len(tree.nodes) == len(NODES)


def test_structural_and_naming_faults_share_the_one_correction(example_items):
    twice = json.dumps({"nodes": SHAPE["nodes"] + [SHAPE["nodes"][2]]})
    llm = fake(twice, answer)
    builder(llm).build(example_items, MARKET)
    assert len([c for c in llm["compile"].calls if is_shape(c)]) == 2
    text = correction(llm)
    assert "- 1.1.1: two nodes have this id" in text and "- DevOps |" in text


def test_a_shape_reply_with_no_fault_makes_no_correction(example_items):
    llm = fake()
    builder(llm).build(example_items, GRAPH_MARKET)
    assert len([c for c in llm["compile"].calls if is_shape(c)]) == 1


def test_the_profile_join_adds_its_own_terms_with_no_counts_after_the_corpus_ones():
    items = hand_items()
    for item in items.items:
        item.tech = []
    items.items[0].tech = ["kubernetes", "Zabbix", "zabbix"]
    joined = tree_store.with_profile(MARKET, items, ["Zabbix", "Alibaba  Cloud", "Scrum"])
    assert [t.term for t in joined.terms] == ["Kubernetes", "DevOps", "Scrum", "Fortran", "Alibaba Cloud", "Zabbix"]
    assert joined.terms[0] == MARKET.terms[0], "a term the corpus part holds keeps its spelling and counts"
    assert (joined.terms[-1].jobs, joined.terms[-1].asked, joined.terms[-1].share) == (0, 0.0, 0.0)
    assert joined.requirements == MARKET.requirements and MARKET.terms == joined.terms[:4]


def test_the_shipped_market_holds_no_employer_named_term():
    """The build tool drops terms spelled as a corpus company name; a few it dropped, as a guard."""
    shipped = Market.model_validate_json((resources_dir() / "market.json").read_text(encoding="utf-8"))
    terms = {t.term for t in shipped.terms}
    assert len(terms) > 500 and not terms & {"SAP", "Salesforce", "Cisco", "ServiceNow"}
