"""Sensitivity suggestion for documents.

The label on a document is a human decision. This module only proposes one, with reasons,
from signals a reviewer can check: personal data found and tell-tale terms. A suggestion
higher than the chosen label is shown as a warning; it never changes access on its own.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from pramana.privacy.pii import HIGH_RISK
from pramana.shared.types import Sensitivity

_RESTRICTED_TERMS = (
    "acquisition",
    "merger",
    "board of directors",
    "board minutes",
    "material non-public",
    "unreleased earnings",
    "executive compensation",
    "national id",
    "social security number",
    "bank account",
    "incident report",
    "data breach",
    "codename",
    "term sheet",
    "severance",
)
_CONFIDENTIAL_TERMS = (
    "confidential",
    "salary band",
    "compensation",
    "payroll",
    "forecast",
    "penetration test",
    "vulnerability",
    "litigation",
    "privileged",
    "performance review",
    "calibration",
    "non-disclosure",
    "contract",
    "agreement",
    "indemnif",
    "pricing",
    "customer list",
)
_MARKER = re.compile(r"classification:\s*(public|internal|confidential|restricted)", re.I)


@dataclass(frozen=True, slots=True)
class SensitivitySuggestion:
    rank: Sensitivity
    reasons: tuple[str, ...]


def suggest_sensitivity(text: str, pii_counts: dict[str, int], *, token_count: int) -> SensitivitySuggestion:
    lowered = text.lower()
    reasons: list[str] = []
    rank = Sensitivity.INTERNAL

    marker = _MARKER.search(text[:3000])
    if marker:
        declared = Sensitivity.parse(marker.group(1))
        reasons.append(f"the document declares itself {declared.name}")
        rank = max(rank, declared)

    high_risk = {k: v for k, v in pii_counts.items() if k in HIGH_RISK}
    if high_risk:
        listed = ", ".join(f"{count} {kind}" for kind, count in sorted(high_risk.items()))
        reasons.append(f"high-risk identifiers found ({listed})")
        rank = max(rank, Sensitivity.RESTRICTED)

    contact = sum(v for k, v in pii_counts.items() if k not in HIGH_RISK)
    density = contact / max(token_count, 1) * 1000
    if contact >= 8 and density >= 4:
        reasons.append(f"dense personal data ({contact} names or contact details)")
        rank = max(rank, Sensitivity.CONFIDENTIAL)

    restricted_hits = [t for t in _RESTRICTED_TERMS if t in lowered]
    if len(restricted_hits) >= 2:
        reasons.append("restricted-topic terms: " + ", ".join(restricted_hits[:4]))
        rank = max(rank, Sensitivity.RESTRICTED)
    confidential_hits = [t for t in _CONFIDENTIAL_TERMS if t in lowered]
    if len(confidential_hits) >= 2:
        reasons.append("confidential-topic terms: " + ", ".join(confidential_hits[:4]))
        rank = max(rank, Sensitivity.CONFIDENTIAL)

    if not reasons:
        reasons.append("no personal data or sensitive terms found")
    return SensitivitySuggestion(rank=rank, reasons=tuple(reasons))
