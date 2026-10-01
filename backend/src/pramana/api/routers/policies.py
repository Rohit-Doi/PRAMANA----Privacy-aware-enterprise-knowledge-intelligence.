"""Policy administration: view the active policy, change its settings, simulate decisions."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from pramana.api.deps import DbSession, RuntimeDep, require
from pramana.auth.models import User
from pramana.auth.principal import Principal, principal_from_user
from pramana.governance.audit import AuditTrail
from pramana.knowledge.models import Document
from pramana.privacy.policy import PolicyStore, ResourceRef
from pramana.shared.errors import NotFound, ValidationFailed
from pramana.shared.types import Permission, Sensitivity

router = APIRouter(prefix="/policies", tags=["Policies"])
PolicyReader = Annotated[Principal, Depends(require(Permission.POLICY_READ))]
PolicyWriter = Annotated[Principal, Depends(require(Permission.POLICY_WRITE))]


class PolicyVersionView(BaseModel):
    version: int
    note: str
    is_active: bool
    rego_sha256: str
    created_at: datetime
    created_by: uuid.UUID | None


class ActivePolicy(PolicyVersionView):
    rego: str
    roles: dict[str, Any]
    config: dict[str, Any]
    sensitivity_levels: list[dict[str, Any]]


class ConfigUpdate(BaseModel):
    egress_max_rank: int | None = Field(default=None, ge=-1, le=3)
    pseudonymize_names: bool | None = None
    show_withheld_count: bool | None = None
    note: str = Field(default="", max_length=500)


class SimulateRequest(BaseModel):
    user_id: uuid.UUID
    document_id: uuid.UUID | None = None
    department: str | None = None
    sensitivity: str | None = None
    region: str = "global"


LEVELS = [
    {"rank": 0, "name": "PUBLIC", "description": "Approved for anyone."},
    {"rank": 1, "name": "INTERNAL", "description": "For all staff."},
    {"rank": 2, "name": "CONFIDENTIAL", "description": "Owning department, and roles with a cross-department need."},
    {"rank": 3, "name": "RESTRICTED", "description": "Executives only; never sent to an external model."},
]


@router.get("/active", response_model=ActivePolicy, summary="The policy in force: rules, role limits and settings")
async def active(_: PolicyReader, db: DbSession, runtime: RuntimeDep) -> ActivePolicy:
    version = await PolicyStore(db, runtime.policy).active()
    if version is None:
        raise NotFound("No policy has been published")
    return ActivePolicy(
        version=version.version,
        note=version.note,
        is_active=True,
        rego_sha256=version.rego_sha256,
        created_at=version.created_at,
        created_by=version.created_by,
        rego=version.rego,
        roles=version.data.get("roles", {}),
        config=version.data.get("config", {}),
        sensitivity_levels=LEVELS,
    )


@router.get("/versions", response_model=list[PolicyVersionView], summary="Policy history")
async def versions(_: PolicyReader, db: DbSession, runtime: RuntimeDep) -> list[PolicyVersionView]:
    return [
        PolicyVersionView(
            version=v.version,
            note=v.note,
            is_active=v.is_active,
            rego_sha256=v.rego_sha256,
            created_at=v.created_at,
            created_by=v.created_by,
        )
        for v in await PolicyStore(db, runtime.policy).history()
    ]


@router.put("/config", response_model=PolicyVersionView, summary="Change policy settings (publishes a new version)")
async def update_config(
    body: ConfigUpdate, principal: PolicyWriter, db: DbSession, runtime: RuntimeDep
) -> PolicyVersionView:
    changes = body.model_dump(exclude_unset=True, exclude={"note"}, exclude_none=True)
    if not changes:
        raise ValidationFailed("No setting was changed")
    version = await PolicyStore(db, runtime.policy).publish(
        note=body.note or "Settings changed: " + ", ".join(sorted(changes)),
        created_by=principal.user_id,
        config=changes,
    )
    await AuditTrail().record(
        action="policy.config.update",
        decision="GRANTED",
        principal=principal,
        resource_type="policy",
        resource_id=str(version.version),
        details={"changes": changes, "policy_version": version.version},
    )
    return PolicyVersionView(
        version=version.version,
        note=version.note,
        is_active=True,
        rego_sha256=version.rego_sha256,
        created_at=version.created_at,
        created_by=version.created_by,
    )


@router.post("/simulate", summary="What can a user read, and why is a given document allowed or denied?")
async def simulate(
    body: SimulateRequest, principal: PolicyReader, db: DbSession, runtime: RuntimeDep
) -> dict[str, Any]:
    user = await db.get(User, body.user_id)
    if user is None:
        raise NotFound("User not found")
    subject = principal_from_user(user)
    context = await runtime.policy.access_context(subject)
    scope = context.scope

    # How many documents of each sensitivity the user can see, of how many exist.
    visible = dict(
        (
            await db.execute(
                select(Document.sensitivity_rank, func.count())
                .where(Document.status == "ready", scope.sql_predicate())
                .group_by(Document.sensitivity_rank)
            )
        ).all()
    )
    total = dict(
        (
            await db.execute(
                select(Document.sensitivity_rank, func.count())
                .where(Document.status == "ready")
                .group_by(Document.sensitivity_rank)
            )
        ).all()
    )
    result: dict[str, Any] = {
        "user": {
            "id": str(user.id),
            "email": user.email,
            "full_name": user.full_name,
            "role": subject.role,
            "department": subject.department,
            "extra_departments": list(subject.extra_departments),
            "region": subject.region,
        },
        "scope": scope.as_dict(),
        "egress": {"max_rank": context.egress.max_rank, "pseudonymize_names": context.egress.pseudonymize_names},
        "visible_documents": [
            {
                "sensitivity": level["name"],
                "rank": level["rank"],
                "visible": visible.get(level["rank"], 0),
                "total": total.get(level["rank"], 0),
            }
            for level in LEVELS
        ],
        "policy_version": scope.policy_version,
    }

    resource: ResourceRef | None = None
    if body.document_id is not None:
        document = await db.get(Document, body.document_id)
        if document is None or document.status == "deleted":
            raise NotFound("Document not found")
        # The simulator reveals a document's labels, so the caller must be able to read it too.
        own_scope = await runtime.policy.scope(principal)
        if not own_scope.permits(
            department=document.department, sensitivity_rank=document.sensitivity_rank, region=document.region
        ):
            raise NotFound("Document not found")
        resource = ResourceRef(str(document.id), document.department, document.sensitivity_rank, document.region)
        result["resource"] = {"title": document.title, **resource.as_input()}
    elif body.department and body.sensitivity:
        try:
            rank = int(Sensitivity.parse(body.sensitivity))
        except (KeyError, ValueError) as exc:
            raise ValidationFailed("Unknown sensitivity") from exc
        resource = ResourceRef("hypothetical", body.department, rank, body.region)
        result["resource"] = {"title": "Hypothetical document", **resource.as_input()}
    if resource is not None:
        decision = await runtime.policy.explain(subject, resource)
        result["decision"] = {"allow": decision.allow, "reasons": list(decision.reasons)}
    return result
