"""Fact-Checking Agent: verifies the draft claim by claim and decides what happens next.

every claim supported          → finalize
a minority of claims failed    → remove them (no LLM call needed) and finalize
most claims failed             → send the draft back for one rewrite, then check again
nothing verifiable is left     → abstain
"""

from __future__ import annotations

from typing import Any

from pramana.agents.state import AgentDeps, PipelineState
from pramana.shared.types import Verdict
from pramana.validation.engine import ClaimResult

REGENERATE_WHEN_FAILED_SHARE = 1 / 3


def _keeps(claim: ClaimResult) -> bool:
    # A claim that one current source supports and another contradicts is kept, visibly flagged.
    return claim.kind == "disclaimer" or claim.verdict in (Verdict.SUPPORTED, Verdict.CONFLICTING)


async def run(state: PipelineState, deps: AgentDeps) -> dict[str, Any]:
    round_number = state.get("round", 0)
    route = state["route"]
    report = await deps.validation.verify(
        question=state["query_redacted"],
        answer=state["draft"],
        evidence=state["llm_evidence"],
        use_judge=route != "extractive",
        run_id=state["run_id"],
        judge_local_only=route == "local",
    )
    updates: dict[str, Any] = {"report": report, "llm_calls": 1 if report.judge_used else 0}

    checkable = report.checkable
    failed = [c for c in checkable if not _keeps(c)]
    kept = [c for c in report.claims if _keeps(c)]
    kept_checkable = [c for c in kept if c.kind == "claim"]
    failed_share = len(failed) / len(checkable) if checkable else 1.0

    can_regenerate = (
        route != "extractive" and round_number < deps.settings.max_correction_rounds - 1 and deps.llm.configured
    )
    if failed and failed_share > REGENERATE_WHEN_FAILED_SHARE and can_regenerate:
        decision = "regenerate"
        updates.update({"next_step": "regenerate", "round": round_number + 1})
    elif not kept_checkable:
        decision = "abstain"
        updates.update(
            {
                "next_step": "finalize",
                "outcome": "abstained",
                "outcome_reason": "unverified",
                "kept_claims": [],
                "removed_claims": failed,
                "final_answer": "",
            }
        )
    else:
        decision = "finalize" if not failed else "remove unverified sentences"
        updates.update(
            {
                "next_step": "finalize",
                "outcome": "answered",
                "kept_claims": kept,
                "removed_claims": failed,
                "final_answer": " ".join(c.raw.strip() for c in kept),
            }
        )

    counts = report.counts()
    updates["_summary"] = (
        f"{len(checkable)} claim(s) checked: "
        + ", ".join(f"{n} {verdict.lower()}" for verdict, n in sorted(counts.items()))
        + f"; confidence {report.confidence:.0%}; decision: {decision}"
    )
    updates["_details"] = {
        "round": round_number,
        "verdicts": counts,
        "confidence": round(report.confidence, 4),
        "supported_ratio": round(report.supported_ratio, 4),
        "judge_used": report.judge_used,
        "decision": decision,
        "calibrator": report.calibrator_version,
        "timings_ms": report.timings_ms,
        "claims": [
            {"text": c.text, "verdict": str(c.verdict), "confidence": c.confidence, "reason": c.reason}
            for c in checkable
        ],
    }
    return updates
