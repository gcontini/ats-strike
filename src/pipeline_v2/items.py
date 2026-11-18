"""Base items and stories: the profile's experiences cut by the ``compile`` role, one call each.

Each call sees one experience (title, company, dates, context, role summary and its numbered
duties) and nothing else of the profile. Its reply is plain JSON, validated here: a reply that
does not validate goes back once with what was wrong, then fails. Code then numbers the items,
checks every source span against the profile, drops what it cannot trust (each with its reason),
puts experience-level context in the experience's list and dissolves a story that holds no
action or result. What is wrong but harmless stays, for :func:`~.store.validate` to report.

An experience whose profile JSON has not changed since ``previous`` was compiled is copied as it
is, hand edits included; a changed one is compiled again, and the hand edits of its old version
are listed for the user to re-apply.
"""

from __future__ import annotations

import json
import logging
import operator
from dataclasses import fields
from datetime import date
from threading import Lock
from typing import (Annotated, Any, Callable, Dict, Iterable, List, Literal, Mapping, Optional,
                    Tuple, TypedDict, Union)

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send
from pydantic import BaseModel, ValidationError, model_validator

from pipeline_v1.errors import ModelOutputError
from pipeline_v1.models import ModelSelector, Usage
from pipeline_v1.observability import stage

from .store import (DutyWeight, ExperienceItems, Item, Items, Story, check_sources, coverage,
                    experience_sha256, is_work, months, profile_sha256)

logger = logging.getLogger("pipeline_v2.items")

ROLE = "compile"
STAGE = "items"
#: The experience's fields the call is shown; nothing else of the profile.
SHOWN = ("title", "company", "dates", "context", "role_summary", "duties")

TYPES = ("context", "action", "result")
AGENCIES = ("led", "did", "supported", "")

#: Called with "items" and one line per experience.
ProgressFn = Callable[[str, str], None]
Built = Tuple[ExperienceItems, List[Item]]


# --- the reply ------------------------------------------------------------------
class ReplyItem(BaseModel):
    """Type and agency are plain strings here: a bad one drops the item, not the reply."""

    n: int
    duty: int
    type: str
    text: str
    source: List[str] = []
    agency: str = ""
    scope: str = ""
    tech: List[str] = []
    tags: List[str] = []
    story: int                # 0 = experience context


class ReplyStory(BaseModel):
    n: int
    title: str
    anchor: List[int] = []


class ReplyDuty(BaseModel):
    duty: int
    weight: Literal[1, 2, 3]
    why: str = ""


class Reply(BaseModel):
    items: List[ReplyItem]
    stories: List[ReplyStory]
    duties: List[ReplyDuty]

    @model_validator(mode="after")
    def _numbers(self) -> "Reply":
        for what, ns in (("item", [i.n for i in self.items]), ("story", [s.n for s in self.stories])):
            twice = sorted({n for n in ns if ns.count(n) > 1})
            if twice:
                raise ValueError(f"{what} numbers used twice: {twice}")
        if 0 in {s.n for s in self.stories}:
            raise ValueError("story 0 means experience context: number stories from 1")
        return self


def _describe(error: ValidationError) -> str:
    """One line per problem, worded for the model."""
    return "\n".join(f"- {'.'.join(map(str, e['loc'])) or '(whole reply)'}: {e['msg']}"
                     for e in error.errors())


# --- pure ---------------------------------------------------------------------------
def shown(exp: Mapping[str, Any]) -> Dict[str, Any]:
    """What the call sees of one experience."""
    return {key: exp.get(key, "") for key in SHOWN}


def in_order(answers: List[Tuple[int, Any]]) -> List[Any]:
    """A fanned-out step's answers, back in the order they were sent."""
    return [answer for _, answer in sorted(answers, key=lambda pair: pair[0])]


def _drop_reason(exp: Mapping[str, Any], raw: ReplyItem, stories: Iterable[int]) -> Optional[str]:
    n_duties = len(exp.get("duties") or [])
    if raw.type not in TYPES:
        return f"bad type {raw.type!r}"
    if raw.agency not in AGENCIES:
        return f"bad agency {raw.agency!r}"
    if not -1 <= raw.duty < n_duties:
        return f"unknown duty {raw.duty}"
    if raw.story != 0 and raw.story not in stories:
        return f"unknown story {raw.story}"
    if raw.story == 0 and raw.type != "context":
        return f"an {raw.type} outside any story"
    if not raw.source:
        return "no source"
    bad = check_sources(exp, raw)
    if bad:
        return f"span not verbatim: {bad}"
    return None


