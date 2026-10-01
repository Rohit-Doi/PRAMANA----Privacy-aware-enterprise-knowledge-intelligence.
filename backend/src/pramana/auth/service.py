"""Authentication use cases: login, MFA, session rotation, user administration."""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from datetime import timedelta

import pyotp
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pramana.auth.models import Department, Role, Session, User
from pramana.auth.principal import Principal, principal_from_user
from pramana.shared import crypto
from pramana.shared.config import get_settings
from pramana.shared.db import utcnow
from pramana.shared.errors import Conflict, NotAuthenticated, NotFound, ValidationFailed

MFA_TOKEN_TYPE = "mfa_challenge"  # noqa: S105 - a token type label, not a secret
MFA_TOKEN_TTL_SECONDS = 300
ISSUER_NAME = "PRAMANA"
_GENERIC_LOGIN_ERROR = "Incorrect email or password"


@dataclass(slots=True)
class TokenPair:
    access_token: str
    expires_in: int
    refresh_token: str
    session_id: uuid.UUID


@dataclass(slots=True)
class LoginResult:
    tokens: TokenPair | None = None
    mfa_token: str | None = None

    @property
    def mfa_required(self) -> bool:
        return self.mfa_token is not None


class AuthService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._settings = get_settings()

    # ── Login ───────────────────────────────────────────────────────────
    async def login(self, *, email: str, password: str, ip: str, user_agent: str) -> LoginResult:
        user = await self._user_by_email(email)
        if user is None:
            # Spend the same time as a real check so timing does not reveal which emails exist.
            crypto.verify_password(crypto.DUMMY_PASSWORD_HASH, password)
            raise NotAuthenticated(_GENERIC_LOGIN_ERROR)

        now = utcnow()
        if user.locked_until and user.locked_until > now:
            raise NotAuthenticated("Account temporarily locked after repeated failed sign-ins")
        if not user.is_active or user.is_service_account:
            crypto.verify_password(crypto.DUMMY_PASSWORD_HASH, password)
            raise NotAuthenticated(_GENERIC_LOGIN_ERROR)

        if not crypto.verify_password(user.password_hash, password):
            user.failed_logins += 1
            if user.failed_logins >= self._settings.max_failed_logins:
                user.locked_until = now + timedelta(minutes=self._settings.lockout_minutes)
                user.failed_logins = 0
            await self._db.commit()  # persist the counter even though the request fails
            raise NotAuthenticated(_GENERIC_LOGIN_ERROR)

        user.failed_logins = 0
        user.locked_until = None
        if crypto.password_needs_rehash(user.password_hash):
            user.password_hash = crypto.hash_password(password)

        if user.mfa_enabled:
            token = crypto.create_scoped_token(
                subject=user.id, purpose=MFA_TOKEN_TYPE, ttl_seconds=MFA_TOKEN_TTL_SECONDS
            )
            return LoginResult(mfa_token=token)

        tokens = await self._open_session(user, mfa_verified=False, ip=ip, user_agent=user_agent)
        return LoginResult(tokens=tokens)

    async def complete_mfa(self, *, mfa_token: str, code: str, ip: str, user_agent: str) -> TokenPair:
        payload = crypto.decode_token(mfa_token, expected_type=MFA_TOKEN_TYPE)
        user = await self._db.get(User, uuid.UUID(payload["sub"]))
        if user is None or not user.is_active or not user.mfa_enabled:
            raise NotAuthenticated("Invalid sign-in state")
        if not self._check_second_factor(user, code):
            user.failed_logins += 1
            if user.failed_logins >= self._settings.max_failed_logins:
                user.locked_until = utcnow() + timedelta(minutes=self._settings.lockout_minutes)
                user.failed_logins = 0
            await self._db.commit()
            raise NotAuthenticated("Incorrect verification code")
        user.failed_logins = 0
        return await self._open_session(user, mfa_verified=True, ip=ip, user_agent=user_agent)

    # ── Sessions ────────────────────────────────────────────────────────
    async def refresh(self, refresh_token: str) -> TokenPair:
        token_hash = crypto.hash_token(refresh_token)
        now = utcnow()
        session = (
            await self._db.execute(select(Session).where(Session.refresh_hash == token_hash))
        ).scalar_one_or_none()

        if session is None:
            # A token that was already rotated is being replayed: treat the session as stolen.
            stolen = (
                await self._db.execute(select(Session).where(Session.previous_refresh_hash == token_hash))
            ).scalar_one_or_none()
            if stolen is not None and stolen.revoked_at is None:
                stolen.revoked_at = now
                await self._db.commit()
            raise NotAuthenticated("Session is no longer valid")

        if session.revoked_at is not None or session.expires_at <= now or not session.user.is_active:
            raise NotAuthenticated("Session is no longer valid")

        new_refresh = crypto.new_opaque_token()
        session.previous_refresh_hash = session.refresh_hash
        session.refresh_hash = crypto.hash_token(new_refresh)
        session.last_used_at = now
        access, ttl = crypto.create_access_token(
            subject=session.user_id, session_id=session.id, mfa=session.mfa_verified
        )
        return TokenPair(access_token=access, expires_in=ttl, refresh_token=new_refresh, session_id=session.id)

    async def logout(self, session_id: uuid.UUID) -> None:
        session = await self._db.get(Session, session_id)
        if session is not None and session.revoked_at is None:
            session.revoked_at = utcnow()

    async def list_sessions(self, user_id: uuid.UUID) -> list[Session]:
        rows = await self._db.execute(
            select(Session)
            .where(Session.user_id == user_id, Session.revoked_at.is_(None), Session.expires_at > utcnow())
            .order_by(Session.last_used_at.desc())
        )
        return list(rows.scalars())

    async def revoke_session(self, *, user_id: uuid.UUID, session_id: uuid.UUID) -> None:
        session = await self._db.get(Session, session_id)
        if session is None or session.user_id != user_id:
            raise NotFound("Session not found")
        session.revoked_at = utcnow()

    async def principal_for_access_token(self, token: str) -> Principal:
        payload = crypto.decode_token(token, expected_type="access")
        try:
            session_id = uuid.UUID(payload["sid"])
        except (KeyError, ValueError) as exc:
            raise NotAuthenticated("Invalid token") from exc
        session = await self._db.get(Session, session_id)
        if (
            session is None
            or session.revoked_at is not None
            or session.expires_at <= utcnow()
            or str(session.user_id) != payload["sub"]
            or not session.user.is_active
        ):
            raise NotAuthenticated("Session is no longer valid")
        return principal_from_user(session.user, session_id=session.id, mfa_verified=session.mfa_verified)

    # ── MFA enrolment ───────────────────────────────────────────────────
    async def begin_mfa_setup(self, user_id: uuid.UUID) -> tuple[str, str]:
        user = await self._require_user(user_id)
        if user.mfa_enabled:
            raise Conflict("MFA is already enabled")
        secret = pyotp.random_base32()
        user.mfa_secret_encrypted = crypto.encrypt_text(secret)
        uri = pyotp.TOTP(secret).provisioning_uri(name=user.email, issuer_name=ISSUER_NAME)
        return secret, uri

    async def confirm_mfa_setup(self, user_id: uuid.UUID, code: str) -> list[str]:
        user = await self._require_user(user_id)
        if user.mfa_enabled or not user.mfa_secret_encrypted:
            raise Conflict("No MFA setup in progress")
        secret = crypto.decrypt_text(user.mfa_secret_encrypted)
        if not pyotp.TOTP(secret).verify(code, valid_window=1):
            raise ValidationFailed("Incorrect verification code")
        recovery_codes = [f"{secrets.token_hex(4)}-{secrets.token_hex(4)}" for _ in range(8)]
        user.mfa_recovery_hashes = [crypto.hash_token(c) for c in recovery_codes]
        user.mfa_enabled = True
        return recovery_codes

    async def disable_mfa(self, user_id: uuid.UUID, password: str) -> None:
        user = await self._require_user(user_id)
        if not crypto.verify_password(user.password_hash, password):
            raise NotAuthenticated("Incorrect password")
        user.mfa_enabled = False
        user.mfa_secret_encrypted = None
        user.mfa_recovery_hashes = []

    async def change_password(self, user_id: uuid.UUID, current: str, new: str) -> None:
        user = await self._require_user(user_id)
        if not crypto.verify_password(user.password_hash, current):
            raise NotAuthenticated("Incorrect password")
        validate_password_strength(new)
        user.password_hash = crypto.hash_password(new)

    # ── Internals ───────────────────────────────────────────────────────
    def _check_second_factor(self, user: User, code: str) -> bool:
        code = code.strip()
        if user.mfa_secret_encrypted:
            secret = crypto.decrypt_text(user.mfa_secret_encrypted)
            if pyotp.TOTP(secret).verify(code, valid_window=1):
                return True
        code_hash = crypto.hash_token(code)
        if code_hash in (user.mfa_recovery_hashes or []):
            # Recovery codes are single use.
            user.mfa_recovery_hashes = [h for h in user.mfa_recovery_hashes if h != code_hash]
            return True
        return False

    async def _open_session(self, user: User, *, mfa_verified: bool, ip: str, user_agent: str) -> TokenPair:
        now = utcnow()
        refresh = crypto.new_opaque_token()
        session = Session(
            id=uuid.uuid4(),
            user_id=user.id,
            refresh_hash=crypto.hash_token(refresh),
            mfa_verified=mfa_verified,
            user_agent=user_agent[:256],
            ip_address=ip[:64],
            created_at=now,
            last_used_at=now,
            expires_at=now + timedelta(days=self._settings.refresh_token_ttl_days),
        )
        self._db.add(session)
        user.last_login_at = now
        await self._db.flush()
        access, ttl = crypto.create_access_token(subject=user.id, session_id=session.id, mfa=mfa_verified)
        return TokenPair(access_token=access, expires_in=ttl, refresh_token=refresh, session_id=session.id)

    async def _user_by_email(self, email: str) -> User | None:
        rows = await self._db.execute(select(User).where(User.email == email.strip().lower()))
        return rows.scalar_one_or_none()

    async def _require_user(self, user_id: uuid.UUID) -> User:
        user = await self._db.get(User, user_id)
        if user is None:
            raise NotFound("User not found")
        return user


