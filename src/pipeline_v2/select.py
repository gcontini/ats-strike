"""The selection for one job (step 6): which experiences are full or compact, which story at which
rung or group, the skills, the summary frame and its keywords, the headline, and the scores.

No model call. The S0 probe's selector, ported to the pool (S3) and the allocation (S5):

- **ATS** = ``LITERAL_WEIGHT`` x the literal score (the requirements' ``keywords`` in the CV's
  text) + the rest x the tree score (each node's points earned by the evidence printed for it).
- **Narrative** (the human score): a job on the page tells ``PRESENCE`` of its years in either
  form (its role summary or its compact line); its duties tell the rest. A job's years, from its
  months, are shared by its duties by points (weight 1, 2, 3 counts 1, 3, 9 points; a duty with no
  action or result item is left out); a duty tells its share times the share of its action and
  result items its bullets print (a bullet's ``covers``; a compact line tells no duty), each item
  at the depth of the deepest bullet telling it (:data:`DEPTH`: a story's top rung or a group tells
  it whole, a rung below less). The score is the years told over the career's years. In both,
  each month of a job counts ``0.5 ** (its age / HALF_LIFE_YEARS)``, its age taken from the CV's
  date (:func:`recency`): a job long ago weighs less however long it lasted.
- **Objective** = ``ATS_WEIGHT`` x ATS + the rest x narrative. The mix is code, not a setting.

The greedy starts from the most recent experience in full with its two best slots (when it has two
stories; neither the refill nor :func:`fit` takes it below two bullets) and takes the move with the
best gain per line while one fits: when no move improves the objective, the one that loses least
per line still fills the page. Every other experience, the earliest too, comes in by a move, so a
job that earns too little is left out. An experience opens with its two best slots. Then the
refill pass: drop the weakest slot, run the greedy again, keep the result when the objective is
higher, stop at the first try that is not.

A slot's text for scoring is its wording ``a`` (the first wording of its rung); its lines are the
longest wording's, so any wording fits. Groups are a long story's alternatives above its top rung.

Pure: values in, values out.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable, Dict, FrozenSet, List, Mapping, Optional, Sequence, Tuple, Union

from .allocate import Allocation, Requirement, points
from .pool_store import ROLE_LINE, Bullet, Frame, Line, Pool, Skill, _month_span, item_leaves, lines, piece_nodes
from .store import Items, _plain, _Strict, is_work
from .terms import count_term
from .tree_store import Tree, leaf_status, level, parent

# --- the page: measured on resources/v2/resume.tex.jinja (S7's calibration of 2026-11-19), in points
PITCH_PT = 11.96
ITEM_GAP_PT = 3.98
FULL_HEADER_PT = 46.11
COMPACT_PT = 54.09
#: What the experiences may take, with a summary of TOP_SUMMARY_LINES lines.
CAPACITY_PT = 827.63
TOP_SUMMARY_LINES = 7
SUMMARY_LINE_CHARS = 102
#: What a skills line holds after its word breaks (S0 read 96; Unipol's 192 characters took 3 lines).
SKILLS_LINE_CHARS = 91
#: The summary the selection plans room for.
SUMMARY_CHARS = 500
#: Skill phrases, on 2 lines.
SKILLS = 10

# --- the mix ---------------------------------------------------------------------------------------
ATS_WEIGHT = 0.5
LITERAL_WEIGHT = 0.5
#: A duty's points by its weight.
DUTY_POINTS = {1: 1, 2: 3, 3: 9}
#: A month of a job counts 0.5 ** (its age in years / HALF_LIFE_YEARS) in the narrative (the user's choice of
#: 2026-11-19, after 3, 5 and 8 were simulated on the dev JDs).
HALF_LIFE_YEARS = 3.0
#: The share of a job's years it tells by being on the page, in either form; its duties tell the rest
#: (the user's "a job's summary matters more than its duties", 2026-11-20).
PRESENCE = 0.5
#: The share of a story's items a bullet tells, by how many rungs below the story's top it is: the
#: top rung (or a group, above it) tells them whole, a shorter rung leaves the context out.
DEPTH = (1.0, 0.85, 0.6)

#: A slot's rung: 1-3 on the ladder, or a group "g1".."g3".
RungKey = Union[int, str]


@dataclass(frozen=True)
class Piece:
    """Something printed under an experience: a bullet or a compact line."""

    id: str
    text: str
    covers: FrozenSet[str]  # the action and result items it tells; a compact line's uses
    nodes: Mapping[str, int]  # leaf id -> level
    lines: int


@dataclass(frozen=True)
class Context:
    """One job's inputs, indexed once by :func:`context`."""

    requirements: Tuple[Requirement, ...]
    points: Mapping[str, float]
    status: Mapping[str, str]
    experiences: Tuple[int, ...]  # profile order, most recent first
    years: Mapping[int, float]  # weighted by recency
    duties: Mapping[Tuple[int, int], Tuple[float, FrozenSet[str]]]  # (exp, duty) -> years (weighted), A/R items
    ladder: Mapping[str, Mapping[int, Tuple[Piece, ...]]]  # story -> rung -> wordings, a first
    groups: Mapping[str, Mapping[str, Tuple[Piece, ...]]]  # story -> "g1" -> bullets in order
    story_exp: Mapping[str, int]
    role: Mapping[int, str]
    compact: Mapping[int, Piece]
    outside: Tuple[Mapping[str, int], ...]  # one level-3 pseudo-piece per outside leaf
    attached: Mapping[str, Mapping[str, int]]  # item id -> leaf id -> level
    skills: Tuple[Skill, ...]
    frames: Tuple[Frame, ...]
    title: str
    titles: Tuple[str, ...]


