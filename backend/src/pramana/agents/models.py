"""Conversation and run tables: what was asked, what each agent did, what the LLM cost."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from pramana.shared.db import Base, Timestamps, UUIDPrimaryKey


class Conversation(Base, UUIDPrimaryKey, Timestamps):
    __tablename__ = "conversations"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(256), default="New conversation", nullable=False)


class Message(Base, UUIDPrimaryKey):
    __tablename__ = "messages"
    __table_args__ = (Index("ix_messages_conversation_created", "conversation_id", "created_at"),)

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("query_runs.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class QueryRun(Base, UUIDPrimaryKey):
    """One pass of a question (or a verification request) through the agent pipeline."""

    __tablename__ = "query_runs"
    __table_args__ = (Index("ix_query_runs_user_started", "user_id", "started_at"),)

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("conversations.id", ondelete="SET NULL"))
    kind: Mapped[str] = mapped_column(String(16), default="query", nullable=False)
    channel: Mapped[str] = mapped_column(String(16), default="web", nullable=False)
    mode: Mapped[str] = mapped_column(String(16), default="strict", nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="running", nullable=False)
    outcome: Mapped[str | None] = mapped_column(String(24))
    # The question with personal data already redacted. The raw text is never stored.
    query_redacted: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str | None] = mapped_column(Text)
    access_scope: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    policy_version: Mapped[int | None] = mapped_column(Integer)
    withheld_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    risk_score: Mapped[float | None] = mapped_column(Float)
    confidence: Mapped[float | None] = mapped_column(Float)
    trust_score: Mapped[float | None] = mapped_column(Float)
    trust_breakdown: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    correction_rounds: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, nullable=False)
    graph_facts: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, nullable=False)
    explanation: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    llm_calls: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AgentStep(Base, UUIDPrimaryKey):
    __tablename__ = "agent_steps"
    __table_args__ = (Index("ix_agent_steps_run", "run_id", "started_at"),)

    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("query_runs.id", ondelete="CASCADE"), nullable=False)
    agent: Mapped[str] = mapped_column(String(48), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    error: Mapped[str | None] = mapped_column(Text)


class LLMCall(Base, UUIDPrimaryKey):
    __tablename__ = "llm_calls"
    __table_args__ = (Index("ix_llm_calls_created", "created_at"),)

    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("query_runs.id", ondelete="SET NULL"))
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(96), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    cached: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
