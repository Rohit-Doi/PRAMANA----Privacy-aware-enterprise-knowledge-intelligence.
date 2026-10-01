"""Trust score: one number, four visible parts.

verification        50%   how much of the answer was verified, and how confidently
evidence quality    25%   how relevant, how many independent sources, how current
citation integrity  15%   whether each claim's own citation is the passage that supports it
policy compliance   10%   nothing had to be removed from the output by the final guard
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

WEIGHTS = {"verification": 0.50, "evidence_quality": 0.25, "citation_integrity": 0.15, "policy_compliance": 0.10}


@dataclass(frozen=True, slots=True)
class TrustScore:
    score: float  # 0-100
    badge: str  # "high" | "medium" | "low"
    components: dict[str, float]

    def as_dict(self) -> dict[str, Any]:
        return {
            "score": round(self.score, 1),
            "badge": self.badge,
            "components": {k: round(v, 3) for k, v in self.components.items()},
            "weights": WEIGHTS,
        }


def compute_trust(
    *,
    claim_confidences: list[float],
    claims_total: int,
    claims_kept: int,
    top_rerank_score: float,
    supporting_documents: int,
    all_current: bool,
    citations_correct: int,
    output_redactions: int,
) -> TrustScore:
    if claims_total == 0 or claims_kept == 0:
        components = {
            "verification": 0.0,
            "evidence_quality": 0.0,
            "citation_integrity": 0.0,
            "policy_compliance": 1.0 if output_redactions == 0 else 0.6,
        }
        score = 100 * sum(WEIGHTS[k] * v for k, v in components.items())
        return TrustScore(score, "low", components)

    mean_confidence = sum(claim_confidences) / len(claim_confidences) if claim_confidences else 0.0
    kept_ratio = claims_kept / claims_total
    # An answer that needed sentences removed is trusted less than one that verified whole.
    verification = mean_confidence * (0.7 + 0.3 * kept_ratio)

    relevance = 1.0 / (1.0 + math.exp(-top_rerank_score / 3.0))  # reranker logit -> 0..1
    breadth = min(1.0, 0.6 + 0.2 * supporting_documents)  # one source 0.8, two or more 1.0
    freshness = 1.0 if all_current else 0.85
    evidence_quality = relevance * breadth * freshness

    citation_integrity = citations_correct / claims_kept
    policy_compliance = 1.0 if output_redactions == 0 else 0.6

    components = {
        "verification": verification,
        "evidence_quality": evidence_quality,
        "citation_integrity": citation_integrity,
        "policy_compliance": policy_compliance,
    }
    score = 100 * sum(WEIGHTS[k] * v for k, v in components.items())
    badge = "high" if score >= 80 else "medium" if score >= 60 else "low"
    return TrustScore(score, badge, components)
