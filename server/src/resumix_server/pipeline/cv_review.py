"""Validating the CV: the model reports each violation as a text replacement,
the code applies it, the model checks again.

The method is adapticv-ai's. The two tools below are only schemas: the model is
shown their names, docstrings and fields, and nothing ever runs them, because a
tool gets the model's arguments and not the document to edit. :func:`review`
applies the arguments instead, with :func:`apply_body` or
:func:`apply_duties`. The validators check facts and grammar only, with
minimal edits — style belongs to the writers — and they count no pages: the
page limit is the render's business.

The tool docstrings are prompt text: the model reads them as the tools'
descriptions.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Mapping, Optional, Type

from pydantic import BaseModel, ConfigDict

from ..models import ModelSelector, Usage
from ..observability import LOGGER_ROOT

logger = logging.getLogger(f"{LOGGER_ROOT}.cv")

#: Rounds per document. After the last one the CV goes on with the last edits.
MAX_VALIDATIONS = 5


class ReplaceInDuties(BaseModel):
    """Replace the exact text `old` with `new` in duty number `duty` (1 = first), or in every duty when `duty` is omitted.

    reason: the category (Exaggeration, Syntax or Logic) and the fact in EXPERIENCE that `old` contradicts.
    """

    model_config = ConfigDict(title="replace_in_duties")

    old: str
    new: str
    duty: Optional[int] = None
    reason: str = ""


class ReplaceInBody(BaseModel):
    """Replace the exact text `old` with `new` in the text field `field`, or in every text field when it is omitted.

    reason: the category (Exaggeration, Syntax or Logic) and the fact in MASTER PROFILE that `old` contradicts.
    """

    model_config = ConfigDict(title="replace_in_body")

    old: str
    new: str
    field: Optional[str] = None
    reason: str = ""


def apply_duties(doc: Dict[str, Any], args: Mapping[str, Any]) -> Optional[str]:
    """Apply one replace_in_duties call to the experience; an error when `old` is not found."""
    targets = [args["duty"] - 1] if args.get("duty") else range(len(doc["duties"]))
    hits = [i for i in targets if 0 <= i < len(doc["duties"]) and args["old"] in doc["duties"][i]]
    for i in hits:
        doc["duties"][i] = doc["duties"][i].replace(args["old"], args["new"])
    if not hits:
        return f"{args['old']!r} not found in " + (f"duty {args['duty']}" if args.get("duty") else "any duty")
    return None


def apply_body(doc: Dict[str, Any], args: Mapping[str, Any]) -> Optional[str]:
    """Same for replace_in_body, on the text fields of the body."""
    fields = [args["field"]] if args.get("field") else [k for k, v in doc.items() if isinstance(v, str)]
    hits = [f for f in fields if isinstance(doc.get(f), str) and args["old"] in doc[f]]
    for f in hits:
        doc[f] = doc[f].replace(args["old"], args["new"])
    if not hits:
        return f"{args['old']!r} not found in " + (args.get("field") or "any text field")
    return None


def review(
    llm: ModelSelector,
    tool: Type[BaseModel],
    apply: Callable[[Dict[str, Any], Mapping[str, Any]], Optional[str]],
    prompt: str,
    doc: Dict[str, Any],
    text_for: Callable[[Dict[str, Any], int], str],
    label: str,
    *,
    usage: Usage,
) -> Dict[str, Any]:
    """Validate one document on the ``validation`` role, in place, until a round
    calls no tool or :data:`MAX_VALIDATIONS` rounds are done; returns it.

    The calls of one reply are applied in the order they came: they edit the
    same document. A replacement whose ``old`` text is not found goes back to
    the model as a FAILED REPLACEMENT in the next round.
    """
    failed: List[str] = []
    for attempt in range(1, MAX_VALIDATIONS + 1):
        text = text_for(doc, attempt) + (
            "\n\nFAILED REPLACEMENTS:\n" + "\n".join(failed) if failed else ""
        )
        calls = llm.call_tools("validation", tool, prompt, [{"role": "user", "content": text}],
                               usage=usage)
        failed = []
        if not calls:
            logger.info("[%s] attempt %d: OK", label, attempt)
            return doc
        for args in calls:
            where = f"duty {args['duty']}" if args.get("duty") else args.get("field") or "all"
            logger.info("[%s] attempt %d: %s %r -> %r (%s)", label, attempt, where,
                        args["old"], args["new"], args.get("reason", ""))
            if error := apply(doc, args):
                logger.info("[%s]   %s", label, error)
                failed.append(f"{error} (your fix: {args['new']!r})")
    logger.info("[%s] %d validations done: going on with the last edits", label, MAX_VALIDATIONS)
    return doc


__all__ = [
    "MAX_VALIDATIONS",
    "ReplaceInBody",
    "ReplaceInDuties",
    "apply_body",
    "apply_duties",
    "review",
]
