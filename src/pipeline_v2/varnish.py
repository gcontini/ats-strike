"""The varnish for one job (step 8): three calls on ``varnish``, after the plan and before the render.

1. **Pick** (``sys_prompt_varnish_pick.txt``): one wording per ladder slot, and one title per
   experience among the titles the profile lists for that job. A pick the slot or the job does not
   have is dropped: the slot prints wording ``a``, the experience its first title.
2. **Headline** (``sys_prompt_varnish_headline.txt``): the job title under the name (the plan's,
   when it has one) and the summary's ``<<sentence>>``. The prompt states the room that brings the
   whole summary to :data:`SUMMARY_SOFT` characters. Code refuses the sentence when the summary then
   runs over :data:`~.document.SUMMARY_MAX`, or when it holds a number (S3's
   :func:`~.pool_store.numbers`) that neither the rest of the summary nor the experiences print.
   The rank rule ("Head", "Manager", ... only when held) is the prompt's alone.
3. **Terms in** (``sys_prompt_varnish_terms.txt``): code offers the job's terms the CV lacks: the
   keywords of each requirement landed on a proven or weak node of which the CV prints none yet
   (:func:`~.terms.has_term`), most important requirement first. The model returns replacements
   ``{target, old, new}`` in the summary first, then in the bullets, each bullet shown with the
   facts it rests on (its ``uses`` items). Code applies one only when the piece keeps its printed
   lines (a bullet) or the summary stays within ``SUMMARY_MAX``, it adds no number, every job term
   it adds is an offered one (at least one), it takes out no job term the piece held, and the
   summary keeps the headline it held. The rest are listed with the reason.

Each reply is JSON taken as text (:func:`~.pool.json_object`); one that does not parse goes back
once with the fault, then fails. The result is a :class:`Varnish`, which
:func:`~.document.document` prints over the plan.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from pydantic import BaseModel

from common.errors import ModelOutputError
from common.models import ModelSelector, Usage
from common.observability import stage

from .allocate import Allocation
from .document import SUMMARY_MAX, _title, document, framed, printed, summary
from .pool import _validated, json_object
from .pool_store import BULLET_LINE, Bullet, Pool, ladder_and_group_bullets, lines, numbers
from .select import Plan
from .store import Items, _Strict
from .terms import has_term

logger = logging.getLogger("pipeline_v2.varnish")

ROLE = "varnish"
STAGE = "Polish"
#: What the headline prompt aims the whole summary at; code accepts up to SUMMARY_MAX.
SUMMARY_SOFT = 450
#: The job title's length the sentence's room assumes when the call writes the title too.
TITLE_ROOM = 45
#: The terms-in target for the summary; a bullet is its id.
SUMMARY = "summary"
#: Requirements whose terms may be offered: they landed where the profile backs them.
BACKED = ("proven", "weak")
LETTERS = "abcdefghij"


# --- the replies ---------------------------------------------------------------------------------
class PickReply(BaseModel):
    picks: Dict[str, str] = {}  # story -> wording letter
    titles: Dict[str, str] = {}  # "e0" -> title letter


class HeadlineReply(BaseModel):
    job_title: str = ""
    sentence: str = ""


class Replacement(BaseModel):
    target: str  # "summary" or a bullet id
    old: str
    new: str


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
    headline: str = ""  # "" = the document's fallback
    sentence: str = ""  # the summary's sentence, as printed ("" when refused)
    sentence_refused: str = ""  # the model's sentence and why it is not printed
    offered: List[str] = []  # the terms offered, most important first
    texts: Dict[str, str] = {}  # "summary" and bullet ids -> their text after the replacements
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


def apply_terms(
    replacements: Sequence[Replacement],
    texts: Mapping[str, str],
    offered: Sequence[str],
    terms: Sequence[str],
    headline: str,
) -> Tuple[Dict[str, str], List[Replacement], List[Refused]]:
    """The replacements applied to ``texts`` (``"summary"`` and the printed bullets, by id) in order,
    each one only when its old text is in the piece, the summary keeps the ``headline`` it held,
    the piece keeps every job term (``terms``) it held, it adds at least one job term and only
    offered ones, it adds no number the piece did not print, and the piece keeps its printed lines
    (a bullet) or stays within ``SUMMARY_MAX`` characters (the summary). Returns the texts that
    changed, the replacements applied and those refused, with the reason."""
    current = dict(texts)
    allowed = {t.lower() for t in offered}
    applied: List[Replacement] = []
    refused: List[Refused] = []
    for r in replacements:
        before = current.get(r.target)
        after = before.replace(r.old, r.new, 1) if before is not None and r.old else ""
        added = [t for t in terms if has_term(t, after) and not has_term(t, before or "")]
        removed = [t for t in terms if has_term(t, before or "") and not has_term(t, after)]
        foreign = [t for t in added if t.lower() not in allowed]
        extra = sorted(_values(after) - _values(texts.get(r.target, "")))
        if before is None:
            reason = "not the summary nor a printed bullet"
        elif not r.old or r.old not in before:
            reason = "old text not found"
        elif r.target == SUMMARY and headline and has_term(headline, before) and not has_term(headline, after):
            reason = "changes the job title"
        elif removed:
            reason = f"removes {', '.join(removed)}"
        elif not added:
            reason = "adds no offered term"
        elif foreign:
            reason = f"adds {', '.join(foreign)}, not offered"
        elif extra:
            reason = f"adds the number {', '.join(extra)}"
        elif r.target == SUMMARY and len(after) > SUMMARY_MAX:
            reason = f"summary of {len(after)} characters, over {SUMMARY_MAX}"
        elif r.target != SUMMARY and lines(after) > lines(texts[r.target]):
            reason = f"{lines(after)} lines, was {lines(texts[r.target])}"
        else:
            current[r.target] = after
            applied.append(r)
            continue
        refused.append(Refused(target=r.target, old=r.old, new=r.new, reason=reason))
    return {k: v for k, v in current.items() if v != texts[k]}, applied, refused


# --- the calls -----------------------------------------------------------------------------------
class Varnisher:
    """The three varnish calls for one job's plan."""

    def __init__(self, *, llm: ModelSelector, pick_prompt: str, headline_prompt: str, terms_prompt: str) -> None:
        self.llm = llm
        self.pick_prompt = pick_prompt
        self.headline_prompt = headline_prompt
        self.terms_prompt = terms_prompt
        #: Every model call of every varnish, failed ones included.
        self.usage = Usage()

    def varnish(
        self,
        plan: Plan,
        profile: Mapping[str, Any],
        pool: Pool,
        items: Items,
        allocation: Allocation,
        data: Mapping[str, Any],
    ) -> Varnish:
        """Pick, headline and sentence, terms in. ``data`` is the candidate's own data the render
        lays over the document (languages count as printed). Raises :class:`ModelOutputError` when
        a reply does not parse twice."""
        title = (profile.get("job_title") or [""])[0]
        with stage(STAGE):
            v = self._pick(plan, profile, pool, allocation)
            v = self._headline(plan, profile, pool, allocation, v, title)
            return self._terms(plan, profile, pool, items, allocation, data, v, title)

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

    # 1. pick ------------------------------------------------------------------------------------
    def _pick(self, plan: Plan, profile: Mapping[str, Any], pool: Pool, allocation: Allocation) -> Varnish:
        text = {b.id: b.text for b in _bullets(pool)}
        source = {e["id"]: e for e in profile["experiences"]}
        out = []
        for x in plan.experiences:
            e = source[x.experience]
            head = f"EXPERIENCE e{x.experience}: {e['company']} ({e['dates']})"
            out.append(f"{head} COMPACT: {_compact(pool, x.experience)}" if x.form == "compact" else head)
            titles = _title(e)
            if len(titles) > 1:
                out.append("  TITLES: " + "; ".join(f"{LETTERS[i]}) {t}" for i, t in enumerate(titles)))
            for s in x.slots:
                if isinstance(s.rung, str):
                    out.append(f"  {s.story} GROUP:\n" + "\n".join(f"    - {text[b]}" for b in s.bullets))
                elif len(s.bullets) == 1:
                    out.append(f"  {s.story} (one wording): {text[s.bullets[0]]}")
                else:
                    out.append(
                        f"  {s.story}:\n" + "\n".join(f"    {b.rsplit('.', 1)[1]}) {text[b]}" for b in s.bullets)
                    )
        user = f"JD TITLE: {allocation.title}\nJD TERMS: {', '.join(jd_terms(allocation))}\n\nCV:\n" + "\n".join(out)
        reply = self._ask("pick", self.pick_prompt, user, PickReply)

        ladder = {s.story: s for x in plan.experiences for s in x.slots if not isinstance(s.rung, str)}
        picks = {}
        for story, letter in reply.picks.items():
            chosen = [b for b in ladder[story].bullets if b.endswith("." + letter.strip())] if story in ladder else []
            if chosen:
                picks[story] = chosen[0]
        titles: Dict[int, str] = {}
        for key, letter in reply.titles.items():
            e = source.get(_int(key.lstrip("e")))
            index = LETTERS.find(letter.strip()[:1]) if letter.strip() else -1
            if e is not None and 0 <= index < len(_title(e)) and any(x.experience == e["id"] for x in plan.experiences):
                titles[e["id"]] = _title(e)[index]
        logger.info(
            "  [%s] pick: %d of %d slots, %d title(s)",
            ROLE,
            len(picks),
            sum(len(s.bullets) > 1 for s in ladder.values()),
            len(titles),
        )
        return Varnish(picks=picks, titles=titles)

    # 2. headline and sentence -------------------------------------------------------------------
    def _headline(
        self, plan: Plan, profile: Mapping[str, Any], pool: Pool, allocation: Allocation, v: Varnish, title: str
    ) -> Varnish:
        frame = {f.id: f.text for f in pool.frames}[plan.frame]
        fixed = plan.headline or ""
        room = SUMMARY_SOFT - 1 - len(summary(frame, plan.keywords, fixed or "x" * TITLE_ROOM))
        doc = document(plan, profile, pool, title, v)
        reqs = sorted(allocation.requirements, key=lambda r: -r.importance)[:8]
        held = [t for e in profile["experiences"] for t in _title(e)]
        user = (
            f"JD TITLE: {allocation.title}\nTOP REQUIREMENTS:\n"
            + "\n".join(f"- {r.name} ({r.importance}): {', '.join(r.keywords)}" for r in reqs)
            + f"\n\nTITLES HELD: {'; '.join(held)}\nTITLES ALLOWED: {'; '.join(profile.get('job_title') or [])}\n"
            + (f"FIXED TITLE: {fixed}\n" if fixed else "")
            + f"\nSUMMARY: {' '.join(framed(frame, plan.keywords).split())}\n\nEXPERIENCES:\n"
            + _experiences(doc)
        )
        reply = self._ask("headline", self.headline_prompt.replace("{max_chars}", str(room)), user, HeadlineReply)
        headline = fixed or " ".join(reply.job_title.split()) or title
        sentence = " ".join(reply.sentence.split())
        rest = summary(frame, plan.keywords, headline)
        fault = sentence_fault(summary(frame, plan.keywords, headline, sentence), sentence, rest, _experiences(doc))
        logger.info(
            "  [%s] headline %r, sentence of %d characters%s",
            ROLE,
            headline,
            len(sentence),
            f": {fault}" if fault else "",
        )
        return v.model_copy(
            update={
                "headline": headline,
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
        texts = {SUMMARY: doc["summary"], **{b: pieces[b].text for b in ids}}
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
            + f"\n\nSUMMARY (at most {SUMMARY_MAX} characters, now {len(texts[SUMMARY])}):\n{texts[SUMMARY]}"
            + "\n\nBULLETS:\n"
            + "\n\n".join(bullets)
        )
        reply = self._ask("terms", self.terms_prompt, user, TermsReply)
        changed, applied, refused = apply_terms(
            reply.replacements, texts, offered, jd_terms(allocation), doc["job_title"]
        )
        logger.info(
            "  [%s] terms in: %d requirement(s) offered, %d replacement(s) applied, %d refused",
            ROLE,
            len(wanted),
            len(applied),
            len(refused),
        )
        return v.model_copy(update={"offered": offered, "texts": changed, "applied": applied, "refused": refused})


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


def varnish_markdown(v: Varnish, plan: Plan) -> str:
    """The view: headline and sentence, the picks, the terms offered, applied and refused."""
    out = [
        f"# Varnish: {plan.title}",
        "",
        f"- Headline: {v.headline}" + (" (the plan's)" if plan.headline else ""),
        f"- Sentence: {v.sentence or '(none)'}",
    ]
    if v.sentence_refused:
        out.append(f"- Sentence refused: {v.sentence_refused}")
    out += ["", "## Picks", ""]
    for x in plan.experiences:
        if x.experience in v.titles:
            out.append(f"- e{x.experience} title: {v.titles[x.experience]}")
        for s in x.slots:
            if not isinstance(s.rung, str) and len(s.bullets) > 1:
                pick = v.picks.get(s.story)
                out.append(f"- {s.story} r{s.rung}: " + (pick.rsplit(".", 1)[1] if pick else "a (no pick)"))
    out += ["", "## Terms in", "", f"Offered: {', '.join(v.offered) or '(none)'}", "", "Applied:", ""]
    out += [f"- **{r.target}**: `{r.old}` -> `{r.new}`" for r in v.applied] or ["- (none)"]
    out += ["", "Refused:", ""]
    out += [f"- **{r.target}** ({r.reason}): `{r.old}` -> `{r.new}`" for r in v.refused] or ["- (none)"]
    if SUMMARY in v.texts:
        out += ["", "Summary after the terms:", "", f"> {v.texts[SUMMARY]}"]
    return "\n".join(out) + "\n"


__all__ = [
    "ROLE",
    "SUMMARY_SOFT",
    "Refused",
    "Replacement",
    "Varnish",
    "Varnisher",
    "apply_terms",
    "cv_text",
    "jd_terms",
    "offer",
    "sentence_fault",
    "varnish_markdown",
]
