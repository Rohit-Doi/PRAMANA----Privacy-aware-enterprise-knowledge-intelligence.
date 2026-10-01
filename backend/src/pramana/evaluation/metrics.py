"""Metrics for the evaluation suites. "Positive" always means: a hallucination was flagged."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class DetectionMetrics:
    samples: int
    hallucinated: int
    catch_rate: float  # recall on hallucinated items
    false_alarm_rate: float  # supported items wrongly flagged
    precision: float
    f1: float
    balanced_accuracy: float
    accuracy: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "samples": self.samples,
            "hallucinated": self.hallucinated,
            "catch_rate": round(self.catch_rate, 4),
            "false_alarm_rate": round(self.false_alarm_rate, 4),
            "precision": round(self.precision, 4),
            "f1": round(self.f1, 4),
            "balanced_accuracy": round(self.balanced_accuracy, 4),
            "accuracy": round(self.accuracy, 4),
        }


def detection_metrics(is_hallucinated: list[bool], flagged: list[bool]) -> DetectionMetrics:
    tp = sum(1 for truth, flag in zip(is_hallucinated, flagged, strict=True) if truth and flag)
    fn = sum(1 for truth, flag in zip(is_hallucinated, flagged, strict=True) if truth and not flag)
    fp = sum(1 for truth, flag in zip(is_hallucinated, flagged, strict=True) if not truth and flag)
    tn = sum(1 for truth, flag in zip(is_hallucinated, flagged, strict=True) if not truth and not flag)
    catch = tp / (tp + fn) if tp + fn else 0.0
    false_alarm = fp / (fp + tn) if fp + tn else 0.0
    precision = tp / (tp + fp) if tp + fp else 0.0
    f1 = 2 * precision * catch / (precision + catch) if precision + catch else 0.0
    total = tp + fn + fp + tn
    return DetectionMetrics(
        samples=total,
        hallucinated=tp + fn,
        catch_rate=catch,
        false_alarm_rate=false_alarm,
        precision=precision,
        f1=f1,
        balanced_accuracy=(catch + (1 - false_alarm)) / 2,
        accuracy=(tp + tn) / total if total else 0.0,
    )


def expected_calibration_error(probabilities: list[float], is_supported: list[bool], bins: int = 10) -> float:
    """Average gap between stated confidence and observed accuracy, weighted by bin size."""
    if not probabilities:
        return 0.0
    totals = [0] * bins
    confidence_sum = [0.0] * bins
    correct_sum = [0.0] * bins
    for probability, supported in zip(probabilities, is_supported, strict=True):
        index = min(bins - 1, int(probability * bins))
        totals[index] += 1
        confidence_sum[index] += probability
        correct_sum[index] += 1.0 if supported else 0.0
    count = len(probabilities)
    return sum(
        totals[i] / count * abs(confidence_sum[i] / totals[i] - correct_sum[i] / totals[i])
        for i in range(bins)
        if totals[i]
    )


def auroc(scores_for_hallucination: list[float], is_hallucinated: list[bool]) -> float | None:
    """Probability that a hallucinated item gets a higher risk score than a supported one."""
    from sklearn.metrics import roc_auc_score

    if len(set(is_hallucinated)) < 2:
        return None
    return float(roc_auc_score(is_hallucinated, scores_for_hallucination))


def ndcg_at(ranks: list[int | None], k: int) -> float:
    """nDCG@k with one relevant item per query: 1/log2(1+rank) if found within k, else 0."""
    if not ranks:
        return 0.0
    return sum(1.0 / math.log2(1 + r) if r is not None and r <= k else 0.0 for r in ranks) / len(ranks)


def recall_at(ranks: list[int | None], k: int) -> float:
    return sum(1 for r in ranks if r is not None and r <= k) / len(ranks) if ranks else 0.0


def mrr(ranks: list[int | None]) -> float:
    return sum(1.0 / r for r in ranks if r is not None) / len(ranks) if ranks else 0.0


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    low, high = math.floor(position), math.ceil(position)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)