def _piece(b: Union[Bullet, Line], pid: str, covers: Sequence[str], leaves: Mapping[str, Mapping[str, int]]) -> Piece:
    return Piece(pid, b.text, frozenset(covers), piece_nodes(b.uses, leaves), lines(b.text))


def duty_years(items: Items) -> Dict[Tuple[int, int], Tuple[float, FrozenSet[str]]]:
    """(experience, duty) -> the years the duty tells in full, and its action and result items.

    A job's years (months / 12) are shared by its duties by points (:data:`DUTY_POINTS`); a duty
    with no action or result item is left out of the share. A job with unreadable months has 0."""
    out: Dict[Tuple[int, int], Tuple[float, FrozenSet[str]]] = {}
    for x in items.experiences:
        work = {
            w.duty: frozenset(i.id for i in items.items if i.experience == x.id and i.duty == w.duty and is_work(i))
            for w in x.weights
        }
        weights = {w.duty: DUTY_POINTS[w.weight] for w in x.weights if work[w.duty]}
        total = sum(weights.values())
        for duty, pts in weights.items():
            out[(x.id, duty)] = ((x.months or 0) / 12 * pts / total, work[duty])
    return out


def recency(experiences: Sequence[Mapping[str, Any]], today: date) -> Dict[int, float]:
    """Experience id -> the weight of its years: the mean over its months of
    ``0.5 ** (age / HALF_LIFE_YEARS)``, a month's age being the years from it to ``today`` (the CV's
    date). 1 for unreadable dates."""
    now = today.year * 12 + today.month
    out = {}
    for e in experiences:
        span = _month_span(_plain(e.get("dates")), today)
        months = range(span[0], span[1] + 1) if span else range(0)
        decay = [0.5 ** ((now - m) / 12 / HALF_LIFE_YEARS) for m in months]
        out[e["id"]] = sum(decay) / len(decay) if decay else 1.0
    return out


