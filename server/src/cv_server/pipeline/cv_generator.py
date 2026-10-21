"""Writing the CV: job description in, a rendered CV out, in six steps.

The method is adapticv-ai's, one model role per step:

``jd_evaluation``
    The JD's key points, each with an importance from 0 to 100.
``profile_scoring``
    Every list of the profile scored against them, one call per list: a
    string item becomes ``text|3-80,7-40`` (key point id - score), an object
    gets a ``"score": "3-80,7-40"`` field. The words that prove each match
    stay out of the profile, in ``score_reasons``: the writer and the
    highlighter are shown them as EVIDENCE.
``format_selection``
    One of :data:`FORMATS`, the experiences to show and how many duties each.
``cv``
    The body (job title, summary, skills, certifications), and each chosen
    experience on its own.
``validation``
    The body and each experience checked against the profile, as
    replacements the code applies (:mod:`.cv_review`).
``highlight``
    ``**bold**`` on the key points' keywords, ``*italics*`` on the stacks.

The steps are a LangGraph graph. A step that makes several calls fans them
out with ``Send`` — one per profile list, per experience — and a node in
code puts the answers back in profile order. Then the CV is rendered: while
the PDF runs past the page limit, the duty whose removal loses the least
:func:`capped_coverage` goes, and it is rendered again. Every render is of the
merged document, your ``candidate_data`` over the top, so every page count is
the page count of the CV you will send — yet no model is ever shown that data.

The code assumes nothing about the CV or the profile beyond the work
experiences (with an ``id`` and ``duties``), their duties and the writing
style: the prompts and the template decide everything else. The prompts, the
two schemas and the template arrive in a
:class:`~cv_server.bundle.ResourceBundle`, the profile and the data in
:class:`~cv_server.bundle.CandidateInputs`; nothing is read from disk and
nothing is written outside ``work_dir``.
"""

from __future__ import annotations

import copy
import json
import logging
import operator
import re
import time
from pathlib import Path
from typing import Annotated, Any, Callable, Dict, List, Mapping, Optional, Tuple, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from ..bundle import CandidateInputs, ResourceBundle
from ..models import ModelSelector, Usage
from ..observability import LOGGER_ROOT, stage
from .cv_renderer import CVRenderer, RenderResult
from .cv_review import ReplaceInBody, ReplaceInDuties, apply_body, apply_duties, review
from .errors import BudgetExceededError, ModelOutputError

logger = logging.getLogger(f"{LOGGER_ROOT}.cv")

#: The CV formats format_selection chooses from; the render keeps at least
#: min_duties per experience. Shown to the model as they are.
FORMATS = {
    "A": {"experiences": 3, "min_duties": 3, "max_duties": 8, "max_total_duties": 22},
    "B": {"experiences": 4, "min_duties": 2, "max_duties": 6, "max_total_duties": 17},
    "C": {"experiences": 5, "min_duties": 1, "max_duties": 4, "max_total_duties": 13},
}
#: The "|3-80,7-40" suffix of a scored text: key point id - score.
SCORES = re.compile(r"\|((?:\d+-\d+,?)*)$")
#: A key point counts up to COVERAGE_CAP x its importance in duty score
#: points, and only its COVERAGE_BEST best duty scores count. The duties
#: prompt says the same in words ("2 best scores", "2 x its importance").
COVERAGE_CAP = 2
COVERAGE_BEST = 2

#: Each step, and the state key that says it has finished.
STEPS = (
    ("jd_evaluation", "key_points"),
    ("profile_scoring", "score_reasons"),
    ("format_selection", "format_selection"),
    ("cv", "cv_experiences"),
    ("validation", "checked_experiences"),
    ("highlight", "highlighted_resume"),
)
#: What the client is handed as the steps finish, by state key.
ARTIFACTS = (
    "key_points", "scored_profile", "score_reasons", "format_selection", "cv_body",
    "cv_experiences", "checked_body", "checked_experiences", "highlighted_resume",
)

#: Called as the run moves on: the step starting now, and one line about the
#: one that just finished. Both go straight to a client, unread by anything.
ProgressFn = Callable[[str, str], None]
#: Called with each intermediate result, by name, as it is ready.
ArtifactFn = Callable[[str, Any], None]


