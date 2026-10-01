"""Local retrieval models (ONNX, CPU): dense embeddings, BM25 sparse vectors, cross-encoder reranker.

Models load lazily, once per process, and run in worker threads so the event loop stays free.
Nothing here calls a network service after the first download.
"""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass
from typing import Any

from pramana.shared.config import get_settings
from pramana.shared.logging import get_logger

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class SparseVector:
    indices: list[int]
    values: list[float]


class RetrievalModels:
    def __init__(self) -> None:
        self._settings = get_settings()
        self._lock = threading.Lock()
        self._dense: Any = None
        self._sparse: Any = None
        self._reranker: Any = None

    # ── Lazy loaders ────────────────────────────────────────────────────
    def _dense_model(self) -> Any:
        if self._dense is None:
            with self._lock:
                if self._dense is None:
                    from fastembed import TextEmbedding

                    log.info("model.load", kind="dense", name=self._settings.embedding_model)
                    self._dense = TextEmbedding(self._settings.embedding_model)
        return self._dense

    def _sparse_model(self) -> Any:
        if self._sparse is None:
            with self._lock:
                if self._sparse is None:
                    from fastembed import SparseTextEmbedding

                    name = self._settings.sparse_model
                    log.info("model.load", kind="sparse", name=name)
                    # Load straight from the cached snapshot when it exists: the BM25 "model" is a set
                    # of stop-word lists, and this avoids a hub lookup that fails when offline.
                    snapshots = (
                        self._settings.cache_dir / "fastembed" / f"models--{name.replace('/', '--')}" / "snapshots"
                    )
                    cached = sorted(p for p in snapshots.glob("*") if p.is_dir()) if snapshots.is_dir() else []
                    if cached:
                        self._sparse = SparseTextEmbedding(name, specific_model_path=str(cached[-1]))
                    else:
                        self._sparse = SparseTextEmbedding(name)
        return self._sparse

    def _rerank_model(self) -> Any:
        if self._reranker is None:
            with self._lock:
                if self._reranker is None:
                    from fastembed.rerank.cross_encoder import TextCrossEncoder

                    log.info("model.load", kind="reranker", name=self._settings.reranker_model)
                    self._reranker = TextCrossEncoder(self._settings.reranker_model)
        return self._reranker

    # ── Synchronous API (call from threads or scripts) ──────────────────
    def embed_documents_sync(self, texts: list[str]) -> list[list[float]]:
        return [v.tolist() for v in self._dense_model().embed(texts, batch_size=32)]

    def embed_query_sync(self, text: str) -> list[float]:
        return next(iter(self._dense_model().query_embed(text))).tolist()  # type: ignore[no-any-return]

    def sparse_documents_sync(self, texts: list[str]) -> list[SparseVector]:
        return [
            SparseVector(v.indices.tolist(), v.values.tolist())
            for v in self._sparse_model().embed(texts, batch_size=64)
        ]

    def sparse_query_sync(self, text: str) -> SparseVector:
        vector = next(iter(self._sparse_model().query_embed(text)))
        return SparseVector(vector.indices.tolist(), vector.values.tolist())

    def rerank_sync(self, query: str, passages: list[str]) -> list[float]:
        if not passages:
            return []
        return [float(s) for s in self._rerank_model().rerank(query, passages, batch_size=16)]

    # ── Async API ───────────────────────────────────────────────────────
    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return await asyncio.to_thread(self.embed_documents_sync, texts)

    async def embed_query(self, text: str) -> list[float]:
        return await asyncio.to_thread(self.embed_query_sync, text)

    async def sparse_documents(self, texts: list[str]) -> list[SparseVector]:
        return await asyncio.to_thread(self.sparse_documents_sync, texts)

    async def sparse_query(self, text: str) -> SparseVector:
        return await asyncio.to_thread(self.sparse_query_sync, text)

    async def rerank(self, query: str, passages: list[str]) -> list[float]:
        return await asyncio.to_thread(self.rerank_sync, query, passages)

    async def warm_up(self) -> None:
        """Load every model now, so the first user request does not pay for it."""
        await asyncio.gather(self.embed_query("warm up"), self.sparse_query("warm up"))
        await self.rerank("warm up", ["warm up"])


_models: RetrievalModels | None = None


def get_retrieval_models() -> RetrievalModels:
    global _models
    if _models is None:
        _models = RetrievalModels()
    return _models
