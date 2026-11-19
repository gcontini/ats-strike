"""The store's third layer: the pool, everything a CV prints from the experiences, written once.

Per story a ladder of bullets at 1, 2 and 3 printed lines in two wordings each, and for a long
story up to three groups; per experience a role summary and a compact line; for the profile, skill
phrases and summary frames (S3). Code picks from it per job later; here nothing is chosen.

The file holds the texts and what each one rests on, by item id: ``uses`` (every item it takes a
fact from), ``covers`` (the action and result items whose whole statement the reader learns) and
``assumes`` (items it leans on without stating them). What follows from them — each piece's lines,
leaves, terms and narrative weight, and the checks — is recomputed on every load, so a hand edit
of the tree or of a bullet is current at the next view.

Pure: values in, values out.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from datetime import date
from typing import Any, Dict, Iterable, List, Literal, Mapping, Optional, Sequence, Tuple

from .store import DATES, MONTHS, Item, Items, _plain, _sha256, _Strict, is_work, squash
from .terms import has_term
from .tree_store import Node, Tree, leaf_links, leaf_status, level, terms_of

#: Characters a printed line holds: a bullet's, and a paragraph's (role summary, compact line).
#: Measured on resources/v2/resume.tex.jinja by S7 (2026-11-19): 96 miscounts 1 of the pool's 165
#: bullets, where 98 counted 3 a line short.
BULLET_LINE = 96
ROLE_LINE = 100
#: Accepted lengths, whole lines; the prompts state a little less (95; 130-190; 225-285; 180; 95; 285).
RUNG_MAX = {k: k * BULLET_LINE for k in (1, 2, 3)}
ROLE_SUMMARY_MAX = 2 * ROLE_LINE
COMPACT_MAX = 98
GROUP_BULLET_MAX = 3 * BULLET_LINE
#: A group: 2 or 3 bullets, 4 to 7 printed lines, 9 for a merged story.
GROUP_BULLETS = (2, 3)
GROUP_LINES = (4, 7)
MERGED_GROUP_LINES = 9
#: A story with this many action and result items is long: only a long story gets groups.
LONG = 5
MAX_GROUPS = 3
SKILL_MAX = 30
SKILL_WORDS = 4
SKILLS = (25, 40)
FRAMES = (4, 6)
#: A frame's fixed text, slots left out; the prompt states 230 to 280.
FRAME_MAX = 300
SLOTS = ("<<job_title>>", "<<keywords>>", "<<sentence>>")

LED = re.compile(r"\b(led|managed|headed)\b", re.I)
_WORDS = {
    w: n
    for n, w in enumerate(
        "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
        "sixteen seventeen eighteen nineteen twenty".split()
    )
}
_NUMBER = re.compile(r"(?<![A-Za-z0-9.,])\d+(?:[.,]\d+)*|\b(?:" + "|".join(_WORDS) + r")\b", re.I)
_YEARS = re.compile(r"(?<![A-Za-z0-9.,])(\d+|" + "|".join(_WORDS) + r")\+?\s+years?\b", re.I)

#: The checks, in the order the view lists them.
CHECKS = (
    "length",
    "lines",
    "rung1",
    "foreign_items",
    "numbers",
    "led",
    "context_only",
    "covers",
    "assumes",
    "anchor",
    "wordings",
    "groups",
    "uncovered",
    "role_summary",
    "compact",
    "skills",
    "skill_count",
    "frames",
    "frame_count",
    "stale",
)


# --- shapes -------------------------------------------------------------------------------
class Line(_Strict):
    """A role summary or a compact line."""

    text: str
    uses: List[str]
    edited: bool = False  # set by hand


class Bullet(_Strict):
    id: str  # "e1.s03.r2.a" on a ladder, "e1.s03.g1.2" in a group
    text: str
    uses: List[str]
    covers: List[str]
    assumes: List[str] = []
    edited: bool = False  # set by hand


class Rung(Bullet):
    rung: Literal[1, 2, 3]
    wording: Literal["a", "b", "c", "d", "e", "f"]  # the model writes a and b; code moves a bullet
    # that fills fewer lines down a rung, as c, d, ...


class Group(_Strict):
    id: str  # "e1.s03.g1"
    bullets: List[Bullet]  # printed whole, in this order


class StoryPool(_Strict):
    story: str
    ladder: List[Rung]
    groups: List[Group] = []


class ExperiencePool(_Strict):
    experience: int
    items_sha256: str  # shown_sha256 of the items and stories it was written from
    role_summary: Line
    compact: Line
    stories: List[StoryPool]
    dropped: List[dict] = []  # groups code refused, with the reason
    previous: List[dict] = []  # hand-edited pieces of a block written again


class Skill(_Strict):
    id: str  # "k01"
    text: str
    nodes: List[str]
    edited: bool = False  # set by hand


class Frame(_Strict):
    id: str  # "f1"
    family: str
    text: str  # with the three slots
    uses: List[str]
    edited: bool = False  # set by hand


class Pool(_Strict):
    level_sha256: str  # what the skills and frames were written from
    experiences: List[ExperiencePool]
    skills: List[Skill]
    frames: List[Frame]


# --- derived values -------------------------------------------------------------------------
def lines(text: str, width: int = BULLET_LINE) -> int:
    """Printed lines: ``ceil(chars / width)``; pass :data:`ROLE_LINE` for a role summary."""
    return math.ceil(len(text) / width)


def item_leaves(tree: Tree, items: Items) -> Dict[str, Dict[str, int]]:
    """item id -> {leaf id: level}, a context item's links already clamped to 1 (S2's ``leaf_links``)."""
    out: Dict[str, Dict[str, int]] = {}
    for leaf, linked in leaf_links(tree, items).items():
        for iid, lvl in linked.items():
            out.setdefault(iid, {})[leaf] = lvl
    return out


def piece_nodes(uses: Iterable[str], leaves: Mapping[str, Mapping[str, int]]) -> Dict[str, int]:
    """The leaves of the items a piece uses, each at the highest level among them."""
    out: Dict[str, int] = {}
    for iid in uses:
        for leaf, lvl in leaves.get(iid, {}).items():
            out[leaf] = max(lvl, out.get(leaf, 0))
    return dict(sorted(out.items(), key=lambda p: [int(x) for x in p[0].split(".") if x.isdigit()]))


def piece_terms(text: str, nodes: Iterable[Node]) -> List[str]:
    """The tree's names, aliases and counts-as terms the text holds, as the tree spells them."""
    found = {t for node in nodes for t in terms_of(node) if has_term(t, text)}
    return sorted(found, key=str.lower)