class CVState(TypedDict, total=False):
    """What the steps hand each other. The lists with a reducer collect the
    answers of a fanned-out step, each tagged with its position."""

    jd: str
    key_points: List[dict]
    scores: Annotated[List[Tuple[int, list]], operator.add]
    scored_profile: dict
    score_reasons: dict
    format_selection: dict
    cv_body: dict
    written: Annotated[List[Tuple[int, dict]], operator.add]
    cv_experiences: List[dict]
    checked_body: dict
    checked: Annotated[List[Tuple[int, dict]], operator.add]
    checked_experiences: List[dict]
    highlighted_resume: dict


# --- pure helpers -----------------------------------------------------------
def dumps(obj: Any) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False)


def suffix(matches: List[dict]) -> str:
    """The scores of profile_scoring's matches, as "3-80,7-40" (key point id - score)."""
    return ",".join(f"{m['key_point']}-{m['score']}" for m in matches)


def scores_of(text: str) -> Dict[int, int]:
    """{key point id: score} from the "|3-80,7-40" suffix of a scored text; {} without one."""
    found = SCORES.search(text)
    if not found:
        return {}
    return {int(k): int(s) for k, s in (p.split("-") for p in found.group(1).split(",") if p)}


def without_scores(obj: Any) -> Any:
    """obj without the scores of profile_scoring: "text|3-80,7-40" becomes "text", and the "score" keys go."""
    if isinstance(obj, dict):
        return {k: without_scores(v) for k, v in obj.items() if k != "score"}
    if isinstance(obj, list):
        return [without_scores(v) for v in obj]
    return SCORES.sub("", obj) if isinstance(obj, str) else obj


def capped_coverage(duties: List[str], importance: Mapping[int, int]) -> int:
    """How much of the key points these scored duties cover: for each key point, its COVERAGE_BEST best duty scores
    added up, counting up to COVERAGE_CAP x its importance. More duties on the same key point add nothing."""
    scores: Dict[int, List[int]] = {k: [] for k in importance}
    for duty in duties:
        for k, score in scores_of(duty).items():
            if k in scores:
                scores[k].append(score)
    best = {k: sum(sorted(s, reverse=True)[:COVERAGE_BEST]) for k, s in scores.items()}
    return sum(min(best[k], COVERAGE_CAP * importance[k]) for k in importance)


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


def score_targets(profile: Dict[str, Any]) -> List[Tuple[str, Dict[str, Any], str, Optional[dict]]]:
    """Every list profile_scoring scores, as (label, the object holding it, its key, context): the top-level lists
    but the experiences, then the lists inside each experience, with its other fields as context."""
    targets = [(k, profile, k, None) for k, v in profile.items() if isinstance(v, list) and k != "experiences"]
    for i, exp in enumerate(profile["experiences"]):
        context = {k: v for k, v in exp.items() if not isinstance(v, list)}
        targets += [(f"experiences[{i}].{k}", exp, k, context) for k, v in exp.items() if isinstance(v, list)]
    return targets


def duty_matches(state: Mapping[str, Any], exp_id: int) -> List[List[dict]]:
    """The matches (key point, score, evidence) of each duty of the experience with this id, in duty order."""
    position = next(p for p, e in enumerate(state["scored_profile"]["experiences"]) if e["id"] == exp_id)
    return [r["matches"] for r in state["score_reasons"][f"experiences[{position}].duties"]]


def key_points_and_style(state: Mapping[str, Any]) -> str:
    """The input both writing prompts start with: KEY POINTS, and WRITING STYLE when the profile has one."""
    style = state["scored_profile"].get("writing_style")
    return f"KEY POINTS:\n{dumps(state['key_points'])}\n\n" + (f"WRITING STYLE:\n{style}\n\n" if style else "")


def style_and_attempt(profile: Mapping[str, Any], attempt: int) -> str:
    """The input both validation prompts start with: WRITING STYLE when the profile has one, and ATTEMPT."""
    style = profile.get("writing_style")
    return (f"WRITING STYLE:\n{style}\n\n" if style else "") + f"ATTEMPT: {attempt}\n\n"


def in_order(answers: List[Tuple[int, Any]]) -> List[Any]:
    """A fanned-out step's answers, back in the order they were sent."""
    return [answer for _, answer in sorted(answers, key=lambda pair: pair[0])]


def _tokens(usage: Usage) -> int:
    """Every token a call was billed for: input, thinking and reply."""
    return usage.input_tokens + usage.thinking_tokens + usage.output_tokens


