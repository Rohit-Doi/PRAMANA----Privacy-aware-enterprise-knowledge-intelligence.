"""Compare candidate confidence models on the stored verifier signals (no model inference).

    python scripts/analyze_signals.py

Reads data/eval/verifier_signals_{train,test}.jsonl written by train_calibrator.py and reports,
for each candidate, the held-out catch rate at the false-alarm limit, AUROC and calibration
error per data source. Used to decide which model train_calibrator.py should fit; it changes
nothing by itself.
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
from pramana.validation.calibration import FEATURES
from pramana.validation.engine import CONTRADICTION_THRESHOLD, SUPPORTING_SOURCE_THRESHOLD

MAX_FALSE_ALARM = 0.10


def base(rows: list[ClaimRow]) -> np.ndarray:
    return np.array([[row.signals[name] for name in FEATURES] for row in rows])


def with_interactions(rows: list[ClaimRow]) -> np.ndarray:
    x = base(rows)
    grounding, entailment, contradiction = x[:, 0], x[:, 1], x[:, 2]
    extra = np.column_stack(
        [
            np.minimum(grounding, entailment),
            np.maximum(grounding, entailment),
            grounding * entailment,
            contradiction * (1 - grounding),
        ]
    )
    return np.hstack([x, extra])


def weights(rows: list[ClaimRow]) -> np.ndarray:
    sources = sorted({row.source.split("-")[0] for row in rows})
    counts = {s: sum(1 for row in rows if row.source.startswith(s)) for s in sources}
    return np.array([len(rows) / (len(sources) * counts[row.source.split("-")[0]]) for row in rows])


def hard_fail(rows: list[ClaimRow]) -> np.ndarray:
    """The engine's rules that override the probability (see ``engine._decide``)."""
    return np.array(
        [
            r.signals["numbers_ok"] != 1.0
            or (
                r.signals["contradiction"] >= CONTRADICTION_THRESHOLD
                and r.signals["grounding"] < SUPPORTING_SOURCE_THRESHOLD
            )
            for r in rows
        ]
    )


def threshold_for(probabilities: np.ndarray, supported: np.ndarray, failed: np.ndarray) -> float:
    """Highest catch rate with the false-alarm rate within the limit (as train_calibrator does)."""
    best, best_catch = 0.5, -1.0
    for step in range(20, 91):
        t = step / 100
        flagged = (probabilities < t) | failed
        false_alarm = flagged[supported == 1].mean()
        catch = flagged[supported == 0].mean()
        if false_alarm <= MAX_FALSE_ALARM and catch > best_catch:
            best, best_catch = t, catch
    return best


def report(name: str, train_p: np.ndarray, test_p: np.ndarray, train: list[ClaimRow], test: list[ClaimRow]) -> None:
    y_train = np.array([0 if r.hallucinated else 1 for r in train])
    y_test = np.array([0 if r.hallucinated else 1 for r in test])
    failed_test = hard_fail(test)
    threshold = threshold_for(train_p, y_train, hard_fail(train))
    print(f"\n{name}  (threshold {threshold:.2f})")
    for source in ("ragtruth", "injected", "halueval"):
        mask = np.array([r.source.startswith(source) for r in test])
        if not mask.any():
            continue
        flagged = (test_p[mask] < threshold) | failed_test[mask]
        y = y_test[mask]
        catch = flagged[y == 0].mean() if (y == 0).any() else float("nan")
        false_alarm = flagged[y == 1].mean() if (y == 1).any() else float("nan")
        precision = (y[flagged] == 0).mean() if flagged.any() else float("nan")
        f1 = 2 * precision * catch / (precision + catch) if precision + catch > 0 else 0.0
        auc = roc_auc_score(1 - y, 1 - test_p[mask]) if len(set(y)) > 1 else float("nan")
        ece = expected_calibration_error(list(test_p[mask]), [bool(v) for v in y])
        print(
            f"  {source:<9} n={mask.sum():<5} catch {catch:.3f}  false alarm {false_alarm:.3f}  "
            f"precision {precision:.3f}  F1 {f1:.3f}  AUROC {auc:.3f}  ECE {ece:.3f}"
        )


def main() -> int:
    eval_dir = get_settings().data_dir / "eval"
    train = load_rows(eval_dir / "verifier_signals_train.jsonl")
    test = load_rows(eval_dir / "verifier_signals_test.jsonl")
    y = np.array([0 if r.hallucinated else 1 for r in train])
    w = weights(train)
    print(f"{len(train)} training sentences, {len(test)} held-out sentences")

    logistic = LogisticRegression(C=1.0, max_iter=2000).fit(base(train), y, sample_weight=w)
    report(
        "logistic, 7 signals",
        logistic.predict_proba(base(train))[:, 1],
        logistic.predict_proba(base(test))[:, 1],
        train,
        test,
    )

    interactions = LogisticRegression(C=1.0, max_iter=5000).fit(with_interactions(train), y, sample_weight=w)
    report(
        "logistic, 7 signals + 4 interactions",
        interactions.predict_proba(with_interactions(train))[:, 1],
        interactions.predict_proba(with_interactions(test))[:, 1],
        train,
        test,
    )

    boosted = HistGradientBoostingClassifier(max_depth=3, max_iter=150, learning_rate=0.06, l2_regularization=1.0)
    boosted.fit(base(train), y, sample_weight=w)
    report(
        "gradient boosting, 7 signals",
        boosted.predict_proba(base(train))[:, 1],
        boosted.predict_proba(base(test))[:, 1],
        train,
        test,
    )

    for index, name in enumerate(FEATURES):
        column = base(test)[:, index]
        y_test = np.array([0 if r.hallucinated else 1 for r in test])
        if len(set(column)) > 1:
            print(f"  single signal {name:<14} AUROC {roc_auc_score(y_test, column):.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
