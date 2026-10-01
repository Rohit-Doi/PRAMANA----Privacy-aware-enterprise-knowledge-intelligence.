"""Train the confidence calibrator and benchmark the verifier.

    python scripts/train_calibrator.py                    # default sizes (about an hour on a laptop CPU)
    python scripts/train_calibrator.py --ragtruth 200 --halueval 150 --handbook-chunks 100

What it does
    1. Builds labelled sentences from three sources: RAGTruth (human-annotated LLM answers),
       HaluEval (labelled answers) and sentences from this corpus with known injected errors.
    2. Runs the detectors on every sentence and stores the signals.
    3. Splits by source document, fits a logistic model on one half, picks the decision
       threshold, and saves it as backend/src/pramana/validation/calibrator.json.
    4. Scores the other half, next to two baselines (the v1 word-overlap heuristic and a
       single grounding model), and records the results for the Validation Reports page.

Nothing here calls an external service.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime

import numpy as np
from scipy.optimize import minimize
from sqlalchemy import String, cast, func, select

from pramana.evaluation.metrics import detection_metrics
from pramana.evaluation.samples import (
    VerifierSample,
    build_injected_samples,
    load_halueval,
    load_ragtruth,
    split_by_group,
)
from pramana.evaluation.store import gate, save_eval_run
from pramana.evaluation.verifier import ClaimRow, load_rows, rescore, save_rows, score_samples, summarize
from pramana.knowledge.models import Chunk, Document
from pramana.shared.config import get_settings
from pramana.shared.db import dispose_engine, session_scope, utcnow
from pramana.validation.calibration import DIRECTIONS, FEATURES, MODEL_PATH, Calibrator
from pramana.validation.detectors import get_detectors
from pramana.validation.engine import ValidationEngine

MAX_FALSE_ALARM = 0.10


async def corpus_chunks(handbook_limit: int) -> list[tuple[str, str, str]]:
    """Passages used for injected-error samples: every Aurelia policy plus a handbook sample."""
    async with session_scope() as db:
        aurelia = await db.execute(
            select(Chunk.id, Chunk.document_id, Chunk.text)
            .join(Document, Document.id == Chunk.document_id)
            .where(Document.collection == "aurelia", Document.status == "ready")
            .order_by(Chunk.id)
        )
        handbook = await db.execute(
            select(Chunk.id, Chunk.document_id, Chunk.text)
            .join(Document, Document.id == Chunk.document_id)
            .where(Document.collection == "handbook", Document.status == "ready", Chunk.token_count >= 120)
            # A fixed pseudo-random order: the same passages are sampled on every run.
            .order_by(func.md5(cast(Chunk.id, String)))
            .limit(handbook_limit)
        )
        return [(str(c), str(d), t) for c, d, t in [*aurelia.all(), *handbook.all()]]


def fit_logistic(x: np.ndarray, y: np.ndarray, weights: np.ndarray, l2: float = 1.0) -> tuple[np.ndarray, float]:
    """Weighted, L2-regularised logistic regression whose coefficients keep their expected sign."""
    count, width = x.shape
    total = weights.sum()

    def loss(theta: np.ndarray) -> tuple[float, np.ndarray]:
        z = np.clip(x @ theta[:width] + theta[width], -30, 30)
        p = 1.0 / (1.0 + np.exp(-z))
        log_loss = -(weights * (y * np.log(p + 1e-12) + (1 - y) * np.log(1 - p + 1e-12))).sum() / total
        slope = weights * (p - y) / total
        gradient = np.concatenate([x.T @ slope + l2 * theta[:width] / count, [slope.sum()]])
        return float(log_loss + 0.5 * l2 * (theta[:width] ** 2).sum() / count), gradient

    bounds = [(0, None) if DIRECTIONS[name] > 0 else (None, 0) for name in FEATURES] + [(None, None)]
    result = minimize(loss, np.zeros(width + 1), jac=True, bounds=bounds, method="L-BFGS-B")
    return result.x[:width], float(result.x[width])


def fit(rows: list[ClaimRow]) -> tuple[Calibrator, dict[str, float]]:
    x = np.array([[row.signals[name] for name in FEATURES] for row in rows])
    y = np.array([0 if row.hallucinated else 1 for row in rows])  # 1 = supported
    # Each data source gets the same total weight, so the larger one does not dominate.
    sources = sorted({row.source.split("-")[0] for row in rows})
    counts = {s: sum(1 for row in rows if row.source.startswith(s)) for s in sources}
    weights = np.array([len(rows) / (len(sources) * counts[row.source.split("-")[0]]) for row in rows])
    coefficients, intercept = fit_logistic(x, y, weights)
    calibrator = Calibrator(
        coefficients={name: float(c) for name, c in zip(FEATURES, coefficients, strict=True)},
        intercept=intercept,
        version="trained-" + datetime.now().strftime("%Y%m%d-%H%M"),
    )
    return calibrator, {s: float(counts[s]) for s in sources}


def choose_threshold(rows: list[ClaimRow], calibrator: Calibrator) -> tuple[float, dict[str, float]]:
    """Highest catch rate at which the model raises false alarms within the limit, on the training half.

    The limit is applied to the model's own decisions. The engine's overriding rules (a missing
    figure, a stated opposite, no support at all) are fixed, apply on top, and are included in every
    result that is reported."""
    truth = [r.hallucinated for r in rows]
    probabilities = [calibrator.probability(r.signals) for r in rows]
    best = (0.5, -1.0, 1.0)
    for step in range(20, 91):
        threshold = step / 100
        result = detection_metrics(truth, [p < threshold for p in probabilities])
        if result.false_alarm_rate <= MAX_FALSE_ALARM and result.catch_rate > best[1]:
            best = (threshold, result.catch_rate, result.false_alarm_rate)
    return best[0], {"train_catch_rate": round(best[1], 4), "train_false_alarm_rate": round(best[2], 4)}


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ragtruth", type=int, default=400, help="RAGTruth responses to use")
    parser.add_argument("--halueval", type=int, default=300, help="HaluEval answers to use")
    parser.add_argument("--handbook-chunks", type=int, default=160, help="handbook passages for injected errors")
    parser.add_argument("--injected", type=int, default=1200, help="maximum injected-error sentences")
    parser.add_argument("--reuse", action="store_true", help="reuse stored signals instead of recomputing them")
    args = parser.parse_args()
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

    settings = get_settings()
    eval_dir = settings.data_dir / "eval"
    started_at = utcnow()
    train_path, test_path = eval_dir / "verifier_signals_train.jsonl", eval_dir / "verifier_signals_test.jsonl"

    if args.reuse and train_path.exists() and test_path.exists():
        train_rows, test_rows = load_rows(train_path), load_rows(test_path)
        print(f"reusing stored signals: {len(train_rows)} training and {len(test_rows)} test sentences")
    else:
        ragtruth = load_ragtruth(eval_dir / "ragtruth_test.jsonl", limit=args.ragtruth)
        halueval = load_halueval(eval_dir / "halueval_qa.jsonl", limit=args.halueval)
        injected = build_injected_samples(await corpus_chunks(args.handbook_chunks))
        rng_order = sorted(injected, key=lambda s: s.id)
        injected = rng_order[:: max(1, len(rng_order) // args.injected)][: args.injected]
        print(
            f"samples: {len(ragtruth)} RAGTruth responses, {len(injected)} injected sentences, {len(halueval)} HaluEval answers"
        )

        ragtruth_train, ragtruth_test = split_by_group(ragtruth)
        injected_train, injected_test = split_by_group(injected)
        engine = ValidationEngine(get_detectors(), calibrator=Calibrator.load(MODEL_PATH.with_name("__none__.json")))
        await get_detectors().warm_up()

        async def score(samples: list[VerifierSample], label: str) -> list[ClaimRow]:
            rows = await score_samples(engine, samples, label=label)
            # Each part is kept as soon as it is done: an hour of inference is not lost to a late failure.
            save_rows(rows, eval_dir / "parts" / f"{label.lower().replace(' ', '_')}.jsonl")
            return rows

        train_rows = await score(ragtruth_train, "RAGTruth train") + await score(injected_train, "injected train")
        test_rows = (
            await score(ragtruth_test, "RAGTruth test")
            + await score(injected_test, "injected test")
            + await score(halueval, "HaluEval")
        )
        save_rows(train_rows, train_path)
        save_rows(test_rows, test_path)

    # ── Fit ─────────────────────────────────────────────────────────────
    calibrator, source_counts = fit(train_rows)
    threshold, threshold_stats = choose_threshold(train_rows, calibrator)
    print(f"\nfitted on {len(train_rows)} sentences {source_counts}; threshold {threshold:.2f} {threshold_stats}")
    print(
        "coefficients:",
        {k: round(v, 3) for k, v in calibrator.coefficients.items()},
        "intercept",
        round(calibrator.intercept, 3),
    )

    # ── Evaluate on held-out data ───────────────────────────────────────
    rescored = rescore(test_rows, calibrator, threshold)
    results: dict[str, dict] = {}
    for source in ("ragtruth", "injected", "halueval"):
        subset = [r for r in rescored if r.source.startswith(source)]
        if subset:
            results[source] = summarize(subset)
    overall = summarize([r for r in rescored if not r.source.startswith("halueval")])

    def line(name: str, block: dict) -> str:
        return (
            f"{name:<15} catch {block['catch_rate']:.3f}  false alarm {block['false_alarm_rate']:.3f}  "
            f"precision {block['precision']:.3f}  F1 {block['f1']:.3f}"
        )

    for source, summary in results.items():
        print(f"\n== {source}: {summary['sentences']} sentences, {summary['responses']} responses (held out)")
        for method, block in summary["sentence_level"].items():
            print("  sentence  " + line(method, block))
        for method, block in summary["response_level"].items():
            print("  response  " + line(method, block))
        print(f"  AUROC {summary['auroc']}  calibration error {summary['calibration_error']}")
        if "catch_rate_by_error_type" in summary:
            print("  catch rate by error type:", json.dumps(summary["catch_rate_by_error_type"]))

    calibrator = Calibrator(
        coefficients=calibrator.coefficients,
        intercept=calibrator.intercept,
        version=calibrator.version,
        threshold=threshold,
        metrics={
            "trained_on_sentences": len(train_rows),
            "sources": source_counts,
            **threshold_stats,
            "held_out": {
                source: {
                    "sentences": s["sentences"],
                    "catch_rate": s["sentence_level"]["cascade"]["catch_rate"],
                    "false_alarm_rate": s["sentence_level"]["cascade"]["false_alarm_rate"],
                    "f1": s["sentence_level"]["cascade"]["f1"],
                    "calibration_error": s["calibration_error"],
                }
                for source, s in results.items()
            },
        },
    )
    MODEL_PATH.write_text(calibrator.to_json() + "\n", encoding="utf-8")
    print(f"\nsaved {MODEL_PATH}")

    # ── Record for the reports page ─────────────────────────────────────
    config = {
        "calibrator": calibrator.version,
        "threshold": threshold,
        "models": {"grounding": settings.grounding_model, "nli": settings.nli_model, "spans": settings.span_model},
    }
    if "injected" in results:
        cascade = results["injected"]["sentence_level"]["cascade"]
        await save_eval_run(
            suite="verifier_injected",
            sample_count=results["injected"]["sentences"],
            metrics=results["injected"],
            gates=[
                gate("catch rate on injected errors", cascade["catch_rate"], minimum=0.90),
                gate("false alarm rate", cascade["false_alarm_rate"], maximum=0.10),
                gate("calibration error", results["injected"]["calibration_error"], maximum=0.10),
            ],
            config=config,
            started_at=started_at,
            notes="Sentences from this corpus, unchanged or corrupted (number, meaning, entity, unrelated). Held-out half.",
        )
    if "ragtruth" in results:
        await save_eval_run(
            suite="verifier_ragtruth",
            sample_count=results["ragtruth"]["sentences"],
            metrics=results["ragtruth"],
            gates=[
                gate("response-level F1", results["ragtruth"]["response_level"]["cascade"]["f1"], minimum=0.70),
                gate("AUROC", results["ragtruth"]["auroc"]["cascade"], minimum=0.80),
            ],
            config=config,
            started_at=started_at,
            notes="RAGTruth test split (QA and summary), human-annotated spans. Held-out half, split by source document.",
        )
    if "halueval" in results:
        await save_eval_run(
            suite="verifier_halueval",
            sample_count=results["halueval"]["responses"],
            metrics=results["halueval"],
            gates=[gate("response-level F1", results["halueval"]["response_level"]["cascade"]["f1"], minimum=0.70)],
            config=config,
            started_at=started_at,
            notes="HaluEval QA samples. Not used for fitting.",
        )
    print("overall (RAGTruth + injected, held out):", json.dumps(overall["sentence_level"]["cascade"]))
    await dispose_engine()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
