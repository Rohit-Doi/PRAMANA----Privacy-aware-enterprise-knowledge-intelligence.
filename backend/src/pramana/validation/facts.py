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
# Multipliers that documents write in words ("one and a half times the hourly rate").
_PHRASE_NUMBERS = {
    "one and a half": "1.5",
    "time and a half": "1.5",
    "two and a half": "2.5",
    "twice": "2",
    "double": "2",
    "half": "0.5",
    "quarter": "0.25",
}
_SCALE = {"thousand": 1e3, "million": 1e6, "billion": 1e9}
_CALC_NUMBER = r"[$€£₹]?\s?(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?(?:\s?(thousand|million|billion)\b)?\s?%?"
_SCALED = re.compile(r"(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?\s?(thousand|million|billion)\b")
_CALC_OPERATOR = r"\s*([+\-−–×x*/÷])\s*"
# A calculation written out in full: "$46,800 / 2,080 = $22.50", "$34.5 million − $32,832,000 = $1,668,000".
_CALCULATION = re.compile(rf"(?<![\w.,]){_CALC_NUMBER}(?:{_CALC_OPERATOR}{_CALC_NUMBER})+\s*=\s*{_CALC_NUMBER}")
_CALC_TOKEN = re.compile(rf"{_CALC_NUMBER}|[+\-−–×x*/÷=]")
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
    # Calculations in the claim whose operands are in the evidence and whose arithmetic is right.
    calculations: list[str] = field(default_factory=list)

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
    lowered = text.lower()
    found.update(value for phrase, value in _PHRASE_NUMBERS.items() if phrase in lowered)
    # "$34.5 million" may be written out in full in the answer.
    for match in _SCALED.finditer(lowered):
        found.add(_plain(float(_canonical_number(match.group(1), match.group(2))) * _SCALE[match.group(3)]))
    return found


def _plain(value: float) -> str:
    """The canonical spelling of a number, as ``numbers_in`` would give it."""
    text = f"{value:.4f}".rstrip("0").rstrip(".")
    return text.lstrip("0") or "0" if not text.startswith("0.") else text


def verified_calculations(claim: str, allowed: set[str]) -> tuple[list[str], set[str]]:
    """Calculations in the claim that hold, and the figures they establish.

    A calculation holds when every operand is a figure from the evidence (or the result of an
    earlier verified calculation) and the stated result is what the arithmetic gives. Its result,
    and a figure written with a scale word ("$34.5 million"), then count as supported figures.
    """
    held: list[str] = []
    established: set[str] = set()
    for match in _CALCULATION.finditer(claim):
        values: list[float] = []
        operators: list[str] = []
        spellings: list[set[str]] = []
        for token in _CALC_TOKEN.finditer(match.group(0)):
            if token.group(1) is None:
                operators.append(token.group(0))
                continue
            written = _canonical_number(token.group(1), token.group(2))
            value = float(written) * _SCALE.get(token.group(3) or "", 1.0)
            values.append(value)
            # A table may give the figure in its header's unit ("Ceiling, $ million: 34.5").
            in_units = {_plain(value / scale) for scale in _SCALE.values() if value >= scale}
            spellings.append({written, _plain(value)} | in_units)
        if len(values) < 3 or len(operators) != len(values) - 1 or operators[-1] != "=":
            continue
        *operands, stated = values
        known = allowed | established
        if not all(spelling & known for spelling in spellings[:-1]):
            continue
        result = _evaluate(operands, operators[:-1])
        if result is None or abs(result - stated) > max(0.005, abs(stated) * 0.0005):
            continue
        held.append(match.group(0).strip())
        established |= {_plain(stated), *spellings[-1]} - _in_units_only(stated, spellings[-1])
        for spelling in spellings[:-1]:
            established |= spelling
    return held, established


def _in_units_only(value: float, spellings: set[str]) -> set[str]:
    """Spellings that only make sense next to a unit ("1.668" for 1,668,000): not free-standing figures."""
    return {s for s in spellings if s != _plain(value) and float(s) != value}


def _evaluate(operands: list[float], operators: list[str]) -> float | None:
    """Left to right, with multiplication and division first."""
    terms = [operands[0]]
    signs = [1.0]
    for operator, value in zip(operators, operands[1:], strict=True):
        if operator in "×x*":
            terms[-1] *= value
        elif operator in "/÷":
            if value == 0:
                return None
            terms[-1] /= value
        else:
            signs.append(1.0 if operator == "+" else -1.0)
            terms.append(value)
    return sum(sign * term for sign, term in zip(signs, terms, strict=True))


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


def check_facts(
    claim: str, evidence: str, *, derived: set[str] | None = None, operands: set[str] | None = None
) -> FactCheck:
    """``derived`` carries figures established by verified calculations in earlier sentences of the
    same answer, and is extended with those established here. ``operands`` are the figures of every
    passage shown: a calculation may combine passages the sentence does not cite one by one."""
    result = FactCheck()
    allowed = evidence_numbers(evidence) | (derived or set())
    result.calculations, established = verified_calculations(claim, allowed | (operands or set()))
    allowed |= established
    if derived is not None:
        derived |= established
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
