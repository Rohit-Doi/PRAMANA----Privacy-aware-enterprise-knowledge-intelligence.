"""Vector index over entity names, for linking a question to graph entities by meaning
("MFA" ↔ "multi-factor authentication") when the wording differs."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from qdrant_client import AsyncQdrantClient
from qdrant_client import models as qm

from pramana.retrieval.local_models import RetrievalModels

_NAMESPACE = uuid.UUID("0b0f6a55-4a0e-4f8b-9a43-6a3b1c0d7e21")


def _point_id(key: str) -> str:
    return str(uuid.uuid5(_NAMESPACE, key))


@dataclass(frozen=True, slots=True)
class EntityMatch:
    key: str
    name: str
    type: str
    score: float


class EntityIndex:
    def __init__(self, client: AsyncQdrantClient, collection: str, dim: int, models: RetrievalModels) -> None:
        self._client = client
        self._collection = collection
        self._dim = dim
        self._models = models

    async def ensure_collection(self) -> None:
        if not await self._client.collection_exists(self._collection):
            await self._client.create_collection(
                self._collection, vectors_config=qm.VectorParams(size=self._dim, distance=qm.Distance.COSINE)
            )

    async def drop_collection(self) -> None:
        if await self._client.collection_exists(self._collection):
            await self._client.delete_collection(self._collection)

    async def upsert(self, entities: list[dict[str, Any]]) -> None:
        if not entities:
            return
        vectors = await self._models.embed_documents([str(e["name"]) for e in entities])
        await self._client.upsert(
            self._collection,
            points=[
                qm.PointStruct(
                    id=_point_id(str(e["key"])), vector=v,
                    payload={"key": e["key"], "name": e["name"], "type": e["type"]},
                )
                for e, v in zip(entities, vectors, strict=True)
            ],
            wait=True,
        )

    async def delete(self, keys: list[str]) -> None:
        if keys:
            await self._client.delete(
                self._collection, points_selector=qm.PointIdsList(points=[_point_id(k) for k in keys]), wait=True
            )

    async def search(self, vector: list[float], *, limit: int = 8, min_score: float = 0.72) -> list[EntityMatch]:
        result = await self._client.query_points(
            self._collection, query=vector, limit=limit, score_threshold=min_score, with_payload=True
        )
        return [
            EntityMatch(str(p.payload["key"]), str(p.payload["name"]), str(p.payload["type"]), float(p.score))
            for p in result.points
            if p.payload
        ]