def context(
    items: Items,
    tree: Tree,
    pool: Pool,
    allocation: Allocation,
    titles: Sequence[str],
    weights: Mapping[int, float],
) -> Context:
    """Everything the scores and the selection read for one job. ``weights`` are :func:`recency`'s;
    a job it does not name weighs 1."""
    leaves = item_leaves(tree, items)
    weight = {x.id: weights.get(x.id, 1.0) for x in items.experiences}
    ladder: Dict[str, Dict[int, Tuple[Piece, ...]]] = {}
    groups: Dict[str, Dict[str, Tuple[Piece, ...]]] = {}
    story_exp: Dict[str, int] = {}
    for block in pool.experiences:
        for s in block.stories:
            story_exp[s.story] = block.experience
            rungs: Dict[int, List[Piece]] = {}
            for r in sorted(s.ladder, key=lambda r: r.wording):
                rungs.setdefault(r.rung, []).append(_piece(r, r.id, r.covers, leaves))
            if rungs:
                ladder[s.story] = {k: tuple(v) for k, v in sorted(rungs.items())}
            if s.groups:
                groups[s.story] = {
                    g.id.rsplit(".", 1)[1]: tuple(_piece(b, b.id, b.covers, leaves) for b in g.bullets)
                    for g in s.groups
                }
    status = leaf_status(tree, items)
    return Context(
        requirements=tuple(allocation.requirements),
        points=points(tree, allocation.requirements),
        status=status,
        experiences=tuple(x.id for x in items.experiences),
        years={x.id: (x.months or 0) / 12 * weight[x.id] for x in items.experiences},
        duties={k: (y * weight[k[0]], work) for k, (y, work) in duty_years(items).items()},
        ladder=ladder,
        groups=groups,
        story_exp=story_exp,
        role={b.experience: b.role_summary.text for b in pool.experiences},
        compact={
            b.experience: _piece(b.compact, f"e{b.experience}.compact", b.compact.uses, leaves)
            for b in pool.experiences
        },
        outside=tuple({n.id: 3} for n in tree.nodes if level(n.id) == 3 and n.outside),
        attached=leaves,
        skills=tuple(pool.skills),
        frames=tuple(pool.frames),
        title=allocation.title,
        titles=tuple(titles),
    )


# --- a draft: {"forms": {exp: "full" | "compact"}, "slots": {story: rung}} -----------------------
Draft = Dict[str, Dict]


def slot_pieces(ctx: Context, story: str, rung: RungKey) -> Tuple[Piece, ...]:
    """What a slot prints for scoring: the group's bullets, or the rung's first wording."""
    if isinstance(rung, str):
        return ctx.groups[story][rung]
    return ctx.ladder[story][rung][:1]


def slot_lines(ctx: Context, story: str, rung: RungKey) -> List[int]:
    """Printed lines per bullet of a slot; for a rung, its longest wording's."""
    if isinstance(rung, str):
        return [p.lines for p in ctx.groups[story][rung]]
    return [max(p.lines for p in ctx.ladder[story][rung])]


def pieces(draft: Draft, ctx: Context) -> List[Piece]:
    """Everything printed under the experiences: bullets and compact lines."""
    out = [p for story, rung in draft["slots"].items() for p in slot_pieces(ctx, story, rung)]
    return out + [ctx.compact[e] for e, form in draft["forms"].items() if form == "compact"]


def height(draft: Draft, ctx: Context) -> float:
    """Points the experiences take on the page."""
    total = 0.0
    for e, form in draft["forms"].items():
        if form == "compact":
            total += COMPACT_PT
            continue
        total += FULL_HEADER_PT + math.ceil(len(ctx.role[e]) / ROLE_LINE) * PITCH_PT
        for story, rung in draft["slots"].items():
            if ctx.story_exp[story] == e:
                total += sum(n * PITCH_PT + ITEM_GAP_PT for n in slot_lines(ctx, story, rung))
    return total


def budget() -> float:
    """Points the experiences may take: the capacity, with the room a shorter summary frees."""
    return CAPACITY_PT + (TOP_SUMMARY_LINES - math.ceil(SUMMARY_CHARS / SUMMARY_LINE_CHARS)) * PITCH_PT


