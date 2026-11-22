"""The varnish for one job (step 8): three calls on ``varnish``, after the plan and before the render.

1. **Compose** (``sys_prompt_varnish_compose.txt``): per full experience, which of its candidates
   (:func:`~.select.candidates`: the plan's slots and the stories it left out, each scored) print,
   in which order and wording, with small edits that link the bullets; and one title per experience
   among the titles the profile lists for that job. Code keeps an experience's reply only when every
   id is one of its candidates, one per story, at least 2 bullets, within the plan's lines for it;
   otherwise the experience prints the plan's slots in profile order, wording ``a``. An edit applies
   only when the bullet keeps its printed lines. A title the job does not
   have is dropped: the experience prints its first. The result is a new plan
   (:func:`~.select.composed`), which the rest of the CV prints.
2. **Headline** (``sys_prompt_varnish_headline.txt``): the job title under the name (the plan's,
   when it has one), the summary's ``<<keywords>>`` (at most :data:`KEYWORDS`) and its
   ``<<sentence>>``. The prompt states :data:`SUMMARY_SOFT` characters for the whole summary. Code refuses the sentence when the summary then
   runs over :data:`~.document.SUMMARY_MAX`, or when it holds a number (S3's
   :func:`~.pool_store.numbers`) that neither the rest of the summary nor the experiences print.
   The rank rule ("Head", "Manager", ... only when held) is the prompt's alone.
3. **Terms in** (``sys_prompt_varnish_terms.txt``): code offers the job's terms the CV lacks: the
   keywords of each requirement landed on a proven or weak node of which the CV prints none yet
   (:func:`~.terms.has_term`), most important requirement first. The model returns replacements
   ``{target, old, new}`` in the bullets, each bullet as the composition left it and shown with the
   facts it rests on (its ``uses`` items); the summary is never edited. The prompt alone states that
   a replacement must read fluently, with the case of the text around it. Code applies one only
   when the bullet keeps its printed lines and every job term it adds is an offered one (at least
   one). The rest are listed with the reason.

Each reply is JSON taken as text (:func:`~.pool.json_object`); one that does not parse goes back
once with the fault, then fails. The result is the composed plan and a
:class:`Varnish`, which :func:`~.document.document` prints over it.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, Union

from pydantic import BaseModel

from common.errors import ModelOutputError
from common.models import ModelSelector, Usage
from common.observability import stage

from .allocate import Allocation
from .document import SUMMARY_MAX, _title, document, printed, summary
from .pool import _validated, json_object
from .pool_store import BULLET_LINE, Bullet, Pool, ladder_and_group_bullets, lines, numbers
from .select import Candidate, Context, Plan, RungKey, candidates, composed
from .store import Items, _Strict
from .terms import has_term

logger = logging.getLogger("pipeline_v2.varnish")

ROLE = "varnish"
STAGE = "Polish"
#: What the headline prompt aims the whole summary at; code accepts up to SUMMARY_MAX.
SUMMARY_SOFT = 450
#: The most keywords the summary's <<keywords>> slot takes.
KEYWORDS = 3
#: Requirements whose terms may be offered: they landed where the profile backs them.
BACKED = ("proven", "weak")
LETTERS = "abcdefghij"


# --- the replies ---------------------------------------------------------------------------------
class Replacement(BaseModel):
    target: str  # a bullet id
    old: str
    new: str


class ComposeReply(BaseModel):
    experiences: Dict[str, List[str]] = {}  # "e0" -> the wording and group ids it prints, in order
    edits: List[Replacement] = []
    titles: Dict[str, str] = {}  # "e0" -> title letter


class HeadlineReply(BaseModel):
    job_title: str = ""
    keywords: List[str] = []
    sentence: str = ""


class TermsReply(BaseModel):
    replacements: List[Replacement] = []


# --- the result ----------------------------------------------------------------------------------
class Refused(_Strict):
    target: str
    old: str
    new: str
    reason: str


class Varnish(_Strict):
    picks: Dict[str, str] = {}  # story -> the bullet id a ladder slot prints
    titles: Dict[int, str] = {}  # experience -> the title it prints
    kept: Dict[int, str] = {}  # experience -> why it prints the plan's slots, not the reply's
    edits: List[Replacement] = []  # the composition's edits applied
    edits_refused: List[Refused] = []
    headline: str = ""  # "" = the document's fallback
    keywords: List[str] = []  # the summary's <<keywords>>, at most KEYWORDS
    sentence: str = ""  # the summary's sentence, as printed ("" when refused)
    sentence_refused: str = ""  # the model's sentence and why it is not printed
    offered: List[str] = []  # the terms offered, most important first
    texts: Dict[str, str] = {}  # bullet ids -> their text after the edits and the replacements
    applied: List[Replacement] = []
    refused: List[Refused] = []


# --- pure helpers --------------------------------------------------------------------------------
def jd_terms(allocation: Allocation) -> List[str]:
    """Every requirement's keywords, most important requirement first, each term once."""
    out: List[str] = []
    for r in sorted(allocation.requirements, key=lambda r: -r.importance):
        out += [t for t in r.keywords if t.lower() not in {x.lower() for x in out}]
    return out


