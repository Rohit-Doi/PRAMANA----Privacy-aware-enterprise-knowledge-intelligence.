"""Validation engine: verifies an answer against its evidence, claim by claim.

answer ─► claims ─► align each claim with its cited evidence
                      ├─ grounding score (HHEM)
                      ├─ entailment / contradiction (NLI)
                      ├─ unsupported spans (LettuceDetect)
                      ├─ literal checks: numbers, identifiers, names
                      └─ other sources: do they agree or conflict?
                    ─► calibrated confidence ─► verdict
                    ─► (uncertain claims only) one batched LLM judge call, quote required
"""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

from pramana.llm.gateway import LLMGateway
from pramana.llm.prompts import JUDGE
from pramana.shared.config import get_settings
from pramana.shared.logging import get_logger
from pramana.shared.types import Verdict
from pramana.validation.alignment import Window, best_window
from pramana.validation.calibration import Calibrator, get_calibrator
from pramana.validation.claims import ClaimDraft, extract_claims, is_insufficient
from pramana.validation.detectors import Detectors, UnsupportedSpan
from pramana.validation.facts import check_facts

log = get_logger(__name__)

CONTRADICTION_THRESHOLD = 0.75
CONFLICT_THRESHOLD = 0.85
CONFLICT_MIN_OVERLAP = 0.5
SUPPORTING_SOURCE_THRESHOLD = 0.5
EXTRA_SOURCES_PER_CLAIM = 2
PARTIAL_THRESHOLD = 0.40


class EvidenceLike(Protocol):
    ref: str
    text: str
    document_id: uuid.UUID
    title: str
    section_path: str
    page: int | None
    is_current: bool


@dataclass(slots=True)
class CitationResult:
    ref: str
    quote: str
    quote_start: int
    quote_end: int
    support: float


@dataclass(slots=True)
class ClaimResult:
    ordinal: int
    text: str
    raw: str
    start: int
    end: int
    kind: str
    cited: list[str]
    verdict: Verdict
    confidence: float
    reason: str
    signals: dict[str, Any] = field(default_factory=dict)
    citations: list[CitationResult] = field(default_factory=list)
    unsupported_spans: list[dict[str, Any]] = field(default_factory=list)

    @property
    def supported(self) -> bool:
        return self.verdict == Verdict.SUPPORTED


@dataclass(slots=True)
class VerificationReport:
    claims: list[ClaimResult]
    insufficient: bool = False
    judge_used: bool = False
    timings_ms: dict[str, int] = field(default_factory=dict)
    calibrator_version: str = ""

    @property
    def checkable(self) -> list[ClaimResult]:
        return [c for c in self.claims if c.kind == "claim"]

    @property
    def supported_ratio(self) -> float:
        claims = self.checkable
        return sum(1 for c in claims if c.supported) / len(claims) if claims else 0.0

    @property
    def confidence(self) -> float:
        claims = self.checkable
        return sum(c.confidence for c in claims) / len(claims) if claims else 0.0

    @property
    def failed(self) -> list[ClaimResult]:
        return [c for c in self.checkable if not c.supported]

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for claim in self.checkable:
            out[str(claim.verdict)] = out.get(str(claim.verdict), 0) + 1
        return out