def item_weights(items: Items) -> Dict[str, int]:
    """item id -> the weight of the duty it comes from; an item of the role summary has none."""
    weight = {(x.id, w.duty): w.weight for x in items.experiences for w in x.weights}
    return {i.id: weight[(i.experience, i.duty)] for i in items.items if (i.experience, i.duty) in weight}


def piece_weight(covers: Iterable[str], weights: Mapping[str, int]) -> int:
    """The narrative weight of a piece: the highest weight among the duties of the items it covers; 0
    when it covers none."""
    return max((weights[iid] for iid in covers if iid in weights), default=0)


def numbers(text: str) -> List[Tuple[str, str]]:
    """Every number of ``text``, in digits or as a word up to twenty: (as written, its value)."""
    out = []
    for m in _NUMBER.finditer(text):
        written = m.group()
        out.append((written, str(_WORDS[written.lower()]) if written.lower() in _WORDS else written))
    return out


def _values(text: str) -> set:
    return {value for _, value in numbers(text)}


def _month_span(dates: str, today: date) -> Optional[Tuple[int, int]]:
    """The first and last month of "Month YYYY -- Month YYYY" as year * 12 + month; None if unreadable."""
    found = DATES.match(dates or "")
    if not found:
        return None
    start_month, start_year, end_month, end_year, present = found.groups()
    start = MONTHS.get(start_month.lower())
    end = today.month if present else MONTHS.get((end_month or "").lower())
    if start is None or end is None:
        return None
    first, last = int(start_year) * 12 + start, (today.year if present else int(end_year)) * 12 + end
    return (first, last) if last >= first else None


def career_years(profile: Mapping[str, Any], today: date) -> int:
    """Whole years from the earliest start to the latest end, "present" counting to ``today``."""
    spans = [s for s in (_month_span(_plain(e.get("dates")), today) for e in profile["experiences"]) if s]
    if not spans:
        return 0
    return (max(s[1] for s in spans) - min(s[0] for s in spans) + 1) // 12