def cv_text(doc: Mapping[str, Any]) -> str:
    """Everything a document prints, as one text: what a term is looked for in."""
    parts = [doc.get("job_title") or "", doc.get("summary") or "", *(doc.get("skills") or [])]
    for e in doc.get("experiences") or []:
        parts += [e["title"], e.get("project_name") or "", e.get("role_summary") or "", *e["duties"], e["compact"]]
    parts += [doc.get("languages") or "", doc.get("education") or ""]
    parts += [c["name"] for c in doc.get("certifications") or []]
    return "\n".join(parts)


def offer(allocation: Allocation, text: str) -> List[Tuple[str, int, List[str]]]:
    """The requirements on a proven or weak node of which ``text`` holds no keyword, most important
    first: (name, importance, keywords)."""
    return [
        (r.name, r.importance, list(r.keywords))
        for r in sorted(allocation.requirements, key=lambda r: -r.importance)
        if r.landing in BACKED and r.keywords and not any(has_term(t, text) for t in r.keywords)
    ]


def _values(text: str) -> set:
    return {value for _, value in numbers(text)}


def sentence_fault(full: str, sentence: str, rest: str, experiences: str) -> str:
    """Why a sentence is not printed, or "": the summary ``full`` over ``SUMMARY_MAX``, or a number
    of the sentence that neither ``rest`` (the summary without it) nor ``experiences`` hold."""
    if len(full) > SUMMARY_MAX:
        return f"the summary would be {len(full)} characters, over {SUMMARY_MAX}"
    unknown = sorted(_values(sentence) - _values(rest) - _values(experiences))
    return f"the number {', '.join(unknown)} is in neither the summary nor the experiences" if unknown else ""


def chosen(
    ids: Sequence[str], options: Sequence[Candidate], budget: int
) -> Tuple[List[Tuple[str, RungKey]], Dict[str, str], str]:
    """The (story, rung) slots, in order, and the wording picks (story -> wording id) that a reply's
    ids name for one experience, with "" as the fault; or nothing, with why they are refused: an id
    that is no candidate, two of one story, fewer than 2 bullets, or more lines than ``budget``."""
    named: Dict[str, Tuple[Candidate, str]] = {c.id: (c, "") for c in options if isinstance(c.rung, str)}
    named.update({b: (c, b) for c in options if not isinstance(c.rung, str) for b in c.bullets})
    taken: List[Tuple[Candidate, str]] = []
    for i in ids:
        if i not in named:
            return [], {}, f"{i} is not a candidate"
        if named[i][0].story in {c.story for c, _ in taken}:
            return [], {}, f"two of {named[i][0].story}"
        taken.append(named[i])
    bullets = sum(len(c.bullets) if isinstance(c.rung, str) else 1 for c, _ in taken)
    used = sum(c.lines for c, _ in taken)
    if bullets < 2:
        return [], {}, f"{bullets} bullet(s), fewer than 2"
    if used > budget:
        return [], {}, f"{used} lines, over {budget}"
    return [(c.story, c.rung) for c, _ in taken], {c.story: w for c, w in taken if w}, ""


