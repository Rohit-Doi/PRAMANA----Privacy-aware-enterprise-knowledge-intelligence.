"""Request-level privacy signals: prompt-injection heuristics and the privacy risk score.

Neither of these is the access control. Access is enforced by the scope filter in the data
stores; these signals are for the audit trail, the reviewer and the user's own awareness.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_INJECTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "override instructions",
        re.compile(
            r"\b(?:ignore|disregard|forget|override)\b.{0,40}"
            r"\b(?:previous|prior|above|all|earlier|system)\b.{0,30}\b(?:instruction|prompt|rule|polic)",
            re.I,
        ),
    ),
    (
        "role reassignment",
        re.compile(
            r"\byou are (?:now|no longer)\b"
            r"|\bact as (?:an?|the)\b.{0,30}\b(?:admin|root|developer|system|executive)"
            r"|\bpretend (?:to be|you)\b",
            re.I,
        ),
    ),
    (
        "reveal hidden prompt",
        re.compile(
            r"\b(?:reveal|show|print|repeat|output)\b.{0,40}\b(?:system prompt|hidden|instructions|your rules)", re.I
        ),
    ),
    (
        "privilege claim",
        re.compile(
            r"\b(?:i am|i'm|as) (?:an? |the )?(?:admin|administrator|ceo|executive|root|superuser)\b"
            r"|\bclearance level\s*[:=]?\s*\d"
            r"|\bmy role is\b",
            re.I,
        ),
    ),
    (
        "bypass controls",
        re.compile(
            r"\b(?:bypass|circumvent|disable|turn off)\b.{0,30}"
            r"\b(?:filter|polic|restriction|access control|redaction|security)",
            re.I,
        ),
    ),
    ("delimiter injection", re.compile(r"</?(?:system|assistant|instructions?)>|\[/?INST\]|```\s*system", re.I)),
)
_SENSITIVE_TOPICS = re.compile(
    r"\b(?:salary|salaries|compensation|payroll|bonus|ssn|social security|national id|bank account|"
    r"acquisition|merger|board minutes|earnings|layoff|termination|lawsuit|litigation|breach|"
    r"password|credential|home address|medical|diagnos)\w*",
    re.I,
)


def injection_flags(text: str) -> list[str]:
    """Names of the prompt-injection patterns found in the text."""
    return [name for name, pattern in _INJECTION_PATTERNS if pattern.search(text)]


@dataclass(frozen=True, slots=True)
class RiskAssessment:
    score: float
    level: str
    factors: dict[str, float]
    notes: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "score": round(self.score, 3),
            "level": self.level,
            "factors": {k: round(v, 3) for k, v in self.factors.items()},
            "notes": list(self.notes),
        }


def assess_risk(
    *,
    question: str,
    high_risk_identifiers: int,
    injection: list[str],
    withheld_count: int,
    returned_count: int,
    withheld_max_rank: int | None,
    max_evidence_rank: int,
    user_max_rank: int,
    recent_requests: int,
) -> RiskAssessment:
    """A transparent score in [0, 1]: each factor is listed with its contribution."""
    factors: dict[str, float] = {}
    notes: list[str] = []

    factors["identifiers_in_question"] = 0.2 if high_risk_identifiers else 0.0
    if high_risk_identifiers:
        notes.append(f"{high_risk_identifiers} high-risk identifier(s) in the question were redacted")

    factors["sensitive_topic"] = 0.1 if _SENSITIVE_TOPICS.search(question) else 0.0

    factors["prompt_injection"] = min(0.25, 0.15 * len(injection))
    if injection:
        notes.append("prompt-injection pattern: " + ", ".join(injection))

    total = withheld_count + returned_count
    # Asking mostly for material beyond one's access is the strongest signal.
    factors["beyond_access"] = 0.3 * (withheld_count / total) if total else 0.0
    if withheld_count:
        notes.append(f"{withheld_count} relevant source(s) withheld by policy")
    if withheld_max_rank is not None and withheld_max_rank > user_max_rank:
        factors["beyond_access"] += 0.05 * (withheld_max_rank - user_max_rank)

    factors["sensitive_evidence"] = 0.05 * max_evidence_rank
    factors["request_volume"] = 0.1 if recent_requests > 30 else 0.0
    if recent_requests > 30:
        notes.append("unusually high request volume in the last five minutes")

    score = min(1.0, sum(factors.values()))
    level = "high" if score >= 0.5 else "medium" if score >= 0.25 else "low"
    return RiskAssessment(score=score, level=level, factors=factors, notes=tuple(notes))
