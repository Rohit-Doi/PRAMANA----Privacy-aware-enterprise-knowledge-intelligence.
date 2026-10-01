"""Knowledge tables: documents, chunks, ingestion jobs."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from pramana.shared.db import Base, Timestamps, UUIDPrimaryKey


class Document(Base, UUIDPrimaryKey, Timestamps):
    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint("sensitivity_rank BETWEEN 0 AND 3", name="sensitivity_range"),
        CheckConstraint("status IN ('pending','processing','ready','failed','deleted')", name="status_valid"),
        Index("ix_documents_scope", "department", "sensitivity_rank", "region"),
        Index("ix_documents_content_hash", "content_hash"),
    )

    title: Mapped[str] = mapped_column(String(512), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_uri: Mapped[str] = mapped_column(String(1024), default="", nullable=False)
    collection: Mapped[str] = mapped_column(String(64), default="default", nullable=False)
    department: Mapped[str] = mapped_column(String(64), nullable=False)
    sensitivity_rank: Mapped[int] = mapped_column(Integer, nullable=False)
    suggested_sensitivity_rank: Mapped[int | None] = mapped_column(Integer)
    region: Mapped[str] = mapped_column(String(32), default="global", nullable=False)
    owner_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    effective_date: Mapped[date | None] = mapped_column(Date)
    version: Mapped[str] = mapped_column(String(32), default="1", nullable=False)
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("documents.id", ondelete="SET NULL"))
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    mime_type: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    storage_key: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    entity_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    pii_summary: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    chunks: Mapped[list[Chunk]] = relationship(
        back_populates="document", cascade="all, delete-orphan", passive_deletes=True
    )


class Chunk(Base, UUIDPrimaryKey):
    """A passage of a document. Postgres holds the text; Qdrant and Neo4j hold only ids."""

    __tablename__ = "chunks"
    __table_args__ = (UniqueConstraint("document_id", "ordinal", name="uq_chunks_document_ordinal"),)

    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True, nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    section_path: Mapped[str] = mapped_column(String(1024), default="", nullable=False)
    page: Mapped[int | None] = mapped_column(Integer)
    char_start: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    char_end: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    pii_types: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)

    document: Mapped[Document] = relationship(back_populates="chunks")


class IngestionJob(Base, UUIDPrimaryKey, Timestamps):
    """Background work item. Workers claim rows with SELECT … FOR UPDATE SKIP LOCKED."""

    __tablename__ = "ingestion_jobs"
    __table_args__ = (
        CheckConstraint("status IN ('queued','running','succeeded','failed','cancelled')", name="status_valid"),
        Index("ix_ingestion_jobs_claim", "status", "created_at"),
    )

    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True, nullable=False
    )
    kind: Mapped[str] = mapped_column(String(32), default="ingest", nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="queued", nullable=False)
    stage: Mapped[str] = mapped_column(String(32), default="queued", nullable=False)
    progress: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    events: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, nullable=False)
    locked_by: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