class ValidationEngine:
    def __init__(
        self, detectors: Detectors, *, calibrator: Calibrator | None = None, llm: LLMGateway | None = None
    ) -> None:
        self._detectors = detectors
        self._calibrator = calibrator
        self._llm = llm
        self._settings = get_settings()

    @property
    def calibrator(self) -> Calibrator:
        return self._calibrator or get_calibrator()

    async def verify(
        self,
        *,
        question: str,
        answer: str,
        evidence: list[Any],
        use_judge: bool = True,
        run_id: uuid.UUID | None = None,
        judge_local_only: bool = False,
    ) -> VerificationReport:
        report = VerificationReport(claims=[], calibrator_version=self.calibrator.version)
        if is_insufficient(answer) and len(answer.strip()) < 60:
            report.insufficient = True
            return report
        drafts = extract_claims(answer)
        by_ref: dict[str, Any] = {e.ref: e for e in evidence}
        checkable = [d for d in drafts if d.kind == "claim"]
        if not checkable or not evidence:
            report.claims = [_unverifiable(d, "no evidence was available" if not evidence else "") for d in drafts]
            return report

        # ── Align every claim with every passage (cheap, lexical) ───────
        started = time.perf_counter()
        windows: dict[tuple[int, str], Window] = {
            (d.ordinal, e.ref): best_window(d.text, e.text) for d in checkable for e in evidence
        }
        candidates: dict[int, list[str]] = {}
        for draft in checkable:
            cited = [ref for ref in draft.cited if ref in by_ref]
            others = sorted(
                (e.ref for e in evidence if e.ref not in cited),
                key=lambda ref, d=draft: -windows[(d.ordinal, ref)].overlap,  # type: ignore[misc]
            )[: EXTRA_SOURCES_PER_CLAIM if cited else EXTRA_SOURCES_PER_CLAIM + 2]
            candidates[draft.ordinal] = cited + others
        report.timings_ms["align"] = _ms(started)

        # ── Grounding for each (claim, candidate passage) ───────────────
        started = time.perf_counter()
        pair_keys = [(d.ordinal, ref) for d in checkable for ref in candidates[d.ordinal]]
        texts = {d.ordinal: d.text for d in checkable}
        grounding_scores = await self._detectors.grounding([(windows[key].text, texts[key[0]]) for key in pair_keys])
        grounding = dict(zip(pair_keys, grounding_scores, strict=True))
        report.timings_ms["grounding"] = _ms(started)

        # The primary passage is the cited one that supports the claim best; if no cited
        # passage supports it but another does, the claim is checked against that one.
        primary: dict[int, str] = {}
        recited: dict[int, bool] = {}
        for draft in checkable:
            cited = [ref for ref in draft.cited if ref in by_ref]
            ranked = sorted(candidates[draft.ordinal], key=lambda ref, d=draft: -grounding[(d.ordinal, ref)])  # type: ignore[misc]
            best_cited = max(cited, key=lambda ref, d=draft: grounding[(d.ordinal, ref)], default=None)  # type: ignore[misc]
            if best_cited is not None and grounding[(draft.ordinal, best_cited)] >= SUPPORTING_SOURCE_THRESHOLD:
                primary[draft.ordinal], recited[draft.ordinal] = best_cited, False
            elif ranked and grounding[(draft.ordinal, ranked[0])] >= SUPPORTING_SOURCE_THRESHOLD:
                primary[draft.ordinal], recited[draft.ordinal] = ranked[0], True
            else:
                primary[draft.ordinal], recited[draft.ordinal] = best_cited or ranked[0], False

        # ── Entailment and contradiction ────────────────────────────────
        started = time.perf_counter()
        nli_keys = [(d.ordinal, primary[d.ordinal]) for d in checkable]
        conflict_keys = [
            (d.ordinal, ref)
            for d in checkable
            for ref in candidates[d.ordinal]
            if ref != primary[d.ordinal]
            and by_ref[ref].document_id != by_ref[primary[d.ordinal]].document_id
            and windows[(d.ordinal, ref)].overlap >= CONFLICT_MIN_OVERLAP
        ]
        nli_results = await self._detectors.nli(
            [(windows[key].text, texts[key[0]]) for key in nli_keys + conflict_keys]
        )
        nli = dict(zip(nli_keys + conflict_keys, nli_results, strict=True))
        report.timings_ms["nli"] = _ms(started)

        # ── Unsupported spans across the whole answer ───────────────────
        started = time.perf_counter()
        joined, offsets = _join_claims(checkable)
        spans = await self._detectors.spans([e.text for e in evidence], question, joined)
        report.timings_ms["spans"] = _ms(started)

        # ── Combine ─────────────────────────────────────────────────────
        calibrator = self.calibrator
        results: dict[int, ClaimResult] = {}
        for draft in checkable:
            ref = primary[draft.ordinal]
            window = windows[(draft.ordinal, ref)]
            cited = [r for r in draft.cited if r in by_ref]
            fact_source = "\n".join(by_ref[r].text for r in dict.fromkeys([*cited, ref]))
            facts = check_facts(draft.text, fact_source)
            claim_spans = _spans_for(draft, offsets[draft.ordinal], spans)
            span_chars = sum(s["end"] - s["start"] for s in claim_spans)
            scores = nli[(draft.ordinal, ref)]
            signals: dict[str, Any] = {
                "grounding": round(grounding[(draft.ordinal, ref)], 4),
                "entailment": round(scores.entailment, 4),
                "contradiction": round(scores.contradiction, 4),
                "span_fraction": round(min(1.0, span_chars / max(len(draft.text), 1)), 4),
                "numbers_ok": 1.0 if facts.numbers_ok else 0.0,
                "names_ok": 1.0 if facts.names_ok else 0.0,
                "overlap": round(window.overlap, 4),
            }
            probability = calibrator.probability(signals)

            supporting = {
                str(by_ref[r].document_id)
                for r in candidates[draft.ordinal]
                if grounding[(draft.ordinal, r)] >= SUPPORTING_SOURCE_THRESHOLD
            }
            conflicts = [
                r
                for (ordinal, r) in conflict_keys
                if ordinal == draft.ordinal and nli[(ordinal, r)].contradiction >= CONFLICT_THRESHOLD
            ]
            signals.update(
                {
                    "primary_evidence": ref,
                    "evidence_changed": recited[draft.ordinal],
                    "supporting_documents": len(supporting),
                    "numbers_missing": facts.numbers_missing,
                    "names_missing": facts.names_missing,
                    "invalid_citations": [r for r in draft.cited if r not in by_ref],
                    "uncited": not draft.cited,
                    "conflicting_evidence": conflicts,
                    "conflict_with_current": any(by_ref[r].is_current for r in conflicts),
                    "probability": round(probability, 4),
                    "judge": None,
                }
            )
            verdict, reason = _decide(signals, probability, self._settings.support_threshold)
            citation_refs = [
                ref,
                *[r for r in cited if r != ref and grounding[(draft.ordinal, r)] >= SUPPORTING_SOURCE_THRESHOLD],
            ]
            results[draft.ordinal] = ClaimResult(
                ordinal=draft.ordinal,
                text=draft.text,
                raw=draft.raw,
                start=draft.start,
                end=draft.end,
                kind="claim",
                cited=draft.cited,
                verdict=verdict,
                confidence=_confidence(verdict, probability),
                reason=reason,
                signals=signals,
                citations=[
                    CitationResult(
                        r,
                        windows[(draft.ordinal, r)].quote,
                        windows[(draft.ordinal, r)].quote_start,
                        windows[(draft.ordinal, r)].quote_end,
                        round(grounding[(draft.ordinal, r)], 4),
                    )
                    for r in citation_refs
                ],
                unsupported_spans=claim_spans,
            )

        # ── LLM judge for the uncertain band ────────────────────────────
        uncertain = [
            c
            for c in results.values()
            if self._settings.uncertain_low <= c.signals["probability"] < self._settings.uncertain_high
            and c.signals["numbers_ok"] == 1.0
        ]
        if use_judge and uncertain and self._llm is not None and self._llm.configured:
            started = time.perf_counter()
            try:
                await self._judge(uncertain, windows, by_ref, run_id=run_id, local_only=judge_local_only)
                report.judge_used = True
            except Exception as exc:  # the judge is an optional second opinion
                log.warning("validation.judge_failed", error=repr(exc))
            report.timings_ms["judge"] = _ms(started)

        report.claims = [results[d.ordinal] if d.kind == "claim" else _unverifiable(d, "") for d in drafts]
        return report

    async def _judge(
        self,
        claims: list[ClaimResult],
        windows: dict[tuple[int, str], Window],
        by_ref: dict[str, Any],
        *,
        run_id: uuid.UUID | None,
        local_only: bool,
    ) -> None:
        assert self._llm is not None
        blocks = []
        shown: dict[int, str] = {}
        for claim in claims:
            refs = list(dict.fromkeys([claim.signals["primary_evidence"], *[r for r in claim.cited if r in by_ref]]))[
                :2
            ]
            evidence_text = "\n".join(windows[(claim.ordinal, r)].text for r in refs)
            shown[claim.ordinal] = evidence_text
            blocks.append(
                f"Claim id {claim.ordinal}: {claim.text}\nEvidence for claim {claim.ordinal}:\n{evidence_text}"
            )
        payload, _ = await self._llm.complete_json(
            purpose="judge",
            system=JUDGE.system,
            user="\n\n".join(blocks),
            max_tokens=700,
            run_id=run_id,
            prompt_version=JUDGE.version,
            local_only=local_only,
        )
        verdicts = payload.get("verdicts", []) if isinstance(payload, dict) else payload
        by_ordinal = {c.ordinal: c for c in claims}
        threshold = self._settings.support_threshold
        for item in verdicts if isinstance(verdicts, list) else []:
            try:
                claim = by_ordinal[int(item["id"])]
                verdict = str(item["verdict"]).upper()
            except (KeyError, ValueError, TypeError):
                continue
            quote = str(item.get("quote") or "")
            quote_valid = bool(quote) and _normalise(quote) in _normalise(shown[claim.ordinal])
            claim.signals["judge"] = {"verdict": verdict, "quote_valid": quote_valid}
            if verdict == "SUPPORTED" and quote_valid:
                # A positive verdict counts only with a verbatim quote from the evidence.
                claim.verdict, claim.confidence = Verdict.SUPPORTED, max(claim.confidence, 0.85)
                claim.reason = "supported; confirmed by the judge with a verbatim quote"
            elif verdict == "CONTRADICTED" and quote_valid:
                claim.verdict, claim.confidence = Verdict.CONTRADICTED, min(claim.confidence, 0.15)
                claim.reason = "the judge found a conflicting statement in the source"
            elif verdict in ("UNSUPPORTED", "CONTRADICTED"):
                claim.verdict, claim.confidence = Verdict.UNSUPPORTED, min(claim.confidence, 0.25)
                claim.reason = "the judge could not find this statement in the source"
            elif verdict == "PARTIAL" and claim.confidence < threshold:
                claim.verdict = Verdict.PARTIAL
                claim.reason = "the source supports the main point but not every detail"


