"""Hash-chained audit trail.

Each entry stores the hash of the previous entry and its own hash:

    entry_hash = SHA-256(prev_hash ‖ canonical_json(entry fields))

Changing, removing or inserting any row breaks every hash after it, and the database
itself rejects UPDATE and DELETE on the table. ``verify_chain`` recomputes the chain.

Entries are written in their own short transaction, serialised by an advisory lock,
so a slow request never blocks other writers and a failed request is still recorded.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select, text

from pramana.auth.principal import Principal
from pramana.governance.models import AuditLog
from pramana.shared.crypto import canonical_json, sha256_hex
from pramana.shared.db import session_scope, utcnow

GENESIS_HASH = "0" * 64
_CHAIN_LOCK_KEY = 0x5052414D  # "PRAM": advisory lock that serialises appends


def _normalise(value: Any) -> Any:
    """Make a JSON value survive a round trip through JSONB byte-for-byte."""
    if isinstance(value, float):
        return round(value, 6)
    if isinstance(value, dict):
        return {str(k): _normalise(v) for k, v in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [_normalise(v) for v in value]
    if isinstance(value, uuid.UUID | datetime):
        return str(value)
    return value


def compute_entry_hash(
    *,
    prev_hash: str,
    entry_id: uuid.UUID,
    ts: datetime,
    actor_id: uuid.UUID | None,
    actor_email: str,
    actor_role: str,
    action: str,
    resource_type: str,
    resource_id: str,
    decision: str,
    details: dict[str, Any],
) -> str:
    payload = {
        "id": str(entry_id),
        "ts": ts.isoformat(timespec="microseconds"),
        "actor_id": str(actor_id) if actor_id else None,
        "actor_email": actor_email,
        "actor_role": actor_role,
        "action": action,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "decision": decision,
        "details": details,
    }
    return sha256_hex(prev_hash.encode() + canonical_json(payload))


@dataclass(frozen=True, slots=True)
class ChainReport:
    ok: bool
    entries_checked: int
    first_invalid_seq: int | None = None
    reason: str = ""
    head_hash: str = GENESIS_HASH


class AuditTrail:
    async def record(
        self,
        *,
        action: str,
        decision: str = "",
        principal: Principal | None = None,
        actor_email: str = "",
        resource_type: str = "",
        resource_id: str = "",
        details: dict[str, Any] | None = None,
    ) -> uuid.UUID:
        entry_id = uuid.uuid4()
        clean_details = _normalise(details or {})
        if principal is not None and principal.simulated_by is not None:
            clean_details["simulated_by"] = str(principal.simulated_by)
        actor_id = principal.user_id if principal else None
        email = principal.email if principal else actor_email
        role = principal.role if principal else ""

        async with session_scope() as db:
            await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _CHAIN_LOCK_KEY})
            prev_hash = (
                await db.execute(select(AuditLog.entry_hash).order_by(AuditLog.seq.desc()).limit(1))
            ).scalar_one_or_none() or GENESIS_HASH
            ts = utcnow()
            db.add(
                AuditLog(
                    id=entry_id, ts=ts, actor_id=actor_id, actor_email=email, actor_role=role,
                    action=action, resource_type=resource_type, resource_id=resource_id,
                    decision=decision, details=clean_details, prev_hash=prev_hash,
                    entry_hash=compute_entry_hash(
                        prev_hash=prev_hash, entry_id=entry_id, ts=ts, actor_id=actor_id,
                        actor_email=email, actor_role=role, action=action,
                        resource_type=resource_type, resource_id=resource_id,
                        decision=decision, details=clean_details,
                    ),
                )
            )
        return entry_id

    async def verify_chain(self) -> ChainReport:
        """Recompute every hash from the first entry. Any edit, gap or reordering is detected."""
        expected_prev = GENESIS_HASH
        checked = 0
        last_seq = 0
        async with session_scope() as db:
            while True:
                rows = (
                    await db.execute(
                        select(AuditLog).where(AuditLog.seq > last_seq).order_by(AuditLog.seq).limit(1000)
                    )
                ).scalars().all()
                if not rows:
                    break
                for row in rows:
                    if row.prev_hash != expected_prev:
                        return ChainReport(False, checked, row.seq, "previous-hash link is broken", expected_prev)
                    recomputed = compute_entry_hash(
                        prev_hash=row.prev_hash, entry_id=row.id, ts=row.ts, actor_id=row.actor_id,
                        actor_email=row.actor_email, actor_role=row.actor_role, action=row.action,
                        resource_type=row.resource_type, resource_id=row.resource_id,
                        decision=row.decision, details=row.details,
                    )
                    if recomputed != row.entry_hash:
                        return ChainReport(False, checked, row.seq, "entry content does not match its hash", expected_prev)
                    expected_prev = row.entry_hash
                    checked += 1
                    last_seq = row.seq
        return ChainReport(True, checked, head_hash=expected_prev)
