"""Authentication and session security."""

from __future__ import annotations

import base64
import json
import time
import uuid

import httpx
import jwt
import pyotp
import pytest
from sqlalchemy import select, text

from pramana.auth.models import Session, User
from pramana.governance.audit import AuditTrail
from pramana.shared.config import get_settings
from pramana.shared.db import session_scope
from tests.conftest import CSRF, EMAIL, TEST_PASSWORD, login

pytestmark = pytest.mark.integration
API = "/api/v1"


async def test_login_returns_identity_from_database(client: httpx.AsyncClient) -> None:
    headers = await login(client, "manager")
    me = (await client.get(f"{API}/auth/me", headers=headers)).json()
    assert me["role"] == "manager"
    assert me["department"] == "hr"
    assert me["max_rank_own_dept"] == 2 and me["max_rank_other_dept"] == 1


async def test_wrong_password_and_unknown_user_are_indistinguishable(client: httpx.AsyncClient) -> None:
    wrong = await client.post(f"{API}/auth/login", json={"email": EMAIL["analyst"], "password": "nope-nope-nope"})
    unknown = await client.post(
        f"{API}/auth/login", json={"email": "ghost@aurelia-systems.example", "password": "x" * 14}
    )
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["detail"] == unknown.json()["detail"]


async def test_requests_without_token_are_rejected(client: httpx.AsyncClient) -> None:
    assert (await client.get(f"{API}/auth/me")).status_code == 401
    assert (await client.get(f"{API}/users")).status_code == 401


@pytest.mark.parametrize("mutation", ["signature", "payload", "alg_none", "other_secret", "expired", "wrong_type"])
async def test_tampered_tokens_are_rejected(client: httpx.AsyncClient, mutation: str) -> None:
    headers = await login(client, "employee")
    token = headers["Authorization"].split()[1]
    header_b64, payload_b64, signature = token.split(".")
    payload = json.loads(base64.urlsafe_b64decode(payload_b64 + "=="))
    secret = get_settings().jwt_secret.get_secret_value()

    def b64(data: dict[str, object]) -> str:
        return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()

    if mutation == "signature":
        forged = f"{header_b64}.{payload_b64}.{signature[:-4]}AAAA"
    elif mutation == "payload":
        # Swap the subject for another user while keeping the original signature.
        forged = f"{header_b64}.{b64({**payload, 'sub': str(uuid.uuid4())})}.{signature}"
    elif mutation == "alg_none":
        forged = f"{b64({'alg': 'none', 'typ': 'JWT'})}.{payload_b64}."
    elif mutation == "other_secret":
        forged = jwt.encode(payload, "attacker-controlled-secret-of-sufficient-length", algorithm="HS256")
    elif mutation == "expired":
        forged = jwt.encode({**payload, "exp": int(time.time()) - 10}, secret, algorithm="HS256")
    else:
        forged = jwt.encode({**payload, "typ": "mfa_challenge"}, secret, algorithm="HS256")

    response = await client.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {forged}"})
    assert response.status_code == 401


async def test_validly_signed_token_for_revoked_session_is_rejected(client: httpx.AsyncClient) -> None:
    headers = await login(client, "employee")
    assert (await client.post(f"{API}/auth/logout", headers={**headers, **CSRF})).status_code == 204
    assert (await client.get(f"{API}/auth/me", headers=headers)).status_code == 401


async def test_identity_claims_in_request_body_are_ignored(client: httpx.AsyncClient) -> None:
    """The v1 flaw: clearance was read from the request. Here extra fields change nothing."""
    response = await client.post(
        f"{API}/auth/login",
        json={
            "email": EMAIL["employee"],
            "password": TEST_PASSWORD,
            "role": "executive",
            "clearance_level": 4,
            "max_rank_own_dept": 3,
            "department": "company",
        },
    )
    headers = {"Authorization": f"Bearer {response.json()['access_token']}"}
    me = (await client.get(f"{API}/auth/me", headers=headers)).json()
    assert me["role"] == "employee" and me["max_rank_own_dept"] == 1


async def test_employee_cannot_use_admin_endpoints(client: httpx.AsyncClient) -> None:
    headers = await login(client, "employee")
    assert (await client.get(f"{API}/users", headers=headers)).status_code == 403
    body = {
        "email": "x@aurelia-systems.example",
        "full_name": "X",
        "password": "a-long-password-1",
        "role": "executive",
        "department": "company",
    }
    assert (await client.post(f"{API}/users", headers=headers, json=body)).status_code == 403
    assert (
        await client.patch(f"{API}/roles/employee", headers=headers, json={"max_rank_own_dept": 3})
    ).status_code == 403


