"""Blob storage for original files and extracted text.

``BlobStore`` is the interface; ``LocalBlobStore`` keeps files on disk. An S3-compatible
implementation can replace it without touching the callers.
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path, PurePosixPath
from typing import Protocol

from pramana.shared.errors import NotFound


class BlobStore(Protocol):
    async def put(self, key: str, data: bytes) -> None: ...

    async def get(self, key: str) -> bytes: ...

    async def delete_prefix(self, prefix: str) -> None: ...


class LocalBlobStore:
    def __init__(self, root: Path) -> None:
        self._root = root.resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        parts = PurePosixPath(key).parts
        if not parts or any(part in ("..", "") or ":" in part for part in parts) or key.startswith("/"):
            raise ValueError(f"Invalid storage key: {key!r}")
        path = (self._root / Path(*parts)).resolve()
        if not path.is_relative_to(self._root):
            raise ValueError(f"Invalid storage key: {key!r}")
        return path

    async def put(self, key: str, data: bytes) -> None:
        path = self._path(key)

        def write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)

        await asyncio.to_thread(write)

    async def get(self, key: str) -> bytes:
        path = self._path(key)
        if not path.is_file():
            raise NotFound("Stored file not found")
        return await asyncio.to_thread(path.read_bytes)

    async def delete_prefix(self, prefix: str) -> None:
        path = self._path(prefix)
        await asyncio.to_thread(shutil.rmtree, path, True)
