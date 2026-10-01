"""Claim extraction: split an answer into checkable sentences and read their citations."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from pramana.llm.prompts import INSUFFICIENT, NOT_COVERED_PREFIX
from pramana.shared.text import split_sentences

CITATION_GROUP = re.compile(r"\s*\[((?:E\d+)(?:\s*[,;]\s*E\d+)*)\]")
_ONLY_CITATIONS = re.compile(r"^(?:\s*\[(?:E\d+)(?:\s*[,;]\s*E\d+)*\]\s*[.]?)+$")
_BULLET = re.compile(r"^\s*(?:[-*•]|\d{1,2}[.)])\s+")
_HAS_WORD = re.compile(r"[A-Za-z]{2}")


@dataclass(slots=True)
class ClaimDraft:
    ordinal: int
    text: str  # the sentence without citation markers: this is what gets verified
    raw: str  # the sentence as written, with markers
    start: int
    end: int
    cited: list[str] = field(default_factory=list)
    kind: str = "claim"  # "claim" | "disclaimer"


def strip_citations(text: str) -> str:
    cleaned = CITATION_GROUP.sub("", text)
    return re.sub(r"\s+([.,;:!?])", r"\1", cleaned).strip()


def citations_in(text: str) -> list[str]:
    refs: list[str] = []
    for group in CITATION_GROUP.findall(text):
        for ref in re.split(r"\s*[,;]\s*", group):
            if ref and ref not in refs:
                refs.append(ref)
    return refs


def is_insufficient(answer: str) -> bool:
    return INSUFFICIENT in answer.upper().replace(" ", "_") or answer.strip().upper().startswith("INSUFFICIENT")


def extract_claims(answer: str) -> list[ClaimDraft]:
    claims: list[ClaimDraft] = []
    for start, end in split_sentences(answer):
        raw = answer[start:end]
        if _ONLY_CITATIONS.match(raw):
            # "… approval. [E1]": the marker landed after the full stop; it belongs to the sentence before.
            if claims:
                previous = claims[-1]
                for ref in citations_in(raw):
                    if ref not in previous.cited:
                        previous.cited.append(ref)
                previous.raw = answer[previous.start : end]
                previous.end = end
            continue
        text = strip_citations(_BULLET.sub("", raw))
        if not _HAS_WORD.search(text):
            continue
        kind = "disclaimer" if text.startswith(NOT_COVERED_PREFIX) or is_insufficient(text) else "claim"
        claims.append(
            ClaimDraft(
                ordinal=len(claims), text=text, raw=raw, start=start, end=end, cited=citations_in(raw), kind=kind
            )
        )
    return claims
