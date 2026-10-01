"""Policy engine client (OPA) and versioned policy storage.

Fail-closed everywhere: if OPA is unreachable or returns no decision, the answer is "deny".
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from pramana.auth.models import Role
from pramana.auth.principal import Principal
from pramana.governance.models import PolicyVersion
from pramana.privacy.scope import EMPTY_SCOPE, AccessScope, EgressPolicy
from pramana.shared.config import REPO_ROOT, get_settings
from pramana.shared.crypto import sha256_hex
from pramana.shared.db import utcnow
from pramana.shared.errors import DependencyUnavailable, ValidationFailed
from pramana.shared.logging import get_logger

log = get_logger(__name__)

POLICY_ID = "pramana_authz"
REGO_PATH = REPO_ROOT / "policies" / "authz.rego"
DEFAULT_CONFIG: dict[str, Any] = {
    # Highest sensitivity rank that may be sent to an external LLM (2 = CONFIDENTIAL).
    "egress_max_rank": 2,
    "pseudonymize_names": True,
    "show_withheld_count": True,
}


@dataclass(frozen=True, slots=True)
class ResourceRef:
    id: str
    department: str
    sensitivity_rank: int
    region: str

    def as_input(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "department": self.department,
            "sensitivity_rank": self.sensitivity_rank,
            "region": self.region,
        }


@dataclass(frozen=True, slots=True)
class Decision:
    allow: bool
    reasons: tuple[str, ...]
    policy_version: int


@dataclass(frozen=True, slots=True)
class AccessContext:
    """Everything the pipeline needs to know about one caller's access, from one policy call."""

    scope: AccessScope
    egress: EgressPolicy
    show_withheld_count: bool


class PolicyEngine:
    """Thin async client for OPA's REST API."""

    def __init__(self, base_url: str | None = None, *, timeout: float = 5.0) -> None:
        self._base_url = (base_url or get_settings().opa_url).rstrip("/")
        self._client = httpx.AsyncClient(base_url=self._base_url, timeout=timeout)
        self._loaded_version: int | None = None
        self._loaded_bundle: tuple[str, dict[str, Any], int] | None = None

    async def close(self) -> None:
        await self._client.aclose()

    @property
    def loaded_version(self) -> int:
        return self._loaded_version or 0

    # ── Loading ─────────────────────────────────────────────────────────
    async def load(self, *, rego: str, data: dict[str, Any], version: int) -> None:
        try:
            response = await self._client.put(
                f"/v1/policies/{POLICY_ID}", content=rego, headers={"Content-Type": "text/plain"}
            )
            if response.status_code >= 400:
                raise ValidationFailed(f"Policy rejected by the engine: {response.text[:500]}")
            response = await self._client.put("/v1/data/pramana", json={**data, "version": version})
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise DependencyUnavailable(f"Policy engine unreachable: {exc!r}") from exc
        self._loaded_version = version
        self._loaded_bundle = (rego, data, version)
        log.info("policy.loaded", version=version)

    async def is_healthy(self) -> bool:
        try:
            response = await self._client.get("/health")
            return response.status_code == 200
        except httpx.HTTPError:
            return False

    # ── Decisions ───────────────────────────────────────────────────────
    async def access_context(self, principal: Principal) -> AccessContext:
        result = await self._query("", {"user": principal.policy_input()})
        scope_doc = result.get("scope")
        if not isinstance(scope_doc, dict):
            # Unknown role or missing data: no scope, therefore no access.
            log.warning("policy.no_scope", role=principal.role, user_id=str(principal.user_id))
            return AccessContext(
                scope=EMPTY_SCOPE, egress=EgressPolicy(max_rank=-1), show_withheld_count=False
            )
        egress_doc = result.get("egress") or {}
        return AccessContext(
            scope=AccessScope.from_policy(scope_doc, policy_version=self.loaded_version),
            egress=EgressPolicy(
                max_rank=int(egress_doc.get("max_rank", -1)),
                pseudonymize_names=bool(egress_doc.get("pseudonymize_names", True)),
            ),
            show_withheld_count=bool(result.get("show_withheld_count", False)),
        )

    async def scope(self, principal: Principal) -> AccessScope:
        return (await self.access_context(principal)).scope

    async def allowed_ids(self, principal: Principal, resources: list[ResourceRef]) -> set[str]:
        """Batch re-check after retrieval. Anything not explicitly allowed is dropped."""
        if not resources:
            return set()
        result = await self._query(
            "/allowed_ids",
            {"user": principal.policy_input(), "resources": [r.as_input() for r in resources]},
        )
        return set(result) if isinstance(result, list) else set()

    async def explain(self, principal: Principal, resource: ResourceRef) -> Decision:
        result = await self._query("", {"user": principal.policy_input(), "resource": resource.as_input()})
        return Decision(
            allow=bool(result.get("allow", False)),
            reasons=tuple(sorted(result.get("reasons", []))),
            policy_version=self.loaded_version,
        )

    async def allow_action(self, principal: Principal, action: str) -> bool:
        result = await self._query("/allow_action", {"user": principal.policy_input(), "action": action})
        return result is True

    async def _query(self, rule: str, input_doc: dict[str, Any]) -> Any:
        async def post() -> Any:
            response = await self._client.post(f"/v1/data/pramana/authz{rule}", json={"input": input_doc})
            response.raise_for_status()
            return response.json().get("result")

        try:
            result = await post()
            if result is None and self._loaded_bundle is not None:
                # OPA restarted and lost its in-memory policy: reload once and retry.
                rego, data, version = self._loaded_bundle
                await self.load(rego=rego, data=data, version=version)
                result = await post()
        except httpx.HTTPError as exc:
            raise DependencyUnavailable(f"Policy engine unreachable: {exc!r}") from exc
        if rule == "":
            return result if isinstance(result, dict) else {}
        return result


