"""Base items and stories (S1): the pure checks, the view, the compile graph and regeneration.

No key, no network, no terminal: the graph runs on :class:`FakeLLM`, whose fake transport sits
inside a real ``ModelSelector``. Its scripted replies are cut from the experience in the request,
so the span check runs on real profile text.
"""

from __future__ import annotations

import json
import time
from datetime import date

import pytest

from common.errors import ModelOutputError
from pipeline_v2 import ROLES, ItemCompiler, Items, items_markdown, resources_dir, validate
from pipeline_v2.items import build_experience
from pipeline_v2.store import Item, check_sources, coverage, experience_sha256, months

from pipeline_helpers import FakeLLM, section, user_text
from v2_helpers import cut, raw

TODAY = date(2026, 11, 18)

#: A small experience whose duties tell one piece of work over two duties, and a one-off.
EXP = {
    "id": 2,
    "title": "Engineer",
    "company": "Acme",
    "dates": "March 2016 -- May 2021",
    "context": "",
    "role_summary": "A team of five ran the billing platform for the whole group.",
    "duties": [
        "Ported the billing engine from notebooks to a Java service. It halved the nightly run time.",
        "During the porting, rewrote the monthly reports.",
        "Presented the plan to management once.",
    ],
}


def item(duty: int, *source: str, **fields) -> Item:
    return Item(id="e2.i01", experience=2, duty=duty, type="action", text="t", source=list(source), **fields)


def reply(items, stories, duties=None) -> dict:
    return {
        "items": items,
        "stories": stories,
        "duties": duties if duties is not None else [{"duty": d, "weight": 2, "why": "w"} for d in range(3)],
    }


# --- check_sources and coverage --------------------------------------------------------
def test_a_verbatim_span_passes():
    assert check_sources(EXP, item(0, "Ported the billing engine")) == []


def test_whitespace_does_not_count():
    assert check_sources(EXP, item(0, "Ported  the\nbilling   engine ")) == []


def test_a_span_from_another_duty_of_the_same_experience_passes():
    """As in S0: the span is the profile's text, only filed under the wrong duty."""
    assert check_sources(EXP, item(0, "rewrote the monthly reports")) == []


def test_a_span_from_nowhere_is_reported():
    assert check_sources(EXP, item(0, "Ported the engine", "Led a team of 40")) == [
        "Ported the engine",
        "Led a team of 40",
    ]


def test_coverage_counts_the_duties_characters_under_a_span():
    duties = [d for d in EXP["duties"]]
    total = sum(len(d) for d in duties)
    assert coverage(EXP, []) == 0.0
    assert coverage(EXP, [item(0, duties[0])]) == pytest.approx(len(duties[0]) / total)
    assert coverage(EXP, [item(d, text) for d, text in enumerate(duties)]) == 1.0


def test_overlapping_spans_count_once():
    half = "Ported the billing engine from notebooks"
    overlap = "billing engine from notebooks to a Java service."
    covered = len("Ported the billing engine from notebooks to a Java service.")
    total = sum(len(d) for d in EXP["duties"])
    assert coverage(EXP, [item(0, half, overlap)]) == pytest.approx(covered / total)


def test_the_role_summary_is_not_counted():
    assert coverage(EXP, [item(-1, EXP["role_summary"])]) == 0.0


# --- months ---------------------------------------------------------------------------------
def test_months_count_both_ends():
    assert months("January 2024 -- April 2026", TODAY) == 28
    assert months("March 2016 -- March 2016", TODAY) == 1
    assert months("Nov 2004 -- Sep 2011", TODAY) == 83


def test_present_counts_to_the_run_date():
    assert months("March 2022 -- Present", TODAY) == 57
    assert months("March 2022 -- present", date(2022, 3, 31)) == 1


@pytest.mark.parametrize("dates", ["2019 -- 2021", "Spring 2019 -- May 2021", "May 2021 -- March 2019", ""])
def test_unreadable_dates_give_none(dates):
    assert months(dates, TODAY) is None


# --- experience_sha256 ----------------------------------------------------------------------
def test_the_hash_ignores_key_order_and_whitespace():
    reordered = json.loads(json.dumps(dict(reversed(list(EXP.items()))), indent=4))
    assert experience_sha256(reordered) == experience_sha256(EXP)
    changed = {**EXP, "duties": [*EXP["duties"][:2], "Presented the plan to management twice."]}
    assert experience_sha256(changed) != experience_sha256(EXP)