def _edited(r: Replacement, current: Mapping[str, str], texts: Mapping[str, str]) -> Tuple[str, str]:
    """The bullet after ``r`` and why it is refused, "" when it is not: not a printed bullet, its
    old text not found, or more printed lines than in ``texts`` (the bullets as they came)."""
    before = current.get(r.target)
    if before is None:
        return "", "not a printed bullet"
    if not r.old or r.old not in before:
        return "", "old text not found"
    after = before.replace(r.old, r.new, 1)
    if lines(after) > lines(texts[r.target]):
        return after, f"{lines(after)} lines, was {lines(texts[r.target])}"
    return after, ""


def apply_edits(
    replacements: Sequence[Replacement], texts: Mapping[str, str]
) -> Tuple[Dict[str, str], List[Replacement], List[Refused]]:
    """The composition's edits applied to ``texts`` (the printed bullets, by id) in order, each one
    only when its old text is in the bullet and the bullet keeps its printed lines. Returns the texts
    that changed, the edits applied and those refused, with the reason."""
    current = dict(texts)
    applied: List[Replacement] = []
    refused: List[Refused] = []
    for r in replacements:
        after, reason = _edited(r, current, texts)
        if reason:
            refused.append(Refused(target=r.target, old=r.old, new=r.new, reason=reason))
            continue
        current[r.target] = after
        applied.append(r)
    return {k: v for k, v in current.items() if v != texts[k]}, applied, refused


def apply_terms(
    replacements: Sequence[Replacement],
    texts: Mapping[str, str],
    offered: Sequence[str],
    terms: Sequence[str],
) -> Tuple[Dict[str, str], List[Replacement], List[Refused]]:
    """The replacements applied as :func:`apply_edits` applies the edits, each one also only when it
    adds at least one job term and only offered ones. Returns the texts that changed, the
    replacements applied and those refused, with the reason."""
    current = dict(texts)
    allowed = {t.lower() for t in offered}
    applied: List[Replacement] = []
    refused: List[Refused] = []
    for r in replacements:
        after, reason = _edited(r, current, texts)
        if not reason:
            added = [t for t in terms if has_term(t, after) and not has_term(t, current[r.target])]
            foreign = [t for t in added if t.lower() not in allowed]
            if not added:
                reason = "adds no offered term"
            elif foreign:
                reason = f"adds {', '.join(foreign)}, not offered"
        if reason:
            refused.append(Refused(target=r.target, old=r.old, new=r.new, reason=reason))
            continue
        current[r.target] = after
        applied.append(r)
    return {k: v for k, v in current.items() if v != texts[k]}, applied, refused


