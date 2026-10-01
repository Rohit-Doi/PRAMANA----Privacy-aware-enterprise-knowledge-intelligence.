"""Runs a question through the agent workflow and records everything about the run."""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pramana.agents.models import AgentStep, Conversation, Message, QueryRun
from pramana.agents.run_view import load_run
from pramana.agents.state import AgentDeps, Emit, PipelineState
from pramana.agents.workflow import build_workflow
from pramana.auth.principal import Principal
from pramana.governance.audit import AuditTrail
from pramana.retrieval.retriever import Evidence
from pramana.shared.crypto import sha256_hex
from pramana.shared.db import session_scope, utcnow
from pramana.shared.errors import AppError, NotFound
from pramana.shared.logging import get_logger
from pramana.validation.engine import ClaimResult
from pramana.validation.models import Citation, Claim, ReviewItem

log = get_logger(__name__)

REVIEW_TRUST_THRESHOLD = 60.0
MAX_QUESTION_CHARS = 2000
_DECISIONS = {"answered": "ANSWERED", "abstained": "ABSTAINED", "denied": "DENIED"}


async def _discard(event: dict[str, Any]) -> None:
    return None


class QueryPipeline:
    def __init__(self, deps: AgentDeps) -> None:
        self._deps = deps
        self._workflow = build_workflow(deps)
        self._audit = AuditTrail()

    async def run(
        self,
        *,
        principal: Principal,
        question: str,
        conversation_id: uuid.UUID | None = None,
        mode: str = "strict",
        channel: str = "web",
        emit: Emit = _discard,
        persist_conversation: bool = True,
    ) -> dict[str, Any]:
        question = question.strip()[:MAX_QUESTION_CHARS]
        started = time.perf_counter()
        run_id = uuid.uuid4()
        # Only the redacted form of the question is ever written to disk.
        redacted = (await asyncio.to_thread(self._deps.pii.redact, question, include_contact=False)).text
        conversation_id, previous_question = await self._open_run(
            run_id, principal, redacted, conversation_id, mode, channel, persist_conversation
        )
        await emit(
            {
                "type": "run.started",
                "run_id": str(run_id),
                "conversation_id": str(conversation_id) if conversation_id else None,
            }
        )

        finished_steps: list[dict[str, Any]] = []

        async def tracking_emit(event: dict[str, Any]) -> None:
            if event["type"] in ("agent.finished", "agent.failed"):
                finished_steps.append(event)
            await emit(event)

        initial: PipelineState = {
            "run_id": run_id,
            "question": question,
            "previous_question": previous_question,
            "mode": mode,
            "principal": principal,
            "round": 0,
            "steps": [],
            "llm_calls": 0,
        }
        try:
            state: PipelineState = await self._workflow.ainvoke(
                initial, config={"configurable": {"emit": tracking_emit}, "recursion_limit": 20}
            )
        except Exception as exc:
            latency = int((time.perf_counter() - started) * 1000)
            log.exception("pipeline.failed", run_id=str(run_id), error=repr(exc))
            await self._record_failure(run_id, principal, finished_steps, exc, latency)
            await emit({"type": "run.failed", "run_id": str(run_id), "error": public_error(exc)})
            raise

        latency = int((time.perf_counter() - started) * 1000)
        await self._record_success(run_id, conversation_id, principal, state, latency, persist_conversation)
        async with session_scope() as db:
            payload = await load_run(db, run_id)
        await emit({"type": "run.finished", "result": payload})
        return payload

    # ── Persistence ─────────────────────────────────────────────────────
    async def _open_run(
        self,
        run_id: uuid.UUID,
        principal: Principal,
        redacted: str,
        conversation_id: uuid.UUID | None,
        mode: str,
        channel: str,
        persist_conversation: bool,
    ) -> tuple[uuid.UUID | None, str | None]:
        previous_question: str | None = None
        async with session_scope() as db:
            if persist_conversation:
                if conversation_id is None:
                    conversation = Conversation(user_id=principal.user_id, title=redacted[:80] or "New conversation")
                    db.add(conversation)
                    await db.flush()
                    conversation_id = conversation.id
                else:
                    conversation = await db.get(Conversation, conversation_id)
                    if conversation is None or conversation.user_id != principal.user_id:
                        raise NotFound("Conversation not found")
                    previous_question = (
                        await db.execute(
                            select(Message.content)
                            .where(Message.conversation_id == conversation_id, Message.role == "user")
                            .order_by(Message.created_at.desc())
                            .limit(1)
                        )
                    ).scalar_one_or_none()
                    conversation.updated_at = utcnow()
            else:
                conversation_id = None
            db.add(
                QueryRun(
                    id=run_id,
                    user_id=principal.user_id,
                    conversation_id=conversation_id,
                    kind="query",
                    channel=channel,
                    mode=mode,
                    status="running",
                    query_redacted=redacted,
                    started_at=utcnow(),
                )
            )
            await db.flush()
            if conversation_id is not None:
                db.add(
                    Message(
                        conversation_id=conversation_id,
                        role="user",
                        content=redacted,
                        run_id=run_id,
                        created_at=utcnow(),
                    )
                )
        return conversation_id, previous_question

    async def _record_failure(
        self, run_id: uuid.UUID, principal: Principal, steps: list[dict[str, Any]], exc: Exception, latency: int
    ) -> None:
        async with session_scope() as db:
            run = await db.get(QueryRun, run_id)
            if run is not None:
                run.status, run.outcome, run.error = "failed", "error", public_error(exc)
                run.finished_at, run.latency_ms = utcnow(), latency
            add_steps(db, run_id, steps)
        await self._audit.record(
            action="query.run",
            decision="ERROR",
            principal=principal,
            resource_type="run",
            resource_id=str(run_id),
            details={"error": public_error(exc), "latency_ms": latency},
        )

    async def _record_success(
        self,
        run_id: uuid.UUID,
        conversation_id: uuid.UUID | None,
        principal: Principal,
        state: PipelineState,
        latency: int,
        persist_conversation: bool,
    ) -> None:
        access = state.get("access")
        scope = access.scope if access else None
        retrieval = state.get("retrieval")
        evidence: list[Evidence] = state.get("evidence", [])
        report = state.get("report")
        kept: list[ClaimResult] = state.get("kept_claims", [])
        removed: list[ClaimResult] = state.get("removed_claims", [])
        outcome = state["outcome"]
        trust = state["trust"]
        kept_claims = [c for c in kept if c.kind == "claim"]
        confidence = sum(c.confidence for c in kept_claims) / len(kept_claims) if kept_claims else None
        conflicts = state["explanation"].get("conflicts") or []
        needs_review = outcome == "answered" and (
            trust["score"] < REVIEW_TRUST_THRESHOLD or bool(removed) or bool(conflicts)
        )
        withheld = retrieval.withheld_count if retrieval else 0

        # The audit entry is written first so that its id can be stored with the run.
        audit_id = await self._audit.record(
            action="query.run",
            decision=_DECISIONS[outcome],
            principal=principal,
            resource_type="run",
            resource_id=str(run_id),
            details={
                "question": state.get("query_redacted", ""),
                "outcome_reason": state.get("outcome_reason", ""),
                "policy_version": scope.policy_version if scope else None,
                "scope": scope.as_dict() if scope else {},
                "evidence": [
                    {
                        "ref": e.ref,
                        "document_id": str(e.document_id),
                        "chunk_ids": [str(c) for c in e.chunk_ids],
                        "sensitivity_rank": e.sensitivity_rank,
                        "department": e.department,
                    }
                    for e in evidence
                ],
                "withheld_count": withheld,
                "route": state.get("route", "none"),
                "claims": {
                    "kept": len(kept_claims),
                    "removed": len(removed),
                    "verdicts": report.counts() if report else {},
                },
                "answer_sha256": sha256_hex(state["display_answer"]),
                "trust_score": trust["score"] if outcome == "answered" else None,
                "risk": state["risk"]["score"],
                "risk_level": state["risk"]["level"],
                "injection_patterns": state.get("injection", []),
                "pii_in_question": state.get("query_pii", {}),
                "output_redactions": state.get("output_redactions", 0),
                "llm_calls": state.get("llm_calls", 0),
                "latency_ms": latency,
                "calibrator": report.calibrator_version if report else "",
            },
        )

        async with session_scope() as db:
            run = await db.get(QueryRun, run_id)
            assert run is not None
            run.status, run.outcome = "completed", outcome
            run.answer = state["display_answer"]
            run.access_scope = scope.as_dict() if scope else {}
            run.policy_version = scope.policy_version if scope else None
            run.withheld_count = withheld
            run.risk_score = state["risk"]["score"]
            run.confidence = confidence
            run.trust_score = trust["score"] if outcome == "answered" else None
            run.trust_breakdown = {"trust": trust, "risk": state["risk"]}
            run.correction_rounds = state.get("round", 0)
            run.evidence = [e.as_dict() for e in evidence]
            run.graph_facts = state["explanation"].get("graph_facts", [])
            run.explanation = {
                **state["explanation"],
                "outcome_reason": state.get("outcome_reason", ""),
                "audit_id": str(audit_id),
                "show_withheld": bool(access and access.show_withheld_count),
            }
            run.llm_calls = state.get("llm_calls", 0)
            run.latency_ms, run.finished_at = latency, utcnow()
            run.needs_review = needs_review
            add_steps(db, run_id, state.get("steps", []))
            await store_claims(db, run_id, state, kept, removed)
            if needs_review:
                reason = "conflicting_sources" if conflicts else "sentences_removed" if removed else "low_trust"
                db.add(ReviewItem(run_id=run_id, reason=reason))
            if persist_conversation and conversation_id is not None:
                db.add(
                    Message(
                        conversation_id=conversation_id,
                        role="assistant",
                        content=state["display_answer"],
                        run_id=run_id,
                        created_at=utcnow(),
                    )
                )


