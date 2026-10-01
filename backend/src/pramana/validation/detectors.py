"""Local hallucination detectors. Three small models with different strengths:

* grounding  HHEM-2.1-open: probability that a claim is supported by a premise.
* nli        DeBERTa-v3 NLI: separates "contradicted" from "simply not stated".
* spans      LettuceDetect: marks the exact unsupported words inside an answer.

All run on the CPU, in worker threads, and load on first use.
"""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass
from typing import Any

from pramana.shared.config import get_settings
from pramana.shared.logging import get_logger

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class NliScores:
    entailment: float
    neutral: float
    contradiction: float


@dataclass(frozen=True, slots=True)
class UnsupportedSpan:
    start: int
    end: int
    confidence: float
    text: str


class Detectors:
    def __init__(self) -> None:
        self._settings = get_settings()
        self._lock = threading.Lock()
        self._grounding: Any = None
        self._nli: tuple[Any, Any, dict[str, int]] | None = None
        self._spans: Any = None

    # ── Loaders ─────────────────────────────────────────────────────────
    def _grounding_model(self) -> Any:
        if self._grounding is None:
            with self._lock:
                if self._grounding is None:
                    from transformers import AutoModelForSequenceClassification

                    log.info("model.load", kind="grounding", name=self._settings.grounding_model)
                    # The model ships its own classification head, so the revision is pinned:
                    # the code that runs is exactly the code that was reviewed and cached.
                    self._grounding = AutoModelForSequenceClassification.from_pretrained(
                        self._settings.grounding_model,
                        revision=self._settings.grounding_model_revision,
                        trust_remote_code=True,
                    ).eval()
        return self._grounding

    def _nli_model(self) -> tuple[Any, Any, dict[str, int]]:
        if self._nli is None:
            with self._lock:
                if self._nli is None:
                    from transformers import AutoModelForSequenceClassification, AutoTokenizer

                    name = self._settings.nli_model
                    log.info("model.load", kind="nli", name=name)
                    tokenizer = AutoTokenizer.from_pretrained(name)
                    model = AutoModelForSequenceClassification.from_pretrained(name).eval()
                    labels = {label.lower(): index for index, label in model.config.id2label.items()}
                    self._nli = (tokenizer, model, labels)
        return self._nli

    def _span_model(self) -> Any:
        if self._spans is None:
            with self._lock:
                if self._spans is None:
                    from lettucedetect.models.inference import HallucinationDetector

                    log.info("model.load", kind="spans", name=self._settings.span_model)
                    self._spans = HallucinationDetector(method="transformer", model_path=self._settings.span_model)
        return self._spans

    # ── Synchronous inference ───────────────────────────────────────────
    def grounding_sync(self, pairs: list[tuple[str, str]]) -> list[float]:
        """P(hypothesis is supported by premise) for each (premise, hypothesis) pair."""
        if not pairs:
            return []
        import torch

        model = self._grounding_model()
        scores: list[float] = []
        with torch.no_grad():
            for start in range(0, len(pairs), 8):
                batch = pairs[start : start + 8]
                scores.extend(float(s) for s in model.predict(batch))
        return scores

    def nli_sync(self, pairs: list[tuple[str, str]]) -> list[NliScores]:
        if not pairs:
            return []
        import torch

        tokenizer, model, labels = self._nli_model()
        results: list[NliScores] = []
        with torch.no_grad():
            for start in range(0, len(pairs), 8):
                batch = pairs[start : start + 8]
                encoded = tokenizer(
                    [p for p, _ in batch],
                    [h for _, h in batch],
                    padding=True,
                    truncation="only_first",
                    max_length=512,
                    return_tensors="pt",
                )
                probabilities = torch.softmax(model(**encoded).logits, dim=-1).tolist()
                results.extend(
                    NliScores(row[labels["entailment"]], row[labels["neutral"]], row[labels["contradiction"]])
                    for row in probabilities
                )
        return results

    def spans_sync(self, contexts: list[str], question: str, answer: str) -> list[UnsupportedSpan]:
        if not answer.strip() or not contexts:
            return []
        raw = self._span_model().predict(context=contexts, question=question, answer=answer, output_format="spans")
        return [
            UnsupportedSpan(int(s["start"]), int(s["end"]), float(s.get("confidence", 0.0)), str(s.get("text", "")))
            for s in raw
        ]

    # ── Async wrappers ──────────────────────────────────────────────────
    async def grounding(self, pairs: list[tuple[str, str]]) -> list[float]:
        return await asyncio.to_thread(self.grounding_sync, pairs)

    async def nli(self, pairs: list[tuple[str, str]]) -> list[NliScores]:
        return await asyncio.to_thread(self.nli_sync, pairs)

    async def spans(self, contexts: list[str], question: str, answer: str) -> list[UnsupportedSpan]:
        return await asyncio.to_thread(self.spans_sync, contexts, question, answer)

    async def warm_up(self) -> None:
        await self.grounding([("The sky is blue.", "The sky is blue.")])
        await self.nli([("The sky is blue.", "The sky is blue.")])
        await self.spans(["The sky is blue."], "What colour is the sky?", "The sky is blue.")


_detectors: Detectors | None = None


def get_detectors() -> Detectors:
    global _detectors
    if _detectors is None:
        _detectors = Detectors()
    return _detectors
