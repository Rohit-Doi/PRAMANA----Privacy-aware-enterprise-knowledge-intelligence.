"""Extractive reader: finds the words in a passage that answer a question, or says there are none.

A small question-answering model trained on SQuAD 2.0, which includes questions that the passage
does not answer. It is what makes quoting safe: when evidence may not be sent to an external
model, the answer is the sentence that contains the reader's span, and when the reader finds no
span the system says it cannot answer instead of quoting something merely related.
"""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass
from typing import Any

from pramana.shared.config import get_settings
from pramana.shared.logging import get_logger

log = get_logger(__name__)

MAX_TOKENS = 384
STRIDE = 128
MAX_ANSWER_TOKENS = 48
TOP_STARTS = 12


@dataclass(frozen=True, slots=True)
class Reading:
    """The best answer span in one passage."""

    start: int  # character offsets into the passage
    end: int
    text: str
    # Span score minus the model's "no answer here" score. Positive: the passage answers the question.
    margin: float


class ExtractiveReader:
    def __init__(self) -> None:
        self._settings = get_settings()
        self._model: Any = None
        self._tokenizer: Any = None
        self._lock = threading.Lock()

    def _load(self) -> tuple[Any, Any]:
        if self._model is None:
            with self._lock:
                if self._model is None:
                    from transformers import AutoModelForQuestionAnswering, AutoTokenizer

                    name = self._settings.reader_model
                    log.info("model.load", kind="reader", name=name)
                    self._tokenizer = AutoTokenizer.from_pretrained(name)  # type: ignore[no-untyped-call]
                    self._model = AutoModelForQuestionAnswering.from_pretrained(name).eval()
        return self._model, self._tokenizer

    def read_sync(self, question: str, passages: list[str]) -> list[Reading | None]:
        """For each passage, the best answer span and its margin over "no answer" (None if empty)."""
        import torch

        model, tokenizer = self._load()
        results: list[Reading | None] = []
        with self._lock:  # one question at a time: CPU-bound, and the tokenizer is not re-entrant
            for passage in passages:
                if not passage.strip():
                    results.append(None)
                    continue
                encoded = tokenizer(
                    question,
                    passage,
                    truncation="only_second",
                    max_length=MAX_TOKENS,
                    stride=STRIDE,
                    return_overflowing_tokens=True,
                    return_offsets_mapping=True,
                    padding=True,
                    return_tensors="pt",
                )
                offsets = encoded.pop("offset_mapping")
                encoded.pop("overflow_to_sample_mapping", None)
                with torch.no_grad():
                    output = model(**encoded)
                best: Reading | None = None
                for window in range(output.start_logits.shape[0]):
                    start_logits = output.start_logits[window]
                    end_logits = output.end_logits[window]
                    null_score = float(start_logits[0] + end_logits[0])
                    in_context = [i for i, s in enumerate(encoded.sequence_ids(window)) if s == 1]
                    if not in_context:
                        continue
                    first, last = in_context[0], in_context[-1]
                    starts = torch.topk(start_logits[first : last + 1], min(TOP_STARTS, last - first + 1)).indices
                    for start_index in (int(i) + first for i in starts):
                        stop = min(last, start_index + MAX_ANSWER_TOKENS - 1)
                        end_index = int(torch.argmax(end_logits[start_index : stop + 1])) + start_index
                        margin = float(start_logits[start_index] + end_logits[end_index]) - null_score
                        if best is None or margin > best.margin:
                            start_char = int(offsets[window][start_index][0])
                            end_char = int(offsets[window][end_index][1])
                            if end_char > start_char:
                                best = Reading(start_char, end_char, passage[start_char:end_char], margin)
                results.append(best)
        return results

    async def read(self, question: str, passages: list[str]) -> list[Reading | None]:
        return await asyncio.to_thread(self.read_sync, question, passages)

    async def warm_up(self) -> None:
        await self.read("What is this?", ["This is a short passage used to load the model."])


_reader: ExtractiveReader | None = None


def get_reader() -> ExtractiveReader:
    global _reader
    if _reader is None:
        _reader = ExtractiveReader()
    return _reader
