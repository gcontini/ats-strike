"""The ``**bold**`` pass: a CV's key-point keywords bold, its stacks italic.

One code path for every pipeline; the prompt is the pipeline's own (each ships
its ``sys_prompt_highlight.txt``), so one can change without the other. The
model is shown the CV a pipeline wrote, never the candidate's data. The
renderer turns the markers into LaTeX.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

from openai import APIError

from .errors import ModelOutputError
from .models import ModelSelector, Usage
from .observability import LOGGER_ROOT, stage

logger = logging.getLogger(f"{LOGGER_ROOT}.highlight")

#: The "|3-80,7-40" suffix of a scored text: key point id - score.
SCORES = re.compile(r"\|((?:\d+-\d+,?)*)$")


def unmarked(text: str) -> str:
    """text without the **bold** and *italics* markers."""
    return re.sub(r"\*\*([^*]+)\*\*|\*([^*]+)\*", r"\1\2", text)


def keep_words(new: Any, old: Any) -> Any:
    """new where it only adds markers to old, outside any "|3-80,7-40" suffix (same structure, same words); old otherwise."""
    if isinstance(old, str):
        score = SCORES.search(old)
        if isinstance(new, str) and unmarked(new) == old and (not score or new.endswith(score.group())):
            return new
        logger.info("highlight: kept the original of %r...", old[:60])
        return old
    if isinstance(old, list):
        if isinstance(new, list) and len(new) == len(old):
            return [keep_words(n, o) for n, o in zip(new, old)]
        return old
    if isinstance(old, dict):
        return {k: keep_words(new.get(k), v) for k, v in old.items()} if isinstance(new, dict) else old
    return old


def _dumps(obj: Any) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False)


class Highlighter:
    """Marks a CV's key-point keywords **bold** and its stacks *italic*. A text whose words the model
    changed keeps its original; a failed call leaves the CV unmarked. ``prompt`` and ``role`` (the model role
    called) are the calling pipeline's."""

    def __init__(self, llm: ModelSelector, prompt: str, role: str) -> None:
        self.llm = llm
        self.prompt = prompt
        self.role = role

    def highlight(self, key_points: Any, resume: dict, evidence: Any, *, usage: Optional[Usage] = None) -> dict:
        """The CV with its markers, on the caller's role. EVIDENCE: for each experience of the CV, the
        matches of its duties in the profile, with their words. A highlighter that gives no usable answer, or
        whose endpoint refuses the call, leaves the CV unmarked rather than failing a finished job. ``usage``,
        when given, counts the call."""
        text = f"KEY POINTS:\n{_dumps(key_points)}\n\nCV:\n{_dumps(resume)}\n\nEVIDENCE:\n{_dumps(evidence)}"
        with stage("cv.highlight"):
            try:
                reply = self.llm.call_llm(
                    self.role, dict, self.prompt, [{"role": "user", "content": text}], usage=usage
                )
                resume = keep_words(reply, resume)
            except (ModelOutputError, APIError) as e:
                logger.warning("  ⚠ highlighting failed (%s) — continuing with the unmarked CV", e)
            flat = json.dumps(resume)  # every text, to list the markers
            logger.info("bold: %s", re.findall(r"\*\*([^*]+)\*\*", flat))
            logger.info("italics: %s", re.findall(r"(?<!\*)\*([^*]+)\*(?!\*)", flat))
        return resume


__all__ = ["Highlighter", "SCORES", "keep_words", "unmarked"]
