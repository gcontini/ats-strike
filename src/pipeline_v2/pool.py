"""The pool: everything a CV prints from the experiences, written by the ``compile`` role in two fan-outs.

First, one experience call per experience to write: its role summary, which states the
experience's context and tells no action, and its compact line. Then, side by side, that
experience's ladder calls — a long story alone, the others in calls of at most four — each shown
the role summary, every context item of the experience (SETTING) and only its own stories; and,
when it is due, the profile call for the skill phrases and the summary frames. Two graphs, not
one: LangGraph steps in supersteps, so in one graph the slow profile call would hold back every
ladder call.

A reply that does not parse (it is parsed here, so a broken JSON goes back with where it breaks),
misses a story it was given, or has a rung outside 1-3 or a wording other than a/b goes back once
with the fault, then fails. Code assigns every id, moves a rung bullet that fills fewer lines than
its rung down to the rung it fills (as wording c, d, ...), and drops a second bullet for one rung
and wording, a group of a short story or a fourth group, each with its reason;
every other fault is a check of the view (:func:`~.pool_store.pool_checks`), never a second call.

Pieces follow the items. With a ``previous`` pool, an experience whose items and stories are
unchanged (:func:`~.pool_store.shown_sha256`) keeps its block, hand edits included; a changed one (or one in ``force``) is written again, and the
hand-edited pieces of its old block are listed in ``previous``. Skill phrases and frames are
written on the first run or with ``profile_level``, and copied otherwise.
"""

from __future__ import annotations

import json
import logging
import math
import operator
from datetime import date
from threading import Lock
from typing import (
    Annotated,
    Any,
    Callable,
    Dict,
    Iterable,
    List,
    Mapping,
    Optional,
    Sequence,
    Tuple,
    TypedDict,
    TypeVar,
)

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send
from pydantic import BaseModel, ValidationError

from common.errors import ModelOutputError
from common.models import ModelSelector, Usage
from common.observability import stage

from .items import ProgressFn, add_usage, in_order
from .pool_store import (
    LONG,
    MAX_GROUPS,
    Bullet,
    ExperiencePool,
    Frame,
    Group,
    Line,
    Pool,
    Rung,
    Skill,
    StoryPool,
    career_years,
    item_weights,
    level_sha256,
    lines,
    settle_ladder,
    shown_sha256,
    story_work,
)
from .store import Item, Items, Story, _plain, squash
from .tree_store import Tree, leaf_status, level

logger = logging.getLogger("pipeline_v2.pool")

ROLE = "compile"
STAGE = "pool"
#: Most stories in one ladder call; a long story is always alone.
BATCH = 4

T = TypeVar("T")


# --- what the calls see ----------------------------------------------------------------------
def header(exp: Mapping[str, Any]) -> str:
    """The job's header as the CV prints it: title, company, dates."""
    return f"{_plain(exp.get('title'))}, {_plain(exp.get('company'))} ({_plain(exp.get('dates'))})"


def item_line(item: Item, weight: Optional[int] = None) -> str:
    head = item.type + (f", {item.agency}" if item.agency else "")
    return (
        f"{item.id} [{head}] {item.text}"
        + (f" | scope: {item.scope}" if item.scope else "")
        + (f" | tech: {', '.join(item.tech)}" if item.tech else "")
        + (f" | weight {weight}" if weight else "")
    )


def _own(items: Items, experience: int) -> List[Item]:
    return [i for i in items.items if i.experience == experience]


def experience_input(exp: Mapping[str, Any], items: Items, style: str) -> str:
    """An experience call's user message: the header, the context list, every item with its weight."""
    e = exp["id"]
    by_id = {i.id: i for i in items.items}
    x = next(x for x in items.experiences if x.id == e)
    weights = item_weights(items)
    return (
        f"WRITING STYLE:\n{style or '-'}\n\nEXPERIENCE: {header(exp)}\n\n"
        "EXPERIENCE CONTEXT:\n"
        + ("\n".join(item_line(by_id[i]) for i in x.context if i in by_id) or "(none)")
        + "\n\nITEMS:\n"
        + "\n".join(item_line(i, weights.get(i.id)) for i in _own(items, e))
    )