def build_experience(exp: Mapping[str, Any], reply: Union[Reply, Mapping[str, Any]],
                     today: date) -> Built:
    """One experience's store entry and items, from the model's reply.

    Items and stories are numbered in reply order, kept ones only. A story's items are its
    context first, then its actions and results, each in profile order. ``story: 0`` and the
    context of a story with no action or result go to the experience's context list.
    """
    reply = Reply.model_validate(reply)
    e = exp["id"]
    story_ns = [s.n for s in reply.stories]

    kept: List[Tuple[ReplyItem, int]] = []   # (item, its story's n; 0 = experience context)
    dropped: List[dict] = []
    for raw in reply.items:
        reason = _drop_reason(exp, raw, story_ns)
        if reason:
            dropped.append({**raw.model_dump(), "reason": reason})
        else:
            kept.append((raw, raw.story))

    # A story with no action or result left is dissolved into the experience context.
    has_work = {s for raw, s in kept if raw.type in ("action", "result")}
    kept = [(raw, s if s in has_work else 0) for raw, s in kept]
    stories = [s for s in reply.stories if s.n in has_work]
    story_id = {s.n: f"e{e}.s{k:02d}" for k, s in enumerate(stories, 1)}
    item_id = {raw.n: f"e{e}.i{k:02d}" for k, (raw, _) in enumerate(kept, 1)}

    items = [
        Item(id=item_id[raw.n], experience=e, duty=raw.duty, type=raw.type, text=raw.text,
             source=raw.source, agency=raw.agency, scope=raw.scope, tech=raw.tech, tags=raw.tags,
             story=story_id.get(s))
        for raw, s in kept
    ]

    def profile_order(group: List[Item]) -> List[str]:
        return [i.id for i in sorted(group, key=lambda i: i.duty)]  # stable: reply order within a duty

    built_stories = []
    for s in stories:
        members = [i for i in items if i.story == story_id[s.n]]
        built_stories.append(Story(
            id=story_id[s.n], experience=e, title=s.title,
            items=profile_order([i for i in members if not is_work(i)])
            + profile_order([i for i in members if is_work(i)]),
            anchor=[item_id[n] for n in s.anchor if n in item_id],
        ))

    weights: Dict[int, DutyWeight] = {}
    for d in reply.duties:
        if 0 <= d.duty < len(exp.get("duties") or []) and d.duty not in weights:
            weights[d.duty] = DutyWeight(duty=d.duty, weight=d.weight, why=d.why)

    entry = ExperienceItems(
        id=e, sha256=experience_sha256(exp), months=months(exp.get("dates", ""), today),
        weights=[weights[d] for d in sorted(weights)],
        context=profile_order([i for i in items if i.story is None]),
        stories=built_stories, coverage=round(coverage(exp, items), 3), dropped=dropped,
    )
    return entry, items


def hand_edits(old: ExperienceItems, old_items: List[Item], new: ExperienceItems) -> List[dict]:
    """What the user changed in the old version of a recompiled experience: items marked
    ``edited``, weights the new compile does not repeat, merged stories."""
    edits: List[dict] = [{"what": "item", **i.model_dump()} for i in old_items if i.edited]
    now = {w.duty: w.weight for w in new.weights}
    edits += [{"what": "weight", "duty": w.duty, "weight": w.weight, "why": w.why, "new": now.get(w.duty)}
              for w in old.weights if now.get(w.duty) != w.weight]
    edits += [{"what": "story", "id": s.id, "title": s.title, "merged": s.merged}
              for s in old.stories if s.merged]
    return edits


# --- the graph ------------------------------------------------------------------------
class ItemsState(TypedDict, total=False):
    """The experiences to compile, and their answers, each tagged with its position."""

    todo: List[Tuple[int, dict]]
    today: date
    answers: Annotated[List[Tuple[int, Built]], operator.add]
    compiled: List[Built]


