"""System-level evaluation suites: retrieval quality, access control, end-to-end answers.

Each suite runs the real pipeline against the real stores and returns measured numbers
together with pass/fail gates. Nothing is simulated.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import select

from pramana.auth.models import User
from pramana.auth.principal import Principal, principal_from_user
from pramana.evaluation.metrics import mrr, ndcg_at, percentile, recall_at
from pramana.evaluation.store import gate
from pramana.knowledge.models import Document
from pramana.retrieval.retriever import RetrievalOptions
from pramana.runtime import Runtime
from pramana.shared.config import REPO_ROOT
from pramana.shared.db import session_scope
from pramana.shared.types import Sensitivity

GOLDEN_PATH = REPO_ROOT / "eval" / "datasets" / "golden_aurelia.jsonl"
PERSONA_EMAILS = {
    "employee": "emma.employee@aurelia-systems.example",
    "manager_hr": "marcus.manager@aurelia-systems.example",
    "manager_finance": "fatima.finance@aurelia-systems.example",
    "analyst": "arjun.analyst@aurelia-systems.example",
    "compliance_officer": "clara.compliance@aurelia-systems.example",
    "executive": "elena.executive@aurelia-systems.example",
    "admin": "adam.admin@aurelia-systems.example",
}


@dataclass(slots=True)
class SuiteResult:
    suite: str
    sample_count: int
    metrics: dict[str, Any]
    gates: list[dict[str, Any]]
    notes: str = ""

    @property
    def passed(self) -> bool:
        return all(g["passed"] for g in self.gates)


def load_golden(path: Path | None = None) -> list[dict[str, Any]]:
    """The question set: the bundled one, or your own file named in GOLDEN_FILE (see docs/TEST_DATA.md)."""
    path = path or Path(os.environ.get("GOLDEN_FILE") or GOLDEN_PATH)
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


async def load_principals() -> dict[str, Principal]:
    async with session_scope() as db:
        users = {u.email: u for u in (await db.execute(select(User))).scalars()}
        return {name: principal_from_user(users[email]) for name, email in PERSONA_EMAILS.items() if email in users}


def satisfies(text: str, expect: list[list[str]]) -> bool:
    """Every group must be satisfied by at least one of its alternatives (case-insensitive)."""
    # Some models write narrow or non-breaking spaces ("10 working days"): those are spaces.
    lowered = " ".join(text.lower().split())
    return all(any(" ".join(option.lower().split()) in lowered for option in group) for group in expect)


# Keys whose values are identifiers, not text a person reads.
_NOT_TEXT = frozenset({"id", "run_id", "chunk_ids", "document_id", "claim_id", "version", "effective_date"})


def readable_text(result: Any) -> str:
    """Every piece of text in a response that a caller could read: the answer, claims, quotes,
    passages, titles, graph facts, notes. Numbers and identifiers are left out: a character offset
    of 4312 is not a leak of the value \"312\"."""
    parts: list[str] = []

    def walk(value: Any, key: str = "") -> None:
        if key in _NOT_TEXT:
            return
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, dict):
            for inner_key, inner in value.items():
                walk(inner, str(inner_key))
        elif isinstance(value, (list, tuple)):
            for inner in value:
                walk(inner, key)

    walk({k: result.get(k) for k in ("answer", "claims", "evidence", "graph", "explanation", "notes")})
    return "\n".join(parts)


def contains_value(text: str, value: str) -> bool:
    """``value`` occurs in ``text`` as a whole token: \"312\" does not match inside \"4312\" or \"3.312\"."""
    return re.search(rf"(?<![\w.]){re.escape(value)}(?![\w])", text, re.IGNORECASE) is not None


# ── Retrieval ───────────────────────────────────────────────────────────────
RETRIEVAL_CONFIGS: dict[str, RetrievalOptions] = {
    "semantic only": RetrievalOptions(use_keyword=False, use_graph=False, use_rerank=False, expand_context=False),
    "keyword only": RetrievalOptions(use_semantic=False, use_graph=False, use_rerank=False, expand_context=False),
    "semantic + keyword": RetrievalOptions(use_graph=False, use_rerank=False, expand_context=False),
    "semantic + keyword + graph": RetrievalOptions(use_rerank=False, expand_context=False),
    "full (with rerank)": RetrievalOptions(expand_context=False),
}


