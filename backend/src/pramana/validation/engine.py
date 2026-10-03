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
from pramana.validation.facts import check_facts, evidence_numbers

log = get_logger(__name__)

CONTRADICTION_THRESHOLD = 0.75
NO_ENTAILMENT = 0.25
NO_SUPPORT = 0.10
CONFLICT_THRESHOLD = 0.85
CONFLICT_MIN_OVERLAP = 0.5
SUPPORTING_SOURCE_THRESHOLD = 0.5
SUPERSEDED_PENALTY = 0.25
CALCULATED_CONFIDENCE = 0.8
MIN_PARTNER_OVERLAP = 0.15
EXTRA_SOURCES_PER_CLAIM = 2
WIDE_CONTEXT_PASSAGES = 2
WIDE_CONTEXT_CHARS = 2400
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

    @property
    def support_threshold(self) -> float:
        """The trained calibrator carries its own decision threshold; the setting is the fallback."""
        return self.calibrator.threshold or self._settings.support_threshold

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
        # The models read natural text: privacy placeholders are swapped for neutral stand-ins,
        # the same on both sides, so a masked name still matches itself.
        texts = {d.ordinal: natural_text(d.text) for d in checkable}
        # What the models are asked to judge. A fragment ("Yes.", "Radiohead") says nothing by itself,
        # so it is read together with the question it answers.
        hypotheses = {d.ordinal: hypothesis_text(question, texts[d.ordinal]) for d in checkable}
        # A passage is read together with where it comes from (document title and section), exactly
        # as the generator saw it: "the 2026 policy" is a fact carried by the title, not the body.
        headers = {e.ref: source_header(e) for e in evidence}
        premises = {key: natural_text(headers[key[1]] + windows[key].text) for key in windows}
        grounding_scores = await self._detectors.grounding([(premises[key], hypotheses[key[0]]) for key in pair_keys])
        grounding = dict(zip(pair_keys, grounding_scores, strict=True))

        # Second look for claims no short window supports: read the whole passage. A sentence such as
        # "the H1 2026 calibration was chaired by …" draws on two places in the same passage.
        widened: set[int] = set()
        weak = [
            d
            for d in checkable
            if max(grounding[(d.ordinal, r)] for r in candidates[d.ordinal]) < SUPPORTING_SOURCE_THRESHOLD
        ]
        if weak:
            wide_keys = [(d.ordinal, ref) for d in weak for ref in candidates[d.ordinal][:WIDE_CONTEXT_PASSAGES]]
            wide = {key: natural_text(headers[key[1]] + by_ref[key[1]].text[:WIDE_CONTEXT_CHARS]) for key in wide_keys}
            wide_scores = await self._detectors.grounding([(wide[key], hypotheses[key[0]]) for key in wide_keys])
            for key, score in zip(wide_keys, wide_scores, strict=True):
                if score > grounding[key]:
                    grounding[key], premises[key] = score, wide[key]
                    widened.add(key[0])
        report.timings_ms["grounding"] = _ms(started)

        # The primary passage is the cited one that supports the claim best; if no cited
        # passage supports it but another does, the claim is checked against that one.
        primary: dict[int, str] = {}
        recited: dict[int, bool] = {}
        for draft in checkable:
            cited = [ref for ref in draft.cited if ref in by_ref]
            # Among passages that were not cited, a current document is preferred to a superseded one:
            # the claim should be measured against what holds today.
            strength = {
                ref: grounding[(draft.ordinal, ref)] - (0.0 if by_ref[ref].is_current else SUPERSEDED_PENALTY)
                for ref in candidates[draft.ordinal]
            }
            ranked = sorted(candidates[draft.ordinal], key=lambda ref: -strength[ref])
            supporting_refs = [r for r in ranked if grounding[(draft.ordinal, r)] >= SUPPORTING_SOURCE_THRESHOLD]
            best_other = next((r for r in supporting_refs if by_ref[r].is_current), None) or (
                supporting_refs[0] if supporting_refs else None
            )
            best_cited = max(cited, key=lambda ref: strength[ref], default=None)
            if best_cited is not None and grounding[(draft.ordinal, best_cited)] >= SUPPORTING_SOURCE_THRESHOLD:
                primary[draft.ordinal], recited[draft.ordinal] = best_cited, False
            elif best_other is not None:
                # Supported, but not by the passage the sentence cites (if it cites one at all).
                primary[draft.ordinal], recited[draft.ordinal] = best_other, bool(cited)
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
            # Two organisations' policies that differ do not contradict each other: a conflict is
            # reported only between documents of the same collection.
            and getattr(by_ref[ref], "collection", "") == getattr(by_ref[primary[d.ordinal]], "collection", "")
            and windows[(d.ordinal, ref)].overlap >= CONFLICT_MIN_OVERLAP
        ]
        # Each claim is read twice against its primary passage: against the surrounding window, and
        # against the single closest sentence. The window catches claims that combine neighbouring
        # sentences; the sentence catches a near-copy with one word changed (a swapped name, a dropped
        # "not"), which a long premise tends to blur into "neutral".
        closest = {key: natural_text(headers[key[1]] + windows[key].quote) for key in nli_keys}
        nli_results = await self._detectors.nli(
            [(premises[key], hypotheses[key[0]]) for key in nli_keys + conflict_keys]
            + [(closest[key], hypotheses[key[0]]) for key in nli_keys]
        )
        nli = dict(zip(nli_keys + conflict_keys, nli_results, strict=False))
        nli_closest = dict(zip(nli_keys, nli_results[len(nli_keys) + len(conflict_keys) :], strict=True))
        report.timings_ms["nli"] = _ms(started)

        # ── Unsupported spans across the whole answer ───────────────────
        started = time.perf_counter()
        joined, offsets = _join_claims([texts[d.ordinal] for d in checkable], [d.ordinal for d in checkable])
        # Only the passages the claims rest on are given to the span detector: shorter input, faster.
        span_context = [natural_text(headers[ref] + by_ref[ref].text) for ref in dict.fromkeys(primary.values())]
        spans = await self._detectors.spans(span_context, question, joined)
        report.timings_ms["spans"] = _ms(started)

        # ── Combine ─────────────────────────────────────────────────────
        calibrator = self.calibrator
        results: dict[int, ClaimResult] = {}
        derived: set[str] = set()  # figures established by calculations earlier in the answer
        all_figures = evidence_numbers("\n".join(e.text for e in by_ref.values()))
        for draft in checkable:
            ref = primary[draft.ordinal]
            window = windows[(draft.ordinal, ref)]
            cited = [r for r in draft.cited if r in by_ref]
            fact_source = "\n".join(headers[r] + by_ref[r].text for r in dict.fromkeys([*cited, ref]))
            facts = check_facts(draft.text, fact_source, derived=derived, operands=all_figures)
            model_text = texts[draft.ordinal]
            claim_spans = _spans_for(model_text, offsets[draft.ordinal], spans)
            span_chars = sum(s["end"] - s["start"] for s in claim_spans)
            if model_text != draft.text:
                claim_spans = []  # offsets refer to the stand-in text, not to what the reader sees
            scores, near = nli[(draft.ordinal, ref)], nli_closest[(draft.ordinal, ref)]
            entailment, contradiction = combine_nli(scores, near)
            signals: dict[str, Any] = {
                "grounding": round(grounding[(draft.ordinal, ref)], 4),
                "entailment": round(entailment, 4),
                "contradiction": round(contradiction, 4),
                "entailment_window": round(scores.entailment, 4),
                "contradiction_window": round(scores.contradiction, 4),
                "entailment_sentence": round(near.entailment, 4),
                "contradiction_sentence": round(near.contradiction, 4),
                "span_fraction": round(min(1.0, span_chars / max(len(model_text), 1)), 4),
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
                    "wide_context": draft.ordinal in widened,
                    "supporting_documents": len(supporting),
                    "numbers_missing": facts.numbers_missing,
                    "names_missing": facts.names_missing,
                    "calculations": facts.calculations,
                    "same_record": same_record(draft.text, by_ref[ref].text),
                    "invalid_citations": [r for r in draft.cited if r not in by_ref],
                    "uncited": not draft.cited,
                    "conflicting_evidence": conflicts,
                    "conflict_with_current": any(by_ref[r].is_current for r in conflicts),
                    "probability": round(probability, 4),
                    "judge": None,
                }
            )
            verdict, reason = _decide(signals, probability, self.support_threshold)
            if not draft.cited:
                # Text checked in Verify Studio carries no citations: there is no "cited" source.
                reason = reason.replace("the cited source", "the closest source")
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
                confidence=_confidence(
                    verdict, probability, calculated=bool(facts.calculations or signals["same_record"])
                ),
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

        # ── Claims that rest on two passages ────────────────────────────
        # "The Marine Superintendent, Captain Dornan, decides …" is stated by no single passage: one
        # gives the office, another its holder. A claim the primary passage does not carry is read
        # once more against that passage and the one that best complements it, together.
        started = time.perf_counter()
        pairs: dict[int, str] = {}
        for claim in results.values():
            if claim.verdict in (Verdict.SUPPORTED, Verdict.CONFLICTING) or claim.signals["numbers_ok"] != 1.0:
                continue
            partner = _partner(claim, windows, by_ref)
            if partner is not None:
                pairs[claim.ordinal] = partner
        if pairs:
            joined_premises = {
                ordinal: natural_text(
                    headers[results[ordinal].signals["primary_evidence"]]
                    + windows[(ordinal, results[ordinal].signals["primary_evidence"])].text
                    + "\n\n"
                    + headers[partner]
                    + windows[(ordinal, partner)].text
                )
                for ordinal, partner in pairs.items()
            }
            order = list(pairs)
            inputs = [(joined_premises[ordinal], hypotheses[ordinal]) for ordinal in order]
            joint_grounding = await self._detectors.grounding(inputs)
            joint_nli = await self._detectors.nli(inputs)
            for ordinal, grounded, read in zip(order, joint_grounding, joint_nli, strict=True):
                claim, partner = results[ordinal], pairs[ordinal]
                primary_ref = claim.signals["primary_evidence"]
                facts = check_facts(
                    claim.text,
                    "\n".join(headers[r] + by_ref[r].text for r in (primary_ref, partner)),
                    derived=derived,
                    operands=all_figures,
                )
                together = {
                    **claim.signals,
                    "grounding": round(grounded, 4),
                    "entailment": round(max(claim.signals["entailment"], read.entailment), 4),
                    "contradiction": round(read.contradiction, 4),
                    "entailment_window": round(read.entailment, 4),
                    "entailment_sentence": round(max(claim.signals["entailment_sentence"], read.entailment), 4),
                    "contradiction_window": round(read.contradiction, 4),
                    "names_ok": 1.0 if facts.names_ok else 0.0,
                    "names_missing": facts.names_missing,
                    "numbers_ok": 1.0 if facts.numbers_ok else 0.0,
                    "numbers_missing": facts.numbers_missing,
                }
                probability = calibrator.probability(together)
                if (
                    probability < self.support_threshold
                    or overruled(together)
                    or read.contradiction >= CONFLICT_THRESHOLD
                ):
                    continue
                together.update({"probability": round(probability, 4), "read_together_with": partner})
                claim.signals = together
                claim.verdict, claim.confidence = Verdict.SUPPORTED, round(probability, 4)
                claim.reason = "supported by two passages read together"
                claim.unsupported_spans = []
                window = windows[(ordinal, partner)]
                if all(c.ref != partner for c in claim.citations):
                    claim.citations.append(
                        CitationResult(partner, window.quote, window.quote_start, window.quote_end, round(grounded, 4))
                    )
        report.timings_ms["joint"] = _ms(started)

        # ── LLM judge for the uncertain band ────────────────────────────
        # Only claims the local checks did not accept are sent, so the judge can rescue a correct
        # sentence the small models were unsure of but can never overturn one they accepted: in a
        # blind test it rejected correct sentences that were well grounded.
        uncertain = [
            c
            for c in results.values()
            if c.verdict not in (Verdict.SUPPORTED, Verdict.CONFLICTING)
            and c.signals["probability"] >= self._settings.uncertain_low
            and c.signals["numbers_ok"] == 1.0
            and not nothing_supports(c.signals)
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
            evidence_text = "\n".join(source_header(by_ref[r]) + windows[(claim.ordinal, r)].text for r in refs)
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
        threshold = self.support_threshold
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


def _partner(claim: ClaimResult, windows: dict[tuple[int, str], Window], by_ref: dict[str, Any]) -> str | None:
    """The passage that best complements the claim's primary one: a cited passage if there is
    another, else the one that holds the names the primary passage lacks, else the closest in wording."""
    primary = claim.signals["primary_evidence"]
    others = [ref for ref in by_ref if ref != primary]
    if not others:
        return None
    cited = [ref for ref in claim.cited if ref in by_ref and ref != primary]
    missing = [name.lower() for name in claim.signals.get("names_missing") or []]

    def score(ref: str) -> tuple[int, int, float]:
        text = by_ref[ref].text.lower()
        return (sum(name in text for name in missing), int(ref in cited), windows[(claim.ordinal, ref)].overlap)

    best = max(others, key=score)
    return best if score(best) > (0, 0, MIN_PARTNER_OVERLAP) else None


_RESTORABLE = re.compile(r"\[[A-Z_]+_\d+\]")


def same_record(claim: str, passage: str) -> bool:
    """The claim links two or more protected values, and one line of the passage holds them all.

    A row of a personnel register reads "Name: [PERSON_1]; Staff ID: [US_SSN_2]; …". A claim that
    gives [US_SSN_2] for [PERSON_1] is exactly what that row records."""
    values = set(_RESTORABLE.findall(claim))
    if len(values) < 2:
        return False
    return any(all(value in line for value in values) for line in passage.splitlines())


def states_the_opposite(signals: dict[str, Any]) -> bool:
    """The closest source sentence contradicts the claim, and no reading of the passage entails it."""
    if signals.get("same_record"):
        return False
    return bool(signals["contradiction_sentence"] >= CONTRADICTION_THRESHOLD and signals["entailment"] < NO_ENTAILMENT)


def nothing_supports(signals: dict[str, Any]) -> bool:
    """Neither the grounding model nor either entailment reading finds any support at all.

    The trained model alone would let some of these through: in real LLM answers most sentences are
    true, so with no evidence either way it leans towards "supported". A verifier must not.
    """
    if signals.get("calculations") or signals.get("same_record"):
        return False  # checked exactly instead: arithmetic, or values found together in one record
    return bool(signals["grounding"] < NO_SUPPORT and signals["entailment"] < NO_SUPPORT)


def overruled(signals: dict[str, Any]) -> bool:
    """Rules that settle a claim as not supported whatever the model's probability says."""
    return not signals["numbers_ok"] or states_the_opposite(signals) or nothing_supports(signals)


def _decide(signals: dict[str, Any], probability: float, support_threshold: float) -> tuple[Verdict, str]:
    if signals["invalid_citations"] and signals["grounding"] < SUPPORTING_SOURCE_THRESHOLD:
        return Verdict.UNSUPPORTED, "cites a source that is not in the evidence"
    if not signals["numbers_ok"]:
        missing = ", ".join(signals["numbers_missing"][:4])
        if signals["contradiction"] >= CONTRADICTION_THRESHOLD:
            return Verdict.CONTRADICTED, f"the source gives a different figure (not found: {missing})"
        return Verdict.UNSUPPORTED, f"figure not found in the cited source: {missing}"
    if states_the_opposite(signals):
        return Verdict.CONTRADICTED, "the cited source states the opposite"
    if nothing_supports(signals):
        return Verdict.UNSUPPORTED, "nothing in the source supports this"
    if signals.get("same_record"):
        # Placeholders are opaque to the entailment models. Protected values that stand together in
        # one row of the cited source, and are stated together, are checked literally instead.
        return Verdict.SUPPORTED, "the protected values are recorded together in one row of the cited source"
    if signals.get("calculations") and signals["names_ok"]:
        # Entailment models cannot follow arithmetic. A calculation whose operands are all in the
        # cited passages and whose result is right is checked exactly instead.
        return Verdict.SUPPORTED, "calculated from figures in the cited sources; the arithmetic was checked"
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


def _confidence(verdict: Verdict, probability: float, *, calculated: bool = False) -> float:
    if verdict in (Verdict.UNSUPPORTED, Verdict.CONTRADICTED):
        return round(min(probability, 0.35), 4)
    if calculated and verdict == Verdict.SUPPORTED:
        return round(max(probability, CALCULATED_CONFIDENCE), 4)
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


FRAGMENT_MAX_WORDS = 4


def hypothesis_text(question: str, claim: str) -> str:
    """The statement the models judge: the claim, or question + claim when the claim is a fragment."""
    question = question.strip()
    if not question or len(claim.split()) > FRAGMENT_MAX_WORDS:
        return claim
    return f"{question if question.endswith('?') else question + '?'} {claim}"


def combine_nli(window: Any, sentence: Any) -> tuple[float, float]:
    """One entailment and one contradiction score from the two readings of a claim.

    Support from either reading counts. A contradiction counts when a reading asserts it and the
    other reading does not entail the claim: a long window sometimes "contradicts" a sentence it
    contains word for word, and the sentence-level reading settles that.
    """
    entailment = max(window.entailment, sentence.entailment)
    contradiction = max(
        min(window.contradiction, 1.0 - sentence.entailment),
        min(sentence.contradiction, 1.0 - window.entailment),
    )
    return entailment, contradiction


def source_header(evidence: Any) -> str:
    """One line naming the document and section a passage belongs to."""
    title = (getattr(evidence, "title", "") or "").strip()
    section = (getattr(evidence, "section_path", "") or "").strip()
    if not title and not section:
        return ""
    if section and section != title and not section.startswith(title):
        return f'From the document "{title}", section "{section}":\n' if title else f'Section "{section}":\n'
    return f'From the document "{section or title}":\n'


_SURROGATE_NAMES = (
    "Alex Morgan",
    "Jordan Blake",
    "Casey Rivera",
    "Taylor Quinn",
    "Morgan Ellis",
    "Riley Harper",
    "Jamie Lawson",
    "Avery Collins",
    "Cameron Doyle",
    "Dana Whitfield",
    "Reese Calloway",
    "Skyler Bennett",
)
_PLACEHOLDER = re.compile(r"\[(?:(REDACTED):([A-Z_]+)|([A-Z_]+)_(\d+))\]")


def natural_text(text: str) -> str:
    """Replace privacy placeholders with neutral, natural-looking stand-ins for the detectors.

    ``[PERSON_2]`` becomes one fixed invented name, ``[REDACTED:PERSON]`` "a named individual", and
    so on. The mapping is deterministic, so the same placeholder reads the same in a claim and in
    its evidence. Nothing is revealed: the stand-ins carry no information about the real value.
    """
    if "[" not in text:
        return text

    def swap(match: re.Match[str]) -> str:
        if match.group(1):
            kind = match.group(2)
            return "a named individual" if kind == "PERSON" else f"a withheld {kind.lower().replace('_', ' ')}"
        kind, number = match.group(3), int(match.group(4))
        if kind == "PERSON":
            return _SURROGATE_NAMES[(number - 1) % len(_SURROGATE_NAMES)]
        if kind == "EMAIL_ADDRESS":
            return f"contact{number}@example.org"
        if kind == "PHONE_NUMBER":
            return f"555-01{number % 100:02d}"
        # An identifier reads as an identifier: the detectors treat a reference number as a value.
        return f"ID-{9000 + number}"

    return _PLACEHOLDER.sub(swap, text)


def _join_claims(texts: list[str], ordinals: list[int]) -> tuple[str, dict[int, int]]:
    offsets: dict[int, int] = {}
    cursor = 0
    for ordinal, text in zip(ordinals, texts, strict=True):
        offsets[ordinal] = cursor
        cursor += len(text) + 1
    return " ".join(texts), offsets


def _spans_for(claim_text: str, offset: int, spans: list[UnsupportedSpan]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    end = offset + len(claim_text)
    for span in spans:
        start_in, end_in = max(span.start, offset), min(span.end, end)
        if end_in - start_in <= 1:
            continue
        out.append(
            {
                "start": start_in - offset,
                "end": end_in - offset,
                "confidence": round(span.confidence, 3),
                "text": claim_text[start_in - offset : end_in - offset],
            }
        )
    return out


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def _ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)
