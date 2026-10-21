"""The free half of "is this a job description?".

Shared so both sides agree on the answer: the client runs it before spending a
network call, the server runs it before spending an LLM call. Nothing here
needs a model, a key or a network.

The answer is yes or no. Why a piece of text failed is not interesting to a
caller — it is either worth a model call or it is not — and the server's log
has the detail if anyone needs it.
"""

from __future__ import annotations

#: A posting shorter than this is a fragment; longer is an article or a dump.
#: Both are measured on postings as they arrive — a job-board page copied
#: whole, with its navigation and applicant counts — not on the bare text: a
#: title and a list of requirements is ~900 characters with that clutter, a
#: public-sector selection notice or a benefits-heavy posting is ~18000.
MIN_JD_CHARS = 500
MAX_JD_CHARS = 30000

#: Above this share of control characters the payload is binary, not prose.
_MAX_CONTROL_RATIO = 0.1


def static_jd_guess(
    text: str, *, min_chars: int = MIN_JD_CHARS, max_chars: int = MAX_JD_CHARS
) -> bool:
    """Cheap structural checks: is this plausibly the text of a job posting?

    Says no to binary payloads and to anything outside the length band. A
    "yes" means "worth asking a model", never "this is a job posting".
    """
    if "\x00" in text:
        return False
    if not min_chars <= len(text) <= max_chars:
        return False
    controls = sum(1 for ch in text if ord(ch) < 32 and ch not in "\t\n\r")
    return controls <= len(text) * _MAX_CONTROL_RATIO


__all__ = ["static_jd_guess", "MIN_JD_CHARS", "MAX_JD_CHARS"]
