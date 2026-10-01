"""Stand-alone ingestion worker:  python -m pramana.worker

Run one or more of these next to the API (with EMBEDDED_WORKER=false) when ingestion load
should not share a process with request handling. Workers coordinate through the database.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal

from pramana.knowledge.jobs import worker_loop
from pramana.runtime import start_runtime, stop_runtime


async def main() -> None:
    runtime = await start_runtime()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        # Windows has no loop signal handlers; Ctrl+C raises KeyboardInterrupt there instead.
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(signum, stop.set)
    try:
        await worker_loop(runtime.services.pipeline, stop)
    finally:
        await stop_runtime()


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
