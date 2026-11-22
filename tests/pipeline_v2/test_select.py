"""The selection (S6): the narrative score, the greedy, the refill pass, and S0's helpers.

No key, no network, no model: everything runs on a hand-made store of three experiences, two
stories each, one leaf per experience.
"""

from __future__ import annotations

import dataclasses
from datetime import date

import pytest

from pipeline_v2 import select as sel
from pipeline_v2.allocate import Allocation, Requirement
from pipeline_v2.pool_store import ExperiencePool, Frame, Line, Pool, Rung, Skill, StoryPool
from pipeline_v2.store import DutyWeight, ExperienceItems, Item, Items, Story
from pipeline_v2.terms import count_term
from pipeline_v2.tree_store import ExperienceLinks, Node, Tree

#: experience -> (months, its leaf, the term its texts name)
EXPERIENCES = {0: (24, "1.1.1", "Kubernetes"), 1: (36, "1.1.2", "Terraform"), 2: (60, "1.1.3", "Java")}
SIZES = {1: 80, 2: 150, 3: 250}


def text(term: str, n: int) -> str:
    return (f"Worked with {term} " + "on the platform " * 20)[: n - 1] + "."


def store():
    """e0 is the most recent, e2 the earliest. Each has one context item and two stories of an
    action and a result; story 1 is duty 0 (weight 3), story 2 duty 1 (weight 1). e0 also has a
    duty 2 with no item."""
    items, exps, links, blocks = [], [], [], []
    for e, (months, leaf, term) in EXPERIENCES.items():
        items.append(Item(id=f"e{e}.i01", experience=e, duty=-1, type="context", text="x", source=["x"]))
        stories, stories_pool, linked = [], [], {}
        for s in (1, 2):
            act, res = f"e{e}.i{2 * s:02d}", f"e{e}.i{2 * s + 1:02d}"
            sid = f"e{e}.s0{s}"
            items += [
                Item(id=act, experience=e, duty=s - 1, type="action", text="x", source=["x"], agency="did", story=sid),
                Item(id=res, experience=e, duty=s - 1, type="result", text="x", source=["x"], story=sid),
            ]
            linked.update({act: {leaf: 3}, res: {leaf: 2}})
            stories.append(Story(id=sid, experience=e, title="t", items=[act, res], anchor=[act]))
            ladder = [
                Rung(
                    id=f"{sid}.r{r}.{w}",
                    rung=r,
                    wording=w,
                    text=text(term, SIZES[r]),
                    uses=[act, res],
                    covers=[act, res] if r > 1 else [act],
                )
                for r in (1, 2, 3)
                for w in "ab"
            ]
            stories_pool.append(StoryPool(story=sid, ladder=ladder))
        weights = [DutyWeight(duty=0, weight=3, why="x"), DutyWeight(duty=1, weight=1, why="x")]
        if e == 0:
            weights.append(DutyWeight(duty=2, weight=2, why="no item"))
        exps.append(
            ExperienceItems(
                id=e, sha256="", months=months, coverage=1.0, context=[f"e{e}.i01"], weights=weights, stories=stories
            )
        )
        links.append(ExperienceLinks(experience=e, items_sha256="", links=linked))
        blocks.append(
            ExperiencePool(
                experience=e,
                items_sha256="",
                role_summary=Line(text=text(term, 150), uses=[f"e{e}.i01"]),
                compact=Line(text=text(term, 90), uses=[f"e{e}.i02"]),
                stories=stories_pool,
            )
        )
    nodes = [Node(id="1", name="Platforms"), Node(id="1.1", name="Tools")] + [
        Node(id=leaf, name=term) for _, leaf, term in EXPERIENCES.values()
    ]
    pool = Pool(
        level_sha256="",
        experiences=blocks,
        skills=[
            Skill(id=f"k0{k}", text=term, nodes=[leaf]) for k, (_, leaf, term) in enumerate(EXPERIENCES.values(), 1)
        ],
        frames=[Frame(id="f1", family="Platform", text="<<job_title>> <<keywords>> <<sentence>>", uses=["e1.i02"])],
    )
    allocation = Allocation(
        title="Platform Engineer",
        requirements=[
            Requirement(id=1, name="Terraform", importance=100, keywords=["Terraform"], node="1.1.2", relation="same"),
            Requirement(id=2, name="Kubernetes", importance=50, keywords=["Kubernetes"], node="1.1.1", relation="same"),
        ],
    )
    return (
        Items(profile_sha256="", experiences=exps, items=items),
        Tree(market_sha256="", nodes=nodes, experiences=links),
        pool,
        allocation,
    )


