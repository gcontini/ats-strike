"""The competence tree: shaped by one ``compile`` call, then linked to the items one experience at a time.

The shape call sees every item, the market list and the profile's fixed sections (skills list,
certifications, languages) and returns the nodes. Code checks them: a first reply with a structural
fault (an id twice, a missing parent, a fourth level, a node with no children), a compound name or
a term the market asks often that no node carries goes back once with all of them; the second
reply fails on a structural fault only. Code then sets ``outside`` on the leaves the fixed sections
name literally. Then one attach call per experience, side by side, sees
the whole tree and that experience's items, context included, and returns each item's leaves.
Code drops a link it cannot use, with its reason, and clamps a context item's link to level 1.

The tree is stable; links follow the items. With a ``previous`` tree, an experience whose items
are unchanged keeps its links, hand edits included; a changed one is attached again, and the old
links of a hand-edited block are listed in ``previous``. ``reshape`` builds a new tree.
"""

from __future__ import annotations

import json
import logging
import operator
from threading import Lock
from typing import (Annotated, Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence,
                    Tuple, TypedDict, TypeVar)

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send
from pydantic import BaseModel, ValidationError, field_validator

from pipeline_v1.errors import ModelOutputError
from pipeline_v1.models import ModelSelector, Usage
from pipeline_v1.observability import stage

from .items import ProgressFn, add_usage, in_order
from .store import Items
from .tree_store import (ExperienceLinks, Market, Node, Tree, is_compound, items_sha256, level,
                         market_sha256, propose_outside, structure_faults, unasked)

logger = logging.getLogger("pipeline_v2.tree")

ROLE = "compile"
STAGE = "tree"

T = TypeVar("T")


# --- what the calls see ----------------------------------------------------------------
def shape_input(items: Items, market: Market, *, skills: Sequence[str] = (),
                certifications: Sequence[str] = (), languages: str = "") -> str:
    """The shape call's user message."""
    item_lines = "\n".join(
        f"{i.id} [{i.type}] {i.text} | tags: {', '.join(i.tags)}" + (f" | tech: {', '.join(i.tech)}" if i.tech else "")
        for i in items.items)
    term_lines = "\n".join(
        f"{t.term} | jobs {t.jobs} | asked {100 * t.asked:.1f}% | share {100 * t.share:.1f}%" for t in market.terms)
    names = sorted({r.name for r in market.requirements})
    return (f"ITEMS:\n{item_lines}\n\n"
            f"FIXED SECTIONS:\nSkills list: {'; '.join(skills) or '-'}\n"
            f"Certifications: {'; '.join(certifications) or '-'}\nLanguages: {languages or '-'}\n\n"
            f"MARKET TERMS (jobs: of {market.build_jobs} sampled job descriptions; asked and share: of "
            f"{market.corpus_jobs}):\n{term_lines}\n\n"
            f"MARKET REQUIREMENTS:\n" + "\n".join(names))


def tree_lines(nodes: Sequence[Node]) -> str:
    """One line per node: id, name, aliases, counts-as terms."""
    return "\n".join(
        f"{n.id} {n.name}" + (f" | aliases: {', '.join(n.aliases)}" if n.aliases else "")
        + (f" | counts as: {', '.join(n.counts_as)}" if n.counts_as else "")
        for n in nodes)


def attach_input(nodes: Sequence[Node], items: Items, experience: int) -> str:
    """An attach call's user message: the whole tree, and one experience's items."""
    item_lines = "\n".join(
        f"{i.id} [{i.type}, {i.agency or '-'}] {i.text}" + (f" | tech: {', '.join(i.tech)}" if i.tech else "")
        for i in items.items if i.experience == experience)
    return f"TREE:\n{tree_lines(nodes)}\n\nITEMS:\n{item_lines}"


# --- the replies --------------------------------------------------------------------------
class ReplyNode(BaseModel):
    id: str
    name: str
    aliases: List[str] = []
    counts_as: List[str] = []
    proven: bool = False

    @field_validator("id", mode="before")
    @classmethod
    def _text(cls, value: Any) -> Any:
        return str(value) if isinstance(value, (int, float)) else value


