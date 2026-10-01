"""The stored record of a run, shaped for the API. Used for live responses and for history alike,
so what a user sees later is exactly what was recorded at the time."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pramana.agents.models import AgentStep, QueryRun
from pramana.agents.state import AGENT_LABELS
from pramana.shared.errors import NotFound
from pramana.validation.models import Citation, Claim

_SIGNAL_KEYS = (
    "grounding",
    "entailment",
    "contradiction",
    "span_fraction",
    "numbers_ok",
    "names_ok",
    "overlap",
    "supporting_documents",
    "numbers_missing",
    "names_missing",
    "evidence_changed",
    "judge",
    "probability",
)


async def load_run(db: AsyncSession, run_id: uuid.UUID) -> dict[str, Any]:
    run = await db.get(QueryRun, run_id)
    if run is None:
        raise NotFound("Run not found")
    return await run_payload(db, run)


async def run_payload(db: AsyncSession, run: QueryRun) -> dict[str, Any]:
    claims = (
        (
            await db.execute(
                select(Claim).where(Claim.run_id == run.id).order_by(Claim.in_final_answer.desc(), Claim.ordinal)
            )
        )
        .scalars()
        .all()
    )
    citations: dict[uuid.UUID, list[Citation]] = {}
    if claims:
        rows = await db.execute(select(Citation).where(Citation.claim_id.in_([c.id for c in claims])))
        for citation in rows.scalars():
            citations.setdefault(citation.claim_id, []).append(citation)
    steps = (
        (
            await db.execute(
                select(AgentStep).where(AgentStep.run_id == run.id).order_by(AgentStep.started_at, AgentStep.id)
            )
        )
        .scalars()
        .all()
    )

    explanation = run.explanation or {}
    breakdown = run.trust_breakdown or {}
    answered = run.outcome == "answered"
    return {
        "run_id": str(run.id),
        "conversation_id": str(run.conversation_id) if run.conversation_id else None,
        "user_id": str(run.user_id),
        "kind": run.kind,
        "status": run.status,
        "outcome": run.outcome,
        "outcome_reason": explanation.get("outcome_reason", ""),
        "question": run.query_redacted,
        "answer": run.answer or "",
        "mode": run.mode,
        "route": explanation.get("route", "none"),
        "confidence": run.confidence,
        "trust": breakdown.get("trust") if answered or run.kind == "verify" else None,
        "risk": breakdown.get("risk"),
        "claims": [
            {
                "id": str(c.id),
                "ordinal": c.ordinal,
                "kind": c.signals.get("kind", "claim"),
                "text": c.text,
                "start": c.char_start if c.in_final_answer else None,
                "end": c.char_end if c.in_final_answer else None,
                "verdict": c.verdict,
                "confidence": c.confidence,
                "reason": c.signals.get("reason", ""),
                "in_answer": c.in_final_answer,
                "citations": [
                    {
                        "ref": x.evidence_ref,
                        "quote": x.quote,
                        "quote_start": x.quote_start,
                        "quote_end": x.quote_end,
                        "support": x.support_score,
                        "document_id": str(x.document_id) if x.document_id else None,
                        "document_title": x.document_title,
                        "section_path": x.section_path,
                        "page": x.page,
                    }
                    for x in sorted(citations.get(c.id, []), key=lambda x: -x.support_score)
                ],
                "unsupported_spans": c.unsupported_spans,
                "signals": {k: c.signals.get(k) for k in _SIGNAL_KEYS},
            }
            for c in claims
        ],
        "evidence": run.evidence,
        "graph": {"entities": explanation.get("linked_entities", []), "facts": run.graph_facts},
        "explanation": explanation,
        "withheld_count": run.withheld_count if explanation.get("show_withheld", True) else None,
        "notes": explanation.get("notes", []),
        "steps": [
            {
                "agent": s.agent,
                "label": AGENT_LABELS.get(s.agent, s.agent),
                "status": s.status,
                "started_at": s.started_at.isoformat(),
                "duration_ms": s.duration_ms,
                "summary": s.summary,
                "details": s.details,
                "error": s.error,
            }
            for s in steps
        ],
        "policy_version": run.policy_version,
        "scope": run.access_scope,
        "audit_id": explanation.get("audit_id"),
        "latency_ms": run.latency_ms,
        "llm_calls": run.llm_calls,
        "correction_rounds": run.correction_rounds,
        "needs_review": run.needs_review,
        "error": run.error,
        "started_at": run.started_at.isoformat(),
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
    }
