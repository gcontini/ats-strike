"""The store's second layer: one competence tree shared by every CV, and the market it is named by.

Three levels (discipline "4", competence "4.1", leaf "4.1.2"), named the way job descriptions
name things. Each experience's items are linked to the leaves they prove (S2). The file holds
what the calls and the user decided: nodes, links, hand notes. What follows from them — each
leaf's status, the checks, the market's frequencies — is recomputed on every load
(:func:`leaf_status`, :func:`checks`, :func:`validate_tree`), so it is current after a hand edit.

Pure: values in, values out.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from .store import Item, Items, _sha256, _Strict
from .terms import has_term, norm

#: A leaf linked to more items than this is too broad to select by.
BROAD = 15
#: A market term asked this often must be some node's name, alias or counts-as term.
OFTEN_SHARE = 0.05
OFTEN_JOBS = 3
#: Statuses, best first.
STATUSES = ("proven", "weak", "ghost")

_COMPOUND = re.compile(r"\s&\s|\sand\s|(?<=\w{4})/(?=\w{4})", re.I)


# --- the market ---------------------------------------------------------------------
class MarketTerm(_Strict):
    term: str            # the most common spelling
    jobs: int            # build runs whose key points list it
    asked: float         # share of corpus analyses listing it
    share: float         # share of corpus JD texts containing it (has_term)


class MarketRequirement(_Strict):
    name: str
    importance: int
    keywords: List[str]


class Market(_Strict):
    build_jobs: int
    corpus_jobs: int
    excluded: Dict[str, int]     # {"test": 50, "held_out": 10, "duplicates": n}
    terms: List[MarketTerm]
    requirements: List[MarketRequirement]


# --- the tree -----------------------------------------------------------------------
class Node(_Strict):
    id: str                       # "4", "4.1", "4.1.2"
    name: str                     # one plain market term, English
    aliases: List[str] = []       # the same thing, English and Italian
    counts_as: List[str] = []     # narrower things that prove it; never swapped
    shape_proven: bool = False    # what the shape call expected: a check, not a status
    outside: str = ""             # where the profile shows it outside the experiences
    confirmed: bool = False       # set by hand: the user backs a weak leaf


class ExperienceLinks(_Strict):
    experience: int
    items_sha256: str                   # of the experience's items as attached
    links: Dict[str, Dict[str, int]]    # item id -> {leaf id: level 1-3}
    dropped: List[dict] = []            # links refused, each with its reason
    edited: bool = False                # set by hand
    previous: List[dict] = []           # links of a hand-edited block that was re-attached


class Tree(_Strict):
    market_sha256: str            # of the market list the shape was built from
    nodes: List[Node]             # parents before children
    experiences: List[ExperienceLinks]


# --- small pure helpers -------------------------------------------------------------
def level(node_id: str) -> int:
    return node_id.count(".") + 1


def parent(node_id: str) -> Optional[str]:
    return node_id.rsplit(".", 1)[0] if "." in node_id else None


def terms_of(node: Node, counts_as: bool = True) -> List[str]:
    """The node's name and aliases, and its counts-as terms unless ``counts_as`` is false."""
    return [node.name, *node.aliases, *(node.counts_as if counts_as else [])]


def market_sha256(market: Market) -> str:
    return _sha256(market.model_dump())


def items_sha256(items: Items, experience: int) -> str:
    """SHA-256 of one experience's items as the attach call sees them: id, type, text, agency, tech."""
    return _sha256([[i.id, i.type, i.text, i.agency, i.tech] for i in items.items if i.experience == experience])


def is_compound(name: str) -> bool:
    """" & ", " and ", or a "/" between two words longer than 3 characters ("CI/CD" passes)."""
    return bool(_COMPOUND.search(name))


def _work(item: Optional[Item]) -> bool:
    return item is not None and item.type in ("action", "result")


