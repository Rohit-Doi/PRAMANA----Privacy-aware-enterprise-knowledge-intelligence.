"""Identity tables: departments, roles, users, sessions."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from pramana.shared.db import Base, Timestamps, UUIDPrimaryKey


class Department(Base, UUIDPrimaryKey, Timestamps):
    __tablename__ = "departments"

    key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)


class Role(Base, UUIDPrimaryKey, Timestamps):
    """A role fixes how sensitive a document its holders may read.

    ``max_rank_own_dept`` applies to documents owned by the user's own department(s),
    ``max_rank_other_dept`` to everything else. Ranks follow ``Sensitivity``.
    """

    __tablename__ = "roles"
    __table_args__ = (
        CheckConstraint("max_rank_own_dept BETWEEN 0 AND 3", name="own_rank_range"),
        CheckConstraint("max_rank_other_dept BETWEEN 0 AND 3", name="other_rank_range"),
        CheckConstraint("max_rank_other_dept <= max_rank_own_dept", name="other_not_above_own"),
    )

    key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    max_rank_own_dept: Mapped[int] = mapped_column(Integer, nullable=False)
    max_rank_other_dept: Mapped[int] = mapped_column(Integer, nullable=False)
    permissions: Mapped[list[str]] = mapped_column(ARRAY(String(64)), default=list, nullable=False)


class User(Base, UUIDPrimaryKey, Timestamps):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(254), unique=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(128), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    role_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("roles.id", ondelete="RESTRICT"), nullable=False)
    department_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("departments.id", ondelete="RESTRICT"), nullable=False
    )
    region: Mapped[str] = mapped_column(String(32), default="global", nullable=False)
    # Departments, besides their own, for which the user is treated as an insider.
    extra_departments: Mapped[list[str]] = mapped_column(ARRAY(String(64)), default=list, nullable=False)
    extra_permissions: Mapped[list[str]] = mapped_column(ARRAY(String(64)), default=list, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_service_account: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    mfa_secret_encrypted: Mapped[str | None] = mapped_column(Text)
    mfa_recovery_hashes: Mapped[list[str]] = mapped_column(ARRAY(String(64)), default=list, nullable=False)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    role: Mapped[Role] = relationship(lazy="joined")
    department: Mapped[Department] = relationship(lazy="joined")


class Session(Base, UUIDPrimaryKey):
    """One login. Holds the hash of the current refresh token, which rotates on every use."""

    __tablename__ = "sessions"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    refresh_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    # Hash of the previous refresh token: presenting it again means the token was stolen.
    previous_refresh_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    mfa_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    user_agent: Mapped[str] = mapped_column(String(256), default="", nullable=False)
    ip_address: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship(lazy="joined")