# --- build_experience -------------------------------------------------------------------------
PORTING = [
    raw(1, -1, "context", ["A team of five ran the billing platform"], 0),
    raw(2, 1, "action", ["rewrote the monthly reports"], 1),
    raw(3, 0, "action", ["Ported the billing engine from notebooks to a Java service."], 1),
    raw(4, 0, "result", ["It halved the nightly run time."], 1),
    raw(5, 0, "context", ["billing engine"], 1),
    raw(6, 2, "context", ["the plan"], 2),  # a story with context only: dissolved
    raw(7, 2, "action", ["Presented the plan to management once."], 3),
]
STORIES = [
    {"n": 1, "title": "Billing engine porting to Java", "anchor": [5, 3]},
    {"n": 2, "title": "The plan", "anchor": [6]},
    {"n": 3, "title": "Plan presentation to management", "anchor": [7]},
]


def test_ids_are_numbered_in_reply_order_and_story_items_put_context_first():
    entry, items = build_experience(EXP, reply(PORTING, STORIES), TODAY)
    assert [i.id for i in items] == [f"e2.i{n:02d}" for n in range(1, 8)]
    assert [i.text for i in items] == [f"text {n}" for n in range(1, 8)]
    assert [s.id for s in entry.stories] == ["e2.s01", "e2.s02"]
    porting = entry.stories[0]
    # context first, then the actions and results in profile order (duty 0 before duty 1)
    assert porting.items == ["e2.i05", "e2.i03", "e2.i04", "e2.i02"]
    assert porting.anchor == ["e2.i05", "e2.i03"]
    assert entry.months == 63 and entry.sha256 == experience_sha256(EXP)
    assert entry.coverage > 0.8


def test_story_0_and_a_story_with_no_action_or_result_go_to_the_experience_context():
    entry, items = build_experience(EXP, reply(PORTING, STORIES), TODAY)
    by_text = {i.text: i for i in items}
    assert entry.context == [by_text["text 1"].id, by_text["text 6"].id]
    assert by_text["text 1"].story is None and by_text["text 6"].story is None
    assert [s.title for s in entry.stories] == ["Billing engine porting to Java", "Plan presentation to management"]
    assert entry.stories[1].items == [by_text["text 7"].id]


def test_the_anchor_keeps_an_item_outside_its_story_for_the_view_and_loses_a_dropped_one():
    stories = [
        {"n": 1, "title": "Billing engine porting to Java", "anchor": [7, 99]},
        {"n": 3, "title": "Plan presentation to management", "anchor": [7]},
    ]
    entry, items = build_experience(EXP, reply([r for r in PORTING if r["story"] != 2], stories), TODAY)
    presentation = next(i for i in items if i.text == "text 7").id
    assert entry.stories[0].anchor == [presentation]
    assert f"e2.s01: anchor {presentation} is not in the story" in validate(
        {"experiences": [EXP]}, Items(profile_sha256="", experiences=[entry], items=items)
    )


def test_one_weight_per_duty_the_first_given():
    duties = [
        {"duty": 0, "weight": 3, "why": "the main work"},
        {"duty": 0, "weight": 1, "why": "again"},
        {"duty": 1, "weight": 2, "why": "recurring"},
        {"duty": 7, "weight": 1, "why": "no such duty"},
    ]
    entry, items = build_experience(EXP, reply(PORTING, STORIES, duties), TODAY)
    assert [(w.duty, w.weight, w.why) for w in entry.weights] == [(0, 3, "the main work"), (1, 2, "recurring")]
    problems = validate({"experiences": [EXP]}, Items(profile_sha256="", experiences=[entry], items=items))
    assert "e2: no weight for duty 2" in problems


def test_what_cannot_be_trusted_is_dropped_with_its_reason():
    bad = [
        raw(8, 0, "action", [], 1),
        raw(9, 0, "action", ["Led a team of 40"], 1),
        raw(10, 0, "action", ["Ported"], 5),
        raw(11, 0, "outcome", ["Ported"], 1),
        raw(12, 0, "action", ["Ported"], 1, agency="owned"),
        raw(13, 0, "action", ["Ported"], 0),
        raw(14, 9, "action", ["Ported"], 1),
    ]
    entry, items = build_experience(EXP, reply(PORTING + bad, STORIES), TODAY)
    assert len(items) == 7
    assert [d["reason"] for d in entry.dropped] == [
        "no source",
        "span not verbatim: ['Led a team of 40']",
        "unknown story 5",
        "bad type 'outcome'",
        "bad agency 'owned'",
        "an action outside any story",
        "unknown duty 9",
    ]
    assert [d["text"] for d in entry.dropped] == [f"text {n}" for n in range(8, 15)]


