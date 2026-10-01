"""Verify Studio: check any text, claim by claim, against the knowledge base or a reference text.

The text may come from anywhere: another AI tool, a draft report, an email. Each sentence is
treated as a claim, evidence for it is retrieved inside the caller's access scope (or taken
from the supplied reference), and the same validation engine used for answers gives a verdict.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, replace
from typing import Any

from pramana.agents.models import QueryRun
from pramana.agents.pipeline import add_steps, store_claims
from pramana.agents.run_view import load_run
from pramana.agents.state import PipelineState
from pramana.auth.principal import Principal
from pramana.governance.audit import AuditTrail
from pramana.governance.trust import compute_trust
from pramana.knowledge.chunking import chunk_document
from pramana.knowledge.parsers import Block, ParsedDocument
from pramana.privacy.pii import PiiEngine, mask_for_reader
from pramana.privacy.policy import PolicyEngine
from pramana.retrieval.retriever import Evidence, HybridRetriever, RetrievalOptions
from pramana.shared.crypto import sha256_hex
from pramana.shared.db import session_scope, utcnow
from pramana.shared.errors import ValidationFailed
from pramana.shared.types import Sensitivity, Verdict
from pramana.validation.claims import extract_claims
from pramana.validation.engine import ValidationEngine

MAX_CLAIMS = 20
MAX_TEXT_CHARS = 12_000
EVIDENCE_PER_CLAIM = 3
_REFERENCE_NAMESPACE = uuid.UUID("5d0e8c8e-1f1b-4c0a-9a57-3f0e6a1b2c3d")


@dataclass(frozen=True, slots=True)
class VerifyRequest:
    text: str
    reference_text: str | None = None
    reference_title: str = "Supplied reference"


class VerifyService:
    def __init__(
        self, *, retriever: HybridRetriever, validation: ValidationEngine, policy: PolicyEngine, pii: PiiEngine
    ) -> None:
        self._retriever = retriever
        self._validation = validation
        self._policy = policy
        self._pii = pii
        self._audit = AuditTrail()

    async def verify(self, request: VerifyRequest, *, principal: Principal) -> dict[str, Any]:
        text = request.text.strip()
        if not text:
            raise ValidationFailed("There is no text to verify")
        if len(text) > MAX_TEXT_CHARS:
            raise ValidationFailed(f"The text is longer than {MAX_TEXT_CHARS} characters; verify it in parts")
        started = time.perf_counter()
        run_id = uuid.uuid4()
        access = await self._policy.access_context(principal)
        scope = access.scope
        redacted = (await asyncio.to_thread(self._pii.redact, text, include_contact=False)).text
        drafts = [d for d in extract_claims(redacted) if d.kind == "claim"]
        if not drafts:
            raise ValidationFailed("No complete sentence was found in the text")
        truncated = len(drafts) > MAX_CLAIMS
        drafts = drafts[:MAX_CLAIMS]
        async with session_scope() as db:
            db.add(
                QueryRun(
                    id=run_id,
                    user_id=principal.user_id,
                    kind="verify",
                    channel="web",
                    mode="strict",
                    status="running",
                    query_redacted=redacted[:4000],
                    started_at=utcnow(),
                )
            )
        steps: list[dict[str, Any]] = []

        # ── Evidence ────────────────────────────────────────────────────
        step_started, step_at = time.perf_counter(), utcnow()
        withheld = 0
        if request.reference_text and request.reference_text.strip():
            evidence = _reference_evidence(request.reference_text, request.reference_title)
            cited = {d.ordinal: [e.ref for e in evidence] for d in drafts}
            source = "the supplied reference"
        else:
            evidence, cited, withheld = await self._retrieve(drafts, principal, scope)
            source = "the knowledge base"
        evidence = [
            _with_text(e, mask_for_reader(e.text, sensitivity_rank=e.sensitivity_rank, can_read_pii=scope.can_read_pii))
            for e in evidence
        ]
        steps.append(
            _step(
                "retrieval",
                step_at,
                step_started,
                f"{len(evidence)} passage(s) from {source} for {len(drafts)} sentence(s)",
            )
        )

        # ── Verification ────────────────────────────────────────────────
        step_started, step_at = time.perf_counter(), utcnow()
        # Each sentence is checked against the passages retrieved for it.
        marked = " ".join(
            d.text.rstrip(".!?") + "".join(f" [{ref}]" for ref in cited.get(d.ordinal, [])[:EVIDENCE_PER_CLAIM]) + "."
            for d in drafts
        )
        # Passages above the egress ceiling are never sent to an external judge.
        may_use_judge = all(e.sensitivity_rank <= access.egress.max_rank for e in evidence)
        report = await self._validation.verify(
            question="",
            answer=marked,
            evidence=evidence,
            use_judge=may_use_judge,
            run_id=run_id,
        )
        steps.append(
            _step(
                "fact_checking",
                step_at,
                step_started,
                f"{len(report.checkable)} sentence(s) checked: "
                + ", ".join(f"{n} {v.lower()}" for v, n in sorted(report.counts().items())),
            )
        )

        kept = [c for c in report.claims if c.verdict in (Verdict.SUPPORTED, Verdict.CONFLICTING)]
        failed = [c for c in report.claims if c not in kept]
        cursor, display_claims = 0, []
        for claim in report.claims:
            display_claims.append(
                {"ordinal": claim.ordinal, "start": cursor, "end": cursor + len(claim.text), "text": claim.text}
            )
            cursor += len(claim.text) + 1
        answer = " ".join(c.text for c in report.claims)
        trust = compute_trust(
            claim_confidences=[c.confidence for c in report.claims],
            claims_total=len(report.claims),
            claims_kept=len(kept),
            top_rerank_score=max((e.rerank_score for e in evidence), default=0.0),
            supporting_documents=len({e.document_id for e in evidence}),
            all_current=all(e.is_current for e in evidence),
            citations_correct=len(kept),
            output_redactions=0,
        ).as_dict()
        latency = int((time.perf_counter() - started) * 1000)
        outcome = "verified" if not failed else "issues_found"

        audit_id = await self._audit.record(
            action="verify.run",
            decision=outcome.upper(),
            principal=principal,
            resource_type="run",
            resource_id=str(run_id),
            details={
                "text_sha256": sha256_hex(text),
                "sentences": len(report.claims),
                "verdicts": report.counts(),
                "source": "reference" if request.reference_text else "knowledge_base",
                "policy_version": scope.policy_version,
                "withheld_count": withheld,
                "latency_ms": latency,
                "evidence": [
                    {"ref": e.ref, "document_id": str(e.document_id), "sensitivity_rank": e.sensitivity_rank}
                    for e in evidence
                ],
            },
        )
        state: PipelineState = {
            "access": access,
            "evidence": evidence,
            "llm_evidence": evidence,
            "round": 0,
            # Every sentence is shown, with its verdict; nothing is removed from the user's own text.
            "display_claims": display_claims,
        }
        async with session_scope() as db:
            run = await db.get(QueryRun, run_id)
            assert run is not None
            run.status, run.outcome, run.answer = "completed", outcome, answer
            run.access_scope, run.policy_version = scope.as_dict(), scope.policy_version
            run.withheld_count, run.confidence = withheld, report.confidence
            run.trust_score = trust["score"]
            run.trust_breakdown = {"trust": trust, "risk": None}
            run.evidence = [e.as_dict() for e in evidence]
            run.explanation = {
                "route": "verify",
                "audit_id": str(audit_id),
                "show_withheld": access.show_withheld_count,
                "notes": (["Only the first 20 sentences were checked"] if truncated else [])
                + ([] if may_use_judge else ["Sensitive evidence: the external LLM judge was not used"]),
                "source": "reference" if request.reference_text else "knowledge_base",
            }
            run.llm_calls = 1 if report.judge_used else 0
            run.latency_ms, run.finished_at = latency, utcnow()
            add_steps(db, run_id, steps)
            await store_claims(db, run_id, state, report.claims, [])
        async with session_scope() as db:
            return await load_run(db, run_id)

    async def _retrieve(
        self, drafts: list[Any], principal: Principal, scope: Any
    ) -> tuple[list[Evidence], dict[int, list[str]], int]:
        options = RetrievalOptions(use_graph=False, expand_context=False, top_k=EVIDENCE_PER_CLAIM, candidates=12)
        semaphore = asyncio.Semaphore(3)

        async def one(claim_text: str) -> Any:
            async with semaphore:
                return await self._retriever.retrieve(claim_text, principal=principal, scope=scope, options=options)

        results = await asyncio.gather(*(one(d.text) for d in drafts))
        merged: dict[tuple[uuid.UUID, ...], Evidence] = {}
        cited: dict[int, list[str]] = {}
        withheld = 0
        for draft, result in zip(drafts, results, strict=True):
            withheld = max(withheld, result.withheld_count)
            refs = []
            for item in result.evidence:
                key = tuple(item.chunk_ids)
                if key not in merged:
                    merged[key] = _with_ref(item, f"E{len(merged) + 1}")
                refs.append(merged[key].ref)
            cited[draft.ordinal] = refs
        return list(merged.values()), cited, withheld


def _reference_evidence(reference: str, title: str) -> list[Evidence]:
    parsed = ParsedDocument(title=title, blocks=[Block("text", p) for p in reference.split("\n\n") if p.strip()])
    _, chunks = chunk_document(parsed, target_tokens=220, max_tokens=320)
    document_id = uuid.uuid5(_REFERENCE_NAMESPACE, sha256_hex(reference))
    return [
        Evidence(
            ref=f"E{i}",
            chunk_ids=[],
            document_id=document_id,
            title=title,
            section_path=c.section_path,
            page=None,
            text=c.text,
            char_start=c.char_start,
            char_end=c.char_end,
            department="",
            sensitivity_rank=int(Sensitivity.PUBLIC),
            region="global",
            source_type="reference",
            collection="reference",
            version="",
            effective_date=None,
            is_current=True,
            rerank_score=0.0,
            fused_score=0.0,
            found_by=["reference"],
        )
        for i, c in enumerate(chunks[:40], start=1)
    ]


def _with_text(evidence: Evidence, text: str) -> Evidence:
    return replace(evidence, text=text)


def _with_ref(evidence: Evidence, ref: str) -> Evidence:
    return replace(evidence, ref=ref)


def _step(agent: str, started_at: Any, started: float, summary: str) -> dict[str, Any]:
    return {
        "agent": agent,
        "status": "completed",
        "started_at": started_at.isoformat(),
        "duration_ms": int((time.perf_counter() - started) * 1000),
        "summary": summary,
        "details": {},
    }