async def test_refresh_rotates_and_detects_reuse(client: httpx.AsyncClient) -> None:
    response = await client.post(f"{API}/auth/login", json={"email": EMAIL["analyst"], "password": TEST_PASSWORD})
    first_cookie = response.cookies["pramana_refresh"]

    assert (await client.post(f"{API}/auth/refresh")).status_code == 403, "CSRF header is required"

    rotated = await client.post(f"{API}/auth/refresh", headers=CSRF)
    assert rotated.status_code == 200
    second_cookie = rotated.cookies["pramana_refresh"]
    assert second_cookie != first_cookie

    # Replaying the old refresh token is treated as theft: the whole session is revoked.
    client.cookies.set("pramana_refresh", first_cookie, path="/api/v1/auth")
    assert (await client.post(f"{API}/auth/refresh", headers=CSRF)).status_code == 401
    client.cookies.set("pramana_refresh", second_cookie, path="/api/v1/auth")
    assert (await client.post(f"{API}/auth/refresh", headers=CSRF)).status_code == 401


async def test_refresh_token_is_stored_only_as_a_hash(client: httpx.AsyncClient) -> None:
    response = await client.post(f"{API}/auth/login", json={"email": EMAIL["analyst"], "password": TEST_PASSWORD})
    cookie = response.cookies["pramana_refresh"]
    async with session_scope() as db:
        stored = (await db.execute(select(Session.refresh_hash))).scalars().all()
    assert cookie not in stored and all(len(h) == 64 for h in stored)