# --- the calls -----------------------------------------------------------------------------------
class Varnisher:
    """The three varnish calls for one job's plan."""

    def __init__(self, *, llm: ModelSelector, compose_prompt: str, headline_prompt: str, terms_prompt: str) -> None:
        self.llm = llm
        self.compose_prompt = compose_prompt
        self.headline_prompt = headline_prompt
        self.terms_prompt = terms_prompt
        #: Every model call of every varnish, failed ones included.
        self.usage = Usage()

    def varnish(
        self,
        plan: Plan,
        ctx: Context,
        profile: Mapping[str, Any],
        pool: Pool,
        items: Items,
        allocation: Allocation,
        data: Mapping[str, Any],
    ) -> Tuple[Plan, Varnish]:
        """Compose, headline and sentence, terms in: the composed plan and its varnish. ``ctx`` is
        the selection's context of ``plan``; ``data`` is the candidate's own data the render lays over
        the document (languages count as printed). Raises :class:`ModelOutputError` when a reply does
        not parse twice."""
        title = (profile.get("job_title") or [""])[0]
        with stage(STAGE):
            plan, v = self._compose(plan, ctx, profile, pool, allocation)
            v = self._headline(plan, profile, pool, allocation, v, title)
            return plan, self._terms(plan, profile, pool, items, allocation, data, v, title)

    def _ask(self, step: str, system: str, user: str, reply: type) -> Any:
        messages = [{"role": "user", "content": user}]
        for attempt in (1, 2):
            raw = self.llm.call_llm(ROLE, str, system, messages, usage=self.usage)
            try:
                return _validated(reply, json_object(raw))
            except ValueError as error:
                problem = str(error)
            logger.warning("  ✗ [%s] %s reply %d/2 rejected:\n%s", ROLE, step, attempt, problem)
            messages += [
                {"role": "assistant", "content": raw},
                {
                    "role": "user",
                    "content": f"Your previous reply was rejected:\n{problem}\n\n"
                    "Reply again with the complete, corrected output only.",
                },
            ]
        raise ModelOutputError(f"{ROLE}: the {step} reply was rejected:\n{problem}", stage=STAGE)

    # 1. compose ---------------------------------------------------------------------------------
    def _compose(
        self, plan: Plan, ctx: Context, profile: Mapping[str, Any], pool: Pool, allocation: Allocation
    ) -> Tuple[Plan, Varnish]:
        text = {b.id: b.text for b in _bullets(pool)}
        roles = {b.experience: b.role_summary.text for b in pool.experiences}
        source = {e["id"]: e for e in profile["experiences"]}
        options = candidates(plan, ctx)
        budget = {x.experience: sum(s.lines for s in x.slots) for x in plan.experiences if x.form == "full"}
        out = []
        for x in plan.experiences:
            e = source[x.experience]
            head = f"EXPERIENCE e{x.experience}: {e['company']} ({e['dates']})"
            full = x.form == "full"
            out.append(
                f"{head} FULL, at most {budget[x.experience]} LINES"
                if full
                else f"{head} COMPACT: {_compact(pool, x.experience)}"
            )
            titles = _title(e)
            if len(titles) > 1:
                out.append("  TITLES: " + "; ".join(f"{LETTERS[i]}) {t}" for i, t in enumerate(titles)))
            if not full:
                continue
            out += [f"  ROLE SUMMARY: {roles[x.experience]}", "  OPTIONS:"]
            for c in options[x.experience]:
                kind = " GROUP" if isinstance(c.rung, str) else ""
                out.append(
                    f"  {c.id}{kind}{' PICKED' if c.picked else ''} ({c.lines} line{'s' * (c.lines != 1)}, "
                    f"score {round(c.score * 1000)})"
                )
                out += [f"    {b}: {text[b]}" for b in c.bullets]
        user = f"JD TITLE: {allocation.title}\nJD TERMS: {', '.join(jd_terms(allocation))}\n\nCV:\n" + "\n".join(out)
        reply = self._ask("compose", self.compose_prompt, user, ComposeReply)

        slots: Dict[int, List[Tuple[str, RungKey]]] = {}
        picks: Dict[str, str] = {}
        kept: Dict[int, str] = {}
        for key, ids in reply.experiences.items():
            e = _int(key.lstrip("e"))
            if e not in options:
                continue
            mine, mine_picks, fault = chosen(ids, options[e], budget[e])
            if fault:
                kept[e] = fault
                continue
            slots[e] = mine
            picks.update(mine_picks)
        new = composed(plan, ctx, slots)
        ids = [b for x in new.experiences if x.form == "full" for b in printed(x.slots, picks)]
        texts, edits, refused = apply_edits(reply.edits, {b: text[b] for b in ids})
        titles: Dict[int, str] = {}
        for key, letter in reply.titles.items():
            e = source.get(_int(key.lstrip("e")))
            index = LETTERS.find(letter.strip()[:1]) if letter.strip() else -1
            if e is not None and 0 <= index < len(_title(e)) and any(x.experience == e["id"] for x in new.experiences):
                titles[e["id"]] = _title(e)[index]
        logger.info(
            "  [%s] compose: %d of %d full experience(s) composed%s; %d edit(s) applied, %d refused; "
            "objective %.4f -> %.4f",
            ROLE,
            len(slots),
            len(options),
            "".join(f"; e{e} kept: {why}" for e, why in kept.items()),
            len(edits),
            len(refused),
            plan.scores.objective,
            new.scores.objective,
        )
        return new, Varnish(picks=picks, titles=titles, kept=kept, texts=texts, edits=edits, edits_refused=refused)

    # 2. headline and sentence -------------------------------------------------------------------
    def _headline(
        self, plan: Plan, profile: Mapping[str, Any], pool: Pool, allocation: Allocation, v: Varnish, title: str
    ) -> Varnish:
        frame = {f.id: f.text for f in pool.frames}[plan.frame]
        fixed = plan.headline or ""
        doc = document(plan, profile, pool, title, v)
        reqs = sorted(allocation.requirements, key=lambda r: -r.importance)[:8]
        held = [t for e in profile["experiences"] for t in _title(e)]
        user = (
            f"JD TITLE: {allocation.title}\nTOP REQUIREMENTS:\n"
            + "\n".join(f"- {r.name} ({r.importance}, {r.landing}): {', '.join(r.keywords)}" for r in reqs)
            + f"\n\nTITLES HELD: {'; '.join(held)}\nTITLES ALLOWED: {'; '.join(profile.get('job_title') or [])}\n"
            + (f"FIXED TITLE: {fixed}\n" if fixed else "")
            + f"\nSUMMARY: {' '.join(frame.split())}\n\nEXPERIENCES:\n"
            + _experiences(doc)
        )
        reply = self._ask("headline", self.headline_prompt.replace("{max_chars}", str(SUMMARY_SOFT)), user, HeadlineReply)
        headline = fixed or " ".join(reply.job_title.split()) or title
        keywords = [" ".join(k.split()) for k in reply.keywords if k.strip()][:KEYWORDS]
        sentence = " ".join(reply.sentence.split())
        rest = summary(frame, keywords, headline)
        fault = sentence_fault(summary(frame, keywords, headline, sentence), sentence, rest, _experiences(doc))
        logger.info(
            "  [%s] headline %r, keywords %s, sentence of %d characters%s",
            ROLE,
            headline,
            keywords,
            len(sentence),
            f": {fault}" if fault else "",
        )
        return v.model_copy(
            update={
                "headline": headline,
                "keywords": keywords,
                "sentence": "" if fault else sentence,
                "sentence_refused": f"{sentence} ({fault})" if fault else "",
            }
        )

    # 3. terms in --------------------------------------------------------------------------------
    def _terms(
        self,
        plan: Plan,
        profile: Mapping[str, Any],
        pool: Pool,
        items: Items,
        allocation: Allocation,
        data: Mapping[str, Any],
        v: Varnish,
        title: str,
    ) -> Varnish:
        doc = document(plan, profile, pool, title, v)
        wanted = offer(allocation, cv_text({**doc, **dict(data)}))
        offered = [t for _, _, terms in wanted for t in terms]
        if not wanted:
            logger.info("  [%s] terms in: nothing to offer", ROLE)
            return v
        pieces = {b.id: b for b in _bullets(pool)}
        ids = [b for x in plan.experiences if x.form == "full" for b in printed(x.slots, v.picks)]
        texts = {b: v.texts.get(b, pieces[b].text) for b in ids}
        by_id = {i.id: i for i in items.items}
        bullets = []
        for bid in ids:
            facts = "; ".join(_fact(by_id[i]) for i in pieces[bid].uses if i in by_id)
            room = lines(texts[bid]) * BULLET_LINE
            bullets.append(
                f"[{bid}] (at most {room} characters, now {len(texts[bid])})\n{texts[bid]}\n  FACTS: {facts}"
            )
        user = (
            f"JD TITLE: {allocation.title}\n\nOFFERED TERMS:\n"
            + "\n".join(f"- {name} ({importance}): {', '.join(terms)}" for name, importance, terms in wanted)
            + "\n\nBULLETS:\n"
            + "\n\n".join(bullets)
        )
        reply = self._ask("terms", self.terms_prompt, user, TermsReply)
        changed, applied, refused = apply_terms(reply.replacements, texts, offered, jd_terms(allocation))
        logger.info(
            "  [%s] terms in: %d requirement(s) offered, %d replacement(s) applied, %d refused",
            ROLE,
            len(wanted),
            len(applied),
            len(refused),
        )
        return v.model_copy(
            update={"offered": offered, "texts": {**v.texts, **changed}, "applied": applied, "refused": refused}
        )


