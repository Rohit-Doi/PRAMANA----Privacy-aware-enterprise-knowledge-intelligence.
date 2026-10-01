"""Audit log: search, inspect, verify the hash chain, export."""

from __future__ import annotations

import csv
import io
import json
import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func, select

from pramana.api.deps import DbSession, require
from pramana.auth.principal import Principal
from pramana.governance.audit import AuditTrail
from pramana.governance.models import AuditLog
from pramana.shared.errors import NotFound
from pramana.shared.types import Permission

router = APIRouter(prefix="/audit", tags=["Audit"])
Auditor = Annotated[Principal, Depends(require(Permission.AUDIT_READ))]


class AuditEntry(BaseModel):
    seq: int
    id: uuid.UUID
    ts: datetime
    actor_id: uuid.UUID | None
    actor_email: str
    actor_role: str
    action: str
    resource_type: str
    resource_id: str
    decision: str
    details: dict[str, Any]
    prev_hash: str
    entry_hash: str


class AuditPage(BaseModel):
    items: list[AuditEntry]
    next_before: int | None
    total: int


class ChainStatus(BaseModel):
    ok: bool
    entries_checked: int
    first_invalid_seq: int | None
    reason: str
    head_hash: str
    verified_at: datetime


def _entry(row: AuditLog) -> AuditEntry:
    return AuditEntry(
        seq=row.seq,
        id=row.id,
        ts=row.ts,
        actor_id=row.actor_id,
        actor_email=row.actor_email,
        actor_role=row.actor_role,
        action=row.action,
        resource_type=row.resource_type,
        resource_id=row.resource_id,
        decision=row.decision,
        details=row.details,
        prev_hash=row.prev_hash,
        entry_hash=row.entry_hash,
    )


def _filters(
    action: str | None,
    actor: str | None,
    decision: str | None,
    resource_id: str | None,
    since: datetime | None,
    until: datetime | None,
) -> list[Any]:
    conditions: list[Any] = []
    if action:
        conditions.append(AuditLog.action.like(f"{action}%"))
    if actor:
        conditions.append(AuditLog.actor_email.ilike(f"%{actor}%"))
    if decision:
        conditions.append(AuditLog.decision == decision.upper())
    if resource_id:
        conditions.append(AuditLog.resource_id == resource_id)
    if since:
        conditions.append(AuditLog.ts >= since)
    if until:
        conditions.append(AuditLog.ts <= until)
    return conditions


@router.get("", response_model=AuditPage, summary="Search the audit log (newest first)")
async def search(
    _: Auditor,
    db: DbSession,
    action: str | None = None,
    actor: str | None = None,
    decision: str | None = None,
    resource_id: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    before: Annotated[int | None, Query(description="Return entries with a sequence number below this")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> AuditPage:
    conditions = _filters(action, actor, decision, resource_id, since, until)
    total = (await db.execute(select(func.count()).select_from(AuditLog).where(*conditions))).scalar_one()
    page_conditions = [*conditions, AuditLog.seq < before] if before else conditions
    rows = (
        (await db.execute(select(AuditLog).where(*page_conditions).order_by(AuditLog.seq.desc()).limit(limit)))
        .scalars()
        .all()
    )
    return AuditPage(
        items=[_entry(r) for r in rows],
        next_before=rows[-1].seq if len(rows) == limit else None,
        total=total,
    )


@router.get("/actions", summary="Actions that appear in the log, with counts")
async def actions(_: Auditor, db: DbSession) -> list[dict[str, Any]]:
    rows = await db.execute(
        select(AuditLog.action, AuditLog.decision, func.count())
        .group_by(AuditLog.action, AuditLog.decision)
        .order_by(AuditLog.action)
    )
    return [{"action": a, "decision": d, "count": n} for a, d, n in rows.all()]


@router.post("/verify", response_model=ChainStatus, summary="Recompute the hash chain over the whole log")
async def verify_chain(principal: Auditor) -> ChainStatus:
    trail = AuditTrail()
    report = await trail.verify_chain()
    await trail.record(
        action="audit.verify",
        decision="VALID" if report.ok else "TAMPERED",
        principal=principal,
        details={"entries_checked": report.entries_checked, "first_invalid_seq": report.first_invalid_seq},
    )
    return ChainStatus(
        ok=report.ok,
        entries_checked=report.entries_checked,
        first_invalid_seq=report.first_invalid_seq,
        reason=report.reason,
        head_hash=report.head_hash,
        verified_at=datetime.now().astimezone(),
    )


@router.get("/export", summary="Download matching entries as CSV")
async def export(
    principal: Auditor,
    db: DbSession,
    action: str | None = None,
    actor: str | None = None,
    decision: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> StreamingResponse:
    conditions = _filters(action, actor, decision, None, since, until)
    rows = (await db.execute(select(AuditLog).where(*conditions).order_by(AuditLog.seq).limit(20_000))).scalars().all()
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "seq",
            "id",
            "timestamp",
            "actor_email",
            "actor_role",
            "action",
            "resource_type",
            "resource_id",
            "decision",
            "details",
            "prev_hash",
            "entry_hash",
        ]
    )
    for row in rows:
        writer.writerow(
            [
                row.seq,
                row.id,
                row.ts.isoformat(),
                row.actor_email,
                row.actor_role,
                row.action,
                row.resource_type,
                row.resource_id,
                row.decision,
                json.dumps(row.details, sort_keys=True),
                row.prev_hash,
                row.entry_hash,
            ]
        )
    await AuditTrail().record(
        action="audit.export",
        decision="GRANTED",
        principal=principal,
        details={"entries": len(rows)},
    )
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="pramana-audit-log.csv"'},
    )


@router.get("/{entry_id}", response_model=AuditEntry, summary="One audit entry")
async def entry(entry_id: uuid.UUID, _: Auditor, db: DbSession) -> AuditEntry:
    row = (await db.execute(select(AuditLog).where(AuditLog.id == entry_id))).scalar_one_or_none()
    if row is None:
        raise NotFound("Audit entry not found")
    return _entry(row)
