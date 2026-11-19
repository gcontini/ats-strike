"""The document for one job (step 7): a plan turned into the flat namespace the template prints,
the one :meth:`common.cv_renderer.CVRenderer.render_document` takes.

- the profile's fixed parts (education, certifications); the caller lays the candidate's own data
  (name, contact, languages, place) over the top, as v1 does;
- the headline: the varnish's, the plan's, or the profile's first title;
- the summary: the plan's frame with its keywords filled in, ``<<job_title>>`` filled with the
  headline, and ``<<sentence>>`` with the varnish's sentence (removed without one); or the
  varnish's summary after its replacements;
- the skills;
- each shown experience: full (its role summary, then its slots' bullets in the plan's order: the
  varnish's pick of a rung's wordings, else wording ``a``, or every bullet of a group) or compact
  (its one line). A bullet the varnish replaced text in prints its new text. A job title the
  profile lists several of prints the varnish's pick, else its first.

Pure: values in, values out.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Mapping, Optional, Sequence

from .pool_store import Pool
from .select import SUMMARY_CHARS, Plan, Slot

if TYPE_CHECKING:
    from .varnish import Varnish

#: The summary's hard limit, the room the selection plans for; S8's prompt states the soft one (450).
SUMMARY_MAX = SUMMARY_CHARS


def join_terms(words: List[str]) -> str:
    """Terms joined: "a", "a and b", "a, b and c"."""
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + " and " + words[-1]


def framed(frame: str, keywords: List[str]) -> str:
    """The frame with its keywords filled in; ``<<job_title>>`` and ``<<sentence>>`` left in."""
    return frame.replace("<<keywords>>", join_terms(keywords)) if keywords else frame.replace(" <<keywords>>", "")


def summary(frame: str, keywords: List[str], job_title: str, sentence: str = "") -> str:
    """The frame filled in: keywords, job title, and the sentence (removed when there is none)."""
    return " ".join(
        framed(frame, keywords).replace("<<job_title>>", job_title).replace("<<sentence>>", sentence).split()
    )


def printed(slots: Sequence[Slot], picks: Mapping[str, str]) -> List[str]:
    """The bullet ids a full experience's slots print, in order: a group's bullets; a rung's pick
    when the rung has it, else its wording ``a``."""
    ids: List[str] = []
    for s in slots:
        pick = picks.get(s.story)
        ids += s.bullets if isinstance(s.rung, str) else [pick if pick in s.bullets else s.bullets[0]]
    return ids


def _title(e: Mapping[str, Any]) -> List[str]:
    """The titles the profile lists for an experience."""
    return [e["title"]] if isinstance(e["title"], str) else list(e["title"])


def document(
    plan: Plan, profile: Mapping[str, Any], pool: Pool, title: str, varnish: Optional["Varnish"] = None
) -> Dict[str, Any]:
    """The template's namespace for ``plan``, with ``varnish``'s choices when given. ``title`` is
    the headline when neither has one.

    Raises ``ValueError`` when the summary runs over :data:`SUMMARY_MAX` characters."""
    picks = varnish.picks if varnish else {}
    texts = varnish.texts if varnish else {}
    headline = (varnish.headline if varnish else "") or plan.headline or title
    frames = {f.id: f.text for f in pool.frames}
    text = texts.get("summary") or summary(
        frames[plan.frame], plan.keywords, headline, varnish.sentence if varnish else ""
    )
    if len(text) > SUMMARY_MAX:
        raise ValueError(f"summary of {len(text)} characters, over {SUMMARY_MAX}: {text}")
    blocks = {b.experience: b for b in pool.experiences}
    bullets = {b.id: b.text for blk in pool.experiences for s in blk.stories for b in s.ladder}
    bullets.update({b.id: b.text for blk in pool.experiences for s in blk.stories for g in s.groups for b in g.bullets})
    source = {e["id"]: e for e in profile["experiences"]}
    experiences = []
    for x in plan.experiences:
        e, block = source[x.experience], blocks[x.experience]
        titles = _title(e)
        chosen = varnish.titles.get(x.experience) if varnish else None
        experiences.append(
            {
                "title": chosen if chosen in titles else titles[0],
                "company": e["company"],
                "location": e["location"],
                "dates": e["dates"],
                "project_name": e.get("context") or "",
                "role_summary": block.role_summary.text if x.form == "full" else "",
                "duties": [texts.get(b, bullets[b]) for b in printed(x.slots, picks)] if x.form == "full" else [],
                "compact": block.compact.text if x.form == "compact" else "",
            }
        )
    return {
        "education": profile.get("education", ""),
        "certifications": list(profile.get("certifications") or []),
        "job_title": headline,
        "summary": text,
        "skills": list(plan.skills),
        "experiences": experiences,
    }


__all__ = ["SUMMARY_MAX", "document", "framed", "join_terms", "printed", "summary"]
