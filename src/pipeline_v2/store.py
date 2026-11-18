"""The store's first layer: base items and stories, the checks a valid one passes, its readable view.

The profile's experiences cut into numbered base items (context, action, result), grouped into
stories (S1). Everything later is built from this file, never from the profile. The user edits it
by hand, so it is checked every time it is loaded (:func:`validate`), and :func:`items_markdown`
is what they read to correct it.

The checks here are pure: values in, values out. They live with the shapes because both the
compile (:mod:`.items`) and :func:`validate` use them.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from datetime import date
from typing import Any, Iterable, List, Literal, Mapping, Optional

from pydantic import BaseModel, ConfigDict

#: Hard limits: the prompt states softer ones, and a breach is reported, never truncated.
MAX_TAGS = 6            # soft 1-4
MAX_TITLE_WORDS = 12    # soft 3-8
MAX_ANCHOR = 3          # soft 1-2
MAX_STORY_WORK = 14     # action and result items per story; soft 10

#: English month names, and their three-letter forms.
MONTHS = {
    name: n
    for n, full in enumerate(
        ("january", "february", "march", "april", "may", "june", "july", "august",
         "september", "october", "november", "december"), 1)
    for name in (full, full[:3])
}
DATES = re.compile(
    r"^\s*([a-z]+)\.?\s+(\d{4})\s*(?:--|–|—)\s*(?:([a-z]+)\.?\s+(\d{4})|(present))\s*$", re.I
)


class _Strict(BaseModel):
    """A key the shape does not know is an error: a typo in a hand edit must not vanish."""

    model_config = ConfigDict(extra="forbid")


class Item(_Strict):
    id: str                  # "e0.i07"
    experience: int
    duty: int                # -1 = role_summary
    type: Literal["context", "action", "result"]
    text: str
    source: List[str]        # verbatim spans, checked in code
    agency: Literal["led", "did", "supported", ""] = ""
    scope: str = ""
    tech: List[str] = []
    tags: List[str] = []
    story: Optional[str] = None   # None = experience-level context
    edited: bool = False          # set by hand


class Story(_Strict):
    id: str                  # "e0.s02"
    experience: int
    title: str
    items: List[str]         # context first, then actions and results in profile order
    anchor: List[str]        # 1-2 item ids
    merged: List[str] = []   # former story ids, set by hand


class DutyWeight(_Strict):
    duty: int
    weight: Literal[1, 2, 3]
    why: str                 # a few words, for the gate


class ExperienceItems(_Strict):
    id: int
    sha256: str              # of the experience's profile JSON (sorted keys, no whitespace)
    months: Optional[int]    # from dates; None when unreadable (reported)
    weights: List[DutyWeight]   # one per profile duty
    context: List[str]       # experience-level context item ids
    stories: List[Story]
    coverage: float          # share of the duties' characters under a source span
    dropped: List[dict] = []    # refused items, with the reason
    previous: List[dict] = []   # hand edits of a recompiled experience, to re-apply


class Items(_Strict):
    profile_sha256: str
    experiences: List[ExperienceItems]
    items: List[Item]


# --- pure checks --------------------------------------------------------------
def _sha256(obj: Any) -> str:
    text = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def experience_sha256(exp: Mapping[str, Any]) -> str:
    """SHA-256 of the experience's JSON, sorted keys and no whitespace: what decides a recompile."""
    return _sha256(exp)


def profile_sha256(profile: Mapping[str, Any]) -> str:
    """The same for the whole profile."""
    return _sha256(profile)


def squash(text: str) -> str:
    """Whitespace normalized, for substring checks."""
    return re.sub(r"\s+", " ", text).strip()


def source_text(exp: Mapping[str, Any], duty: int) -> str:
    """The text an item of this duty comes from; "" for a duty the experience does not have."""
    if duty == -1:
        return exp.get("role_summary") or ""
    duties = exp.get("duties") or []
    return duties[duty] if 0 <= duty < len(duties) else ""


