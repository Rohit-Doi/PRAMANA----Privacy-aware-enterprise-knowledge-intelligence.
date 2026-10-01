"""Liveness, readiness and metrics."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text

from pramana.api.deps import RuntimeDep
from pramana.shared.db import get_engine

router = APIRouter(tags=["Health"])


@router.get("/healthz", summary="Liveness: the process is running")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


async def _check(name: str, probe: Any) -> tuple[str, str]:
    try:
        await asyncio.wait_for(probe, timeout=3)
        return name, "ok"
    except Exception as exc:
        return name, f"unavailable: {type(exc).__name__}"


@router.get("/readyz", summary="Readiness: every backing service answers")
async def readyz(runtime: RuntimeDep, response: Response) -> dict[str, Any]:
    async def postgres() -> None:
        async with get_engine().connect() as connection:
            await connection.execute(text("SELECT 1"))

    async def policy() -> None:
        if not await runtime.policy.is_healthy():
            raise ConnectionError("policy engine")

    results = dict(
        await asyncio.gather(
            _check("postgres", postgres()),
            _check("redis", runtime.redis.ping()),
            _check("qdrant", runtime.qdrant.get_collections()),
            _check("neo4j", runtime.neo4j.verify_connectivity()),
            _check("policy_engine", policy()),
        )
    )
    ready = all(v == "ok" for v in results.values())
    if not ready:
        response.status_code = 503
    return {"status": "ready" if ready else "degraded", "checks": results,
            "policy_version": runtime.policy.loaded_version}


@router.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
