"""The pool's review: v1's duties validation, one experience at a time, on the ``validation`` role.

Each experience is one review, all of them side by side (LangGraph ``Send``). The model is shown
the experience's items as the evidence (EXPERIENCE) and every piece written from them (GENERATED
EXPERIENCE): the role summary, and as numbered duties the compact line, every ladder bullet and
every group bullet, their ids in ``duty_ids``. It reports each fault as a ``replace_in_duties``
call; code applies it and asks again, until a round calls nothing or :data:`MAX_ROUNDS` rounds
are done. Code refuses a call that changes how many printed lines a piece fills; a refusal, like
an ``old`` text not found, goes back to the model as a FAILED REPLACEMENT in the next round.

Only text changes: ``uses``, ``covers`` and ``assumes`` stay as written. The review is the last
automatic pass on the pool. The tool below is only a schema; its docstring is prompt text.
"""

from __future__ import annotations

import json
import logging
import operator
from threading import Lock
from typing import Annotated, Any, Dict, List, Mapping, Optional, Tuple, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send
from pydantic import BaseModel, ConfigDict

from common.models import ModelSelector, Usage
from common.observability import stage

from .items import ProgressFn, add_usage, in_order
from .pool_store import BULLET_LINE, ROLE_LINE, ExperiencePool, Pool, lines
from .store import Items, _plain

logger = logging.getLogger("pipeline_v2.pool_review")

ROLE = "validation"
STAGE = "pool_review"
#: Rounds per experience, as v1's validation. After the last one the pool keeps the last edits.
MAX_ROUNDS = 5


class ReplaceInDuties(BaseModel):
    """Replace the exact text `old` with `new` in duty number `duty` (1 = first), or in every duty and in the role
    summary when `duty` is omitted.

    reason: the category (Exaggeration, Syntax or Logic) and the fact in EXPERIENCE that `old` contradicts.
    """

    model_config = ConfigDict(title="replace_in_duties")

    old: str
    new: str
    duty: Optional[int] = None
    reason: str = ""


# --- one experience's pieces ---------------------------------------------------------------------
class Piece(TypedDict):
    id: str  # "e1.role", "e1.compact", a bullet's id
    text: str
    width: int  # characters a printed line holds


def pieces_of(block: ExperiencePool) -> List[Piece]:
    """The role summary first, then the duties: the compact line, each story's ladder and groups."""
    e = block.experience
    out: List[Piece] = [
        {"id": f"e{e}.role", "text": block.role_summary.text, "width": ROLE_LINE},
        {"id": f"e{e}.compact", "text": block.compact.text, "width": BULLET_LINE},
    ]
    for s in block.stories:
        out += [{"id": r.id, "text": r.text, "width": BULLET_LINE} for r in s.ladder]
        out += [{"id": b.id, "text": b.text, "width": BULLET_LINE} for g in s.groups for b in g.bullets]
    return out


def with_texts(block: ExperiencePool, texts: Mapping[str, str]) -> ExperiencePool:
    """The block with each piece's text from ``texts`` (by piece id); everything else as it was."""
    data = block.model_dump()
    e = block.experience
    data["role_summary"]["text"] = texts.get(f"e{e}.role", data["role_summary"]["text"])
    data["compact"]["text"] = texts.get(f"e{e}.compact", data["compact"]["text"])
    for s in data["stories"]:
        for b in [*s["ladder"], *(x for g in s["groups"] for x in g["bullets"])]:
            b["text"] = texts.get(b["id"], b["text"])
    return ExperiencePool.model_validate(data)


