"""Text utilities shared by ingestion and validation: normalisation, sentences, token estimates."""

from __future__ import annotations

import re
import unicodedata

_WS = re.compile(r"[ \t ]+")
_MANY_NEWLINES = re.compile(r"\n{3,}")
_TOKEN = re.compile(r"\w+|[^\w\s]", re.UNICODE)

# Abbreviations after which a full stop does not end the sentence.
_ABBREVIATIONS = frozenset(
    {
        "e.g", "i.e", "etc", "vs", "mr", "mrs", "ms", "dr", "prof", "inc", "ltd", "co", "corp", "llc",
        "no", "nos", "art", "sec", "para", "fig", "st", "jr", "sr", "u.s", "u.k", "e.u", "approx",
        "dept", "est", "cf", "al", "p", "pp", "v", "jan", "feb", "mar", "apr", "jun", "jul", "aug",
        "sep", "sept", "oct", "nov", "dec",
    }
)
_BOUNDARY = re.compile(r"([.!?])([\"')\]]*)\s+(?=[\"'(\[]*[A-Z0-9])")


def normalize_text(text: str) -> str:
    """Unicode NFKC, uniform whitespace, no control characters, at most one blank line."""
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = "".join(ch for ch in text if ch == "\n" or ch == "\t" or not unicodedata.category(ch).startswith("C"))
    lines = [_WS.sub(" ", line).strip() for line in text.split("\n")]
    return _MANY_NEWLINES.sub("\n\n", "\n".join(lines)).strip()


def estimate_tokens(text: str) -> int:
    """Approximate sub-word token count (word and punctuation pieces, plus 15% for word splitting)."""
    return int(len(_TOKEN.findall(text)) * 1.15) + 1


def split_sentences(text: str) -> list[tuple[int, int]]:
    """Sentence spans as (start, end) offsets into ``text``.

    Rule-based on purpose: deterministic, fast and with exact offsets, which the citation
    highlighter and the claim extractor both rely on.
    """
    spans: list[tuple[int, int]] = []
    start = 0
    length = len(text)

    def push(end: int) -> None:
        nonlocal start
        segment_start, segment_end = start, end
        while segment_start < segment_end and text[segment_start].isspace():
            segment_start += 1
        while segment_end > segment_start and text[segment_end - 1].isspace():
            segment_end -= 1
        if segment_end > segment_start:
            spans.append((segment_start, segment_end))
        start = end

    for match in _BOUNDARY.finditer(text):
        end = match.end(2)
        if match.group(1) == ".":
            preceding = text[max(start, match.start() - 12) : match.start()]
            last_word = re.split(r"[\s(\[\"']", preceding)[-1].lower().rstrip(".")
            if last_word in _ABBREVIATIONS:
                continue
            if len(last_word) == 1 and last_word.isalpha():
                continue  # an initial, as in "J. Smith"
            if last_word.isdigit() and re.match(r"\s+[a-z]", text[match.end(2) :]):
                continue
            # A list number at the start of a line ("1. Scope") is not a sentence end.
            line_start = text.rfind("\n", 0, match.start()) + 1
            if text[line_start : match.start()].strip().isdigit():
                continue
        push(end)

    # Newline-separated fragments (headings, bullet items) are their own units.
    push(length)
    refined: list[tuple[int, int]] = []
    for span_start, span_end in spans:
        cursor = span_start
        for line_match in re.finditer(r"\n+", text[span_start:span_end]):
            piece_end = span_start + line_match.start()
            if text[cursor:piece_end].strip():
                refined.append((cursor, piece_end))
            cursor = span_start + line_match.end()
        if text[cursor:span_end].strip():
            refined.append((cursor, span_end))
    return [(s, e) for s, e in ((_trim(text, s, e)) for s, e in refined) if e > s]


def _trim(text: str, start: int, end: int) -> tuple[int, int]:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end


def sentences(text: str) -> list[str]:
    return [text[s:e] for s, e in split_sentences(text)]