async def retrieval_suite(runtime: Runtime, *, limit: int | None = None) -> SuiteResult:
    """Does the passage that contains the answer come back, and how high? With an ablation table."""
    principal = (await load_principals())["executive"]
    scope = await runtime.policy.scope(principal)
    items = [g for g in load_golden() if g.get("answerable", True)][:limit]
    table: dict[str, dict[str, Any]] = {}
    for name, options in RETRIEVAL_CONFIGS.items():
        ranks: list[int | None] = []
        latencies: list[float] = []
        for item in items:
            started = time.perf_counter()
            result = await runtime.services.retriever.retrieve(
                item["question"], principal=principal, scope=scope, options=options
            )
            latencies.append((time.perf_counter() - started) * 1000)
            rank = next((i for i, e in enumerate(result.evidence, start=1) if satisfies(e.text, item["expect"])), None)
            ranks.append(rank)
        table[name] = {
            "recall@1": round(recall_at(ranks, 1), 4),
            "recall@3": round(recall_at(ranks, 3), 4),
            "recall@5": round(recall_at(ranks, 5), 4),
            "recall@8": round(recall_at(ranks, 8), 4),
            "mrr": round(mrr(ranks), 4),
            "ndcg@8": round(ndcg_at(ranks, 8), 4),
            "latency_p50_ms": round(percentile(latencies, 0.5) or 0),
            "missed": [item["id"] for item, rank in zip(items, ranks, strict=True) if rank is None],
        }
        print(f"  retrieval [{name}] recall@8 {table[name]['recall@8']:.3f} mrr {table[name]['mrr']:.3f}", flush=True)
    full = table["full (with rerank)"]
    return SuiteResult(
        suite="retrieval",
        sample_count=len(items),
        metrics={"configurations": table, "corpus_passages": await runtime.services.vectors.count()},
        gates=[
            gate("recall@8 (full pipeline)", full["recall@8"], minimum=0.90),
            gate("MRR (full pipeline)", full["mrr"], minimum=0.70),
        ],
        notes="Hand-written questions about the Aurelia documents, searched across the whole corpus. "
        "A hit is a returned passage that contains the expected answer.",
    )


# ── Access control ──────────────────────────────────────────────────────────
async def access_suite(runtime: Runtime, *, limit: int | None = None) -> SuiteResult:
    """Canary facts planted in protected documents must never reach a role that may not read them."""
    canary_path = runtime.settings.data_dir / "synthetic" / "canaries.json"
    canaries = json.loads(canary_path.read_text(encoding="utf-8"))[:limit]
    principals = await load_principals()
    leaks: list[dict[str, Any]] = []
    out_of_scope: list[dict[str, Any]] = []
    unauthorized = authorized = authorized_found = 0
    per_role: dict[str, dict[str, int]] = {}

    for role, principal in principals.items():
        scope = await runtime.policy.scope(principal)
        stats = per_role.setdefault(role, {"unauthorized": 0, "leaks": 0, "authorized": 0, "found": 0})
        for canary in canaries:
            may_read = scope.permits(
                department=canary["department"],
                sensitivity_rank=int(Sensitivity.parse(canary["sensitivity"])),
                region=canary["region"],
            )
            result = await runtime.services.retriever.retrieve(canary["question"], principal=principal, scope=scope)
            text = "\n".join(e.text for e in result.evidence).lower()
            exposed = [value for value in canary["secret_values"] if value.lower() in text]
            # Independent of the canary: every returned passage must itself be inside the scope.
            for evidence in result.evidence:
                if not scope.permits(
                    department=evidence.department, sensitivity_rank=evidence.sensitivity_rank, region=evidence.region
                ):
                    out_of_scope.append({"role": role, "canary": canary["id"], "document": evidence.title})
            if may_read:
                authorized += 1
                stats["authorized"] += 1
                if exposed:
                    authorized_found += 1
                    stats["found"] += 1
            else:
                unauthorized += 1
                stats["unauthorized"] += 1
                if exposed:
                    stats["leaks"] += 1
                    leaks.append({"role": role, "canary": canary["id"], "values": exposed})
        print(
            f"  access [{role}] unauthorized {stats['unauthorized']} leaks {stats['leaks']} "
            f"authorized {stats['authorized']} found {stats['found']}",
            flush=True,
        )

    false_permit = len(leaks) / unauthorized if unauthorized else 0.0
    recall = authorized_found / authorized if authorized else 0.0
    return SuiteResult(
        suite="access_control",
        sample_count=unauthorized + authorized,
        metrics={
            "canaries": len(canaries),
            "roles": len(principals),
            "unauthorized_cases": unauthorized,
            "leaks": leaks,
            "false_permit_rate": round(false_permit, 6),
            "out_of_scope_passages": out_of_scope,
            "authorized_cases": authorized,
            "authorized_recall": round(recall, 4),
            "per_role": per_role,
        },
        gates=[
            gate("false-permit rate (canary leaks)", false_permit, maximum=0.0),
            gate("passages returned outside the caller's scope", float(len(out_of_scope)), maximum=0.0),
            gate("authorized recall", recall, minimum=0.90),
        ],
        notes="Each canary question is asked as every role. A leak is a canary value in any returned passage "
        "for a role whose scope does not include the canary's document.",
    )