def leaf_links(tree: Tree, items: Items) -> Dict[str, Dict[str, int]]:
    """leaf id -> {item id: level}, over every experience; a context link counted at level 1 at
    most, a link to an unknown item left out."""
    by_id = {i.id: i for i in items.items}
    out: Dict[str, Dict[str, int]] = defaultdict(dict)
    for block in tree.experiences:
        for iid, leaves in block.links.items():
            item = by_id.get(iid)
            if item is None:
                continue
            for leaf, lvl in leaves.items():
                out[leaf][iid] = min(lvl, 1) if item.type == "context" else lvl
    return dict(out)


# --- status (decision 3) --------------------------------------------------------------
def leaf_status(tree: Tree, items: Items) -> Dict[str, str]:
    """Every node's status, computed, never taken from the model.

    - proven: a level-3 link from an action or result; two or more action or result items at
      any level; ``outside``; or ``confirmed``.
    - weak: exactly one action or result item at level 1 or 2; or context items only.
    - ghost: nothing.

    A competence or discipline takes the best status among its children.
    """
    by_id = {i.id: i for i in items.items}
    linked = leaf_links(tree, items)
    status: Dict[str, str] = {}
    for node in tree.nodes:
        if level(node.id) != 3:
            continue
        mine = linked.get(node.id, {})
        work = {iid: lvl for iid, lvl in mine.items() if _work(by_id.get(iid))}
        if node.outside or node.confirmed or len(work) >= 2 or 3 in work.values():
            status[node.id] = "proven"
        elif mine:
            status[node.id] = "weak"
        else:
            status[node.id] = "ghost"
    for node in sorted(tree.nodes, key=lambda n: -level(n.id)):
        if level(node.id) < 3:
            children = [status[c.id] for c in tree.nodes if parent(c.id) == node.id and c.id in status]
            status[node.id] = min(children, key=STATUSES.index) if children else "ghost"
    return {n.id: status[n.id] for n in tree.nodes}


# --- outside (decision 6) ---------------------------------------------------------------
def propose_outside(nodes: Iterable[Node], skills: Sequence[str], certifications: Sequence[str],
                    languages: str) -> Dict[str, str]:
    """leaf id -> where the profile shows it outside the experiences: a leaf whose name or alias
    occurs in the skills list, a certification's name, or the languages line."""
    out: Dict[str, str] = {}
    for node in nodes:
        if level(node.id) != 3:
            continue
        terms = terms_of(node, counts_as=False)
        if any(has_term(t, skill) for t in terms for skill in skills):
            out[node.id] = "skills list"
            continue
        cert = next((c for c in certifications if any(has_term(t, c) for t in terms)), None)
        if cert is not None:
            out[node.id] = f"certification: {cert}"
        elif any(has_term(t, languages) for t in terms):
            out[node.id] = "languages"
    return out


# --- checks ------------------------------------------------------------------------------
def unasked(nodes: Iterable[Node], market: Market) -> List[MarketTerm]:
    """Market terms asked often (``share`` >= 5% or ``jobs`` >= 3) that equal no node's name, alias
    or counts-as term, case and spaces normalized."""
    carried = {norm(t) for n in nodes for t in terms_of(n)}
    return [t for t in market.terms
            if (t.share >= OFTEN_SHARE or t.jobs >= OFTEN_JOBS) and norm(t.term) not in carried]


def _market_by_term(market: Market) -> Dict[str, MarketTerm]:
    return {norm(t.term): t for t in market.terms}


