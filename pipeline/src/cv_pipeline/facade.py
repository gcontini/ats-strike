"""The pipeline's face: the only thing a server calls.

Every call takes the request's files in memory — the pack, the job, the
settings (see :mod:`.pack`) — and gives back values, or hands its outputs to a
:class:`Sink` as they are ready. Nothing here knows HTTP, sessions or where a
client keeps its files.

Log lines and model-call costs flow through the active
:class:`~cv_pipeline.observability.Run`, which the caller sets up. Every
expected failure leaves as a :class:`PipelineError` with a ``kind``: a model
endpoint's error becomes ``provider_timeout``, ``provider_unreachable`` or
``provider_error`` — its body never travels, it can carry the endpoint and the
key — and a pack problem ``missing_part`` or ``bad_part``.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterator, Mapping, Protocol, Union

from openai import APIConnectionError, APIError, APITimeoutError

from . import pack as packs
from .bundle import ResourceBundle, default_bundle
from .cv_generator import CVGenerator
from .cv_renderer import DEFAULT_LATEX_TIMEOUT, CVRenderer
from .errors import (
    MissingPartError,
    ProviderError,
    ProviderTimeoutError,
    ProviderUnreachableError,
)
from .jd_validator import JDValidator
from .letter_generator import LetterGenerator
from .models import ModelSelector, build_selector

Files = Mapping[str, bytes]

#: Wall-clock budget for one CV run, checked between attempts.
DEFAULT_BUDGET_SECONDS = 1200.0
#: Generate -> review -> render rounds before the last CV is delivered anyway.
DEFAULT_MAX_ATTEMPTS = 4


@dataclass(frozen=True)
class Output:
    """One thing to store: a step's result, or the CV."""

    location: str  # "CV" | "CACHE"
    type: str  # "step" | "cv"
    name: str
    content: Union[str, bytes]


class Sink(Protocol):
    """Where a long call reports: what runs now, and each output as it is ready."""

    def status(self, status: str, detail: str = "") -> None: ...

    def store(self, output: Output) -> None: ...


def _json(data: Any) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False)


@contextmanager
def _provider_errors() -> Iterator[None]:
    """A model endpoint's failure, as a :class:`PipelineError` of its kind."""
    try:
        yield
    except APIError as exc:
        # A timeout is a connection error too, so it is asked first.
        failure = (
            ProviderTimeoutError if isinstance(exc, APITimeoutError)
            else ProviderUnreachableError if isinstance(exc, APIConnectionError)
            else ProviderError
        )
        raise failure(f"the model endpoint failed: {type(exc).__name__}") from exc


