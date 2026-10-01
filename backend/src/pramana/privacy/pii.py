"""Personal-data detection, redaction and reversible pseudonymisation.

Two classes of personal data are treated differently:

* **High-risk identifiers** (government ids, card and bank numbers) are masked everywhere,
  for everyone without ``pii:read``, and are never sent to an external LLM.
* **Contact-level data** (names, email addresses, phone numbers) is ordinary business
  information in PUBLIC and INTERNAL documents, but personal data in CONFIDENTIAL and
  RESTRICTED ones. There it is masked for readers without ``pii:read`` and replaced by
  stable placeholders before any text leaves the machine.
"""

from __future__ import annotations

import re
import threading
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field

from presidio_analyzer import AnalyzerEngine, Pattern, PatternRecognizer
from presidio_analyzer.nlp_engine import NlpEngineProvider

from pramana.shared.config import get_settings
from pramana.shared.types import Sensitivity

HIGH_RISK = frozenset(
    {
        "US_SSN", "CREDIT_CARD", "IBAN_CODE", "US_BANK_NUMBER", "US_PASSPORT",
        "US_DRIVER_LICENSE", "IN_AADHAAR", "IN_PAN", "BANK_ACCOUNT",
    }
)
CONTACT = frozenset({"PERSON", "EMAIL_ADDRESS", "PHONE_NUMBER", "EMPLOYEE_ID", "IP_ADDRESS"})
ALL_ENTITIES = sorted(HIGH_RISK | CONTACT)

# Capitalised phrases that statistical NER regularly mistakes for people in policy text.
_NOT_A_PERSON = frozenset(
    {
        "policy", "act", "regulation", "framework", "standard", "committee", "board", "team",
        "department", "group", "program", "plan", "agreement", "procedure", "guideline",
        "gitlab", "slack", "okta", "google", "zoom", "workday", "vpn", "mfa", "sso", "hipaa",
        "gdpr", "nist", "soc", "iso", "saas", "ceo", "cfo", "cto", "ciso", "hr", "it",
    }
)
_TOKEN = re.compile(r"\[(PERSON|EMAIL_ADDRESS|PHONE_NUMBER|EMPLOYEE_ID|IP_ADDRESS)_(\d+)\]")


@dataclass(frozen=True, slots=True)
class PiiFinding:
    entity_type: str
    start: int
    end: int
    score: float
    text: str

    @property
    def high_risk(self) -> bool:
        return self.entity_type in HIGH_RISK


@dataclass(slots=True)
class Redaction:
    text: str
    findings: list[PiiFinding]

    @property
    def counts(self) -> dict[str, int]:
        return dict(Counter(f.entity_type for f in self.findings))


def _custom_recognizers() -> list[PatternRecognizer]:
    return [
        PatternRecognizer(
            supported_entity="EMPLOYEE_ID",
            patterns=[Pattern("employee id", r"\bEMP-\d{5}\b", 0.9)],
            context=["employee", "staff", "id"],
        ),
        PatternRecognizer(
            supported_entity="BANK_ACCOUNT",
            # A bare digit run is weak evidence; nearby context words lift it over the threshold.
            patterns=[Pattern("account number", r"\b\d{10,18}\b", 0.3)],
            context=["bank", "account", "iban", "routing", "payroll"],
        ),
        PatternRecognizer(
            supported_entity="US_SSN",
            patterns=[Pattern("ssn with dashes", r"\b(?!000|666|9\d\d)\d{3}-(?!00)\d{2}-(?!0000)\d{4}\b", 0.85)],
            context=["ssn", "social", "security"],
        ),
        PatternRecognizer(
            supported_entity="IN_AADHAAR",
            patterns=[Pattern("aadhaar", r"\b[2-9]\d{3}\s\d{4}\s\d{4}\b", 0.7)],
            context=["aadhaar", "uidai"],
        ),
        PatternRecognizer(
            supported_entity="IN_PAN",
            patterns=[Pattern("pan", r"\b[A-Z]{5}\d{4}[A-Z]\b", 0.7)],
            context=["pan", "income", "tax"],
        ),
    ]


