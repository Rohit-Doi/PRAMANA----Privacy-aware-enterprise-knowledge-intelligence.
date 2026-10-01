"""Stores evaluation results so the Validation Reports page shows measured numbers."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pramana.governance.models import EvalRun
from pramana.shared.db import session_scope, utcnow


def gate(
    name: str, value: float | None, *, minimum: float | None = None, maximum: float | None = None
) -> dict[str, Any]:
    """One pass/fail criterion. A missing value fails: an unmeasured target is not a met target."""
    passed = value is not None
    if passed and minimum is not None:
        passed = value >= minimum  # type: ignore[operator]
    if passed and maximum is not None:
        passed = value <= maximum  # type: ignore[operator]
    return {
        "name": name,
        "value": None if value is None else round(value, 4),
        "minimum": minimum,
        "maximum": maximum,
        "passed": passed,
    }


async def save_eval_run(
    *,
    suite: str,
    sample_count: int,
    metrics: dict[str, Any],
    gates: list[dict[str, Any]],
    config: dict[str, Any] | None = None,
    notes: str = "",
    started_at: datetime | None = None,
) -> bool:
    passed = all(g["passed"] for g in gates) if gates else None
    async with session_scope() as db:
        db.add(
            EvalRun(
                suite=suite,
                status="completed",
                sample_count=sample_count,
                metrics=metrics,
                gates={"criteria": gates},
                gate_passed=passed,
                config=config or {},
                notes=notes,
                started_at=started_at or utcnow(),
                finished_at=utcnow(),
            )
        )
    return bool(passed)
