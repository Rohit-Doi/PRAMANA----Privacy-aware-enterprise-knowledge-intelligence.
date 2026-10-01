"""Knowledge sources: upload, browse, relabel, re-index and delete documents; follow ingestion."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from pramana.api.deps import DbSession, RuntimeDep, require
from pramana.auth.principal import Principal
from pramana.governance.audit import AuditTrail
from pramana.knowledge.models import Chunk, Document, IngestionJob
from pramana.knowledge.parsers import SUPPORTED_EXTENSIONS
from pramana.knowledge.service import MAX_UPLOAD_BYTES, DocumentService, NewDocument
from pramana.privacy.pii import mask_for_reader
from pramana.runtime import Runtime
from pramana.shared.errors import NotFound, PermissionDenied, ValidationFailed
from pramana.shared.types import Permission, Sensitivity

router = APIRouter(tags=["Knowledge sources"])
Reader = Annotated[Principal, Depends(require(Permission.DOCUMENTS_READ))]
Writer = Annotated[Principal, Depends(require(Permission.DOCUMENTS_WRITE))]


class DocumentView(BaseModel):
    id: uuid.UUID
    title: str
    source_type: str
    source_uri: str
    collection: str
    department: str
    sensitivity: str
    sensitivity_rank: int
    suggested_sensitivity: str | None
    suggestion_reasons: list[str]
    region: str
    version: str
    effective_date: date | None
    is_current: bool
    supersedes_id: uuid.UUID | None
    status: str
    error: str | None
    chunk_count: int
    token_count: int
    entity_count: int
    pii_counts: dict[str, int]
    graph_status: str | None
    filename: str | None
    size_bytes: int | None
    owned_by_me: bool
    created_at: datetime
    updated_at: datetime


class DocumentPage(BaseModel):
    items: list[DocumentView]
    total: int
    limit: int
    offset: int


class ChunkView(BaseModel):
    id: uuid.UUID
    ordinal: int
    section_path: str
    page: int | None
    text: str
    token_count: int
    pii_types: list[str]


class DocumentUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=512)
    department: str | None = None
    sensitivity: str | None = None
    region: str | None = None
    effective_date: date | None = None


class JobView(BaseModel):
    id: uuid.UUID
    document_id: uuid.UUID
    document_title: str
    kind: str
    status: str
    stage: str
    progress: int
    attempts: int
    error: str | None
    events: list[dict[str, Any]]
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


def _view(document: Document, principal: Principal) -> DocumentView:
    summary = document.pii_summary or {}
    suggested = document.suggested_sensitivity_rank
    return DocumentView(
        id=document.id,
        title=document.title,
        source_type=document.source_type,
        source_uri=document.source_uri,
        collection=document.collection,
        department=document.department,
        sensitivity=Sensitivity(document.sensitivity_rank).name,
        sensitivity_rank=document.sensitivity_rank,
        suggested_sensitivity=Sensitivity(suggested).name if suggested is not None else None,
        suggestion_reasons=list(summary.get("suggestion_reasons", [])),
        region=document.region,
        version=document.version,
        effective_date=document.effective_date,
        is_current=document.is_current,
        supersedes_id=document.supersedes_id,
        status=document.status,
        error=document.error,
        chunk_count=document.chunk_count,
        token_count=document.token_count,
        entity_count=document.entity_count,
        pii_counts=dict(summary.get("counts", {})),
        graph_status=document.meta.get("graph_status"),
        filename=document.meta.get("filename"),
        size_bytes=document.meta.get("size_bytes"),
        owned_by_me=document.owner_id == principal.user_id,
        created_at=document.created_at,
        updated_at=document.updated_at,
    )


def _service(db: DbSession, runtime: Runtime) -> DocumentService:
    services = runtime.services
    return DocumentService(db, blobs=services.blobs, vectors=services.vectors, graph=services.graph)


def _parse_sensitivity(value: str) -> Sensitivity:
    try:
        return Sensitivity.parse(value)
    except (KeyError, ValueError) as exc:
        raise ValidationFailed("sensitivity must be PUBLIC, INTERNAL, CONFIDENTIAL or RESTRICTED") from exc


def _may_manage(document: Document, principal: Principal) -> None:
    """Changing or deleting needs write permission and either ownership or department membership."""
    if document.owner_id == principal.user_id or document.department in principal.departments:
        return
    if principal.has(Permission.USERS_MANAGE):
        return
    raise PermissionDenied("You can manage only documents of your own department")


@router.get("/documents/formats", summary="File types that can be uploaded")
async def formats(_: Reader) -> dict[str, Any]:
    return {"extensions": sorted(SUPPORTED_EXTENSIONS), "max_bytes": MAX_UPLOAD_BYTES}


@router.post("/documents", response_model=DocumentView, status_code=201, summary="Upload a document")
async def upload(
    principal: Writer,
    db: DbSession,
    runtime: RuntimeDep,
    file: Annotated[UploadFile, File(description="The document to add")],
    department: Annotated[str, Form()],
    sensitivity: Annotated[str, Form()],
    title: Annotated[str, Form()] = "",
    region: Annotated[str, Form()] = "global",
    effective_date: Annotated[date | None, Form()] = None,
    version: Annotated[str, Form(max_length=32)] = "1",
    supersedes_id: Annotated[uuid.UUID | None, Form()] = None,
) -> DocumentView:
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    service = _service(db, runtime)
    if supersedes_id is not None:
        scope = await runtime.policy.scope(principal)
        _may_manage(await service.get(supersedes_id, scope, principal=principal), principal)
    document, job = await service.register(
        NewDocument(
            data=data,
            filename=file.filename or "upload",
            title=title,
            department=department,
            sensitivity=_parse_sensitivity(sensitivity),
            region=region,
            effective_date=effective_date,
            version=version,
            supersedes_id=supersedes_id,
        ),
        owner=principal,
    )
    await AuditTrail().record(
        action="document.upload",
        decision="GRANTED",
        principal=principal,
        resource_type="document",
        resource_id=str(document.id),
        details={
            "title": document.title,
            "department": department,
            "sensitivity": sensitivity.upper(),
            "region": region,
            "sha256": document.content_hash,
            "job_id": str(job.id),
        },
    )
    await db.refresh(document)
    return _view(document, principal)


@router.get("/documents", response_model=DocumentPage, summary="Documents you are allowed to read")
async def list_documents(
    principal: Reader,
    db: DbSession,
    runtime: RuntimeDep,
    department: str | None = None,
    sensitivity: str | None = None,
    status: str | None = None,
    collection: str | None = None,
    q: Annotated[str | None, Query(max_length=200, description="Title contains")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> DocumentPage:
    scope = await runtime.policy.scope(principal)
    rank = int(_parse_sensitivity(sensitivity)) if sensitivity else None
    items, total = await _service(db, runtime).list(
        scope,
        principal=principal,
        department=department,
        sensitivity_rank=rank,
        status=status,
        collection=collection,
        search=q,
        limit=limit,
        offset=offset,
    )
    return DocumentPage(items=[_view(d, principal) for d in items], total=total, limit=limit, offset=offset)


@router.get("/documents/facets", summary="Document counts by department, sensitivity, status and collection")
async def facets(principal: Reader, db: DbSession, runtime: RuntimeDep) -> dict[str, dict[str, int]]:
    scope = await runtime.policy.scope(principal)
    return await _service(db, runtime).facets(scope, principal=principal)


@router.get("/documents/{document_id}", response_model=DocumentView, summary="One document")
async def get_document(document_id: uuid.UUID, principal: Reader, db: DbSession, runtime: RuntimeDep) -> DocumentView:
    scope = await runtime.policy.scope(principal)
    return _view(await _service(db, runtime).get(document_id, scope, principal=principal), principal)


@router.get("/documents/{document_id}/chunks", response_model=list[ChunkView], summary="Passages of a document")
async def get_chunks(
    document_id: uuid.UUID,
    principal: Reader,
    db: DbSession,
    runtime: RuntimeDep,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ChunkView]:
    scope = await runtime.policy.scope(principal)
    service = _service(db, runtime)
    document = await service.get(document_id, scope, principal=principal)
    return [
        ChunkView(
            id=c.id,
            ordinal=c.ordinal,
            section_path=c.section_path,
            page=c.page,
            # Personal data is masked exactly as it would be in an answer.
            text=mask_for_reader(c.text, sensitivity_rank=document.sensitivity_rank, can_read_pii=scope.can_read_pii),
            token_count=c.token_count,
            pii_types=list(c.pii_types or []),
        )
        for c in await service.chunks(document_id, limit=limit, offset=offset)
    ]


@router.patch("/documents/{document_id}", response_model=DocumentView, summary="Change labels or title")
async def update_document(
    document_id: uuid.UUID, body: DocumentUpdate, principal: Writer, db: DbSession, runtime: RuntimeDep
) -> DocumentView:
    scope = await runtime.policy.scope(principal)
    service = _service(db, runtime)
    document = await service.get(document_id, scope, principal=principal)
    _may_manage(document, principal)
    before = {
        "department": document.department,
        "sensitivity_rank": document.sensitivity_rank,
        "region": document.region,
    }
    changes = await service.relabel(
        document,
        department=body.department,
        sensitivity=_parse_sensitivity(body.sensitivity) if body.sensitivity else None,
        region=body.region,
        title=body.title,
        effective_date=body.effective_date,
    )
    if changes:
        await AuditTrail().record(
            action="document.relabel",
            decision="GRANTED",
            principal=principal,
            resource_type="document",
            resource_id=str(document_id),
            details={"before": before, "changes": changes},
        )
    await db.refresh(document)
    return _view(document, principal)


@router.delete("/documents/{document_id}", status_code=204, summary="Remove a document from every store")
async def delete_document(document_id: uuid.UUID, principal: Writer, db: DbSession, runtime: RuntimeDep) -> None:
    scope = await runtime.policy.scope(principal)
    service = _service(db, runtime)
    document = await service.get(document_id, scope, principal=principal)
    _may_manage(document, principal)
    await service.delete(document)
    await AuditTrail().record(
        action="document.delete",
        decision="GRANTED",
        principal=principal,
        resource_type="document",
        resource_id=str(document_id),
        details={"title": document.title, "sha256": document.content_hash},
    )


@router.post("/documents/{document_id}/reindex", response_model=JobView, summary="Re-run ingestion for a document")
async def reindex(document_id: uuid.UUID, principal: Writer, db: DbSession, runtime: RuntimeDep) -> JobView:
    scope = await runtime.policy.scope(principal)
    service = _service(db, runtime)
    document = await service.get(document_id, scope, principal=principal)
    _may_manage(document, principal)
    job = await service.queue_reindex(document)
    await AuditTrail().record(
        action="document.reindex",
        decision="GRANTED",
        principal=principal,
        resource_type="document",
        resource_id=str(document_id),
        details={"job_id": str(job.id)},
    )
    await db.refresh(job)
    return _job(job, document.title)


def _job(job: IngestionJob, title: str) -> JobView:
    return JobView(
        id=job.id,
        document_id=job.document_id,
        document_title=title,
        kind=job.kind,
        status=job.status,
        stage=job.stage,
        progress=job.progress,
        attempts=job.attempts,
        error=job.error,
        events=list(job.events),
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
    )


@router.get("/ingestion/jobs", response_model=list[JobView], summary="Ingestion jobs for documents you can read")
async def jobs(
    principal: Reader,
    db: DbSession,
    runtime: RuntimeDep,
    document_id: uuid.UUID | None = None,
    active_only: bool = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[JobView]:
    scope = await runtime.policy.scope(principal)
    query = (
        select(IngestionJob, Document.title)
        .join(Document, Document.id == IngestionJob.document_id)
        .where((scope.sql_predicate()) | (Document.owner_id == principal.user_id))
        .order_by(IngestionJob.created_at.desc())
        .limit(limit)
    )
    if document_id is not None:
        query = query.where(IngestionJob.document_id == document_id)
    if active_only:
        query = query.where(IngestionJob.status.in_(("queued", "running")))
    return [_job(job, title) for job, title in (await db.execute(query)).all()]


@router.get("/ingestion/jobs/{job_id}", response_model=JobView, summary="One ingestion job with its progress events")
async def job(job_id: uuid.UUID, principal: Reader, db: DbSession, runtime: RuntimeDep) -> JobView:
    found = await db.get(IngestionJob, job_id)
    if found is None:
        raise NotFound("Job not found")
    scope = await runtime.policy.scope(principal)
    document = await _service(db, runtime).get(found.document_id, scope, principal=principal)
    return _job(found, document.title)


@router.get("/documents/{document_id}/pii", summary="Personal-data types found in each passage of a document")
async def document_pii(document_id: uuid.UUID, principal: Reader, db: DbSession, runtime: RuntimeDep) -> dict[str, Any]:
    scope = await runtime.policy.scope(principal)
    document = await _service(db, runtime).get(document_id, scope, principal=principal)
    rows = await db.execute(
        select(func.jsonb_array_elements_text(Chunk.pii_types).label("kind"), func.count())
        .where(Chunk.document_id == document_id)
        .group_by("kind")
    )
    return {
        "document_id": str(document_id),
        "passages_by_type": {kind: n for kind, n in rows.all()},
        "totals": (document.pii_summary or {}).get("counts", {}),
    }