class ShapeReply(BaseModel):
    nodes: List[ReplyNode]


class AttachReply(BaseModel):
    attach: Dict[str, Any]


def _describe(error: ValidationError) -> str:
    return "\n".join(f"- {'.'.join(map(str, e['loc'])) or '(whole reply)'}: {e['msg']}" for e in error.errors())


def _key(node_id: str) -> List[int]:
    return [int(x) if x.isdigit() else 0 for x in node_id.split(".")]


def shape_nodes(raw: Any) -> List[Node]:
    """The nodes of a shape reply, sorted parents before children, not yet checked. Raises
    ``ValueError`` when the reply is not a list of nodes at all."""
    try:
        reply = ShapeReply.model_validate(raw)
    except ValidationError as error:
        raise ValueError(_describe(error)) from None
    return sorted((Node(id=n.id.strip(), name=n.name.strip(), aliases=n.aliases, counts_as=n.counts_as,
                        shape_proven=n.proven) for n in reply.nodes), key=lambda n: _key(n.id))


def parse_shape(raw: Any) -> List[Node]:
    """The nodes of a shape reply. Raises ``ValueError`` with the faults that make it unusable;
    naming faults (a compound name, an asked term no node carries) are :func:`naming_faults`."""
    nodes = shape_nodes(raw)
    faults = structure_faults(nodes)
    if faults:
        raise ValueError("\n".join(f"- {f}" for f in faults))
    return nodes


def naming_faults(nodes: Sequence[Node], market: Market) -> str:
    """What the first shape reply gets back besides its structural faults: compound names, and the
    terms the market asks often that no node carries; "" when there are none."""
    compound = [n for n in nodes if is_compound(n.name)]
    missing = unasked(nodes, market)
    if not (compound or missing):
        return ""
    lines = ["Fix these names too."]
    if compound:
        lines += ["Compound names: give each node one plain term, and make the other part another node "
                  "or an alias where it is the same thing:"]
        lines += [f"- {n.id} {n.name}" for n in compound]
    if missing:
        lines += ["Terms job descriptions ask often that no node carries. Add each that is a skill or a "
                  "competence as a name, alias or counts-as term of the node where it belongs, as a ghost "
                  "leaf (proven false) if the items cannot prove it. Skip degrees, job titles, language "
                  "levels and travel:"]
        lines += [f"- {t.term} | jobs {t.jobs} | asked {100 * t.asked:.1f}% | share {100 * t.share:.1f}%"
                  for t in missing]
    return "\n".join(lines)


def build_links(attach: Mapping[str, Any], nodes: Sequence[Node], items: Items, experience: int) -> ExperienceLinks:
    """One experience's links from an attach reply: a link to an unknown item, an unknown node or a
    non-leaf, or with a bad level, is dropped with its reason; a context link above 1 is clamped."""
    known = {n.id for n in nodes}
    own = {i.id: i for i in items.items if i.experience == experience}
    links: Dict[str, Dict[str, int]] = {}
    dropped: List[dict] = []
    for iid, leaves in attach.items():
        if not isinstance(leaves, Mapping):
            dropped.append({"item": iid, "leaf": None, "level": leaves, "reason": "not an object of leaves"})
            continue
        for leaf, lvl in leaves.items():
            reason = None
            if iid not in own:
                reason = "not an item of this experience"
            elif leaf not in known:
                reason = "unknown node"
            elif level(leaf) != 3:
                reason = "not a leaf"
            elif isinstance(lvl, bool) or lvl not in (1, 2, 3):
                reason = "bad level"
            if reason:
                dropped.append({"item": iid, "leaf": leaf, "level": lvl, "reason": reason})
                continue
            links.setdefault(iid, {})[leaf] = 1 if own[iid].type == "context" else lvl
    return ExperienceLinks(experience=experience, items_sha256=items_sha256(items, experience),
                           links={iid: links[iid] for iid in own if iid in links}, dropped=dropped)


def old_links(block: ExperienceLinks) -> List[dict]:
    """A block's links, one dict each, for ``previous``."""
    return [{"item": iid, "leaf": leaf, "level": lvl} for iid, leaves in block.links.items()
            for leaf, lvl in leaves.items()]


