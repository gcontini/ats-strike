"""The highlight for one job (step 9): the three inputs :class:`common.highlight.Highlighter` reads,
built from the varnished document as v1 builds them, and its markers laid back over the document.

The highlighter, its prompt and its rules are ``common``'s, unchanged: one prompt and one code path
for every pipeline. v2 only feeds it:

- **KEY POINTS:** S5's requirements as ``{id, requirement, keywords, importance}``, most important
  first.
- **CV:** the document's texts: summary, skills, and per shown experience its role summary and
  bullets (full) or its compact line. Each bullet ends with ``|<id>-<score>,...`` for the
  requirements whose node it proves, as S6's tree score counts it (the bullet's level on the node
  or on one below it); the score is that level x :data:`SCORE_PER_LEVEL` (90, 60, 30). A bullet
  that proves none has no suffix.
- **EVIDENCE:** per shown experience, in CV order: for each match of its bullets, the key point
  id, the score, and the words: the text of the items the bullet uses.

The reply's markers come back as :func:`marks` (the plain text -> the marked text, without the
suffixes), which :func:`marked` lays over any document: a text the fit step changes afterwards is
not in it, so it prints plain.

Pure: values in, values out.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from common.highlight import SCORES

from .allocate import Requirement
from .document import printed
from .pool_store import Pool, ladder_and_group_bullets
from .select import Context, Plan, levels_at
from .store import Items

#: A bullet's score on a requirement it proves: its level there (1-3) times this.
SCORE_PER_LEVEL = 30


def key_points(requirements: Sequence[Requirement]) -> List[Dict[str, Any]]:
    """The requirements as the highlighter's KEY POINTS, most important first."""
    return [
        {"id": r.id, "requirement": r.name, "keywords": list(r.keywords), "importance": r.importance}
        for r in sorted(requirements, key=lambda r: -r.importance)
    ]


def proofs(ctx: Context) -> Dict[str, List[Tuple[int, int]]]:
    """Every ladder and group bullet's id -> the requirements it proves, as (id, score), best score
    first, then the most important."""
    every = [p for rungs in ctx.ladder.values() for ps in rungs.values() for p in ps]
    every += [p for groups in ctx.groups.values() for ps in groups.values() for p in ps]
    ranked = sorted(ctx.requirements, key=lambda r: -r.importance)
    out = {}
    for p in every:
        at = levels_at(p.nodes)
        found = [(r.id, at[r.node] * SCORE_PER_LEVEL) for r in ranked if r.node in at]
        out[p.id] = sorted(found, key=lambda m: -m[1])
    return out


def shown_bullets(plan: Plan, picks: Mapping[str, str]) -> List[List[str]]:
    """Per shown experience, in CV order, the ids of the bullets it prints ([] for a compact one)."""
    return [printed(x.slots, picks) if x.form == "full" else [] for x in plan.experiences]


def scored(text: str, matches: Sequence[Tuple[int, int]]) -> str:
    """``text|3-90,7-60``; the text alone without a match."""
    return f"{text}|{','.join(f'{k}-{s}' for k, s in matches)}" if matches else text


def cv(doc: Mapping[str, Any], bullets: Sequence[Sequence[str]], found: Mapping[str, Sequence]) -> Dict[str, Any]:
    """The highlighter's CV: the document's texts, each bullet with its scores."""
    experiences = []
    for e, ids in zip(doc["experiences"], bullets, strict=True):
        if e["compact"]:
            experiences.append({"compact": e["compact"]})
            continue
        duties = [scored(d, found.get(b, [])) for d, b in zip(e["duties"], ids, strict=True)]
        experiences.append({"role_summary": e["role_summary"], "duties": duties})
    return {"summary": doc["summary"], "skills": list(doc["skills"]), "experiences": experiences}


def evidence(
    bullets: Sequence[Sequence[str]], found: Mapping[str, Sequence], pool: Pool, items: Items
) -> List[List[Dict[str, Any]]]:
    """The highlighter's EVIDENCE: per shown experience, each match of its bullets with the text of
    the items the bullet uses."""
    uses = {b.id: b.uses for blk in pool.experiences for b in ladder_and_group_bullets(blk)}
    text = {i.id: i.text for i in items.items}
    out = []
    for ids in bullets:
        matches = []
        for b in ids:
            words = " ".join(text[i] for i in uses[b] if i in text)
            matches += [{"key_point": k, "score": s, "evidence": words} for k, s in found.get(b, [])]
        out.append(matches)
    return out


def inputs(
    doc: Mapping[str, Any], plan: Plan, picks: Mapping[str, str], ctx: Context, pool: Pool, items: Items
) -> Tuple[List[Dict[str, Any]], Dict[str, Any], List[List[Dict[str, Any]]]]:
    """KEY POINTS, CV and EVIDENCE for the varnished ``doc`` of ``plan`` (``picks``: the varnish's)."""
    bullets = shown_bullets(plan, picks)
    found = proofs(ctx)
    return key_points(ctx.requirements), cv(doc, bullets, found), evidence(bullets, found, pool, items)


def marks(sent: Any, got: Any) -> Dict[str, str]:
    """The highlighter's reply (``got``, what it returned for ``sent``) as plain text -> marked
    text, both without the scores; only the texts it marked."""
    if isinstance(sent, str):
        plain, new = SCORES.sub("", sent), SCORES.sub("", got)
        return {plain: new} if new != plain else {}
    if isinstance(sent, list):
        return {k: v for s, g in zip(sent, got, strict=True) for k, v in marks(s, g).items()}
    if isinstance(sent, dict):
        return {k: v for key in sent for k, v in marks(sent[key], got[key]).items()}
    return {}


def marked(doc: Mapping[str, Any], found: Mapping[str, str]) -> Dict[str, Any]:
    """``doc`` with the marked texts in place of the plain ones: the summary, the skills, and each
    experience's role summary, bullets and compact line."""

    def m(text: str) -> str:
        return found.get(text, text)

    return {
        **doc,
        "summary": m(doc["summary"]),
        "skills": [m(s) for s in doc["skills"]],
        "experiences": [
            {
                **e,
                "role_summary": m(e["role_summary"]),
                "duties": [m(d) for d in e["duties"]],
                "compact": m(e["compact"]),
            }
            for e in doc["experiences"]
        ],
    }


def highlight_markdown(doc: Mapping[str, Any]) -> str:
    """The view's Highlight section: the bold and italic phrases ``doc`` prints, each with where
    (skills, summary, eN role / bullet / compact)."""
    texts = [("skills", t) for t in doc["skills"]] + [("summary", doc["summary"])]
    for n, e in enumerate(doc["experiences"]):
        texts += [(f"e{n} role", e["role_summary"]), *((f"e{n} bullet", d) for d in e["duties"])]
        texts += [(f"e{n} compact", e["compact"])]
    bold = [f"{b} ({where})" for where, t in texts for b in re.findall(r"\*\*([^*]+)\*\*", t)]
    italics = [f"{i} ({where})" for where, t in texts for i in re.findall(r"(?<!\*)\*([^*]+)\*(?!\*)", t)]
    return (
        f"\n## Highlight\n\nBold ({len(bold)}): {'; '.join(bold) or '(none)'}\n\n"
        f"Italics ({len(italics)}): {'; '.join(italics) or '(none)'}\n"
    )


__all__ = [
    "SCORE_PER_LEVEL",
    "cv",
    "evidence",
    "highlight_markdown",
    "inputs",
    "key_points",
    "marked",
    "marks",
    "proofs",
    "scored",
    "shown_bullets",
]