def cv_text(draft: Draft, ctx: Context) -> str:
    return "\n".join(
        [p.text for p in pieces(draft, ctx)]
        + list(draft.get("skills") or [])
        + list(draft.get("keywords") or [])
        + [draft.get("headline") or ""]
    )


# --- the scores ------------------------------------------------------------------------------------
def literal_score(text: str, requirements: Sequence[Requirement]) -> float:
    """Per requirement with keywords, a first occurrence of any of them counts in full and a second
    a third more, weighted by importance, over the most it could get."""
    got = most = 0.0
    for r in requirements:
        if not r.keywords:
            continue
        occ = sum(count_term(t, text) for t in r.keywords)
        got += r.importance * (min(occ, 1) + (1 / 3 if occ >= 2 else 0))
        most += r.importance * 4 / 3
    return got / most if most else 0.0


def levels_at(nodes: Mapping[str, int]) -> Dict[str, int]:
    """A piece's level on each leaf it touches and on every ancestor of those leaves."""
    out: Dict[str, int] = {}
    for leaf, lvl in nodes.items():
        n: Optional[str] = leaf
        while n is not None:
            out[n] = max(out.get(n, 0), lvl)
            n = parent(n)
    return out


def tree_score(draft: Draft, ctx: Context) -> float:
    """Per node, its points times the evidence of its best three pieces (level / 3, then / 9, then
    / 15, capped at 1), over all the points. A ghost has no piece, so its points stay unearned."""
    levels: Dict[str, List[int]] = {}
    for nodes in [p.nodes for p in pieces(draft, ctx)] + list(ctx.outside):
        for n, lvl in levels_at(nodes).items():
            levels.setdefault(n, []).append(lvl)
    total = sum(ctx.points.values())
    if not total:
        return 0.0
    earned = 0.0
    for n, pts in ctx.points.items():
        best = sorted(levels.get(n, []), reverse=True)[:3]
        earned += pts * min(1.0, sum(lvl / 3 / d for lvl, d in zip(best, (1, 3, 5), strict=False)))
    return earned / total


def ats(draft: Draft, ctx: Context) -> Dict[str, float]:
    lit = literal_score(cv_text(draft, ctx), ctx.requirements)
    tree = tree_score(draft, ctx)
    return {"ats": LITERAL_WEIGHT * lit + (1 - LITERAL_WEIGHT) * tree, "literal": lit, "tree": tree}


def told(draft: Draft, ctx: Context) -> Dict[str, float]:
    """Item id -> the depth it is told at: the deepest slot covering it (:data:`DEPTH`)."""
    out: Dict[str, float] = {}
    for story, rung in draft["slots"].items():
        depth = 1.0 if isinstance(rung, str) else DEPTH[max(ctx.ladder[story]) - rung]
        for p in slot_pieces(ctx, story, rung):
            for i in p.covers:
                out[i] = max(out.get(i, 0.0), depth)
    return out


def narrative(draft: Draft, ctx: Context) -> float:
    """The years told over the career's years (see the module)."""
    career = sum(ctx.years.values())
    if not career:
        return 0.0
    shown = sum(ctx.years[e] for e in draft["forms"])
    items = told(draft, ctx)
    duties = sum(y * sum(items.get(i, 0.0) for i in work) / len(work) for y, work in ctx.duties.values())
    return (PRESENCE * shown + (1 - PRESENCE) * duties) / career


def objective(draft: Draft, ctx: Context) -> float:
    return ATS_WEIGHT * ats(draft, ctx)["ats"] + (1 - ATS_WEIGHT) * narrative(draft, ctx)


