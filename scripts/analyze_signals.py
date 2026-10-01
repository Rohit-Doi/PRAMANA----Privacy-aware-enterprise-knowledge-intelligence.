"""Compare candidate confidence models on the stored verifier signals (no model inference).

    python scripts/analyze_signals.py

Reads data/eval/verifier_signals_{train,test}.jsonl written by train_calibrator.py and reports,
for each candidate, held-out catch rate and false-alarm rate at the operating point (chosen on
the training half under the false-alarm limit), AUROC and calibration error per data source.
It answers "would a more flexible model do better?" and changes nothing by itself.

What it showed when the current model was chosen (see docs/DECISIONS.md):
    * gradient boosting fits the injected errors better and real LLM answers (RAGTruth) worse;
    * a rule "nearly verbatim but not entailed" catches every injected error and, on RAGTruth,
      raises ten false alarms for each real error it finds — so it is not used.
"""

from __future__ import annotations

import sys

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score

from pramana.evaluation.metrics import expected_calibration_error
from pramana.evaluation.verifier import ClaimRow, load_rows
from pramana.shared.config import get_settings
from pramana.validation.calibration import DIRECTIONS, FEATURES, Calibrator
from pramana.validation.engine import overruled

MAX_FALSE_ALARM = 0.10


def matrix(rows: list[ClaimRow]) -> np.ndarray:
    return np.array([[row.signals[name] for name in FEATURES] for row in rows])


def labels(rows: list[ClaimRow]) -> np.ndarray:
    return np.array([0 if row.hallucinated else 1 for row in rows])  # 1 = supported


def weights(rows: list[ClaimRow]) -> np.ndarray:
    sources = sorted({row.source.split("-")[0] for row in rows})
    counts = {s: sum(1 for row in rows if row.source.startswith(s)) for s in sources}
    return np.array([len(rows) / (len(sources) * counts[row.source.split("-")[0]]) for row in rows])


def threshold_for(probabilities: np.ndarray, supported: np.ndarray, failed: np.ndarray) -> float:
    """Highest catch rate with the false-alarm rate within the limit (as train_calibrator does)."""
    best, best_catch = 0.5, -1.0
    for step in range(20, 91):
        threshold = step / 100
        flagged = (probabilities < threshold) | failed
        false_alarm = flagged[supported == 1].mean()
        catch = flagged[supported == 0].mean()
        if false_alarm <= MAX_FALSE_ALARM and catch > best_catch:
            best, best_catch = threshold, catch
    return best


def report(name: str, train_p: np.ndarray, test_p: np.ndarray, train: list[ClaimRow], test: list[ClaimRow]) -> None:
    failed_train = np.array([overruled(r.signals) for r in train])
    failed_test = np.array([overruled(r.signals) for r in test])
    y_test = labels(test)
    threshold = threshold_for(train_p, labels(train), failed_train)
    print(f"\n{name}  (threshold {threshold:.2f})")
    for source in ("ragtruth", "injected", "halueval"):
        mask = np.array([r.source.startswith(source) for r in test])
        if not mask.any():
            continue
        flagged = (test_p[mask] < threshold) | failed_test[mask]
        y = y_test[mask]
        catch = flagged[y == 0].mean()
        false_alarm = flagged[y == 1].mean()
        precision = (y[flagged] == 0).mean() if flagged.any() else float("nan")
        f1 = 2 * precision * catch / (precision + catch) if precision + catch > 0 else 0.0
        auc = roc_auc_score(1 - y, 1 - test_p[mask])
        ece = expected_calibration_error(list(test_p[mask]), [bool(v) for v in y])
        print(
            f"  {source:<9} n={mask.sum():<5} catch {catch:.3f}  false alarm {false_alarm:.3f}  "
            f"precision {precision:.3f}  F1 {f1:.3f}  AUROC {auc:.3f}  ECE {ece:.3f}"
        )


def main() -> int:
    eval_dir = get_settings().data_dir / "eval"
    train = load_rows(eval_dir / "verifier_signals_train.jsonl")
    test = load_rows(eval_dir / "verifier_signals_test.jsonl")
    y, w = labels(train), weights(train)
    print(f"{len(train)} training sentences, {len(test)} held-out sentences")

    current = Calibrator.load()
    report(
        f"current model ({current.version})",
        np.array([current.probability(r.signals) for r in train]),
        np.array([current.probability(r.signals) for r in test]),
        train,
        test,
    )

    free = LogisticRegression(C=1.0, max_iter=5000).fit(matrix(train), y, sample_weight=w)
    report(
        "logistic, signs unconstrained",
        free.predict_proba(matrix(train))[:, 1],
        free.predict_proba(matrix(test))[:, 1],
        train,
        test,
    )

    boosted = HistGradientBoostingClassifier(
        max_depth=3,
        max_iter=200,
        learning_rate=0.05,
        l2_regularization=1.0,
        min_samples_leaf=20,
        monotonic_cst=[DIRECTIONS[name] for name in FEATURES],
    )
    boosted.fit(matrix(train), y, sample_weight=w)
    report(
        "gradient boosting, monotonic",
        boosted.predict_proba(matrix(train))[:, 1],
        boosted.predict_proba(matrix(test))[:, 1],
        train,
        test,
    )

    print("\nsingle signals, held-out AUROC for telling supported from unsupported:")
    y_test = labels(test)
    for index, name in enumerate(FEATURES):
        column = matrix(test)[:, index] * DIRECTIONS[name]
        if len(set(column)) > 1:
            print(f"  {name:<24} {roc_auc_score(y_test, column):.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