def shown_sha256(items: Items, experience: int) -> str:
    """What decides a rewrite of an experience's block: what its calls are shown of its items (each
    one's id, type, text, agency, scope and technologies) and of its stories (id, title, items and
    anchor). A changed scope, or a story cut again from the same texts, writes the block again."""
    x = next((x for x in items.experiences if x.id == experience), None)
    return _sha256(
        [
            [[i.id, i.type, i.text, i.agency, i.scope, i.tech] for i in items.items if i.experience == experience],
            [[s.id, s.title, s.items, s.anchor] for s in (x.stories if x else [])],
        ]
    )


def level_sha256(items: Items, tree: Tree) -> str:
    """What the skill phrases and frames are written from: every item, and the proven leaf ids."""
    status = leaf_status(tree, items)
    proven = sorted(n.id for n in tree.nodes if level(n.id) == 3 and status[n.id] == "proven")
    return _sha256([[[i.id, i.type, i.text, i.agency, i.scope, i.tech] for i in items.items], proven])


def fixed_text(frame: Frame) -> str:
    """A frame's text without its slots: what its length limit counts."""
    text = frame.text
    for slot in SLOTS:
        text = text.replace(slot, "")
    return squash(text)


def frame_numbers(
    frame: Frame, items: Items, profile: Mapping[str, Any], today: date
) -> List[Tuple[str, Optional[str]]]:
    """Every number of a frame's fixed text, with where code found it; None where it found it nowhere.

    A number passes when it is in an item the frame uses, in an experience's dates, or is a count of
    years no larger than the career's whole years.
    """
    by_id = {i.id: i for i in items.items}
    career = career_years(profile, today)
    text = fixed_text(frame)
    years = {str(_WORDS.get(m.group(1).lower(), m.group(1))) for m in _YEARS.finditer(text)}
    out: List[Tuple[str, Optional[str]]] = []
    for written, value in numbers(text):
        where = next(
            (f"item {iid}" for iid in frame.uses if iid in by_id and value in _values(_source(by_id[iid]))), None
        )
        if where is None:
            where = next(
                (f"dates of e{e['id']}" for e in profile["experiences"] if value in _values(_plain(e.get("dates")))),
                None,
            )
        if where is None and value in years and value.isdigit() and int(value) <= career:
            where = f"a count of years, the career has {career}"
        out.append((written, where))
    return out


def _source(item: Item) -> str:
    return " ".join([item.text, item.scope, *item.tech])


# --- what a pool holds -------------------------------------------------------------------------
def story_work(items: Items) -> Dict[str, int]:
    """story id -> its action and result items."""
    by_id = {i.id: i for i in items.items}
    return {
        s.id: sum(is_work(by_id[iid]) for iid in s.items if iid in by_id) for x in items.experiences for s in x.stories
    }


def ladder_and_group_bullets(block: ExperiencePool) -> List[Bullet]:
    return [b for s in block.stories for b in [*s.ladder, *(x for g in s.groups for x in g.bullets)]]


def settle_ladder(story: str, ladder: Sequence[Rung]) -> Tuple[List[Rung], List[Tuple[str, str]]]:
    """The ladder with every bullet that fills fewer lines than its rung moved down to the rung it
    fills, as the first free wording there after that rung's own (c, d, ...), its id following;
    sorted by rung and wording. Also each move, (old id, new id)."""
    kept = [r for r in ladder if lines(r.text) >= r.rung]
    moves: List[Tuple[str, str]] = []
    for r in sorted((r for r in ladder if lines(r.text) < r.rung), key=lambda r: (r.rung, r.wording)):
        rung = lines(r.text)
        wording = next(w for w in "abcdef" if all((x.rung, x.wording) != (rung, w) for x in kept))
        moved = r.model_copy(update={"id": f"{story}.r{rung}.{wording}", "rung": rung, "wording": wording})
        kept.append(moved)
        moves.append((r.id, moved.id))
    return sorted(kept, key=lambda x: (x.rung, x.wording)), moves


