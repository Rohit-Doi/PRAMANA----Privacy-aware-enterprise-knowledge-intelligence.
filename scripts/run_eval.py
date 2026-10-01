"""Run the system-level evaluation suites and record the results.

    python scripts/run_eval.py                         # retrieval and access control (no LLM calls)
    python scripts/run_eval.py --suites end_to_end     # full pipeline; uses LLM quota
    python scripts/run_eval.py --suites retrieval access end_to_end --limit 20

Results are printed, written to data/eval/results/, and stored for the Validation Reports page.
The verifier benchmarks are run by scripts/train_calibrator.py.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime

from pramana.evaluation.store import save_eval_run
from pramana.evaluation.suites import access_suite, end_to_end_suite, retrieval_suite
from pramana.runtime import start_runtime, stop_runtime
from pramana.shared.db import utcnow

SUITES = {"retrieval": retrieval_suite, "access": access_suite, "end_to_end": end_to_end_suite}


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--suites", nargs="+", choices=sorted(SUITES), default=["retrieval", "access"])
    parser.add_argument("--limit", type=int, default=None, help="use only the first N items of each dataset")
    args = parser.parse_args()

    runtime = await start_runtime()
    results_dir = runtime.settings.data_dir / "eval" / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    all_passed = True
    try:
        for name in args.suites:
            started_at = utcnow()
            print(f"\n=== {name}", flush=True)
            result = await SUITES[name](runtime, limit=args.limit)
            await save_eval_run(
                suite=result.suite, sample_count=result.sample_count, metrics=result.metrics,
                gates=result.gates, notes=result.notes, started_at=started_at,
                config={"limit": args.limit, "policy_version": runtime.policy.loaded_version},
            )
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            (results_dir / f"{result.suite}-{stamp}.json").write_text(
                json.dumps({"suite": result.suite, "metrics": result.metrics, "gates": result.gates}, indent=2),
                encoding="utf-8",
            )
            for criterion in result.gates:
                limit = f">= {criterion['minimum']}" if criterion["minimum"] is not None else f"<= {criterion['maximum']}"
                print(f"  {'PASS' if criterion['passed'] else 'FAIL'}  {criterion['name']}: {criterion['value']} (target {limit})")
            all_passed = all_passed and result.passed
    finally:
        await stop_runtime()
    print("\nall gates passed" if all_passed else "\nsome gates failed")
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
