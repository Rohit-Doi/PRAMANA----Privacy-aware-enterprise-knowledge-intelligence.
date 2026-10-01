"""Reference data: departments, roles and the demo personas."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pramana.auth.models import Department, Role, User
from pramana.shared import crypto
from pramana.shared.types import Permission as P
from pramana.shared.types import Sensitivity as S

DEPARTMENTS = {
    "company": "Company and Executive Office",
    "hr": "People (HR)",
    "security": "Security",
    "finance": "Finance",
    "legal": "Legal",
    "engineering": "Engineering",
    "it": "IT",
    "marketing": "Marketing",
    "sales": "Sales",
    "operations": "Operations",
    "compliance": "Compliance",
}

_BASIC = [P.QUERY, P.VERIFY, P.DOCUMENTS_READ, P.GRAPH_READ]


@dataclass(frozen=True, slots=True)
class RoleSpec:
    name: str
    description: str
    own: S
    other: S
    permissions: list[P]


ROLES: dict[str, RoleSpec] = {
    "employee": RoleSpec(
        "Employee", "Reads PUBLIC and INTERNAL documents.", S.INTERNAL, S.INTERNAL, [*_BASIC],
    ),
    "manager": RoleSpec(
        "Manager", "Adds CONFIDENTIAL documents of their own department.",
        S.CONFIDENTIAL, S.INTERNAL, [*_BASIC, P.DOCUMENTS_WRITE, P.REPORTS_READ],
    ),
    "analyst": RoleSpec(
        "Analyst", "Reads CONFIDENTIAL documents across departments.",
        S.CONFIDENTIAL, S.CONFIDENTIAL, [*_BASIC, P.REPORTS_READ, P.MONITORING_READ, P.ANALYTICS_READ],
    ),
    "compliance_officer": RoleSpec(
        "Compliance Officer", "Analyst access plus audit log, policy view and answer review.",
        S.CONFIDENTIAL, S.CONFIDENTIAL,
        [*_BASIC, P.AUDIT_READ, P.POLICY_READ, P.REPORTS_READ, P.REVIEW_WRITE, P.MONITORING_READ,
         P.ANALYTICS_READ],
    ),
    "executive": RoleSpec(
        "Executive", "Reads everything including RESTRICTED, in every region, with personal data.",
        S.RESTRICTED, S.RESTRICTED,
        [*_BASIC, P.PII_READ, P.REGION_ALL, P.REPORTS_READ, P.ANALYTICS_READ],
    ),
    # Separation of duties: administrators run the platform but get no elevated content access.
    "admin": RoleSpec(
        "Administrator", "Manages users, policies and sources. No elevated access to content.",
        S.INTERNAL, S.INTERNAL,
        [*_BASIC, P.DOCUMENTS_WRITE, P.USERS_MANAGE, P.POLICY_READ, P.POLICY_WRITE, P.AUDIT_READ,
         P.MONITORING_READ, P.REPORTS_READ, P.REVIEW_WRITE, P.ANALYTICS_READ, P.SETTINGS_WRITE,
         P.SIMULATE_USER, P.REGION_ALL],
    ),
}


@dataclass(frozen=True, slots=True)
class Persona:
    email: str
    full_name: str
    role: str
    department: str
    region: str
    is_service_account: bool = False


PERSONAS = [
    Persona("emma.employee@aurelia-systems.example", "Emma Lindqvist", "employee", "engineering", "emea"),
    Persona("marcus.manager@aurelia-systems.example", "Marcus Okafor", "manager", "hr", "amer"),
    Persona("fatima.finance@aurelia-systems.example", "Fatima Al-Sayed", "manager", "finance", "emea"),
    Persona("arjun.analyst@aurelia-systems.example", "Arjun Mehta", "analyst", "security", "apac"),
    Persona("clara.compliance@aurelia-systems.example", "Clara Jensen", "compliance_officer", "compliance", "emea"),
    Persona("elena.executive@aurelia-systems.example", "Elena Vasquez", "executive", "company", "amer"),
    Persona("adam.admin@aurelia-systems.example", "Adam Novak", "admin", "it", "amer"),
    Persona("assistant.bot@aurelia-systems.example", "Helpdesk Assistant (bot)", "employee", "it", "global",
            is_service_account=True),
]


async def seed_reference_data(db: AsyncSession) -> None:
    existing_departments = {d.key for d in (await db.execute(select(Department))).scalars()}
    for key, name in DEPARTMENTS.items():
        if key not in existing_departments:
            db.add(Department(key=key, name=name))

    existing_roles = {r.key: r for r in (await db.execute(select(Role))).scalars()}
    for key, spec in ROLES.items():
        if key not in existing_roles:
            db.add(
                Role(
                    key=key, name=spec.name, description=spec.description,
                    max_rank_own_dept=int(spec.own), max_rank_other_dept=int(spec.other),
                    permissions=sorted(str(p) for p in spec.permissions),
                )
            )
    await db.flush()


async def seed_personas(db: AsyncSession, password: str) -> list[str]:
    """Create the demo users that do not exist yet. Returns the emails created."""
    roles = {r.key: r for r in (await db.execute(select(Role))).scalars()}
    departments = {d.key: d for d in (await db.execute(select(Department))).scalars()}
    existing = {u.email for u in (await db.execute(select(User))).scalars()}
    password_hash = crypto.hash_password(password)
    created = []
    for persona in PERSONAS:
        if persona.email in existing:
            continue
        db.add(
            User(
                email=persona.email, full_name=persona.full_name, password_hash=password_hash,
                role_id=roles[persona.role].id, department_id=departments[persona.department].id,
                region=persona.region, is_service_account=persona.is_service_account,
            )
        )
        created.append(persona.email)
    await db.flush()
    return created