# --- checks --------------------------------------------------------------------------------------
def pool_checks(pool: Pool, items: Items, tree: Tree, profile: Mapping[str, Any], today: date) -> Dict[str, List[dict]]:
    """What the gate should look at: each check a list of ``{"id", "detail"}``, empty when nothing
    is found. Nothing is changed."""
    found: Dict[str, List[dict]] = {k: [] for k in CHECKS}

    def add(check: str, pid: str, detail: str) -> None:
        found[check].append({"id": pid, "detail": detail})

    by_id = {i.id: i for i in items.items}
    exps = {x.id: x for x in items.experiences}
    stories = {s.id: s for x in items.experiences for s in x.stories}
    headers = {
        e["id"]: " ".join(_plain(e.get(k)) for k in ("title", "company", "dates")) for e in profile["experiences"]
    }
    work = story_work(items)

    def grounded(
        pid: str,
        e: int,
        text: str,
        uses: Sequence[str],
        covers: Sequence[str] = (),
        assumes: Sequence[str] = (),
        limit: Optional[int] = None,
    ) -> None:
        """The checks every piece written from one experience's items shares."""
        if limit is not None and len(text) > limit:
            add("length", pid, f"{len(text)} characters, over {limit}")
        foreign = sorted({i for i in [*uses, *covers, *assumes] if i not in by_id or by_id[i].experience != e})
        if foreign:
            add("foreign_items", pid, "not an item of this experience: " + ", ".join(foreign))
        used = [by_id[i] for i in uses if i in by_id and by_id[i].experience == e]
        source = _values(" ".join([headers.get(e, ""), *map(_source, used)]))
        strange = [w for w, v in numbers(text) if v not in source]
        if strange:
            add("numbers", pid, "not in its items or the header: " + ", ".join(strange))
        led = LED.search(text)
        if led and not any(i.agency == "led" for i in used):
            add("led", pid, f'"{led.group()}" with no item of agency led')
        wrong = sorted(set(covers) - set(uses))
        if wrong:
            add("covers", pid, "covers what it does not use: " + ", ".join(wrong))
        context = sorted(i for i in covers if i in by_id and by_id[i].type == "context")
        if context:
            add("covers", pid, "covers a context item: " + ", ".join(context))
        both = sorted(set(assumes) & set(uses))
        if both:
            add("assumes", pid, "assumes what it uses: " + ", ".join(both))

    def told(pid: str, uses: Sequence[str]) -> None:
        if not any(i in by_id and is_work(by_id[i]) for i in uses):
            add("context_only", pid, "uses no action or result item")

    def may_assume(pid: str, assumes: Sequence[str], allowed: set, whose: str) -> None:
        extra = sorted(set(assumes) - allowed)
        if extra:
            add("assumes", pid, f"assumes what {whose} does not state: " + ", ".join(extra))

    def names_anchor(pid: str, story: str, uses: Sequence[str], assumes: Sequence[str]) -> None:
        anchor = stories[story].anchor if story in stories else []
        if anchor and not set(anchor) & set([*uses, *assumes]):
            add("anchor", pid, "uses or assumes no anchor item (" + ", ".join(anchor) + ")")

    covered: set = set()
    for block in pool.experiences:
        e = block.experience
        x = exps.get(e)
        if x is None:
            continue
        if block.items_sha256 != shown_sha256(items, e):
            add("stale", f"e{e}", f"its items or stories changed since it was written: run with --experiences {e}")

        role = block.role_summary
        grounded(f"e{e}.role", e, role.text, role.uses, limit=ROLE_SUMMARY_MAX)
        missing = [i for i in x.context if i not in role.uses]
        if missing:
            add("role_summary", f"e{e}.role", "misses the experience context " + ", ".join(missing))
        actions = [i for i in role.uses if i in by_id and is_work(by_id[i])]
        if actions:
            add("role_summary", f"e{e}.role", "uses an action or result: " + ", ".join(actions))
        stated = set(role.uses)

        compact = block.compact
        grounded(f"e{e}.compact", e, compact.text, compact.uses, limit=COMPACT_MAX)
        if not any(i in by_id and by_id[i].type == "action" for i in compact.uses):
            add("compact", f"e{e}.compact", "uses no action item")
        covered |= {i for i in compact.uses if i in by_id and is_work(by_id[i])}

        for s in block.stories:
            for r in s.ladder:
                grounded(r.id, e, r.text, r.uses, r.covers, r.assumes, RUNG_MAX[r.rung])
                if len(r.text) <= RUNG_MAX[r.rung] and lines(r.text) != r.rung:
                    add("lines", r.id, f"{len(r.text)} characters fill {lines(r.text)} line(s), not {r.rung}")
                told(r.id, r.uses)
                may_assume(r.id, r.assumes, stated, "the role summary")
                names_anchor(r.id, s.story, r.uses, r.assumes)
                covered |= set(r.covers)
            if not any(r.rung == 1 for r in s.ladder):
                add("rung1", s.story, "no rung 1")
            for rung in (1, 2, 3):
                same = [r for r in s.ladder if r.rung == rung]
                for k, x in enumerate(same):
                    for y in same[k + 1 :]:
                        if _first_word(x.text) == _first_word(y.text):
                            add(
                                "wordings",
                                f"{s.story}.r{rung}",
                                f'{x.wording} and {y.wording} both open with "{_first_word(x.text)}"',
                            )

            most = MERGED_GROUP_LINES if stories.get(s.story) and stories[s.story].merged else GROUP_LINES[1]
            for g in s.groups:
                size, total = len(g.bullets), sum(lines(b.text) for b in g.bullets)
                if not GROUP_BULLETS[0] <= size <= GROUP_BULLETS[1]:
                    add("groups", g.id, f"{size} bullets, not {GROUP_BULLETS[0]} or {GROUP_BULLETS[1]}")
                if not GROUP_LINES[0] <= total <= most:
                    add("groups", g.id, f"{total} lines, outside {GROUP_LINES[0]} to {most}")
                earlier: set = set()
                for k, b in enumerate(g.bullets):
                    grounded(b.id, e, b.text, b.uses, b.covers, b.assumes, GROUP_BULLET_MAX)
                    told(b.id, b.uses)
                    may_assume(b.id, b.assumes, stated | earlier, "the role summary or an earlier bullet")
                    if k == 0:
                        names_anchor(b.id, s.story, b.uses, b.assumes)
                    earlier |= set(b.uses)
                    covered |= set(b.covers)

    written = {b.experience for b in pool.experiences}
    for item in items.items:
        if item.experience in written and is_work(item) and item.id not in covered:
            add("uncovered", item.id, "no bullet or compact line covers it")

    status = leaf_status(tree, items)
    nodes = {n.id: n for n in tree.nodes}
    for k in pool.skills:
        if len(k.text) > SKILL_MAX:
            add("length", k.id, f"{len(k.text)} characters, over {SKILL_MAX}")
        if len(k.text.split()) > SKILL_WORDS:
            add("length", k.id, f"{len(k.text.split())} words, over {SKILL_WORDS}")
        if not k.nodes:
            add("skills", k.id, "names no leaf")
        off = [n for n in k.nodes if n in nodes and (level(n) != 3 or status[n] != "proven")]
        if off:
            add("skills", k.id, "not a proven leaf: " + ", ".join(f"{n} {nodes[n].name} ({status[n]})" for n in off))
        known = [nodes[n] for n in k.nodes if n in nodes]
        if known and not any(has_term(t, k.text) for node in known for t in terms_of(node, counts_as=False)):
            add("skills", k.id, "holds the name or an alias of none of its nodes")
    if not SKILLS[0] <= len(pool.skills) <= SKILLS[1]:
        add("skill_count", "skills", f"{len(pool.skills)} skill phrases, not {SKILLS[0]} to {SKILLS[1]}")

    for f in pool.frames:
        for slot in SLOTS:
            if f.text.count(slot) != 1:
                add("frames", f.id, f"{slot} {f.text.count(slot)} times")
        if len(fixed_text(f)) > FRAME_MAX:
            add("frames", f.id, f"{len(fixed_text(f))} characters of fixed text, over {FRAME_MAX}")
        unknown = sorted(i for i in f.uses if i not in by_id)
        if unknown:
            add("foreign_items", f.id, "not an item: " + ", ".join(unknown))
        strange = [w for w, where in frame_numbers(f, items, profile, today) if where is None]
        if strange:
            add("numbers", f.id, "not in its items, the dates or the career's years: " + ", ".join(strange))
        led = LED.search(f.text)
        if led and not any(by_id[i].agency == "led" for i in f.uses if i in by_id):
            add("led", f.id, f'"{led.group()}" with no item of agency led')
    if not FRAMES[0] <= len(pool.frames) <= FRAMES[1]:
        add("frame_count", "frames", f"{len(pool.frames)} frames, not {FRAMES[0]} to {FRAMES[1]}")

    if (pool.skills or pool.frames) and pool.level_sha256 != level_sha256(items, tree):
        add("stale", "skills and frames", "written before the items or the tree changed: run with --profile")
    return found