def ladder_input(exp: Mapping[str, Any], items: Items, role_summary: Line, stories: Sequence[Story], style: str) -> str:
    """A ladder call's user message: the header, the role summary, SETTING and its own stories."""
    by_id = {i.id: i for i in items.items}
    work = story_work(items)
    setting = [item_line(i) for i in _own(items, exp["id"]) if i.type == "context"]
    blocks = []
    for s in stories:
        marks = [m for m in ("LONG" if work[s.id] >= LONG else "", "MERGED" if s.merged else "") if m]
        blocks.append(
            f"{s.id}" + (f" ({', '.join(marks)})" if marks else "") + f": {s.title}\n"
            f"  ANCHOR: {', '.join(s.anchor)}\n" + "\n".join("  " + item_line(by_id[i]) for i in s.items if i in by_id)
        )
    return (
        f"WRITING STYLE:\n{style or '-'}\n\nEXPERIENCE: {header(exp)}\n\n"
        f"ROLE SUMMARY: {role_summary.text}\nIt states: {', '.join(role_summary.uses) or '-'}\n\n"
        "SETTING:\n" + ("\n".join(setting) or "(none)") + "\n\nSTORIES:\n" + "\n\n".join(blocks)
    )


def profile_input(profile: Mapping[str, Any], items: Items, tree: Tree, today: date) -> str:
    """The profile call's user message: every experience with its items, the career, the proven
    leaves, and the profile's own skills, job titles and summaries."""
    weights = item_weights(items)
    months = {x.id: x.months for x in items.experiences}
    experiences = "\n\n".join(
        f"EXPERIENCE {exp['id']}: {header(exp)}, {months.get(exp['id']) or '?'} months\n"
        + "\n".join("  " + item_line(i, weights.get(i.id)) for i in _own(items, exp["id"]))
        for exp in profile["experiences"]
    )
    status = leaf_status(tree, items)
    leaves = "\n".join(
        f"{n.id} {n.name}" + (f" | aliases: {', '.join(n.aliases)}" if n.aliases else "")
        for n in tree.nodes
        if level(n.id) == 3 and status[n.id] == "proven"
    )
    return (
        f"WRITING STYLE:\n{profile.get('writing_style') or '-'}\n\nEXPERIENCES:\n{experiences}\n\n"
        f"CAREER: {career_years(profile, today)} whole years\n\nPROVEN LEAVES:\n{leaves}\n\n"
        f"CANDIDATE SKILLS:\n{', '.join(profile.get('skills') or []) or '-'}\n\n"
        f"JOB TITLES:\n{', '.join(_titles(profile.get('job_title')))}\n\n"
        "MASTER SUMMARIES:\n" + ("\n".join(profile.get("summaries") or []) or "-")
    )


def _titles(value: Any) -> List[str]:
    return [str(t) for t in value] if isinstance(value, list) else [str(value or "-")]


def batches(stories: Sequence[Story], work: Mapping[str, int]) -> List[List[Story]]:
    """A long story alone; the others in calls of at most :data:`BATCH`, of even size."""
    long = [[s] for s in stories if work[s.id] >= LONG]
    short = [s for s in stories if work[s.id] < LONG]
    if not short:
        return long
    size = math.ceil(len(short) / math.ceil(len(short) / BATCH))
    return long + [list(short[k : k + size]) for k in range(0, len(short), size)]


# --- the replies ---------------------------------------------------------------------------------
class ReplyLine(BaseModel):
    text: str
    uses: List[str] = []


class ExperienceReply(BaseModel):
    role_summary: ReplyLine
    compact: ReplyLine


class ReplyBullet(BaseModel):
    text: str
    uses: List[str] = []
    covers: List[str] = []
    assumes: List[str] = []


class ReplyRung(ReplyBullet):
    rung: int
    wording: str


class ReplyGroupBullet(ReplyBullet):
    group: int


class ReplyStory(BaseModel):
    """``groups`` is flat, like ``ladder``: each bullet names its group. A list of lists broke the
    JSON in a third of the calls that wrote groups (a group list closed too early)."""

    id: str
    ladder: List[ReplyRung] = []
    groups: List[ReplyGroupBullet] = []


class LadderReply(BaseModel):
    stories: List[ReplyStory]