# ── End to end ──────────────────────────────────────────────────────────────
async def end_to_end_suite(runtime: Runtime, *, limit: int | None = None, leak_checks: int = 12) -> SuiteResult:
    """Full pipeline with the LLM: are answers right, verified, and withheld when they should be?"""
    principals = await load_principals()
    executive, employee = principals["executive"], principals["employee"]
    items = load_golden()
    answerable = [g for g in items if g.get("answerable", True)]
    unanswerable = [g for g in items if not g.get("answerable", True)]
    if limit:
        answerable, unanswerable = answerable[:limit], unanswerable[: max(2, limit // 5)]

    correct = answered = abstained_wrongly = 0
    latencies: list[float] = []
    trust: list[float] = []
    supported: list[float] = []
    llm_calls = removed_sentences = 0
    wrong: list[dict[str, Any]] = []
    routes: dict[str, int] = {}
    for item in answerable:
        result = await runtime.services.query.run(
            principal=executive, question=item["question"], persist_conversation=False, channel="eval"
        )
        latencies.append(result["latency_ms"])
        llm_calls += result["llm_calls"]
        routes[result["route"]] = routes.get(result["route"], 0) + 1
        removed_sentences += sum(1 for c in result["claims"] if not c["in_answer"])
        if result["outcome"] != "answered":
            abstained_wrongly += 1
            wrong.append({"id": item["id"], "outcome": result["outcome"], "reason": result["outcome_reason"]})
            continue
        answered += 1
        kept = [c for c in result["claims"] if c["in_answer"] and c["kind"] == "claim"]
        supported.append(sum(1 for c in kept if c["verdict"] == "SUPPORTED") / len(kept) if kept else 0.0)
        if result["trust"]:
            trust.append(result["trust"]["score"])
        if satisfies(result["answer"], item["expect"]):
            correct += 1
        else:
            wrong.append({"id": item["id"], "outcome": "answered", "answer": result["answer"][:200]})
        print(f"  e2e {item['id']} {result['outcome']} {result['latency_ms']}ms", flush=True)

    correct_abstentions = 0
    for item in unanswerable:
        result = await runtime.services.query.run(
            principal=executive, question=item["question"], persist_conversation=False, channel="eval"
        )
        llm_calls += result["llm_calls"]
        if result["outcome"] == "abstained":
            correct_abstentions += 1
        else:
            wrong.append({"id": item["id"], "outcome": "answered_unanswerable", "answer": result["answer"][:200]})

    # The same canary questions, asked by an employee, through the whole pipeline.
    canaries = json.loads((runtime.settings.data_dir / "synthetic" / "canaries.json").read_text(encoding="utf-8"))
    leaked: list[str] = []
    checked = 0
    for canary in canaries[:leak_checks]:
        result = await runtime.services.query.run(
            principal=employee, question=canary["question"], persist_conversation=False, channel="eval"
        )
        checked += 1
        llm_calls += result["llm_calls"]
        if any(contains_value(readable_text(result), value) for value in canary["secret_values"]):
            leaked.append(canary["id"])

    accuracy = correct / len(answerable) if answerable else 0.0
    abstention = correct_abstentions / len(unanswerable) if unanswerable else 0.0
    mean_supported = sum(supported) / len(supported) if supported else 0.0
    documents = await _document_count()
    return SuiteResult(
        suite="end_to_end",
        sample_count=len(answerable) + len(unanswerable) + checked,
        metrics={
            "answerable": len(answerable),
            "answered": answered,
            "correct": correct,
            "answer_accuracy": round(accuracy, 4),
            "wrongly_abstained": abstained_wrongly,
            "unanswerable": len(unanswerable),
            "correct_abstentions": correct_abstentions,
            "abstention_accuracy": round(abstention, 4),
            "supported_claim_share": round(mean_supported, 4),
            "sentences_removed_by_verifier": removed_sentences,
            "mean_trust": round(sum(trust) / len(trust), 1) if trust else None,
            "latency_p50_ms": round(percentile(latencies, 0.5) or 0),
            "latency_p95_ms": round(percentile(latencies, 0.95) or 0),
            "llm_calls": llm_calls,
            "routes": routes,
            "end_to_end_leak_checks": checked,
            "end_to_end_leaks": leaked,
            "failures": wrong,
            "corpus_documents": documents,
        },
        gates=[
            gate("answer accuracy", accuracy, minimum=0.85),
            gate("supported share of final-answer claims", mean_supported, minimum=0.95),
            gate("abstains on unanswerable questions", abstention, minimum=0.80),
            gate("canary leaks through the full pipeline", float(len(leaked)), maximum=0.0),
            gate("latency p50 (ms)", percentile(latencies, 0.5), maximum=10_000),
        ],
        notes="Golden questions asked as the executive persona (full access); unanswerable questions; "
        "and canary questions asked as an employee.",
    )


async def _document_count() -> int:
    from sqlalchemy import func

    async with session_scope() as db:
        return int(
            (
                await db.execute(select(func.count()).select_from(Document).where(Document.status == "ready"))
            ).scalar_one()
        )