def test_hard_limits_are_reported_never_truncated():
    many = [raw(n, 0, "action", ["Ported"], 1, tags=[f"t{k}" for k in range(7)]) for n in range(1, 16)]
    stories = [{"n": 1, "title": " ".join(["word"] * 13), "anchor": [1, 2, 3, 4]}]
    entry, items = build_experience(EXP, reply(many, stories), TODAY)
    assert len(items) == 15 and len(entry.stories[0].anchor) == 4 and len(items[0].tags) == 7
    problems = validate({"experiences": [EXP]}, Items(profile_sha256="", experiences=[entry], items=items))
    assert "e2.s01: 15 action and result items (limit 14)" in problems
    assert "e2.s01: anchor of 4 items (limit 1-3)" in problems
    assert "e2.s01: title of 13 words (limit 12)" in problems
    assert "e2.i01: 7 tags (limit 1-6)" in problems


def test_a_reply_that_does_not_validate_raises():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        build_experience(EXP, {"items": PORTING, "stories": STORIES}, TODAY)


# --- validate and the view --------------------------------------------------------------------
def built() -> Items:
    entry, items = build_experience(EXP, reply(PORTING, STORIES), TODAY)
    return Items(profile_sha256="", experiences=[entry], items=items)


def test_a_fresh_compile_validates():
    assert validate({"experiences": [EXP]}, built()) == []


def test_validate_reports_hand_edits_that_break_the_store_and_keeps_them():
    store = built()
    reports = store.items[1]
    reports.text, reports.source, reports.edited = "Rewrote the yearly reports.", ["rewrote the yearly reports"], True
    store.items[3].story = "e2.s02"  # moved in the item, not in the story lists
    store.experiences[0].weights = store.experiences[0].weights[:2]
    problems = validate({"experiences": [EXP]}, store)
    assert problems == [
        "e2: no weight for duty 2",
        "e2.i04: listed in e2.s01, but its story is e2.s02",
        "e2.i02: span not verbatim in the profile: ['rewrote the yearly reports']",
    ]
    assert store.items[1].text == "Rewrote the yearly reports."


def test_validate_reports_a_changed_experience_and_unreadable_dates():
    changed = {**EXP, "dates": "since 2016"}
    problems = validate({"experiences": [changed]}, built())
    assert "e2: the profile's experience changed since it was compiled: recompile it" in problems
    assert validate({"experiences": [EXP, {**EXP, "id": 3}]}, built()) == ["e3: not compiled"]


def test_the_view_lists_weights_context_anchors_and_drops():
    duties = [
        {"duty": 0, "weight": 3, "why": "the main work"},
        {"duty": 1, "weight": 2, "why": "recurring"},
        {"duty": 2, "weight": 1, "why": "once"},
    ]
    entry, items = build_experience(EXP, reply(PORTING + [raw(8, 0, "action", ["Led 40"], 1)], STORIES, duties), TODAY)
    page = items_markdown({"experiences": [EXP]}, Items(profile_sha256="", experiences=[entry], items=items))
    assert "## e2 · Engineer, Acme · March 2016 -- May 2021 · 63 months" in page
    assert "- duty 0: **3** · the main work — Ported the billing engine from notebooks" in page
    assert "- duty 2: **1** · once — Presented the plan to management once." in page
    context = page.split("**Experience context**")[1].split("###")[0]
    assert "**e2.i01** (context, role summary) text 1" in context
    assert "**e2.i06** (context, duty 2) text 6" in context
    assert "### e2.s01 · Billing engine porting to Java" in page
    assert "- **e2.i05** (anchor, context, duty 0) text 5" in page
    assert "- **e2.i04** (result, did, duty 0) text 4" in page
    assert "**Dropped**" in page and "(span not verbatim: ['Led 40']) text 8" in page