def check_sources(exp: Mapping[str, Any], item: Any) -> List[str]:
    """The spans of ``item`` (an :class:`Item`, or anything with ``duty`` and ``source``) that are
    verbatim neither in its duty nor anywhere else in the experience.

    Whitespace does not count. A span from another duty of the same experience passes, as in S0.
    """
    own = squash(source_text(exp, item.duty))
    whole = squash(" ".join([exp.get("role_summary") or "", *(exp.get("duties") or [])]))
    return [s for s in item.source if squash(s) not in own and squash(s) not in whole]


def coverage(exp: Mapping[str, Any], items: Iterable[Item]) -> float:
    """Share of the duties' characters that some source span covers (the role summary does not count)."""
    spans = [squash(s) for item in items for s in item.source if squash(s)]
    covered = total = 0
    for duty in exp.get("duties") or []:
        text = squash(duty)
        mask = [False] * len(text)
        for span in spans:
            at = text.find(span)
            if at >= 0:
                mask[at:at + len(span)] = [True] * len(span)
        covered += sum(mask)
        total += len(text)
    return covered / total if total else 1.0


def months(dates: str, today: date) -> Optional[int]:
    """Months of "Month YYYY -- Month YYYY", both months counted; "present" counts to ``today``.

    None when the dates cannot be read, or end before they start: reported, never guessed.
    """
    found = DATES.match(dates or "")
    if not found:
        return None
    start_month, start_year, end_month, end_year, present = found.groups()
    start = MONTHS.get(start_month.lower())
    if present:
        end, end_year = today.month, today.year
    else:
        end = MONTHS.get(end_month.lower())
    if start is None or end is None:
        return None
    count = (int(end_year) * 12 + end) - (int(start_year) * 12 + start) + 1
    return count if count > 0 else None


def is_work(item: Item) -> bool:
    """An action or a result: what a story is made of."""
    return item.type in ("action", "result")


# --- validation -----------------------------------------------------------------
def validate(profile: Mapping[str, Any], items: Items) -> List[str]:
    """What is wrong with a store, one line each, as ``"<id>: <problem>"``; [] when nothing is.

    Runs on every load of a hand-edited file, after pydantic has checked the shapes. Nothing is
    changed or dropped: a hand edit whose spans no longer match, an anchor outside its story, a
    missing weight or a breached limit is reported and kept.
    """
    problems: List[str] = []
    exps = {exp["id"]: exp for exp in profile["experiences"]}
    compiled = {x.id: x for x in items.experiences}
    problems += [f"e{e}: not compiled" for e in exps if e not in compiled]
    problems += [f"e{e}: not in the profile" for e in compiled if e not in exps]

    by_id = {}
    for item in items.items:
        if item.id in by_id:
            problems.append(f"{item.id}: two items have this id")
        by_id[item.id] = item
        if not item.id.startswith(f"e{item.experience}.i"):
            problems.append(f"{item.id}: the id does not match experience {item.experience}")

    for x in items.experiences:
        exp = exps.get(x.id)
        if exp is None:
            continue
        problems += _check_experience(exp, x, [i for i in items.items if i.experience == x.id])
    return problems


