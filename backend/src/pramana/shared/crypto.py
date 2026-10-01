"""Cryptographic primitives: password hashing, token signing, field encryption, hashing."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import orjson
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from cryptography.fernet import Fernet, InvalidToken

from pramana.shared.config import get_settings
from pramana.shared.errors import NotAuthenticated

_hasher = PasswordHasher()  # Argon2id with library defaults
_JWT_ALGORITHM = "HS256"


# ── Passwords ───────────────────────────────────────────────────────────────
def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


# A real hash to verify against when the account does not exist, so that a login
# attempt takes the same time whether or not the email is registered.
DUMMY_PASSWORD_HASH = _hasher.hash(secrets.token_urlsafe(16))


# ── Access tokens (JWT) ─────────────────────────────────────────────────────
def create_access_token(*, subject: uuid.UUID, session_id: uuid.UUID, mfa: bool) -> tuple[str, int]:
    settings = get_settings()
    now = datetime.now(UTC)
    ttl = settings.access_token_ttl_seconds
    payload = {
        "iss": settings.jwt_issuer,
        "sub": str(subject),
        "sid": str(session_id),
        "mfa": mfa,
        "typ": "access",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl)).timestamp()),
        "jti": uuid.uuid4().hex,
    }
    token = jwt.encode(payload, settings.jwt_secret.get_secret_value(), algorithm=_JWT_ALGORITHM)
    return token, ttl


def create_scoped_token(*, subject: uuid.UUID, purpose: str, ttl_seconds: int) -> str:
    """Short-lived single-purpose token (for example the step between password and MFA)."""
    settings = get_settings()
    now = datetime.now(UTC)
    payload = {
        "iss": settings.jwt_issuer,
        "sub": str(subject),
        "typ": purpose,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl_seconds)).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret.get_secret_value(), algorithm=_JWT_ALGORITHM)


def decode_token(token: str, *, expected_type: str) -> dict[str, Any]:
    settings = get_settings()
    try:
        payload: dict[str, Any] = jwt.decode(
            token,
            settings.jwt_secret.get_secret_value(),
            algorithms=[_JWT_ALGORITHM],  # pinned: rejects "none" and algorithm confusion
            issuer=settings.jwt_issuer,
            options={"require": ["exp", "iat", "sub", "iss", "typ"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise NotAuthenticated("Token has expired") from exc
    except jwt.InvalidTokenError as exc:
        raise NotAuthenticated("Invalid token") from exc
    if payload.get("typ") != expected_type:
        raise NotAuthenticated("Invalid token type")
    return payload


# ── Opaque tokens (refresh tokens, API keys) ────────────────────────────────
def new_opaque_token() -> str:
    return secrets.token_urlsafe(48)


def hash_token(token: str) -> str:
    """Keyed hash for storing opaque tokens. The database never holds the token itself."""
    key = get_settings().jwt_secret.get_secret_value().encode()
    return hmac.new(key, token.encode(), hashlib.sha256).hexdigest()


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


# ── Field encryption (MFA seeds, connector credentials) ─────────────────────
def _fernet() -> Fernet:
    return Fernet(get_settings().encryption_key.get_secret_value().encode())


def encrypt_text(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt_text(ciphertext: str) -> str:
    try:
        return _fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        raise ValueError("Ciphertext could not be decrypted with the configured key") from exc


# ── Content hashing ─────────────────────────────────────────────────────────
def sha256_hex(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode()
    return hashlib.sha256(data).hexdigest()


def canonical_json(value: Any) -> bytes:
    """Deterministic JSON encoding used wherever a hash must be reproducible."""
    return orjson.dumps(value, option=orjson.OPT_SORT_KEYS | orjson.OPT_UTC_Z, default=str)
