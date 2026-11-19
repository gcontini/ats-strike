"""Pure text helpers for terms: whole-word matching, compound phrases, copy detection.

Values in, values out. The market list, the tree's checks and later the JD allocation and the
keyword score all find terms in text the same way, so the rule lives here once.
"""

from __future__ import annotations

import re
from typing import FrozenSet, List, Tuple

#: A term of up to this many characters must not be written all in lower case to count.
SHORT = 3

#: Where a compound phrase splits: " and ", "&", ",", parentheses, and a "/" between two words
#: longer than 3 characters ("Linux/Windows" splits, "CI/CD" and "TCP/IP" stay whole).
_SPLIT = re.compile(r"\s+and\s+|&|,|\(|\)|(?<=\w{4})/(?=\w{4})", re.I)


def has_term(term: str, text: str) -> bool:
    """``term`` as a whole word in ``text``, ignoring case.

    A term of up to 3 characters does not count where the text writes it all in lower case: "ai"
    is an Italian preposition, "go" an English verb. A hyphen just before the term joins it to the
    word before ("non-functional" does not hold "functional", "pre-sales" not "sales"); one just
    after does not ("AI-driven" holds "AI").
    """
    term = term.strip()
    if not term:
        return False
    rx = re.compile(r"(?<![\w-])" + re.escape(term) + r"(?!\w)", re.I)
    short = len(term) <= SHORT
    return any(not short or m.group() != m.group().lower() for m in rx.finditer(text))


def split_compound(phrase: str) -> List[str]:
    """"docker and kubernetes" -> ["docker", "kubernetes"]; "IaC(Terraform)" -> ["IaC", "Terraform"].

    The parts keep their case and lose surrounding spaces; empty parts are dropped.
    """
    return [part.strip() for part in _SPLIT.split(phrase) if part.strip()]


def norm(term: str) -> str:
    """A term as compared with another: lower case, spaces squashed, no surrounding punctuation."""
    return re.sub(r"\s+", " ", term).strip().strip(".;:").strip().lower()


def shingles(text: str, n: int = 8) -> FrozenSet[Tuple[str, ...]]:
    """The word ``n``-grams of ``text``, lower case: what the copy check compares."""
    words = re.findall(r"\w+", text.lower())
    return frozenset(tuple(words[k:k + n]) for k in range(len(words) - n + 1))


def jaccard(a: FrozenSet, b: FrozenSet) -> float:
    """Shared shingles over all shingles; 0 when both are empty. A copy is 0.5 or more."""
    union = len(a | b)
    return len(a & b) / union if union else 0.0


__all__ = ["has_term", "jaccard", "norm", "shingles", "split_compound"]