# --- the moves ---------------------------------------------------------------------------------------
def _with(
    draft: Draft, forms: Optional[Dict] = None, slots: Optional[Dict] = None, drop: Tuple[str, ...] = ()
) -> Draft:
    new_slots = {k: v for k, v in {**draft["slots"], **(slots or {})}.items() if k not in drop}
    return {**draft, "forms": {**draft["forms"], **(forms or {})}, "slots": new_slots}


def _rungs(ctx: Context, story: str) -> List[RungKey]:
    return [*ctx.ladder.get(story, {}), *ctx.groups.get(story, {})]


def _stories(ctx: Context, e: int) -> List[str]:
    return sorted(s for s, x in ctx.story_exp.items() if x == e and s in ctx.ladder)


def moves(draft: Draft, ctx: Context) -> List[Tuple[str, Draft]]:
    """Every move from ``draft``: add a story at a rung; step a story up a rung, or from its top
    rung to any of its groups; open a compact experience with its two best slots (any rung or
    group, each by gain per line); add an absent experience in compact form."""
    out: List[Tuple[str, Draft]] = []
    for e in [e for e, f in draft["forms"].items() if f == "full"]:
        for s in _stories(ctx, e):
            if s not in draft["slots"]:
                out += [(f"add {s} r{r}", _with(draft, slots={s: r})) for r in ctx.ladder[s]]
    for s, r in draft["slots"].items():
        if isinstance(r, str):
            continue
        ups = [k for k in ctx.ladder[s] if k > r][:1] or list(ctx.groups.get(s, {}))
        out += [(f"step {s} to {k if isinstance(k, str) else f'r{k}'}", _with(draft, slots={s: k})) for k in ups]
    for e in ctx.experiences:
        if draft["forms"].get(e) == "compact" and len(_stories(ctx, e)) >= 2:
            out.append((f"open e{e}", opened(draft, ctx, e)))
        elif e not in draft["forms"]:
            out.append((f"compact e{e}", _with(draft, forms={e: "compact"})))
    return out


def opened(draft: Draft, ctx: Context, e: int) -> Draft:
    """``draft`` with experience ``e`` in full and its two best slots (any rung or group, each by
    gain per line)."""
    out = _with(draft, forms={e: "full"})
    for _ in range(2):
        adds = [_with(out, slots={s: r}) for s in _stories(ctx, e) if s not in out["slots"] for r in _rungs(ctx, s)]
        if not adds:
            break
        h0, o0 = height(out, ctx), objective(out, ctx)
        out = max(adds, key=lambda d: (objective(d, ctx) - o0) / max(height(d, ctx) - h0, 1e-9))
    return out


def floor_kept(draft: Draft, ctx: Context) -> bool:
    """The most recent experience is in full with at least 2 bullets, or has fewer than 2 stories."""
    e = ctx.experiences[0]
    if len(_stories(ctx, e)) < 2:
        return True
    mine = [s for s in draft["slots"] if ctx.story_exp[s] == e]
    return draft["forms"].get(e) == "full" and sum(len(slot_lines(ctx, s, draft["slots"][s])) for s in mine) >= 2


def tidy(draft: Draft, ctx: Context) -> Draft:
    """A full experience left with fewer than 2 bullets goes back to compact."""
    for e, form in list(draft["forms"].items()):
        mine = tuple(s for s in draft["slots"] if ctx.story_exp[s] == e)
        if form == "full" and sum(len(slot_lines(ctx, s, draft["slots"][s])) for s in mine) < 2:
            draft = _with(draft, forms={e: "compact"}, drop=mine)
    return draft


