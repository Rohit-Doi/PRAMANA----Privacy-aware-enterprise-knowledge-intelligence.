"""The access scope returned by the policy engine, and its compilation into store filters.

The scope is the only thing the retrieval code needs to know about access control.
It is compiled into the Qdrant filter, the Cypher predicate and the SQL WHERE clause,
so content the user may not read is never fetched from any store.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from qdrant_client import models as qm
from sqlalchemy import ColumnElement, and_, or_, true

from pramana.knowledge.models import Document


@dataclass(frozen=True, slots=True)
class EgressPolicy:
    """What may leave the machine in a prompt to an external LLM."""

    max_rank: int
    pseudonymize_names: bool = True


@dataclass(frozen=True, slots=True)
class AccessScope:
    max_rank_own_dept: int
    max_rank_other_dept: int
    departments: tuple[str, ...]
    regions: tuple[str, ...]
    all_regions: bool
    can_read_pii: bool
    policy_version: int = 0

    @classmethod
    def from_policy(cls, result: dict[str, Any], *, policy_version: int) -> AccessScope:
        return cls(
            max_rank_own_dept=int(result["max_rank_own_dept"]),
            max_rank_other_dept=int(result["max_rank_other_dept"]),
            departments=tuple(result["departments"]),
            regions=tuple(result["regions"]),
            all_regions=bool(result["all_regions"]),
            can_read_pii=bool(result["can_read_pii"]),
            policy_version=policy_version,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "max_rank_own_dept": self.max_rank_own_dept,
            "max_rank_other_dept": self.max_rank_other_dept,
            "departments": list(self.departments),
            "regions": list(self.regions),
            "all_regions": self.all_regions,
            "can_read_pii": self.can_read_pii,
            "policy_version": self.policy_version,
        }

    # ── Reference semantics ─────────────────────────────────────────────
    def permits(self, *, department: str, sensitivity_rank: int, region: str) -> bool:
        """Plain-Python statement of the rule. The compiled filters below must agree with it,
        and both must agree with the Rego policy; the test suite checks all three."""
        if not self.all_regions and region not in self.regions:
            return False
        if sensitivity_rank <= self.max_rank_other_dept:
            return True
        return department in self.departments and sensitivity_rank <= self.max_rank_own_dept

    # ── Qdrant ──────────────────────────────────────────────────────────
    def qdrant_filter(self, *extra: qm.Condition) -> qm.Filter:
        any_department = qm.FieldCondition(key="sensitivity_rank", range=qm.Range(lte=self.max_rank_other_dept))
        own_department = qm.Filter(
            must=[
                qm.FieldCondition(key="department", match=qm.MatchAny(any=list(self.departments))),
                qm.FieldCondition(key="sensitivity_rank", range=qm.Range(lte=self.max_rank_own_dept)),
            ]
        )
        must: list[qm.Condition] = [qm.Filter(should=[any_department, own_department])]
        if not self.all_regions:
            must.append(qm.FieldCondition(key="region", match=qm.MatchAny(any=list(self.regions))))
        must.extend(extra)
        return qm.Filter(must=must)

    # ── Neo4j ───────────────────────────────────────────────────────────
    def cypher_predicate(self, alias: str) -> str:
        """Predicate over a node or relationship carrying department, sensitivity_rank, region."""
        return (
            f"(($all_regions OR {alias}.region IN $regions) AND "
            f"({alias}.sensitivity_rank <= $max_rank_other OR "
            f"({alias}.department IN $departments AND {alias}.sensitivity_rank <= $max_rank_own)))"
        )

    def cypher_params(self) -> dict[str, Any]:
        return {
            "all_regions": self.all_regions,
            "regions": list(self.regions),
            "departments": list(self.departments),
            "max_rank_other": self.max_rank_other_dept,
            "max_rank_own": self.max_rank_own_dept,
        }

    # ── SQL ─────────────────────────────────────────────────────────────
    def sql_predicate(self) -> ColumnElement[bool]:
        rank_ok = or_(
            Document.sensitivity_rank <= self.max_rank_other_dept,
            and_(
                Document.department.in_(self.departments),
                Document.sensitivity_rank <= self.max_rank_own_dept,
            ),
        )
        region_ok: ColumnElement[bool] = true() if self.all_regions else Document.region.in_(self.regions)
        return and_(rank_ok, region_ok)


# A scope that matches nothing: used when the policy engine returns no decision.
EMPTY_SCOPE = AccessScope(
    max_rank_own_dept=-1,
    max_rank_other_dept=-1,
    departments=(),
    regions=(),
    all_regions=False,
    can_read_pii=False,
)