def checks(tree: Tree, items: Items, market: Market) -> Dict[str, list]:
    """What the gate should look at, each list empty when nothing is found."""
    status = leaf_status(tree, items)
    linked = leaf_links(tree, items)
    leaves = [n for n in tree.nodes if level(n.id) == 3]

    on_nodes: Dict[str, List[str]] = defaultdict(list)
    for node in tree.nodes:
        for t in {norm(t) for t in terms_of(node)}:
            on_nodes[t].append(node.id)
    by_node = {n.id: n for n in tree.nodes}

    def twice(term: str, ids: List[str]) -> bool:
        """On two nodes, except a node's term repeated in an ancestor's counts-as list: redundant,
        not wrong (the node already proves its ancestor)."""
        own = [i for i in ids if norm(term) in {norm(t) for t in terms_of(by_node[i], counts_as=False)}
               or not any(j != i and j.startswith(i + ".") for j in ids)]
        return len(own) > 1

    named: List[dict] = []
    for leaf in leaves:
        if status[leaf.id] != "ghost":
            continue
        hits = [i.id for i in items.items
                if any(has_term(t, " | ".join([i.text, *i.tech, *i.tags])) for t in terms_of(leaf, counts_as=False))]
        if hits:
            named.append({"leaf": leaf.id, "items": hits})

    linked_items = {iid for mine in linked.values() for iid in mine}
    return {
        "compound_names": [n.id for n in tree.nodes if is_compound(n.name)],
        "two_nodes": [{"term": t, "nodes": ids} for t, ids in sorted(on_nodes.items()) if twice(t, ids)],
        "unasked": [t.model_dump() for t in unasked(tree.nodes, market)],
        "unlinked_items": [i.id for i in items.items if _work(i) and i.id not in linked_items],
        "broad": [n.id for n in leaves if len(linked.get(n.id, {})) > BROAD],
        "ghosts_expected": [n.id for n in leaves if status[n.id] == "ghost" and n.shape_proven],
        "ghosts_named": named,
        "weak": [n.id for n in leaves if status[n.id] == "weak"],
        "market_changed": ([] if tree.market_sha256 == market_sha256(market)
                           else ["the market list changed since the shape: reshape to use it"]),
    }


# --- validation ----------------------------------------------------------------------------
def structure_faults(nodes: Sequence[Node]) -> List[str]:
    """The faults that make a tree unusable: an id twice, a bad id, a fourth level, a missing
    parent, a child before its parent, a discipline or competence with no children."""
    faults: List[str] = []
    seen: set = set()
    for node in nodes:
        if node.id in seen:
            faults.append(f"{node.id}: two nodes have this id")
        if not re.fullmatch(r"[1-9]\d*(\.[1-9]\d*){0,2}", node.id):
            faults.append(f"{node.id}: not an id of the form 4, 4.1 or 4.1.2" if node.id.count(".") < 3
                          else f"{node.id}: deeper than three levels")
        elif parent(node.id) is not None and parent(node.id) not in seen:
            ids = {n.id for n in nodes}
            faults.append(f"{node.id}: listed before its parent {parent(node.id)}" if parent(node.id) in ids
                          else f"{node.id}: no parent {parent(node.id)}")
        seen.add(node.id)
    has_children = {parent(n.id) for n in nodes}
    faults += [f"{n.id} {n.name}: no children" for n in nodes if level(n.id) < 3 and n.id not in has_children]
    return faults


def validate_tree(tree: Tree, items: Items) -> List[str]:
    """What is wrong with a (hand-edited) tree, one line each; [] when nothing is.

    Runs on every load, after pydantic has checked the shapes. Nothing is changed or dropped.
    """
    problems = structure_faults(tree.nodes)
    ids = {n.id for n in tree.nodes}

    by_id = {i.id: i for i in items.items}
    known = {x.id for x in items.experiences}
    blocks = {b.experience for b in tree.experiences}
    problems += [f"e{e}: not attached" for e in sorted(known - blocks)]
    for block in tree.experiences:
        e = f"e{block.experience}"
        if block.experience not in known:
            problems.append(f"{e}: links of an experience the items do not have")
            continue
        if block.items_sha256 != items_sha256(items, block.experience):
            problems.append(f"{e}: its items changed since they were attached: re-attach it")
        for iid, leaves in block.links.items():
            item = by_id.get(iid)
            if item is None or item.experience != block.experience:
                problems.append(f"{e}: links {iid}, which is not one of its items")
                continue
            for leaf, lvl in leaves.items():
                if leaf not in ids:
                    problems.append(f"{iid}: linked to {leaf}, which does not exist")
                elif level(leaf) != 3:
                    problems.append(f"{iid}: linked to {leaf}, which is not a leaf")
                if lvl not in (1, 2, 3):
                    problems.append(f"{iid}: level {lvl} on {leaf} (1-3)")
                elif item.type == "context" and lvl > 1:
                    problems.append(f"{iid}: a context item at level {lvl} on {leaf}: it counts as 1")
    return problems


