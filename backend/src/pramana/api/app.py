"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from pramana import __version__
from pramana.api.middleware import RateLimitMiddleware, RequestContextMiddleware, install_error_handlers
from pramana.api.routers import auth, health, users
from pramana.runtime import start_runtime, stop_runtime
from pramana.shared.config import get_settings

DESCRIPTION = """
Privacy-aware enterprise knowledge platform.

* Identity comes only from the verified access token.
* Access control is compiled into every retrieval query.
* Every answer is verified claim by claim and recorded in a hash-chained audit log.
"""


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.runtime = await start_runtime()
    yield
    await stop_runtime()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="PRAMĀṆA API",
        version=__version__,
        description=DESCRIPTION,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url=None,
        openapi_url="/openapi.json",
    )
    # Outermost first: CORS, then request context (ids, headers, metrics), then rate limiting.
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.frontend_origin],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "X-Requested-With", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
        max_age=600,
    )
    install_error_handlers(app)

    v1 = APIRouter(prefix="/api/v1")
    for module in API_MODULES:
        v1.include_router(module.router)
    app.include_router(v1)
    app.include_router(health.router)
    return app


# The whole versioned API surface, in one place.
API_MODULES = (auth, users)

app = create_app()
