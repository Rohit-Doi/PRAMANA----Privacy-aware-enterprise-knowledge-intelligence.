"""Authentication endpoints.

The access token is returned in the response body and kept in memory by the client.
The refresh token travels only in an httpOnly, SameSite=Strict cookie scoped to this router,
and every cookie-authenticated call must also carry a custom header, which a cross-site
form or image request cannot add (CSRF protection).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Cookie, Header, Request, Response
from pydantic import BaseModel, EmailStr, Field

from pramana.api.deps import CurrentPrincipal, DbSession, client_ip
from pramana.auth.service import AuthService, TokenPair
from pramana.governance.audit import AuditTrail
from pramana.shared.config import get_settings
from pramana.shared.errors import NotAuthenticated, PermissionDenied

router = APIRouter(prefix="/auth", tags=["Authentication"])

REFRESH_COOKIE = "pramana_refresh"
COOKIE_PATH = "/api/v1/auth"
CSRF_HEADER_VALUE = "pramana-web"


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class MfaVerifyRequest(BaseModel):
    mfa_token: str
    code: str = Field(min_length=6, max_length=32)


class TokenResponse(BaseModel):
    access_token: str | None = None
    token_type: str = "bearer"  # noqa: S105 - OAuth2 token type, not a secret
    expires_in: int | None = None
    mfa_required: bool = False
    mfa_token: str | None = None


class PrincipalResponse(BaseModel):
    user_id: uuid.UUID
    email: str
    full_name: str
    role: str
    department: str
    extra_departments: list[str]
    region: str
    max_rank_own_dept: int
    max_rank_other_dept: int
    permissions: list[str]
    mfa_verified: bool


class SessionResponse(BaseModel):
    id: uuid.UUID
    user_agent: str
    ip_address: str
    created_at: datetime
    last_used_at: datetime
    current: bool


class MfaSetupResponse(BaseModel):
    secret: str
    otpauth_uri: str


class MfaConfirmRequest(BaseModel):
    code: str = Field(min_length=6, max_length=8)


class RecoveryCodesResponse(BaseModel):
    recovery_codes: list[str]


class PasswordChangeRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=12, max_length=256)


class PasswordConfirmRequest(BaseModel):
    password: str


def _set_refresh_cookie(response: Response, tokens: TokenPair) -> None:
    settings = get_settings()
    response.set_cookie(
        REFRESH_COOKIE,
        tokens.refresh_token,
        max_age=settings.refresh_token_ttl_days * 86_400,
        path=COOKIE_PATH,
        httponly=True,
        secure=settings.is_production,
        samesite="strict",
    )


def _require_csrf_header(value: str | None) -> None:
    if value != CSRF_HEADER_VALUE:
        raise PermissionDenied("Missing or invalid X-Requested-With header")


def _token_response(tokens: TokenPair) -> TokenResponse:
    return TokenResponse(access_token=tokens.access_token, expires_in=tokens.expires_in)


@router.post("/login", response_model=TokenResponse, summary="Sign in with email and password")
async def login(body: LoginRequest, request: Request, response: Response, db: DbSession) -> TokenResponse:
    audit = AuditTrail()
    try:
        result = await AuthService(db).login(
            email=body.email, password=body.password, ip=client_ip(request),
            user_agent=request.headers.get("user-agent", ""),
        )
    except NotAuthenticated:
        await audit.record(
            action="auth.login", decision="DENIED", actor_email=body.email.lower(),
            details={"ip": client_ip(request)},
        )
        raise
    if result.mfa_required:
        return TokenResponse(mfa_required=True, mfa_token=result.mfa_token)
    assert result.tokens is not None
    _set_refresh_cookie(response, result.tokens)
    await audit.record(
        action="auth.login", decision="GRANTED", actor_email=body.email.lower(),
        resource_type="session", resource_id=str(result.tokens.session_id),
        details={"ip": client_ip(request), "mfa": False},
    )
    return _token_response(result.tokens)


@router.post("/mfa/verify", response_model=TokenResponse, summary="Complete sign-in with a one-time code")
async def mfa_verify(body: MfaVerifyRequest, request: Request, response: Response, db: DbSession) -> TokenResponse:
    tokens = await AuthService(db).complete_mfa(
        mfa_token=body.mfa_token, code=body.code, ip=client_ip(request),
        user_agent=request.headers.get("user-agent", ""),
    )
    _set_refresh_cookie(response, tokens)
    await AuditTrail().record(
        action="auth.login", decision="GRANTED", resource_type="session",
        resource_id=str(tokens.session_id), details={"ip": client_ip(request), "mfa": True},
    )
    return _token_response(tokens)


@router.post("/refresh", response_model=TokenResponse, summary="Exchange the refresh cookie for a new access token")
async def refresh(
    response: Response,
    db: DbSession,
    refresh_token: Annotated[str | None, Cookie(alias=REFRESH_COOKIE)] = None,
    x_requested_with: Annotated[str | None, Header()] = None,
) -> TokenResponse:
    _require_csrf_header(x_requested_with)
    if not refresh_token:
        raise NotAuthenticated("No session")
    tokens = await AuthService(db).refresh(refresh_token)
    _set_refresh_cookie(response, tokens)
    return _token_response(tokens)


@router.post("/logout", status_code=204, summary="End the current session")
async def logout(
    response: Response,
    principal: CurrentPrincipal,
    db: DbSession,
    x_requested_with: Annotated[str | None, Header()] = None,
) -> None:
    _require_csrf_header(x_requested_with)
    if principal.session_id is not None:
        await AuthService(db).logout(principal.session_id)
    response.delete_cookie(REFRESH_COOKIE, path=COOKIE_PATH)
    await AuditTrail().record(action="auth.logout", decision="GRANTED", principal=principal)


@router.get("/me", response_model=PrincipalResponse, summary="The signed-in user and their permissions")
async def me(principal: CurrentPrincipal) -> PrincipalResponse:
    return PrincipalResponse(
        user_id=principal.user_id, email=principal.email, full_name=principal.full_name,
        role=principal.role, department=principal.department,
        extra_departments=list(principal.extra_departments), region=principal.region,
        max_rank_own_dept=principal.max_rank_own_dept, max_rank_other_dept=principal.max_rank_other_dept,
        permissions=sorted(principal.permissions), mfa_verified=principal.mfa_verified,
    )


@router.get("/sessions", response_model=list[SessionResponse], summary="Active sessions of the signed-in user")
async def sessions(principal: CurrentPrincipal, db: DbSession) -> list[SessionResponse]:
    rows = await AuthService(db).list_sessions(principal.user_id)
    return [
        SessionResponse(
            id=s.id, user_agent=s.user_agent, ip_address=s.ip_address, created_at=s.created_at,
            last_used_at=s.last_used_at, current=s.id == principal.session_id,
        )
        for s in rows
    ]


@router.delete("/sessions/{session_id}", status_code=204, summary="Revoke one of your sessions")
async def revoke_session(session_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession) -> None:
    await AuthService(db).revoke_session(user_id=principal.user_id, session_id=session_id)
    await AuditTrail().record(
        action="auth.session.revoke", decision="GRANTED", principal=principal,
        resource_type="session", resource_id=str(session_id),
    )


@router.post("/mfa/setup", response_model=MfaSetupResponse, summary="Start authenticator-app enrolment")
async def mfa_setup(principal: CurrentPrincipal, db: DbSession) -> MfaSetupResponse:
    secret, uri = await AuthService(db).begin_mfa_setup(principal.user_id)
    return MfaSetupResponse(secret=secret, otpauth_uri=uri)


@router.post("/mfa/confirm", response_model=RecoveryCodesResponse, summary="Confirm enrolment with a code")
async def mfa_confirm(body: MfaConfirmRequest, principal: CurrentPrincipal, db: DbSession) -> RecoveryCodesResponse:
    codes = await AuthService(db).confirm_mfa_setup(principal.user_id, body.code)
    await AuditTrail().record(action="auth.mfa.enable", decision="GRANTED", principal=principal)
    return RecoveryCodesResponse(recovery_codes=codes)


@router.post("/mfa/disable", status_code=204, summary="Turn off MFA (requires the password)")
async def mfa_disable(body: PasswordConfirmRequest, principal: CurrentPrincipal, db: DbSession) -> None:
    await AuthService(db).disable_mfa(principal.user_id, body.password)
    await AuditTrail().record(action="auth.mfa.disable", decision="GRANTED", principal=principal)


@router.post("/password", status_code=204, summary="Change your password")
async def change_password(body: PasswordChangeRequest, principal: CurrentPrincipal, db: DbSession) -> None:
    await AuthService(db).change_password(principal.user_id, body.current_password, body.new_password)
    await AuditTrail().record(action="auth.password.change", decision="GRANTED", principal=principal)
