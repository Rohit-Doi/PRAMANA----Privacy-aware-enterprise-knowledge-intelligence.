"""Knowledge graph explorer. Every query is restricted to the caller's access scope."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select

from pramana.api.deps import DbSession, RuntimeDep, require
from pramana.auth.principal import Principal
from pramana.knowledge.models import Chunk, Document
from pramana.privacy.pii import mask_for_reader
from pramana.shared.errors import NotFound
from pramana.shared.types import Permission

router = APIRouter(prefix="/graph", tags=["Knowledge graph"])
GraphReader = Annotated[Principal, Depends(require(Permission.GRAPH_READ))]


@router.get("/search", summary="Find entities by name")
async def search(
    principal: GraphReader,
    runtime: RuntimeDep,
    q: Annotated[str, Query(min_length=2, max_length=100)],
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> list[dict[str, Any]]:
    scope = await runtime.policy.scope(principal)
    return await runtime.services.graph_store.search_entities(q, scope, limit=limit)


@router.get("/entity", summary="An entity with its relations, related entities and source passages")
async def entity(
    principal: GraphReader,
    runtime: RuntimeDep,
    db: DbSession,
    key: Annotated[str, Query(min_length=1, max_length=200)],
    limit: Annotated[int, Query(ge=1, le=80)] = 40,
) -> dict[str, Any]:
    scope = await runtime.policy.scope(principal)
    data = await runtime.services.graph_store.neighbourhood(key, scope, limit=limit)
    if not data:
        # Also the answer for an entity that exists only in passages outside the caller's scope.
        raise NotFound("Entity not found")
    chunk_ids = [uuid.UUID(c) for c in data["entity"].pop("chunk_ids", [])][:8]
    rows = await db.execute(
        select(Chunk, Document)
        .join(Document, Document.id == Chunk.document_id)
        .where(Chunk.id.in_(chunk_ids), scope.sql_predicate())
    )
    data["passages"] = [
        {
            "chunk_id": str(chunk.id),
            "document_id": str(document.id),
            "title": document.title,
            "section_path": chunk.section_path,
            "sensitivity_rank": document.sensitivity_rank,
            "department": document.department,
            "text": mask_for_reader(
                chunk.text[:700], sensitivity_rank=document.sensitivity_rank, can_read_pii=scope.can_read_pii
            ),
        }
        for chunk, document in rows.all()
    ]
    return data


@router.get("/stats", summary="Size of the knowledge graph")
async def stats(_: GraphReader, runtime: RuntimeDep) -> dict[str, int]:
    return await runtime.services.graph.counts()
