"""Tokenisation shared by BM25 and the value index."""
from __future__ import annotations

import re

_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_WORD = re.compile(r"[a-z0-9]+")
STOPWORDS = {
    "a", "an", "the", "of", "in", "on", "for", "to", "and", "or", "is", "are", "was", "were",
    "what", "which", "who", "whom", "how", "many", "much", "list", "show", "give", "me", "all",
    "with", "by", "from", "that", "than", "their", "there", "this", "those", "these", "it",
    "do", "does", "did", "be", "been", "has", "have", "had", "at", "as", "each", "per",
    "please", "find", "name", "names", "number",
}


def split_identifier(name: str) -> str:
    """`custID` -> `cust id`, `order_items` -> `order items`."""
    return _CAMEL.sub(" ", name).replace("_", " ").replace("-", " ").lower()


def tokenize(text: str, drop_stopwords: bool = True) -> list[str]:
    toks = _WORD.findall(split_identifier(text))
    toks = [_stem(t) for t in toks]
    return [t for t in toks if not (drop_stopwords and t in STOPWORDS)]


def _stem(t: str) -> str:
    """Tiny plural stemmer: enough to match `customers` with `customer`."""
    if len(t) > 4 and t.endswith("ies"):
        return t[:-3] + "y"
    if len(t) > 3 and t.endswith("s") and not t.endswith("ss"):
        return t[:-1]
    return t


def normalize_value(v: str) -> str:
    return re.sub(r"\s+", " ", v.strip().lower())


def trigrams(s: str) -> set[str]:
    s = f"  {s} "
    return {s[i:i + 3] for i in range(len(s) - 2)}
