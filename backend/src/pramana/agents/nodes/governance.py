"""Governance & Audit Agent: the last gate before anything is shown.

* restores placeholders for readers allowed to see personal data, masks them for everyone else
* runs the output guard (no high-risk identifier may leave unless the reader holds pii:read)
* computes the trust score and the privacy risk assessment, each with its breakdown
* builds the explanation: which entities, graph facts and passages led to which sentence
"""

from __future__ import annotations

from typing import Any

from pramana.agents.state import AgentDeps, PipelineState
from pramana.governance.trust import compute_trust
from pramana.privacy.pii import HIGH_RISK
from pramana.privacy.risk import assess_risk
from pramana.shared.types import Verdict

MESSAGES = {
    "no_access_scope": "Your account has no access scope under the current policy, so no sources can be consulted. Ask an administrator to check your role.",
    "missing_permission": "Your account is not permitted to ask questions.",
    "no_evidence": "I could not find anything about this in the sources you can access.",
    "insufficient_evidence": "The sources you can access do not contain enough information to answer this.",
    "unverified": "I found related sources, but could not verify an answer against them, so I am not giving one.",
    "llm_unavailable": "The language model is unavailable right now and no sentence in the sources answers this directly. Please try again later.",
}


async def run(state: PipelineState, deps: AgentDeps) -> dict[str, Any]:
    access = state.get("access")
    scope = access.scope if access else None
    can_read_pii = bool(scope and scope.can_read_pii)
    outcome = state.get("outcome") or "abstained"
    reason = state.get("outcome_reason", "")
    retrieval = state.get("retrieval")
    evidence = state.get("evidence", [])
    kept = [c for c in state.get("kept_claims", []) if c.kind == "claim"]
    removed = state.get("removed_claims", [])
    pseudonymizer = state.get("pseudonymizer")

    # ── Text shown to the reader ────────────────────────────────────────
    redactions = 0
    display_claims: list[dict[str, Any]] = []

    def for_reader(text: str) -> str:
        nonlocal redactions
        if pseudonymizer is not None:
            text = pseudonymizer.restore(text, reveal=can_read_pii)
        if not can_read_pii:
            # Output guard: whatever happened upstream, no high-risk identifier is released.
            guarded = deps.pii.redact(text, include_contact=False)
            redactions += len(guarded.findings)
            text = guarded.text
        return text

    if outcome == "answered":
        cursor = 0
        parts: list[str] = []
        for claim in state.get("kept_claims", []):
            sentence = for_reader(claim.raw.strip())
            parts.append(sentence)
            display_claims.append(
                {
                    "ordinal": claim.ordinal,
                    "start": cursor,
                    "end": cursor + len(sentence),
                    "text": for_reader(claim.text),
                }
            )
            cursor += len(sentence) + 1
        answer = " ".join(parts)
    else:
        answer = MESSAGES.get(reason, MESSAGES["insufficient_evidence"])
        withheld = retrieval.withheld_count if retrieval else 0
        if withheld and access is not None and access.show_withheld_count and outcome != "denied":
            answer += f" {withheld} relevant source(s) exist but are restricted by your access policy."

    # ── Trust ───────────────────────────────────────────────────────────
    total_claims = len(kept) + len([c for c in removed if c.kind == "claim"])
    supporting_documents = len(
        {e.document_id for c in kept for e in evidence if e.ref in {cit.ref for cit in c.citations}}
    )
    trust = compute_trust(
        claim_confidences=[c.confidence for c in kept],
        claims_total=total_claims,
        claims_kept=len(kept),
        top_rerank_score=max((e.rerank_score for e in evidence), default=-10.0),
        supporting_documents=supporting_documents,
        all_current=all(e.is_current for e in evidence if any(e.ref == cit.ref for c in kept for cit in c.citations)),
        citations_correct=sum(
            1
            for c in kept
            if not c.signals.get("evidence_changed")
            and not c.signals.get("invalid_citations")
            and not c.signals.get("uncited")
        ),
        output_redactions=redactions,
    )

    # ── Privacy risk ────────────────────────────────────────────────────
    pii_counts = state.get("query_pii", {})
    risk = assess_risk(
        question=state.get("query_redacted", ""),
        high_risk_identifiers=sum(n for kind, n in pii_counts.items() if kind in HIGH_RISK),
        injection=state.get("injection", []),
        withheld_count=retrieval.withheld_count if retrieval else 0,
        returned_count=len(evidence),
        withheld_max_rank=retrieval.withheld_max_rank if retrieval else None,
        max_evidence_rank=max((e.sensitivity_rank for e in evidence), default=0),
        user_max_rank=scope.max_rank_own_dept if scope else 0,
        recent_requests=state.get("recent_requests", 0),
    )

    # ── Explanation: the path from question to each sentence ───────────
    explanation: dict[str, Any] = {
        "linked_entities": [
            {"key": e.key, "name": e.name, "type": e.type, "matched_by": e.matched_by}
            for e in (retrieval.linked_entities if retrieval else [])
        ],
        "graph_facts": [
            {
                "head": f.head,
                "relation": f.relation,
                "tail": f.tail,
                "document_id": str(f.document_id),
                "chunk_id": str(f.chunk_id),
            }
            for f in (retrieval.graph_facts if retrieval else [])
            if any(f.document_id == e.document_id for e in evidence)
        ][:12],
        "evidence_paths": [
            {"ref": e.ref, "found_by": e.found_by, "ranks": e.ranks, "relevance": round(e.rerank_score, 3)}
            for e in evidence
        ],
        "claim_support": [
            {"claim": c.ordinal, "evidence": [cit.ref for cit in c.citations], "verdict": str(c.verdict)} for c in kept
        ],
        "notes": state.get("evidence_notes", []),
        "route": state.get("route", "none"),
        "removed_sentences": len([c for c in removed if c.kind == "claim"]),
        "conflicts": [c.ordinal for c in kept if c.verdict == Verdict.CONFLICTING],
    }

    summary = (
        f"Outcome: {outcome}; trust {trust.score:.0f}/100 ({trust.badge}); "
        f"privacy risk {risk.level} ({risk.score:.2f}); output guard removed {redactions} identifier(s)"
    )
    return {
        "outcome": outcome,
        "display_answer": answer,
        "trust": trust.as_dict(),
        "risk": risk.as_dict(),
        "explanation": explanation,
        "output_redactions": redactions,
        "display_claims": display_claims,
        "_summary": summary,
        "_details": {
            "trust": trust.as_dict(),
            "risk": risk.as_dict(),
            "output_redactions": redactions,
            "outcome": outcome,
            "reason": reason,
        },
    }