def validate_password_strength(password: str) -> None:
    problems = []
    if len(password) < 12:
        problems.append("at least 12 characters")
    if not any(c.isdigit() for c in password):
        problems.append("a digit")
    if not any(c.isalpha() for c in password):
        problems.append("a letter")
    if problems:
        raise ValidationFailed("Password needs " + ", ".join(problems))


class UserAdminService:
    """User, role and department management (requires ``users:manage``)."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def list_users(self) -> list[User]:
        rows = await self._db.execute(select(User).order_by(User.full_name))
        return list(rows.scalars())

    async def list_roles(self) -> list[Role]:
        rows = await self._db.execute(select(Role).order_by(Role.max_rank_own_dept, Role.key))
        return list(rows.scalars())

    async def list_departments(self) -> list[Department]:
        rows = await self._db.execute(select(Department).order_by(Department.name))
        return list(rows.scalars())

    async def get_user(self, user_id: uuid.UUID) -> User:
        user = await self._db.get(User, user_id)
        if user is None:
            raise NotFound("User not found")
        return user

    async def create_user(
        self,
        *,
        email: str,
        full_name: str,
        password: str,
        role_key: str,
        department_key: str,
        region: str = "global",
        extra_departments: list[str] | None = None,
        extra_permissions: list[str] | None = None,
        is_service_account: bool = False,
    ) -> User:
        email = email.strip().lower()
        existing = await self._db.execute(select(User.id).where(User.email == email))
        if existing.scalar_one_or_none() is not None:
            raise Conflict("A user with this email already exists")
        validate_password_strength(password)
        user = User(
            email=email,
            full_name=full_name.strip(),
            password_hash=crypto.hash_password(password),
            role_id=(await self._role(role_key)).id,
            department_id=(await self._department(department_key)).id,
            region=region,
            extra_departments=extra_departments or [],
            extra_permissions=extra_permissions or [],
            is_service_account=is_service_account,
        )
        self._db.add(user)
        await self._db.flush()
        await self._db.refresh(user)
        return user

    async def update_user(self, user_id: uuid.UUID, **changes: object) -> User:
        user = await self.get_user(user_id)
        if "role_key" in changes and changes["role_key"] is not None:
            user.role_id = (await self._role(str(changes["role_key"]))).id
        if "department_key" in changes and changes["department_key"] is not None:
            user.department_id = (await self._department(str(changes["department_key"]))).id
        for attr in ("full_name", "region", "extra_departments", "extra_permissions", "is_active"):
            if attr in changes and changes[attr] is not None:
                setattr(user, attr, changes[attr])
        if changes.get("is_active") is False:
            await self._revoke_all_sessions(user.id)
        await self._db.flush()
        await self._db.refresh(user)
        return user

    async def reset_password(self, user_id: uuid.UUID, new_password: str) -> None:
        user = await self.get_user(user_id)
        validate_password_strength(new_password)
        user.password_hash = crypto.hash_password(new_password)
        user.failed_logins = 0
        user.locked_until = None
        await self._revoke_all_sessions(user.id)

    async def _revoke_all_sessions(self, user_id: uuid.UUID) -> None:
        rows = await self._db.execute(
            select(Session).where(Session.user_id == user_id, Session.revoked_at.is_(None))
        )
        now = utcnow()
        for session in rows.scalars():
            session.revoked_at = now

    async def _role(self, key: str) -> Role:
        role = (await self._db.execute(select(Role).where(Role.key == key))).scalar_one_or_none()
        if role is None:
            raise ValidationFailed(f"Unknown role: {key}")
        return role

    async def _department(self, key: str) -> Department:
        dept = (await self._db.execute(select(Department).where(Department.key == key))).scalar_one_or_none()
        if dept is None:
            raise ValidationFailed(f"Unknown department: {key}")
        return dept