def test_the_view_joins_a_title_given_several_ways():
    exp = {**EXP, "title": ["Engineer", "Developer"]}
    entry, items = build_experience(exp, reply(PORTING, STORIES), TODAY)
    page = items_markdown({"experiences": [exp]}, Items(profile_sha256="", experiences=[entry], items=items))
    assert "## e2 · Engineer / Developer, Acme ·" in page


# --- the graph ---------------------------------------------------------------------------------------
def compiler(llm) -> ItemCompiler:
    prompt = (resources_dir() / "sys_prompt_items.txt").read_text(encoding="utf-8")
    return ItemCompiler(llm=llm, system_prompt=prompt)


def fake(*replies) -> FakeLLM:
    llm = FakeLLM(roles=ROLES)
    llm["compile"].replies = list(replies) or [cut]
    return llm


def test_one_call_per_experience_shown_only_that_experience(profile, candidate_data):
    llm = fake()
    store = compiler(llm).compile(profile, today=TODAY)
    calls = llm["compile"].calls
    assert len(calls) == len(profile["experiences"]) == 5
    for call in calls:
        shown = section(call, "EXPERIENCE")
        exp = next(e for e in profile["experiences"] if e["duties"] == shown["duties"])
        assert set(shown) == {"title", "company", "dates", "context", "role_summary", "duties"}
        text = user_text(call)
        others = [e for e in profile["experiences"] if e is not exp]
        assert not [d for e in others for d in e["duties"] if d in text]
        assert candidate_data["email"] not in text and candidate_data["phone"] not in text
        assert profile["skills"][0] not in text
    assert validate(profile, store) == []


def test_the_answers_land_on_their_own_experience_whatever_order_the_calls_finish(profile):
    """The earliest experiences answer last."""

    def slow_first(body):
        exp = section(body, "EXPERIENCE")
        position = next(n for n, e in enumerate(profile["experiences"]) if e["duties"] == exp["duties"])
        time.sleep(0.05 * (5 - position))
        return cut(body)

    store = compiler(fake(slow_first)).compile(profile, today=TODAY)
    assert [x.id for x in store.experiences] == [0, 1, 2, 3, 4]
    for exp in profile["experiences"]:
        mine = [i for i in store.items if i.experience == exp["id"]]
        assert [i.text for i in mine if i.duty >= 0] == exp["duties"]
        assert all(i.id.startswith(f"e{exp['id']}.i") for i in mine)
    assert validate(profile, store) == []


def test_a_failed_call_fails_the_compile_with_stage_profile_breakdown(profile):
    with pytest.raises(ModelOutputError) as caught:
        compiler(fake("not json")).compile(profile, today=TODAY)
    assert caught.value.stage == "Profile breakdown"


def test_every_call_is_counted(profile):
    items = compiler(fake())
    items.compile(profile, today=TODAY)
    assert items.usage.calls == 5
    assert (items.usage.input_tokens, items.usage.thinking_tokens, items.usage.output_tokens) == (55, 10, 100)


def test_a_reply_that_is_not_json_is_retried_through_the_selector(profile):
    profile["experiences"] = profile["experiences"][:1]
    llm = fake("not json", cut)
    store = compiler(llm).compile(profile, today=TODAY)
    assert len(llm["compile"].calls) == 2 and len(store.items) == 10


def test_an_invalid_reply_is_corrected_once(profile):
    profile["experiences"] = profile["experiences"][:1]
    no_stories = lambda body: json.dumps({k: v for k, v in json.loads(cut(body)).items() if k != "stories"})
    llm = fake(no_stories, cut)
    items = compiler(llm)
    store = items.compile(profile, today=TODAY)
    assert len(llm["compile"].calls) == 2 and len(store.experiences[0].stories) == 5
    assert "Your previous reply was rejected:\n- stories: Field required" in llm["compile"].last_prompt
    assert items.usage.calls == 2


def test_an_invalid_reply_fails_after_its_correction(profile):
    profile["experiences"] = profile["experiences"][:1]
    llm = fake(json.dumps({"items": [], "stories": [], "duties": [{"duty": 0, "weight": 5, "why": ""}]}))
    with pytest.raises(ModelOutputError) as caught:
        compiler(llm).compile(profile, today=TODAY)
    assert caught.value.stage == "Profile breakdown" and len(llm["compile"].calls) == 2
    assert "duties.0.weight" in str(caught.value)