@pytest.fixture
def ctx():
    items, tree, pool, allocation = store()
    return sel.context(items, tree, pool, allocation, ["Platform Engineer"], {})


@pytest.fixture
def tight(monkeypatch):
    """A page of about 27 lines for the experiences: not everything fits."""
    monkeypatch.setattr(sel, "CAPACITY_PT", 300.0)


# --- the narrative score ----------------------------------------------------------------------------
def test_duties_share_the_years_by_1_3_9_points():
    items, *_ = store()
    got = sel.duty_years(items)
    for e, (months, _, _) in EXPERIENCES.items():
        mine = {d: y for (x, d), (y, _) in got.items() if x == e}
        assert set(mine) == {0, 1}  # e0's duty 2 has no action or result item: left out
        assert sum(mine.values()) == pytest.approx(months / 12)
        assert mine[0] == pytest.approx(9 * mine[1])  # weight 3 against weight 1
    assert got[(0, 0)][1] == {"e0.i02", "e0.i03"}


def test_a_job_on_the_page_tells_half_its_years_and_its_bullets_the_rest(ctx):
    career = 120 / 12
    half = sel.PRESENCE * 2  # e0 on the page: half its 2 years
    # e0.s01 at its top rung covers both items of e0's duty 0 in full: 2 years x 9/10
    draft = {"forms": {0: "full"}, "slots": {"e0.s01": 3}}
    assert sel.narrative(draft, ctx) == pytest.approx((half + 0.5 * 2 * 0.9) / career)
    # a rung below tells them at 0.85
    draft = {"forms": {0: "full"}, "slots": {"e0.s01": 2}}
    assert sel.narrative(draft, ctx) == pytest.approx((half + 0.5 * 2 * 0.9 * 0.85) / career)
    # rung 1 covers the action only, at 0.6
    draft = {"forms": {0: "full"}, "slots": {"e0.s01": 1}}
    assert sel.narrative(draft, ctx) == pytest.approx((half + 0.5 * 2 * 0.9 * 0.6 / 2) / career)
    # a compact line tells no duty, though it uses e2's duty 0's action: only the presence
    draft = {"forms": {2: "compact"}, "slots": {}}
    assert sel.narrative(draft, ctx) == pytest.approx(0.5 * 5 / career)


def test_the_depth_counts_from_the_storys_own_top_rung(ctx):
    # a story whose longest bullet is 2 lines tells everything at rung 2
    short = dataclasses.replace(
        ctx, ladder={**ctx.ladder, "e0.s01": {1: ctx.ladder["e0.s01"][1], 2: ctx.ladder["e0.s01"][2]}}
    )
    draft = {"forms": {0: "full"}, "slots": {"e0.s01": 2}}
    assert sel.told(draft, short) == {"e0.i02": 1.0, "e0.i03": 1.0}
    assert sel.told(draft, ctx) == {"e0.i02": 0.85, "e0.i03": 0.85}


def test_older_months_weigh_less():
    experiences = [
        {"id": 0, "dates": "November 2026 -- present"},  # this month: age 0
        {"id": 1, "dates": "November 2023 -- November 2023"},  # one month, a half-life old: half
        {"id": 2, "dates": "November 2017 -- November 2023"},  # 9 to 3 years old
        {"id": 3, "dates": "sometime"},
    ]
    assert sel.HALF_LIFE_YEARS == 3
    w = sel.recency(experiences, date(2026, 11, 19))
    assert w[0] == 1.0 and w[1] == pytest.approx(0.5) and w[3] == 1.0
    assert 0.125 < w[2] < 0.5  # the mean over its 73 months
    items, tree, pool, allocation = store()
    ctx = sel.context(items, tree, pool, allocation, ["Platform Engineer"], {2: 0.5})
    # e2's 5 years count 2.5, in what it tells and in the career (2 + 3 + 2.5)
    draft = {"forms": {2: "compact"}, "slots": {}}
    assert sel.narrative(draft, ctx) == pytest.approx(0.5 * 2.5 / 7.5)


# --- the greedy and the refill pass -----------------------------------------------------------------
def test_the_plan_fits_the_budget_and_shows_the_latest_job_in_full(ctx, tight):
    plan = sel.select(ctx)
    shown = {x.experience: x for x in plan.experiences}
    assert shown[0].form == "full"
    assert plan.lines <= plan.budget_lines
    assert len(shown) < 3 or "compact" in [x.form for x in plan.experiences]  # not everything fits
    # S0's fix: the page is filled even by moves that lose, so no line is left
    assert plan.budget_lines - plan.lines < 1
    # slots in profile order: story ids follow the duties
    for x in plan.experiences:
        assert [s.story for s in x.slots] == sorted(s.story for s in x.slots)


