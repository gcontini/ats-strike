"""Small helpers for model replies: a fence dropped, a payload logged short.

Parsing and validating a reply is :meth:`resumix_server.models.ModelSelector.call_llm`'s
job; what is left here is what a caller still does with plain text.
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
    return (
        f"{content[:RAW_HEAD_CHARS]}"
        f"\n    ... [{elided} chars elided] ...\n"
        f"{content[-RAW_TAIL_CHARS:]}"
    )


def strip_fences(text: str) -> str:
    """Drop a Markdown code fence around a reply, if the model added one."""
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


__all__ = ["strip_fences", "raw_excerpt"]
