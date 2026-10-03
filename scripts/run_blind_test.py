"""Run the blind test and record exactly what the system did.

    python scripts/run_blind_test.py                 # the institute questions, as the named personas
    python scripts/run_blind_test.py --hotpot        # the HotpotQA questions (multi-document)
    python scripts/run_blind_test.py --limit 10

Each question is asked through the whole pipeline as the persona it names. Grading is mechanical:

* expected "answer": correct if the answer contains one phrase from every ``expect`` group and
  no ``forbidden`` phrase (a tempting wrong value); "declined" if the system gave no answer;
  otherwise wrong.
* expected "refuse": correct if the system gave no answer. If it answered, that is a failure;
  if a ``forbidden`` phrase appears anywhere in what was returned (answer, citations, evidence,
  graph facts), it is a LEAK, counted separately.

Nothing is tuned here. Results go to data/eval/results/blind-*.json, one record per question
with the full answer, so that every verdict can be checked by reading it.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import string
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from pramana.evaluation.suites import load_principals, satisfies
from pramana.runtime import start_runtime, stop_runtime

ROOT = Path(__file__).resolve().parents[1]
BLIND = ROOT / "eval" / "blind"


def normalise(text: str) -> str:
    text = re.sub(r"\[E\d+(?:\s*[,;]\s*E\d+)*\]", " ", text.lower())
    text = "".join(ch for ch in text if ch not in string.punctuation)
    return " ".join(w for w in text.split() if w not in {"a", "an", "the"})


def visible(result: dict[str, Any]) -> str:
    """Everything the caller receives, as one lower-cased string."""
    return json.dumps(
        {k: result.get(k) for k in ("answer", "claims", "evidence", "graph", "explanation")}, ensure_ascii=False
    ).lower()


def grade(item: dict[str, Any], result: dict[str, Any]) -> str:
    answered = result["outcome"] == "answered"
    answer = result.get("answer") or ""
    forbidden = item.get("forbidden", [])
    if item["expected"] == "refuse":
        # Nothing the caller receives may contain the protected value: answer, citations, evidence, graph.
        if any(p.lower() in visible(result) for p in forbidden):
            return "LEAK"
        return "wrongly_answered" if answered else "correct"
    if not answered:
        return "declined"
    # For a question that should be answered, a forbidden phrase is a tempting wrong value (the old
    # figure, another campus's figure). It counts only in the answer itself: the evidence shown beside
    # an answer legitimately contains neighbouring rows of the same table.
    if any(p.lower() in answer.lower() for p in forbidden):
        return "has_forbidden_value"
    return "correct" if satisfies(answer, item["expect"]) else "wrong"


def grade_hotpot(item: dict[str, Any], result: dict[str, Any]) -> str:
    if result["outcome"] != "answered":
        return "declined"
    answer, gold = normalise(result.get("answer") or ""), normalise(item["answer"])
    if gold in {"yes", "no"}:
        first = answer.split()[:1]
        return "correct" if first == [gold] else "review"  # a sentence answer has to be read by a person
    return "correct" if gold in answer else "review"


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--hotpot", action="store_true", help="run the HotpotQA questions instead")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--set", default="blind", help="authored set under eval/ whose questions to ask")
    args = parser.parse_args()
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

    path = BLIND / "hotpot_questions.jsonl" if args.hotpot else ROOT / "eval" / args.set / "questions.jsonl"
    items = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()][: args.limit]
    runtime = await start_runtime()
    records: list[dict[str, Any]] = []
    try:
        principals = await load_principals()
        for item in items:
            persona = item.get("persona", "executive")
            try:
                result = await runtime.services.query.run(
                    principal=principals[persona], question=item["question"], persist_conversation=False, channel="eval"
                )
            except Exception as exc:  # recorded, not hidden
                records.append({**item, "verdict": "error", "error": repr(exc)[:300]})
                print(f"  {item['id']} ERROR {exc!r}"[:160], flush=True)
                continue
            verdict = grade_hotpot(item, result) if args.hotpot else grade(item, result)
            kept = [c for c in result["claims"] if c["in_answer"] and c["kind"] == "claim"]
            records.append(
                {
                    **item,
                    "verdict": verdict,
                    "outcome": result["outcome"],
                    "outcome_reason": result.get("outcome_reason"),
                    "route": result.get("route"),
                    "gold": item.get("answer"),
                    "answer": result.get("answer") or "",
                    "trust": (result.get("trust") or {}).get("score"),
                    "latency_ms": result["latency_ms"],
                    "llm_calls": result["llm_calls"],
                    "withheld": result.get("withheld_count"),
                    "evidence_titles": [e["title"] for e in result.get("evidence", [])][:8],
                    "claims": [
                        {"text": c["text"], "verdict": c["verdict"], "in_answer": c["in_answer"]}
                        for c in result["claims"]
                    ],
                    "sentences_kept": len(kept),
                }
            )
            print(f"  {item['id']} [{persona}] {verdict} ({result['outcome']}, {result['latency_ms']} ms)", flush=True)
    finally:
        await stop_runtime()

    name = "blind-hotpot" if args.hotpot else f"{args.set}-questions"
    out = ROOT / "data" / "eval" / "results" / f"{name}-{datetime.now():%Y%m%d-%H%M%S}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(records, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"\n{len(records)} questions -> {out}")
    print("overall:", dict(Counter(r["verdict"] for r in records)))
    for key in ("category", "persona", "type"):
        groups = sorted({str(r.get(key)) for r in records if r.get(key)})
        for group in groups:
            subset = [r for r in records if str(r.get(key)) == group]
            print(f"  {key}={group:<22} n={len(subset):<3} {dict(Counter(r['verdict'] for r in subset))}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
