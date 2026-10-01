"""Hybrid GraphRAG retrieval.

question
  ├─ dense vector search   ┐
  ├─ BM25 keyword search   ├─ each restricted to the caller's access scope
  └─ graph (PageRank)      ┘
        ↓ weighted Reciprocal Rank Fusion (all three rank the same thing: passages)
        ↓ policy re-check on every candidate (defence in depth)
        ↓ cross-encoder rerank
        ↓ context expansion (adjacent passages of the best hits)
      evidence pack [E1] … [En]  +  graph facts
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from qdrant_client import models as qm
from sqlalchemy import select

from pramana.auth.principal import Principal
from pramana.graph.reasoning import GraphRanking, rank_passages
from pramana.graph.store import GraphFact, GraphStore, LinkedEntity
from pramana.knowledge.chunking import embedding_text
from pramana.knowledge.models import Chunk, Document
from pramana.privacy.policy import PolicyEngine, ResourceRef
from pramana.privacy.scope import AccessScope
from pramana.retrieval.local_models import RetrievalModels
from pramana.retrieval.vector_store import VectorHit, VectorStore
from pramana.shared.config import get_settings
from pramana.shared.db import session_scope
from pramana.shared.logging import get_logger
from pramana.shared.text import estimate_tokens

log = get_logger(__name__)

RETRIEVER_WEIGHTS = {"semantic": 1.0, "keyword": 1.0, "graph": 0.7}
STALE_PENALTY = 1.5  # subtracted from the rerank score of a superseded document
CONTEXT_TOKEN_BUDGET = 2600
# A passage counts as "withheld" when it is close in meaning to the question but outside scope.
WITHHELD_MIN_SIMILARITY = 0.66
WITHHELD_MARGIN = 0.08
WITHHELD_SCAN = 25


@dataclass(slots=True)
class Evidence:
    ref: str
    chunk_ids: list[uuid.UUID]
    document_id: uuid.UUID
    title: str
    section_path: str
    page: int | None
    text: str
    char_start: int
    char_end: int
    department: str
    sensitivity_rank: int
    region: str
    source_type: str
    collection: str
    version: str
    effective_date: str | None
    is_current: bool
    rerank_score: float
    fused_score: float
    found_by: list[str] = field(default_factory=list)
    ranks: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ref": self.ref,
            "chunk_ids": [str(c) for c in self.chunk_ids],
            "document_id": str(self.document_id),
            "title": self.title,
            "section_path": self.section_path,
            "page": self.page,
            "text": self.text,
            "char_start": self.char_start,
            "char_end": self.char_end,
            "department": self.department,
            "sensitivity_rank": self.sensitivity_rank,
            "region": self.region,
            "source_type": self.source_type,
            "collection": self.collection,
            "version": self.version,
            "effective_date": self.effective_date,
            "is_current": self.is_current,
            "rerank_score": round(self.rerank_score, 4),
            "fused_score": round(self.fused_score, 6),
            "found_by": self.found_by,
            "ranks": self.ranks,
        }


@dataclass(slots=True)
class RetrievalResult:
    evidence: list[Evidence]
    graph_facts: list[GraphFact]
    linked_entities: list[LinkedEntity]
    withheld_count: int
    withheld_max_rank: int | None
    candidates_considered: int
    timings_ms: dict[str, int]
    entity_scores: dict[str, float] = field(default_factory=dict)


@dataclass(slots=True)
class VectorCandidates:
    """Output of the passage search: ranked passage ids from the two vector retrievers."""

    semantic: list[uuid.UUID] = field(default_factory=list)
    keyword: list[uuid.UUID] = field(default_factory=list)
    withheld_count: int = 0
    withheld_max_rank: int | None = None
    timings_ms: dict[str, int] = field(default_factory=dict)


@dataclass(slots=True)
class GraphCandidates:
    """Output of the graph search: passages ranked by PageRank, plus the facts and entities used."""

    chunks: list[uuid.UUID] = field(default_factory=list)
    facts: list[GraphFact] = field(default_factory=list)
    linked_entities: list[LinkedEntity] = field(default_factory=list)
    entity_scores: dict[str, float] = field(default_factory=dict)
    timings_ms: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RetrievalOptions:
    use_semantic: bool = True
    use_keyword: bool = True
    use_graph: bool = True
    use_rerank: bool = True
    expand_context: bool = True
    top_k: int | None = None
    candidates: int | None = None


def reciprocal_rank_fusion(
    rankings: dict[str, list[uuid.UUID]], *, k: int, weights: dict[str, float]
) -> list[tuple[uuid.UUID, float]]:
    """score(d) = Σ_r  w_r / (k + rank_r(d)).  Rank-based, so no score calibration is needed."""
    scores: dict[uuid.UUID, float] = {}
    for name, ranking in rankings.items():
        weight = weights.get(name, 1.0)
        for rank, item in enumerate(ranking, start=1):
            scores[item] = scores.get(item, 0.0) + weight / (k + rank)
    return sorted(scores.items(), key=lambda pair: -pair[1])


class HybridRetriever:
    def __init__(
        self, *, models: RetrievalModels, vectors: VectorStore, graph: GraphStore, policy: PolicyEngine
    ) -> None:
        self._models = models
        self._vectors = vectors
        self._graph = graph
        self._policy = policy
        self._settings = get_settings()

    async def retrieve(
        self,
        question: str,
        *,
        principal: Principal,
        scope: AccessScope,
        options: RetrievalOptions | None = None,
    ) -> RetrievalResult:
        """All stages in one call. The agent workflow runs the same stages as separate steps."""
        options = options or RetrievalOptions()
        vector_task = self.search_passages(question, scope, options=options)
        graph_task = self.search_graph(question, scope, options=options)
        vector_candidates, graph_candidates = await asyncio.gather(vector_task, graph_task)
        return await self.build_evidence(
            question, principal=principal, vectors=vector_candidates, graph=graph_candidates, options=options
        )

    # ── Stage 1a: passage search (Retrieval Agent) ──────────────────────
    async def search_passages(
        self, question: str, scope: AccessScope, *, options: RetrievalOptions | None = None
    ) -> VectorCandidates:
        options = options or RetrievalOptions()
        limit = options.candidates or self._settings.retrieval_candidates
        started = time.perf_counter()
        dense_vector, sparse_vector = await asyncio.gather(
            self._models.embed_query(question), self._models.sparse_query(question)
        )
        embed_ms = _ms(started)

        scope_filter = scope.qdrant_filter()
        started = time.perf_counter()

        async def none() -> list[VectorHit]:
            return []

        semantic_hits, keyword_hits, (withheld, withheld_rank) = await asyncio.gather(
            self._vectors.search_dense(dense_vector, query_filter=scope_filter, limit=limit)
            if options.use_semantic
            else none(),
            self._vectors.search_sparse(sparse_vector, query_filter=scope_filter, limit=limit)
            if options.use_keyword
            else none(),
            self._withheld(dense_vector, scope),
        )
        return VectorCandidates(
            semantic=[h.chunk_id for h in semantic_hits],
            keyword=[h.chunk_id for h in keyword_hits],
            withheld_count=withheld,
            withheld_max_rank=withheld_rank,
            timings_ms={"embed": embed_ms, "search": _ms(started)},
        )

    # ── Stage 1b: graph search (Graph Reasoning Agent) ──────────────────
    async def search_graph(
        self, question: str, scope: AccessScope, *, options: RetrievalOptions | None = None
    ) -> GraphCandidates:
        options = options or RetrievalOptions()
        if not options.use_graph:
            return GraphCandidates()
        started = time.perf_counter()
        limit = options.candidates or self._settings.retrieval_candidates
        vector = await self._models.embed_query(question)
        ranking, linked = await self._graph_ranking(question, vector, scope, limit)
        return GraphCandidates(
            chunks=[chunk_id for chunk_id, _ in ranking.chunks] if ranking else [],
            facts=ranking.facts if ranking else [],
            linked_entities=linked,
            entity_scores=ranking.entity_scores if ranking else {},
            timings_ms={"graph": _ms(started)},
        )

    # ── Stage 2: fuse, re-check policy, rerank, expand ──────────────────
    async def build_evidence(
        self,
        question: str,
        *,
        principal: Principal,
        vectors: VectorCandidates,
        graph: GraphCandidates,
        options: RetrievalOptions | None = None,
    ) -> RetrievalResult:
        options = options or RetrievalOptions()
        settings = self._settings
        candidates = options.candidates or settings.retrieval_candidates
        top_k = options.top_k or settings.retrieval_top_k
        timings: dict[str, int] = {**vectors.timings_ms, **graph.timings_ms}

        rankings: dict[str, list[uuid.UUID]] = {
            "semantic": vectors.semantic,
            "keyword": vectors.keyword,
            "graph": graph.chunks,
        }
        fused = reciprocal_rank_fusion(rankings, k=settings.rrf_k, weights=RETRIEVER_WEIGHTS)
        # The cross-encoder is the expensive step: it reads only the best fused candidates.
        fused = fused[: min(candidates, settings.rerank_candidates) if options.use_rerank else candidates]

        def result(evidence: list[Evidence], considered: int) -> RetrievalResult:
            return RetrievalResult(
                evidence=evidence,
                graph_facts=graph.facts,
                linked_entities=graph.linked_entities,
                withheld_count=vectors.withheld_count,
                withheld_max_rank=vectors.withheld_max_rank,
                candidates_considered=considered,
                timings_ms=timings,
                entity_scores=graph.entity_scores,
            )

        if not fused:
            return result([], 0)

        # Load passages; re-check each one against the policy engine (defence in depth).
        started = time.perf_counter()
        rows = await self._load_chunks([chunk_id for chunk_id, _ in fused])
        allowed = await self._policy.allowed_ids(
            principal,
            [
                ResourceRef(
                    id=str(chunk.id),
                    department=doc.department,
                    sensitivity_rank=doc.sensitivity_rank,
                    region=doc.region,
                )
                for chunk, doc in rows.values()
            ],
        )
        dropped = [cid for cid in rows if str(cid) not in allowed]
        if dropped:
            # Should be impossible: the stores already filtered. Log loudly, then fail safe.
            log.error("retrieval.policy_recheck_dropped", count=len(dropped), user_id=str(principal.user_id))
        fused = [(cid, score) for cid, score in fused if cid in rows and str(cid) in allowed]
        timings["load"] = _ms(started)
        if not fused:
            return result([], 0)

        started = time.perf_counter()
        passages = [
            embedding_text(
                title=rows[cid][1].title, section_path=rows[cid][0].section_path, text=rows[cid][0].text
            )[: settings.rerank_max_chars]
            for cid, _ in fused
        ]
        if options.use_rerank:
            rerank_scores = await self._models.rerank(question, passages)
        else:
            rerank_scores = [score * 1000 for _, score in fused]  # keep fusion order
        timings["rerank"] = _ms(started)

        scored: list[tuple[uuid.UUID, float, float]] = []
        for (cid, fused_score), rerank_score in zip(fused, rerank_scores, strict=True):
            adjusted = rerank_score - (0.0 if rows[cid][1].is_current else STALE_PENALTY)
            scored.append((cid, adjusted, fused_score))
        scored.sort(key=lambda item: -item[1])

        started = time.perf_counter()
        evidence = await self._assemble(scored[:top_k], rows, rankings, expand=options.expand_context)
        timings["assemble"] = _ms(started)
        return result(evidence, len(fused))

    async def _graph_ranking(
        self, question: str, vector: list[float], scope: AccessScope, limit: int
    ) -> tuple[GraphRanking | None, list[LinkedEntity]]:
        linked = await self._graph.link_entities(question, vector, scope)
        if not linked:
            return None, []
        subgraph = await self._graph.subgraph(linked, scope)
        ranking = await asyncio.to_thread(rank_passages, subgraph, limit=limit)
        return ranking, linked

    async def _withheld(self, vector: list[float], scope: AccessScope) -> tuple[int, int | None]:
        """How many passages relevant to the question the policy kept from this caller.

        Only vector scores and access labels are read here. The text of a withheld passage
        is never loaded, and nothing from it can reach the answer.
        """
        hits = await self._vectors.search_dense(vector, query_filter=qm.Filter(), limit=WITHHELD_SCAN)
        if not hits:
            return 0, None
        # Close to the question in absolute terms, and close to the best match overall.
        threshold = max(WITHHELD_MIN_SIMILARITY, hits[0].score - WITHHELD_MARGIN)
        outside = [
            h
            for h in hits
            if h.score >= threshold
            and not scope.permits(
                department=str(h.payload.get("department", "")),
                sensitivity_rank=int(h.payload.get("sensitivity_rank", 3)),
                region=str(h.payload.get("region", "")),
            )
        ]
        if not outside:
            return 0, None
        return len(outside), max(int(h.payload.get("sensitivity_rank", 3)) for h in outside)

    async def _load_chunks(self, chunk_ids: list[uuid.UUID]) -> dict[uuid.UUID, tuple[Chunk, Document]]:
        async with session_scope() as db:
            result = await db.execute(
                select(Chunk, Document)
                .join(Document, Document.id == Chunk.document_id)
                .where(Chunk.id.in_(chunk_ids), Document.status == "ready")
            )
            return {chunk.id: (chunk, document) for chunk, document in result.all()}

    async def _assemble(
        self,
        top: list[tuple[uuid.UUID, float, float]],
        rows: dict[uuid.UUID, tuple[Chunk, Document]],
        rankings: dict[str, list[uuid.UUID]],
        *,
        expand: bool,
    ) -> list[Evidence]:
        rank_of = {name: {cid: i for i, cid in enumerate(ids, start=1)} for name, ids in rankings.items()}

        # Adjacent passages of the same document are merged into one piece of evidence.
        groups: list[dict[str, Any]] = []
        for cid, rerank_score, fused_score in top:
            chunk, document = rows[cid]
            placed = False
            for group in groups:
                if group["document"].id == document.id and any(
                    abs(chunk.ordinal - c.ordinal) == 1 for c in group["chunks"]
                ):
                    group["chunks"].append(chunk)
                    placed = True
                    break
            if not placed:
                groups.append({"document": document, "chunks": [chunk], "rerank": rerank_score, "fused": fused_score})

        # Expand the strongest hits with their neighbours while the context budget allows.
        if expand:
            used = sum(c.token_count for g in groups for c in g["chunks"])
            for group in groups[:3]:
                ordinals = {c.ordinal for c in group["chunks"]}
                wanted = {min(ordinals) - 1, max(ordinals) + 1} - ordinals
                neighbours = await self._neighbours(group["document"].id, wanted)
                for neighbour in neighbours:
                    if used + neighbour.token_count > CONTEXT_TOKEN_BUDGET:
                        break
                    group["chunks"].append(neighbour)
                    used += neighbour.token_count

        evidence: list[Evidence] = []
        for index, group in enumerate(groups, start=1):
            chunks: list[Chunk] = sorted(group["chunks"], key=lambda c: c.ordinal)
            document: Document = group["document"]
            text = _merge_text(chunks)
            found_by = [name for name in rankings if any(c.id in rank_of[name] for c in chunks)]
            ranks = {name: min(rank_of[name][c.id] for c in chunks if c.id in rank_of[name]) for name in found_by}
            evidence.append(
                Evidence(
                    ref=f"E{index}",
                    chunk_ids=[c.id for c in chunks],
                    document_id=document.id,
                    title=document.title,
                    section_path=chunks[0].section_path,
                    page=chunks[0].page,
                    text=text,
                    char_start=chunks[0].char_start,
                    char_end=chunks[-1].char_end,
                    department=document.department,
                    sensitivity_rank=document.sensitivity_rank,
                    region=document.region,
                    source_type=document.source_type,
                    collection=document.collection,
                    version=document.version,
                    effective_date=document.effective_date.isoformat() if document.effective_date else None,
                    is_current=document.is_current,
                    rerank_score=group["rerank"],
                    fused_score=group["fused"],
                    found_by=found_by,
                    ranks=ranks,
                )
            )
        return evidence

    async def _neighbours(self, document_id: uuid.UUID, ordinals: set[int]) -> list[Chunk]:
        wanted = [o for o in ordinals if o >= 0]
        if not wanted:
            return []
        async with session_scope() as db:
            result = await db.execute(
                select(Chunk).where(Chunk.document_id == document_id, Chunk.ordinal.in_(wanted)).order_by(Chunk.ordinal)
            )
            return list(result.scalars())


def _merge_text(chunks: list[Chunk]) -> str:
    """Join consecutive passages, dropping the sentence each one repeats from its predecessor."""
    text = chunks[0].text
    previous = chunks[0]
    for chunk in chunks[1:]:
        if chunk.ordinal == previous.ordinal + 1:
            overlap = max(0, previous.char_end - chunk.char_start)
            text = text + ("" if overlap else "\n\n") + chunk.text[overlap:]
        else:
            text = text + "\n\n[…]\n\n" + chunk.text
        previous = chunk
    return text


def _ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def context_tokens(evidence: list[Evidence]) -> int:
    return sum(estimate_tokens(e.text) for e in evidence)