def _first_word(text: str) -> str:
    words = re.findall(r"[\w'-]+", text.lower())
    return words[0] if words else ""


# --- validation ------------------------------------------------------------------------------------
def validate_pool(pool: Pool, items: Items, tree: Tree) -> List[str]:
    """What is wrong with a (hand-edited) pool's structure, one line each; [] when nothing is.

    Runs on every load, after pydantic has checked the shapes. Nothing is changed or dropped.
    """
    problems: List[str] = []
    known = {x.id: [s.id for s in x.stories] for x in items.experiences}
    blocks = Counter(b.experience for b in pool.experiences)
    problems += [f"e{e}: written twice" for e, n in sorted(blocks.items()) if n > 1]
    problems += [f"e{e}: not written" for e in known if e not in blocks]

    ids: List[str] = []
    for block in pool.experiences:
        e = block.experience
        if e not in known:
            problems.append(f"e{e}: an experience the items do not have")
            continue
        listed = Counter(s.story for s in block.stories)
        problems += [f"{sid}: written twice" for sid, n in listed.items() if n > 1]
        problems += [f"{sid}: not a story of e{e}" for sid in listed if sid not in known[e]]
        problems += [f"{sid}: no pool" for sid in known[e] if sid not in listed]
        for s in block.stories:
            for r in s.ladder:
                ids.append(r.id)
                place = f"{s.story}.r{r.rung}.{r.wording}"
                if r.id != place:
                    problems.append(f"{r.id}: does not match its place ({place})")
            for j, g in enumerate(s.groups, 1):
                ids.append(g.id)
                if g.id != f"{s.story}.g{j}":
                    problems.append(f"{g.id}: does not match its place ({s.story}.g{j})")
                for i, b in enumerate(g.bullets, 1):
                    ids.append(b.id)
                    if b.id != f"{g.id}.{i}":
                        problems.append(f"{b.id}: does not match its place ({g.id}.{i})")
    ids += [k.id for k in pool.skills] + [f.id for f in pool.frames]
    problems += [f"{i}: two pieces have this id" for i, n in Counter(ids).items() if n > 1]

    nodes = {n.id for n in tree.nodes}
    for k in pool.skills:
        problems += [f"{k.id}: node {n} does not exist" for n in k.nodes if n not in nodes]
    return problems


