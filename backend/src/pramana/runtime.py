"""Process-wide runtime: the long-lived clients shared by the API and the worker."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Any

import redis.asyncio as aioredis
from neo4j import AsyncDriver, AsyncGraphDatabase
from qdrant_client import AsyncQdrantClient

from pramana.privacy.policy import PolicyEngine, PolicyStore
from pramana.shared.config import Settings, get_settings
from pramana.shared.db import dispose_engine, session_scope
from pramana.shared.logging import configure_logging, get_logger

log = get_logger(__name__)


@dataclass(slots=True)
class Runtime:
    settings: Settings
    redis: aioredis.Redis
    qdrant: AsyncQdrantClient
    neo4j: AsyncDriver
    policy: PolicyEngine
    extras: dict[str, Any] = field(default_factory=dict)

    async def close(self) -> None:
        await self.policy.close()
        await self.neo4j.close()
        await self.qdrant.close()
        await self.redis.aclose()
        await dispose_engine()


_runtime: Runtime | None = None


def _use_utf8_console() -> None:
    # Some model libraries print non-ASCII characters; the default Windows code page cannot.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


async def start_runtime() -> Runtime:
    """Create clients, verify configuration and load the active policy into the engine."""
    global _runtime
    if _runtime is not None:
        return _runtime
    _use_utf8_console()
    settings = get_settings()
    configure_logging(json_output=settings.is_production)
    settings.require_secrets()

    runtime = Runtime(
        settings=settings,
        redis=aioredis.from_url(settings.redis_url, decode_responses=True),
        qdrant=AsyncQdrantClient(url=settings.qdrant_url, timeout=30),
        neo4j=AsyncGraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_user, settings.neo4j_password.get_secret_value()),
        ),
        policy=PolicyEngine(settings.opa_url),
    )
    async with session_scope() as db:
        version = await PolicyStore(db, runtime.policy).bootstrap()
    log.info("runtime.started", policy_version=version.version, env=settings.app_env)
    _runtime = runtime
    return runtime


def get_runtime() -> Runtime:
    if _runtime is None:
        raise RuntimeError("Runtime has not been started")
    return _runtime


async def stop_runtime() -> None:
    global _runtime
    if _runtime is not None:
        await _runtime.close()
        _runtime = None
