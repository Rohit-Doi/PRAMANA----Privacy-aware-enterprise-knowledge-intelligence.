"""Extractive answering: an answer made only of sentences quoted from the evidence.

Used when the evidence may not be sent to an external model (it is above the egress ceiling
and no local model is configured) and when every LLM provider is unavailable. Nothing is
generated, so nothing can be invented; the cost is a less fluent answer.
"""

from __future__ import annotations

from pramana.retrieval.local_models import RetrievalModels
from pramana.retrieval.retriever import Evidence
from pramana.shared.text import split_sentences

MAX_SENTENCES = 4
MIN_SCORE = 0.0
SCORE_WINDOW = 5.0


async def extractive_answer(question: str, evidence: list[Evidence], models: RetrievalModels) -> str:
    """The most relevant sentences from the best passages, each with its reference."""
    candidates: list[tuple[int, int, str, str]] = []  # evidence index, sentence index, ref, sentence
    for evidence_index, item in enumerate(evidence[:4]):
        for sentence_index, (start, end) in enumerate(split_sentences(item.text)):
            sentence = item.text[start:end].strip()
            # Headings and fragments make poor answers.
            if len(sentence.split()) >= 5 and sentence[-1:] in ".!?":
                candidates.append((evidence_index, sentence_index, item.ref, sentence))
    if not candidates:
        return ""
    scores = await models.rerank(question, [c[3] for c in candidates])
    ranked = sorted(zip(scores, candidates, strict=True), key=lambda pair: -pair[0])
    best = ranked[0][0]
    if best < MIN_SCORE:
        return ""
    chosen = [c for score, c in ranked[:MAX_SENTENCES] if score >= max(MIN_SCORE, best - SCORE_WINDOW)]
    chosen.sort(key=lambda c: (c[0], c[1]))  # back into reading order
    return " ".join(f"{sentence.rstrip('.!?')} [{ref}]." for _, _, ref, sentence in chosen)