def greedy(ctx: Context, start: Draft, log: Optional[Callable[[str], None]] = None) -> Draft:
    """From ``start``, the move with the best gain per line while one fits; moves that improve the
    objective come first by construction, and once none does the page is still filled with the
    one that loses least per line."""
    draft, room = start, budget()
    while True:
        base, used = objective(draft, ctx), height(draft, ctx)
        best = None
        for name, cand in moves(draft, ctx):
            h = height(cand, ctx)
            if h > room or h - used <= 0:
                continue
            rate = (objective(cand, ctx) - base) / (h - used)
            if best is None or rate > best[0]:
                best = (rate, name, cand)
        if best is None:
            break
        draft = best[2]
        if log:
            log(
                f"{best[1]}: {best[0] * PITCH_PT:+.4f} per line{'' if best[0] > 0 else ' (fill)'}, "
                f"{height(draft, ctx) / PITCH_PT:.1f} of {room / PITCH_PT:.1f} lines"
            )
    return tidy(draft, ctx)


def weakest(draft: Draft, ctx: Context) -> Optional[Tuple[str, Draft]]:
    """The slot whose removal loses least objective per line freed, and the draft without it; the
    most recent experience keeps its 2 bullets (:func:`floor_kept`)."""
    base, used = objective(draft, ctx), height(draft, ctx)
    best = None
    for s in draft["slots"]:
        cand = tidy(_with(draft, drop=(s,)), ctx)
        freed = used - height(cand, ctx)
        if freed <= 0 or not floor_kept(cand, ctx):
            continue
        loss = (base - objective(cand, ctx)) / freed
        if best is None or loss < best[0]:
            best = (loss, s, cand)
    return (best[1], best[2]) if best else None


def refill(ctx: Context, draft: Draft, log: Optional[Callable[[str], None]] = None) -> Draft:
    """Drop the weakest slot and run the greedy again; keep the result when the objective is
    higher, and stop at the first try that is not."""
    while True:
        dropped = weakest(draft, ctx)
        if dropped is None:
            return draft
        cand = greedy(ctx, dropped[1], log)
        gain = objective(cand, ctx) - objective(draft, ctx)
        if log:
            log(f"refill without {dropped[0]}: {gain:+.4f} objective, {'kept' if gain > 0 else 'stop'}")
        if gain <= 0:
            return draft
        draft = cand


def fit(draft: Draft, ctx: Context) -> Optional[Draft]:
    """One step towards a shorter CV (S0's): the step down that loses least objective per line
    saved (a group to its story's top rung, a rung to the one below); only when no story can step
    down, the removal that loses least, keeping the most recent experience's 2 bullets
    (:func:`floor_kept`). None when nothing is left to take away."""
    base, used = objective(draft, ctx), height(draft, ctx)

    def cost(cand: Draft) -> float:
        saved = used - height(cand, ctx)
        return (base - objective(cand, ctx)) / saved if saved > 0 else math.inf

    downs, removals = [], []
    for s, r in draft["slots"].items():
        ladder = list(ctx.ladder[s])
        below = ladder[-1] if isinstance(r, str) else next((k for k in reversed(ladder) if k < r), None)
        if below is not None:
            downs.append(_with(draft, slots={s: below}))
        removals.append(tidy(_with(draft, drop=(s,)), ctx))
    options = [c for c in (downs or removals) if cost(c) < math.inf and floor_kept(c, ctx)]
    return min(options, key=cost) if options else None


# --- skills, frame, headline -----------------------------------------------------------------------
def skills(draft: Draft, ctx: Context) -> List[str]:
    """Skill phrases on 2 lines, taken one at a time by the literal score they add, then by the
    points of their nodes."""
    text = "\n".join(p.text for p in pieces(draft, ctx))
    width = 2 * SKILLS_LINE_CHARS
    chosen: List[str] = []
    left = list(ctx.skills)
    while len(chosen) < SKILLS and left:
        now = "\n".join([text, *chosen])
        before = literal_score(now, ctx.requirements)

        def gain(s: Skill, now: str = now, before: float = before) -> Tuple[float, float]:
            return (
                literal_score(now + "\n" + s.text, ctx.requirements) - before,
                sum(ctx.points.get(n, 0.0) for n in s.nodes),
            )

        fits = [s for s in left if sum(map(len, chosen)) + len(s.text) + 3 * len(chosen) <= width]
        if not fits:
            break
        best = max(fits, key=gain)
        chosen.append(best.text)
        left.remove(best)
    return chosen


