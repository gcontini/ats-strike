"""Pipeline 2.0's face: the only thing a server calls.

As v1's: every call takes the request's files in memory — the **pack** (every file of a client's
server folder), the **job** (the posting and what an earlier run stored) and the **settings** — and
hands its outputs to a :class:`Sink` as they are ready. Nothing here knows HTTP, sessions or where
a client keeps its files. Log lines and model-call costs flow through the active
:class:`~common.observability.Run`, which the caller sets up.

**The store** is the pack's ``cache/``: ``items.json`` (Profile breakdown), ``tree.json`` (Skill
tree) and ``pool.json`` (Bullet writing, then Bullet review). Each file missing is built, in that
order, and stored at once as a ``CACHE`` item with its readable page; the client keeps it in the
server folder's ``cache/`` and sends it back with every request. There is no hashing: after a
profile change, the user deletes ``cache/``.

**The budget** is v1's (``ATS_REQUEST_BUDGET_SECONDS``, 1200 s). A part of the store starts only
when about its time is left of the budget, and the CV only once the store is complete and about 7
minutes are left. Otherwise the job ends with a :class:`BudgetExceededError` that says which files
are built and to run the job again: the next run starts from what this one stored. The store takes
about 26 minutes, so the first job of a profile usually ends after Skill tree.

**The CV:** Job reading, Content choice, Polish, Highlight, Page layout. It stores ``cv.pdf``,
``cv.json`` (the document) and ``cv.tex``, and the job's steps under ``steps/``: the allocation,
the plan and the varnish, each as ``.json`` and ``.md``.

Every expected failure leaves as a :class:`PipelineError` with a ``kind``, as v1's do.
"""

from __future__ import annotations

import importlib.resources
import json
import logging
import os
import shutil
import time
from datetime import date
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Mapping, Optional, Sequence, Type, TypeVar

from pydantic import BaseModel, ValidationError

from common.cv_renderer import DEFAULT_LATEX_TIMEOUT, CVRenderer
from common.errors import BadPartError, BudgetExceededError, MissingPartError, provider_errors
from common.files import Files, json_file, text_file
from common.highlight import Highlighter
from common.models import ModelSelector, Usage, build_selector
from common.observability import stage
from common.outputs import Output, Sink

from .allocate import Allocator, allocation_markdown
from .document import document
from .highlight import highlight_markdown, inputs, marks
from .items import ItemCompiler
from .pool import PoolWriter
from .pool_review import PoolReviewer, review_markdown
from .pool_store import Pool, pool_dump, pool_markdown
from .render import render
from .select import context, plan_markdown, recency, select
from .store import Items, items_markdown
from .tree import TreeBuilder
from .tree_store import Market, Tree, tree_dump, tree_markdown, with_profile
from .varnish import Varnisher, varnish_markdown

#: The model roles of ``resources/v2/models.toml``. None is a v1 role's name, so an
#: ``ATS_<ROLE>_<FIELD>`` env override reaches one pipeline only.
ROLES = ("compile", "review", "allocate", "varnish", "markup")
#: The role of the highlight call.
MARKUP = "markup"

#: Wall-clock budget for one CV job, v1's.
DEFAULT_BUDGET_SECONDS = 1200.0

#: About how long each part takes (doc/CV-PIPELINE-V2.md, 3.1 "How a CV is made now"): a part
#: starts only when that much of the budget is left. ``pool.json`` is stored after Bullet writing
#: (9 min) and Bullet review (2 min) both.
ITEMS_SECONDS = 180.0
TREE_SECONDS = 660.0
POOL_SECONDS = 660.0
CV_SECONDS = 420.0

PROFILE_FILE = "candidate_profile.json"
DATA_FILE = "candidate_data.json"
CACHE = "cache/"
STORE_FILES = ("items.json", "tree.json", "pool.json")
JD_FILE = "job_description.txt"
DOCUMENT_FILE = "cv.json"
TEX_FILE = "cv.tex"
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg")

M = TypeVar("M", bound=BaseModel)

logger = logging.getLogger("pipeline_v2.facade")


def resources_dir() -> Path:
    """The ``resources/`` folder inside the installed package.

    ``importlib.resources`` in full: importing the subpackage binds the name ``resources`` here.
    """
    return Path(str(importlib.resources.files("pipeline_v2.resources")))


