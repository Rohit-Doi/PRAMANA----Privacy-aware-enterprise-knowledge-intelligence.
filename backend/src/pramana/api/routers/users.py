"""User, role and department administration."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, EmailStr, Field

from pramana.api.deps import DbSession, RuntimeDep, require
from pramana.auth.models import Role, User
from pramana.auth.principal import Principal, principal_from_user
from pramana.auth.service import UserAdminService
from pramana.governance.audit import AuditTrail
from pramana.privacy.policy import PolicyStore
from pramana.shared.errors import NotFound, ValidationFailed
from pramana.shared.types import Permission

router = APIRouter(tags=["Users and roles"])
UserAdmin = Annotated[Principal, Depends(require(Permission.USERS_MANAGE))]
VALID_PERMISSIONS = {str(p) for p in Permission}
REGIONS = {"global", "amer", "emea", "apac"}


class UserResponse(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str
    role: str
    role_name: str
    department: str
    region: str
    extra_departments: list[str]
    extra_permissions: list[str]
    is_active: bool
    is_service_account: bool
    mfa_enabled: bool
    last_login_at: datetime | None
    created_at: datetime


class UserCreate(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=12, max_length=256)
    role: str
    department: str
    region: str = "global"
    extra_departments: list[str] = Field(default_factory=list)
    extra_permissions: list[str] = Field(default_factory=list)
    is_service_account: bool = False


class UserUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=128)
    role: str | None = None
    department: str | None = None
    region: str | None = None
    extra_departments: list[str] | None = None
    extra_permissions: list[str] | None = None
    is_active: bool | None = None


class PasswordReset(BaseModel):
    new_password: str = Field(min_length=12, max_length=256)


class RoleResponse(BaseModel):
    key: str
    name: str
    description: str
    max_rank_own_dept: int
    max_rank_other_dept: int
    permissions: list[str]


class RoleUpdate(BaseModel):
    max_rank_own_dept: int | None = Field(default=None, ge=0, le=3)
    max_rank_other_dept: int | None = Field(default=None, ge=0, le=3)
    permissions: list[str] | None = None
    note: str = Field(default="", max_length=500)


class DepartmentResponse(BaseModel):
    key: str
    name: str


def _user(user: User) -> UserResponse:
    return UserResponse(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        role=user.role.key,
        role_name=user.role.name,
        department=user.department.key,
        region=user.region,
        extra_departments=list(user.extra_departments or []),
        extra_permissions=list(user.extra_permissions or []),
        is_active=user.is_active,
        is_service_account=user.is_service_account,
        mfa_enabled=user.mfa_enabled,
        last_login_at=user.last_login_at,
        created_at=user.created_at,
    )


def _role(role: Role) -> RoleResponse:
    return RoleResponse(
        key=role.key,
        name=role.name,
        description=role.description,
        max_rank_own_dept=role.max_rank_own_dept,
        max_rank_other_dept=role.max_rank_other_dept,
        permissions=sorted(role.permissions),
    )


def _check_permissions(values: list[str] | None) -> None:
    unknown = sorted(set(values or []) - VALID_PERMISSIONS)
    if unknown:
        raise ValidationFailed("Unknown permission: " + ", ".join(unknown))


def _check_region(region: str | None) -> None:
    if region is not None and region not in REGIONS:
        raise ValidationFailed(f"Unknown region: {region}")


@router.get("/users", response_model=list[UserResponse], summary="List users")
async def list_users(_: UserAdmin, db: DbSession) -> list[UserResponse]:
    return [_user(u) for u in await UserAdminService(db).list_users()]


class PreviewUser(BaseModel):
    id: uuid.UUID
    full_name: str
    role: str
    role_name: str
    department: str
    region: str
    is_self: bool


@router.get("/users/previewable", response_model=list[PreviewUser], summary="Users you may preview the system as")
async def previewable_users(
    principal: Annotated[Principal, Depends(require(Permission.SIMULATE_USER))], db: DbSession, runtime: RuntimeDep
) -> list[PreviewUser]:
    """Active people whose access lies within the caller's own (see ``AccessScope.covers``)."""
    own = await runtime.policy.scope(principal)
    result = []
    for user in await UserAdminService(db).list_users():
        if not user.is_active or user.is_service_account:
            continue
        if user.id != principal.user_id and not own.covers(await runtime.policy.scope(principal_from_user(user))):
            continue
        result.append(
            PreviewUser(
                id=user.id,
                full_name=user.full_name,
                role=user.role.key,
                role_name=user.role.name,
                department=user.department.key,
                region=user.region,
                is_self=user.id == principal.user_id,
            )
        )
    return result