# --- regeneration ----------------------------------------------------------------------------
def test_an_unchanged_profile_makes_no_call(profile):
    first = compiler(fake()).compile(profile, today=TODAY)
    llm = fake()
    again = compiler(llm).compile(profile, today=TODAY, previous=first)
    assert llm["compile"].calls == []
    assert again.model_dump_json() == first.model_dump_json()


def test_force_recompiles_an_unchanged_experience(profile):
    first = compiler(fake()).compile(profile, today=TODAY)
    llm = fake()
    compiler(llm).compile(profile, today=TODAY, previous=first, force=[3])
    assert [section(c, "EXPERIENCE")["duties"] for c in llm["compile"].calls] == [profile["experiences"][3]["duties"]]


def test_one_changed_experience_makes_one_call_and_lists_its_previous_edits(profile):
    first = Items.model_validate_json(compiler(fake()).compile(profile, today=TODAY).model_dump_json())
    # hand edits: an item of e0 and one of e1, a weight and a merge in e0
    for exp_id in (0, 1):
        edited = next(i for i in first.items if i.experience == exp_id and i.duty == 0)
        edited.text, edited.edited = edited.text + " (by hand)", True
    e0 = first.experiences[0]
    e0.weights[1].weight, e0.weights[1].why, e0.weights[1].edited = 3, "my main work", True
    e0.weights[2].weight = 1  # changed with nobody marking it: not a hand edit
    e0.stories[0].merged = ["e0.s09"]
    kept = {x.id: x.model_dump_json() for x in first.experiences}

    profile["experiences"][0]["duties"][2] += " Twice."
    llm = fake()
    again = compiler(llm).compile(profile, today=TODAY, previous=first)

    assert len(llm["compile"].calls) == 1
    for x in again.experiences[1:]:
        assert x.model_dump_json() == kept[x.id]
    assert [i for i in again.items if i.experience == 1] == [i for i in first.items if i.experience == 1]
    assert any(i.text.endswith("(by hand)") for i in again.items if i.experience == 1)
    previous = again.experiences[0].previous
    assert [p["what"] for p in previous] == ["item", "weight", "story"]
    assert previous[0]["text"].endswith("(by hand)")
    assert previous[1] == {"what": "weight", "duty": 1, "weight": 3, "why": "my main work", "new": 2}
    assert previous[2]["merged"] == ["e0.s09"]
    assert not any(i.edited for i in again.items if i.experience == 0)
    page = items_markdown(profile, again)
    assert "**Previous hand edits, to re-apply**" in page
    assert "- duty 1: weight set by hand to 3 (my main work), the recompile gave 2" in page


# --- S1 carry-overs ------------------------------------------------------------------------------
def test_a_story_that_lost_its_work_to_drops_is_kept_and_reported():
    """Only a story the reply gave no action or result is dissolved; story 3's one action is dropped."""
    lost = [r for r in PORTING if r["n"] != 7] + [
        raw(7, 2, "action", ["Presented it to the board"], 3),
        raw(8, 2, "context", ["Presented the plan"], 3),
    ]
    entry, items = build_experience(EXP, reply(lost, STORIES), TODAY)
    assert [s.title for s in entry.stories] == ["Billing engine porting to Java", "Plan presentation to management"]
    kept = entry.stories[1]
    assert [next(i.text for i in items if i.id == iid) for iid in kept.items] == ["text 8"]
    assert entry.dropped[0]["story_id"] == kept.id
    problems = validate({"experiences": [EXP]}, Items(profile_sha256="", experiences=[entry], items=items))
    assert f"{kept.id}: no action or result left: its items were dropped" in problems
    # the context-only story 2 is still dissolved
    assert next(i for i in items if i.text == "text 6").story is None


def test_an_edited_weight_is_marked_in_the_view_and_old_files_load():
    store = built()
    old = json.loads(store.model_dump_json())
    for w in old["experiences"][0]["weights"]:
        del w["edited"]
    assert not any(w.edited for w in Items.model_validate(old).experiences[0].weights)
    store.experiences[0].weights[0].edited = True
    page = items_markdown({"experiences": [EXP]}, store)
    assert "- duty 0: **2** · w (edited) — Ported the billing engine" in page
    assert "(edited)" not in page.split("- duty 1:")[1].split("\n")[0]