def _decide(signals: dict[str, Any], probability: float, support_threshold: float) -> tuple[Verdict, str]:
    if signals["invalid_citations"] and signals["grounding"] < SUPPORTING_SOURCE_THRESHOLD:
        return Verdict.UNSUPPORTED, "cites a source that is not in the evidence"
    if not signals["numbers_ok"]:
        missing = ", ".join(signals["numbers_missing"][:4])
        if signals["contradiction"] >= CONTRADICTION_THRESHOLD:
            return Verdict.CONTRADICTED, f"the source gives a different figure (not found: {missing})"
        return Verdict.UNSUPPORTED, f"figure not found in the cited source: {missing}"
    if signals["contradiction"] >= CONTRADICTION_THRESHOLD and signals["grounding"] < SUPPORTING_SOURCE_THRESHOLD:
        return Verdict.CONTRADICTED, "the cited source states the opposite"
    if probability >= support_threshold:
        if signals["conflicting_evidence"] and signals["conflict_with_current"]:
            return Verdict.CONFLICTING, "supported by one source but contradicted by another current source"
        if signals["conflicting_evidence"]:
            return Verdict.SUPPORTED, "supported by the current source; an older version says otherwise"
        if signals["evidence_changed"]:
            return Verdict.SUPPORTED, "supported, by a different passage than the one cited"
        return Verdict.SUPPORTED, "supported by the cited source"
    if not signals["names_ok"]:
        return Verdict.UNSUPPORTED, "name not found in the cited source: " + ", ".join(signals["names_missing"][:3])
    if probability >= PARTIAL_THRESHOLD:
        return Verdict.PARTIAL, "only partly supported by the cited source"
    return Verdict.UNSUPPORTED, "not found in the cited source"