async def test_mfa_enrolment_and_login(client: httpx.AsyncClient) -> None:
    headers = await login(client, "compliance_officer")
    setup = (await client.post(f"{API}/auth/mfa/setup", headers=headers)).json()
    totp = pyotp.TOTP(setup["secret"])
    assert (await client.post(f"{API}/auth/mfa/confirm", headers=headers, json={"code": "000000"})).status_code == 422
    confirm = await client.post(f"{API}/auth/mfa/confirm", headers=headers, json={"code": totp.now()})
    recovery_codes = confirm.json()["recovery_codes"]
    assert len(recovery_codes) == 8

    # Password alone no longer yields an access token.
    step1 = (
        await client.post(f"{API}/auth/login", json={"email": EMAIL["compliance_officer"], "password": TEST_PASSWORD})
    ).json()
    assert step1["mfa_required"] is True and step1["access_token"] is None
    bad = await client.post(f"{API}/auth/mfa/verify", json={"mfa_token": step1["mfa_token"], "code": "123456"})
    assert bad.status_code == 401
    good = await client.post(f"{API}/auth/mfa/verify", json={"mfa_token": step1["mfa_token"], "code": totp.now()})
    assert good.status_code == 200
    me = (await client.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {good.json()['access_token']}"})).json()
    assert me["mfa_verified"] is True

    # A recovery code works once.
    step1 = (
        await client.post(f"{API}/auth/login", json={"email": EMAIL["compliance_officer"], "password": TEST_PASSWORD})
    ).json()
    once = await client.post(
        f"{API}/auth/mfa/verify", json={"mfa_token": step1["mfa_token"], "code": recovery_codes[0]}
    )
    assert once.status_code == 200
    step1 = (
        await client.post(f"{API}/auth/login", json={"email": EMAIL["compliance_officer"], "password": TEST_PASSWORD})
    ).json()
    twice = await client.post(
        f"{API}/auth/mfa/verify", json={"mfa_token": step1["mfa_token"], "code": recovery_codes[0]}
    )
    assert twice.status_code == 401

    # The MFA challenge token is not an access token.
    assert (
        await client.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {step1['mfa_token']}"})
    ).status_code == 401

    disabled = await client.post(
        f"{API}/auth/mfa/disable",
        headers={"Authorization": f"Bearer {good.json()['access_token']}"},
        json={"password": TEST_PASSWORD},
    )
    assert disabled.status_code == 204


async def test_mfa_secret_is_encrypted_at_rest(client: httpx.AsyncClient) -> None:
    headers = await login(client, "manager_finance")
    secret = (await client.post(f"{API}/auth/mfa/setup", headers=headers)).json()["secret"]
    async with session_scope() as db:
        stored = (
            await db.execute(select(User.mfa_secret_encrypted).where(User.email == EMAIL["manager_finance"]))
        ).scalar_one()
    assert stored and secret not in stored


async def test_account_locks_after_repeated_failures(client: httpx.AsyncClient) -> None:
    admin = await login(client, "admin")
    body = {
        "email": "lockme@aurelia-systems.example",
        "full_name": "Lock Me",
        "password": TEST_PASSWORD,
        "role": "employee",
        "department": "sales",
    }
    assert (await client.post(f"{API}/users", headers=admin, json=body)).status_code == 201
    for _ in range(get_settings().max_failed_logins):
        await client.post(f"{API}/auth/login", json={"email": body["email"], "password": "wrong-password-1"})
    locked = await client.post(f"{API}/auth/login", json={"email": body["email"], "password": TEST_PASSWORD})
    assert locked.status_code == 401 and "locked" in locked.json()["detail"].lower()


async def test_deactivating_a_user_revokes_their_sessions(client: httpx.AsyncClient) -> None:
    admin = await login(client, "admin")
    body = {
        "email": "leaver@aurelia-systems.example",
        "full_name": "Leaver",
        "password": TEST_PASSWORD,
        "role": "employee",
        "department": "sales",
    }
    created = (await client.post(f"{API}/users", headers=admin, json=body)).json()
    token = (await client.post(f"{API}/auth/login", json={"email": body["email"], "password": TEST_PASSWORD})).json()[
        "access_token"
    ]
    user_headers = {"Authorization": f"Bearer {token}"}
    assert (await client.get(f"{API}/auth/me", headers=user_headers)).status_code == 200
    assert (
        await client.patch(f"{API}/users/{created['id']}", headers=admin, json={"is_active": False})
    ).status_code == 200
    assert (await client.get(f"{API}/auth/me", headers=user_headers)).status_code == 401
    assert (
        await client.post(f"{API}/auth/login", json={"email": body["email"], "password": TEST_PASSWORD})
    ).status_code == 401


async def test_service_accounts_cannot_sign_in_with_a_password(client: httpx.AsyncClient) -> None:
    response = await client.post(
        f"{API}/auth/login", json={"email": "assistant.bot@aurelia-systems.example", "password": TEST_PASSWORD}
    )
    assert response.status_code == 401


async def test_weak_password_is_rejected(client: httpx.AsyncClient) -> None:
    admin = await login(client, "admin")
    body = {
        "email": "weak@aurelia-systems.example",
        "full_name": "Weak",
        "password": "abcdefghijkl",
        "role": "employee",
        "department": "sales",
    }
    assert (await client.post(f"{API}/users", headers=admin, json=body)).status_code == 422


async def test_error_responses_do_not_leak_internals(client: httpx.AsyncClient) -> None:
    response = await client.post(f"{API}/auth/login", json={"email": "not-an-email", "password": ""})
    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/problem+json")
    text_body = response.text.lower()
    assert "traceback" not in text_body and "sqlalchemy" not in text_body
    assert response.headers["x-content-type-options"] == "nosniff"


async def test_audit_chain_is_valid_and_tamper_evident(client: httpx.AsyncClient) -> None:
    await login(client, "employee")
    trail = AuditTrail()
    report = await trail.verify_chain()
    assert report.ok and report.entries_checked > 0

    # The database refuses to change or delete audit rows.
    for statement in ("UPDATE audit_log SET decision = 'GRANTED'", "DELETE FROM audit_log", "TRUNCATE audit_log"):
        with pytest.raises(Exception, match="append-only"):
            async with session_scope() as db:
                await db.execute(text(statement))

    # Even with the trigger bypassed (as a database superuser could), the chain exposes the edit.
    async with session_scope() as db:
        await db.execute(text("ALTER TABLE audit_log DISABLE TRIGGER audit_log_no_update_delete"))
        await db.execute(
            text(
                "UPDATE audit_log SET decision = 'GRANTED' WHERE seq = (SELECT min(seq) FROM audit_log WHERE decision = 'DENIED')"
            )
        )
        await db.execute(text("ALTER TABLE audit_log ENABLE TRIGGER audit_log_no_update_delete"))
    tampered = await trail.verify_chain()
    assert not tampered.ok and tampered.first_invalid_seq is not None

    # Restore the row so later tests see a valid chain.
    async with session_scope() as db:
        await db.execute(text("ALTER TABLE audit_log DISABLE TRIGGER audit_log_no_update_delete"))
        await db.execute(
            text("UPDATE audit_log SET decision = 'DENIED' WHERE seq = :seq"), {"seq": tampered.first_invalid_seq}
        )
        await db.execute(text("ALTER TABLE audit_log ENABLE TRIGGER audit_log_no_update_delete"))
    assert (await trail.verify_chain()).ok
