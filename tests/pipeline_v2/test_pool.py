"""The pool (S3): derived values, checks, validation, the view, the file, and the writer's two fan-outs;
and its review (S4).

No key, no network, no terminal: the writer runs on :class:`FakeLLM`, whose fake transport sits
inside a real ``ModelSelector``. Everything runs on the hand-made store of ``conftest.py`` with one
long story (``long_items``, ``long_tree``, ``long_profile``).
"""

from __future__ import annotations

import json
import time
from datetime import date

import pytest

from common.errors import ModelOutputError
from pipeline_v2 import (
    ROLES,
    Pool,
    PoolReviewer,
    PoolWriter,
    pool_checks,
    pool_dump,
    pool_markdown,
    resources_dir,
    review_markdown,
    validate_pool,
)
from pipeline_v2.pool import batches, json_object
from pipeline_v2.pool_review import dumps
from pipeline_v2.pool_store import (
    Bullet,
    ExperiencePool,
    Frame,
    Group,
    Line,
    Rung,
    Skill,
    StoryPool,
    career_years,
    frame_numbers,
    item_leaves,
    item_weights,
    level_sha256,
    lines,
    numbers,
    piece_nodes,
    piece_terms,
    piece_weight,
    shown_sha256,
    story_work,
)
from pipeline_v2.store import Items

from pipeline_helpers import FakeLLM, tool_completion, user_text

TODAY = date(2026, 11, 19)


def sized(first: str, n: int) -> str:
    """A text of exactly ``n`` characters starting with ``first``."""
    text = (first + " the platform work" + " and more of it" * 30)[:n]
    return text[:-1] + "." if text.endswith(" ") else text[:-1] + "x"


# --- a clean hand-made pool ----------------------------------------------------------------------------
def rung(sid: str, r: int, w: str, uses, covers, assumes=(), first=None) -> Rung:
    size = {1: 80, 2: 150, 3: 250}[r]
    return Rung(
        id=f"{sid}.r{r}.{w}",
        rung=r,
        wording=w,
        text=sized(first or ("Moved" if w == "a" else "I moved"), size),
        uses=list(uses),
        covers=list(covers),
        assumes=list(assumes),
    )


def ladder(sid: str, uses, covers, assumes=()) -> list:
    return [rung(sid, r, w, uses, covers, assumes) for r in (1, 2, 3) for w in ("a", "b")]


def clean_pool(items: Items, tree) -> Pool:
    s01 = StoryPool(
        story="e0.s01",
        ladder=ladder(
            "e0.s01",
            ["e0.i03", "e0.i04", "e0.i05", "e0.i06", "e0.i07", "e0.i08"],
            ["e0.i04", "e0.i05", "e0.i06", "e0.i07", "e0.i08"],
            ["e0.i01"],
        ),
        groups=[
            Group(
                id="e0.s01.g1",
                bullets=[
                    Bullet(
                        id="e0.s01.g1.1",
                        text=sized("Moved", 250),
                        uses=["e0.i03", "e0.i04", "e0.i05"],
                        covers=["e0.i04", "e0.i05"],
                        assumes=["e0.i01"],
                    ),
                    Bullet(
                        id="e0.s01.g1.2",
                        text=sized("Then", 150),
                        uses=["e0.i06", "e0.i07", "e0.i08"],
                        covers=["e0.i06", "e0.i07", "e0.i08"],
                        assumes=["e0.i04"],
                    ),
                ],
            )
        ],
    )
    s02 = StoryPool(story="e0.s02", ladder=ladder("e0.s02", ["e0.i09", "e0.i10"], ["e0.i09", "e0.i10"]))
    s03 = StoryPool(
        story="e0.s03",
        ladder=[rung("e0.s03", 1, "a", ["e0.i11"], ["e0.i11"]), rung("e0.s03", 1, "b", ["e0.i11"], ["e0.i11"])],
    )
    e0 = ExperiencePool(
        experience=0,
        items_sha256=shown_sha256(items, 0),
        role_summary=Line(
            text="A team of five runs the payments platform of a retail bank.", uses=["e0.i01", "e0.i02"]
        ),
        compact=Line(text="Moved a bank's payments platform to Kubernetes.", uses=["e0.i04", "e0.i01"]),
        stories=[s01, s02, s03],
    )
    e1 = ExperiencePool(
        experience=1,
        items_sha256=shown_sha256(items, 1),
        role_summary=Line(text="An online shop selling garden tools.", uses=["e1.i01"]),
        compact=Line(text="Built the checkout pages of an online shop in Java.", uses=["e1.i02"]),
        stories=[StoryPool(story="e1.s01", ladder=ladder("e1.s01", ["e1.i02", "e1.i03"], ["e1.i02", "e1.i03"]))],
    )
    names = [("Kubernetes", "1.1.1"), ("Docker", "1.1.2"), ("Terraform", "1.2.1"), ("K8s operations", "1.1.1")]
    skills = [Skill(id=f"k{k:02d}", text=names[k % 4][0], nodes=[names[k % 4][1]]) for k in range(1, 26)]
    frames = [
        Frame(
            id=f"f{k}",
            family="Platform engineering",
            text="<<job_title>> with 11 years of work since 2015 on bank and shop platforms. "
            + sized("Moved", 150)
            + " Strong in <<keywords>>. <<sentence>>",
            uses=["e0.i04"],
        )
        for k in range(1, 5)
    ]
    return Pool(level_sha256=level_sha256(items, tree), experiences=[e0, e1], skills=skills, frames=frames)


def failing(found: dict) -> dict:
    return {k: v for k, v in found.items() if v}


def details(found: dict, check: str) -> list:
    return [f"{e['id']}: {e['detail']}" for e in found[check]]


# --- derived values ------------------------------------------------------------------------------------
def test_lines_of_a_bullet_and_of_a_role_summary():
    assert (lines("x" * 96), lines("x" * 97), lines("x" * 192), lines("x" * 193)) == (1, 2, 2, 3)
    assert lines("x" * 100, 100) == 1 and lines("x" * 101, 100) == 2


