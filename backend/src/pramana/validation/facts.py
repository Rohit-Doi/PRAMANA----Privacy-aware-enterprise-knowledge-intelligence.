"""Deterministic fact checks.

Neural detectors are good at meaning and poor at exact figures: "up to 5 days" and "up to
3 days" look almost the same to them. These checks are literal. Every number, identifier and
proper name in a claim must occur in the evidence the claim cites. They are cheap, exact and
explainable, and they catch the most damaging class of error: a wrong figure.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_NUMBER = re.compile(r"(?<![\w.,])[$€£₹]?\s?(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?\s?(%)?")
_IDENTIFIER = re.compile(r"\b[A-Z]{2,6}-\d{2,}(?:-\d+)*\b|\b\d{1,2}-[a-z]{2}-\d{3,}\b|\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")
_PLACEHOLDER = re.compile(r"\[(?:REDACTED:[A-Z_]+|[A-Z_]+_\d+)\]")
_NAME = re.compile(
    r"\b(?:[A-Z][a-zA-Z&'\-]+|[A-Z]{2,})(?:\s+(?:of|and|the|for|&)?\s?(?:[A-Z][a-zA-Z&'\-]+|[A-Z]{2,}|\d{4}))+\b|\b[A-Z]{3,}\b"
)
_NUMBER_WORDS = {
    "zero": "0",
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
    "ten": "10",
    "eleven": "11",
    "twelve": "12",
    "fifteen": "15",
    "twenty": "20",
    "thirty": "30",
    "forty": "40",
    "fifty": "50",
    "sixty": "60",
    "ninety": "90",
    "hundred": "100",
    "thousand": "1000",
}
_WORD = re.compile(r"[a-z]+")
# Capitalised words that are ordinary vocabulary, not names that must appear in the source.
_COMMON_CAPITALISED = frozenset(
    [
        "The",
        "A",
        "An",
        "This",
        "That",
        "These",
        "Those",
        "It",
        "They",
        "He",
        "She",
        "We",
        "You",
        "If",
        "When",
        "Where",
        "While",
        "After",
        "Before",
        "During",
        "Each",
        "Every",
        "All",
        "Any",
        "No",
        "Not",
        "Only",
        "Both",
        "Either",
        "Neither",
        "However",
        "Therefore",
        "Additionally",
        "Also",
        "In",
        "On",
        "At",
        "For",
        "From",
        "To",
        "Of",
        "By",
        "With",
        "Without",
        "Under",
        "Over",
        "Per",
        "As",
        "Is",
        "Are",
        "Was",
        "Were",
        "Be",
        "Been",
        "Has",
        "Have",
        "Had",
        "May",
        "Must",
        "Should",
        "Can",
        "Will",
        "Not-Covered",
        "Employees",
        "Employee",
        "Managers",
        "Manager",
        "Contractors",
        "Company",
    ]
)


@dataclass(slots=True)
class FactCheck:
    numbers_checked: list[str] = field(default_factory=list)
    numbers_missing: list[str] = field(default_factory=list)
    names_checked: list[str] = field(default_factory=list)
    names_missing: list[str] = field(default_factory=list)

    @property
    def numbers_ok(self) -> bool:
        return not self.numbers_missing

    @property
    def names_ok(self) -> bool:
        return not self.names_missing


def _canonical_number(integer: str, fraction: str | None) -> str:
    value = integer.replace(",", "") + (fraction or "")
    if "." in value:
        value = value.rstrip("0").rstrip(".")
    return value.lstrip("0") or "0"


def numbers_in(text: str) -> list[str]:
    return [_canonical_number(m.group(1), m.group(2)) for m in _NUMBER.finditer(text)]


def evidence_numbers(text: str) -> set[str]:
    """Figures in the evidence, including numbers written as words ("three days" counts as 3)."""
    found = set(numbers_in(text))
    for word in _WORD.findall(text.lower()):
        digit = _NUMBER_WORDS.get(word)
        if digit:
            found.add(digit)
    return found


def _names_in(claim: str) -> list[str]:
    names: list[str] = []
    for match in _NAME.finditer(claim):
        name = match.group(0).strip()
        words = name.split()
        # A lone capitalised word at the start of the sentence is just the first word.
        if match.start() == 0 and len(words) == 1 and not name.isupper():
            continue
        while words and words[0] in _COMMON_CAPITALISED:
            words = words[1:]
        while words and words[-1] in _COMMON_CAPITALISED:
            words = words[:-1]
        if not words or (len(words) == 1 and (words[0] in _COMMON_CAPITALISED or len(words[0]) < 3)):
            continue
        names.append(" ".join(words))
    names.extend(m.group(0) for m in _IDENTIFIER.finditer(claim))
    names.extend(m.group(0) for m in _PLACEHOLDER.finditer(claim))
    return list(dict.fromkeys(names))


def _name_present(name: str, evidence_lower: str) -> bool:
    lowered = name.lower()
    if lowered in evidence_lower:
        return True
    # A multi-word name counts if each of its significant words appears in the evidence.
    words = [w for w in re.findall(r"[a-z0-9@.\-]+", lowered) if len(w) > 2 and w not in ("the", "and", "for")]
    return bool(words) and all(w in evidence_lower for w in words)


def check_facts(claim: str, evidence: str) -> FactCheck:
    result = FactCheck()
    allowed = evidence_numbers(evidence)
    for number in dict.fromkeys(numbers_in(claim)):
        result.numbers_checked.append(number)
        if number not in allowed:
            result.numbers_missing.append(number)
    evidence_lower = evidence.lower()
    for name in _names_in(claim):
        result.names_checked.append(name)
        if not _name_present(name, evidence_lower):
            result.names_missing.append(name)
    return result
