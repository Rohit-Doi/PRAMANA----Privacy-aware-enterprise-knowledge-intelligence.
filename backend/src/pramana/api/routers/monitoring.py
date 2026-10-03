"""Agent monitoring: recent runs, per-agent timings, LLM usage, system status."""

from __future__ import annotations

from datetime import timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import case, func, select

from pramana.agents.models import AgentStep, LLMCall, QueryRun
from pramana.agents.state import AGENTS
from pramana.api.deps import DbSession, RuntimeDep, require
from pramana.auth.models import User
from pramana.auth.principal import Principal
from pramana.knowledge.models import Document, IngestionJob
from pramana.shared.db import utcnow
from pramana.shared.types import Permission
from pramana.validation.calibration import get_calibrator

router = APIRouter(prefix="/monitoring", tags=["Agent monitoring"])
Operator = Annotated[Principal, Depends(require(Permission.MONITORING_READ))]


@router.get("/runs", summary="Recent runs across all users (metadata only)")
async def runs(
    _: Operator,
    db: DbSession,
    outcome: str | None = None,
    kind: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    conditions = []
    if outcome:
        conditions.append(QueryRun.outcome == outcome)
    if kind:
        conditions.append(QueryRun.kind == kind)
    total = (await db.execute(select(func.count()).select_from(QueryRun).where(*conditions))).scalar_one()
    rows = await db.execute(
        select(QueryRun, User.email, User.full_name)
        .join(User, User.id == QueryRun.user_id)
        .where(*conditions)
        .order_by(QueryRun.started_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return {
        "total": total,
        "items": [
            {
                "run_id": str(run.id),
                "kind": run.kind,
                "user_email": email,
                "user_name": name,
                "status": run.status,
                "outcome": run.outcome,
                "trust_score": run.trust_score,
                "confidence": run.confidence,
                "risk_score": run.risk_score,
                "latency_ms": run.latency_ms,
                "llm_calls": run.llm_calls,
                "correction_rounds": run.correction_rounds,
                "withheld_count": run.withheld_count,
                "needs_review": run.needs_review,
                "route": (run.explanation or {}).get("route"),
                "error": run.error,
                "started_at": run.started_at.isoformat(),
            }
            for run, email, name in rows.all()
        ],
    }


@router.get("/agents", summary="Per-agent run counts, failure rate and latency over a time window")
async def agents(_: Operator, db: DbSession, hours: Annotated[int, Query(ge=1, le=720)] = 24) -> dict[str, Any]:
    since = utcnow() - timedelta(hours=hours)
    rows = await db.execute(
        select(
            AgentStep.agent,
            func.count(),
            func.sum(case((AgentStep.status != "completed", 1), else_=0)),
            func.avg(AgentStep.duration_ms),
            func.percentile_cont(0.5).within_group(AgentStep.duration_ms),
            func.percentile_cont(0.95).within_group(AgentStep.duration_ms),
            func.max(AgentStep.duration_ms),
        )
        .where(AgentStep.started_at >= since)
        .group_by(AgentStep.agent)
    )
    stats = {
        agent: {
            "runs": n,
            "failures": int(failures or 0),
            "avg_ms": round(float(avg or 0)),
            "p50_ms": round(float(p50 or 0)),
            "p95_ms": round(float(p95 or 0)),
            "max_ms": int(slowest or 0),
        }
        for agent, n, failures, avg, p50, p95, slowest in rows.all()
    }
    empty = {"runs": 0, "failures": 0, "avg_ms": 0, "p50_ms": 0, "p95_ms": 0, "max_ms": 0}
    corrections = (
        await db.execute(
            select(func.count(), func.sum(case((QueryRun.correction_rounds > 0, 1), else_=0))).where(
                QueryRun.started_at >= since, QueryRun.kind == "query"
            )
        )
    ).one()
    return {
        "window_hours": hours,
        "agents": [{"agent": key, "label": label, **stats.get(key, empty)} for key, label in AGENTS],
        "runs": corrections[0],
        "runs_with_correction": int(corrections[1] or 0),
    }


@router.get("/llm", summary="LLM providers: today's usage, cool-downs and recent calls")
async def llm(_: Operator, db: DbSession, runtime: RuntimeDep) -> dict[str, Any]:
    since = utcnow() - timedelta(hours=24)
    by_purpose = await db.execute(
        select(
            LLMCall.purpose,
            LLMCall.status,
            func.count(),
            func.sum(LLMCall.tokens_in + LLMCall.tokens_out),
            func.avg(LLMCall.latency_ms),
            func.sum(case((LLMCall.cached, 1), else_=0)),
        )
        .where(LLMCall.created_at >= since)
        .group_by(LLMCall.purpose, LLMCall.status)
    )
    recent = await db.execute(select(LLMCall).order_by(LLMCall.created_at.desc()).limit(25))
    return {
        "providers": await runtime.services.llm.usage_today(),
        "configured": runtime.services.llm.configured,
        "max_calls_per_question": runtime.settings.llm_max_calls_per_query,
        "last_24h": [
            {
                "purpose": purpose,
                "status": status,
                "calls": n,
                "tokens": int(tokens or 0),
                "avg_latency_ms": round(float(latency or 0)),
                "cached": int(cached or 0),
            }
            for purpose, status, n, tokens, latency, cached in by_purpose.all()
        ],
        "recent": [
            {
                "purpose": c.purpose,
                "provider": c.provider,
                "model": c.model,
                "status": c.status,
                "cached": c.cached,
                "tokens_in": c.tokens_in,
                "tokens_out": c.tokens_out,
                "latency_ms": c.latency_ms,
                "prompt_version": c.prompt_version,
                "error": c.error,
                "created_at": c.created_at.isoformat(),
            }
            for c in recent.scalars()
        ],
    }


@router.get("/system", summary="What the platform is running: models, stores, corpus size")
async def system(_: Operator, db: DbSession, runtime: RuntimeDep) -> dict[str, Any]:
    settings = runtime.settings
    documents = dict((await db.execute(select(Document.status, func.count()).group_by(Document.status))).all())
    jobs = dict((await db.execute(select(IngestionJob.status, func.count()).group_by(IngestionJob.status))).all())
    calibrator = get_calibrator()
    return {
        "environment": settings.app_env,
        "policy_version": runtime.policy.loaded_version,
        "models": {
            "embedding": settings.embedding_model,
            "keyword": settings.sparse_model,
            "reranker": settings.reranker_model,
            "extraction": settings.extraction_model,
            "grounding": settings.grounding_model,
            "nli": settings.nli_model,
            "spans": settings.span_model,
            "reader": settings.reader_model,
            "calibrator": calibrator.version,
            "calibrator_metrics": calibrator.metrics,
        },
        "llm_chain": [p.name for p in runtime.services.llm.chain],
        "thresholds": {
            "support": calibrator.threshold or settings.support_threshold,
            "uncertain_low": settings.uncertain_low,
            "min_evidence_score": settings.min_evidence_score,
            "max_correction_rounds": settings.max_correction_rounds,
        },
        "documents": documents,
        "ingestion_jobs": jobs,
        "vectors": await runtime.services.vectors.count(),
        "graph": await runtime.services.graph.counts(),
    }