def apply_replacement(pieces: List[Piece], args: Mapping[str, Any]) -> Tuple[Optional[str], List[dict]]:
    """Apply one replace_in_duties call to ``pieces`` (the role summary, then the duties), in place.

    Returns the error, None when applied, and each piece's change ``{"id", "before", "after"}``, the
    changes a refused call would have made included. Duty ``n`` is ``pieces[n]``; without a duty the
    call replaces in every piece. Nothing is applied when ``old`` is found nowhere, or when the
    replacement changes the printed lines of a piece it hits.
    """
    duty = args.get("duty")
    targets = [duty] if duty else range(len(pieces))
    hits = [i for i in targets if 0 <= i < len(pieces) and (duty is None or i > 0) and args["old"] in pieces[i]["text"]]
    if not hits:
        where = f"duty {duty}" if duty else "any duty or the role summary"
        return f"{args['old']!r} not found in {where}", []
    after = {i: pieces[i]["text"].replace(args["old"], args["new"]) for i in hits}

    def name(i: int) -> str:
        return f"duty {i}" if i else "the role summary"

    wrong = [
        f"{name(i)} would fill {lines(after[i], pieces[i]['width'])} printed line(s), not "
        f"{lines(pieces[i]['text'], pieces[i]['width'])} ({len(after[i])} characters, {pieces[i]['width']} a line)"
        for i in hits
        if lines(after[i], pieces[i]["width"]) != lines(pieces[i]["text"], pieces[i]["width"])
    ]
    changes = [{"id": pieces[i]["id"], "before": pieces[i]["text"], "after": after[i]} for i in hits]
    if wrong:
        return f"{args['old']!r} refused: " + "; ".join(wrong), changes
    for i in hits:
        pieces[i]["text"] = after[i]
    return None, changes


def evidence(exp: Mapping[str, Any], items: Items) -> dict:
    """EXPERIENCE: the job's header and its items, what the pieces were written from."""
    return {
        **{k: _plain(exp.get(k)) for k in ("title", "company", "location", "dates")},
        "items": [
            {"id": i.id, "type": i.type, "agency": i.agency, "text": i.text, "scope": i.scope, "tech": i.tech}
            for i in items.items
            if i.experience == exp["id"]
        ],
    }


def generated(exp: Mapping[str, Any], pieces: List[Piece]) -> dict:
    """GENERATED EXPERIENCE: the header, the role summary, the duties and their ids."""
    return {
        **{k: _plain(exp.get(k)) for k in ("title", "company", "location", "dates")},
        "role_summary": pieces[0]["text"],
        "duties": [p["text"] for p in pieces[1:]],
        "duty_ids": [p["id"] for p in pieces[1:]],
    }


def dumps(value: Any) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False)


# --- the reviewer -----------------------------------------------------------------------------------
class ReviewState(TypedDict, total=False):
    tasks: List[dict]
    answers: Annotated[List[Tuple[int, Any]], operator.add]
    collected: List[Tuple[ExperiencePool, List[dict]]]