# --- the readable view ------------------------------------------------------------------------
GATE = """\
# Competence tree

Read the **To check** part first, in this order (about 15 minutes):

1. weak leaves: back the ones you can (`"confirmed": true` on the node);
2. ghosts an item may prove: add the link (and `"edited": true` on that experience's block);
3. names: a name a job description would not use, a compound, a term on two nodes, an alias that is
   not the same thing (move it to `counts_as`);
4. the `outside` notes code set: remove a wrong one, add what code cannot see.

Then skim the tree. Correct `tree.json`, then run `md` to check it and rebuild this page.
"""


def _asked(t: Optional[MarketTerm]) -> str:
    if t is None:
        return ""
    return f"{t.jobs} jobs, asked {t.asked:.1%}, in {t.share:.1%} of JDs"


def _best_market(node: Node, market: Mapping[str, MarketTerm]) -> Optional[MarketTerm]:
    found = [market[norm(t)] for t in terms_of(node, counts_as=False) if norm(t) in market]
    return max(found, key=lambda t: (t.share, t.asked, t.jobs)) if found else None


def _label(node: Node) -> str:
    return f"{node.id} {node.name}"


def tree_markdown(tree: Tree, items: Items, market: Market) -> str:
    """The gate view: counts, then what needs a decision, then the tree."""
    status = leaf_status(tree, items)
    found = checks(tree, items, market)
    problems = validate_tree(tree, items)
    linked = leaf_links(tree, items)
    nodes = {n.id: n for n in tree.nodes}
    by_id = {i.id: i for i in items.items}
    by_term = _market_by_term(market)
    leaves = [n for n in tree.nodes if level(n.id) == 3]

    def text(iid: str) -> str:
        item = by_id.get(iid)
        return f"{iid} ({item.type}) {item.text}" if item else f"{iid} (unknown item)"

    count = {s: sum(status[n.id] == s for n in leaves) for s in STATUSES}
    out = [GATE,
           f"{sum(level(n.id) == 1 for n in tree.nodes)} disciplines, "
           f"{sum(level(n.id) == 2 for n in tree.nodes)} competences, {len(leaves)} leaves: "
           f"{count['proven']} proven, {count['weak']} weak, {count['ghost']} ghost.", ""]
    if problems:
        out += ["**Problems**", "", *[f"- {p}" for p in problems], ""]

    out += ["## To check", ""]
    out += [f"### Weak leaves ({len(found['weak'])}): back it or leave it weak", ""]
    for lid in found["weak"]:
        out.append(f"- **{_label(nodes[lid])}**")
        out += [f"  - level {lvl}: {text(iid)}" for iid, lvl in sorted(linked[lid].items(), key=lambda p: -p[1])]
    out.append("")

    expected = found["ghosts_expected"]
    named = {g["leaf"]: g["items"] for g in found["ghosts_named"]}
    may = sorted(set(expected) | set(named), key=lambda i: [int(x) for x in i.split(".")])
    out += [f"### Ghosts an item may prove ({len(may)}): add the link", ""]
    for lid in may:
        why = ["the shape expected it proven"] if lid in expected else []
        out.append(f"- **{_label(nodes[lid])}**" + (f" ({why[0]})" if why else ""))
        out += [f"  - named in {text(iid)}" for iid in named.get(lid, [])]
    out.append("")

    outside = [n for n in leaves if n.outside]
    out += [f"### `outside` notes ({len(outside)}): remove a wrong one", ""]
    out += [f"- {_label(n)}: {n.outside}" for n in outside]
    out.append("")

    out += ["### Names", ""]
    out += [f"- compound name: {_label(nodes[i])}" for i in found["compound_names"]]
    out += [f"- on two nodes: {t['term']!r} on " + ", ".join(_label(nodes[i]) for i in t["nodes"])
            for t in found["two_nodes"]]
    out += [f"- asked, no node carries it: {t['term']} ({_asked(MarketTerm(**t))})" for t in found["unasked"]]
    if not (found["compound_names"] or found["two_nodes"] or found["unasked"]):
        out.append("- nothing found")
    out.append("")

    other = [f"- no leaf proves {text(iid)}" for iid in found["unlinked_items"]]
    other += [f"- too broad ({len(linked[i])} items): {_label(nodes[i])}" for i in found["broad"]]
    other += [f"- {m}" for m in found["market_changed"]]
    for block in tree.experiences:
        other += [f"- e{block.experience}: dropped link {d.get('item')} -> {d.get('leaf')} ({d.get('reason')})"
                  for d in block.dropped]
        other += [f"- e{block.experience}: hand-edited link before the re-attach: {d.get('item')} -> "
                  f"{d.get('leaf')} at level {d.get('level')}" for d in block.previous]
    out += ["### Other", "", *(other or ["- nothing found"]), ""]

    out += ["## The tree", ""]
    for d in (n for n in tree.nodes if level(n.id) == 1):
        out += [f"### {_label(d)} · {status[d.id]}", ""]
        for c in (n for n in tree.nodes if parent(n.id) == d.id):
            out.append(f"- **{_label(c)}** · {status[c.id]}" + _also(c))
            for leaf in (n for n in tree.nodes if parent(n.id) == c.id):
                asked = _asked(_best_market(leaf, by_term))
                mark = status[leaf.id].upper() if status[leaf.id] != "proven" else "proven"
                notes = [x for x in (leaf.outside and f"outside: {leaf.outside}",
                                     leaf.confirmed and "confirmed", asked) if x]
                out.append(f"  - {_label(leaf)} · {mark}" + _also(leaf) + (f" · {'; '.join(notes)}" if notes else ""))
                for iid, lvl in sorted(linked.get(leaf.id, {}).items(), key=lambda p: (-p[1], p[0])):
                    out.append(f"    - {lvl} · {text(iid)}")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def _also(node: Node) -> str:
    parts = []
    if node.aliases:
        parts.append("also: " + ", ".join(node.aliases))
    if node.counts_as:
        parts.append("counts as: " + ", ".join(node.counts_as))
    return f" _({'; '.join(parts)})_" if parts else ""


def tree_dump(tree: Tree) -> str:
    """The file's text: one node per line, so a hand edit and a diff stay readable."""
    nodes = ",\n".join("  " + json.dumps(n.model_dump(), ensure_ascii=False) for n in tree.nodes)
    blocks = json.dumps([b.model_dump() for b in tree.experiences], ensure_ascii=False, indent=1)
    return (f'{{\n "market_sha256": {json.dumps(tree.market_sha256)},\n "nodes": [\n{nodes}\n ],\n'
            f' "experiences": {blocks}\n}}\n')


__all__ = [
    "ExperienceLinks",
    "Market",
    "MarketRequirement",
    "MarketTerm",
    "Node",
    "Tree",
    "checks",
    "items_sha256",
    "leaf_links",
    "leaf_status",
    "level",
    "market_sha256",
    "parent",
    "propose_outside",
    "structure_faults",
    "tree_dump",
    "tree_markdown",
    "unasked",
    "validate_tree",
]
