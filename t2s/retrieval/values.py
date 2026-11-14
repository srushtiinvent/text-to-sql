"""Value index: which column contains the literal the user typed?

Questions mention values ("customers in Delhi", "the Electronics category") that
are not in the schema at all. This index stores distinct text values per column,
keyed by character trigrams, and matches spans of the question against them.
Fuzzy matching handles case, spacing and small typos ("electronic" -> "Electronics").
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from difflib import SequenceMatcher

from .text import STOPWORDS, normalize_value, trigrams


@dataclass
class ValueMatch:
    table: str
    column: str
    value: str
    span: str
    score: float

    def to_dict(self) -> dict:
        return self.__dict__.copy()


class ValueIndex:
    def __init__(self) -> None:
        self.values: list[tuple[str, str, str, str]] = []  # (table, column, raw, normalized)
        self.postings: dict[str, list[int]] = defaultdict(list)

    @classmethod
    def build(cls, fetch_distinct, schema, max_values: int = 5000, max_len: int = 80) -> "ValueIndex":
        """`fetch_distinct(table, column, limit)` returns distinct non-null values of a column."""
        idx = cls()
        for t in schema.tables.values():
            for c in t.columns:
                if not _is_text(c.type):
                    continue
                try:
                    vals = fetch_distinct(t.name, c.name, max_values)
                except Exception:
                    continue
                for v in vals:
                    if not isinstance(v, str) or not v.strip() or len(v) > max_len:
                        continue
                    if _looks_like_id(v):
                        continue
                    idx._add(t.name, c.name, v)
        return idx

    def _add(self, table: str, column: str, raw: str) -> None:
        norm = normalize_value(raw)
        i = len(self.values)
        self.values.append((table, column, raw, norm))
        for g in trigrams(norm):
            self.postings[g].append(i)

    def match(self, question: str, threshold: float = 0.75, limit: int = 12) -> list[ValueMatch]:
        best: dict[tuple[str, str, str], ValueMatch] = {}
        for span in candidate_spans(question):
            norm = normalize_value(span)
            grams = trigrams(norm)
            counts: dict[int, int] = defaultdict(int)
            for g in grams:
                for i in self.postings.get(g, ()):
                    counts[i] += 1
            # only score values sharing enough trigrams (cheap filter before SequenceMatcher)
            for i, shared in counts.items():
                table, column, raw, vnorm = self.values[i]
                if shared < 0.5 * min(len(grams), len(trigrams(vnorm))):
                    continue
                score = 1.0 if vnorm == norm else SequenceMatcher(None, norm, vnorm).ratio()
                if score < threshold:
                    continue
                key = (table, column, raw)
                if key not in best or score > best[key].score:
                    best[key] = ValueMatch(table, column, raw, span, round(score, 3))
        ranked = sorted(best.values(), key=lambda m: (-m.score, -len(m.span)))
        return ranked[:limit]

    def __len__(self) -> int:
        return len(self.values)


def candidate_spans(question: str, max_words: int = 4) -> list[str]:
    """Quoted strings plus every 1..4-word window that is not just stopwords."""
    spans = re.findall(r"['\"“”‘’]([^'\"“”‘’]{1,80})['\"“”‘’]", question)
    words = re.findall(r"[\w.&'-]+", question)
    for n in range(1, max_words + 1):
        for i in range(len(words) - n + 1):
            window = words[i:i + n]
            if all(w.lower() in STOPWORDS for w in window):
                continue
            if n == 1 and (len(window[0]) < 3 or window[0].isdigit()):
                continue
            spans.append(" ".join(window))
    seen, out = set(), []
    for s in spans:
        if s.lower() not in seen:
            seen.add(s.lower())
            out.append(s)
    return out


def _is_text(type_name: str) -> bool:
    t = (type_name or "").upper()
    return t == "" or any(k in t for k in ("CHAR", "TEXT", "CLOB", "STRING", "VARCHAR"))


def _looks_like_id(v: str) -> bool:
    return bool(re.fullmatch(r"[0-9a-f-]{16,}|\d{4}-\d{2}-\d{2}.*", v.strip(), re.I))