class ItemCompiler:
    """Cuts a profile's experiences into base items and stories."""

    def __init__(self, *, llm: ModelSelector, system_prompt: str,
                 progress: Optional[ProgressFn] = None) -> None:
        self.llm = llm
        self.system_prompt = system_prompt
        self.progress = progress
        #: Every model call of every compile, failed ones included.
        self.usage = Usage()
        self._lock = Lock()

    def compile(self, profile: Mapping[str, Any], *, today: date,
                previous: Optional[Items] = None, force: Iterable[int] = ()) -> Items:
        """The store's items for ``profile``: unchanged experiences of ``previous`` copied, the
        others (and those in ``force``, by experience id) compiled, one call each, side by side."""
        force = set(force)
        old = {x.id: x for x in previous.experiences} if previous else {}
        old_items: Dict[int, List[Item]] = {}
        for item in previous.items if previous else []:
            old_items.setdefault(item.experience, []).append(item)

        copied: Dict[int, Built] = {}
        todo: List[Tuple[int, dict]] = []
        for n, exp in enumerate(profile["experiences"]):
            x = old.get(exp["id"])
            if x is not None and x.sha256 == experience_sha256(exp) and exp["id"] not in force:
                copied[n] = (x, old_items.get(exp["id"], []))
                self._report(f"e{exp['id']}: unchanged, copied")
            else:
                todo.append((n, dict(exp)))

        fresh = iter(self._graph().invoke({"todo": todo, "today": today})["compiled"] if todo else [])
        experiences: List[ExperienceItems] = []
        items: List[Item] = []
        for n, exp in enumerate(profile["experiences"]):
            if n in copied:
                x, its = copied[n]
            else:
                x, its = next(fresh)
                if exp["id"] in old:
                    x.previous = hand_edits(old[exp["id"]], old_items.get(exp["id"], []), x)
            experiences.append(x)
            items += its
        return Items(profile_sha256=profile_sha256(profile), experiences=experiences, items=items)

    def _graph(self):
        """START -> compile (one per experience) -> collect -> END"""
        graph = StateGraph(ItemsState)
        graph.add_node("compile", self._compile)
        graph.add_node("collect", self._collect)
        graph.add_conditional_edges(START, self._each_experience, ["compile"])
        graph.add_edge("compile", "collect")
        graph.add_edge("collect", END)
        return graph.compile()

    def _each_experience(self, state: ItemsState) -> List[Send]:
        return [Send("compile", {"position": n, "experience": exp, "today": state["today"]})
                for n, exp in state["todo"]]

    def _compile(self, task: dict) -> dict:
        exp = task["experience"]
        usage = Usage()
        try:
            with stage(STAGE):
                built = build_experience(exp, self._ask(exp, usage), task["today"])
        finally:
            self._count(usage)
        entry, items = built
        self._report(
            f"e{exp['id']}: {len(items)} items, {len(entry.stories)} stories, "
            f"{len(entry.dropped)} dropped, {entry.coverage:.0%} covered; {usage.calls} call(s), "
            f"{usage.seconds:.0f}s, tokens {usage.input_tokens} in, {usage.thinking_tokens} thinking, "
            f"{usage.output_tokens} out"
        )
        return {"answers": [(task["position"], built)]}

    def _collect(self, state: ItemsState) -> dict:
        return {"compiled": in_order(state["answers"])}

    def _ask(self, exp: Mapping[str, Any], usage: Usage) -> Reply:
        """The reply for one experience; one that does not validate goes back once."""
        messages = [{"role": "user",
                     "content": "EXPERIENCE:\n" + json.dumps(shown(exp), ensure_ascii=False, indent=1)}]
        for attempt in (1, 2):
            raw = self.llm.call_llm(ROLE, dict, self.system_prompt, messages, usage=usage)
            try:
                return Reply.model_validate(raw)
            except ValidationError as error:
                problem = _describe(error)
            logger.warning("  ✗ [%s] e%s: reply %d/2 does not validate:\n%s", ROLE, exp["id"], attempt, problem)
            messages += [
                {"role": "assistant", "content": json.dumps(raw, ensure_ascii=False)},
                {"role": "user", "content": f"Your previous reply was rejected:\n{problem}\n\n"
                                            "Reply again with the complete, corrected output only."},
            ]
        raise ModelOutputError(f"{ROLE}: experience {exp['id']}: the reply does not validate:\n{problem}",
                               stage=STAGE)

    def _count(self, usage: Usage) -> None:
        """Add one experience's calls to the compiler's total; the calls run side by side."""
        with self._lock:
            for f in fields(Usage):
                setattr(self.usage, f.name, getattr(self.usage, f.name) + getattr(usage, f.name))

    def _report(self, line: str) -> None:
        logger.info("%s", line)
        if self.progress is not None:
            self.progress(STAGE, line)


__all__ = ["ItemCompiler", "Reply", "build_experience", "hand_edits", "in_order", "shown"]
