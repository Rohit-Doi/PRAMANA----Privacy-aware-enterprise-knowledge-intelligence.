"""Process-wide runtime: the long-lived clients shared by the API and the worker."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Any

import redis.asyncio as aioredis
from neo4j import AsyncDriver, AsyncGraphDatabase
from qdrant_client import AsyncQdrantClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pramana.auth.models import User
from pramana.governance.models import AppSetting
from pramana.graph.builder import GraphBuilder
from pramana.graph.entity_index import EntityIndex
from pramana.graph.extraction import GraphExtractor, get_graph_extractor
from pramana.graph.store import GraphStore
from pramana.knowledge.ingestion import IngestionPipeline
from pramana.knowledge.storage import LocalBlobStore
from pramana.privacy.pii import PiiEngine, get_pii_engine
from pramana.privacy.policy import PolicyEngine, PolicyStore
from pramana.retrieval.local_models import RetrievalModels, get_retrieval_models
from pramana.retrieval.retriever import HybridRetriever
from pramana.retrieval.vector_store import VectorStore
from pramana.shared.config import Settings, get_settings
from pramana.shared.db import dispose_engine, session_scope
from pramana.shared.logging import configure_logging, get_logger

log = get_logger(__name__)

KNOWN_NAMES_SETTING = "pii.known_names"


@dataclass(slots=True)
class Services:
    """Application services built on the clients. Models inside them load on first use."""

    models: RetrievalModels
    pii: PiiEngine
    blobs: LocalBlobStore
    vectors: VectorStore
    entity_index: EntityIndex
    extractor: GraphExtractor
    graph: GraphBuilder
    graph_store: GraphStore
    retriever: HybridRetriever
    pipeline: IngestionPipeline


@dataclass(slots=True)
class Runtime:
    settings: Settings
    redis: aioredis.Redis
    qdrant: AsyncQdrantClient
    neo4j: AsyncDriver
    policy: PolicyEngine
    services: Services
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

    qdrant = AsyncQdrantClient(url=settings.qdrant_url, timeout=30)
    neo4j = AsyncGraphDatabase.driver(
        settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password.get_secret_value())
    )
    policy = PolicyEngine(settings.opa_url)
    runtime = Runtime(
        settings=settings,
        redis=aioredis.from_url(settings.redis_url, decode_responses=True),
        qdrant=qdrant,
        neo4j=neo4j,
        policy=policy,
        services=_build_services(settings, qdrant, neo4j, policy),
    )
    await runtime.services.vectors.ensure_collection()
    await runtime.services.entity_index.ensure_collection()
    await runtime.services.graph.ensure_schema()
    async with session_scope() as db:
        version = await PolicyStore(db, runtime.policy).bootstrap()
        await _load_known_names(db, runtime.services.pii)
    log.info("runtime.started", policy_version=version.version, env=settings.app_env)
    _runtime = runtime
    return runtime


def _build_services(
    settings: Settings, qdrant: AsyncQdrantClient, neo4j: AsyncDriver, policy: PolicyEngine
) -> Services:
    models = get_retrieval_models()
    pii = get_pii_engine()
    blobs = LocalBlobStore(settings.storage_dir)
    vectors = VectorStore(qdrant, settings.qdrant_collection, settings.embedding_dim)
    entity_index = EntityIndex(qdrant, settings.qdrant_entity_collection, settings.embedding_dim, models)
    extractor = get_graph_extractor()
    graph = GraphBuilder(neo4j, extractor, entity_index=entity_index)
    graph_store = GraphStore(neo4j, entity_index, extractor)
    retriever = HybridRetriever(models=models, vectors=vectors, graph=graph_store, policy=policy)
    pipeline = IngestionPipeline(models=models, vector_store=vectors, blob_store=blobs, pii=pii, graph=graph)
    return Services(
        models=models, pii=pii, blobs=blobs, vectors=vectors, entity_index=entity_index,
        extractor=extractor, graph=graph, graph_store=graph_store, retriever=retriever, pipeline=pipeline,
    )


async def _load_known_names(db: AsyncSession, pii: PiiEngine) -> None:
    """Names known to be people (the employee directory) sharpen personal-name detection."""
    setting = await db.get(AppSetting, KNOWN_NAMES_SETTING)
    users = (await db.execute(select(User.full_name))).scalars().all()
    names = set(users) | set(setting.value if setting is not None else [])
    pii.set_known_names(names)


def get_runtime() -> Runtime:
    if _runtime is None:
        raise RuntimeError("Runtime has not been started")
    return _runtime


async def stop_runtime() -> None:
    global _runtime
    if _runtime is not None:
        await _runtime.close()
        _runtime = None
