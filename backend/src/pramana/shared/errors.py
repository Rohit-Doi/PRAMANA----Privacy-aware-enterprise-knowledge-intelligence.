"""Application error types. The API layer maps these to RFC 9457 problem responses."""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    status_code: int = 500
    code: str = "internal_error"
    title: str = "Internal error"

    def __init__(self, detail: str = "", **extra: Any) -> None:
        super().__init__(detail or self.title)
        self.detail = detail or self.title
        self.extra = extra


class ValidationFailed(AppError):
    status_code = 422
    code = "validation_failed"
    title = "Validation failed"


class NotAuthenticated(AppError):
    status_code = 401
    code = "not_authenticated"
    title = "Authentication required"


class PermissionDenied(AppError):
    status_code = 403
    code = "permission_denied"
    title = "Permission denied"


class NotFound(AppError):
    status_code = 404
    code = "not_found"
    title = "Resource not found"


class Conflict(AppError):
    status_code = 409
    code = "conflict"
    title = "Conflict"


class RateLimited(AppError):
    status_code = 429
    code = "rate_limited"
    title = "Too many requests"


class DependencyUnavailable(AppError):
    """A required backing service (database, policy engine, model, LLM) is not usable."""

    status_code = 503
    code = "dependency_unavailable"
    title = "A required service is unavailable"


class LLMQuotaExhausted(DependencyUnavailable):
    code = "llm_quota_exhausted"
    title = "All configured LLM providers are out of quota"