def test_nodes_take_the_highest_level_with_context_clamped(long_items, long_tree):
    leaves = item_leaves(long_tree, long_items)
    assert piece_nodes(["e0.i01", "e0.i04", "e0.i05"], leaves) == {"1.1.1": 3, "1.1.2": 1, "2.1.1": 1}
    assert piece_nodes(["e0.i03"], leaves) == {}


def test_terms_are_names_aliases_and_counts_as_terms(long_tree):
    assert piece_terms("Ran K8s clusters with Scrum and Terraform; nothing dockerized.", long_tree.nodes) == [
        "K8s",
        "Scrum",
        "Terraform",
    ]


def test_weight_comes_from_the_covered_items_only(long_items):
    weights = item_weights(long_items)
    assert "e0.i01" not in weights  # an item of the role summary
    assert piece_weight(["e0.i11"], weights) == 1
    assert piece_weight(["e0.i11", "e0.i09"], weights) == 2
    assert piece_weight([], weights) == 0


def test_numbers_in_digits_and_words():
    assert numbers("Cut 3 hours to 40 minutes for Twelve teams and 1,500 users of e1 in S3 24/7") == [
        ("3", "3"),
        ("40", "40"),
        ("Twelve", "12"),
        ("1,500", "1,500"),
        ("24", "24"),
        ("7", "7"),
    ]


def test_career_years_count_present_to_today(long_profile):
    assert career_years(long_profile, TODAY) == 11  # March 2015 to November 2026: 141 months
    assert career_years(long_profile, date(2027, 2, 19)) == 12
    long_profile["experiences"][0]["dates"] = "sometime"
    assert career_years(long_profile, TODAY) == 4  # the readable one: 58 months


def test_story_work_counts_actions_and_results(long_items):
    assert story_work(long_items) == {"e0.s01": 5, "e0.s02": 2, "e0.s03": 1, "e1.s01": 2}


# --- checks --------------------------------------------------------------------------------------------
def test_a_clean_pool_passes_every_check(long_items, long_tree, long_profile):
    pool = clean_pool(long_items, long_tree)
    assert failing(pool_checks(pool, long_items, long_tree, long_profile, TODAY)) == {}
    assert validate_pool(pool, long_items, long_tree) == []


def test_length_lines_and_a_missing_rung_1(long_items, long_tree, long_profile):
    pool = clean_pool(long_items, long_tree)
    s02 = pool.experiences[0].stories[1]
    s02.ladder[0].text = sized("Moved", 99)  # r1.a over 96
    s02.ladder[2].text = sized("Moved", 90)  # r2.a fills one line
    pool.experiences[0].stories[2].ladder = []  # e0.s03: no rung 1
    pool.skills[0].text = "Kubernetes container orchestration"  # 34 characters, 3 words
    pool.skills[1].text = "Docker on any old laptop"  # 5 words
    found = pool_checks(pool, long_items, long_tree, long_profile, TODAY)
    assert details(found, "length") == [
        "e0.s02.r1.a: 99 characters, over 96",
        "k01: 34 characters, over 30",
        "k02: 5 words, over 4",
    ]
    assert details(found, "lines") == ["e0.s02.r2.a: 90 characters fill 1 line(s), not 2"]
    assert details(found, "rung1") == ["e0.s03: no rung 1"]


def test_items_numbers_and_led(long_items, long_tree, long_profile):
    pool = clean_pool(long_items, long_tree)
    r = pool.experiences[0].stories[1].ladder
    r[0].uses.append("e1.i02")  # another experience's item
    r[1].text = "Found two outages in 3 weeks before the customers did."  # 3 is nowhere; two is in e0.i10
    r[2].text = sized("Headed the monitoring", 150)  # no item of agency led
    pool.experiences[0].stories[0].ladder[0].text = sized("Led the move", 80)  # e0.i04 is led: passes
    pool.experiences[1].role_summary.text = "An online shop selling garden tools since 2015."  # in the header
    found = pool_checks(pool, long_items, long_tree, long_profile, TODAY)
    assert details(found, "foreign_items") == ["e0.s02.r1.a: not an item of this experience: e1.i02"]
    assert details(found, "numbers") == ["e0.s02.r1.b: not in its items or the header: 3"]
    assert details(found, "led") == ['e0.s02.r2.a: "Headed" with no item of agency led']


def test_context_only_covers_and_assumes(long_items, long_tree, long_profile):
    pool = clean_pool(long_items, long_tree)
    s01 = pool.experiences[0].stories[0]
    s01.ladder[0].uses, s01.ladder[0].covers = ["e0.i03", "e0.i04"], ["e0.i04", "e0.i05", "e0.i03"]
    s01.ladder[1].uses, s01.ladder[1].covers = ["e0.i03"], []
    s01.ladder[2].assumes = ["e0.i01", "e0.i03"]  # e0.i03 is used, and the role summary lacks it
    second = s01.groups[0].bullets[1]
    second.assumes = ["e0.i04", "e0.i09"]  # e0.i04 from the first bullet; e0.i09 from nowhere
    found = pool_checks(pool, long_items, long_tree, long_profile, TODAY)
    assert details(found, "context_only") == ["e0.s01.r1.b: uses no action or result item"]
    assert details(found, "covers") == [
        "e0.s01.r1.a: covers what it does not use: e0.i05",
        "e0.s01.r1.a: covers a context item: e0.i03",
    ]
    assert details(found, "assumes") == [
        "e0.s01.r2.a: assumes what it uses: e0.i03",
        "e0.s01.r2.a: assumes what the role summary does not state: e0.i03",
        "e0.s01.g1.2: assumes what the role summary or an earlier bullet does not state: e0.i09",
    ]


