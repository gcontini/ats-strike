"""A fence dropped from a model reply: what a caller still does with plain text.

Parsing and validating a reply is :meth:`common.models.ModelSelector.call_llm`'s
job.
"""

from __future__ import annotations


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


__all__ = ["strip_fences"]
