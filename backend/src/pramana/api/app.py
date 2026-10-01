"""FastAPI application factory."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from pramana import __version__
from pramana.agents.pipeline import close_interrupted_runs
from pramana.api.middleware import RateLimitMiddleware, RequestContextMiddleware, install_error_handlers
from pramana.api.routers import (
    audit,
    auth,
    chat,
    documents,
    graph,
    health,
    monitoring,
    policies,
    reports,
    users,
    verify,
)
from pramana.knowledge.jobs import worker_loop
from pramana.runtime import Runtime, start_runtime, stop_runtime
from pramana.shared.config import get_settings
from pramana.shared.logging import get_logger

log = get_logger(__name__)

DESCRIPTION = """
Privacy-aware enterprise knowledge platform.

* Identity comes only from the verified access token.
* Access control is compiled into every retrieval query.
* Every answer is verified claim by claim and recorded in a hash-chained audit log.
"""


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    runtime = await start_runtime()
    app.state.runtime = runtime
    interrupted = await close_interrupted_runs()
    if interrupted:
        log.info("runs.closed_after_restart", count=interrupted)
    stop = asyncio.Event()
    worker: asyncio.Task[None] | None = None
    if runtime.settings.embedded_worker:
        # One process is enough for a single machine: uploads are ingested by this background task.
        # For more throughput, set EMBEDDED_WORKER=false and run `python -m pramana.worker` separately.
        worker = asyncio.create_task(worker_loop(runtime.services.pipeline, stop))
    warm_up: asyncio.Task[None] | None = None
    if runtime.settings.warm_up_models:
        # Load the local models in the background so the first question is not the slow one.
        warm_up = asyncio.create_task(_warm_up(runtime))
    yield
    stop.set()
    if warm_up is not None:
        warm_up.cancel()
    if worker is not None:
        await worker
    await stop_runtime()


async def _warm_up(runtime: Runtime) -> None:
    try:
        await runtime.services.models.warm_up()
        await asyncio.to_thread(runtime.services.pii.analyze, "Warm up for Jane Doe")
        await asyncio.to_thread(runtime.services.extractor.extract_fast, ["Warm up."])
        await runtime.services.detectors.warm_up()
        await runtime.services.reader.warm_up()
        log.info("models.ready")
    except Exception as exc:  # the models still load on first use
        log.warning("models.warm_up_failed", error=repr(exc))


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="PRAMĀṆA API",
        version=__version__,
        description=DESCRIPTION,
        lifespan=lifespan,
        # The interactive documentation describes every endpoint; it is not published in production.
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None,
        openapi_url=None if settings.is_production else "/openapi.json",
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
API_MODULES = (auth, users, chat, verify, documents, graph, policies, audit, monitoring, reports)

app = create_app()
