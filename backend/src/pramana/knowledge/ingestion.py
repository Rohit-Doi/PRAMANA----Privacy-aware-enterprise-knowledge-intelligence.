"""Ingestion pipeline: original file → parsed blocks → chunks → privacy scan → vectors → graph.

Each stage reports progress to the job row. A document becomes searchable ("ready") as soon
as its vectors are indexed; knowledge-graph extraction follows as the last stage.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import delete, select

from pramana.graph.builder import GraphBuilder
from pramana.knowledge.chunking import ChunkDraft, chunk_document, embedding_text
from pramana.knowledge.models import Chunk, Document
from pramana.knowledge.parsers import parse_document
from pramana.knowledge.storage import BlobStore
from pramana.privacy.pii import PiiEngine
from pramana.privacy.sensitivity import suggest_sensitivity
from pramana.retrieval.local_models import RetrievalModels
from pramana.retrieval.vector_store import ChunkPoint, VectorStore
from pramana.shared.db import session_scope
from pramana.shared.errors import NotFound, ValidationFailed
from pramana.shared.logging import get_logger

log = get_logger(__name__)

STAGES = ("parse", "chunk", "privacy_scan", "embed", "index", "graph")
ProgressCallback = Callable[[str, int, str], Awaitable[None]]
_CHUNK_NAMESPACE = uuid.UUID("6f1c2a54-8d0b-4b7e-9d53-0c7a1f2e9a10")


def chunk_id_for(document_id: uuid.UUID, ordinal: int) -> uuid.UUID:
    """Stable chunk ids: re-ingesting an unchanged document reproduces the same ids."""
    return uuid.uuid5(_CHUNK_NAMESPACE, f"{document_id}:{ordinal}")


@dataclass(slots=True)
class IngestResult:
    document_id: uuid.UUID
    chunk_count: int = 0
    token_count: int = 0
    entity_count: int = 0
    relation_count: int = 0
    pii_counts: dict[str, int] = field(default_factory=dict)
    timings_ms: dict[str, int] = field(default_factory=dict)


async def _no_progress(stage: str, percent: int, message: str) -> None:
    return None


class IngestionPipeline:
    def __init__(
        self,
        *,
        models: RetrievalModels,
        vector_store: VectorStore,
        blob_store: BlobStore,
        pii: PiiEngine,
        graph: GraphBuilder,
    ) -> None:
        self._models = models
        self._vectors = vector_store
        self._blobs = blob_store
        self._pii = pii
        self._graph = graph

    async def ingest(
        self,
        document_id: uuid.UUID,
        *,
        progress: ProgressCallback = _no_progress,
        build_graph: bool = True,
    ) -> IngestResult:
        result = IngestResult(document_id=document_id)
        async with session_scope() as db:
            document = await db.get(Document, document_id)
            if document is None or document.status == "deleted":
                raise NotFound("Document not found")
            document.status = "processing"
            document.error = None
            snapshot = _DocumentSnapshot.of(document)

        try:
            # ── parse ───────────────────────────────────────────────────
            started = time.perf_counter()
            await progress("parse", 5, "Reading the file")
            data = await self._blobs.get(snapshot.storage_key)
            parsed = await asyncio.to_thread(
                parse_document, data, filename=snapshot.filename, mime_type=snapshot.mime_type or None
            )
            if parsed.is_empty:
                raise ValidationFailed("No text could be extracted from this file")
            result.timings_ms["parse"] = _elapsed(started)

            # ── chunk ───────────────────────────────────────────────────
            started = time.perf_counter()
            await progress("chunk", 20, "Splitting into passages")
            canonical, drafts = await asyncio.to_thread(chunk_document, parsed)
            if not drafts:
                raise ValidationFailed("The document produced no passages")
            await self._blobs.put(f"documents/{document_id}/content.txt", canonical.encode("utf-8"))
            result.chunk_count = len(drafts)
            result.token_count = sum(d.token_count for d in drafts)
            result.timings_ms["chunk"] = _elapsed(started)

            # ── privacy scan ────────────────────────────────────────────
            started = time.perf_counter()
            await progress("privacy_scan", 35, f"Scanning {len(drafts)} passages for personal data")
            pii_per_chunk = await asyncio.to_thread(self._scan, drafts)
            totals: Counter[str] = Counter()
            for counts in pii_per_chunk:
                totals.update(counts)
            result.pii_counts = dict(totals)
            suggestion = suggest_sensitivity(canonical, result.pii_counts, token_count=result.token_count)
            result.timings_ms["privacy_scan"] = _elapsed(started)

            # ── embed ───────────────────────────────────────────────────
            started = time.perf_counter()
            await progress("embed", 55, "Computing embeddings")
            texts = [embedding_text(title=snapshot.title, section_path=d.section_path, text=d.text) for d in drafts]
            dense, sparse = await asyncio.gather(
                self._models.embed_documents(texts), self._models.sparse_documents(texts)
            )
            result.timings_ms["embed"] = _elapsed(started)

            # ── index ───────────────────────────────────────────────────
            started = time.perf_counter()
            await progress("index", 75, "Writing passages and vectors")
            chunk_ids = [chunk_id_for(document_id, d.ordinal) for d in drafts]
            await self._store_chunks(document_id, drafts, chunk_ids, pii_per_chunk)
            await self._vectors.delete_document(document_id)
            await self._vectors.upsert(
                [
                    ChunkPoint(chunk_id=cid, dense=dv, sparse=sv, payload=snapshot.payload(cid, d.ordinal))
                    for cid, d, dv, sv in zip(chunk_ids, drafts, dense, sparse, strict=True)
                ]
            )
            result.timings_ms["index"] = _elapsed(started)

            async with session_scope() as db:
                document = await db.get(Document, document_id)
                assert document is not None
                document.status = "ready"
                document.chunk_count = result.chunk_count
                document.token_count = result.token_count
                document.pii_summary = {"counts": result.pii_counts, "suggestion_reasons": list(suggestion.reasons)}
                document.suggested_sensitivity_rank = int(suggestion.rank)
                document.meta = {
                    **document.meta,
                    **{k: v for k, v in parsed.meta.items() if _jsonable(v)},
                    "graph_status": "pending" if build_graph else "skipped",
                }

            # ── graph ───────────────────────────────────────────────────
            if build_graph:
                started = time.perf_counter()
                await progress("graph", 85, "Extracting entities and relations")
                stats = await self._graph.index_document(
                    snapshot.graph_document(), [(cid, d.text) for cid, d in zip(chunk_ids, drafts, strict=True)]
                )
                result.entity_count, result.relation_count = stats.entities, stats.relations
                result.timings_ms["graph"] = _elapsed(started)
                async with session_scope() as db:
                    document = await db.get(Document, document_id)
                    assert document is not None
                    document.entity_count = stats.entities
                    document.meta = {**document.meta, "graph_status": "ready", "relation_count": stats.relations}
            await progress("done", 100, "Ready")
            return result
        except Exception as exc:
            async with session_scope() as db:
                document = await db.get(Document, document_id)
                if document is not None and document.status != "ready":
                    document.status = "failed"
                    document.error = str(exc)[:1000]
                elif document is not None:
                    # Vectors are indexed and searchable; only the graph stage failed.
                    document.meta = {**document.meta, "graph_status": "failed", "graph_error": str(exc)[:500]}
            raise

    def _scan(self, drafts: list[ChunkDraft]) -> list[dict[str, int]]:
        return [dict(Counter(f.entity_type for f in self._pii.analyze(d.text))) for d in drafts]

    async def _store_chunks(
        self,
        document_id: uuid.UUID,
        drafts: list[ChunkDraft],
        chunk_ids: list[uuid.UUID],
        pii_per_chunk: list[dict[str, int]],
    ) -> None:
        async with session_scope() as db:
            existing = {
                c.id: c for c in (await db.execute(select(Chunk).where(Chunk.document_id == document_id))).scalars()
            }
            # Free the (document, ordinal) slots first so re-ordered chunks cannot collide.
            stale = set(existing) - set(chunk_ids)
            if stale:
                await db.execute(delete(Chunk).where(Chunk.id.in_(stale)))
                await db.flush()
            for chunk_id, draft, pii in zip(chunk_ids, drafts, pii_per_chunk, strict=True):
                values: dict[str, Any] = {
                    "ordinal": draft.ordinal,
                    "text": draft.text,
                    "section_path": draft.section_path[:1024],
                    "page": draft.page,
                    "char_start": draft.char_start,
                    "char_end": draft.char_end,
                    "token_count": draft.token_count,
                    "pii_types": sorted(pii),
                }
                row = existing.get(chunk_id)
                if row is None:
                    db.add(Chunk(id=chunk_id, document_id=document_id, **values))
                else:
                    for key, value in values.items():
                        setattr(row, key, value)


@dataclass(frozen=True, slots=True)
class _DocumentSnapshot:
    """The document fields the pipeline needs, detached from any database session."""

    id: uuid.UUID
    title: str
    filename: str
    mime_type: str
    storage_key: str
    department: str
    sensitivity_rank: int
    region: str
    collection: str
    source_type: str
    is_current: bool

    @classmethod
    def of(cls, document: Document) -> _DocumentSnapshot:
        return cls(
            id=document.id,
            title=document.title,
            filename=str(document.meta.get("filename") or document.storage_key.rsplit("/", 1)[-1]),
            mime_type=document.mime_type,
            storage_key=document.storage_key,
            department=document.department,
            sensitivity_rank=document.sensitivity_rank,
            region=document.region,
            collection=document.collection,
            source_type=document.source_type,
            is_current=document.is_current,
        )

    def payload(self, chunk_id: uuid.UUID, ordinal: int) -> dict[str, Any]:
        return {
            "chunk_id": str(chunk_id),
            "document_id": str(self.id),
            "ordinal": ordinal,
            "department": self.department,
            "sensitivity_rank": self.sensitivity_rank,
            "region": self.region,
            "collection": self.collection,
            "source_type": self.source_type,
            "is_current": self.is_current,
        }

    def graph_document(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "title": self.title,
            "department": self.department,
            "sensitivity_rank": self.sensitivity_rank,
            "region": self.region,
            "collection": self.collection,
            "source_type": self.source_type,
            "is_current": self.is_current,
        }


def _elapsed(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _jsonable(value: object) -> bool:
    return isinstance(value, str | int | float | bool) or value is None
