"""Extractive answering: an answer made only of a sentence quoted from the evidence.

Used when the evidence may not be sent to an external model (it is above the egress ceiling
and no local model is configured) and when every LLM provider is unavailable. Nothing is
generated, so nothing can be invented; the cost is a shorter, less fluent answer.

A local reading model finds the words that answer the question and the sentence around them
is quoted. When the reader finds no answer in the passages, nothing is quoted: a sentence that
is merely related to the question is not an answer.
"""

from __future__ import annotations

from pramana.retrieval.reader import ExtractiveReader
from pramana.retrieval.retriever import Evidence
from pramana.shared.text import split_sentences

# Passages are read in order of relevance, and only those close to the best one: measured on the
# golden questions, an answer taken from a clearly less relevant passage was usually the wrong one.
MAX_PASSAGES = 6
RELEVANCE_WINDOW = 2.0
# Reader margin (answer score minus "no answer" score) from which a passage counts as answering.
MIN_MARGIN = 0.0
STRONG_MARGIN = 3.5
FOLLOW_UP_MARGIN = 1.0
MIN_QUOTE_WORDS = 3
MIN_UNPUNCTUATED_WORDS = 8


async def extractive_answer(question: str, evidence: list[Evidence], reader: ExtractiveReader) -> str:
    """The sentence that answers the question, quoted with its reference, or "" if none does."""
    if not evidence:
        return ""
    best = evidence[0].rerank_score
    candidates = [e for e in evidence[:MAX_PASSAGES] if e.rerank_score >= best - RELEVANCE_WINDOW]
    readings = await reader.read(question, [e.text for e in candidates])
    for item, reading in zip(candidates, readings, strict=True):
        if reading is None or reading.margin < MIN_MARGIN:
            continue
        quote = quotable(item.text, reading.start, reading.end)
        if quote:
            return f"{quote.rstrip('.!?')} [{item.ref}]."
    # Nothing among the most relevant passages. A passage the ranker placed lower may still hold the
    # answer when the question is worded unlike the document; it is accepted only if the reader is
    # clearly confident.
    others = [e for e in evidence[:MAX_PASSAGES] if e not in candidates]
    if others:
        ranked = sorted(
            zip(await reader.read(question, [e.text for e in others]), others, strict=True),
            key=lambda pair: -(pair[0].margin if pair[0] else float("-inf")),
        )
        for reading, item in ranked:
            if reading is None or reading.margin < STRONG_MARGIN:
                break
            quote = quotable(item.text, reading.start, reading.end)
            if quote:
                return f"{quote.rstrip('.!?')} [{item.ref}]."
    return ""


def about_the_named(question_names: list[str], quote: str) -> bool:
    """A quoted row answers a question about a named person only if it is that person's row."""
    lowered = quote.lower()
    return all(name.lower() in lowered or name.split()[-1].lower() in lowered for name in question_names)


async def extractive_follow_up(
    question: str, missing: str, terms: list[str], passages: list[Evidence], reader: ExtractiveReader
) -> str:
    """A second quoted sentence that supplies what the first one only pointed to.

    For a missing person the reader is asked who holds each office the first quote named; for a
    missing date or amount it is asked the original question of the new passages.
    """
    if not passages:
        return ""
    questions = [f"Who is the {term}?" for term in terms] if missing == "person" and terms else []
    questions.append(question)
    best: tuple[float, str] | None = None
    for follow_up in questions:
        readings = await reader.read(follow_up, [e.text for e in passages])
        for item, reading in zip(passages, readings, strict=True):
            if reading is None or reading.margin < FOLLOW_UP_MARGIN:
                continue
            quote = quotable(item.text, reading.start, reading.end)
            if quote and (best is None or reading.margin > best[0]):
                best = (reading.margin, f"{quote.rstrip('.!?')} [{item.ref}].")
        if best is not None:
            break
    return best[1] if best else ""


def quotable(text: str, start: int, end: int) -> str:
    """The whole sentence (or sentences) around an answer span; "" if it is not a quotable sentence."""
    spans = [(s, e) for s, e in split_sentences(text) if s < end and start < e]
    if not spans:
        return ""
    quote = text[spans[0][0] : spans[-1][1]].strip()
    # A table row needs its header to mean anything, and a heading is not a statement.
    words = len(quote.split())
    if "|" in quote or "\n" in quote or words < MIN_QUOTE_WORDS:
        return ""
    if quote[-1] not in ".!?" and (words < MIN_UNPUNCTUATED_WORDS or quote.count(": ") >= 2):
        return ""  # a heading, a label, or a block of "Reference: … Classification: …" lines
    return quote