class CVGenerator:
    """Writes one CV for one job description.

    Parameters
    ----------
    llm:
        The selector: one role per step. Built once by the caller and shared
        across requests — it holds HTTP clients, not per-job state.
    bundle:
        Prompts, schemas and the LaTeX template for this run (defaults, or
        whatever the request overrode).
    candidate:
        The profile the models read and the data only the template prints.
        Not cached; it dies with the request.
    work_dir:
        Scratch directory for the renders. Created and removed by the caller.
    deadline:
        Optional :func:`time.monotonic` value, checked before each step, so a
        run that cannot finish in time fails with a clear cause instead of
        being cut off by a proxy.
    progress, artifact:
        Called as each step starts and with each intermediate result. ``None``
        runs the whole thing silently, which is what most tests do.
    """

    def __init__(
        self,
        *,
        llm: ModelSelector,
        bundle: ResourceBundle,
        candidate: CandidateInputs,
        work_dir: Path,
        deadline: Optional[float] = None,
        latex_timeout: Optional[float] = None,
        page_limit: Optional[int] = None,
        progress: Optional[ProgressFn] = None,
        artifact: Optional[ArtifactFn] = None,
    ) -> None:
        self.llm = llm
        self.bundle = bundle
        self.candidate = candidate
        self.deadline = deadline
        self.progress = progress
        self.artifact = artifact
        #: Every model call of the run, so a step can say what it cost.
        self.usage = Usage()

        renderer_kwargs: Dict[str, Any] = {}
        if latex_timeout is not None:
            renderer_kwargs["latex_timeout"] = latex_timeout
        if page_limit is not None:
            renderer_kwargs["page_limit"] = page_limit
        self.renderer = CVRenderer(
            template_source=bundle.template_source,
            template_name=bundle.template_name,
            assets=bundle.assets,
            work_dir=Path(work_dir),
            **renderer_kwargs,
        )

    # --- the run ------------------------------------------------------------
    def generate(self, job_description: str) -> Tuple[Dict[str, Any], str, bytes, str]:
        """Run the six steps, then render. Returns the merged document, its
        LaTeX, its PDF and one line saying what the run cost.

        The graph's state is read after every superstep: each intermediate
        result goes to ``artifact`` as soon as it is there, and when a step's
        last result arrives the next step is reported, with what the one that
        finished cost.
        """
        started = time.monotonic()
        steps = iter(STEPS)
        step, done_when = next(steps)
        self._check_deadline(step)
        self._report(step)
        mark = self._mark()
        handed: set = set()
        state: Dict[str, Any] = {}
        for state in self._graph().stream({"jd": job_description}, stream_mode="values"):
            for key in ARTIFACTS:
                if key in state and key not in handed:
                    handed.add(key)
                    if self.artifact is not None:
                        self.artifact(key, state[key])
            while done_when is not None and done_when in state:
                finished = self._finished(step, state)
                step, done_when = next(steps, (None, None))
                if step is not None:
                    self._check_deadline(step)
                    self._report(step, self._cost(mark, finished))
                    mark = self._mark()

        document, result, removed = self._render(state)
        summary = (
            f"done, {self.usage.calls} model calls, tokens used={_tokens(self.usage)}, "
            f"thinking={self.usage.thinking_tokens}, "
            f"elapsed={time.monotonic() - started:.1f}s, {result.pages} page(s)"
            + (f", {removed} duty(s) removed to fit" if removed else "")
        )
        return document, result.tex, result.pdf, summary

    def _graph(self):
        """START -> evaluate_jd -> score_list (one per profile list) -> merge_scores -> select_format
        -> write_body + write_duties (one per experience) -> collect_cv
        -> validate_body + validate_duties (one per experience) -> collect_validation -> highlight -> END"""
        graph = StateGraph(CVState)
        graph.add_node("evaluate_jd", self._evaluate_jd)
        graph.add_node("score_list", self._score_list)
        graph.add_node("merge_scores", self._merge_scores)
        graph.add_node("select_format", self._select_format)
        graph.add_node("write_body", self._write_body)
        graph.add_node("write_duties", self._write_duties)
        graph.add_node("collect_cv", self._collect_cv)
        graph.add_node("validate_body", self._validate_body)
        graph.add_node("validate_duties", self._validate_duties)
        graph.add_node("collect_validation", self._collect_validation)
        graph.add_node("highlight", self._highlight)
        graph.add_edge(START, "evaluate_jd")
        graph.add_conditional_edges("evaluate_jd", self._each_list, ["score_list"])
        graph.add_edge("score_list", "merge_scores")
        graph.add_edge("merge_scores", "select_format")
        graph.add_conditional_edges("select_format", self._each_part, ["write_body", "write_duties"])
        graph.add_edge("write_body", "collect_cv")
        graph.add_edge("write_duties", "collect_cv")
        graph.add_conditional_edges("collect_cv", self._each_written, ["validate_body", "validate_duties"])
        graph.add_edge("validate_body", "collect_validation")
        graph.add_edge("validate_duties", "collect_validation")
        graph.add_edge("collect_validation", "highlight")
        graph.add_edge("highlight", END)
        return graph.compile()

    # --- jd_evaluation ------------------------------------------------------
    def _evaluate_jd(self, state: CVState) -> dict:
        with stage("cv.jd_evaluation"):
            return {"key_points": self._ask("jd_evaluation", list, self.bundle.sys_prompt_jd_evaluation, state["jd"])}

    # --- profile_scoring ----------------------------------------------------
    def _each_list(self, state: CVState) -> List[Send]:
        """One score_list per list of the profile, all at once."""
        return [
            Send("score_list", {"position": n, "key_points": state["key_points"], "items": obj[key], "context": ctx})
            for n, (_, obj, key, ctx) in enumerate(score_targets(copy.deepcopy(dict(self.candidate.profile))))
        ]

    def _score_list(self, task: dict) -> dict:
        """Score one list against the key points it matches (at most 4 per item).

        The model also quotes the words of the item that prove each match: they
        go to score_reasons, with a reason, not into the profile."""
        text = (
            f"KEY POINTS:\n{dumps(task['key_points'])}\n\n"
            + (f"EXPERIENCE:\n{dumps(task['context'])}\n\n" if task["context"] else "")
            + f"ITEMS:\n{dumps(task['items'])}"
        )
        with stage("cv.profile_scoring"):
            results = self._ask("profile_scoring", list, self.bundle.sys_prompt_profile_scoring, text)
        return {"scores": [(task["position"], results)]}

    def _merge_scores(self, state: CVState) -> dict:
        """In code: strings become "text|3-80,7-40", objects get a "score" field with the same scores."""
        profile = copy.deepcopy(dict(self.candidate.profile))
        reasons = {}
        for (label, obj, key, _), results in zip(score_targets(profile), in_order(state["scores"]), strict=True):
            pairs = list(zip(obj[key], results, strict=True))
            reasons[label] = [{"item": item, "matches": r["matches"], "reason": r["reason"]} for item, r in pairs]
            obj[key] = [
                {**item, "score": suffix(r["matches"])} if isinstance(item, dict) else f"{item}|{suffix(r['matches'])}"
                for item, r in pairs
            ]
        return {"scored_profile": profile, "score_reasons": reasons}

    # --- format_selection ---------------------------------------------------
    def _select_format(self, state: CVState) -> dict:
        """The CV format (FORMATS), the experiences to show and how many duties each one shows."""
        text = (
            f"KEY POINTS:\n{dumps(state['key_points'])}\n\nFORMATS:\n{dumps(FORMATS)}\n\n"
            f"WORK EXPERIENCES:\n{dumps(state['scored_profile']['experiences'])}"
        )
        with stage("cv.format_selection"):
            choice = self._ask("format_selection", dict, self.bundle.sys_prompt_format_selection, text)
            logger.info("format %s: experiences %s, duties %s\n  %s", choice["format"],
                        choice["experience_ids"], choice["duty_caps"], choice["reason"])
        return {"format_selection": choice}

    # --- cv -----------------------------------------------------------------
    def _each_part(self, state: CVState) -> List[Send]:
        """The body, and one write_duties per chosen experience, all at once."""
        choice = state["format_selection"]
        chosen = zip(choice["experience_ids"], choice["duty_caps"], strict=True)
        return [Send("write_body", state)] + [
            Send("write_duties", {**state, "position": n, "exp_id": exp_id, "cap": cap})
            for n, (exp_id, cap) in enumerate(chosen)
        ]

    def _write_body(self, state: CVState) -> dict:
        """The CV fields outside the work experiences, as in the body schema."""
        profile = {k: v for k, v in state["scored_profile"].items() if k != "writing_style"}
        text = key_points_and_style(state) + f"SCHEMA:\n{self.bundle.cv_body_schema}\n\nMASTER PROFILE:\n{dumps(profile)}"
        with stage("cv.cv"):
            return {"cv_body": self._ask("cv", dict, self.bundle.sys_prompt_cv_body, text)}

    def _write_duties(self, task: dict) -> dict:
        """One experience within its duty cap, as in the duties schema.

        EVIDENCE gives, for each duty number (1 = first) with some match, its matches with the words that prove them."""
        experience = next(e for e in task["scored_profile"]["experiences"] if e["id"] == task["exp_id"])
        evidence = {n: matches for n, matches in enumerate(duty_matches(task, task["exp_id"]), 1) if matches}
        text = (
            key_points_and_style(task) + f"SCHEMA:\n{self.bundle.cv_duties_schema}\n\n"
            f"DUTY CAP: {task['cap']}\n\nEXPERIENCE:\n{dumps(experience)}\n\nEVIDENCE:\n{dumps(evidence)}"
        )
        with stage("cv.cv"):
            written = self._ask("cv", dict, self.bundle.sys_prompt_cv_duties, text)
        return {"written": [(task["position"], written)]}

    def _collect_cv(self, state: CVState) -> dict:
        return {"cv_experiences": in_order(state["written"])}

    # --- validation ---------------------------------------------------------
    def _each_written(self, state: CVState) -> List[Send]:
        """The body, and one validate_duties per written experience, all at once."""
        return [Send("validate_body", state)] + [
            Send("validate_duties", {**state, "position": n}) for n in range(len(state["cv_experiences"]))
        ]

    def _validate_body(self, state: CVState) -> dict:
        """The body against the (unscored) profile: see cv_review."""
        profile = {k: v for k, v in self.candidate.profile.items() if k != "writing_style"}

        def text_for(doc: dict, attempt: int) -> str:
            return (style_and_attempt(self.candidate.profile, attempt)
                    + f"MASTER PROFILE:\n{dumps(profile)}\n\nGENERATED CV BODY:\n{dumps(doc)}")

        with stage("cv.validation"):
            doc = review(self.llm, ReplaceInBody, apply_body, self.bundle.sys_prompt_validation_body,
                         copy.deepcopy(state["cv_body"]), text_for, "body", usage=self.usage)
        return {"checked_body": doc}

    def _validate_duties(self, task: dict) -> dict:
        """One experience against the same (unscored) experience of the profile: see cv_review."""
        n = task["position"]
        exp_id = task["format_selection"]["experience_ids"][n]
        source = next(e for e in self.candidate.profile["experiences"] if e["id"] == exp_id)

        def text_for(doc: dict, attempt: int) -> str:
            return (style_and_attempt(self.candidate.profile, attempt)
                    + f"EXPERIENCE:\n{dumps(source)}\n\nGENERATED EXPERIENCE:\n{dumps(doc)}")

        doc = copy.deepcopy(task["cv_experiences"][n])
        with stage("cv.validation"):
            doc = review(self.llm, ReplaceInDuties, apply_duties, self.bundle.sys_prompt_validation_duties,
                         doc, text_for, doc["company"], usage=self.usage)
        return {"checked": [(n, doc)]}

    def _collect_validation(self, state: CVState) -> dict:
        return {"checked_experiences": in_order(state["checked"])}

    # --- highlight ----------------------------------------------------------
    def _highlight(self, state: CVState) -> dict:
        """Marks the KEY POINTS keywords with **bold** and the technical stacks with *italics*: CVRenderer turns them
        into LaTeX. The prompt says what to mark; a text whose words the model changed keeps its original.

        The model is shown the CV the models wrote — never your candidate data, which goes over the top at the
        render. EVIDENCE: for each experience of the CV, the matches of its duties in the profile, with their words.
        A highlighter that gives no usable answer leaves the CV unmarked rather than failing a finished job."""
        resume = {**state["checked_body"], "experiences": state["checked_experiences"]}
        evidence = [
            [m for matches in duty_matches(state, i) for m in matches]
            for i in state["format_selection"]["experience_ids"]
        ]
        text = f"KEY POINTS:\n{dumps(state['key_points'])}\n\nCV:\n{dumps(resume)}\n\nEVIDENCE:\n{dumps(evidence)}"
        with stage("cv.highlight"):
            try:
                resume = keep_words(self._ask("highlight", dict, self.bundle.sys_prompt_highlight, text), resume)
            except ModelOutputError as e:
                logger.warning("  ⚠ highlighting failed (%s) — continuing with the unmarked CV", e)
            flat = json.dumps(resume)  # every text, to list the markers
            logger.info("bold: %s", re.findall(r"\*\*([^*]+)\*\*", flat))
            logger.info("italics: %s", re.findall(r"(?<!\*)\*([^*]+)\*(?!\*)", flat))
        return {"highlighted_resume": resume}

    # --- the render ---------------------------------------------------------
    def _render(self, state: Mapping[str, Any]) -> Tuple[Dict[str, Any], RenderResult, int]:
        """The highlighted CV without the scores, compiled; the document, the result and how many duties went.

        While the PDF is longer than the page limit, it removes a duty of an experience with more duties than the
        format's minimum: the one whose removal loses the fewest capped_coverage points; on a tie, the one with the
        lowest total score, then the older experience's. Then it renders again. When no duty can go, it keeps the
        longer PDF."""
        resume = copy.deepcopy(state["highlighted_resume"])
        min_duties = FORMATS[state["format_selection"]["format"]]["min_duties"]
        importance = {k["id"]: k["importance"] for k in state["key_points"]}
        removed = 0
        with stage("render"):
            while (result := self.renderer.render_document(self._document(resume))).pages > self.renderer.page_limit:
                shown = [(i, j, d) for i, e in enumerate(resume["experiences"]) for j, d in enumerate(e["duties"])]
                covered = capped_coverage([d for _, _, d in shown], importance)
                removable = [  # (points lost without the duty, its total score, -i, -j: the older experience's, i, j)
                    (
                        covered - capped_coverage([d for a, b, d in shown if (a, b) != (i, j)], importance),
                        sum(scores_of(duty).values()),
                        -i,
                        -j,
                        i,
                        j,
                    )
                    for i, j, duty in shown
                    if len(resume["experiences"][i]["duties"]) > min_duties
                ]
                if not removable:
                    logger.warning("  ⚠ %d pages and no duty left to remove: keeping it", result.pages)
                    break
                lost, _, _, _, i, j = min(removable)
                exp = resume["experiences"][i]
                duty = exp["duties"].pop(j)
                removed += 1
                logger.info("%d pages: removed duty %d of %s (scores %s, %d coverage points lost): %s...",
                            result.pages, j + 1, exp["company"], scores_of(duty), lost, SCORES.sub("", duty)[:50])
            logger.info("rendered: %d pages, %d lines past page %d",
                        result.pages, result.overflow_lines, self.renderer.page_limit)
        return self._document(resume), result, removed

    def _document(self, resume: Mapping[str, Any]) -> Dict[str, Any]:
        """One flat namespace for the template: the CV without the scores, and your own data over the top, so a
        field a model invents can never replace a real name, email or address."""
        return {**without_scores(resume), **dict(self.candidate.data)}

    # --- helpers ------------------------------------------------------------
    def _ask(self, role: str, output_type: type, prompt: str, text: str) -> Any:
        return self.llm.call_llm(role, output_type, prompt, [{"role": "user", "content": text}], usage=self.usage)

    def _finished(self, step: str, state: Mapping[str, Any]) -> str:
        """One line, sometimes two, about a step that has just finished."""
        if step == "jd_evaluation":
            return f"{len(state['key_points'])} key points"
        if step == "profile_scoring":
            return f"{len(state['score_reasons'])} lists of the profile scored"
        if step == "format_selection":
            choice = state["format_selection"]
            return (f"format {choice['format']}: experiences {choice['experience_ids']}, "
                    f"duties {choice['duty_caps']}")
        if step == "cv":
            return f"body and {len(state['cv_experiences'])} experience(s) written"
        if step == "validation":
            return f"body and {len(state['checked_experiences'])} experience(s) validated"
        return f"{step} finished"

    def _check_deadline(self, step: str) -> None:
        """Stop before starting a step that cannot finish in the budget."""
        if self.deadline is not None and time.monotonic() >= self.deadline:
            raise BudgetExceededError(f"time budget exhausted before {step}", stage=f"cv.{step}")

    def _mark(self) -> Tuple[Usage, float]:
        """Where the ledger stands now; a step's cost is the difference."""
        return copy.copy(self.usage), time.monotonic()

    def _cost(self, mark: Tuple[Usage, float], what: str) -> str:
        spent = self.usage - mark[0]
        return (
            f"{what}, tokens used={_tokens(spent)}, thinking={spent.thinking_tokens}, "
            f"elapsed={time.monotonic() - mark[1]:.1f}s"
        )

    def _report(self, status: str, detail: str = "") -> None:
        if self.progress is not None:
            self.progress(status, detail)


__all__ = [
    "CVGenerator",
    "FORMATS",
    "COVERAGE_CAP",
    "COVERAGE_BEST",
    "capped_coverage",
    "keep_words",
    "scores_of",
    "score_targets",
    "without_scores",
]