# --- the request's files --------------------------------------------------------------------
def cached(pack: Files, name: str, shape: Type[M]) -> Optional[M]:
    """``cache/<name>`` of the pack, read as ``shape``; ``None`` when the pack has none."""
    text = text_file(pack, CACHE + name)
    if text is None:
        return None
    try:
        return shape.model_validate_json(text)
    except ValidationError as exc:
        raise BadPartError(f"file '{CACHE}{name}' does not match the store's shape: {exc.errors()[0]['msg']}") from exc


def images(pack: Files) -> Dict[str, bytes]:
    """The pack's top-level images: what the template includes."""
    return {
        name: blob for name, blob in pack.items() if "/" not in name and PurePosixPath(name).suffix in IMAGE_SUFFIXES
    }


def jd_text(job: Files) -> str:
    """The posting, decoded; an undecodable byte becomes U+FFFD."""
    blob = job.get(JD_FILE)
    if blob is None:
        raise MissingPartError(f"file '{JD_FILE}' is required")
    return blob.decode("utf-8", errors="replace")


def job_action(job: Files) -> str:
    """What a CV request does with this job, in the protocol's order: the document wins over the
    tex, and either wins over writing."""
    if DOCUMENT_FILE in job:
        return "render_document"
    if TEX_FILE in job:
        return "render_tex"
    return "generate"


# --- the budget -----------------------------------------------------------------------------
def _listed(names: Sequence[str]) -> str:
    return " and ".join([", ".join(names[:-1]), names[-1]] if len(names) > 1 else names)


def budget_message(built: Sequence[str], what: str, left: float, needs: float) -> str:
    """Why the job stops before ``what``: the time, which store files are built, and run again."""
    missing = [name for name in STORE_FILES if name not in built]
    head = f"{max(left, 0.0):.0f}s of the budget left, {what} needs about {needs:.0f}s"
    if not missing:
        return f"{head}: the store is complete ({_listed(built)}). Run this job again for the CV."
    held = f"the store holds {_listed(built)}" if built else "the store holds nothing yet"
    return f"{head}: {held}; still to build: {_listed(missing)}. Run this job again to continue."


def _cost(name: str, usage: Usage, seconds: float) -> str:
    return (
        f"{name}: {usage.calls} call(s), {seconds:.0f}s, tokens {usage.input_tokens} in, "
        f"{usage.thinking_tokens} thinking, {usage.output_tokens} out"
    )


def _dump(model: BaseModel) -> str:
    return model.model_dump_json(indent=1) + "\n"


class _Job:
    """One CV job's reporting: each step's status, the time left, each step's cost."""

    def __init__(self, sink: Sink, budget: float) -> None:
        self.sink = sink
        self.started = time.monotonic()
        self.deadline = self.started + budget
        self.usage: List[Usage] = []
        self.last = ""
        self._mark = self.started

    def start(
        self, step: str, needs: float = 0.0, *, built: Sequence[str] = (), what: str = "", check: bool = True
    ) -> None:
        """Report ``step``; first stop when less than ``needs`` seconds are left, or with ``needs``
        0 when the deadline has passed. ``check`` false starts it whatever is left."""
        left = self.deadline - time.monotonic()
        if check and (left < needs or left <= 0):
            if needs:
                message = budget_message(built, what or step, left, needs)
            else:
                message = f"time budget exhausted before {step}"
            raise BudgetExceededError(message, stage=step)
        self.sink.status(step, self.last)
        self._mark = time.monotonic()

    def done(self, step: str, usage: Optional[Usage] = None, what: str = "") -> None:
        """One line about the step that just finished: what it made and what it cost."""
        usage = usage or Usage()
        self.usage.append(usage)
        self.last = _cost(step, usage, time.monotonic() - self._mark) + (f"; {what}" if what else "")
        logger.info("  %s", self.last)

    def summary(self, pages: int) -> str:
        total = {f: sum(getattr(u, f) for u in self.usage) for f in ("calls", "input_tokens", "thinking_tokens")}
        tokens = total["input_tokens"] + total["thinking_tokens"] + sum(u.output_tokens for u in self.usage)
        return (
            f"done, {total['calls']} model calls, tokens used={tokens}, thinking={total['thinking_tokens']}, "
            f"elapsed={time.monotonic() - self.started:.1f}s, {pages} page(s)"
        )


