"""Re-run ingestion for documents that are already in the platform.

    python scripts/reindex.py --collections aurelia blind      # parse, chunk, embed and graph again
    python scripts/reindex.py --collections blind --drop pdf   # remove the PDFs of a collection instead

Needed after a change to how documents are read (for example: tables are now stored as one
statement per row). ``--drop`` removes documents whose stored file has the given extension, so
that a rebuilt file can be registered afresh by ``ingest_corpus.py``. Stop the API first.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import select

from pramana.knowledge.jobs import run_job
from pramana.knowledge.models import Document
from pramana.knowledge.service import DocumentService
from pramana.runtime import start_runtime, stop_runtime
from pramana.shared.db import session_scope


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--collections", nargs="+", required=True)
    parser.add_argument("--drop", default=None, help="file extension (without dot) of documents to remove")
    args = parser.parse_args()
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

    runtime = await start_runtime()
    services = runtime.services
    try:
        async with session_scope() as db:
            rows = (
                (
                    await db.execute(
                        select(Document).where(Document.collection.in_(args.collections), Document.status != "deleted")
                    )
                )
                .scalars()
                .all()
            )
            service = DocumentService(db, blobs=services.blobs, vectors=services.vectors, graph=services.graph)
            job_ids = []
            dropped = 0
            for document in rows:
                filename = str(document.meta.get("filename") or document.storage_key)
                if args.drop:
                    if filename.lower().endswith("." + args.drop.lower()):
                        await service.delete(document)
                        # A rebuilt file will be registered under the same manifest path.
                        document.meta = {k: v for k, v in document.meta.items() if k != "manifest_path"}
                        dropped += 1
                    continue
                job_ids.append((await service.queue_reindex(document)).id)
        if args.drop:
            print(f"removed {dropped} document(s)")
            return 0
        failed = 0
        for index, job_id in enumerate(job_ids, start=1):
            ok = await run_job(job_id, services.pipeline)
            failed += 0 if ok else 1
            if index % 10 == 0 or index == len(job_ids):
                print(f"  {index}/{len(job_ids)} re-indexed", flush=True)
        print(f"re-indexed {len(job_ids)} document(s), {failed} failed")
        return 1 if failed else 0
    finally:
        await stop_runtime()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