class PiiEngine:
    """Wraps Presidio. Thread-safe; the analyzer is built once, on first use."""

    def __init__(self) -> None:
        self._analyzer: AnalyzerEngine | None = None
        self._lock = threading.Lock()
        self._known_names: frozenset[str] = frozenset()

    def set_known_names(self, names: set[str]) -> None:
        """Full names known to be people (for example the employee directory)."""
        self._known_names = frozenset(n.strip().lower() for n in names if n.strip())

    def _engine(self) -> AnalyzerEngine:
        if self._analyzer is None:
            with self._lock:
                if self._analyzer is None:
                    nlp = NlpEngineProvider(
                        nlp_configuration={
                            "nlp_engine_name": "spacy",
                            "models": [{"lang_code": "en", "model_name": get_settings().spacy_model}],
                        }
                    ).create_engine()
                    analyzer = AnalyzerEngine(nlp_engine=nlp, supported_languages=["en"])
                    for recognizer in _custom_recognizers():
                        analyzer.registry.add_recognizer(recognizer)
                    self._analyzer = analyzer
        return self._analyzer

    def analyze(self, text: str, *, min_score: float = 0.5) -> list[PiiFinding]:
        if not text.strip():
            return []
        raw = self._engine().analyze(text=text, language="en", entities=ALL_ENTITIES)
        findings = [
            PiiFinding(r.entity_type, r.start, r.end, float(r.score), text[r.start : r.end])
            for r in raw
            if r.score >= min_score
        ]
        findings = [f for f in findings if self._plausible(f)]
        return _drop_overlaps(findings)

    def _plausible(self, finding: PiiFinding) -> bool:
        if finding.entity_type != "PERSON":
            return True
        value = finding.text.strip()
        if value.lower() in self._known_names:
            return True
        words = value.split()
        if len(words) < 2 or len(words) > 4:
            return False
        if any(w.lower().strip(".,'") in _NOT_A_PERSON for w in words):
            return False
        return all(w[0].isupper() and w.replace("-", "").replace("'", "").replace(".", "").isalpha() for w in words)

    def redact(self, text: str, *, include_contact: bool) -> Redaction:
        """Replace personal data with ``[REDACTED:TYPE]``. High-risk identifiers always go."""
        findings = [f for f in self.analyze(text) if f.high_risk or include_contact]
        return Redaction(text=_replace(text, findings, lambda f: f"[REDACTED:{f.entity_type}]"), findings=findings)


def _drop_overlaps(findings: list[PiiFinding]) -> list[PiiFinding]:
    """Keep the strongest finding where several overlap (high-risk wins, then score, then length)."""
    ordered = sorted(findings, key=lambda f: (not f.high_risk, -f.score, -(f.end - f.start)))
    kept: list[PiiFinding] = []
    for finding in ordered:
        if all(finding.end <= k.start or finding.start >= k.end for k in kept):
            kept.append(finding)
    return sorted(kept, key=lambda f: f.start)


def _replace(text: str, findings: list[PiiFinding], label: Callable[[PiiFinding], str]) -> str:
    out = text
    for finding in sorted(findings, key=lambda f: f.start, reverse=True):
        out = out[: finding.start] + label(finding) + out[finding.end :]
    return out


@dataclass(slots=True)
class Pseudonymizer:
    """Per-request reversible mapping between personal values and placeholders.

    The mapping lives only in memory for the duration of one request. An external LLM
    sees ``[PERSON_1]``; the answer is restored locally, and only for permitted readers.
    """

    engine: PiiEngine
    _forward: dict[tuple[str, str], str] = field(default_factory=dict)
    _reverse: dict[str, tuple[str, str]] = field(default_factory=dict)
    _counters: Counter[str] = field(default_factory=Counter)
    high_risk_removed: Counter[str] = field(default_factory=Counter)

    def protect(self, text: str, *, sensitivity_rank: int, pseudonymize_contact: bool = True) -> str:
        """Text safe to send to an external LLM."""
        findings = self.engine.analyze(text)
        contact_is_personal = pseudonymize_contact and sensitivity_rank >= Sensitivity.CONFIDENTIAL
        selected = [f for f in findings if f.high_risk or contact_is_personal]

        def label(finding: PiiFinding) -> str:
            if finding.high_risk:
                self.high_risk_removed[finding.entity_type] += 1
                return f"[REDACTED:{finding.entity_type}]"
            return self._token(finding.entity_type, finding.text)

        return _replace(text, selected, label)

    def _token(self, entity_type: str, value: str) -> str:
        key = (entity_type, " ".join(value.split()).lower())
        token = self._forward.get(key)
        if token is None:
            self._counters[entity_type] += 1
            token = f"[{entity_type}_{self._counters[entity_type]}]"
            self._forward[key] = token
            self._reverse[token] = (entity_type, " ".join(value.split()))
        return token

    def restore(self, text: str, *, reveal: bool) -> str:
        """Replace placeholders with the original values, or with a neutral mask."""

        def swap(match: re.Match[str]) -> str:
            original = self._reverse.get(match.group(0))
            if original is None:
                return match.group(0)
            return original[1] if reveal else f"[REDACTED:{original[0]}]"

        return _TOKEN.sub(swap, text)

    @property
    def placeholder_count(self) -> int:
        return len(self._reverse)


_engine_singleton: PiiEngine | None = None


def get_pii_engine() -> PiiEngine:
    global _engine_singleton
    if _engine_singleton is None:
        _engine_singleton = PiiEngine()
    return _engine_singleton


def mask_for_reader(text: str, *, sensitivity_rank: int, can_read_pii: bool) -> str:
    """What a human reader is allowed to see of a passage."""
    if can_read_pii:
        return text
    engine = get_pii_engine()
    include_contact = sensitivity_rank >= Sensitivity.CONFIDENTIAL
    return engine.redact(text, include_contact=include_contact).text
