"""Validation tables: claims, their citations, user feedback and the human review queue."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from pramana.shared.db import Base, Timestamps, UUIDPrimaryKey


class Claim(Base, UUIDPrimaryKey):
    __tablename__ = "claims"
    __table_args__ = (Index("ix_claims_run", "run_id", "round", "ordinal"),)

    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("query_runs.id", ondelete="CASCADE"), nullable=False
    )
    round: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    char_start: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    char_end: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    verdict: Mapped[str] = mapped_column(String(16), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    # Every detector signal that fed the verdict, kept for explanation and recalibration.
    signals: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    unsupported_spans: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, nullable=False)
    in_final_answer: Mapped[bool] = mapped_column(default=True, nullable=False)


class Citation(Base, UUIDPrimaryKey):
    __tablename__ = "citations"

    claim_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("claims.id", ondelete="CASCADE"), index=True, nullable=False
    )
    evidence_ref: Mapped[str] = mapped_column(String(16), nullable=False)
    chunk_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("chunks.id", ondelete="SET NULL"))
    document_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("documents.id", ondelete="SET NULL"))
    document_title: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    section_path: Mapped[str] = mapped_column(String(1024), default="", nullable=False)
    page: Mapped[int | None] = mapped_column(Integer)
    quote: Mapped[str] = mapped_column(Text, default="", nullable=False)
    quote_start: Mapped[int | None] = mapped_column(Integer)
    quote_end: Mapped[int | None] = mapped_column(Integer)
    support_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)


class Feedback(Base, UUIDPrimaryKey):
    __tablename__ = "feedback"

    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("query_runs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    claim_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("claims.id", ondelete="SET NULL"))
    rating: Mapped[int] = mapped_column(Integer, nullable=False)
    comment: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ReviewItem(Base, UUIDPrimaryKey, Timestamps):
    """An answer waiting for a human decision (low confidence, negative feedback, or a report)."""

    __tablename__ = "review_items"
    __table_args__ = (Index("ix_review_items_status", "status", "created_at"),)

    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("query_runs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    reason: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="open", nullable=False)
    reviewer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    resolution: Mapped[str | None] = mapped_column(String(32))
    note: Mapped[str] = mapped_column(Text, default="", nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