class Pipeline:
    """The models, the shipped defaults and the limits, built once and shared
    by every request — none of it is personal."""

    def __init__(
        self,
        *,
        llm: ModelSelector,
        bundle: ResourceBundle,
        latex_timeout: float = DEFAULT_LATEX_TIMEOUT,
        budget_seconds: float = DEFAULT_BUDGET_SECONDS,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    ) -> None:
        self.llm = llm
        self.bundle = bundle
        self.latex_timeout = latex_timeout
        self.budget_seconds = budget_seconds
        self.max_attempts = max_attempts

    @classmethod
    def build(cls) -> "Pipeline":
        """``models.toml`` and the environment, once at startup."""
        return cls(
            llm=build_selector(),
            bundle=default_bundle(),
            latex_timeout=float(os.getenv("RESUMIX_LATEX_TIMEOUT") or DEFAULT_LATEX_TIMEOUT),
            budget_seconds=float(
                os.getenv("RESUMIX_REQUEST_BUDGET_SECONDS") or DEFAULT_BUDGET_SECONDS
            ),
            max_attempts=int(os.getenv("RESUMIX_MAX_ATTEMPTS") or DEFAULT_MAX_ATTEMPTS),
        )

    def info(self) -> Dict[str, Any]:
        """The model per role, and whether a pdflatex binary is available."""
        return {
            "models": {role: spec.model for role, spec in self.llm.specs.items()},
            "pdflatex": shutil.which("pdflatex") is not None,
        }

    def detect(self, jd: str) -> bool:
        """Is this text a job posting? The model call only."""
        with _provider_errors():
            return JDValidator(self.llm).detect(jd).is_job_description

    def analyze(self, pack: Files, job: Files, settings: Mapping) -> Dict[str, Any]:
        """The posting scored against the profile, its facts extracted."""
        candidate = packs.candidate(pack, preferences=True)
        prompt = packs.analysis_prompt(pack)
        jd = packs.jd_text(job)
        with _provider_errors():
            return JDValidator(self.llm).analyze(jd, candidate, prompt).model_dump(mode="json")

    def letter(self, pack: Files, job: Files, settings: Mapping) -> str:
        """The cover letter, plain text."""
        candidate = packs.candidate(pack, data=True)
        bundle = self.bundle.with_overrides(**packs.bundle_overrides(pack))
        jd = packs.jd_text(job)
        analysis = packs.job_analysis(job)
        llm = self.llm.with_overrides("letter", **packs.sampling(settings))
        with _provider_errors():
            return LetterGenerator(llm, system_prompt=bundle.sys_prompt_letter).generate(
                jd, profile=candidate.profile, candidate_data=candidate.data, analysis=analysis
            )

    def cv(self, pack: Files, job: Files, settings: Mapping, work_dir: Path, sink: Sink) -> str:
        """The CV for this job: ``cv.json`` in the job is rendered, else
        ``cv.tex`` is compiled, else the CV is written from ``jd.txt``.
        Returns one line about the run."""
        action = packs.job_action(job)
        if action != "generate":
            return self._render(pack, job, work_dir, sink, action)

        bundle = self.bundle.with_overrides(**packs.bundle_overrides(pack))
        candidate = packs.candidate(pack, data=True)
        jd = packs.jd_text(job)
        pages = packs.page_limit(settings)
        llm = self.llm.with_overrides("cv", **packs.sampling(settings))

        with _provider_errors():
            document, tex, pdf, summary = CVGenerator(
                llm=llm,
                bundle=bundle,
                candidate=candidate,
                work_dir=work_dir,
                max_attempts=self.max_attempts,
                deadline=time.monotonic() + self.budget_seconds,
                latex_timeout=self.latex_timeout,
                page_limit=pages,
                progress=sink.status,
            ).generate(jd)
        sink.store(Output("CV", "step", packs.DOCUMENT_FILE, _json(document)))
        sink.store(Output("CV", "step", packs.TEX_FILE, tex))
        sink.store(Output("CV", "cv", "cv.pdf", pdf))
        return summary

    def render(self, pack: Files, job: Files, work_dir: Path, sink: Sink) -> None:
        """``cv.json`` rendered, or ``cv.tex`` compiled, for nothing."""
        action = packs.job_action(job)
        if action == "generate":
            raise MissingPartError(
                f"send '{packs.DOCUMENT_FILE}' (CV data as JSON) or '{packs.TEX_FILE}' (LaTeX source)"
            )
        self._render(pack, job, work_dir, sink, action)

    def _render(self, pack: Files, job: Files, work_dir: Path, sink: Sink, action: str) -> str:
        bundle = self.bundle.with_overrides(**packs.bundle_overrides(pack))
        renderer = CVRenderer(
            template_source=bundle.template_source,
            template_name=bundle.template_name,
            assets=bundle.assets,
            work_dir=work_dir,
            latex_timeout=self.latex_timeout,
        )
        if action == "render_document":
            result = renderer.render_document(packs.job_document(job))
            sink.store(Output("CV", "step", packs.TEX_FILE, result.tex))
        else:
            result = renderer.compile_tex(packs.job_tex(job))
        sink.store(Output("CV", "cv", "cv.pdf", result.pdf))
        return f"rendered, {result.pages} page(s)"


__all__ = ["Pipeline", "Output", "Sink", "Files"]