# --- the readable view -----------------------------------------------------------------------------
GATE = """\
# Pool

Read the **To check** part, in this order (about 15 minutes):

1. the role summaries: does each introduce the job with its shared context, and tell no action?
2. the groups of the two longest stories: do the views differ, does each open with what the story
   is about, does a later bullet lean only on an earlier one?
3. the summary frames, with every number and where code found it;
4. the skill phrases: anything you would not claim;
5. the code-check failures: fix by hand now, or leave for S4.

Correct `pool.json` (set `"edited": true` on each piece you change), then run `md` to check it and
rebuild this page. The wordings `a` and `b` of a rung are alternatives; a group is printed whole.
"""


def _ids(ids: Iterable[str]) -> str:
    return ", ".join(ids) or "-"


def pool_markdown(pool: Pool, items: Items, tree: Tree, profile: Mapping[str, Any], today: date) -> str:
    """The gate view: counts, then what needs a decision in the gate's order, then the pool."""
    found = pool_checks(pool, items, tree, profile, today)
    problems = validate_pool(pool, items, tree)
    by_id = {i.id: i for i in items.items}
    exps = {x.id: x for x in items.experiences}
    stories = {s.id: s for x in items.experiences for s in x.stories}
    headers = {e["id"]: e for e in profile["experiences"]}
    nodes = {n.id: n for n in tree.nodes}
    leaves = item_leaves(tree, items)
    weights = item_weights(items)
    work = story_work(items)
    faults: Dict[str, List[str]] = {}
    for check in CHECKS:
        for entry in found[check]:
            faults.setdefault(entry["id"], []).append(f"{check}: {entry['detail']}")

    def head(e: int) -> str:
        exp = headers.get(e, {})
        return f"e{e} · {_plain(exp.get('title'))}, {_plain(exp.get('company'))} · {_plain(exp.get('dates'))}"

    def item_line(iid: str, anchor: bool = False) -> str:
        item = by_id.get(iid)
        if item is None:
            return f"> **{iid}** (unknown item)  "
        what = (["anchor"] if anchor else []) + [item.type] + ([item.agency] if item.agency else [])
        return (
            f"> **{iid}** ({', '.join(what)}) {item.text}" + (f" _(scope: {item.scope})_" if item.scope else "") + "  "
        )

    def check_lines(pid: str, pad: str = "  ") -> List[str]:
        return [f"{pad}- CHECK {f}" for f in faults.get(pid, [])]

    def leaf_text(uses: Sequence[str]) -> str:
        found_nodes = piece_nodes(uses, leaves)
        return ", ".join(f"{nodes[n].name if n in nodes else n} {lvl}" for n, lvl in found_nodes.items()) or "-"

    def bullet_lines(b: Bullet) -> List[str]:
        size = f"{lines(b.text)} line{'s' if lines(b.text) > 1 else ''}, {len(b.text)} ch"
        weight = piece_weight(b.covers, weights)
        size += f", weight {weight}" if weight else ""
        size += ", edited" if b.edited else ""
        out = [
            f"- **{b.id}** ({size}) {b.text}",
            f"  - uses {_ids(b.uses)} · covers {_ids(b.covers)} · assumes {_ids(b.assumes)} · "
            f"leaves: {leaf_text(b.uses)}",
        ]
        return out + check_lines(b.id)

    def group_lines(story: str, g: Group) -> List[str]:
        merged = bool(stories.get(story) and stories[story].merged)
        out = [
            f"Group **{g.id}**, {sum(lines(b.text) for b in g.bullets)} lines"
            + (" (merged story: up to 9)" if merged else "")
            + ", printed together:",
            "",
        ]
        out += check_lines(g.id, "")
        for b in g.bullets:
            out += bullet_lines(b)
        return out + [""]

    bullets = [b for block in pool.experiences for b in ladder_and_group_bullets(block)]
    in_groups = sum(len(g.bullets) for block in pool.experiences for s in block.stories for g in s.groups)
    n_groups = sum(len(s.groups) for block in pool.experiences for s in block.stories)
    rungs = Counter(r.rung for block in pool.experiences for s in block.stories for r in s.ladder)
    failures = sum(map(len, found.values()))
    out = [
        GATE,
        f"{len(bullets)} bullets: {len(bullets) - in_groups} on ladders (rung 1 {rungs[1]}, rung 2 {rungs[2]}, "
        f"rung 3 {rungs[3]}), {in_groups} in {n_groups} groups; {len(pool.experiences)} role summaries and "
        f"compact lines, {len(pool.skills)} skill phrases, {len(pool.frames)} summary frames. "
        f"{failures} code-check failure(s).",
        "",
    ]
    if problems:
        out += ["**Problems**", "", *[f"- {p}" for p in problems], ""]

    out += ["## To check", "", "### 1. Role summaries", ""]
    for block in pool.experiences:
        x, role = exps.get(block.experience), block.role_summary
        out += [
            f"**{head(block.experience)}**",
            "",
            f"- role summary ({lines(role.text, ROLE_LINE)} lines, {len(role.text)} ch"
            + (", edited" if role.edited else "")
            + f"): {role.text}",
            f"  - states {_ids(role.uses)}; the experience context is {_ids(x.context if x else [])}",
        ]
        out += check_lines(f"e{block.experience}.role")
        out += [""] + [item_line(i) for i in (x.context if x else [])] + [""]

    longest = sorted((s for block in pool.experiences for s in block.stories), key=lambda s: -work.get(s.story, 0))[:2]
    out += ["### 2. Groups of the two longest stories", ""]
    for s in longest:
        story = stories.get(s.story)
        out += [f"**{s.story} · {story.title if story else '?'}** ({work.get(s.story, 0)} action and result items)", ""]
        out += [item_line(i, i in story.anchor) for i in story.items] if story else []
        out.append("")
        if not s.groups:
            out += ["No group written.", ""]
        for g in s.groups:
            out += group_lines(s.story, g)

    out += ["### 3. Summary frames", ""]
    career = career_years(profile, today)
    out += [f"The career: {career} whole years.", ""]
    for f in pool.frames:
        out.append(
            f"- **{f.id}** · {f.family} ({len(fixed_text(f))} ch of fixed text"
            + (", edited" if f.edited else "")
            + f"): {f.text}"
        )
        found_numbers = frame_numbers(f, items, profile, today)
        out.append(
            "  - numbers: " + ("; ".join(f'"{w}" ({where or "found nowhere"})' for w, where in found_numbers) or "none")
        )
        out.append(f"  - uses {_ids(f.uses)}")
        out += check_lines(f.id)
    out.append("")

    out += [f"### 4. Skill phrases ({len(pool.skills)})", ""]
    for k in pool.skills:
        named = ", ".join(f"{n} {nodes[n].name}" if n in nodes else f"{n} (unknown)" for n in k.nodes) or "no leaf"
        out.append(f"- **{k.id}** {k.text} — {named}" + (" (edited)" if k.edited else ""))
        out += check_lines(k.id)
    out.append("")

    out += [f"### 5. Code-check failures ({failures})", ""]
    for check in CHECKS:
        if found[check]:
            out += [f"**{check}** ({len(found[check])})", ""]
            out += [f"- {entry['id']}: {entry['detail']}" for entry in found[check]]
            out.append("")
    if not failures:
        out += ["Nothing found.", ""]

    other: List[str] = []
    for block in pool.experiences:
        other += [
            f"- e{block.experience}: dropped {d.get('what', 'piece')} of {d.get('story')} ({d.get('reason')}): "
            f"{d.get('text', '')}"
            for d in block.dropped
        ]
        other += [
            f"- e{block.experience}: hand-edited {d.get('what')} before the rewrite"
            + (f" {d['id']}" if d.get("id") else "")
            + f": {d.get('text')}"
            for d in block.previous
        ]
    if other:
        out += ["### Other", "", *other, ""]

    out += ["## The pool", ""]
    for block in pool.experiences:
        e, x = block.experience, exps.get(block.experience)
        out += [f"## {head(e)}" + (f" · {x.months} months" if x and x.months else ""), ""]
        role, compact = block.role_summary, block.compact
        out += [
            f"- role summary ({lines(role.text, ROLE_LINE)} lines, {len(role.text)} ch): {role.text}",
            f"  - uses {_ids(role.uses)}",
        ]
        out += check_lines(f"e{e}.role")
        out += [
            f"- compact ({len(compact.text)} ch, weight {piece_weight(compact.uses, weights)}): {compact.text}",
            f"  - uses {_ids(compact.uses)} · leaves: {leaf_text(compact.uses)}",
        ]
        out += check_lines(f"e{e}.compact")
        out.append("")
        for s in block.stories:
            story = stories.get(s.story)
            marks = [
                m
                for m in ("LONG" if work.get(s.story, 0) >= LONG else "", "MERGED" if story and story.merged else "")
                if m
            ]
            out += [
                f"### {s.story} · {story.title if story else '?'}" + (f" ({', '.join(marks)})" if marks else ""),
                "",
            ]
            out += [item_line(i, i in story.anchor) for i in story.items] if story else []
            out.append("")
            out += check_lines(s.story, "")
            for rung in (1, 2, 3):
                out += check_lines(f"{s.story}.r{rung}", "")
            for r in s.ladder:
                out += bullet_lines(r)
            out.append("")
            for g in s.groups:
                out += group_lines(s.story, g)
    return "\n".join(out).rstrip() + "\n"