def _confidence(verdict: Verdict, probability: float) -> float:
    if verdict in (Verdict.UNSUPPORTED, Verdict.CONTRADICTED):
        return round(min(probability, 0.35), 4)
    return round(probability, 4)


def _unverifiable(draft: ClaimDraft, reason: str) -> ClaimResult:
    if draft.kind == "disclaimer":
        return ClaimResult(
            draft.ordinal,
            draft.text,
            draft.raw,
            draft.start,
            draft.end,
            "disclaimer",
            draft.cited,
            Verdict.SUPPORTED,
            1.0,
            "a statement about what the sources do not cover; nothing to verify",
        )
    return ClaimResult(
        draft.ordinal,
        draft.text,
        draft.raw,
        draft.start,
        draft.end,
        "claim",
        draft.cited,
        Verdict.UNSUPPORTED,
        0.0,
        reason or "could not be verified",
    )


def _join_claims(claims: list[ClaimDraft]) -> tuple[str, dict[int, int]]:
    parts: list[str] = []
    offsets: dict[int, int] = {}
    cursor = 0
    for claim in claims:
        offsets[claim.ordinal] = cursor
        parts.append(claim.text)
        cursor += len(claim.text) + 1
    return " ".join(parts), offsets


def _spans_for(claim: ClaimDraft, offset: int, spans: list[UnsupportedSpan]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    end = offset + len(claim.text)
    for span in spans:
        start_in, end_in = max(span.start, offset), min(span.end, end)
        if end_in - start_in <= 1:
            continue
        out.append(
            {
                "start": start_in - offset,
                "end": end_in - offset,
                "confidence": round(span.confidence, 3),
                "text": claim.text[start_in - offset : end_in - offset],
            }
        )
    return out


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def _ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)
