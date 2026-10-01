"""Runs the validation engine over labelled samples and scores it, next to two baselines:

* ``v1_overlap``     the word-overlap heuristic the first version of this project used
* ``grounding_only`` a single small grounding model with a fixed threshold
* ``cascade``        the full engine: grounding + NLI + spans + literal checks + calibration
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from pramana.evaluation.metrics import auroc, detection_metrics, expected_calibration_error
from pramana.evaluation.samples import VerifierSample
from pramana.shared.types import Verdict
from pramana.validation.calibration import FEATURES, Calibrator
from pramana.validation.engine import ValidationEngine

GROUNDING_ONLY_THRESHOLD = 0.5


@dataclass(slots=True)
class ClaimRow:
    """One checked sentence with its label, the engine's signals and every method's decision."""

    sample_id: str
    source: str
    group: str
    error_type: str
    claim: str
    hallucinated: bool
    signals: dict[str, float]
    probability: float
    verdict: str
    v1_score: float

    @property
    def flagged(self) -> bool:
        return self.verdict not in (Verdict.SUPPORTED, Verdict.CONFLICTING)


def v1_overlap_score(claim: str, evidence: str) -> float:
    """The confidence formula of the first version, reproduced for comparison:
    (0.45 * unigram + 0.35 * bigram + 0.20 * entity) * 175, capped at 98.5; verified at 60 or more."""
    evidence_lower = evidence.lower()
    words = [w for w in claim.lower().split() if len(w) > 2]
    if not words:
        return 95.0
    unigram = sum(1 for w in words if w in evidence_lower) / len(words)
    bigrams = [f"{words[i]} {words[i + 1]}" for i in range(len(words) - 1)]
    bigram = sum(1 for b in bigrams if b in evidence_lower) / len(bigrams) if bigrams else unigram
    keywords = ("policy", "vpn", "mfa", "remote", "wfh", "security", "sha-256", "authentication", "pramana")
    entity = 1.0 if any(k in claim.lower() for k in keywords) else 0.85
    return min(98.5, max(0.0, (0.45 * unigram + 0.35 * bigram + 0.20 * entity) * 100.0 * 1.75))


async def score_samples(
    engine: ValidationEngine, samples: list[VerifierSample], *, progress_every: int = 25, label: str = ""
) -> list[ClaimRow]:
    rows: list[ClaimRow] = []
    started = time.perf_counter()
    for index, sample in enumerate(samples, start=1):
        report = await engine.verify(
            question=sample.question, answer=sample.response, evidence=sample.evidence, use_judge=False
        )
        all_evidence = "\n".join(e.text for e in sample.evidence)
        for claim in report.checkable:
            if sample.hallucinated_spans or sample.response_hallucinated is False:
                hallucinated = sample.sentence_is_hallucinated(claim.start, claim.end)
            else:
                # Only a response-level label exists (HaluEval): every sentence inherits it.
                hallucinated = bool(sample.response_hallucinated)
            rows.append(
                ClaimRow(
                    sample_id=sample.id, source=sample.source, group=sample.group, error_type=sample.error_type,
                    claim=claim.text, hallucinated=hallucinated,
                    signals={name: float(claim.signals.get(name, 0.0)) for name in FEATURES},
                    probability=float(claim.signals.get("probability", claim.confidence)),
                    verdict=str(claim.verdict), v1_score=v1_overlap_score(claim.text, all_evidence),
                )
            )
        if index % progress_every == 0 or index == len(samples):
            elapsed = time.perf_counter() - started
            print(f"  {label} {index}/{len(samples)} responses, {len(rows)} sentences, {elapsed:.0f}s", flush=True)
    return rows


def rescore(rows: list[ClaimRow], calibrator: Calibrator, threshold: float) -> list[ClaimRow]:
    """Apply a (new) calibrator to stored signals, with the engine's literal-check rule."""
    out: list[ClaimRow] = []
    for row in rows:
        probability = calibrator.probability(row.signals)
        supported = probability >= threshold and row.signals["numbers_ok"] == 1.0
        out.append(
            ClaimRow(
                **{**asdict(row), "probability": probability,
                   "verdict": str(Verdict.SUPPORTED if supported else Verdict.UNSUPPORTED)}
            )
        )
    return out


def summarize(rows: list[ClaimRow]) -> dict[str, Any]:
    """Sentence-level and response-level results for the cascade and both baselines."""
    if not rows:
        return {"sentences": 0}
    truth = [r.hallucinated for r in rows]
    methods = {
        "cascade": [r.flagged for r in rows],
        "grounding_only": [r.signals["grounding"] < GROUNDING_ONLY_THRESHOLD for r in rows],
        "v1_overlap": [r.v1_score < 60.0 for r in rows],
    }
    result: dict[str, Any] = {
        "sentences": len(rows),
        "sentence_level": {name: detection_metrics(truth, flags).as_dict() for name, flags in methods.items()},
        "auroc": {
            "cascade": auroc([1 - r.probability for r in rows], truth),
            "grounding_only": auroc([1 - r.signals["grounding"] for r in rows], truth),
            "v1_overlap": auroc([100 - r.v1_score for r in rows], truth),
        },
        "calibration_error": round(expected_calibration_error([r.probability for r in rows], [not t for t in truth]), 4),
    }
    by_sample: dict[str, list[int]] = {}
    for index, row in enumerate(rows):
        by_sample.setdefault(row.sample_id, []).append(index)
    response_truth = [any(truth[i] for i in indexes) for indexes in by_sample.values()]
    result["responses"] = len(by_sample)
    result["response_level"] = {
        name: detection_metrics(response_truth, [any(flags[i] for i in indexes) for indexes in by_sample.values()]).as_dict()
        for name, flags in methods.items()
    }
    kinds = sorted({r.error_type for r in rows if r.error_type and r.error_type != "original"})
    if kinds:
        result["catch_rate_by_error_type"] = {
            kind: {
                name: round(
                    sum(1 for r, f in zip(rows, flags, strict=True) if r.error_type == kind and f)
                    / max(1, sum(1 for r in rows if r.error_type == kind)),
                    4,
                )
                for name, flags in methods.items()
            }
            | {"samples": sum(1 for r in rows if r.error_type == kind)}
            for kind in kinds
        }
    return result


def save_rows(rows: list[ClaimRow], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(asdict(r), ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def load_rows(path: Path) -> list[ClaimRow]:
    return [ClaimRow(**json.loads(line)) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
