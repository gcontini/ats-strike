"""A model reply logged short: its head and tail, the middle elided.

Parsing and validating a reply is :meth:`common.models.ModelSelector.call_llm`'s
job; this is what it logs of a reply it rejects.
"""

from __future__ import annotations

from typing import Optional

# How much of a rejected payload to log: enough head to see the shape the
# model chose and enough tail to see where a truncated reply stopped. Both
# together stay under the per-line cap the run log store applies.
RAW_HEAD_CHARS = 1200
RAW_TAIL_CHARS = 400


def raw_excerpt(content: Optional[str]) -> str:
    """Head and tail of a payload, with the middle elided."""
    if not content:
        return repr(content)
    elided = len(content) - RAW_HEAD_CHARS - RAW_TAIL_CHARS
    if elided <= 0:
        return content
    return f"{content[:RAW_HEAD_CHARS]}\n    ... [{elided} chars elided] ...\n{content[-RAW_TAIL_CHARS:]}"


__all__ = ["raw_excerpt"]
