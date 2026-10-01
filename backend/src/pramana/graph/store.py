"""Read side of the knowledge graph. Every query carries the caller's access scope."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from neo4j import AsyncDriver

from pramana.graph.entity_index import EntityIndex
from pramana.graph.extraction import GraphExtractor, entity_key
from pramana.privacy.scope import AccessScope

_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9\-]{2,}")
_LUCENE_SPECIAL = re.compile(r'([+\-&|!(){}\[\]^"~*?:\\/])')
_STOPWORDS = frozenset(
    ["the", "and", "for", "are", "but", "not", "you", "all", "any", "can", "had", "her", "was", "one", "our", "out", "has", "have", "what", "when", "where", "which", "who", "whom", "whose", "why", "how", "many", "much", "does", "did", "from", "into", "with", "about", "this", "that", "these", "those", "their", "there", "then", "than", "them", "they", "will", "would", "should", "could", "shall", "may", "might", "must", "been", "being", "were", "your", "its", "also", "such", "per", "between", "within", "without", "under", "over", "after", "before", "during", "against", "each", "other"]
)
MAX_SEEDS = 8


@dataclass(frozen=True, slots=True)
class LinkedEntity:
    key: str
    name: str
    type: str
    score: float
    visible_chunks: int
    matched_by: str


@dataclass(frozen=True, slots=True)
class GraphFact:
    head: str
    head_key: str
    relation: str
    tail: str
    tail_key: str
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    confidence: float


@dataclass(slots=True)
class Subgraph:
    seeds: list[LinkedEntity]
    mentions: list[tuple[str, uuid.UUID]] = field(default_factory=list)  # (entity key, chunk id)
    facts: list[GraphFact] = field(default_factory=list)
    names: dict[str, tuple[str, str]] = field(default_factory=dict)  # key -> (name, type)


def _query_terms(text: str) -> list[str]:
    return [w.lower() for w in _WORD.findall(text) if w.lower() not in _STOPWORDS]


class GraphStore:
    def __init__(self, driver: AsyncDriver, entity_index: EntityIndex, extractor: GraphExtractor) -> None:
        self._driver = driver
        self._entity_index = entity_index
        self._extractor = extractor

    # ── Entity linking ──────────────────────────────────────────────────
    async def link_entities(self, question: str, query_vector: list[float], scope: AccessScope) -> list[LinkedEntity]:
        """Graph entities the question refers to, restricted to those the caller may see."""
        candidates: dict[str, tuple[float, str]] = {}

        def offer(key: str, score: float, how: str) -> None:
            if key and (key not in candidates or score > candidates[key][0]):
                candidates[key] = (score, how)

        # 1. Phrases and names in the question that are entity keys verbatim.
        for entity in self._extractor.query_entities(question):
            offer(entity.key, 1.0, "exact")
        terms = _query_terms(question)
        for size in (3, 2, 1):
            for i in range(len(terms) - size + 1):
                offer(entity_key(" ".join(terms[i : i + size])), 0.95 if size > 1 else 0.6, "exact")

        # 2. Keyword match on entity names: most of the entity's words must be in the question.
        if terms:
            lucene = " ".join(_LUCENE_SPECIAL.sub(r"\\\1", t) for t in dict.fromkeys(terms))
            async with self._driver.session() as session:
                result = await session.run(
                    "CALL db.index.fulltext.queryNodes('entity_name', $q, {limit: 30}) YIELD node, score "
                    "RETURN node.key AS key, node.name AS name, score",
                    q=lucene,
                )
                rows = [r async for r in result]
            term_set = set(terms)
            top = max((r["score"] for r in rows), default=1.0) or 1.0
            for row in rows:
                name_terms = set(_query_terms(row["name"])) or {row["name"].lower()}
                coverage = len(name_terms & term_set) / len(name_terms)
                if coverage >= 0.6:
                    offer(row["key"], 0.5 + 0.4 * coverage * (row["score"] / top), "keyword")

        # 3. Meaning match on typed entity names.
        for match in await self._entity_index.search(query_vector, limit=8):
            offer(match.key, 0.85 * match.score, "semantic")

        if not candidates:
            return []
        # Keep only entities mentioned in passages this caller may read. An entity that exists
        # solely in restricted material is indistinguishable, from here, from one that does not exist.
        async with self._driver.session() as session:
            result = await session.run(
                f"""
                UNWIND $keys AS key
                MATCH (e:Entity {{key: key}})<-[:MENTIONS]-(c:Chunk)
                WHERE {scope.cypher_predicate("c")}
                RETURN e.key AS key, e.name AS name, e.type AS type, count(c) AS visible
                """,
                keys=list(candidates),
                **scope.cypher_params(),
            )
            visible = [r async for r in result]
        linked = [
            LinkedEntity(r["key"], r["name"], r["type"], candidates[r["key"]][0], r["visible"], candidates[r["key"]][1])
            for r in visible
        ]
        # A single common word matching a hub entity is weak evidence: drop hubs matched that way.
        linked = [e for e in linked if not (e.matched_by == "exact" and e.score <= 0.6 and e.visible_chunks > 150)]
        linked.sort(key=lambda e: (-e.score, e.visible_chunks))
        return linked[:MAX_SEEDS]

    # ── Subgraph around the seeds ───────────────────────────────────────
    async def subgraph(self, seeds: list[LinkedEntity], scope: AccessScope) -> Subgraph:
        graph = Subgraph(seeds=seeds, names={s.key: (s.name, s.type) for s in seeds})
        if not seeds:
            return graph
        seed_keys = [s.key for s in seeds]
        params = scope.cypher_params()
        async with self._driver.session() as session:
            # Hop 1: readable passages that mention a seed.
            result = await session.run(
                f"""
                MATCH (e:Entity)<-[:MENTIONS]-(c:Chunk)
                WHERE e.key IN $seeds AND {scope.cypher_predicate("c")}
                RETURN e.key AS key, c.id AS chunk_id
                LIMIT 500
                """,
                seeds=seed_keys,
                **params,
            )
            seed_mentions = [(r["key"], uuid.UUID(r["chunk_id"])) async for r in result]
            graph.mentions.extend(seed_mentions)
            chunk_ids = list({str(c) for _, c in seed_mentions})

            # Hop 2: entities that share those passages, the more specific the better.
            if chunk_ids:
                result = await session.run(
                    """
                    MATCH (c:Chunk)-[:MENTIONS]->(o:Entity)
                    WHERE c.id IN $chunk_ids AND NOT o.key IN $seeds
                    WITH o, count(c) AS shared
                    WHERE shared >= 2 OR o.type <> 'concept'
                    WITH o, shared, COUNT { (o)<-[:MENTIONS]-() } AS degree
                    ORDER BY toFloat(shared) / log(2.0 + degree) DESC
                    LIMIT 40
                    RETURN o.key AS key, o.name AS name, o.type AS type
                    """,
                    chunk_ids=chunk_ids,
                    seeds=seed_keys,
                )
                neighbours = [r async for r in result]
                for row in neighbours:
                    graph.names[row["key"]] = (row["name"], row["type"])
                if neighbours:
                    result = await session.run(
                        f"""
                        MATCH (o:Entity)<-[:MENTIONS]-(c:Chunk)
                        WHERE o.key IN $keys AND {scope.cypher_predicate("c")}
                        RETURN o.key AS key, c.id AS chunk_id
                        LIMIT 1500
                        """,
                        keys=[r["key"] for r in neighbours],
                        **params,
                    )
                    graph.mentions.extend([(r["key"], uuid.UUID(r["chunk_id"])) async for r in result])

            # Typed relations touching a seed, drawn from readable passages only.
            result = await session.run(
                f"""
                MATCH (h:Entity)-[r:REL]->(t:Entity)
                WHERE (h.key IN $seeds OR t.key IN $seeds) AND {scope.cypher_predicate("r")}
                RETURN h.key AS head_key, h.name AS head, h.type AS head_type, r.type AS relation,
                       t.key AS tail_key, t.name AS tail, t.type AS tail_type,
                       r.chunk_id AS chunk_id, r.document_id AS document_id, r.confidence AS confidence
                ORDER BY r.confidence DESC
                LIMIT 120
                """,
                seeds=seed_keys,
                **params,
            )
            async for row in result:
                graph.names.setdefault(row["head_key"], (row["head"], row["head_type"]))
                graph.names.setdefault(row["tail_key"], (row["tail"], row["tail_type"]))
                graph.facts.append(
                    GraphFact(
                        head=row["head"],
                        head_key=row["head_key"],
                        relation=row["relation"],
                        tail=row["tail"],
                        tail_key=row["tail_key"],
                        chunk_id=uuid.UUID(row["chunk_id"]),
                        document_id=uuid.UUID(row["document_id"]),
                        confidence=float(row["confidence"] or 0.0),
                    )
                )
        return graph

    # ── Explorer ────────────────────────────────────────────────────────
    async def search_entities(self, text: str, scope: AccessScope, *, limit: int = 20) -> list[dict[str, Any]]:
        terms = _query_terms(text) or [text.strip().lower()]
        lucene = " ".join(_LUCENE_SPECIAL.sub(r"\\\1", t) + "*" for t in terms if t)
        if not lucene:
            return []
        async with self._driver.session() as session:
            result = await session.run(
                f"""
                CALL db.index.fulltext.queryNodes('entity_name', $q, {{limit: 80}}) YIELD node, score
                MATCH (node)<-[:MENTIONS]-(c:Chunk)
                WHERE {scope.cypher_predicate("c")}
                WITH node, score, count(c) AS mentions
                RETURN node.key AS key, node.name AS name, node.type AS type, mentions
                ORDER BY score DESC, mentions DESC
                LIMIT $limit
                """,
                q=lucene,
                limit=limit,
                **scope.cypher_params(),
            )
            return [dict(r) async for r in result]

    async def neighbourhood(self, key: str, scope: AccessScope, *, limit: int = 40) -> dict[str, Any]:
        """One entity with its readable relations, co-mentioned entities and source passages."""
        params = scope.cypher_params()
        async with self._driver.session() as session:
            result = await session.run(
                f"""
                MATCH (e:Entity {{key: $key}})<-[:MENTIONS]-(c:Chunk)
                WHERE {scope.cypher_predicate("c")}
                RETURN e.key AS key, e.name AS name, e.type AS type,
                       collect(DISTINCT c.id)[..25] AS chunk_ids, count(c) AS mentions
                """,
                key=key,
                **params,
            )
            center = await result.single()
            if center is None:
                return {}
            result = await session.run(
                f"""
                MATCH (e:Entity {{key: $key}})-[r:REL]-(n:Entity)
                WHERE {scope.cypher_predicate("r")}
                RETURN startNode(r).key AS source, endNode(r).key AS target, r.type AS relation,
                       r.chunk_id AS chunk_id, r.confidence AS confidence,
                       n.key AS key, n.name AS name, n.type AS type
                LIMIT $limit
                """,
                key=key,
                limit=limit,
                **params,
            )
            relations = [dict(r) async for r in result]
            result = await session.run(
                f"""
                MATCH (e:Entity {{key: $key}})<-[:MENTIONS]-(c:Chunk)-[:MENTIONS]->(n:Entity)
                WHERE {scope.cypher_predicate("c")} AND n.key <> $key
                WITH n, count(DISTINCT c) AS shared
                ORDER BY shared DESC
                LIMIT $limit
                RETURN n.key AS key, n.name AS name, n.type AS type, shared
                """,
                key=key,
                limit=limit,
                **params,
            )
            related = [dict(r) async for r in result]
        return {"entity": dict(center), "relations": relations, "co_mentioned": related}