def test_anchor_wordings_and_groups(long_items, long_tree, long_profile):
    pool = clean_pool(long_items, long_tree)
    s01 = pool.experiences[0].stories[0]
    s01.ladder[0].uses = ["e0.i05"]  # neither uses nor assumes the anchor e0.i04
    s01.ladder[0].covers = ["e0.i05"]
    s01.ladder[3].text = sized("Moved", 150)  # r2.b opens like r2.a
    s01.groups[0].bullets[0].uses = ["e0.i05", "e0.i06"]  # the group's first bullet: no anchor
    s01.groups[0].bullets[0].covers = ["e0.i05", "e0.i06"]
    s01.groups.append(
        Group(
            id="e0.s01.g2",
            bullets=[Bullet(id="e0.s01.g2.1", text=sized("Wrote", 120), uses=["e0.i04"], covers=["e0.i04"])],
        )
    )
    found = pool_checks(pool, long_items, long_tree, long_profile, TODAY)
    assert details(found, "anchor") == [
        "e0.s01.r1.a: uses or assumes no anchor item (e0.i04)",
        "e0.s01.g1.1: uses or assumes no anchor item (e0.i04)",
    ]
    assert details(found, "wordings") == ['e0.s01.r2: a and b both open with "moved"']
    assert details(found, "groups") == ["e0.s01.g2: 1 bullets, not 2 or 3", "e0.s01.g2: 2 lines, outside 4 to 7"]


def test_a_merged_story_may_have_a_group_of_9_lines(long_items, long_tree, long_profile):
    pool = clean_pool(long_items, long_tree)
    group = pool.experiences[0].stories[0].groups[0]
    group.bullets.append(Bullet(id="e0.s01.g1.3", text=sized("Also", 250), uses=["e0.i08"], covers=["e0.i08"]))
    assert details(pool_checks(pool, long_items, long_tree, long_profile, TODAY), "groups") == [
        "e0.s01.g1: 8 lines, outside 4 to 7"
    ]
    long_items.experiences[0].stories[0].merged = ["e0.s07"]
    assert pool_checks(pool, long_items, long_tree, long_profile, TODAY)["groups"] == []


def test_uncovered_items_role_summary_and_compact(long_items, long_tree, long_profile):
    pool = clean_pool(long_items, long_tree)
    for r in pool.experiences[0].stories[1].ladder:
        r.covers = ["e0.i09"]  # e0.i10 is covered nowhere now
    pool.experiences[0].role_summary.uses = ["e0.i01", "e0.i04"]  # misses e0.i02, uses an action
    pool.experiences[1].compact.uses = ["e1.i01"]  # no action
    found = pool_checks(pool, long_items, long_tree, long_profile, TODAY)
    assert details(found, "uncovered") == ["e0.i10: no bullet or compact line covers it"]
    assert details(found, "role_summary") == [
        "e0.role: misses the experience context e0.i02",
        "e0.role: uses an action or result: e0.i04",
    ]
    assert details(found, "compact") == ["e1.compact: uses no action item"]


def test_skill_phrases_rest_on_proven_leaves_and_name_one(long_items, long_tree, long_profile):
    pool = clean_pool(long_items, long_tree)
    pool.skills[0].nodes = ["2.1.1"]  # weak
    pool.skills[1].nodes = ["1.1"]  # a competence
    pool.skills[2].text, pool.skills[2].nodes = "Scrum", ["2.1.1", "1.1.1"]  # Scrum is a counts-as term
    pool.skills[3].nodes = []
    found = pool_checks(pool, long_items, long_tree, long_profile, TODAY)
    assert details(found, "skills") == [
        "k01: not a proven leaf: 2.1.1 Agile (weak)",
        "k01: holds the name or an alias of none of its nodes",
        "k02: not a proven leaf: 1.1 Containers (proven)",
        "k02: holds the name or an alias of none of its nodes",
        "k03: not a proven leaf: 2.1.1 Agile (weak)",
        "k03: holds the name or an alias of none of its nodes",
        "k04: names no leaf",
    ]
    pool.skills = pool.skills[:24]
    assert details(pool_checks(pool, long_items, long_tree, long_profile, TODAY), "skill_count") == [
        "skills: 24 skill phrases, not 25 to 40"
    ]


def test_frames_slots_length_numbers_and_count(long_items, long_tree, long_profile):
    pool = clean_pool(long_items, long_tree)
    f1, f2, f3 = pool.frames[:3]
    f1.text = f1.text.replace("<<sentence>>", "<<sentence>> <<sentence>>").replace("<<keywords>>", "Kubernetes")
    f2.text = f2.text.replace("Strong in", sized("Strong", 160) + " Strong in")
    f3.text = f3.text.replace("11 years", "twelve years").replace("since 2015", "in three languages in 2014")
    found = pool_checks(pool, long_items, long_tree, long_profile, TODAY)
    assert details(found, "frames") == [
        "f1: <<keywords>> 0 times",
        "f1: <<sentence>> 2 times",
        "f2: 384 characters of fixed text, over 300",
    ]
    assert details(found, "numbers") == ["f3: not in its items, the dates or the career's years: twelve, three, 2014"]
    pool.frames = pool.frames[:3]
    assert details(pool_checks(pool, long_items, long_tree, long_profile, TODAY), "frame_count") == [
        "frames: 3 frames, not 4 to 6"
    ]


def test_frame_numbers_say_where_code_found_each(long_items, long_profile):
    frame = Frame(
        id="f1",
        family="x",
        uses=["e0.i07"],
        text="<<job_title>> with eleven years since 2015, cutting releases to 40 minutes, in 3 teams. "
        "<<keywords>> <<sentence>>",
    )
    assert frame_numbers(frame, long_items, long_profile, TODAY) == [
        ("eleven", "a count of years, the career has 11"),
        ("2015", "dates of e1"),
        ("40", "item e0.i07"),
        ("3", "item e0.i07"),
    ]
    frame.text = frame.text.replace("eleven", "twelve")
    assert frame_numbers(frame, long_items, long_profile, TODAY)[0] == ("twelve", None)


def test_stale_blocks_skills_and_frames(long_items, long_tree, long_profile):
    pool = clean_pool(long_items, long_tree)
    long_items.items[12].text = "Built the checkout pages in Kotlin."  # e1.i02
    found = pool_checks(pool, long_items, long_tree, long_profile, TODAY)
    assert details(found, "stale") == [
        "e1: its items or stories changed since it was written: run with --experiences 1",
        "skills and frames: written before the items or the tree changed: run with --profile",
    ]