# --- views -----------------------------------------------------------------------------------------
def _int(text: str) -> Optional[int]:
    return int(text) if text.strip().isdigit() else None


def _bullets(pool: Pool) -> List[Bullet]:
    """Every ladder and group bullet of the pool."""
    return [b for blk in pool.experiences for b in ladder_and_group_bullets(blk)]


def _compact(pool: Pool, experience: int) -> str:
    return next(b.compact.text for b in pool.experiences if b.experience == experience)


def _experiences(doc: Mapping[str, Any]) -> str:
    """A document's experiences as the headline call reads them: header, then what it prints."""
    return "\n".join(
        f"{e['title']} / {e['company']} ({e['dates']})\n"
        + "\n".join(f"  - {d}" for d in ([e["role_summary"], *e["duties"]] if e["duties"] else [e["compact"]]))
        for e in doc["experiences"]
    )


def _fact(item: Any) -> str:
    return item.text + (f" (tech: {', '.join(item.tech)})" if item.tech else "")


def _changes(rs: Sequence[Union[Replacement, Refused]]) -> List[str]:
    return [
        f"- **{r.target}**" + (f" ({r.reason})" if isinstance(r, Refused) else "") + f": `{r.old}` -> `{r.new}`"
        for r in rs
    ] or ["- (none)"]