class ReplySkill(BaseModel):
    text: str
    nodes: List[str] = []


class ReplyFrame(BaseModel):
    family: str
    text: str
    uses: List[str] = []


class ProfileReply(BaseModel):
    skills: List[ReplySkill]
    frames: List[ReplyFrame]


def _describe(error: ValidationError) -> str:
    return "\n".join(f"- {'.'.join(map(str, e['loc'])) or '(whole reply)'}: {e['msg']}" for e in error.errors())


def json_object(text: str) -> dict:
    """A reply's JSON object, a Markdown fence around it allowed. Raises ``ValueError`` saying where
    the JSON breaks: the selector's lenient parser gives up on a mismatched bracket without saying
    where, and a model told only "not a JSON object" sends the same reply again."""
    body = text.strip()
    if body.startswith("```"):
        body = body.split("\n", 1)[1].rsplit("```", 1)[0] if "\n" in body else ""
    try:
        value = json.loads(body, strict=False)
    except json.JSONDecodeError as error:
        at = error.pos
        raise ValueError(
            f"- the JSON breaks at character {at}: {error.msg}. Around it: "
            f"...{body[max(0, at - 100) : at]}<<HERE>>{body[at : at + 40]}..."
        ) from None
    if not isinstance(value, dict):
        raise ValueError("- it is not a JSON object")
    return value


def _validated(model: type, raw: Any) -> Any:
    try:
        return model.model_validate(raw)
    except ValidationError as error:
        raise ValueError(_describe(error)) from None


def parse_experience(raw: Any) -> ExperienceReply:
    return _validated(ExperienceReply, raw)


def parse_profile(raw: Any) -> ProfileReply:
    return _validated(ProfileReply, raw)


def parse_ladder(raw: Any, asked: Sequence[str]) -> LadderReply:
    """A ladder reply. Raises ``ValueError`` when it does not parse, misses a story it was asked, or
    has a rung outside 1-3 or a wording other than a or b."""
    reply = _validated(LadderReply, raw)
    given = {s.id for s in reply.stories}
    faults = [f"- story {sid} is missing: write it" for sid in asked if sid not in given]
    for s in reply.stories:
        if s.id not in asked:
            continue
        faults += [f"- {s.id}: rung {r.rung} (1, 2 or 3)" for r in s.ladder if r.rung not in (1, 2, 3)]
        faults += [f"- {s.id}: wording {r.wording!r} (a or b)" for r in s.ladder if r.wording not in ("a", "b")]
    if faults:
        raise ValueError("\n".join(faults))
    return reply


def _bullet(cls: type, bid: str, b: ReplyBullet, **extra: Any) -> Any:
    return cls(id=bid, text=squash(b.text), uses=b.uses, covers=b.covers, assumes=b.assumes, **extra)


def build_story(story: Story, reply: ReplyStory, work: int) -> Tuple[StoryPool, List[dict]]:
    """One story's pool from its reply, with code's ids; and what code dropped, each with its reason:
    a second bullet for one rung and wording, an empty bullet, a group of a short story, a fourth
    group. A rung bullet that fills fewer lines than its rung moves down to the rung it fills, after
    that rung's own wordings (c, d, ...), and does not hold its rung and wording against a second
    bullet for them. A group's bullets are those with its number, in reply
    order; groups in the order their numbers first come."""
    dropped: List[dict] = []
    ladder: List[Rung] = []
    for r in sorted(reply.ladder, key=lambda r: (r.rung, r.wording)):
        rid, text = f"{story.id}.r{r.rung}.{r.wording}", squash(r.text)
        if not text or any(x.id == rid and lines(x.text) >= x.rung for x in ladder):
            dropped.append(
                {
                    "story": story.id,
                    "what": f"r{r.rung}.{r.wording}",
                    "text": text,
                    "reason": "an empty bullet" if not text else "a second bullet for this rung and wording",
                }
            )
        else:
            ladder.append(_bullet(Rung, rid, r, rung=r.rung, wording=r.wording))
    ladder, _ = settle_ladder(story.id, ladder)
    by_number: Dict[int, List[ReplyGroupBullet]] = {}
    for b in reply.groups:
        by_number.setdefault(b.group, []).append(b)
    groups: List[Group] = []
    for raw in by_number.values():
        text = " | ".join(squash(b.text) for b in raw)
        reason = (
            f"a group of a short story ({work} action and result items; {LONG} make it long)"
            if work < LONG
            else f"a group beyond the {MAX_GROUPS}th"
            if len(groups) >= MAX_GROUPS
            else None
        )
        if reason:
            dropped.append({"story": story.id, "what": "group", "text": text, "reason": reason})
            continue
        gid = f"{story.id}.g{len(groups) + 1}"
        groups.append(Group(id=gid, bullets=[_bullet(Bullet, f"{gid}.{k}", b) for k, b in enumerate(raw, 1)]))
    return StoryPool(story=story.id, ladder=ladder, groups=groups), dropped