def _dump(value: Any, pad: str) -> str:
    """JSON with one piece per line: a dict holding a ``text`` is written on one line."""
    if isinstance(value, dict) and value and "text" not in value:
        inner = ",\n".join(f"{pad} {json.dumps(k)}: {_dump(v, pad + ' ')}" for k, v in value.items())
        return "{\n" + inner + "\n" + pad + "}"
    if isinstance(value, list) and any(isinstance(v, (dict, list)) for v in value):
        inner = ",\n".join(f"{pad} {_dump(v, pad + ' ')}" for v in value)
        return "[\n" + inner + "\n" + pad + "]"
    return json.dumps(value, ensure_ascii=False)


def pool_dump(pool: Pool) -> str:
    """The file's text: one piece per line, so a hand edit and a diff stay readable."""
    return _dump(pool.model_dump(), "") + "\n"


__all__ = [
    "Bullet",
    "CHECKS",
    "ExperiencePool",
    "Frame",
    "Group",
    "Line",
    "Pool",
    "Rung",
    "Skill",
    "StoryPool",
    "career_years",
    "fixed_text",
    "frame_numbers",
    "item_leaves",
    "item_weights",
    "level_sha256",
    "lines",
    "numbers",
    "piece_nodes",
    "piece_terms",
    "piece_weight",
    "pool_checks",
    "pool_dump",
    "pool_markdown",
    "settle_ladder",
    "shown_sha256",
    "story_work",
    "validate_pool",
]
