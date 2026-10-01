"""Qdrant vector store: one collection, two named vectors per chunk (dense and BM25).

The payload holds only ids and the attributes used for filtering. Chunk text stays in
Postgres, so a relabel or a delete has a single source of truth.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from qdrant_client import AsyncQdrantClient
from qdrant_client import models as qm

from pramana.retrieval.local_models import SparseVector

DENSE = "dense"
SPARSE = "bm25"
_KEYWORD_FIELDS = ("department", "region", "document_id", "collection", "source_type")


@dataclass(frozen=True, slots=True)
class ChunkPoint:
    chunk_id: uuid.UUID
    dense: list[float]
    sparse: SparseVector
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class VectorHit:
    chunk_id: uuid.UUID
    score: float
    payload: dict[str, Any]


class VectorStore:
    def __init__(self, client: AsyncQdrantClient, collection: str, dim: int) -> None:
        self._client = client
        self._collection = collection
        self._dim = dim

    @property
    def collection(self) -> str:
        return self._collection

    async def ensure_collection(self) -> None:
        if not await self._client.collection_exists(self._collection):
            await self._client.create_collection(
                self._collection,
                vectors_config={DENSE: qm.VectorParams(size=self._dim, distance=qm.Distance.COSINE)},
                # IDF is applied by Qdrant at query time, so it stays correct as documents change.
                sparse_vectors_config={SPARSE: qm.SparseVectorParams(modifier=qm.Modifier.IDF)},
            )
        # Indexes on every field used by the access filter: filtered search stays fast and exact.
        for field in _KEYWORD_FIELDS:
            await self._client.create_payload_index(self._collection, field, qm.PayloadSchemaType.KEYWORD)
        await self._client.create_payload_index(self._collection, "sensitivity_rank", qm.PayloadSchemaType.INTEGER)
        await self._client.create_payload_index(self._collection, "is_current", qm.PayloadSchemaType.BOOL)

    async def drop_collection(self) -> None:
        if await self._client.collection_exists(self._collection):
            await self._client.delete_collection(self._collection)

    async def upsert(self, points: list[ChunkPoint]) -> None:
        for start in range(0, len(points), 128):
            batch = points[start : start + 128]
            await self._client.upsert(
                self._collection,
                points=[
                    qm.PointStruct(
                        id=str(p.chunk_id),
                        vector={
                            DENSE: p.dense,
                            SPARSE: qm.SparseVector(indices=p.sparse.indices, values=p.sparse.values),
                        },
                        payload=p.payload,
                    )
                    for p in batch
                ],
                wait=True,
            )

    async def delete_document(self, document_id: uuid.UUID) -> None:
        await self._client.delete(
            self._collection, points_selector=qm.FilterSelector(filter=_document_filter(document_id)), wait=True
        )

    async def set_document_payload(self, document_id: uuid.UUID, payload: dict[str, Any]) -> None:
        """Propagate a relabel (department, sensitivity, region, currency) to every chunk."""
        await self._client.set_payload(
            self._collection, payload=payload, points=_document_filter(document_id), wait=True
        )

    async def search_dense(self, vector: list[float], *, query_filter: qm.Filter, limit: int) -> list[VectorHit]:
        result = await self._client.query_points(
            self._collection,
            query=vector,
            using=DENSE,
            query_filter=query_filter,
            limit=limit,
            with_payload=True,
        )
        return [_hit(p) for p in result.points]

    async def search_sparse(self, vector: SparseVector, *, query_filter: qm.Filter, limit: int) -> list[VectorHit]:
        if not vector.indices:
            return []
        result = await self._client.query_points(
            self._collection,
            query=qm.SparseVector(indices=vector.indices, values=vector.values),
            using=SPARSE,
            query_filter=query_filter,
            limit=limit,
            with_payload=True,
        )
        return [_hit(p) for p in result.points]

    async def count(self, query_filter: qm.Filter | None = None) -> int:
        result = await self._client.count(self._collection, count_filter=query_filter, exact=True)
        return result.count

    async def retrieve(self, chunk_ids: list[uuid.UUID], *, query_filter: qm.Filter) -> list[VectorHit]:
        """Fetch specific chunks, still subject to the access filter."""
        if not chunk_ids:
            return []
        combined = qm.Filter(must=[query_filter, qm.HasIdCondition(has_id=[str(c) for c in chunk_ids])])
        points, _ = await self._client.scroll(
            self._collection, scroll_filter=combined, limit=len(chunk_ids), with_payload=True
        )
        return [VectorHit(uuid.UUID(str(p.id)), 0.0, dict(p.payload or {})) for p in points]


def _document_filter(document_id: uuid.UUID) -> qm.Filter:
    return qm.Filter(must=[qm.FieldCondition(key="document_id", match=qm.MatchValue(value=str(document_id)))])


def _hit(point: qm.ScoredPoint) -> VectorHit:
    return VectorHit(uuid.UUID(str(point.id)), float(point.score), dict(point.payload or {}))