def test_candidates_score_both_ways_and_composed_keeps_the_given_order(ctx):
    slots = {"e0.s01": 2, "e2.s01": 1, "e2.s02": 1}
    plan = sel.plan_of({"forms": {0: "full", 1: "compact", 2: "full"}, "slots": slots}, ctx, 0.0)
    options = sel.candidates(plan, ctx)
    assert set(options) == {0, 2}  # full experiences only
    e0 = {c.id: c for c in options[0]}
    assert set(e0) == {"e0.s01.r2", "e0.s02.r1", "e0.s02.r2", "e0.s02.r3"}  # a picked story at its rung only
    assert e0["e0.s01.r2"].picked and e0["e0.s01.r2"].score > 0  # what the objective loses without it
    assert not e0["e0.s02.r1"].picked and e0["e0.s02.r1"].bullets == ("e0.s02.r1.a", "e0.s02.r1.b")
    assert [e0[f"e0.s02.r{r}"].lines for r in (1, 2, 3)] == [1, 2, 3]
    new = sel.composed(plan, ctx, {0: [("e0.s02", 1), ("e0.s01", 2)]})
    assert [s.story for s in new.experiences[0].slots] == ["e0.s02", "e0.s01"]
    assert list(sel.draft_of(new)["slots"].items()) == [("e0.s02", 1), ("e0.s01", 2), ("e2.s01", 1), ("e2.s02", 1)]


def test_on_a_roomy_page_everything_is_shown_in_full(ctx):
    plan = sel.select(ctx)
    assert [(x.experience, x.form) for x in plan.experiences] == [(0, "full"), (1, "full"), (2, "full")]
    assert all(s.rung == 3 for x in plan.experiences for s in x.slots)
    assert plan.headline == "Platform Engineer"
    assert plan.skills[0] == "Terraform"  # the most literal score first


def test_the_refill_pass_never_lowers_the_objective(ctx, tight):
    first = sel.greedy(ctx, {"forms": {0: "compact", 2: "compact"}, "slots": {}})
    assert sel.objective(sel.refill(ctx, first), ctx) >= sel.objective(first, ctx)
    # a poor start: the least relevant stories at rung 1
    poor = {"forms": {0: "full", 2: "full"}, "slots": {"e0.s02": 1, "e0.s01": 1, "e2.s01": 1, "e2.s02": 1}}
    assert sel.objective(sel.refill(ctx, poor), ctx) >= sel.objective(poor, ctx)
    plan = sel.select(ctx)
    assert plan.scores.objective >= plan.greedy_objective


def test_the_latest_job_keeps_two_bullets_when_nothing_matches_it(monkeypatch):
    monkeypatch.setattr(sel, "CAPACITY_PT", 200.0)
    items, tree, pool, allocation = store()
    allocation = allocation.model_copy(update={"requirements": allocation.requirements[:1]})  # Terraform only: e1
    ctx = sel.context(items, tree, pool, allocation, ["Platform Engineer"], {})
    draft = sel.draft_of(sel.select(ctx))
    assert draft["forms"][0] == "full" and sel.floor_kept(draft, ctx)
    while (draft := sel.fit(draft, ctx)) is not None:  # the fit steps keep it too
        assert sel.floor_kept(draft, ctx)


def test_a_job_that_earns_too_little_is_left_out(tight):
    items, tree, pool, allocation = store()
    ctx = sel.context(items, tree, pool, allocation, ["Platform Engineer"], {2: 0.001})
    assert 2 not in [x.experience for x in sel.select(ctx).experiences]


# --- S0's asserts ------------------------------------------------------------------------------------
def test_count_term_as_has_term():
    assert count_term("AI", "ai and AI, Ai") == 2
    assert count_term("Kubernetes", "kubernetes K8s Kubernetes") == 2


def test_literal_score():
    def r(importance, keywords):
        return Requirement(id=1, name="x", importance=importance, keywords=keywords)

    reqs = [r(60, ["Kubernetes", "K8s"]), r(40, ["Terraform"]), r(90, [])]
    assert sel.literal_score("Kubernetes", reqs) == pytest.approx(60 / (100 * 4 / 3))
    assert sel.literal_score("Kubernetes K8s Terraform", reqs) == pytest.approx((60 * 4 / 3 + 40) / (100 * 4 / 3))


def test_levels_reach_every_ancestor():
    assert sel.levels_at({"1.1.1": 2}) == {"1.1.1": 2, "1.1": 2, "1": 2}