# --- the pipeline ---------------------------------------------------------------------------
class Pipeline:
    """The models, the shipped prompts, template and market, and the limits: built once and
    shared by every request — none of it is personal."""

    def __init__(
        self,
        *,
        llm: ModelSelector,
        latex_timeout: float = DEFAULT_LATEX_TIMEOUT,
        budget_seconds: float = DEFAULT_BUDGET_SECONDS,
    ) -> None:
        self.llm = llm
        self.latex_timeout = latex_timeout
        self.budget_seconds = budget_seconds
        folder = resources_dir()
        self.prompts = {p.stem.removeprefix("sys_prompt_"): p.read_text(encoding="utf-8") for p in folder.glob("*.txt")}
        self.template = (folder / "resume.tex.jinja").read_text(encoding="utf-8")
        self.market = Market.model_validate_json((folder / "market.json").read_text(encoding="utf-8"))

    @classmethod
    def build(cls) -> "Pipeline":
        """``models.toml`` and the environment, once at startup."""
        return cls(
            llm=build_selector(resources_dir() / "models.toml", ROLES),
            latex_timeout=float(os.getenv("ATS_LATEX_TIMEOUT") or DEFAULT_LATEX_TIMEOUT),
            budget_seconds=float(os.getenv("ATS_REQUEST_BUDGET_SECONDS") or DEFAULT_BUDGET_SECONDS),
        )

    def info(self) -> Dict[str, Any]:
        """The model per role, and whether a pdflatex binary is available."""
        return {
            "models": {role: spec.model for role, spec in self.llm.specs.items()},
            "pdflatex": shutil.which("pdflatex") is not None,
        }

    def cv(self, pack: Files, job: Files, settings: Mapping, work_dir: Path, sink: Sink) -> str:
        """The CV for this job: ``cv.json`` in the job is rendered, else ``cv.tex`` is compiled,
        else the store is completed and the CV written from ``job_description.txt``. Returns one
        line about the run. The settings are not read."""
        action = job_action(job)
        if action != "generate":
            return self._render(pack, job, work_dir, sink, action)

        profile = json_file(pack, PROFILE_FILE, required=True)
        data = json_file(pack, DATA_FILE, required=True)
        jd = jd_text(job)
        today = date.today()
        run = _Job(sink, self.budget_seconds)
        items, tree, pool = (cached(pack, n, s) for n, s in zip(STORE_FILES, (Items, Tree, Pool), strict=True))

        def built() -> List[str]:
            return [n for n, part in zip(STORE_FILES, (items, tree, pool), strict=True) if part is not None]

        def keep(name: str, text: str) -> None:
            sink.store(Output("CACHE", "step", name, text))

        def step(name: str, data: str) -> None:
            sink.store(Output("CV", "step", f"steps/{name}", data))

        with provider_errors():
            # --- the store
            if items is None:
                run.start("Profile breakdown", ITEMS_SECONDS, built=built())
                with stage("Profile breakdown"):
                    compiler = ItemCompiler(llm=self.llm, system_prompt=self.prompts["items"])
                    items = compiler.compile(profile, today=today)
                    keep("items.json", _dump(items))
                    keep("items.md", items_markdown(profile, items))
                    run.done("Profile breakdown", compiler.usage, f"{len(items.items)} items; stored items.json")
            if tree is None:
                run.start("Skill tree", TREE_SECONDS, built=built())
                with stage("Skill tree"):
                    market = with_profile(self.market, items, profile.get("skills") or [])
                    builder = TreeBuilder(
                        llm=self.llm, shape_prompt=self.prompts["tree_shape"], attach_prompt=self.prompts["tree_attach"]
                    )
                    tree = builder.build(
                        items,
                        market,
                        skills=profile.get("skills") or [],
                        certifications=[c["name"] for c in profile.get("certifications") or []],
                        languages=data.get("languages") or "",
                    )
                    keep("tree.json", tree_dump(tree))
                    keep("tree.md", tree_markdown(tree, items, market))
                    run.done("Skill tree", builder.usage, f"{len(tree.nodes)} nodes; stored tree.json")
            if pool is None:
                run.start("Bullet writing", POOL_SECONDS, built=built(), what="Bullet writing and Bullet review")
                with stage("Bullet writing"):
                    writer = PoolWriter(
                        llm=self.llm,
                        experience_prompt=self.prompts["pool_experience"],
                        ladder_prompt=self.prompts["pool_ladder"],
                        profile_prompt=self.prompts["pool_profile"],
                        rules=self.prompts["pool_rules"],
                    )
                    written = writer.write(items, tree, profile, today=today)
                    run.done("Bullet writing", writer.usage, f"{len(written.experiences)} experience(s) written")
                # The time for the review was asked before the writing: the written pool is not dropped.
                run.start("Bullet review", check=False)
                with stage("Bullet review"):
                    reviewer = PoolReviewer(llm=self.llm, prompt=self.prompts["pool_review"])
                    pool, calls, moves = reviewer.review(written, profile)
                    keep("pool.json", pool_dump(pool))
                    keep("pool.md", pool_markdown(pool, items, tree, profile, today))
                    keep("review.md", review_markdown(calls, moves))
                    run.done("Bullet review", reviewer.usage, f"{len(calls)} replacement(s) asked; stored pool.json")

            # --- the job
            run.start("Job reading", CV_SECONDS, built=built(), what="the CV")
            with stage("Job reading"):
                allocator = Allocator(llm=self.llm, prompt=self.prompts["allocate"])
                allocation = allocator.allocate(jd, tree, items)
                step("allocation.json", _dump(allocation))
                step("allocation.md", allocation_markdown(allocation, tree))
                run.done("Job reading", allocator.usage, f"{len(allocation.requirements)} requirements")

            run.start("Content choice")
            with stage("Content choice"):
                weights = recency(profile["experiences"], today)
                ctx = context(items, tree, pool, allocation, profile["job_title"], weights)
                plan = select(ctx)
                step("plan.json", _dump(plan))
                step("plan.md", plan_markdown(plan, pool))
                forms = " ".join(f"e{x.experience}:{x.form[0]}{len(x.slots) or ''}" for x in plan.experiences)
                run.done("Content choice", what=f"{forms}, lines {plan.lines}/{plan.budget_lines}")

            run.start("Polish")
            with stage("Polish"):
                varnisher = Varnisher(
                    llm=self.llm,
                    pick_prompt=self.prompts["varnish_pick"],
                    headline_prompt=self.prompts["varnish_headline"],
                    terms_prompt=self.prompts["varnish_terms"],
                )
                varnish = varnisher.varnish(plan, profile, pool, items, allocation, data)
                run.done("Polish", varnisher.usage, f"{len(varnish.applied)} term edit(s) applied")

            run.start("Highlight")
            with stage("Highlight"):
                highlighted = Usage()
                title = profile["job_title"][0]
                sent = inputs(document(plan, profile, pool, title, varnish), plan, varnish.picks, ctx, pool, items)
                found = marks(
                    sent[1],
                    Highlighter(self.llm, self.prompts["highlight"], MARKUP).highlight(*sent, usage=highlighted),
                )
                run.done("Highlight", highlighted, f"{len(found)} text(s) marked")

            run.start("Page layout")
            with stage("Page layout"):
                renderer = CVRenderer(
                    template_source=self.template,
                    assets=images(pack),
                    work_dir=work_dir,
                    latex_timeout=self.latex_timeout,
                )
                out = render(plan, ctx, profile, pool, data, renderer, varnish, found)
                for fit in out.steps:
                    logger.info("  fit: %s", fit)
                step("varnish.json", _dump(varnish))
                step("varnish.md", varnish_markdown(varnish, plan) + highlight_markdown(out.document))
                run.done("Page layout", what=f"{out.result.pages} page(s) after {len(out.steps)} fit step(s)")

        sink.store(Output("CV", "step", DOCUMENT_FILE, json.dumps(out.document, indent=2, ensure_ascii=False)))
        sink.store(Output("CV", "step", TEX_FILE, out.result.tex))
        sink.store(Output("CV", "cv", "cv.pdf", out.result.pdf))
        return run.summary(out.result.pages)

    def render(self, pack: Files, job: Files, work_dir: Path, sink: Sink) -> None:
        """``cv.json`` rendered with v2's template, or ``cv.tex`` compiled, for nothing."""
        action = job_action(job)
        if action == "generate":
            raise MissingPartError(f"send '{DOCUMENT_FILE}' (CV data as JSON) or '{TEX_FILE}' (LaTeX source)")
        self._render(pack, job, work_dir, sink, action)

    def _render(self, pack: Files, job: Files, work_dir: Path, sink: Sink, action: str) -> str:
        renderer = CVRenderer(
            template_source=self.template, assets=images(pack), work_dir=work_dir, latex_timeout=self.latex_timeout
        )
        if action == "render_document":
            result = renderer.render_document(json_file(job, DOCUMENT_FILE, required=True))
            sink.store(Output("CV", "step", TEX_FILE, result.tex))
        else:
            result = renderer.compile_tex(text_file(job, TEX_FILE, required=True))
        sink.store(Output("CV", "cv", "cv.pdf", result.pdf))
        return f"rendered, {result.pages} page(s)"


__all__ = [
    "DEFAULT_BUDGET_SECONDS",
    "Files",
    "Output",
    "Pipeline",
    "ROLES",
    "Sink",
    "budget_message",
    "resources_dir",
]
