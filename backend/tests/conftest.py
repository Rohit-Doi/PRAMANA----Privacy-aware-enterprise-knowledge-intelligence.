"""Shared fixtures.

Integration tests run against the Docker datastores but in an isolated database
(``pramana_test``) that is dropped and rebuilt at the start of every session.
"""

from __future__ import annotations

import asyncio
import os

# Must be set before any pramana module reads its settings.
os.environ["APP_ENV"] = "test"
os.environ["POSTGRES_DB"] = "pramana_test"
os.environ["QDRANT_COLLECTION"] = "test_pramana_chunks"
os.environ["QDRANT_ENTITY_COLLECTION"] = "test_pramana_entities"
os.environ["RATE_LIMIT_PER_MINUTE"] = "1000000"
os.environ["AUTH_RATE_LIMIT_PER_MINUTE"] = "1000000"

from collections.abc import AsyncIterator  # noqa: E402
from pathlib import Path  # noqa: E402

import asyncpg  # noqa: E402
import httpx  # noqa: E402
import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402

from pramana.auth.seed import PERSONAS, seed_personas, seed_reference_data  # noqa: E402
from pramana.runtime import Runtime, start_runtime, stop_runtime  # noqa: E402
from pramana.shared.config import get_settings  # noqa: E402
from pramana.shared.db import session_scope  # noqa: E402

BACKEND_DIR = Path(__file__).resolve().parents[1]
TEST_PASSWORD = "Test-password-123456"
CSRF = {"X-Requested-With": "pramana-web"}
EMAIL = {p.role if p.department not in ("finance", "it") or p.role == "admin" else f"{p.role}_{p.department}": p.email
         for p in PERSONAS}


async def _rebuild_database() -> None:
    settings = get_settings()
    connection = await asyncpg.connect(
        host=settings.postgres_host, port=settings.postgres_port, user=settings.postgres_user,
        password=settings.postgres_password.get_secret_value(), database="postgres",
    )
    try:
        await connection.execute(f'DROP DATABASE IF EXISTS "{settings.postgres_db}" WITH (FORCE)')
        await connection.execute(f'CREATE DATABASE "{settings.postgres_db}"')
    finally:
        await connection.close()


def _migrate() -> None:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    command.upgrade(config, "head")


@pytest.fixture(scope="session")
async def runtime() -> AsyncIterator[Runtime]:
    assert get_settings().postgres_db == "pramana_test", "tests must never touch the main database"
    await _rebuild_database()
    await asyncio.to_thread(_migrate)  # Alembic drives its own event loop
    async with session_scope() as db:
        await seed_reference_data(db)
        await seed_personas(db, TEST_PASSWORD)
    rt = await start_runtime()
    yield rt
    await stop_runtime()


@pytest.fixture
async def client(runtime: Runtime) -> AsyncIterator[httpx.AsyncClient]:
    from pramana.api.app import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as http:
        yield http


async def login(client: httpx.AsyncClient, persona: str, password: str = TEST_PASSWORD) -> dict[str, str]:
    """Sign in as a seeded persona and return the Authorization header."""
    response = await client.post("/api/v1/auth/login", json={"email": EMAIL[persona], "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}
