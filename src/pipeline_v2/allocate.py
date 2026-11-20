"""The allocation: a job description's requirements placed on the competence tree, in one call on ``allocate``.

The prompt is v1's JD evaluation with a PLACEMENT block appended (``sys_prompt_allocate.txt``);
code appends the tree, one line per node: id, name, aliases, counts-as terms. The reply is the
JD's requirements, each with a node and a relation. It is taken as text and parsed here
(:func:`~.pool.json_object`); one that does not parse goes back once with the fault, then fails.

Then code's rules, as the S0 probe's and S2's ``placed``:

- an unknown node becomes no node, relation ``none``; so does a relation other than ``same``,
  ``broader``, ``narrower`` or ``sibling``;
- landing: the node's status (:func:`~.tree_store.leaf_status`: proven, weak, ghost), or nowhere
  without a node;
- a ghost leaf placed ``same`` becomes ``sibling``: the JD asks the leaf itself, which the profile
  cannot prove;
- the meta items ("success stories", "concrete examples") are dropped.

A requirement's literal terms are its ``keywords`` as the prompt returns them, in English; nothing
checks the JD text holds them (that check gave Italian JDs nothing). :func:`points` turns the
placed requirements into each node's points.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Literal, Optional, Sequence

from pydantic import BaseModel, ConfigDict

from common.errors import ModelOutputError
from common.models import ModelSelector, Usage
from common.observability import stage

from .pool import _validated, json_object
from .store import Items, _Strict
from .tree_store import Tree, leaf_status, level, parent

logger = logging.getLogger("pipeline_v2.allocate")

ROLE = "allocate"
STAGE = "Job reading"
#: Items the JD evaluation adds to every list; they ask for no competence.
META = ("success stories", "concrete examples")
#: What a requirement placed with each relation counts of its importance (S0's selector).
RELATION = {"same": 1.0, "broader": 1.0, "sibling": 1.0, "narrower": 0.5, "none": 0.0}


class Requirement(_Strict):
    id: int
    name: str  # the requirement as the prompt names it, English
    importance: int  # 0-100
    keywords: List[str] = []  # its literal terms, English
    node: Optional[str] = None
    relation: Literal["same", "broader", "narrower", "sibling", "none"] = "none"
    landing: Literal["proven", "weak", "ghost", "nowhere"] = "nowhere"


class Allocation(_Strict):
    title: str  # the job title as the JD writes it
    requirements: List[Requirement]


# --- the reply ---------------------------------------------------------------------------------
class ReplyRequirement(BaseModel):
    model_config = ConfigDict(coerce_numbers_to_str=True)

    id: int = 0
    requirement: str
    keywords: List[str] = []
    importance: int
    node: Optional[str] = None
    relation: str = "none"


class Reply(BaseModel):
    title: str = ""
    requirements: List[ReplyRequirement]


def tree_lines(tree: Tree) -> str:
    """The tree as the prompt shows it: one line per node, id, name, aliases, counts-as terms."""
    return "\n".join(
        f"{n.id} {n.name}"
        + (f" | aliases: {', '.join(n.aliases)}" if n.aliases else "")
        + (f" | counts as: {', '.join(n.counts_as)}" if n.counts_as else "")
        for n in tree.nodes
    )


def placed(reply: Reply, tree: Tree, status: Dict[str, str]) -> Allocation:
    """The reply with code's rules applied (see the module)."""
    known = {n.id for n in tree.nodes}
    out: List[Requirement] = []
    for i, r in enumerate(reply.requirements, 1):
        if r.requirement.strip().lower() in META:
            continue
        node = r.node if r.node in known else None
        if r.node and node is None:
            logger.warning("  ✗ [%s] %r: unknown node %r, placed nowhere", ROLE, r.requirement, r.node)
        said = r.relation.strip().lower()
        relation = said if node is not None and said in ("same", "broader", "narrower", "sibling") else "none"
        landing = status[node] if node is not None else "nowhere"
        if landing == "ghost" and level(node) == 3 and relation == "same":
            relation = "sibling"
        out.append(
            Requirement(
                id=r.id or i,
                name=r.requirement,
                importance=r.importance,
                keywords=r.keywords,
                node=node,
                relation=relation,
                landing=landing,
            )
        )
    return Allocation(title=reply.title, requirements=out)


