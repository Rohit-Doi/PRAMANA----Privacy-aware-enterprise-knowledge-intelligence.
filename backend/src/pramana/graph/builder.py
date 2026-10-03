"""Writes the knowledge graph.

Model
    (:Document {id, title, department, sensitivity_rank, region, …})
        -[:HAS_CHUNK]-> (:Chunk {id, document_id, department, sensitivity_rank, region, is_current})
    (:Chunk) -[:MENTIONS {confidence}]-> (:Entity {key, name, type})
    (:Entity) -[:REL {type, chunk_id, document_id, confidence,
                      department, sensitivity_rank, region}]-> (:Entity)

Every MENTIONS and REL edge is tied to the passage it came from and carries that passage's
access attributes. A traversal therefore applies the same access filter as the vector
search, and an entity is visible to a user only through a passage that user may read.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from typing import Any

from neo4j import AsyncDriver

from pramana.graph.entity_index import EntityIndex
from pramana.graph.extraction import ChunkExtraction, GraphExtractor, entity_key
from pramana.shared.logging import get_logger

log = get_logger(__name__)

SCHEMA_STATEMENTS = (
    "CREATE CONSTRAINT document_id IF NOT EXISTS FOR (d:Document) REQUIRE d.id IS UNIQUE",
    "CREATE CONSTRAINT chunk_id IF NOT EXISTS FOR (c:Chunk) REQUIRE c.id IS UNIQUE",
    "CREATE CONSTRAINT entity_key IF NOT EXISTS FOR (e:Entity) REQUIRE e.key IS UNIQUE",
    "CREATE INDEX chunk_document IF NOT EXISTS FOR (c:Chunk) ON (c.document_id)",
    "CREATE INDEX rel_document IF NOT EXISTS FOR ()-[r:REL]-() ON (r.document_id)",
    "CREATE INDEX entity_type IF NOT EXISTS FOR (e:Entity) ON (e.type)",
    "CREATE FULLTEXT INDEX entity_name IF NOT EXISTS FOR (e:Entity) ON EACH [e.name]",
)

# Which extractor each collection gets when a whole corpus is loaded. Single uploads use "full".
DEFAULT_MODES = {"aurelia": "full", "uploads": "full", "blind": "full", "blind2": "full", "blind3": "full"}
BULK_MODE = "fast"


@dataclass(frozen=True, slots=True)
class GraphStats:
    entities: int
    relations: int


class GraphBuilder:
    def __init__(
        self,
        driver: AsyncDriver,
        extractor: GraphExtractor,
        *,
        entity_index: EntityIndex | None = None,
    ) -> None:
        self._driver = driver
        self._extractor = extractor
        self._entity_index = entity_index

    async def ensure_schema(self) -> None:
        async with self._driver.session() as session:
            for statement in SCHEMA_STATEMENTS:
                await session.run(statement)

    def mode_for(self, collection: str) -> str:
        return DEFAULT_MODES.get(collection, BULK_MODE)

    async def index_document(
        self, document: dict[str, Any], chunks: list[tuple[uuid.UUID, str]], *, mode: str | None = None
    ) -> GraphStats:
        mode = mode or self.mode_for(str(document.get("collection", "")))
        extractions = await asyncio.to_thread(self._extractor.extract, [text for _, text in chunks], mode=mode)
        return await self.write_document(document, [cid for cid, _ in chunks], extractions)

    async def write_document(
        self, document: dict[str, Any], chunk_ids: list[uuid.UUID], extractions: list[ChunkExtraction]
    ) -> GraphStats:
        access = {
            "department": document["department"],
            "sensitivity_rank": document["sensitivity_rank"],
            "region": document["region"],
            "is_current": document.get("is_current", True),
        }
        chunk_rows = [{"id": str(cid), "ordinal": i} for i, cid in enumerate(chunk_ids)]
        entities: dict[str, dict[str, Any]] = {}
        mentions: list[dict[str, Any]] = []
        relations: list[dict[str, Any]] = []
        for chunk_id, extraction in zip(chunk_ids, extractions, strict=True):
            for entity in extraction.entities:
                known = entities.get(entity.key)
                if known is None or entity.confidence > known["confidence"]:
                    entities[entity.key] = {
                        "key": entity.key,
                        "name": entity.name,
                        "type": entity.type,
                        "confidence": entity.confidence,
                    }
                mentions.append({"chunk_id": str(chunk_id), "key": entity.key, "confidence": entity.confidence})
            for relation in extraction.relations:
                relations.append(
                    {
                        "head": entity_key(relation.head),
                        "tail": entity_key(relation.tail),
                        "type": relation.type,
                        "confidence": relation.confidence,
                        "chunk_id": str(chunk_id),
                    }
                )

        document_id = str(document["id"])
        async with self._driver.session() as session:
            orphaned = await session.execute_write(
                _write_document,
                document,
                document_id,
                access,
                chunk_rows,
                list(entities.values()),
                mentions,
                relations,
            )
        if self._entity_index is not None:
            # Only typed entities are embedded; generic noun phrases are found by keyword.
            typed = [e for e in entities.values() if e["type"] != "concept"]
            await self._entity_index.upsert(typed)
            await self._entity_index.delete(orphaned)
        return GraphStats(entities=len(entities), relations=len(relations))

    async def update_document_labels(self, document_id: str, changes: dict[str, Any]) -> None:
        """Propagate a relabel to the document node, its chunks and the relations drawn from it."""
        allowed = {
            k: v for k, v in changes.items() if k in ("department", "sensitivity_rank", "region", "is_current", "title")
        }
        if not allowed:
            return
        edge_changes = {k: v for k, v in allowed.items() if k != "title"}
        async with self._driver.session() as session:
            await session.run("MATCH (d:Document {id: $id}) SET d += $changes", id=document_id, changes=allowed)
            if edge_changes:
                await session.run(
                    "MATCH (c:Chunk {document_id: $id}) SET c += $changes", id=document_id, changes=edge_changes
                )
                await session.run(
                    "MATCH ()-[r:REL {document_id: $id}]->() SET r += $changes", id=document_id, changes=edge_changes
                )

    async def delete_document(self, document_id: str) -> None:
        async with self._driver.session() as session:
            orphaned = await session.execute_write(_delete_document, document_id)
        if self._entity_index is not None:
            await self._entity_index.delete(orphaned)

    async def counts(self) -> dict[str, int]:
        async with self._driver.session() as session:
            result = await session.run(
                """
                CALL () { MATCH (d:Document) RETURN count(d) AS documents }
                CALL () { MATCH (c:Chunk) RETURN count(c) AS chunks }
                CALL () { MATCH (e:Entity) RETURN count(e) AS entities }
                CALL () { MATCH ()-[m:MENTIONS]->() RETURN count(m) AS mentions }
                CALL () { MATCH ()-[r:REL]->() RETURN count(r) AS relations }
                RETURN documents, chunks, entities, mentions, relations
                """
            )
            record = await result.single()
            return dict(record) if record else {}


async def _mentioned_keys(tx: Any, document_id: str) -> list[str]:
    result = await tx.run(
        "MATCH (:Chunk {document_id: $id})-[:MENTIONS]->(e:Entity) RETURN collect(DISTINCT e.key) AS keys",
        id=document_id,
    )
    record = await result.single()
    return list(record["keys"]) if record else []


async def _drop_orphans(tx: Any, keys: list[str]) -> list[str]:
    """Delete entities no remaining passage mentions, so names of removed content do not linger."""
    if not keys:
        return []
    result = await tx.run(
        """
        UNWIND $keys AS key
        MATCH (e:Entity {key: key}) WHERE NOT (e)<-[:MENTIONS]-()
        WITH e, e.key AS removed
        DETACH DELETE e
        RETURN collect(removed) AS removed
        """,
        keys=keys,
    )
    record = await result.single()
    return list(record["removed"]) if record else []


async def _delete_document(tx: Any, document_id: str) -> list[str]:
    keys = await _mentioned_keys(tx, document_id)
    await tx.run("MATCH ()-[r:REL {document_id: $id}]->() DELETE r", id=document_id)
    await tx.run("MATCH (c:Chunk {document_id: $id}) DETACH DELETE c", id=document_id)
    await tx.run("MATCH (d:Document {id: $id}) DETACH DELETE d", id=document_id)
    return await _drop_orphans(tx, keys)


async def _write_document(
    tx: Any,
    document: dict[str, Any],
    document_id: str,
    access: dict[str, Any],
    chunk_rows: list[dict[str, Any]],
    entities: list[dict[str, Any]],
    mentions: list[dict[str, Any]],
    relations: list[dict[str, Any]],
) -> list[str]:
    # Re-indexing replaces the document's previous graph footprint.
    previous_keys = await _mentioned_keys(tx, document_id)
    await tx.run("MATCH ()-[r:REL {document_id: $id}]->() DELETE r", id=document_id)
    await tx.run("MATCH (c:Chunk {document_id: $id}) DETACH DELETE c", id=document_id)
    await tx.run(
        """
        MERGE (d:Document {id: $id})
        SET d.title = $title, d.collection = $collection, d.source_type = $source_type, d += $access
        """,
        id=document_id,
        title=document.get("title", ""),
        collection=document.get("collection", ""),
        source_type=document.get("source_type", ""),
        access=access,
    )
    await tx.run(
        """
        MATCH (d:Document {id: $id})
        UNWIND $chunks AS row
        CREATE (c:Chunk {id: row.id, ordinal: row.ordinal, document_id: $id})
        SET c += $access
        CREATE (d)-[:HAS_CHUNK]->(c)
        """,
        id=document_id,
        chunks=chunk_rows,
        access=access,
    )
    if entities:
        await tx.run(
            """
            UNWIND $entities AS row
            MERGE (e:Entity {key: row.key})
            ON CREATE SET e.name = row.name, e.type = row.type
            // A typed extraction replaces the generic label of the fast extractor.
            ON MATCH SET e.type = CASE WHEN e.type = 'concept' AND row.type <> 'concept' THEN row.type ELSE e.type END
            """,
            entities=entities,
        )
    if mentions:
        await tx.run(
            """
            UNWIND $mentions AS row
            MATCH (c:Chunk {id: row.chunk_id})
            MATCH (e:Entity {key: row.key})
            MERGE (c)-[m:MENTIONS]->(e)
            SET m.confidence = row.confidence
            """,
            mentions=mentions,
        )
    if relations:
        await tx.run(
            """
            UNWIND $relations AS row
            MATCH (h:Entity {key: row.head})
            MATCH (t:Entity {key: row.tail})
            CREATE (h)-[r:REL {type: row.type, chunk_id: row.chunk_id, document_id: $id,
                               confidence: row.confidence}]->(t)
            SET r += $access
            """,
            relations=relations,
            id=document_id,
            access=access,
        )
    return await _drop_orphans(tx, previous_keys)
