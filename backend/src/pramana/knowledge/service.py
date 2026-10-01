"""Document use cases: register, list, read, relabel, delete, re-index."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from pathlib import PurePath
from typing import Any

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from pramana.auth.principal import Principal
from pramana.graph.builder import GraphBuilder
from pramana.knowledge.models import Chunk, Document, IngestionJob
from pramana.knowledge.parsers import detect_mime
from pramana.knowledge.storage import BlobStore
from pramana.privacy.scope import AccessScope
from pramana.retrieval.vector_store import VectorStore
from pramana.shared.crypto import sha256_hex
from pramana.shared.db import utcnow
from pramana.shared.errors import Conflict, NotFound, PermissionDenied, ValidationFailed
from pramana.shared.types import Permission, Sensitivity

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
REGIONS = ("global", "amer", "emea", "apac")


@dataclass(frozen=True, slots=True)
class NewDocument:
    data: bytes
    filename: str
    title: str
    department: str
    sensitivity: Sensitivity
    region: str = "global"
    source_type: str = "upload"
    collection: str = "uploads"
    effective_date: date | None = None
    version: str = "1"
    supersedes_id: uuid.UUID | None = None
    source_uri: str = ""
    mime_type: str | None = None
    meta: dict[str, Any] | None = None


class DocumentService:
    def __init__(self, db: AsyncSession, *, blobs: BlobStore, vectors: VectorStore, graph: GraphBuilder) -> None:
        self._db = db
        self._blobs = blobs
        self._vectors = vectors
        self._graph = graph

    # ── Create ──────────────────────────────────────────────────────────
    async def register(self, new: NewDocument, *, owner: Principal | None) -> tuple[Document, IngestionJob]:
        """Store the file, create the document row and queue it for ingestion."""
        if not new.data:
            raise ValidationFailed("The file is empty")
        if len(new.data) > MAX_UPLOAD_BYTES:
            raise ValidationFailed(f"The file is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
        if new.region not in REGIONS:
            raise ValidationFailed(f"Unknown region: {new.region}")
        mime = new.mime_type or detect_mime(new.filename)
        if owner is not None:
            self._check_may_file_under(owner, new.department)

        content_hash = sha256_hex(new.data)
        duplicate = (
            await self._db.execute(
                select(Document.id).where(
                    Document.content_hash == content_hash,
                    Document.department == new.department,
                    Document.status != "deleted",
                )
            )
        ).scalar_one_or_none()
        if duplicate is not None:
            raise Conflict("This file is already in the knowledge base", document_id=str(duplicate))

        document_id = uuid.uuid4()
        extension = PurePath(new.filename).suffix.lower()
        storage_key = f"documents/{document_id}/original{extension}"
        await self._blobs.put(storage_key, new.data)

        document = Document(
            id=document_id,
            title=new.title.strip()[:512] or PurePath(new.filename).stem,
            source_type=new.source_type,
            source_uri=new.source_uri[:1024],
            collection=new.collection,
            department=new.department,
            sensitivity_rank=int(new.sensitivity),
            region=new.region,
            owner_id=owner.user_id if owner else None,
            effective_date=new.effective_date,
            version=new.version,
            supersedes_id=new.supersedes_id,
            mime_type=mime,
            storage_key=storage_key,
            content_hash=content_hash,
            status="pending",
            meta={"filename": PurePath(new.filename).name, "size_bytes": len(new.data), **(new.meta or {})},
        )
        self._db.add(document)
        if new.supersedes_id is not None:
            await self._mark_superseded(new.supersedes_id)
        job = IngestionJob(document_id=document_id, kind="ingest")
        self._db.add(job)
        await self._db.flush()
        return document, job

    @staticmethod
    def _check_may_file_under(principal: Principal, department: str) -> None:
        if department in principal.departments or principal.has(Permission.USERS_MANAGE):
            return
        raise PermissionDenied("You can add documents only for your own department")

    async def _mark_superseded(self, document_id: uuid.UUID) -> None:
        older = await self._db.get(Document, document_id)
        if older is None:
            raise ValidationFailed("The superseded document does not exist")
        older.is_current = False
        await self._vectors.set_document_payload(older.id, {"is_current": False})
        await self._graph.update_document_labels(str(older.id), {"is_current": False})

    # ── Read ────────────────────────────────────────────────────────────
    async def list_documents(
        self,
        scope: AccessScope,
        *,
        principal: Principal,
        department: str | None = None,
        sensitivity_rank: int | None = None,
        status: str | None = None,
        collection: str | None = None,
        search: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Document], int]:
        visible = or_(scope.sql_predicate(), Document.owner_id == principal.user_id)
        conditions = [visible, Document.status != "deleted"]
        if department:
            conditions.append(Document.department == department)
        if sensitivity_rank is not None:
            conditions.append(Document.sensitivity_rank == sensitivity_rank)
        if status:
            conditions.append(Document.status == status)
        if collection:
            conditions.append(Document.collection == collection)
        if search:
            conditions.append(Document.title.ilike(f"%{search.strip()}%"))
        total = (await self._db.execute(select(func.count()).select_from(Document).where(*conditions))).scalar_one()
        rows = await self._db.execute(
            select(Document)
            .where(*conditions)
            .order_by(Document.updated_at.desc(), Document.id)
            .limit(min(limit, 200))
            .offset(offset)
        )
        return list(rows.scalars()), total

    async def get(self, document_id: uuid.UUID, scope: AccessScope, *, principal: Principal) -> Document:
        document = await self._db.get(Document, document_id)
        # "Not found" for anything outside the caller's scope: existence itself is not disclosed.
        if document is None or document.status == "deleted" or not self._can_read(document, scope, principal):
            raise NotFound("Document not found")
        return document

    @staticmethod
    def _can_read(document: Document, scope: AccessScope, principal: Principal) -> bool:
        return document.owner_id == principal.user_id or scope.permits(
            department=document.department, sensitivity_rank=document.sensitivity_rank, region=document.region
        )

    async def chunks(self, document_id: uuid.UUID, *, limit: int = 100, offset: int = 0) -> list[Chunk]:
        rows = await self._db.execute(
            select(Chunk)
            .where(Chunk.document_id == document_id)
            .order_by(Chunk.ordinal)
            .limit(min(limit, 500))
            .offset(offset)
        )
        return list(rows.scalars())

    async def facets(self, scope: AccessScope, *, principal: Principal) -> dict[str, dict[str, int]]:
        visible = or_(scope.sql_predicate(), Document.owner_id == principal.user_id)
        base = [visible, Document.status != "deleted"]
        out: dict[str, dict[str, int]] = {}
        for name, column in (
            ("department", Document.department),
            ("sensitivity_rank", Document.sensitivity_rank),
            ("status", Document.status),
            ("collection", Document.collection),
        ):
            rows = await self._db.execute(select(column, func.count()).where(*base).group_by(column))
            out[name] = {str(key): count for key, count in rows.all()}
        return out

    # ── Change ──────────────────────────────────────────────────────────
    async def relabel(
        self,
        document: Document,
        *,
        department: str | None = None,
        sensitivity: Sensitivity | None = None,
        region: str | None = None,
        title: str | None = None,
        effective_date: date | None = None,
    ) -> dict[str, Any]:
        """Change access labels everywhere at once: Postgres, the vector payload and the graph."""
        changes: dict[str, Any] = {}
        if department is not None and department != document.department:
            changes["department"] = department
            document.department = department
        if sensitivity is not None and int(sensitivity) != document.sensitivity_rank:
            changes["sensitivity_rank"] = int(sensitivity)
            document.sensitivity_rank = int(sensitivity)
        if region is not None and region != document.region:
            if region not in REGIONS:
                raise ValidationFailed(f"Unknown region: {region}")
            changes["region"] = region
            document.region = region
        if changes:
            await self._vectors.set_document_payload(document.id, changes)
            await self._graph.update_document_labels(str(document.id), changes)
        if title is not None and title.strip() and title.strip() != document.title:
            document.title = title.strip()[:512]
            changes["title"] = document.title
        if effective_date is not None:
            document.effective_date = effective_date
            changes["effective_date"] = effective_date.isoformat()
        await self._db.flush()
        return changes

    async def delete(self, document: Document) -> None:
        """Remove the document from every store. The row stays, marked deleted, for the audit trail."""
        await self._vectors.delete_document(document.id)
        await self._graph.delete_document(str(document.id))
        await self._blobs.delete_prefix(f"documents/{document.id}")
        await self._db.execute(delete(Chunk).where(Chunk.document_id == document.id))
        document.status = "deleted"
        document.deleted_at = utcnow()
        document.chunk_count = 0

    async def queue_reindex(self, document: Document) -> IngestionJob:
        job = IngestionJob(document_id=document.id, kind="ingest")
        document.status = "pending"
        self._db.add(job)
        await self._db.flush()
        return job

    async def jobs(self, document_id: uuid.UUID | None = None, *, limit: int = 50) -> list[IngestionJob]:
        query = select(IngestionJob).order_by(IngestionJob.created_at.desc()).limit(min(limit, 200))
        if document_id is not None:
            query = query.where(IngestionJob.document_id == document_id)
        return list((await self._db.execute(query)).scalars())
