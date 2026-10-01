"""Background job queue on Postgres.

Workers claim one queued row at a time with ``SELECT … FOR UPDATE SKIP LOCKED``, so any
number of workers can run without a broker and without ever taking the same job twice.
"""

from __future__ import annotations

import asyncio
import os
import socket
import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import select, update

from pramana.knowledge.ingestion import IngestionPipeline
from pramana.knowledge.models import IngestionJob
from pramana.shared.db import session_scope, utcnow
from pramana.shared.logging import get_logger

log = get_logger(__name__)

MAX_ATTEMPTS = 3
STALE_AFTER = timedelta(minutes=45)


def worker_name() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


async def claim_next(worker: str) -> uuid.UUID | None:
    async with session_scope() as db:
        job = (
            await db.execute(
                select(IngestionJob)
                .where(IngestionJob.status == "queued")
                .order_by(IngestionJob.created_at)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
        ).scalar_one_or_none()
        if job is None:
            return None
        job.status = "running"
        job.stage = "starting"
        job.attempts += 1
        job.locked_by = worker
        job.started_at = utcnow()
        job.error = None
        return job.id


async def requeue_stale() -> int:
    """Jobs whose worker died are put back in the queue (or failed after too many attempts)."""
    cutoff = utcnow() - STALE_AFTER
    async with session_scope() as db:
        rows = (
            await db.execute(
                select(IngestionJob).where(IngestionJob.status == "running", IngestionJob.started_at < cutoff)
            )
        ).scalars().all()
        for job in rows:
            if job.attempts >= MAX_ATTEMPTS:
                job.status, job.error, job.finished_at = "failed", "Worker stopped responding", utcnow()
            else:
                job.status, job.locked_by = "queued", None
        return len(rows)


async def _report(job_id: uuid.UUID, stage: str, percent: int, message: str) -> None:
    async with session_scope() as db:
        job = await db.get(IngestionJob, job_id)
        if job is None:
            return
        job.stage = stage
        job.progress = percent
        event: dict[str, Any] = {"at": utcnow().isoformat(), "stage": stage, "progress": percent, "message": message}
        job.events = [*job.events, event][-50:]


async def run_job(job_id: uuid.UUID, pipeline: IngestionPipeline) -> bool:
    async with session_scope() as db:
        job = await db.get(IngestionJob, job_id)
        if job is None:
            return False
        document_id = job.document_id

    async def progress(stage: str, percent: int, message: str) -> None:
        await _report(job_id, stage, percent, message)

    try:
        result = await pipeline.ingest(document_id, progress=progress)
    except Exception as exc:
        log.warning("job.failed", job_id=str(job_id), document_id=str(document_id), error=repr(exc))
        async with session_scope() as db:
            await db.execute(
                update(IngestionJob).where(IngestionJob.id == job_id)
                .values(status="failed", error=str(exc)[:1000], finished_at=utcnow(), stage="failed")
            )
        return False
    await _report(job_id, "done", 100, f"{result.chunk_count} passages, {result.entity_count} entities")
    async with session_scope() as db:
        await db.execute(
            update(IngestionJob).where(IngestionJob.id == job_id)
            .values(status="succeeded", finished_at=utcnow(), stage="done", progress=100)
        )
    log.info("job.succeeded", job_id=str(job_id), chunks=result.chunk_count, timings=result.timings_ms)
    return True


async def worker_loop(pipeline: IngestionPipeline, stop: asyncio.Event, *, poll_seconds: float = 2.0) -> None:
    name = worker_name()
    log.info("worker.started", worker=name)
    idle_cycles = 0
    while not stop.is_set():
        try:
            if idle_cycles % 60 == 0:
                await requeue_stale()
            job_id = await claim_next(name)
            if job_id is None:
                idle_cycles += 1
                try:
                    await asyncio.wait_for(stop.wait(), timeout=poll_seconds)
                except TimeoutError:
                    pass
                continue
            idle_cycles = 1
            await run_job(job_id, pipeline)
        except Exception as exc:  # the loop itself must survive a database blip
            log.error("worker.loop_error", error=repr(exc))
            await asyncio.sleep(poll_seconds)
    log.info("worker.stopped", worker=name)