@router.post("/users", response_model=UserResponse, status_code=201, summary="Create a user")
async def create_user(body: UserCreate, admin: UserAdmin, db: DbSession) -> UserResponse:
    _check_permissions(body.extra_permissions)
    _check_region(body.region)
    user = await UserAdminService(db).create_user(
        email=body.email,
        full_name=body.full_name,
        password=body.password,
        role_key=body.role,
        department_key=body.department,
        region=body.region,
        extra_departments=body.extra_departments,
        extra_permissions=body.extra_permissions,
        is_service_account=body.is_service_account,
    )
    await AuditTrail().record(
        action="user.create",
        decision="GRANTED",
        principal=admin,
        resource_type="user",
        resource_id=str(user.id),
        details={"email": user.email, "role": body.role, "department": body.department},
    )
    return _user(user)


@router.get("/users/{user_id}", response_model=UserResponse, summary="Get a user")
async def get_user(user_id: uuid.UUID, _: UserAdmin, db: DbSession) -> UserResponse:
    return _user(await UserAdminService(db).get_user(user_id))


@router.patch("/users/{user_id}", response_model=UserResponse, summary="Change a user's role or attributes")
async def update_user(user_id: uuid.UUID, body: UserUpdate, admin: UserAdmin, db: DbSession) -> UserResponse:
    _check_permissions(body.extra_permissions)
    _check_region(body.region)
    if user_id == admin.user_id and (body.is_active is False or (body.role and body.role != admin.role)):
        raise ValidationFailed("You cannot deactivate yourself or change your own role")
    changes = body.model_dump(exclude_unset=True)
    user = await UserAdminService(db).update_user(
        user_id, role_key=changes.pop("role", None), department_key=changes.pop("department", None), **changes
    )
    await AuditTrail().record(
        action="user.update",
        decision="GRANTED",
        principal=admin,
        resource_type="user",
        resource_id=str(user_id),
        details={"changes": body.model_dump(exclude_unset=True)},
    )
    return _user(user)


@router.post("/users/{user_id}/password", status_code=204, summary="Reset a user's password")
async def reset_password(user_id: uuid.UUID, body: PasswordReset, admin: UserAdmin, db: DbSession) -> None:
    await UserAdminService(db).reset_password(user_id, body.new_password)
    await AuditTrail().record(
        action="user.password.reset",
        decision="GRANTED",
        principal=admin,
        resource_type="user",
        resource_id=str(user_id),
    )


@router.get("/roles", response_model=list[RoleResponse], summary="List roles with their limits")
async def list_roles(
    _: Annotated[Principal, Depends(require(Permission.POLICY_READ))], db: DbSession
) -> list[RoleResponse]:
    return [_role(r) for r in await UserAdminService(db).list_roles()]


@router.patch(
    "/roles/{role_key}",
    response_model=RoleResponse,
    summary="Change a role's limits or permissions (publishes a new policy version)",
)
async def update_role(
    role_key: str,
    body: RoleUpdate,
    admin: Annotated[Principal, Depends(require(Permission.POLICY_WRITE))],
    db: DbSession,
    runtime: RuntimeDep,
) -> RoleResponse:
    _check_permissions(body.permissions)
    roles = {r.key: r for r in await UserAdminService(db).list_roles()}
    role = roles.get(role_key)
    if role is None:
        raise NotFound("Role not found")
    own = body.max_rank_own_dept if body.max_rank_own_dept is not None else role.max_rank_own_dept
    other = body.max_rank_other_dept if body.max_rank_other_dept is not None else role.max_rank_other_dept
    if other > own:
        raise ValidationFailed("The cross-department limit cannot exceed the own-department limit")
    before = _role(role).model_dump()
    role.max_rank_own_dept, role.max_rank_other_dept = own, other
    if body.permissions is not None:
        role.permissions = sorted(set(body.permissions))
    await db.flush()
    version = await PolicyStore(db, runtime.policy).publish(
        note=body.note or f"Role '{role_key}' updated", created_by=admin.user_id
    )
    await AuditTrail().record(
        action="policy.role.update",
        decision="GRANTED",
        principal=admin,
        resource_type="role",
        resource_id=role_key,
        details={"before": before, "after": _role(role).model_dump(), "policy_version": version.version},
    )
    return _role(role)


@router.get("/departments", response_model=list[DepartmentResponse], summary="List departments")
async def list_departments(
    _: Annotated[Principal, Depends(require(Permission.DOCUMENTS_READ))], db: DbSession
) -> list[DepartmentResponse]:
    return [DepartmentResponse(key=d.key, name=d.name) for d in await UserAdminService(db).list_departments()]