async def store_claims(
    db: AsyncSession, run_id: uuid.UUID, state: PipelineState, kept: list[ClaimResult], removed: list[ClaimResult]
) -> None:
    """Persist claims and citations in the form the reader is allowed to see."""
    access = state.get("access")
    can_read_pii = bool(access and access.scope.can_read_pii)
    pseudonymizer = state.get("pseudonymizer")
    evidence: list[Evidence] = state.get("evidence", [])
    by_ref = {e.ref: e for e in evidence}
    llm_text = {e.ref: e.text for e in state.get("llm_evidence", [])}
    display = {c["ordinal"]: c for c in state.get("display_claims", [])}
    placeholders_used = bool(pseudonymizer and pseudonymizer.placeholder_count)

    def reader_quote(ref: str, quote: str, start: int, end: int) -> tuple[str, int | None, int | None]:
        """The quote as this reader sees it, with offsets into the reader's version of the passage."""
        if pseudonymizer is not None:
            quote = pseudonymizer.restore(quote, reveal=can_read_pii)
        text = by_ref[ref].text if ref in by_ref else ""
        if text == llm_text.get(ref) and text[start:end] == quote:
            return quote, start, end
        position = text.find(quote)
        return (quote, position, position + len(quote)) if position >= 0 else (quote, None, None)

    for claim, in_answer in [*[(c, True) for c in kept], *[(c, False) for c in removed]]:
        shown = display.get(claim.ordinal) if in_answer else None
        if shown is not None:
            text = shown["text"]
        else:
            text = pseudonymizer.restore(claim.text, reveal=can_read_pii) if pseudonymizer else claim.text
        row = Claim(
            run_id=run_id,
            round=state.get("round", 0),
            ordinal=claim.ordinal,
            text=text,
            char_start=shown["start"] if shown else 0,
            char_end=shown["end"] if shown else 0,
            verdict=str(claim.verdict),
            confidence=claim.confidence,
            signals={**claim.signals, "reason": claim.reason, "kind": claim.kind},
            # Span offsets refer to the generator's text; they are kept only when it equals the reader's.
            unsupported_spans=[] if placeholders_used else claim.unsupported_spans,
            in_final_answer=in_answer,
        )
        db.add(row)
        await db.flush()
        for citation in claim.citations:
            source = by_ref.get(citation.ref)
            quote, quote_start, quote_end = reader_quote(
                citation.ref, citation.quote, citation.quote_start, citation.quote_end
            )
            db.add(
                Citation(
                    claim_id=row.id,
                    evidence_ref=citation.ref,
                    # Evidence from a pasted reference text has no stored chunk or document.
                    chunk_id=source.chunk_ids[0] if source and source.chunk_ids else None,
                    document_id=source.document_id if source and source.chunk_ids else None,
                    document_title=source.title if source else "",
                    section_path=(source.section_path if source else "")[:1024],
                    page=source.page if source else None,
                    quote=quote,
                    quote_start=quote_start,
                    quote_end=quote_end,
                    support_score=citation.support,
                )
            )


def add_steps(db: AsyncSession, run_id: uuid.UUID, steps: list[dict[str, Any]]) -> None:
    for step in steps:
        db.add(
            AgentStep(
                run_id=run_id,
                agent=step["agent"],
                status=step.get("status", "failed"),
                started_at=datetime.fromisoformat(step["started_at"]),
                duration_ms=int(step.get("duration_ms", 0)),
                summary=str(step.get("summary", ""))[:2000],
                details=step.get("details", {}),
                error=step.get("error"),
            )
        )


def public_error(exc: Exception) -> str:
    """An error description that is safe to store and show: no stack, no internals."""
    if isinstance(exc, AppError):
        return f"{exc.code}: {exc.detail}"
    return f"internal_error: {type(exc).__name__}"
