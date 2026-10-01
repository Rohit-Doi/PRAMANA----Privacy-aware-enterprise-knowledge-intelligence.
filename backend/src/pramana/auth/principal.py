"""The authenticated caller, as the rest of the system sees it."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from pramana.auth.models import User
from pramana.shared.errors import PermissionDenied
from pramana.shared.types import Permission


@dataclass(frozen=True, slots=True)
class Principal:
    """Identity and attributes taken from the database for a verified token.

    Nothing here ever comes from a request body or query string.
    """

    user_id: uuid.UUID
    email: str
    full_name: str
    role: str
    department: str
    region: str
    max_rank_own_dept: int
    max_rank_other_dept: int
    extra_departments: tuple[str, ...] = ()
    permissions: frozenset[str] = field(default_factory=frozenset)
    session_id: uuid.UUID | None = None
    mfa_verified: bool = False
    is_service_account: bool = False
    # Set when an administrator is previewing the system as another user.
    simulated_by: uuid.UUID | None = None

    def has(self, permission: Permission | str) -> bool:
        return str(permission) in self.permissions

    def require(self, permission: Permission | str) -> None:
        if not self.has(permission):
            raise PermissionDenied(f"Missing permission: {permission}")

    @property
    def departments(self) -> tuple[str, ...]:
        return (self.department, *self.extra_departments)

    def policy_input(self) -> dict[str, object]:
        """The user attributes handed to the policy engine."""
        return {
            "id": str(self.user_id),
            "role": self.role,
            "department": self.department,
            "extra_departments": list(self.extra_departments),
            "region": self.region,
            "permissions": sorted(self.permissions),
        }


def principal_from_user(
    user: User,
    *,
    session_id: uuid.UUID | None = None,
    mfa_verified: bool = False,
    simulated_by: uuid.UUID | None = None,
) -> Principal:
    return Principal(
        user_id=user.id,
        email=user.email,
        full_name=user.full_name,
        role=user.role.key,
        department=user.department.key,
        region=user.region,
        max_rank_own_dept=user.role.max_rank_own_dept,
        max_rank_other_dept=user.role.max_rank_other_dept,
        extra_departments=tuple(user.extra_departments or ()),
        permissions=frozenset([*user.role.permissions, *(user.extra_permissions or ())]),
        session_id=session_id,
        mfa_verified=mfa_verified,
        is_service_account=user.is_service_account,
        simulated_by=simulated_by,
    )