def points(tree: Tree, requirements: Sequence[Requirement]) -> Dict[str, float]:
    """Each node's points (S0's selector): a requirement adds importance / 100 x its relation's
    share to its node; each node above gets half of that, halved again per level up, as the
    maximum over its children, not the sum."""
    known = {n.id for n in tree.nodes}
    own: Dict[str, float] = {}
    inherited: Dict[str, float] = {}
    for r in requirements:
        value = r.importance / 100 * RELATION[r.relation]
        if r.node not in known or value <= 0:
            continue
        own[r.node] = own.get(r.node, 0.0) + value
        up, share = parent(r.node), value / 2
        while up is not None:
            inherited[up] = max(inherited.get(up, 0.0), share)
            up, share = parent(up), share / 2
    return {n: round(own.get(n, 0.0) + inherited.get(n, 0.0), 6) for n in set(own) | set(inherited)}


class Allocator:
    """Places a job description's requirements on a profile's competence tree."""

    def __init__(self, *, llm: ModelSelector, prompt: str) -> None:
        self.llm = llm
        self.prompt = prompt
        #: Every model call of every allocation, failed ones included.
        self.usage = Usage()

    def allocate(self, jd: str, tree: Tree, items: Items) -> Allocation:
        """The JD's requirements, placed. Raises :class:`ModelOutputError` when the reply does not
        parse twice."""
        system = self.prompt.rstrip("\n") + "\n" + tree_lines(tree) + "\n"
        messages = [{"role": "user", "content": jd}]
        with stage(STAGE):
            for attempt in (1, 2):
                raw = self.llm.call_llm(ROLE, str, system, messages, usage=self.usage)
                try:
                    reply = _validated(Reply, json_object(raw))
                    break
                except ValueError as error:
                    problem = str(error)
                logger.warning("  ✗ [%s] reply %d/2 rejected:\n%s", ROLE, attempt, problem)
                messages += [
                    {"role": "assistant", "content": raw},
                    {
                        "role": "user",
                        "content": f"Your previous reply was rejected:\n{problem}\n\n"
                        "Reply again with the complete, corrected output only.",
                    },
                ]
            else:
                raise ModelOutputError(f"{ROLE}: the reply was rejected:\n{problem}", stage=STAGE)
        return placed(reply, tree, leaf_status(tree, items))


def allocation_markdown(allocation: Allocation, tree: Tree, top: int = 10) -> str:
    """The view: each requirement's node, relation and landing, then the nodes with the most points."""
    names = {n.id: n.name for n in tree.nodes}
    rows = [
        f"| {r.id} | {r.name} | {r.importance} | {', '.join(r.keywords)} | "
        f"{f'{r.node} {names[r.node]}' if r.node else '-'} | {r.relation} | {r.landing} |"
        for r in allocation.requirements
    ]
    ranked = sorted(points(tree, allocation.requirements).items(), key=lambda kv: (-kv[1], kv[0]))[:top]
    return (
        f"# Allocation: {allocation.title}\n\n"
        "| # | Requirement | Importance | Keywords | Node | Relation | Landing |\n"
        "|---|---|---|---|---|---|---|\n" + "\n".join(rows) + "\n\n"
        f"## Top {top} nodes by points\n\n| Node | Points |\n|---|---|\n"
        + "\n".join(f"| {n} {names[n]} | {p:.3f} |" for n, p in ranked)
        + "\n"
    )


__all__ = ["META", "RELATION", "Allocation", "Allocator", "Requirement", "allocation_markdown", "placed", "points"]
