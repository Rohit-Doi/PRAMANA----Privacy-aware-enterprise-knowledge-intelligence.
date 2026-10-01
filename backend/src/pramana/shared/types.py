"""Small value types shared across modules."""

from __future__ import annotations

from enum import IntEnum, StrEnum


class Sensitivity(IntEnum):
    """Document sensitivity. The integer is the rank used in every access comparison."""

    PUBLIC = 0
    INTERNAL = 1
    CONFIDENTIAL = 2
    RESTRICTED = 3

    @classmethod
    def parse(cls, value: str | int | Sensitivity) -> Sensitivity:
        if isinstance(value, Sensitivity):
            return value
        if isinstance(value, int):
            return cls(value)
        return cls[value.strip().upper()]


class Verdict(StrEnum):
    SUPPORTED = "SUPPORTED"
    PARTIAL = "PARTIAL"
    UNSUPPORTED = "UNSUPPORTED"
    CONTRADICTED = "CONTRADICTED"
    CONFLICTING = "CONFLICTING"


class Permission(StrEnum):
    """Fine-grained permissions granted by roles (or individually to a user)."""

    QUERY = "query:run"
    VERIFY = "verify:run"
    DOCUMENTS_READ = "documents:read"
    DOCUMENTS_WRITE = "documents:write"
    GRAPH_READ = "graph:read"
    PII_READ = "pii:read"
    USERS_MANAGE = "users:manage"
    POLICY_READ = "policy:read"
    POLICY_WRITE = "policy:write"
    AUDIT_READ = "audit:read"
    MONITORING_READ = "monitoring:read"
    REPORTS_READ = "reports:read"
    REVIEW_WRITE = "review:write"
    ANALYTICS_READ = "analytics:read"
    SETTINGS_WRITE = "settings:write"
    SIMULATE_USER = "users:simulate"
    REGION_ALL = "region:all"
