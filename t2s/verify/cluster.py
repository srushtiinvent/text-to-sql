"""Group candidates by what they return, not by what their SQL looks like.

Design choices (see docs/design.md):
  * Rows are compared as a multiset: row order is ignored, duplicates are not.
    Ordering rarely changes the answer to a question; a missing DISTINCT does.
  * Values inside a row are compared order-insensitively, so `SELECT name, total`
    and `SELECT total, name` land in the same cluster.
  * Numbers are normalised (3.0 == 3, floats rounded to 6 places) because SQLite
    returns int or float depending on the expression.
"""
from __future__ import annotations

import datetime as dt
import decimal
import re
from collections import Counter
from dataclasses import dataclass, field

from ..execute.base import ResultSet


def norm_value(v):
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, decimal.Decimal):
        v = float(v)
    if isinstance(v, float):
        if v != v:  # NaN
            return "NaN"
        r = round(v, 6)
        return int(r) if r.is_integer() else r
    if isinstance(v, (bytes, bytearray, memoryview)):
        return bytes(v).hex()
    if isinstance(v, (dt.date, dt.datetime, dt.time)):
        return v.isoformat()
    return v


def _row_key(row: tuple) -> tuple:
    vals = [norm_value(v) for v in row]
    return tuple(sorted(vals, key=lambda x: (type(x).__name__, str(x))))


def result_key(result: ResultSet) -> frozenset:
    return frozenset(Counter(_row_key(r) for r in result.rows).items())


def same_result(a: ResultSet, b: ResultSet) -> bool:
    return result_key(a) == result_key(b)


def set_equal(a: ResultSet, b: ResultSet) -> bool:
    """BIRD's official execution-accuracy comparison: rows as a set, column order matters."""
    return {tuple(norm_value(v) for v in r) for r in a.rows} == \
           {tuple(norm_value(v) for v in r) for r in b.rows}


@dataclass
class Cluster:
    id: int
    members: list[int] = field(default_factory=list)  # candidate ids
    representative: int = -1
    size: int = 0

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def cluster_candidates(candidates) -> list[Cluster]:
    groups: dict[frozenset, list] = {}
    for c in candidates:
        if c.ok:
            groups.setdefault(result_key(c.result), []).append(c)
    ordered = sorted(groups.values(), key=lambda g: (-len(g), min(c.id for c in g)))
    clusters = []
    for i, members in enumerate(ordered):
        # Representative: the simplest SQL in the group (fewest tokens).
        rep = min(members, key=lambda c: (len(re.findall(r"\w+|[^\s\w]", c.sql)), c.id))
        for c in members:
            c.cluster = i
        clusters.append(Cluster(i, [c.id for c in members], rep.id, len(members)))
    return clusters