# --- the graph ----------------------------------------------------------------------------
class AttachState(TypedDict, total=False):
    """The experiences to attach, the tree and items they are attached to, and the answers."""

    todo: List[Tuple[int, int]]          # (position, experience id)
    nodes: List[Node]
    items: Items
    answers: Annotated[List[Tuple[int, ExperienceLinks]], operator.add]
    attached: List[ExperienceLinks]


class TreeBuilder:
    """Shapes the competence tree from the items and the market, and links each experience's
    items to its leaves."""

    def __init__(self, *, llm: ModelSelector, shape_prompt: str, attach_prompt: str,
                 progress: Optional[ProgressFn] = None) -> None:
        self.llm = llm
        self.shape_prompt = shape_prompt
        self.attach_prompt = attach_prompt
        self.progress = progress
        #: Every model call of every build, failed ones included.
        self.usage = Usage()
        self._lock = Lock()

    def build(self, items: Items, market: Market, *, skills: Sequence[str] = (),
              certifications: Sequence[str] = (), languages: str = "",
              previous: Optional[Tree] = None, reshape: bool = False,
              force: Iterable[int] = ()) -> Tree:
        """The tree for ``items``: the shape of ``previous`` (a new one when there is none or on
        ``reshape``), its links copied for experiences whose items are unchanged, the others (and
        those in ``force``, by experience id) attached, one call each, side by side."""
        force = set(force)
        if previous is None or reshape:
            nodes = self._shape(items, market, skills, certifications, languages)
            market_hash, old = market_sha256(market), {}
        else:
            nodes, market_hash = previous.nodes, previous.market_sha256
            old = {b.experience: b for b in previous.experiences}

        copied: Dict[int, ExperienceLinks] = {}
        todo: List[Tuple[int, int]] = []
        for n, x in enumerate(items.experiences):
            block = old.get(x.id)
            if block is not None and block.items_sha256 == items_sha256(items, x.id) and x.id not in force:
                copied[n] = block
                self._report(f"e{x.id}: items unchanged, links copied")
            else:
                todo.append((n, x.id))

        fresh = iter(self._graph().invoke({"todo": todo, "nodes": nodes, "items": items})["attached"]
                     if todo else [])
        blocks: List[ExperienceLinks] = []
        for n, x in enumerate(items.experiences):
            if n in copied:
                blocks.append(copied[n])
                continue
            block = next(fresh)
            if x.id in old and old[x.id].edited:
                block.previous = old_links(old[x.id])
            blocks.append(block)
        return Tree(market_sha256=market_hash, nodes=nodes, experiences=blocks)

    # --- the shape --------------------------------------------------------------------
    def _shape(self, items: Items, market: Market, skills: Sequence[str], certifications: Sequence[str],
               languages: str) -> List[Node]:
        usage = Usage()
        user = shape_input(items, market, skills=skills, certifications=certifications, languages=languages)
        try:
            with stage(STAGE):
                nodes = self._ask_shape(user, market, usage)
        finally:
            add_usage(self.usage, usage, self._lock)
        outside = propose_outside(nodes, skills, certifications, languages)
        nodes = [n.model_copy(update={"outside": outside[n.id]}) if n.id in outside else n for n in nodes]
        count = {k: sum(level(n.id) == k for n in nodes) for k in (1, 2, 3)}
        self._report(
            f"shape: {len(nodes)} nodes ({count[1]} disciplines, {count[2]} competences, {count[3]} leaves, "
            f"{sum(level(n.id) == 3 and not n.shape_proven for n in nodes)} expected ghosts), "
            f"{len(outside)} outside; {self._cost(usage)}")
        return nodes

    def _ask_shape(self, user: str, market: Market, usage: Usage) -> List[Node]:
        """The shape's nodes. The first reply goes back once with its structural faults and its
        naming faults together; the second fails on a structural fault only."""
        messages = [{"role": "user", "content": user}]
        raw = self.llm.call_llm(ROLE, dict, self.shape_prompt, messages, usage=usage)
        try:
            nodes = shape_nodes(raw)
        except ValueError as error:
            problem = str(error)
        else:
            faults = "\n".join(f"- {f}" for f in structure_faults(nodes))
            naming = naming_faults(nodes, market)
            if not (faults or naming):
                return nodes
            problem = "\n\n".join(x for x in (faults, naming) if x)
        logger.warning("  ✗ [%s] shape: reply 1/2 sent back:\n%s", ROLE, problem)
        messages += [
            {"role": "assistant", "content": json.dumps(raw, ensure_ascii=False)},
            {"role": "user", "content": f"Your previous reply was rejected:\n{problem}\n\n"
                                        "Reply again with the complete, corrected output only."},
        ]
        raw = self.llm.call_llm(ROLE, dict, self.shape_prompt, messages, usage=usage)
        try:
            return parse_shape(raw)
        except ValueError as error:
            raise ModelOutputError(f"{ROLE}: tree shape: the reply was rejected:\n{error}", stage=STAGE) from None

    # --- the attach graph ------------------------------------------------------------
    def _graph(self):
        """START -> attach (one per experience) -> collect -> END"""
        graph = StateGraph(AttachState)
        graph.add_node("attach", self._attach)
        graph.add_node("collect", self._collect)
        graph.add_conditional_edges(START, self._each_experience, ["attach"])
        graph.add_edge("attach", "collect")
        graph.add_edge("collect", END)
        return graph.compile()

    def _each_experience(self, state: AttachState) -> List[Send]:
        return [Send("attach", {"position": n, "experience": e, "nodes": state["nodes"], "items": state["items"]})
                for n, e in state["todo"]]

    def _attach(self, task: dict) -> dict:
        e, nodes, items = task["experience"], task["nodes"], task["items"]
        usage = Usage()
        try:
            with stage(STAGE):
                reply = self._ask(self.attach_prompt, attach_input(nodes, items, e), _parse_attach, f"e{e}", usage)
                block = build_links(reply, nodes, items, e)
        finally:
            add_usage(self.usage, usage, self._lock)
        work = [i.id for i in items.items if i.experience == e and i.type in ("action", "result")]
        self._report(
            f"e{e}: {sum(map(len, block.links.values()))} links, {sum(i not in block.links for i in work)} of "
            f"{len(work)} action and result items unlinked, {len(block.dropped)} dropped; {self._cost(usage)}")
        return {"answers": [(task["position"], block)]}

    def _collect(self, state: AttachState) -> dict:
        return {"attached": in_order(state["answers"])}

    # --- one call -----------------------------------------------------------------------
    def _ask(self, prompt: str, user: str, parse: Callable[[Any], T], what: str, usage: Usage) -> T:
        """The parsed reply; one that does not parse goes back once with what was wrong, then fails."""
        messages = [{"role": "user", "content": user}]
        for attempt in (1, 2):
            raw = self.llm.call_llm(ROLE, dict, prompt, messages, usage=usage)
            try:
                return parse(raw)
            except ValueError as error:
                problem = str(error)
            logger.warning("  ✗ [%s] %s: reply %d/2 rejected:\n%s", ROLE, what, attempt, problem)
            messages += [
                {"role": "assistant", "content": json.dumps(raw, ensure_ascii=False)},
                {"role": "user", "content": f"Your previous reply was rejected:\n{problem}\n\n"
                                            "Reply again with the complete, corrected output only."},
            ]
        raise ModelOutputError(f"{ROLE}: tree {what}: the reply was rejected:\n{problem}", stage=STAGE)

    @staticmethod
    def _cost(usage: Usage) -> str:
        return (f"{usage.calls} call(s), {usage.seconds:.0f}s, tokens {usage.input_tokens} in, "
                f"{usage.thinking_tokens} thinking, {usage.output_tokens} out")

    def _report(self, line: str) -> None:
        logger.info("%s", line)
        if self.progress is not None:
            self.progress(STAGE, line)


def _parse_attach(raw: Any) -> Dict[str, Any]:
    try:
        return AttachReply.model_validate(raw).attach
    except ValidationError as error:
        raise ValueError(_describe(error)) from None


__all__ = ["TreeBuilder", "attach_input", "build_links", "naming_faults", "parse_shape", "shape_input",
           "shape_nodes", "tree_lines"]