def varnish_markdown(v: Varnish, plan: Plan, composed: Plan) -> str:
    """The view: headline and sentence, the composition of ``plan`` into ``composed``, the terms
    offered, applied and refused."""
    out = [
        f"# Varnish: {plan.title}",
        "",
        f"- Headline: {v.headline}" + (" (the plan's)" if plan.headline else ""),
        f"- Sentence: {v.sentence or '(none)'}",
    ]
    if v.sentence_refused:
        out.append(f"- Sentence refused: {v.sentence_refused}")
    out += [
        "",
        "## Composition",
        "",
        f"- Objective: plan {plan.scores.objective:.4f}, composed {composed.scores.objective:.4f}",
    ]
    before = {x.experience: [s.story for s in x.slots] for x in plan.experiences}
    for x in composed.experiences:
        e = x.experience
        if e in v.titles:
            out.append(f"- e{e} title: {v.titles[e]}")
        if x.form != "full":
            continue
        shown = [
            (f"{s.story}.{s.rung}" if isinstance(s.rung, str) else v.picks.get(s.story, s.bullets[0]))
            + (" (swapped in)" if s.story not in before.get(e, []) else "")
            for s in x.slots
        ]
        gone = [s for s in before.get(e, []) if s not in {t.story for t in x.slots}]
        out.append(f"- e{e}: {', '.join(shown)}" + (f"; left out {', '.join(gone)}" if gone else ""))
        if e in v.kept:
            out.append(f"  - kept the plan's slots: {v.kept[e]}")
    out += ["", "Edits applied:", "", *_changes(v.edits), "", "Edits refused:", "", *_changes(v.edits_refused)]
    out += ["", "## Terms in", "", f"Offered: {', '.join(v.offered) or '(none)'}", "", "Applied:", ""]
    out += [*_changes(v.applied), "", "Refused:", "", *_changes(v.refused)]
    return "\n".join(out) + "\n"


__all__ = [
    "ROLE",
    "SUMMARY_SOFT",
    "Refused",
    "Replacement",
    "Varnish",
    "Varnisher",
    "apply_edits",
    "apply_terms",
    "chosen",
    "cv_text",
    "jd_terms",
    "offer",
    "sentence_fault",
    "varnish_markdown",
]