def test_a_newly_proven_leaf_makes_skills_and_frames_stale(long_items, long_tree, long_profile):
    pool = clean_pool(long_items, long_tree)
    next(n for n in long_tree.nodes if n.id == "2.1.1").confirmed = True
    assert [e["id"] for e in pool_checks(pool, long_items, long_tree, long_profile, TODAY)["stale"]] == [
        "skills and frames"
    ]


# --- validation, the view, the file ------------------------------------------------------------------
def test_validate_reports_a_hand_edited_file(long_items, long_tree):
    pool = clean_pool(long_items, long_tree)
    e0 = pool.experiences[0]
    e0.stories[1].ladder[1].id = "e0.s02.r1.a"  # twice, and not its place
    e0.stories[0].groups[0].bullets[1].id = "e0.s01.g1.3"
    e0.stories[2].story = "e0.s09"
    pool.skills[0].nodes = ["9.9.9"]
    pool.frames[1].id = "f1"
    pool.experiences.append(pool.experiences[1].model_copy(update={"experience": 7}))
    problems = validate_pool(pool, long_items, long_tree)
    assert problems == [
        "e0.s09: not a story of e0",
        "e0.s03: no pool",
        "e0.s01.g1.3: does not match its place (e0.s01.g1.2)",
        "e0.s02.r1.a: does not match its place (e0.s02.r1.b)",
        "e0.s03.r1.a: does not match its place (e0.s09.r1.a)",
        "e0.s03.r1.b: does not match its place (e0.s09.r1.b)",
        "e7: an experience the items do not have",
        "e0.s02.r1.a: two pieces have this id",
        "f1: two pieces have this id",
        "k01: node 9.9.9 does not exist",
    ]
    pool.experiences = pool.experiences[:1]
    assert validate_pool(pool, long_items, long_tree)[0] == "e1: not written"


def test_the_view_puts_to_check_first_in_the_gates_order(long_items, long_tree, long_profile):
    pool = clean_pool(long_items, long_tree)
    pool.experiences[0].role_summary.uses = ["e0.i01"]
    page = pool_markdown(pool, long_items, long_tree, long_profile, TODAY)
    heads = [
        "## To check",
        "### 1. Role summaries",
        "### 2. Groups of the two longest stories",
        "### 3. Summary frames",
        "### 4. Skill phrases (25)",
        "### 5. Code-check failures (1)",
        "## The pool",
    ]
    assert [page.index(h) for h in heads] == sorted(page.index(h) for h in heads)
    roles = page.split("### 1. Role summaries")[1].split("### 2.")[0]
    assert "  - states e0.i01; the experience context is e0.i01, e0.i02" in roles
    assert "  - CHECK role_summary: misses the experience context e0.i02" in roles
    assert "> **e0.i02** (context) The platform served the bank's mobile app and its branches.  " in roles
    longest = page.split("### 2.")[1].split("### 3.")[0]
    assert "**e0.s01 · Move to Kubernetes** (5 action and result items)" in longest
    assert "> **e0.i04** (anchor, action, led) Led the move of the platform to Kubernetes.  " in longest
    assert "Group **e0.s01.g1**, 5 lines, printed together:" in longest
    assert '  - numbers: "11" (a count of years, the career has 11); "2015" (dates of e1)' in page
    assert "- **k01** Docker — 1.1.2 Docker" in page
    assert "  - uses e0.i09, e0.i10 · covers e0.i09, e0.i10 · assumes - · leaves: Docker 1" in page
    assert "1 code-check failure(s)." in page


def test_the_file_survives_a_round_trip_one_piece_per_line(long_items, long_tree):
    pool = clean_pool(long_items, long_tree)
    pool.experiences[0].dropped = [{"story": "e0.s02", "what": "group", "text": "a | b", "reason": "short"}]
    text = pool_dump(pool)
    assert Pool.model_validate_json(text) == pool
    assert '{"id": "e0.s02.r1.a", "text": ' in text
    assert all(line.count('"text"') <= 1 for line in text.splitlines())


# --- the writer ---------------------------------------------------------------------------------------
def kind(body: dict) -> str:
    text = user_text(body)
    return "experience" if "EXPERIENCE CONTEXT:" in text else "ladder" if "\nSTORIES:\n" in text else "profile"


def experience_reply(body: dict) -> str:
    text = user_text(body)
    context = text.split("EXPERIENCE CONTEXT:\n")[1].split("\n\nITEMS:")[0].splitlines()
    action = next(line.split()[0] for line in text.split("ITEMS:\n")[1].splitlines() if "[action" in line)
    e = action.split(".")[0]
    return json.dumps(
        {
            "role_summary": {"text": f"The {e} team ran the platform.", "uses": [x.split()[0] for x in context]},
            "compact": {"text": "Built the platform.", "uses": [action]},
        }
    )


def story_blocks(body: dict) -> list:
    """(story id, LONG?, item ids) of each story in a ladder call."""
    out = []
    for block in user_text(body).split("\nSTORIES:\n")[1].split("\n\n"):
        head, _anchor, *items = block.splitlines()
        out.append((head.split()[0].rstrip(":"), "(LONG" in head, [x.split()[0] for x in items]))
    return out


def rung_text(sid: str, r: int, w: str) -> str:
    """A rung's text that fills its lines: "e0.s01 r1 a" on rung 1, padded to 150 and 250 characters above."""
    return f"{sid} r{r} {w}" if r == 1 else sized(f"{sid} r{r} {w}", {2: 150, 3: 250}[r])


def ladder_reply(body: dict, groups_for_all: bool = False, skip: str = "") -> str:
    stories = []
    for sid, long, ids in story_blocks(body):
        if sid == skip:
            continue
        b = {"uses": ids, "covers": ids[-1:], "assumes": []}
        stories.append(
            {
                "id": sid,
                "ladder": [
                    {"rung": r, "wording": w, "text": rung_text(sid, r, w), **b} for r in (1, 2, 3) for w in ("a", "b")
                ],
                "groups": [{"group": 1, "text": f"{sid} g1.1", **b}, {"group": 1, "text": f"{sid} g1.2", **b}]
                if long or groups_for_all
                else [],
            }
        )
    return json.dumps({"stories": stories})


