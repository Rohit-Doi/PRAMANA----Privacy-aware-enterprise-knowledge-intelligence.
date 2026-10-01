"""Calibrated confidence.

Each detector produces a signal. A small logistic model combines them into one number that
means what it says: of the claims given 0.8, about 80% are truly supported. The weights are
fitted on human-labelled data by ``scripts/train_calibrator.py`` and stored next to this file;
until that has been run, conservative hand-set weights are used.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

FEATURES = ("grounding", "entailment", "contradiction", "span_fraction", "numbers_ok", "names_ok", "overlap")
MODEL_PATH = Path(__file__).with_name("calibrator.json")

_DEFAULT_COEFFICIENTS = {
    "grounding": 3.6,
    "entailment": 1.4,
    "contradiction": -2.8,
    "span_fraction": -1.8,
    "numbers_ok": 2.2,
    "names_ok": 0.8,
    "overlap": 1.0,
}
_DEFAULT_INTERCEPT = -4.6


@dataclass(frozen=True, slots=True)
class Calibrator:
    coefficients: dict[str, float]
    intercept: float
    version: str = "default-v1"
    metrics: dict[str, Any] = field(default_factory=dict)
    # Probability at or above which a claim counts as supported; chosen during training.
    threshold: float | None = None

    def probability(self, signals: dict[str, float]) -> float:
        z = self.intercept + sum(self.coefficients[name] * float(signals.get(name, 0.0)) for name in FEATURES)
        return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, z))))

    def to_json(self) -> str:
        return json.dumps(
            {
                "version": self.version,
                "features": list(FEATURES),
                "coefficients": self.coefficients,
                "intercept": self.intercept,
                "threshold": self.threshold,
                "metrics": self.metrics,
            },
            indent=2,
        )

    @classmethod
    def load(cls, path: Path = MODEL_PATH) -> Calibrator:
        if not path.exists():
            return cls(dict(_DEFAULT_COEFFICIENTS), _DEFAULT_INTERCEPT)
        payload = json.loads(path.read_text(encoding="utf-8"))
        if list(payload.get("features", [])) != list(FEATURES):
            raise ValueError("calibrator.json was trained with a different feature set; retrain it")
        return cls(
            {name: float(payload["coefficients"][name]) for name in FEATURES},
            float(payload["intercept"]),
            str(payload.get("version", "trained")),
            dict(payload.get("metrics", {})),
            float(payload["threshold"]) if payload.get("threshold") is not None else None,
        )


_calibrator: Calibrator | None = None


def get_calibrator() -> Calibrator:
    global _calibrator
    if _calibrator is None:
        _calibrator = Calibrator.load()
    return _calibrator


def reload_calibrator() -> Calibrator:
    global _calibrator
    _calibrator = Calibrator.load()
    return _calibrator