def frame(ctx: Context) -> Tuple[str, List[str]]:
    """The summary frame whose items reach the nodes with the most points, and 2-4 keywords of
    requirements on proven nodes for its keyword slot, most important first."""

    def reach(f: Frame) -> float:
        nodes = set()
        for i in f.uses:
            for leaf in ctx.attached.get(i, {}):
                n: Optional[str] = leaf
                while n is not None:
                    nodes.add(n)
                    n = parent(n)
        return sum(ctx.points.get(n, 0.0) for n in nodes)

    best = max(ctx.frames, key=reach)
    words: List[str] = []
    for r in sorted(ctx.requirements, key=lambda r: -r.importance):
        if r.node and ctx.status.get(r.node) == "proven" and r.keywords and len(words) < 4:
            t = r.keywords[0]
            if t.lower() not in {w.lower() for w in words} and t.lower() != ctx.title.lower():
                words.append(t)
    return best.id, words


def headline(ctx: Context) -> Optional[str]:
    """The job title when it matches one of the profile's titles; otherwise S8 writes it."""
    t = ctx.title.lower().strip()
    hit = t and any(t == x.lower() or x.lower() in t or t in x.lower() for x in ctx.titles)
    return ctx.title if hit else None


# --- the plan -----------------------------------------------------------------------------------------
class Slot(_Strict):
    story: str
    rung: RungKey
    bullets: List[str]  # what it may print: the rung's wordings, or the group's bullets in order
    lines: int
    relevance: float


class Shown(_Strict):
    experience: int
    form: str  # "full" | "compact"
    slots: List[Slot] = []  # most relevant first


class Scores(_Strict):
    ats: float
    literal: float
    tree: float
    narrative: float
    objective: float


class Plan(_Strict):
    title: str
    experiences: List[Shown]  # by id: most recent first
    skills: List[str]
    frame: str
    keywords: List[str]
    headline: Optional[str]
    scores: Scores
    greedy_objective: float  # before the refill pass
    lines: float  # of the experiences
    budget_lines: float


def _slots(draft: Draft, ctx: Context, e: int) -> List[Slot]:
    """A full experience's slots, most relevant first: by the points its pieces earn on this
    job's nodes, each node counted at the piece's level / 3."""
    out = []
    for story, rung in draft["slots"].items():
        if ctx.story_exp[story] != e:
            continue
        levels: Dict[str, int] = {}
        for p in slot_pieces(ctx, story, rung):
            for n, lvl in levels_at(p.nodes).items():
                levels[n] = max(levels.get(n, 0), lvl)
        relevance = sum(ctx.points.get(n, 0.0) * lvl / 3 for n, lvl in levels.items())
        ids = [p.id for p in (ctx.groups[story][rung] if isinstance(rung, str) else ctx.ladder[story][rung])]
        out.append(
            Slot(
                story=story,
                rung=rung,
                bullets=ids,
                lines=sum(slot_lines(ctx, story, rung)),
                relevance=round(relevance, 4),
            )
        )
    return sorted(out, key=lambda s: -s.relevance)


def finish(draft: Draft, ctx: Context) -> Draft:
    """The draft with its skills, frame and keywords, and headline."""
    draft = {**draft, "skills": skills(draft, ctx)}
    draft["frame"], draft["keywords"] = frame(ctx)
    draft["headline"] = headline(ctx)
    return draft


def scores(draft: Draft, ctx: Context) -> Scores:
    a = ats(draft, ctx)
    return Scores(
        ats=round(a["ats"], 4),
        literal=round(a["literal"], 4),
        tree=round(a["tree"], 4),
        narrative=round(narrative(draft, ctx), 4),
        objective=round(objective(draft, ctx), 4),
    )