def profile_reply(body: dict) -> str:
    leaves = user_text(body).split("PROVEN LEAVES:\n")[1].split("\n\n")[0].splitlines()
    return json.dumps(
        {
            "skills": [{"text": " ".join(x.split()[1:]).split(" |")[0], "nodes": [x.split()[0]]} for x in leaves],
            "frames": [{"family": "Platform", "text": "<<job_title>> <<keywords>> <<sentence>>", "uses": ["e0.i04"]}],
        }
    )


def answer(body: dict) -> str:
    return {"experience": experience_reply, "ladder": ladder_reply, "profile": profile_reply}[kind(body)](body)


def fake(reply=answer) -> FakeLLM:
    llm = FakeLLM(roles=ROLES)
    llm["compile"].replies = [reply]
    return llm


def writer(llm) -> PoolWriter:
    def prompt(name: str) -> str:
        return (resources_dir() / f"sys_prompt_pool_{name}.txt").read_text(encoding="utf-8")

    return PoolWriter(
        llm=llm,
        experience_prompt=prompt("experience"),
        ladder_prompt=prompt("ladder"),
        profile_prompt=prompt("profile"),
        rules=prompt("rules"),
    )


def write(llm, items, tree, profile, **kwargs) -> Pool:
    return writer(llm).write(items, tree, profile, today=TODAY, **kwargs)


def kinds(llm) -> list:
    return [kind(c) for c in llm["compile"].calls]


def test_batches_put_a_long_story_alone_and_the_others_in_even_calls_of_four(long_items):
    stories = long_items.experiences[0].stories
    work = story_work(long_items)
    assert [[s.id for s in b] for b in batches(stories, work)] == [["e0.s01"], ["e0.s02", "e0.s03"]]
    many = [stories[1].model_copy(update={"id": f"e0.s{k:02d}"}) for k in range(1, 6)]
    assert [len(b) for b in batches(many, {s.id: 2 for s in many})] == [3, 2]
    assert [len(b) for b in batches(many[:4], {s.id: 2 for s in many})] == [4]


def test_a_first_write_makes_experience_calls_then_ladder_calls_and_one_profile_call(
    long_items, long_tree, long_profile
):
    llm = fake()
    pool = write(llm, long_items, long_tree, long_profile)
    assert kinds(llm)[:2] == ["experience", "experience"]
    assert sorted(kinds(llm)[2:]) == ["ladder", "ladder", "ladder", "profile"]
    assert validate_pool(pool, long_items, long_tree) == []
    assert [r.id for r in pool.experiences[0].stories[0].ladder] == [
        f"e0.s01.r{r}.{w}" for r in (1, 2, 3) for w in ("a", "b")
    ]
    assert [[b.id for b in g.bullets] for g in pool.experiences[0].stories[0].groups] == [
        ["e0.s01.g1.1", "e0.s01.g1.2"]
    ]
    assert [k.id for k in pool.skills] == ["k01", "k02", "k03", "k04"] and pool.frames[0].id == "f1"
    assert pool.level_sha256 == level_sha256(long_items, long_tree)
    system = llm["compile"].calls[0]["messages"][0]["content"]
    assert system.startswith("You write two lines") and "SENTENCE VARIETY" in system


def test_each_ladder_call_sees_its_role_summary_its_setting_and_only_its_stories(long_items, long_tree, long_profile):
    llm = fake()
    write(llm, long_items, long_tree, long_profile)
    seen = []
    for call in (c for c in llm["compile"].calls if kind(c) == "ladder"):
        text = user_text(call)
        ids = [sid for sid, _, _ in story_blocks(call)]
        e = ids[0].split(".")[0]
        assert all(sid.startswith(e + ".") for sid in ids)
        assert f"ROLE SUMMARY: The {e} team ran the platform.\nIt states: " in text
        setting = text.split("SETTING:\n")[1].split("\n\nSTORIES:")[0].splitlines()
        assert [x.split()[0] for x in setting] == [
            i.id for i in long_items.items if i.experience == int(e[1:]) and i.type == "context"
        ]
        seen += ids
    assert sorted(seen) == ["e0.s01", "e0.s02", "e0.s03", "e1.s01"]
    first = next(c for c in llm["compile"].calls if kind(c) == "ladder" and story_blocks(c)[0][0] == "e0.s01")
    assert "It states: e0.i01, e0.i02" in user_text(first)
    assert "e0.s01 (LONG): Move to Kubernetes\n  ANCHOR: e0.i04\n  e0.i03 [context]" in user_text(first)


def test_a_long_story_is_alone_in_its_call(long_items, long_tree, long_profile):
    llm = fake()
    write(llm, long_items, long_tree, long_profile)
    calls = [[sid for sid, _, _ in story_blocks(c)] for c in llm["compile"].calls if kind(c) == "ladder"]
    assert ["e0.s01"] in calls and ["e0.s02", "e0.s03"] in calls


def test_answers_land_on_their_own_experience_whatever_order_the_calls_finish(long_items, long_tree, long_profile):
    def slow_first(body):
        if kind(body) == "experience":
            time.sleep(0.1 if "e0.i" in user_text(body) else 0)
        elif kind(body) == "ladder":
            time.sleep(0.1 if story_blocks(body)[0][0].startswith("e0") else 0)
        return answer(body)

    pool = write(fake(slow_first), long_items, long_tree, long_profile)
    assert [b.experience for b in pool.experiences] == [0, 1]
    assert pool.experiences[0].role_summary.text == "The e0 team ran the platform."
    assert pool.experiences[1].role_summary.text == "The e1 team ran the platform."
    assert [s.story for s in pool.experiences[0].stories] == ["e0.s01", "e0.s02", "e0.s03"]
    assert [s.story for s in pool.experiences[1].stories] == ["e1.s01"]


