"""Evidence alignment: find, inside a passage, the sentences most relevant to a claim.

Verification models work best on a short premise. For each claim we therefore pick a window
of a few sentences around the best-matching sentence, and keep that sentence as the quote
shown to the reader.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from pramana.shared.text import split_sentences
from pramana.validation.facts import numbers_in

_TOKEN = re.compile(r"[a-z0-9][a-z0-9\-']*")
_STOP = frozenset(
    ["the", "a", "an", "and", "or", "of", "to", "in", "on", "at", "for", "from", "by", "with", "as", "is", "are", "was", "were", "be", "been", "being", "has", "have", "had", "it", "its", "this", "that", "these", "those", "their", "there", "than", "then", "which", "who", "whom", "whose", "will", "would", "should", "could", "may", "might", "must", "can", "not", "no", "but", "if", "when", "while", "per", "up", "any", "all", "each", "every", "such", "other", "into"]
)


def content_tokens(text: str) -> set[str]:
    tokens = set()
    for token in _TOKEN.findall(text.lower()):
        if token in _STOP or len(token) < 2:
            continue
        if len(token) > 4 and token.endswith("ies"):
            token = token[:-3] + "y"
        elif len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
            token = token[:-1]
        tokens.add(token)
    return tokens


@dataclass(frozen=True, slots=True)
class Window:
    text: str  # premise handed to the verification models
    quote: str  # the single best sentence
    quote_start: int  # offsets of the quote inside the evidence text
    quote_end: int
    overlap: float  # share of the claim's content words found in the window


def best_window(claim: str, evidence: str, *, radius: int = 2, max_chars: int = 1400) -> Window:
    spans = split_sentences(evidence)
    if not spans:
        return Window(evidence[:max_chars], evidence[:300], 0, min(len(evidence), 300), 0.0)
    claim_tokens = content_tokens(claim)
    claim_numbers = set(numbers_in(claim))
    best_index, best_score = 0, -1.0
    for index, (start, end) in enumerate(spans):
        sentence = evidence[start:end]
        tokens = content_tokens(sentence)
        score = len(claim_tokens & tokens) / max(len(claim_tokens), 1)
        if claim_numbers and claim_numbers & set(numbers_in(sentence)):
            score += 0.35  # a sentence carrying the same figure is very likely the source
        if score > best_score:
            best_index, best_score = index, score

    low, high = best_index, best_index
    while (high - low) < 2 * radius and (low > 0 or high < len(spans) - 1):
        grew = False
        if high < len(spans) - 1 and spans[high + 1][1] - spans[low][0] <= max_chars:
            high += 1
            grew = True
        if (high - low) < 2 * radius and low > 0 and spans[high][1] - spans[low - 1][0] <= max_chars:
            low -= 1
            grew = True
        if not grew:
            break
    window_text = evidence[spans[low][0] : spans[high][1]]
    quote_start, quote_end = spans[best_index]
    overlap = len(claim_tokens & content_tokens(window_text)) / max(len(claim_tokens), 1)
    return Window(window_text, evidence[quote_start:quote_end], quote_start, quote_end, overlap)