class PolicyStore:
    """Immutable policy versions in Postgres. Publishing a version also loads it into OPA."""

    def __init__(self, db: AsyncSession, engine: PolicyEngine) -> None:
        self._db = db
        self._engine = engine

    async def active(self) -> PolicyVersion | None:
        rows = await self._db.execute(select(PolicyVersion).where(PolicyVersion.is_active))
        return rows.scalar_one_or_none()

    async def history(self) -> list[PolicyVersion]:
        rows = await self._db.execute(select(PolicyVersion).order_by(PolicyVersion.version.desc()))
        return list(rows.scalars())

    async def bootstrap(self) -> PolicyVersion:
        """Load the active version into OPA, creating version 1 on first start."""
        current = await self.active()
        if current is None:
            current = await self.publish(note="Initial policy", created_by=None)
        else:
            await self._engine.load(rego=current.rego, data=current.data, version=current.version)
        return current

    async def publish(
        self,
        *,
        note: str,
        created_by: uuid.UUID | None,
        config: dict[str, Any] | None = None,
        rego: str | None = None,
    ) -> PolicyVersion:
        """Snapshot the roles table and config into a new active version."""
        previous = await self.active()
        rego_text = rego or (previous.rego if previous else Path(REGO_PATH).read_text(encoding="utf-8"))
        merged_config = {
            **DEFAULT_CONFIG,
            **((previous.data.get("config") or {}) if previous else {}),
            **(config or {}),
        }
        _validate_config(merged_config)
        data = {"roles": await self._roles_snapshot(), "config": merged_config}

        next_version = ((await self._db.execute(select(func.max(PolicyVersion.version)))).scalar() or 0) + 1
        # Load first: if OPA rejects the policy nothing is stored or activated.
        await self._engine.load(rego=rego_text, data=data, version=next_version)

        await self._db.execute(update(PolicyVersion).where(PolicyVersion.is_active).values(is_active=False))
        version = PolicyVersion(
            version=next_version,
            rego=rego_text,
            rego_sha256=sha256_hex(rego_text),
            data=data,
            note=note,
            is_active=True,
            created_by=created_by,
            created_at=utcnow(),
        )
        self._db.add(version)
        await self._db.flush()
        return version

    async def _roles_snapshot(self) -> dict[str, Any]:
        rows = await self._db.execute(select(Role).order_by(Role.key))
        return {
            role.key: {
                "max_rank_own_dept": role.max_rank_own_dept,
                "max_rank_other_dept": role.max_rank_other_dept,
                "permissions": sorted(role.permissions),
            }
            for role in rows.scalars()
        }


def _validate_config(config: dict[str, Any]) -> None:
    rank = config.get("egress_max_rank")
    if not isinstance(rank, int) or not -1 <= rank <= 3:
        raise ValidationFailed("egress_max_rank must be an integer between -1 and 3")
    for key in ("pseudonymize_names", "show_withheld_count"):
        if not isinstance(config.get(key), bool):
            raise ValidationFailed(f"{key} must be true or false")