def _check_experience(exp: Mapping[str, Any], x: ExperienceItems, own: List[Item]) -> List[str]:
    """The problems of one experience: hash, dates, weights, placement, stories, items."""
    e = f"e{x.id}"
    problems: List[str] = []
    if x.sha256 != experience_sha256(exp):
        problems.append(f"{e}: the profile's experience changed since it was compiled: recompile it")
    if x.months is None:
        problems.append(f"{e}: dates {exp.get('dates')!r} unreadable: no months")

    n_duties = len(exp.get("duties") or [])
    weighted = Counter(w.duty for w in x.weights)
    problems += [f"{e}: no weight for duty {d}" for d in range(n_duties) if not weighted[d]]
    problems += [f"{e}: {n} weights for duty {d}" for d, n in sorted(weighted.items()) if n > 1]
    problems += [f"{e}: a weight for duty {d}, which the profile does not have"
                 for d in sorted(weighted) if not 0 <= d < n_duties]

    by_id = {i.id: i for i in own}
    stories = {s.id: s for s in x.stories}
    placed = Counter(x.context) + Counter(iid for s in x.stories for iid in s.items)
    for iid in x.context:
        item = by_id.get(iid)
        if item is None:
            problems.append(f"{e}: experience context lists {iid}, which does not exist")
        elif item.story is not None:
            problems.append(f"{iid}: in the experience context, but its story is {item.story}")
        elif item.type != "context":
            problems.append(f"{iid}: in the experience context, but it is an {item.type}")

    for s in x.stories:
        problems += _check_story(s, by_id)

    for item in own:
        if placed[item.id] == 0:
            problems.append(f"{item.id}: in no story and not in the experience context")
        elif placed[item.id] > 1:
            problems.append(f"{item.id}: listed {placed[item.id]} times")
        if item.story is not None and item.story not in stories:
            problems.append(f"{item.id}: story {item.story} does not exist")
        if not -1 <= item.duty < n_duties:
            problems.append(f"{item.id}: duty {item.duty}, which the profile does not have")
        if not item.source:
            problems.append(f"{item.id}: no source")
        bad = check_sources(exp, item)
        if bad:
            problems.append(f"{item.id}: span not verbatim in the profile: {bad}")
        if not 1 <= len(item.tags) <= MAX_TAGS:
            problems.append(f"{item.id}: {len(item.tags)} tags (limit 1-{MAX_TAGS})")
    return problems


def _check_story(s: Story, by_id: Mapping[str, Item]) -> List[str]:
    problems: List[str] = []
    members = [by_id[iid] for iid in s.items if iid in by_id]
    problems += [f"{s.id}: lists {iid}, which does not exist" for iid in s.items if iid not in by_id]
    problems += [f"{i.id}: listed in {s.id}, but its story is {i.story}" for i in members if i.story != s.id]
    work = sum(map(is_work, members))
    if work == 0:
        problems.append(f"{s.id}: no action or result item")
    elif work > MAX_STORY_WORK:
        problems.append(f"{s.id}: {work} action and result items (limit {MAX_STORY_WORK})")
    if not 1 <= len(s.anchor) <= MAX_ANCHOR:
        problems.append(f"{s.id}: anchor of {len(s.anchor)} items (limit 1-{MAX_ANCHOR})")
    problems += [f"{s.id}: anchor {iid} is not in the story" for iid in s.anchor if iid not in s.items]
    words = len(s.title.split())
    if not 1 <= words <= MAX_TITLE_WORDS:
        problems.append(f"{s.id}: title of {words} words (limit {MAX_TITLE_WORDS})")
    return problems


# --- the readable view ------------------------------------------------------------
GATE = """\
# Base items and stories

Read from the top, one experience at a time, looking for these, in order:

1. a duty weight that is wrong for your career;
2. experience context that belongs to one story, or story context that two stories need;
3. a story cut where the profile cuts a duty, or two pieces of work in one story; an anchor that
   does not say what the story is about;
4. a statement the profile does not make, a lost limit on your role (scope), a wrong type or agency.

Correct `items.json` (set `"edited": true` on each item you change), then run `md` to check it and
rebuild this page.
"""


def _first_words(text: str, n: int = 12) -> str:
    words = text.split()
    return " ".join(words[:n]) + (" …" if len(words) > n else "")


def _plain(value: Any) -> str:
    """A profile field as text: a list (a title given several ways) joined with " / "."""
    return " / ".join(map(str, value)) if isinstance(value, list) else str(value or "")


def _where(duty: int) -> str:
    return "role summary" if duty == -1 else f"duty {duty}"