def test_unchanged_items_make_no_call(long_items, long_tree, long_profile):
    first = write(fake(), long_items, long_tree, long_profile)
    llm = fake()
    again = write(llm, long_items, long_tree, long_profile, previous=first)
    assert llm["compile"].calls == []
    assert again == first


def test_one_changed_experience_makes_its_calls_only_and_lists_its_edited_pieces(long_items, long_tree, long_profile):
    first = Pool.model_validate_json(pool_dump(write(fake(), long_items, long_tree, long_profile)))
    edited = first.experiences[1].stories[0].ladder[2]
    edited.text, edited.edited = "Built the checkout pages in Java, by hand.", True
    first.experiences[1].compact.edited = True
    first.experiences[0].stories[1].ladder[0].edited = True  # e0 is copied: its edit stays
    long_items.items[12].text = "Built the checkout pages in Kotlin."  # e1.i02
    llm = fake()
    again = write(llm, long_items, long_tree, long_profile, previous=first)
    assert kinds(llm) == ["experience", "ladder"]
    assert again.experiences[0] == first.experiences[0]
    assert (again.skills, again.frames, again.level_sha256) == (first.skills, first.frames, first.level_sha256)
    block = again.experiences[1]
    assert [(p["what"], p.get("id")) for p in block.previous] == [("compact", None), ("rung", "e1.s01.r2.a")]
    assert block.previous[1]["text"] == "Built the checkout pages in Java, by hand."
    assert block.items_sha256 == shown_sha256(long_items, 1)
    stale = [e["id"] for e in pool_checks(again, long_items, long_tree, long_profile, TODAY)["stale"]]
    assert stale == ["skills and frames"]
    assert "hand-edited rung before the rewrite e1.s01.r2.a" in pool_markdown(
        again, long_items, long_tree, long_profile, TODAY
    )


def test_a_changed_scope_rewrites_that_block(long_items, long_tree, long_profile):
    first = write(fake(), long_items, long_tree, long_profile)
    long_items.items[12].scope = "front end only"  # e1.i02
    llm = fake()
    again = write(llm, long_items, long_tree, long_profile, previous=first)
    assert kinds(llm) == ["experience", "ladder"]
    assert again.experiences[0] == first.experiences[0]
    assert "  e1.i02 [action, did] Built the checkout pages in Java. | scope: front end only" in user_text(
        next(c for c in llm["compile"].calls if kind(c) == "ladder")
    )


def test_a_story_cut_again_from_the_same_texts_rewrites_that_block(long_items, long_tree, long_profile):
    first = write(fake(), long_items, long_tree, long_profile)
    e0 = long_items.experiences[0]
    e0.stories[1].items, e0.stories[2].items = ["e0.i09"], ["e0.i10", "e0.i11"]  # e0.i10 moves to e0.s03
    long_items.items[9].story = "e0.s03"
    llm = fake()
    again = write(llm, long_items, long_tree, long_profile, previous=first)
    assert sorted(kinds(llm)) == ["experience", "ladder", "ladder"]
    assert again.experiences[1] == first.experiences[1]
    assert again.experiences[0].items_sha256 == shown_sha256(long_items, 0) != first.experiences[0].items_sha256


def test_force_writes_an_unchanged_experience_again(long_items, long_tree, long_profile):
    first = write(fake(), long_items, long_tree, long_profile)
    llm = fake()
    write(llm, long_items, long_tree, long_profile, previous=first, force=[0])
    assert sorted(kinds(llm)) == ["experience", "ladder", "ladder"]


def test_profile_level_alone_makes_one_call(long_items, long_tree, long_profile):
    first = write(fake(), long_items, long_tree, long_profile)
    llm = fake()
    again = write(llm, long_items, long_tree, long_profile, previous=first, profile_level=True)
    assert kinds(llm) == ["profile"]
    assert again.experiences == first.experiences


def test_a_group_of_a_short_story_is_dropped_with_its_reason(long_items, long_tree, long_profile):
    pool = write(
        fake(lambda body: ladder_reply(body, groups_for_all=True) if kind(body) == "ladder" else answer(body)),
        long_items,
        long_tree,
        long_profile,
    )
    e0 = pool.experiences[0]
    assert len(e0.stories[0].groups) == 1 and e0.stories[1].groups == [] and e0.stories[2].groups == []
    assert [(d["story"], d["what"], d["reason"]) for d in e0.dropped] == [
        ("e0.s02", "group", "a group of a short story (2 action and result items; 5 make it long)"),
        ("e0.s03", "group", "a group of a short story (1 action and result items; 5 make it long)"),
    ]
    assert e0.dropped[0]["text"] == "e0.s02 g1.1 | e0.s02 g1.2"


def test_a_rung_that_fills_fewer_lines_moves_down_to_the_rung_it_fills(long_items, long_tree, long_profile):
    def thin(body):
        if kind(body) != "ladder":
            return answer(body)
        reply = json.loads(ladder_reply(body))
        for s in reply["stories"]:
            for r in s["ladder"]:
                if r["rung"] == 3:
                    r["text"] = sized("Moved", 190 if r["wording"] == "a" else 200)  # a: 2 lines; b: 3
                if s["id"] == "e0.s03" and r["rung"] == 2:
                    r["text"] = sized("Ran", 90)  # both: 1 line
        return json.dumps(reply)

    e0 = write(fake(thin), long_items, long_tree, long_profile).experiences[0]
    assert [r.id for r in e0.stories[1].ladder] == [
        "e0.s02.r1.a",
        "e0.s02.r1.b",
        "e0.s02.r2.a",
        "e0.s02.r2.b",
        "e0.s02.r2.c",
        "e0.s02.r3.b",
    ]
    moved = e0.stories[1].ladder[4]
    assert (moved.text, moved.rung, moved.wording) == (sized("Moved", 190), 2, "c")
    # e0.s03: both rung-2 bullets move to rung 1 (c, d); the 2-line r3.a then takes rung 2's free a
    assert [(r.rung, r.wording) for r in e0.stories[2].ladder] == [
        (1, "a"),
        (1, "b"),
        (1, "c"),
        (1, "d"),
        (2, "a"),
        (3, "b"),
    ]
    assert e0.dropped == []


