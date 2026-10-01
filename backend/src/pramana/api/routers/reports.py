"""Validation reports, the human review queue, evaluation runs and usage analytics."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import case, cast, func, select
from sqlalchemy.types import Date

from pramana.agents.models import QueryRun
from pramana.agents.run_view import run_payload
from pramana.api.deps import DbSession, require
from pramana.auth.models import Department, User
from pramana.auth.principal import Principal
from pramana.governance.audit import AuditTrail
from pramana.governance.models import EvalRun
from pramana.shared.db import utcnow
from pramana.shared.errors import NotFound
from pramana.shared.types import Permission
from pramana.validation.models import Citation, Claim, Feedback, ReviewItem

router = APIRouter(tags=["Reports and analytics"])
ReportReader = Annotated[Principal, Depends(require(Permission.REPORTS_READ))]
Reviewer = Annotated[Principal, Depends(require(Permission.REVIEW_WRITE))]
Analyst = Annotated[Principal, Depends(require(Permission.ANALYTICS_READ))]
Days = Annotated[int, Query(ge=1, le=365)]


class ReviewResolution(BaseModel):
    resolution: Literal["correct", "incorrect", "partially_correct", "not_reviewable"]
    note: str = Field(default="", max_length=2000)


@router.get("/reports/validation", summary="How answers are verifying: outcomes, verdicts, trust over time")
async def validation_report(_: ReportReader, db: DbSession, days: Days = 30) -> dict[str, Any]:
    since = utcnow() - timedelta(days=days)
    queries = [QueryRun.started_at >= since, QueryRun.kind == "query", QueryRun.status == "completed"]

    outcomes = dict(
        (await db.execute(select(QueryRun.outcome, func.count()).where(*queries).group_by(QueryRun.outcome))).all()
    )
    verdicts = dict(
        (
            await db.execute(
                select(Claim.verdict, func.count())
                .join(QueryRun, QueryRun.id == Claim.run_id)
                .where(QueryRun.started_at >= since)
                .group_by(Claim.verdict)
            )
        ).all()
    )
    removed = (
        await db.execute(
            select(func.count())
            .select_from(Claim)
            .join(QueryRun, QueryRun.id == Claim.run_id)
            .where(QueryRun.started_at >= since, Claim.in_final_answer.is_(False))
        )
    ).scalar_one()
    totals = (
        await db.execute(
            select(
                func.avg(QueryRun.trust_score),
                func.avg(QueryRun.confidence),
                func.sum(case((QueryRun.correction_rounds > 0, 1), else_=0)),
                func.sum(case((QueryRun.needs_review, 1), else_=0)),
                func.count(),
            ).where(*queries)
        )
    ).one()
    day = cast(QueryRun.started_at, Date)
    daily = await db.execute(
        select(
            day,
            func.count(),
            func.avg(QueryRun.trust_score),
            func.avg(QueryRun.confidence),
            func.sum(case((QueryRun.outcome == "answered", 1), else_=0)),
            func.sum(case((QueryRun.outcome == "abstained", 1), else_=0)),
            func.sum(case((QueryRun.outcome == "denied", 1), else_=0)),
        )
        .where(*queries)
        .group_by(day)
        .order_by(day)
    )
    claims_total = sum(verdicts.values())
    return {
        "window_days": days,
        "runs": totals[4],
        "outcomes": outcomes,
        "verdicts": verdicts,
        "claims_checked": claims_total,
        "sentences_removed": removed,
        # Share of drafted claims that failed verification and were caught before reaching the user.
        "caught_rate": round(removed / claims_total, 4) if claims_total else None,
        "avg_trust": round(float(totals[0]), 1) if totals[0] is not None else None,
        "avg_confidence": round(float(totals[1]), 3) if totals[1] is not None else None,
        "runs_with_correction": int(totals[2] or 0),
        "runs_needing_review": int(totals[3] or 0),
        "daily": [
            {
                "date": d.isoformat(),
                "runs": n,
                "avg_trust": round(float(t), 1) if t is not None else None,
                "avg_confidence": round(float(c), 3) if c is not None else None,
                "answered": int(a or 0),
                "abstained": int(b or 0),
                "denied": int(x or 0),
            }
            for d, n, t, c, a, b, x in daily.all()
        ],
    }


@router.get("/reports/review", summary="Answers waiting for a human decision")
async def review_queue(
    _: ReportReader,
    db: DbSession,
    status: Literal["open", "resolved", "all"] = "open",
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[dict[str, Any]]:
    query = (
        select(ReviewItem, QueryRun, User.email)
        .join(QueryRun, QueryRun.id == ReviewItem.run_id)
        .join(User, User.id == QueryRun.user_id)
        .order_by(ReviewItem.created_at.desc())
        .limit(limit)
    )
    if status != "all":
        query = query.where(ReviewItem.status == status)
    rows = (await db.execute(query)).all()
    comments = {}
    if rows:
        feedback = await db.execute(
            select(Feedback.run_id, Feedback.comment, Feedback.rating)
            .where(Feedback.run_id.in_([run.id for _, run, _ in rows]))
            .order_by(Feedback.created_at.desc())
        )
        for run_id, comment, rating in feedback.all():
            comments.setdefault(run_id, {"comment": comment, "rating": rating})
    return [
        {
            "id": str(item.id),
            "run_id": str(run.id),
            "reason": item.reason,
            "status": item.status,
            "resolution": item.resolution,
            "note": item.note,
            "created_at": item.created_at.isoformat(),
            "resolved_at": item.resolved_at.isoformat() if item.resolved_at else None,
            "asked_by": email,
            "trust_score": run.trust_score,
            "confidence": run.confidence,
            "outcome": run.outcome,
            "kind": run.kind,
            "feedback": comments.get(run.id),
        }
        for item, run, email in rows
    ]


@router.get("/reports/review/{item_id}", summary="A review item with the full verification record of its run")
async def review_item(item_id: uuid.UUID, _: Reviewer, db: DbSession) -> dict[str, Any]:
    item = await db.get(ReviewItem, item_id)
    if item is None:
        raise NotFound("Review item not found")
    run = await db.get(QueryRun, item.run_id)
    assert run is not None
    # Reviewers decide on the verification record: verdicts, signals and cited quotes.
    return {
        "id": str(item.id),
        "reason": item.reason,
        "status": item.status,
        "note": item.note,
        "resolution": item.resolution,
        "run": await run_payload(db, run),
    }


@router.post("/reports/review/{item_id}/resolve", status_code=204, summary="Record the reviewer's decision")
async def resolve_review(item_id: uuid.UUID, body: ReviewResolution, principal: Reviewer, db: DbSession) -> None:
    item = await db.get(ReviewItem, item_id)
    if item is None:
        raise NotFound("Review item not found")
    item.status, item.resolution, item.note = "resolved", body.resolution, body.note
    item.reviewer_id, item.resolved_at = principal.user_id, utcnow()
    run = await db.get(QueryRun, item.run_id)
    if run is not None:
        run.needs_review = False
    await AuditTrail().record(
        action="review.resolve",
        decision=body.resolution.upper(),
        principal=principal,
        resource_type="run",
        resource_id=str(item.run_id),
        details={"review_item": str(item_id), "note": body.note[:300]},
    )


@router.get("/reports/evals", summary="Evaluation runs: benchmark results and gates")
async def evals(
    _: ReportReader, db: DbSession, suite: str | None = None, limit: Annotated[int, Query(ge=1, le=200)] = 50
) -> list[dict[str, Any]]:
    query = select(EvalRun).order_by(EvalRun.started_at.desc()).limit(limit)
    if suite:
        query = query.where(EvalRun.suite == suite)
    return [_eval(r) for r in (await db.execute(query)).scalars()]


@router.get("/reports/evals/latest", summary="The most recent result of every evaluation suite")
async def latest_evals(_: ReportReader, db: DbSession) -> list[dict[str, Any]]:
    latest = (
        select(EvalRun.suite, func.max(EvalRun.started_at).label("started_at"))
        .where(EvalRun.status == "completed")
        .group_by(EvalRun.suite)
        .subquery()
    )
    rows = await db.execute(
        select(EvalRun)
        .join(latest, (EvalRun.suite == latest.c.suite) & (EvalRun.started_at == latest.c.started_at))
        .order_by(EvalRun.suite)
    )
    return [_eval(r) for r in rows.scalars()]


def _eval(run: EvalRun) -> dict[str, Any]:
    return {
        "id": str(run.id),
        "suite": run.suite,
        "status": run.status,
        "sample_count": run.sample_count,
        "metrics": run.metrics,
        "gates": run.gates,
        "gate_passed": run.gate_passed,
        "config": run.config,
        "notes": run.notes,
        "started_at": run.started_at.isoformat(),
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
    }


@router.get("/analytics/overview", summary="Usage, quality and latency over a time window")
async def analytics(_: Analyst, db: DbSession, days: Days = 30) -> dict[str, Any]:
    since = utcnow() - timedelta(days=days)
    base = [QueryRun.started_at >= since, QueryRun.status == "completed"]
    queries = [*base, QueryRun.kind == "query"]

    totals = (
        await db.execute(
            select(
                func.count(),
                func.count(func.distinct(QueryRun.user_id)),
                func.percentile_cont(0.5).within_group(QueryRun.latency_ms),
                func.percentile_cont(0.95).within_group(QueryRun.latency_ms),
                func.sum(QueryRun.withheld_count),
                func.sum(QueryRun.llm_calls),
                func.avg(QueryRun.trust_score),
            ).where(*queries)
        )
    ).one()
    by_kind = dict((await db.execute(select(QueryRun.kind, func.count()).where(*base).group_by(QueryRun.kind))).all())
    by_outcome = dict(
        (await db.execute(select(QueryRun.outcome, func.count()).where(*queries).group_by(QueryRun.outcome))).all()
    )
    by_department = await db.execute(
        select(Department.name, func.count())
        .select_from(QueryRun)
        .join(User, User.id == QueryRun.user_id)
        .join(Department, Department.id == User.department_id)
        .where(*queries)
        .group_by(Department.name)
        .order_by(func.count().desc())
    )
    by_route = await db.execute(
        select(QueryRun.explanation["route"].astext, func.count())
        .where(*queries, QueryRun.outcome == "answered")
        .group_by(QueryRun.explanation["route"].astext)
    )
    bucket = case((QueryRun.trust_score >= 80, "high"), (QueryRun.trust_score >= 60, "medium"), else_="low")
    trust_buckets = dict(
        (
            await db.execute(
                select(bucket, func.count()).where(*queries, QueryRun.trust_score.is_not(None)).group_by(bucket)
            )
        ).all()
    )
    top_sources = await db.execute(
        select(Citation.document_title, func.count(func.distinct(Claim.run_id)))
        .join(Claim, Claim.id == Citation.claim_id)
        .join(QueryRun, QueryRun.id == Claim.run_id)
        .where(QueryRun.started_at >= since, Claim.in_final_answer, Citation.document_title != "")
        .group_by(Citation.document_title)
        .order_by(func.count(func.distinct(Claim.run_id)).desc())
        .limit(10)
    )
    day = cast(QueryRun.started_at, Date)
    daily = await db.execute(
        select(day, func.count(), func.percentile_cont(0.5).within_group(QueryRun.latency_ms))
        .where(*queries)
        .group_by(day)
        .order_by(day)
    )
    feedback = dict(
        (
            await db.execute(
                select(Feedback.rating, func.count()).where(Feedback.created_at >= since).group_by(Feedback.rating)
            )
        ).all()
    )
    return {
        "window_days": days,
        "questions": totals[0],
        "active_users": totals[1],
        "latency_p50_ms": round(float(totals[2])) if totals[2] is not None else None,
        "latency_p95_ms": round(float(totals[3])) if totals[3] is not None else None,
        "sources_withheld": int(totals[4] or 0),
        "llm_calls": int(totals[5] or 0),
        "avg_trust": round(float(totals[6]), 1) if totals[6] is not None else None,
        "by_kind": by_kind,
        "by_outcome": by_outcome,
        "by_department": [{"department": name, "questions": n} for name, n in by_department.all()],
        "by_route": {route or "none": n for route, n in by_route.all()},
        "trust_buckets": trust_buckets,
        "top_sources": [{"title": title, "answers": n} for title, n in top_sources.all()],
        "daily": [
            {"date": d.isoformat(), "questions": n, "latency_p50_ms": round(float(p)) if p is not None else None}
            for d, n, p in daily.all()
        ],
        "feedback": {"positive": feedback.get(1, 0), "negative": feedback.get(-1, 0)},
        "generated_at": datetime.now().astimezone().isoformat(),
    }