def build_profile(reply: ProfileReply) -> Tuple[List[Skill], List[Frame]]:
    skills = [Skill(id=f"k{k:02d}", text=squash(s.text), nodes=s.nodes) for k, s in enumerate(reply.skills, 1)]
    frames = [
        Frame(id=f"f{k}", family=squash(f.family), text=squash(f.text), uses=f.uses)
        for k, f in enumerate(reply.frames, 1)
    ]
    return skills, frames


def edited_pieces(block: ExperiencePool) -> List[dict]:
    """The pieces of a block the user edited by hand, one dict each, for ``previous``."""
    out: List[dict] = []
    for name in ("role_summary", "compact"):
        line = getattr(block, name)
        if line.edited:
            out.append({"what": name, **line.model_dump()})
    for s in block.stories:
        out += [{"what": "rung", **r.model_dump()} for r in s.ladder if r.edited]
        out += [{"what": "group bullet", **b.model_dump()} for g in s.groups for b in g.bullets if b.edited]
    return out


# --- the graphs -------------------------------------------------------------------------------------
class ExperienceState(TypedDict, total=False):
    """The experiences to write, and each one's role summary and compact line."""

    todo: List[Tuple[int, dict]]  # (position, profile experience)
    items: Items
    style: str
    answers: Annotated[List[Tuple[int, Tuple[int, Line, Line]]], operator.add]
    written: List[Tuple[int, Line, Line]]


class WriteState(TypedDict, total=False):
    """The ladder calls and the profile call when due, and their answers."""

    ladders: List[dict]
    profile_task: Optional[dict]
    answers: Annotated[List[Tuple[int, Any]], operator.add]
    collected: List[Any]