class PoolReviewer:
    """Reviews a pool's experience pieces against their items."""

    def __init__(self, *, llm: ModelSelector, prompt: str, progress: Optional[ProgressFn] = None) -> None:
        self.llm = llm
        self.prompt = prompt
        self.progress = progress
        #: Every model call of every review.
        self.usage = Usage()
        self._lock = Lock()

    def review(self, pool: Pool, items: Items, profile: Mapping[str, Any]) -> Tuple[Pool, List[dict]]:
        """The pool with the reviewed texts, and every call the model made, in order: ``{"experience",
        "round", "duty", "old", "new", "reason", "error", "changes"}``; ``error`` is None when applied."""
        exps = {e["id"]: e for e in profile["experiences"]}
        style = profile.get("writing_style") or ""
        tasks = [
            {"position": n, "block": block, "experience": exps[block.experience], "items": items, "style": style}
            for n, block in enumerate(pool.experiences)
        ]
        collected = self._graph().invoke({"tasks": tasks})["collected"] if tasks else []
        blocks = [block for block, _ in collected]
        calls = [call for _, block_calls in collected for call in block_calls]
        return pool.model_copy(update={"experiences": blocks}), calls

    def _graph(self):
        """START -> experience (one per experience) -> collect -> END"""
        graph = StateGraph(ReviewState)
        graph.add_node("experience", self._experience)
        graph.add_node("collect", self._collect)
        graph.add_conditional_edges(START, self._each, ["experience"])
        graph.add_edge("experience", "collect")
        graph.add_edge("collect", END)
        return graph.compile()

    def _each(self, state: ReviewState) -> List[Send]:
        return [Send("experience", task) for task in state["tasks"]]

    def _collect(self, state: ReviewState) -> dict:
        return {"collected": in_order(state["answers"])}

    def _experience(self, task: dict) -> dict:
        block, exp = task["block"], task["experience"]
        label = f"e{block.experience} review"
        pieces = pieces_of(block)
        shown = dumps(evidence(exp, task["items"]))
        style = f"WRITING STYLE:\n{task['style']}\n\n" if task["style"] else ""
        calls: List[dict] = []
        usage = Usage()
        failed: List[str] = []
        try:
            with stage(STAGE):
                for attempt in range(1, MAX_ROUNDS + 1):
                    text = (
                        f"{style}EXPERIENCE:\n{shown}\n\nATTEMPT: {attempt}\n\n"
                        f"GENERATED EXPERIENCE:\n{dumps(generated(exp, pieces))}"
                        + ("\n\nFAILED REPLACEMENTS:\n" + "\n".join(failed) if failed else "")
                    )
                    asked = self.llm.call_tools(
                        ROLE, ReplaceInDuties, self.prompt, [{"role": "user", "content": text}], usage=usage
                    )
                    failed = []
                    if not asked:
                        logger.info("[%s] attempt %d: OK", label, attempt)
                        break
                    for args in asked:
                        error, changes = apply_replacement(pieces, args)
                        logger.info(
                            "[%s] attempt %d: %s %r -> %r (%s)%s",
                            label,
                            attempt,
                            f"duty {args['duty']}" if args.get("duty") else "all",
                            args["old"],
                            args["new"],
                            args.get("reason", ""),
                            f"\n  {error}" if error else "",
                        )
                        if error:
                            failed.append(f"{error} (your fix: {args['new']!r})")
                        calls.append(
                            {
                                "experience": block.experience,
                                "round": attempt,
                                "duty": args.get("duty"),
                                "old": args["old"],
                                "new": args["new"],
                                "reason": args.get("reason", ""),
                                "error": error,
                                "changes": changes,
                            }
                        )
                else:
                    logger.info("[%s] %d rounds done: the pool keeps the last edits", label, MAX_ROUNDS)
        finally:
            add_usage(self.usage, usage, self._lock)
        applied = sum(c["error"] is None for c in calls)
        self._report(
            f"e{block.experience}: {applied} replacement(s) applied, {len(calls) - applied} not; "
            f"{usage.calls} call(s), {usage.seconds:.0f}s, tokens {usage.input_tokens} in, "
            f"{usage.thinking_tokens} thinking, {usage.output_tokens} out"
        )
        reviewed = with_texts(block, {p["id"]: p["text"] for p in pieces})
        return {"answers": [(task["position"], (reviewed, calls))]}

    def _report(self, line: str) -> None:
        logger.info("%s", line)
        if self.progress is not None:
            self.progress(STAGE, line)


# --- the readable record ----------------------------------------------------------------------------
def review_markdown(calls: List[dict]) -> str:
    """review.md: every change, before and after, then every replacement code did not apply."""
    applied = [c for c in calls if c["error"] is None]
    refused = [c for c in calls if c["error"] is not None]
    out = [
        "# Pool review",
        "",
        f"{len(calls)} replacement(s) asked: {len(applied)} applied, changing "
        f"{sum(len(c['changes']) for c in applied)} piece(s); {len(refused)} not applied.",
        "",
        "## Changes",
        "",
    ]
    for c in applied:
        for ch in c["changes"]:
            out += [
                f'- **{ch["id"]}** (round {c["round"]}): "{c["old"]}" → "{c["new"]}" — {c["reason"]}',
                f"  - before ({len(ch['before'])} ch): {ch['before']}",
                f"  - after ({len(ch['after'])} ch): {ch['after']}",
            ]
    if not applied:
        out.append("None.")
    out += ["", "## Not applied", ""]
    for c in refused:
        where = ", ".join(ch["id"] for ch in c["changes"]) or (
            f"e{c['experience']} duty {c['duty']}" if c["duty"] else f"e{c['experience']}, every piece"
        )
        out += [f'- **{where}** (round {c["round"]}): "{c["old"]}" → "{c["new"]}" — {c["reason"]}']
        out += [f"  - {c['error']}"]
        for ch in c["changes"]:
            out += [f"  - as it stays ({len(ch['before'])} ch): {ch['before']}"]
            out += [f"  - refused text ({len(ch['after'])} ch): {ch['after']}"]
    if not refused:
        out.append("None.")
    return "\n".join(out) + "\n"


__all__ = [
    "MAX_ROUNDS",
    "PoolReviewer",
    "ReplaceInDuties",
    "apply_replacement",
    "evidence",
    "generated",
    "pieces_of",
    "review_markdown",
    "with_texts",
]