def _item_lines(item: Item, anchor: bool = False) -> List[str]:
    head = ["anchor"] if anchor else []
    head += [item.type] + ([item.agency] if item.agency else []) + [_where(item.duty)]
    if item.edited:
        head.append("edited")
    lines = [f"- **{item.id}** ({', '.join(head)}) {item.text}"]
    if item.scope:
        lines.append(f"  - scope: {item.scope}")
    lines.append(f"  - tags: {', '.join(item.tags) or '-'}"
                 + (f" | tech: {', '.join(item.tech)}" if item.tech else ""))
    return lines


def _previous_line(edit: Mapping[str, Any]) -> str:
    what = edit.get("what")
    if what == "item":
        return f"- item {edit.get('id')} (edited): {edit.get('text')}"
    if what == "weight":
        return (f"- duty {edit.get('duty')}: weight was {edit.get('weight')} ({edit.get('why')}), "
                f"the recompile gave {edit.get('new')}")
    if what == "story":
        return f"- story {edit.get('id')} {edit.get('title')!r} was merged from {edit.get('merged')}"
    return f"- {json.dumps(edit, ensure_ascii=False)}"


def items_markdown(profile: Mapping[str, Any], items: Items) -> str:
    """The gate view: per experience its months, the duty weights, the experience context, each
    story with its anchor marked, then what is wrong, what was dropped and the edits to re-apply."""
    problems = validate(profile, items)
    out = [GATE]
    general = [p for p in problems if not re.match(r"e\d+[.:]", p)]
    if general:
        out += ["**Problems**", "", *[f"- {p}" for p in general], ""]
    compiled = {x.id: x for x in items.experiences}
    by_id = {i.id: i for i in items.items}
    for exp in profile["experiences"]:
        x = compiled.get(exp["id"])
        e = f"e{exp['id']}"
        length = f"{x.months} months" if x and x.months is not None else "dates unreadable"
        out += [f"## {e} · {_plain(exp.get('title'))}, {_plain(exp.get('company'))} · "
                f"{_plain(exp.get('dates'))} · {length}", ""]
        if x is None:
            out += ["Not compiled.", ""]
            continue
        own = [i for i in items.items if i.experience == x.id]
        in_stories = sum(len(s.items) for s in x.stories)
        out += [f"{len(own)} items: {len(x.context)} experience context, {in_stories} in "
                f"{len(x.stories)} stories. {x.coverage:.0%} of the duties' text under a source span. "
                f"{len(x.dropped)} dropped.", ""]

        weights = {w.duty: w for w in x.weights}
        out += ["**Narrative**", ""]
        for d, duty in enumerate(exp.get("duties") or []):
            w = weights.get(d)
            mark = f"**{w.weight}** · {w.why}" if w else "**no weight**"
            out.append(f"- duty {d}: {mark} — {_first_words(duty)}")
        out.append("")

        out += ["**Experience context**", ""]
        for iid in x.context:
            if iid in by_id:
                out += _item_lines(by_id[iid])
        out.append("")

        for s in x.stories:
            out += [f"### {s.id} · {s.title}" + (f" (merged from {', '.join(s.merged)})" if s.merged else ""), ""]
            for iid in s.items:
                if iid in by_id:
                    out += _item_lines(by_id[iid], anchor=iid in s.anchor)
            out.append("")

        mine = [p for p in problems if re.match(rf"{e}[.:]", p)]
        if mine:
            out += ["**Problems**", "", *[f"- {p}" for p in mine], ""]
        if x.dropped:
            out += ["**Dropped**", ""]
            out += [f"- ({d.get('reason')}) {d.get('text')}" for d in x.dropped]
            out.append("")
        if x.previous:
            out += ["**Previous hand edits, to re-apply**", "", *map(_previous_line, x.previous), ""]
    return "\n".join(out).rstrip() + "\n"


__all__ = [
    "DutyWeight",
    "ExperienceItems",
    "Item",
    "Items",
    "Story",
    "check_sources",
    "coverage",
    "experience_sha256",
    "items_markdown",
    "months",
    "profile_sha256",
    "validate",
]
