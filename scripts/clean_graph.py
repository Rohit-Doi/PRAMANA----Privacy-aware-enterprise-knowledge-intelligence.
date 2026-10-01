"""Remove malformed entity names from the knowledge graph.

    python scripts/clean_graph.py

Entities whose names begin with a list marker ("3) Limit"), a table pipe or other symbols
are extraction noise. They are deleted from the graph and from the entity index; passages
and all other entities are untouched. Safe to re-run.
"""

from __future__ import annotations

import asyncio
import sys

from pramana.runtime import start_runtime, stop_runtime

NOISE_PATTERN = r"^(\(?[0-9a-zA-Z]{1,2}[.)]\s.*|[^A-Za-z0-9].*|.{0,2}|[0-9\s.,%$-]+)$"


async def main() -> int:
    runtime = await start_runtime()
    try:
        removed_total = 0
        async with runtime.neo4j.session() as session:
            while True:
                result = await session.run(
                    """
                    MATCH (e:Entity) WHERE e.name =~ $pattern
                    WITH e LIMIT 2000
                    WITH e, e.key AS key
                    DETACH DELETE e
                    RETURN collect(key) AS keys
                    """,
                    pattern=NOISE_PATTERN,
                )
                record = await result.single()
                keys = list(record["keys"]) if record else []
                if not keys:
                    break
                await runtime.services.entity_index.delete(keys)
                removed_total += len(keys)
        print(f"removed {removed_total} malformed entities")
        print("graph:", await runtime.services.graph.counts())
    finally:
        await stop_runtime()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
