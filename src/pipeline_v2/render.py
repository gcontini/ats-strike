"""Render and fit (step 7): the plan's document compiled by :class:`common.cv_renderer.CVRenderer`;
while the PDF runs over the renderer's page limit, one :func:`select.fit` step (an experience's
last bullet removed, so no reference breaks) and render again; the highlight's markers, when
given, laid over each document (:func:`~.highlight.marked`). And the measure of a rendered page:
its text lines, and the lines left free above the signature.

The renderer is the caller's, built on its scratch directory with the v2 template; this module
writes nothing else. No model call.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, List, Mapping, Optional, Tuple

from pypdf import PdfReader

from common.cv_renderer import CVRenderer, RenderResult

from .document import document
from .highlight import marked
from .pool_store import Pool
from .select import Context, Plan, draft_of, fit, plan_of

if TYPE_CHECKING:
    from .varnish import Varnish

#: A4 in points, and the template's text frame: top margin 2.54 cm, text height 24.62 cm.
FRAME_TOP = 841.89 - 72.0
FRAME_HEIGHT = 24.619999 / 2.54 * 72
#: The signature image's height.
SIGNATURE = 1.36 / 2.54 * 72


@dataclass
class Rendered:
    """The last render, the plan and document it came from, and the fit steps that led to it."""

    plan: Plan
    document: Dict[str, Any]
    result: RenderResult
    first_pages: int
    steps: List[str] = field(default_factory=list)


def _step(before: Plan, after: Plan) -> str:
    old, new = draft_of(before)["slots"], draft_of(after)["slots"]
    return "; ".join(f"removed {s}" for s in old if s not in new) or "no change"


def render(
    plan: Plan,
    ctx: Context,
    profile: Mapping[str, Any],
    pool: Pool,
    data: Mapping[str, Any],
    renderer: CVRenderer,
    varnish: Optional["Varnish"] = None,
    marks: Optional[Mapping[str, str]] = None,
) -> Rendered:
    """Render ``plan`` with ``varnish``'s choices and the highlight's ``marks``; while over the page
    limit, fit one step and render again. ``data`` (the candidate's name, contact, languages,
    place) goes over the document, as in v1. The headline falls back to the profile's first title."""
    title = (profile.get("job_title") or [""])[0]

    def build(p: Plan) -> Tuple[Dict[str, Any], RenderResult]:
        doc = {**marked(document(p, profile, pool, title, varnish), marks or {}), **dict(data)}
        return doc, renderer.render_document(doc)

    doc, result = build(plan)
    out = Rendered(plan=plan, document=doc, result=result, first_pages=result.pages)
    while out.result.pages > renderer.page_limit:
        draft = fit(draft_of(out.plan), ctx)
        if draft is None:
            break
        new = plan_of(draft, ctx, out.plan.greedy_objective)
        out.steps.append(f"{out.result.pages} pages, {out.result.overflow_lines} lines over: {_step(out.plan, new)}")
        out.plan = new
        out.document, out.result = build(new)
    return out


def page_lines(pdf: bytes) -> List[List[Tuple[float, str]]]:
    """Per page, its text lines top to bottom: (baseline y in points, text)."""
    pages = []
    for page in PdfReader(io.BytesIO(pdf)).pages:
        runs: List[Tuple[float, str]] = []

        def visit(text: str, cm: Any, tm: Any, font: Any, size: Any, runs: List = runs) -> None:
            if text.strip():
                runs.append((tm[4] * cm[1] + tm[5] * cm[3] + cm[5], text))

        page.extract_text(visitor_text=visit)
        lines: List[List[Any]] = []  # runs in the order the PDF draws them: x is unreliable
        for y, text in runs:
            same = next((ln for ln in lines if abs(ln[0] - y) < 1.5), None)
            if same:
                same[1].append(text)
            else:
                lines.append([y, [text]])
        pages.append(
            [
                (round(y, 2), " ".join(parts).replace("\n", " ").strip())
                for y, parts in sorted(lines, key=lambda ln: -ln[0])
            ]
        )
    return pages


def room_left(pdf: bytes, location: str, pitch: float) -> float:
    """Lines left free on the last page above the signature, found by the place-and-date line
    that starts with ``location``. Negative when the CV ran over."""
    pages = page_lines(pdf)
    p, i = next(
        (p, i) for p, lines in enumerate(pages) for i, (_, t) in enumerate(lines) if t.startswith(location + ",")
    )
    return round((pages[p][i][0] - SIGNATURE - pitch - (FRAME_TOP - FRAME_HEIGHT)) / pitch, 1)


__all__ = ["Rendered", "page_lines", "render", "room_left"]