def test_a_second_bullet_for_a_rung_is_kept_when_the_first_moves_down(long_items, long_tree, long_profile):
    def twice(body):
        if kind(body) != "ladder":
            return answer(body)
        reply = json.loads(ladder_reply(body))
        for s in reply["stories"]:
            if s["id"] == "e0.s02":
                first = next(r for r in s["ladder"] if (r["rung"], r["wording"]) == (2, "a"))
                s["ladder"].append(dict(first, text=sized("Ran", 150)))  # a second r2.a, 2 lines
                first["text"] = sized("Ran", 90)  # the first r2.a fills 1 line
        return json.dumps(reply)

    e0 = write(fake(twice), long_items, long_tree, long_profile).experiences[0]
    ladder = {r.id: r.text for r in e0.stories[1].ladder}
    assert (ladder["e0.s02.r1.c"], ladder["e0.s02.r2.a"]) == (sized("Ran", 90), sized("Ran", 150))
    assert e0.dropped == []


def test_the_wordings_check_compares_every_pair_of_a_rung(long_items, long_tree, long_profile):
    pool = clean_pool(long_items, long_tree)
    ladder = pool.experiences[0].stories[1].ladder
    ladder.append(rung("e0.s02", 2, "c", ["e0.i09", "e0.i10"], ["e0.i09", "e0.i10"], first="I"))
    assert details(pool_checks(pool, long_items, long_tree, long_profile, TODAY), "wordings") == [
        'e0.s02.r2: b and c both open with "i"'
    ]


def test_a_fourth_group_and_a_second_rung_are_dropped(long_items, long_tree, long_profile):
    def many(body):
        if kind(body) != "ladder":
            return answer(body)
        reply = json.loads(ladder_reply(body))
        for s in reply["stories"]:
            s["ladder"].append(dict(s["ladder"][0], text="again"))
            s["groups"] = [dict(b, group=n) for n in (1, 2, 3, 4) for b in s["groups"]]
        return json.dumps(reply)

    e0 = write(fake(many), long_items, long_tree, long_profile).experiences[0]
    assert [g.id for g in e0.stories[0].groups] == ["e0.s01.g1", "e0.s01.g2", "e0.s01.g3"]
    assert ("e0.s01", "group", "a group beyond the 3th") in [(d["story"], d["what"], d["reason"]) for d in e0.dropped]
    assert ("e0.s01", "r1.a", "a second bullet for this rung and wording") in [
        (d["story"], d["what"], d["reason"]) for d in e0.dropped
    ]
    assert [r.text for r in e0.stories[0].ladder if r.id == "e0.s01.r1.a"] == ["e0.s01 r1 a"]


def test_a_reply_missing_a_story_goes_back_once(long_items, long_tree, long_profile):
    asked = []

    def forgetful(body):
        if kind(body) == "ladder" and story_blocks(body)[0][0] == "e0.s02":
            asked.append(body)
            return ladder_reply(body, skip="e0.s03") if len(asked) == 1 else ladder_reply(body)
        return answer(body)

    llm = fake(forgetful)
    pool = write(llm, long_items, long_tree, long_profile)
    assert len(asked) == 2 and len(llm["compile"].calls) == 7
    assert (
        "Your previous reply was rejected:\n- story e0.s03 is missing: write it" in asked[1]["messages"][-1]["content"]
    )
    assert [s.story for s in pool.experiences[0].stories] == ["e0.s01", "e0.s02", "e0.s03"]


def test_json_object_says_where_the_json_breaks():
    assert json_object('```json\n{"a": [1]}\n```') == {"a": [1]}
    with pytest.raises(ValueError) as caught:
        json_object('{"groups": [[{"text": "x"}]}]')
    assert str(caught.value) == (
        "- the JSON breaks at character 27: Expecting ',' delimiter. "
        'Around it: ...{"groups": [[{"text": "x"}]<<HERE>>}]...'
    )
    with pytest.raises(ValueError, match="it is not a JSON object"):
        json_object("[1, 2]")


def test_a_broken_json_goes_back_once_with_where_it_breaks(long_items, long_tree, long_profile):
    sent = []

    def mismatched(body):
        if kind(body) == "ladder" and story_blocks(body)[0][0] == "e0.s01":
            sent.append(body)
            if len(sent) == 1:
                return ladder_reply(body).replace("}]}]}", "}]}]")
        return answer(body)

    llm = fake(mismatched)
    write(llm, long_items, long_tree, long_profile)
    assert len(sent) == 2 and len(llm["compile"].calls) == 7
    assert "- the JSON breaks at character" in sent[1]["messages"][-1]["content"]


def test_a_reply_that_stays_wrong_fails_with_stage_bullet_writing(long_items, long_tree, long_profile):
    def bad_rung(body):
        if kind(body) != "ladder":
            return answer(body)
        reply = json.loads(ladder_reply(body))
        reply["stories"][0]["ladder"][0]["rung"] = 4
        reply["stories"][0]["ladder"][1]["wording"] = "c"
        return json.dumps(reply)

    with pytest.raises(ModelOutputError) as caught:
        write(fake(bad_rung), long_items, long_tree, long_profile)
    assert caught.value.stage == "Bullet writing"
    assert "rung 4 (1, 2 or 3)" in str(caught.value) and "wording 'c' (a or b)" in str(caught.value)


def test_every_call_is_counted(long_items, long_tree, long_profile):
    w = writer(fake())
    w.write(long_items, long_tree, long_profile, today=TODAY)
    assert w.usage.calls == 6
    assert (w.usage.input_tokens, w.usage.thinking_tokens, w.usage.output_tokens) == (66, 12, 120)


def test_a_failed_call_is_counted_too(long_items, long_tree, long_profile):
    w = writer(fake(lambda body: answer(body) if kind(body) != "profile" else json.dumps({"skills": []})))
    with pytest.raises(ModelOutputError):
        w.write(long_items, long_tree, long_profile, today=TODAY)
    assert w.usage.calls >= 2 + 2  # the experience calls, the profile call twice


