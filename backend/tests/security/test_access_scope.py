"""The access rule exists in three forms: Rego, the Python reference, and compiled store filters.
These tests prove that they agree for every role, department, region and sensitivity."""

from __future__ import annotations

import itertools
import uuid

import pytest
from qdrant_client import AsyncQdrantClient
from qdrant_client import models as qm
from sqlalchemy import select

from pramana.auth.principal import Principal
from pramana.auth.seed import ROLES
from pramana.knowledge.models import Document
from pramana.privacy.policy import PolicyStore, ResourceRef
from pramana.privacy.scope import EMPTY_SCOPE
from pramana.runtime import Runtime
from pramana.shared.db import session_scope

pytestmark = pytest.mark.integration

DEPARTMENTS = ["hr", "finance", "security", "company"]
REGIONS = ["global", "amer", "emea", "apac"]
RESOURCES = [
    ResourceRef(id=f"{d}|{rank}|{region}", department=d, sensitivity_rank=rank, region=region)
    for d, rank, region in itertools.product(DEPARTMENTS, range(4), REGIONS)
]


def make_principal(role: str, department: str, region: str, *, extra: tuple[str, ...] = ()) -> Principal:
    spec = ROLES[role]
    return Principal(
        user_id=uuid.uuid4(), email=f"{role}@test", full_name=role, role=role, department=department,
        region=region, max_rank_own_dept=int(spec.own), max_rank_other_dept=int(spec.other),
        extra_departments=extra, permissions=frozenset(str(p) for p in spec.permissions),
    )


PRINCIPALS = [
    make_principal(role, department, region)
    for role, department, region in itertools.product(ROLES, ["hr", "finance", "it"], ["amer", "emea", "apac"])
] + [make_principal("manager", "hr", "emea", extra=("finance",))]


async def test_python_reference_agrees_with_rego_for_every_combination(runtime: Runtime) -> None:
    checked = 0
    for principal in PRINCIPALS:
        scope = await runtime.policy.scope(principal)
        allowed = await runtime.policy.allowed_ids(principal, RESOURCES)
        for resource in RESOURCES:
            expected = resource.id in allowed
            actual = scope.permits(
                department=resource.department, sensitivity_rank=resource.sensitivity_rank, region=resource.region
            )
            assert actual == expected, f"{principal.role}/{principal.department}/{principal.region} vs {resource.id}"
            checked += 1
    assert checked == len(PRINCIPALS) * len(RESOURCES)


async def test_sql_filter_agrees_with_rego(runtime: Runtime) -> None:
    async with session_scope() as db:
        for resource in RESOURCES:
            db.add(Document(
                title=resource.id, source_type="test", collection="scope-test", department=resource.department,
                sensitivity_rank=resource.sensitivity_rank, region=resource.region,
                content_hash=uuid.uuid4().hex, status="ready",
            ))
    try:
        for principal in PRINCIPALS:
            scope = await runtime.policy.scope(principal)
            allowed = await runtime.policy.allowed_ids(principal, RESOURCES)
            async with session_scope() as db:
                rows = await db.execute(
                    select(Document.title).where(Document.collection == "scope-test", scope.sql_predicate())
                )
                assert set(rows.scalars()) == allowed
    finally:
        async with session_scope() as db:
            for doc in (await db.execute(select(Document).where(Document.collection == "scope-test"))).scalars():
                await db.delete(doc)


async def test_qdrant_filter_agrees_with_rego(runtime: Runtime) -> None:
    client: AsyncQdrantClient = runtime.qdrant
    name = "test_scope_filter"
    if await client.collection_exists(name):
        await client.delete_collection(name)
    await client.create_collection(name, vectors_config=qm.VectorParams(size=4, distance=qm.Distance.COSINE))
    try:
        await client.upsert(name, points=[
            qm.PointStruct(
                id=str(uuid.uuid5(uuid.NAMESPACE_URL, r.id)), vector=[0.1, 0.2, 0.3, 0.4],
                payload={"rid": r.id, "department": r.department, "sensitivity_rank": r.sensitivity_rank, "region": r.region},
            )
            for r in RESOURCES
        ], wait=True)
        for principal in PRINCIPALS:
            scope = await runtime.policy.scope(principal)
            allowed = await runtime.policy.allowed_ids(principal, RESOURCES)
            points, _ = await client.scroll(name, scroll_filter=scope.qdrant_filter(), limit=1000, with_payload=True)
            assert {p.payload["rid"] for p in points if p.payload} == allowed
    finally:
        await client.delete_collection(name)


async def test_neo4j_predicate_agrees_with_rego(runtime: Runtime) -> None:
    label = "ScopeTestNode"
    async with runtime.neo4j.session() as session:
        await session.run(f"MATCH (n:{label}) DETACH DELETE n")
        await session.run(
            f"UNWIND $rows AS row CREATE (n:{label}) SET n = row",
            rows=[{"rid": r.id, "department": r.department, "sensitivity_rank": r.sensitivity_rank, "region": r.region}
                  for r in RESOURCES],
        )
        try:
            for principal in PRINCIPALS:
                scope = await runtime.policy.scope(principal)
                allowed = await runtime.policy.allowed_ids(principal, RESOURCES)
                result = await session.run(
                    f"MATCH (n:{label}) WHERE {scope.cypher_predicate('n')} RETURN n.rid AS rid",
                    **scope.cypher_params(),
                )
                assert {record["rid"] async for record in result} == allowed
        finally:
            await session.run(f"MATCH (n:{label}) DETACH DELETE n")


async def test_unknown_role_gets_empty_scope_everywhere(runtime: Runtime) -> None:
    ghost = Principal(
        user_id=uuid.uuid4(), email="ghost@test", full_name="ghost", role="does_not_exist", department="hr",
        region="emea", max_rank_own_dept=3, max_rank_other_dept=3, permissions=frozenset({"region:all"}),
    )
    scope = await runtime.policy.scope(ghost)
    assert scope == EMPTY_SCOPE
    assert await runtime.policy.allowed_ids(ghost, RESOURCES) == set()
    assert not any(
        scope.permits(department=r.department, sensitivity_rank=r.sensitivity_rank, region=r.region) for r in RESOURCES
    )


async def test_administrator_has_no_elevated_content_access(runtime: Runtime) -> None:
    admin = make_principal("admin", "it", "amer")
    allowed = await runtime.policy.allowed_ids(admin, RESOURCES)
    assert all(int(rid.split("|")[1]) <= 1 for rid in allowed)


async def test_role_change_publishes_a_new_policy_version(runtime: Runtime) -> None:
    async with session_scope() as db:
        store = PolicyStore(db, runtime.policy)
        before = await store.active()
        assert before is not None
        after = await store.publish(note="config change", created_by=None, config={"egress_max_rank": 1})
        assert after.version == before.version + 1 and after.is_active
        assert after.data["config"]["egress_max_rank"] == 1
        context = await runtime.policy.access_context(make_principal("employee", "hr", "emea"))
        assert context.egress.max_rank == 1 and context.scope.policy_version == after.version
        restored = await store.publish(note="restore", created_by=None, config={"egress_max_rank": 2})
        assert restored.version == after.version + 1