def plan_of(draft: Draft, ctx: Context, greedy_objective: float) -> Plan:
    """The plan of a draft, finished with its skills, frame and headline."""
    best = finish(draft, ctx)
    return Plan(
        title=ctx.title,
        experiences=[
            Shown(experience=e, form=best["forms"][e], slots=_slots(best, ctx, e) if best["forms"][e] == "full" else [])
            for e in ctx.experiences
            if e in best["forms"]
        ],
        skills=best["skills"],
        frame=best["frame"],
        keywords=best["keywords"],
        headline=best["headline"],
        scores=scores(best, ctx),
        greedy_objective=greedy_objective,
        lines=round(height(best, ctx) / PITCH_PT, 1),
        budget_lines=round(budget() / PITCH_PT, 1),
    )


def draft_of(plan: Plan) -> Draft:
    """A plan's forms and slots, as the moves and :func:`fit` take them."""
    return {
        "forms": {x.experience: x.form for x in plan.experiences},
        "slots": {s.story: s.rung for x in plan.experiences for s in x.slots},
    }


def select(ctx: Context, log: Optional[Callable[[str], None]] = None) -> Plan:
    """The plan for one job: the greedy from the most recent experience in full, the refill pass,
    then skills, frame and headline."""
    e = ctx.experiences[0]
    start: Draft = {"forms": {e: "compact"}, "slots": {}}
    if len(_stories(ctx, e)) >= 2:
        start = opened({"forms": {}, "slots": {}}, ctx, e)
    first = greedy(ctx, start, log)
    return plan_of(refill(ctx, first, log), ctx, round(objective(finish(first, ctx), ctx), 4))


def plan_markdown(plan: Plan, pool: Pool) -> str:
    """The view: lines of the budget, scores, then per experience its slots and texts."""
    text = {b.id: b.text for block in pool.experiences for s in block.stories for b in s.ladder}
    text.update(
        {b.id: b.text for block in pool.experiences for s in block.stories for g in s.groups for b in g.bullets}
    )
    blocks = {b.experience: b for b in pool.experiences}
    frames = {f.id: f.text for f in pool.frames}
    sc = plan.scores
    out = [
        f"# Plan: {plan.title}",
        "",
        f"- Lines: {plan.lines} of {plan.budget_lines}",
        (
            f"- Objective {sc.objective:.4f} (greedy {plan.greedy_objective:.4f}, refill "
            f"{sc.objective - plan.greedy_objective:+.4f}); ATS {sc.ats:.4f} (literal {sc.literal:.4f}, tree "
            f"{sc.tree:.4f}); narrative {sc.narrative:.4f}"
        ),
        f"- Headline: {plan.headline or '(S8 writes it)'}",
        f"- Frame {plan.frame}, keywords: {', '.join(plan.keywords)}",
        f"  > {frames.get(plan.frame, '')}",
        f"- Skills: {' | '.join(plan.skills)}",
        "",
    ]
    for x in plan.experiences:
        block = blocks[x.experience]
        if x.form == "compact":
            out += [f"## e{x.experience} compact", "", f"- {block.compact.text}", ""]
            continue
        out += [f"## e{x.experience} full", "", f"_{block.role_summary.text}_", ""]
        for s in x.slots:
            rung = s.rung if isinstance(s.rung, str) else f"r{s.rung}"
            shown = s.bullets if isinstance(s.rung, str) else s.bullets[:1]
            out.append(f"- **{s.story} {rung}** ({s.lines} line{'s' * (s.lines != 1)}, relevance {s.relevance:.3f})")
            out += [f"  - {text[b]}" for b in shown]
        out.append("")
    return "\n".join(out)


__all__ = [
    "ATS_WEIGHT",
    "LITERAL_WEIGHT",
    "Context",
    "Plan",
    "context",
    "draft_of",
    "duty_years",
    "fit",
    "greedy",
    "narrative",
    "objective",
    "plan_markdown",
    "plan_of",
    "recency",
    "refill",
    "select",
]
