"""Cross-cutting HTTP behaviour: request ids, security headers, rate limiting, error mapping."""

from __future__ import annotations

import time
import uuid

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from prometheus_client import Counter, Histogram
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from pramana.runtime import get_runtime
from pramana.shared.errors import AppError
from pramana.shared.logging import get_logger

log = get_logger(__name__)

HTTP_REQUESTS = Counter("pramana_http_requests_total", "HTTP requests", ["method", "route", "status"])
HTTP_LATENCY = Histogram(
    "pramana_http_request_seconds",
    "HTTP request latency",
    ["method", "route"],
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 20, 40),
)

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
}
_UNLIMITED_PATHS = ("/healthz", "/readyz", "/metrics")


def problem(status: int, code: str, title: str, detail: str, request_id: str, **extra: object) -> JSONResponse:
    """RFC 9457 problem document."""
    body = {"type": f"urn:pramana:error:{code}", "title": title, "status": status, "detail": detail,
            "code": code, "request_id": request_id, **extra}
    return JSONResponse(body, status_code=status, media_type="application/problem+json")


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        request.state.request_id = request_id
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        started = time.perf_counter()
        response = await call_next(request)
        elapsed = time.perf_counter() - started

        route = request.scope.get("route")
        route_path = getattr(route, "path", "unmatched")
        HTTP_REQUESTS.labels(request.method, route_path, str(response.status_code)).inc()
        HTTP_LATENCY.labels(request.method, route_path).observe(elapsed)

        response.headers["X-Request-ID"] = request_id
        for header, value in SECURITY_HEADERS.items():
            response.headers.setdefault(header, value)
        if route_path not in _UNLIMITED_PATHS:
            log.info("http.request", method=request.method, path=request.url.path,
                     status=response.status_code, ms=round(elapsed * 1000))
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Fixed-window limit per client address, counted in Redis.

    If Redis is unavailable the request is allowed and the failure is logged: rate
    limiting protects availability and must not itself take the service down.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path
        if path in _UNLIMITED_PATHS or request.method == "OPTIONS":
            return await call_next(request)
        runtime = get_runtime()
        limit = runtime.settings.rate_limit_per_minute
        # Sign-in endpoints get a much tighter budget to slow password guessing.
        if path.startswith("/api/v1/auth/login") or path.startswith("/api/v1/auth/mfa/verify"):
            limit = runtime.settings.auth_rate_limit_per_minute
            bucket = "auth"
        else:
            bucket = "api"
        client = request.client.host if request.client else "unknown"
        key = f"ratelimit:{bucket}:{client}:{int(time.time() // 60)}"
        try:
            count = await runtime.redis.incr(key)
            if count == 1:
                await runtime.redis.expire(key, 70)
        except Exception as exc:  # Redis down: fail open, loudly
            log.warning("ratelimit.unavailable", error=repr(exc))
            return await call_next(request)
        if count > limit:
            request_id = getattr(request.state, "request_id", "")
            response = problem(429, "rate_limited", "Too many requests",
                               "Request rate limit exceeded; retry in a minute.", request_id)
            response.headers["Retry-After"] = "60"
            return response
        return await call_next(request)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def app_error(request: Request, exc: AppError) -> JSONResponse:
        request_id = getattr(request.state, "request_id", "")
        if exc.status_code >= 500:
            log.error("app.error", code=exc.code, detail=exc.detail)
        response = problem(exc.status_code, exc.code, exc.title, exc.detail, request_id, **exc.extra)
        if exc.status_code == 401:
            response.headers["WWW-Authenticate"] = "Bearer"
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        request_id = getattr(request.state, "request_id", "")
        errors = [{"field": ".".join(str(p) for p in e["loc"][1:]), "message": e["msg"]} for e in exc.errors()]
        return problem(422, "validation_failed", "Validation failed",
                       "The request is not valid.", request_id, errors=errors)

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception) -> JSONResponse:
        request_id = getattr(request.state, "request_id", "")
        log.exception("app.unhandled", error=repr(exc))
        # Internal details are logged, never returned.
        return problem(500, "internal_error", "Internal error",
                       "An unexpected error occurred. Quote the request id when reporting it.", request_id)