class PoolWriter:
    """Writes a profile's pool from its items and its competence tree."""

    def __init__(
        self,
        *,
        llm: ModelSelector,
        experience_prompt: str,
        ladder_prompt: str,
        profile_prompt: str,
        rules: str,
        progress: Optional[ProgressFn] = None,
    ) -> None:
        self.llm = llm
        self.experience_prompt = _with_rules(experience_prompt, rules)
        self.ladder_prompt = _with_rules(ladder_prompt, rules)
        self.profile_prompt = _with_rules(profile_prompt, rules)
        self.progress = progress
        #: Every model call of every write, failed ones included.
        self.usage = Usage()
        self._lock = Lock()

    def write(
        self,
        items: Items,
        tree: Tree,
        profile: Mapping[str, Any],
        *,
        today: date,
        previous: Optional[Pool] = None,
        force: Iterable[int] = (),
        profile_level: bool = False,
    ) -> Pool:
        """The pool for ``items``: blocks of ``previous`` whose items are unchanged copied, the others
        (and those in ``force``, by experience id) written; skill phrases and frames written when
        there is no ``previous`` or on ``profile_level``, else copied."""
        force = set(force)
        old = {b.experience: b for b in previous.experiences} if previous else {}
        exps = {e["id"]: e for e in profile["experiences"]}
        style = profile.get("writing_style") or ""
        work = story_work(items)

        todo: List[Tuple[int, dict]] = []
        for n, x in enumerate(items.experiences):
            block = old.get(x.id)
            if block is not None and block.items_sha256 == shown_sha256(items, x.id) and x.id not in force:
                self._report(f"e{x.id}: items unchanged, pool copied")
            else:
                todo.append((n, dict(exps[x.id])))
        due = previous is None or profile_level

        lines: Dict[int, Tuple[Line, Line]] = {}
        if todo:
            state = self._experience_graph().invoke({"todo": todo, "items": items, "style": style})
            lines = {e: (role, compact) for e, role, compact in state["written"]}

        ladders = []
        for n, exp in todo:
            x = items.experiences[n]
            for batch in batches(x.stories, work):
                ladders.append(
                    {
                        "position": len(ladders),
                        "experience": exp,
                        "items": items,
                        "role_summary": lines[x.id][0],
                        "stories": batch,
                        "style": style,
                    }
                )
        profile_task = (
            {"position": len(ladders), "profile": dict(profile), "items": items, "tree": tree, "today": today}
            if due
            else None
        )
        answers: List[Any] = []
        if ladders or profile_task:
            answers = self._write_graph().invoke({"ladders": ladders, "profile_task": profile_task})["collected"]

        written: Dict[int, Dict[str, StoryPool]] = {}
        dropped: Dict[int, List[dict]] = {}
        skills, frames = (previous.skills, previous.frames) if previous and not due else ([], [])
        for answer in answers:
            if answer[0] == "ladder":
                _, e, stories, lost = answer
                written.setdefault(e, {}).update({s.story: s for s in stories})
                dropped.setdefault(e, []).extend(lost)
            else:
                skills, frames = answer[1], answer[2]

        blocks: List[ExperiencePool] = []
        for x in items.experiences:
            if x.id not in lines:
                blocks.append(old[x.id])
                continue
            blocks.append(
                ExperiencePool(
                    experience=x.id,
                    items_sha256=shown_sha256(items, x.id),
                    role_summary=lines[x.id][0],
                    compact=lines[x.id][1],
                    stories=[written[x.id][s.id] for s in x.stories],
                    dropped=dropped.get(x.id, []),
                    previous=edited_pieces(old[x.id]) if x.id in old else [],
                )
            )
        level = level_sha256(items, tree) if due else previous.level_sha256
        return Pool(level_sha256=level, experiences=blocks, skills=skills, frames=frames)

    # --- fan-out 1: one experience call per experience ---------------------------------------------
    def _experience_graph(self):
        """START -> experience (one per experience) -> collect -> END"""
        graph = StateGraph(ExperienceState)
        graph.add_node("experience", self._experience)
        graph.add_node("collect", self._collect_lines)
        graph.add_conditional_edges(START, self._each_experience, ["experience"])
        graph.add_edge("experience", "collect")
        graph.add_edge("collect", END)
        return graph.compile()

    def _each_experience(self, state: ExperienceState) -> List[Send]:
        return [
            Send("experience", {"position": n, "experience": exp, "items": state["items"], "style": state["style"]})
            for n, exp in state["todo"]
        ]

    def _experience(self, task: dict) -> dict:
        exp, items = task["experience"], task["items"]
        usage = Usage()
        try:
            with stage(STAGE):
                reply = self._ask(
                    self.experience_prompt,
                    experience_input(exp, items, task["style"]),
                    parse_experience,
                    f"e{exp['id']} role summary",
                    usage,
                )
        finally:
            add_usage(self.usage, usage, self._lock)
        role = Line(text=squash(reply.role_summary.text), uses=reply.role_summary.uses)
        compact = Line(text=squash(reply.compact.text), uses=reply.compact.uses)
        self._report(
            f"e{exp['id']}: role summary {len(role.text)} characters, compact line {len(compact.text)}; "
            f"{self._cost(usage)}"
        )
        return {"answers": [(task["position"], (exp["id"], role, compact))]}

    def _collect_lines(self, state: ExperienceState) -> dict:
        return {"written": in_order(state["answers"])}

    # --- fan-out 2: the ladder calls, and the profile call when due --------------------------------
    def _write_graph(self):
        """START -> ladder (one per batch of stories) and profile (when due) -> collect -> END"""
        graph = StateGraph(WriteState)
        graph.add_node("ladder", self._ladder)
        graph.add_node("profile", self._profile)
        graph.add_node("collect", self._collect_answers)
        graph.add_conditional_edges(START, self._each_call, ["ladder", "profile"])
        graph.add_edge("ladder", "collect")
        graph.add_edge("profile", "collect")
        graph.add_edge("collect", END)
        return graph.compile()

    def _each_call(self, state: WriteState) -> List[Send]:
        sends = [Send("ladder", task) for task in state["ladders"]]
        if state.get("profile_task"):
            sends.append(Send("profile", state["profile_task"]))
        return sends

    def _ladder(self, task: dict) -> dict:
        exp, items, stories = task["experience"], task["items"], task["stories"]
        asked = [s.id for s in stories]
        what = f"e{exp['id']} " + ", ".join(sid.split(".", 1)[1] for sid in asked)
        usage = Usage()
        try:
            with stage(STAGE):
                reply = self._ask(
                    self.ladder_prompt,
                    ladder_input(exp, items, task["role_summary"], stories, task["style"]),
                    lambda raw: parse_ladder(raw, asked),
                    what,
                    usage,
                )
        finally:
            add_usage(self.usage, usage, self._lock)
        work = story_work(items)
        replies = {s.id: s for s in reply.stories}
        built: List[StoryPool] = []
        lost: List[dict] = [
            {"story": s.id, "what": "story", "text": "", "reason": "not a story of this call"}
            for s in reply.stories
            if s.id not in asked
        ]
        for story in stories:
            pool, dropped = build_story(story, replies[story.id], work[story.id])
            built.append(pool)
            lost += dropped
        self._report(
            f"{what}: {sum(len(s.ladder) for s in built)} rungs, {sum(len(s.groups) for s in built)} "
            f"groups, {len(lost)} dropped; {self._cost(usage)}"
        )
        return {"answers": [(task["position"], ("ladder", exp["id"], built, lost))]}

    def _profile(self, task: dict) -> dict:
        usage = Usage()
        try:
            with stage(STAGE):
                reply = self._ask(
                    self.profile_prompt,
                    profile_input(task["profile"], task["items"], task["tree"], task["today"]),
                    parse_profile,
                    "profile",
                    usage,
                )
        finally:
            add_usage(self.usage, usage, self._lock)
        skills, frames = build_profile(reply)
        self._report(f"profile: {len(skills)} skill phrases, {len(frames)} summary frames; {self._cost(usage)}")
        return {"answers": [(task["position"], ("profile", skills, frames))]}

    def _collect_answers(self, state: WriteState) -> dict:
        return {"collected": in_order(state["answers"])}

    # --- one call -------------------------------------------------------------------------------------
    def _ask(self, prompt: str, user: str, parse: Callable[[Any], T], what: str, usage: Usage) -> T:
        """The parsed reply; one that does not parse goes back once with what was wrong, then fails.

        The reply is taken as text and parsed here (:func:`json_object`), so a broken JSON goes back
        with where it breaks."""
        messages = [{"role": "user", "content": user}]
        for attempt in (1, 2):
            raw = self.llm.call_llm(ROLE, str, prompt, messages, usage=usage)
            try:
                return parse(json_object(raw))
            except ValueError as error:
                problem = str(error)
            logger.warning("  ✗ [%s] %s: reply %d/2 rejected:\n%s", ROLE, what, attempt, problem)
            messages += [
                {"role": "assistant", "content": raw},
                {
                    "role": "user",
                    "content": f"Your previous reply was rejected:\n{problem}\n\n"
                    "Reply again with the complete, corrected output only.",
                },
            ]
        raise ModelOutputError(f"{ROLE}: pool {what}: the reply was rejected:\n{problem}", stage=STAGE)

    @staticmethod
    def _cost(usage: Usage) -> str:
        return (
            f"{usage.calls} call(s), {usage.seconds:.0f}s, tokens {usage.input_tokens} in, "
            f"{usage.thinking_tokens} thinking, {usage.output_tokens} out"
        )

    def _report(self, line: str) -> None:
        logger.info("%s", line)
        if self.progress is not None:
            self.progress(STAGE, line)


def _with_rules(prompt: str, rules: str) -> str:
    """A system prompt with the writing rules appended."""
    return prompt.rstrip("\n") + "\n\n" + rules.strip("\n") + "\n"


__all__ = [
    "PoolWriter",
    "batches",
    "build_profile",
    "build_story",
    "edited_pieces",
    "experience_input",
    "json_object",
    "ladder_input",
    "parse_experience",
    "parse_ladder",
    "parse_profile",
    "profile_input",
]
