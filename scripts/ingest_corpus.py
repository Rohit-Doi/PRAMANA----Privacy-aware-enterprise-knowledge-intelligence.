"""Load the demo corpus into the platform: register every document, then ingest.

    python scripts/ingest_corpus.py                       # everything not yet ingested
    python scripts/ingest_corpus.py --collections aurelia hr-directory
    python scripts/ingest_corpus.py --limit 50 --no-graph

Idempotent: a file already registered (same content, same department) is skipped, and only
queued ingestion jobs are run. Stop the API server first, or its worker will share the queue.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time

from sqlalchemy import select, update

from pramana.governance.models import AppSetting
from pramana.knowledge.connectors import manifest_documents, sql_employee_documents
from pramana.knowledge.jobs import run_job, worker_name
from pramana.knowledge.models import Document, IngestionJob
from pramana.knowledge.service import DocumentService, NewDocument
from pramana.runtime import KNOWN_NAMES_SETTING, start_runtime, stop_runtime
from pramana.shared.config import get_settings
from pramana.shared.db import session_scope, utcnow
from pramana.shared.errors import Conflict

ORDER = ["aurelia", "hr-directory", "regulations", "contracts", "handbook", "emails"]


async def register_all(collections: set[str] | None) -> int:
    runtime = await start_runtime()
    services = runtime.services
    data_dir = get_settings().data_dir
    items: list[tuple[NewDocument, str | None, str]] = []  # document, supersedes path, own path

    for manifest in (data_dir / "synthetic" / "manifest.jsonl", data_dir / "raw" / "manifest.jsonl"):
        if manifest.exists():
            for new, row in manifest_documents(data_dir, manifest):
                items.append((new, row.get("supersedes"), row["path"]))
    hr_database = data_dir / "synthetic" / "hr.sqlite"
    if hr_database.exists():
        employees = list(sql_employee_documents(f"sqlite:///{hr_database.as_posix()}"))
        items.extend((new, None, new.source_uri) for new in employees)
        async with session_scope() as db:
            names = sorted({str(new.meta["full_name"]) for new in employees if new.meta})
            setting = await db.get(AppSetting, KNOWN_NAMES_SETTING)
            if setting is None:
                db.add(AppSetting(key=KNOWN_NAMES_SETTING, value=names, updated_at=utcnow()))
            else:
                setting.value, setting.updated_at = names, utcnow()
        services.pii.set_known_names(set(names))

    created = 0
    by_path: dict[str, object] = {}
    # Older versions first, so that a superseding document can point at its predecessor.
    items.sort(key=lambda item: item[1] is not None)
    for new, supersedes, own_path in items:
        if collections and new.collection not in collections:
            continue
        async with session_scope() as db:
            service = DocumentService(db, blobs=services.blobs, vectors=services.vectors, graph=services.graph)
            if supersedes:
                previous = (
                    await db.execute(select(Document.id).where(Document.meta["manifest_path"].astext == supersedes))
                ).scalar_one_or_none()
                if previous is not None:
                    new = NewDocument(**{**_fields(new), "supersedes_id": previous})
            try:
                document, _ = await service.register(new, owner=None)
                by_path[own_path] = document.id
                created += 1
            except Conflict:
                pass
    return created


def _fields(new: NewDocument) -> dict[str, object]:
    return {name: getattr(new, name) for name in new.__dataclass_fields__}


async def ingest_pending(collections: set[str] | None, limit: int | None, build_graph: bool) -> tuple[int, int]:
    """Work through the queued ingestion jobs, in a sensible order, in this process."""
    runtime = await start_runtime()
    pipeline = runtime.services.pipeline
    async with session_scope() as db:
        rows = (
            await db.execute(
                select(IngestionJob.id, Document.collection, Document.title)
                .join(Document, Document.id == IngestionJob.document_id)
                # "running" rows are leftovers of an interrupted run: this script is the only worker.
                .where(IngestionJob.status.in_(("queued", "running")), Document.status != "deleted")
            )
        ).all()
    if collections:
        rows = [r for r in rows if r.collection in collections]
    rows.sort(key=lambda r: (ORDER.index(r.collection) if r.collection in ORDER else len(ORDER), r.title))
    if limit:
        rows = rows[:limit]

    ok = failed = 0
    started = time.perf_counter()
    for index, row in enumerate(rows, start=1):
        async with session_scope() as db:
            await db.execute(
                update(IngestionJob)
                .where(IngestionJob.id == row.id)
                .values(status="running", locked_by=worker_name(), started_at=utcnow())
            )
        if await run_job(row.id, pipeline, build_graph=build_graph):
            ok += 1
        else:
            failed += 1
            print(f"  FAILED {row.collection}: {row.title[:70]!r}", flush=True)
        if index % 25 == 0 or index == len(rows):
            elapsed = time.perf_counter() - started
            print(
                f"  {index}/{len(rows)} documents, {elapsed:.0f}s ({elapsed / index:.2f}s per document), {failed} failed",
                flush=True,
            )
    return ok, failed


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--collections", nargs="*", default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--no-graph", action="store_true", help="index vectors only; build the graph later")
    args = parser.parse_args()
    collections = set(args.collections) if args.collections else None
    try:
        created = await register_all(collections)
        print(f"registered {created} new documents")
        ok, failed = await ingest_pending(collections, args.limit, build_graph=not args.no_graph)
        print(f"ingested {ok} documents, {failed} failed")
        runtime = await start_runtime()
        print("graph:", await runtime.services.graph.counts())
        print("vectors:", await runtime.services.vectors.count())
    finally:
        await stop_runtime()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
