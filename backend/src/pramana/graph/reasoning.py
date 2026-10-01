"""Graph reasoning: rank passages by Personalized PageRank over the query's subgraph.

The walk starts on the entities the question mentions and spreads through passages that
mention them and through typed relations. A passage scores highly when it is connected to
several query entities, directly or through related entities: the multi-hop evidence that
plain similarity search misses. (The approach follows HippoRAG's use of PageRank.)
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass

import networkx as nx

from pramana.graph.store import GraphFact, Subgraph

DAMPING = 0.5


@dataclass(frozen=True, slots=True)
class GraphRanking:
    chunks: list[tuple[uuid.UUID, float]]
    entity_scores: dict[str, float]
    facts: list[GraphFact]


def rank_passages(subgraph: Subgraph, *, limit: int = 30, max_facts: int = 12) -> GraphRanking:
    if not subgraph.seeds or not subgraph.mentions:
        return GraphRanking(chunks=[], entity_scores={}, facts=[])

    graph: nx.Graph[tuple[str, str]] = nx.Graph()
    for key, chunk_id in subgraph.mentions:
        graph.add_edge(("e", key), ("c", str(chunk_id)), weight=1.0)
    for fact in subgraph.facts:
        weight = 1.0 + fact.confidence
        if graph.has_edge(("e", fact.head_key), ("e", fact.tail_key)):
            graph[("e", fact.head_key)][("e", fact.tail_key)]["weight"] += weight
        else:
            graph.add_edge(("e", fact.head_key), ("e", fact.tail_key), weight=weight)

    # Start the walk on the seeds. Rare entities say more about the question than common ones.
    personalization: dict[tuple[str, str], float] = {}
    for seed in subgraph.seeds:
        node = ("e", seed.key)
        if node in graph:
            personalization[node] = seed.score / math.log(2.0 + seed.visible_chunks)
    if not personalization:
        return GraphRanking(chunks=[], entity_scores={}, facts=[])

    scores = nx.pagerank(graph, alpha=DAMPING, personalization=personalization, weight="weight", max_iter=60, tol=1e-5)
    chunks = sorted(
        ((uuid.UUID(name), score) for (kind, name), score in scores.items() if kind == "c"),
        key=lambda item: -item[1],
    )[:limit]
    entity_scores = {name: score for (kind, name), score in scores.items() if kind == "e"}
    facts = sorted(
        subgraph.facts,
        key=lambda f: -(entity_scores.get(f.head_key, 0.0) + entity_scores.get(f.tail_key, 0.0)) * (0.5 + f.confidence),
    )[:max_facts]
    return GraphRanking(chunks=chunks, entity_scores=entity_scores, facts=facts)
