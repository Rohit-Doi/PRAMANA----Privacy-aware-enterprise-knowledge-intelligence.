"""Every read endpoint, as every persona: the answer is a success or a clean refusal, never a crash.

This catches the class of defect where a query is valid Python but invalid SQL, and checks
that permission failures are reported as RFC 9457 problems rather than as stack traces.
"""

from __future__ import annotations

import httpx
import pytest

from tests.conftest import EMAIL, TEST_PASSWORD, login

SERVICE_ACCOUNT = "employee_it"  # the helpdesk bot: it has a role and a scope, but no interactive sign-in
PERSONAS = sorted(set(EMAIL) - {SERVICE_ACCOUNT})


def _parameterless_get_paths(schema: dict) -> list[str]:  # type: ignore[type-arg]
    paths = []
    for path, operations in schema["paths"].items():
        if "{" in path or "get" not in operations:
            continue
        required = [p for p in operations["get"].get("parameters", []) if p.get("required")]
        if not required:
            paths.append(path)
    return sorted(paths)


@pytest.mark.parametrize("persona", PERSONAS)
async def test_read_endpoints_never_fail_with_a_server_error(client: httpx.AsyncClient, persona: str) -> None:
    schema = (await client.get("/openapi.json")).json()
    paths = _parameterless_get_paths(schema)
    assert len(paths) >= 20, "the API surface shrank unexpectedly"
    headers = await login(client, persona)

    failures = []
    for path in paths:
        response = await client.get(path, headers=headers)
        if response.status_code >= 500:
            failures.append((path, response.status_code, response.text[:200]))
        elif response.status_code in (401, 403):
            body = response.json()
            assert {"title", "status"} <= set(body), f"{path}: refusal is not a problem document"
    assert not failures, failures


async def test_service_accounts_cannot_sign_in_interactively(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/v1/auth/login", json={"email": EMAIL[SERVICE_ACCOUNT], "password": TEST_PASSWORD}
    )
    assert response.status_code == 401
    # The refusal is indistinguishable from a wrong password.
    assert response.json()["detail"] == "Incorrect email or password"


async def test_read_endpoints_require_a_session(client: httpx.AsyncClient) -> None:
    schema = (await client.get("/openapi.json")).json()
    open_paths = {"/healthz", "/readyz", "/metrics"}
    for path in _parameterless_get_paths(schema):
        if path in open_paths:
            continue
        response = await client.get(path)
        assert response.status_code == 401, f"{path} answered {response.status_code} without a session"


async def test_unknown_ids_are_not_found_rather_than_errors(client: httpx.AsyncClient) -> None:
    headers = await login(client, "executive")
    missing = "00000000-0000-0000-0000-000000000000"
    for path in (f"/api/v1/documents/{missing}", f"/api/v1/documents/{missing}/chunks", f"/api/v1/runs/{missing}"):
        response = await client.get(path, headers=headers)
        assert response.status_code in (403, 404), f"{path}: {response.status_code}"
    assert (await client.get("/api/v1/documents/not-a-uuid", headers=headers)).status_code == 422