# --- the review (S4) -----------------------------------------------------------------------------------
def reviewing(replacements: list) -> FakeLLM:
    """The validation role: on e0's first round, ``replacements`` (duty 1 is the compact line, duty 2
    e0.s01.r1.a); every other round, and e1, "OK"."""
    llm = FakeLLM(roles=ROLES)

    def answer(body: dict):
        text = user_text(body)
        if "Example Bank" in text and "ATTEMPT: 1\n" in text:
            return tool_completion("fake-validation", [("replace_in_duties", r) for r in replacements])
        return "OK"

    llm["review"].replies = [answer]
    return llm


def review(llm, items, tree, profile):
    prompt = (resources_dir() / "sys_prompt_pool_review.txt").read_text(encoding="utf-8")
    return PoolReviewer(llm=llm, prompt=prompt).review(clean_pool(items, tree), profile)


def test_a_replacement_is_applied_to_the_piece_and_nothing_else(long_items, long_tree, long_profile):
    fix = {"old": "Moved a bank's", "new": "Took a bank's", "duty": 1, "reason": "Syntax: x"}
    llm = reviewing([fix])
    pool, calls, moves = review(llm, long_items, long_tree, long_profile)
    before = clean_pool(long_items, long_tree)

    assert pool.experiences[0].compact.text == "Took a bank's payments platform to Kubernetes."
    assert pool.experiences[0].compact.uses == before.experiences[0].compact.uses
    assert pool.experiences[0].stories == before.experiences[0].stories
    assert pool.experiences[1] == before.experiences[1]
    assert calls == [
        {
            "experience": 0,
            "round": 1,
            "duty": 1,
            "old": "Moved a bank's",
            "new": "Took a bank's",
            "reason": "Syntax: x",
            "error": None,
            "changes": [
                {
                    "id": "e0.compact",
                    "before": "Moved a bank's payments platform to Kubernetes.",
                    "after": "Took a bank's payments platform to Kubernetes.",
                }
            ],
            "refused": [],
        }
    ]
    first = next(
        c for c in llm["review"].calls if "Example Bank" in user_text(c) and "ATTEMPT: 1\n" in user_text(c)
    )
    shown = user_text(first)
    assert '"duty_ids": [\n    "e0.compact",\n    "e0.s01.r1.a"' in shown
    assert dumps(long_profile["experiences"][0]) in shown and "Example Shop" not in shown  # its own experience
    assert first["tools"][0]["function"]["name"] == "replace_in_duties"
    assert len(llm["review"].calls) == 3  # e0 twice (the fix, then OK), e1 once
    assert moves == []
    assert "- **e0.compact** (round 1)" in review_markdown(calls, moves)


def test_a_replacement_is_refused_on_each_piece_whose_line_count_it_changes(long_items, long_tree, long_profile):
    """Without a duty the call hits every wording a of e0: each rung 1 would fill 2 lines and keeps its
    text; rungs 2 and 3 and the group bullet still fit and change. The refusal goes back to the model."""
    old = "Moved the platform work"
    llm = reviewing([{"old": old, "new": old + " for the retail bank"}])
    pool, calls, moves = review(llm, long_items, long_tree, long_profile)

    def texts(block) -> dict:
        return {b.id: b.text for s in block.stories for b in [*s.ladder, *(x for g in s.groups for x in g.bullets)]}

    before, after = texts(clean_pool(long_items, long_tree).experiences[0]), texts(pool.experiences[0])
    assert sorted(i for i in after if after[i] != before[i]) == [
        "e0.s01.g1.1",
        "e0.s01.r2.a",
        "e0.s01.r3.a",
        "e0.s02.r2.a",
        "e0.s02.r3.a",
    ]
    assert [r["id"] for r in calls[0]["refused"]] == ["e0.s01.r1.a", "e0.s02.r1.a", "e0.s03.r1.a"]
    assert calls[0]["error"].startswith(
        "'Moved the platform work' refused in duty 2: it would fill 2 printed line(s), more than 1 "
        "(100 characters, 96 a line); duty 10: "
    )
    retry = next(c for c in llm["review"].calls if "ATTEMPT: 2\n" in user_text(c))
    assert (
        "FAILED REPLACEMENTS:\n'Moved the platform work' refused in duty 2: "
        "it would fill 2 printed line(s), more than 1" in user_text(retry)
    )
    view = review_markdown(calls, moves)
    assert "3 piece replacement(s) refused for the line count" in view
    assert "- **e0.s01.r1.a, e0.s02.r1.a, e0.s03.r1.a** (round 1)" in view and "refused text (100 ch)" in view


def test_a_bullet_a_replacement_shortens_moves_down_to_the_rung_it_fills(long_items, long_tree, long_profile):
    """Only more lines are refused. e0.s02.r2.a cut to one line goes in, and at the end of the review
    it moves to rung 1 as its first free wording, c, by the writer's rule."""
    two_lines = clean_pool(long_items, long_tree).experiences[0].stories[1].ladder[2]
    assert two_lines.id == "e0.s02.r2.a" and lines(two_lines.text) == 2
    llm = reviewing([{"old": two_lines.text[23:], "new": ".", "duty": 12}])
    pool, calls, moves = review(llm, long_items, long_tree, long_profile)

    assert (calls[0]["error"], calls[0]["refused"]) == (None, [])
    ladder = pool.experiences[0].stories[1].ladder
    assert [r.id for r in ladder] == [
        "e0.s02.r1.a",
        "e0.s02.r1.b",
        "e0.s02.r1.c",
        "e0.s02.r2.b",
        "e0.s02.r3.a",
        "e0.s02.r3.b",
    ]
    assert (ladder[2].text, ladder[2].rung, ladder[2].uses) == ("Moved the platform work.", 1, two_lines.uses)
    assert moves == [("e0.s02.r2.a", "e0.s02.r1.c")]
    assert "- e0.s02.r2.a → **e0.s02.r1.c**" in review_markdown(calls, moves)
