"""Join-path completion over the foreign-key graph.

If retrieval picks `customers` and `products`, the SQL also needs `orders` and
`order_items` to connect them. We find the cheapest set of extra tables that
connects everything selected: an approximate Steiner tree on the FK graph.
"""
from __future__ import annotations

import networkx as nx

from ..schema import Schema


def fk_graph(schema: Schema) -> nx.Graph:
    g = nx.Graph()
    g.add_nodes_from(schema.tables)
    for fk in schema.foreign_keys:
        if g.has_edge(fk.table, fk.ref_table):
            g[fk.table][fk.ref_table]["keys"].append((fk.column, fk.ref_column))
        else:
            g.add_edge(fk.table, fk.ref_table, weight=1.0, keys=[(fk.column, fk.ref_column)])
    return g


def connect_tables(schema: Schema, selected: list[str]) -> tuple[list[str], list[str]]:
    """Return (all tables needed, the bridge tables that were added)."""
    g = fk_graph(schema)
    chosen = [t for t in selected if t in g]
    if len(chosen) < 2:
        return chosen, []
    needed: set[str] = set()
    # Steiner trees need terminals in one connected component; handle each separately.
    for comp in nx.connected_components(g):
        terms = [t for t in chosen if t in comp]
        if len(terms) < 2:
            needed.update(terms)
            continue
        sub = g.subgraph(comp)
        tree = nx.algorithms.approximation.steiner_tree(sub, terms, weight="weight")
        needed.update(tree.nodes or terms)
    ordered = [t for t in chosen] + sorted(needed - set(chosen))
    return ordered, sorted(needed - set(chosen))
