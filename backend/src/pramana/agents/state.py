"""Shared state of the agent workflow, the dependencies agents use, and event emission."""

from __future__ import annotations

import operator
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated, Any, TypedDict

import redis.asyncio as aioredis

from pramana.auth.principal import Principal
from pramana.llm.gateway import LLMGateway
from pramana.privacy.pii import PiiEngine, Pseudonymizer
from pramana.privacy.policy import AccessContext, PolicyEngine
from pramana.retrieval.local_models import RetrievalModels
from pramana.retrieval.reader import ExtractiveReader
from pramana.retrieval.retriever import (
    Evidence,
    GraphCandidates,
    HybridRetriever,
    RetrievalResult,
    VectorCandidates,
)
from pramana.shared.config import Settings
from pramana.validation.engine import ClaimResult, ValidationEngine, VerificationReport

Emit = Callable[[dict[str, Any]], Awaitable[None]]

AGENTS = (
    ("privacy", "Privacy Agent"),
    ("retrieval", "Retrieval Agent"),
    ("graph_reasoning", "Graph Reasoning Agent"),
    ("verification", "Verification Agent"),
    ("response_generation", "Response Generation Agent"),
    ("fact_checking", "Fact-Checking Agent"),
    ("governance", "Governance & Audit Agent"),
)
AGENT_LABELS = dict(AGENTS)


@dataclass(frozen=True, slots=True)
class AgentDeps:
    settings: Settings
    policy: PolicyEngine
    retriever: HybridRetriever
    models: RetrievalModels
    reader: ExtractiveReader
    pii: PiiEngine
    llm: LLMGateway
    validation: ValidationEngine
    redis: aioredis.Redis


class PipelineState(TypedDict, total=False):
    # ── input ───────────────────────────────────────────────────────────
    run_id: uuid.UUID
    question: str
    previous_question: str | None
    mode: str  # "strict" | "live"
    principal: Principal

    # ── Privacy Agent ───────────────────────────────────────────────────
    access: AccessContext
    retrieval_query: str
    query_redacted: str
    query_pii: dict[str, int]
    injection: list[str]
    recent_requests: int
    pseudonymizer: Pseudonymizer

    # ── Retrieval and Graph Reasoning Agents (run in parallel) ─────────
    vector_candidates: VectorCandidates
    graph_candidates: GraphCandidates

    # ── Verification Agent ──────────────────────────────────────────────
    retrieval: RetrievalResult
    evidence: list[Evidence]  # text as this reader may see it
    llm_evidence: list[Evidence]  # text as the generator sees it
    # Only when some evidence may not leave the machine: the passages that may, prepared for the
    # external model. Used if no sentence can be quoted from the full evidence.
    open_llm_evidence: list[Evidence]
    route: str  # "external" | "local" | "extractive"
    evidence_notes: list[str]

    # ── Response Generation and Fact-Checking Agents ───────────────────
    draft: str
    round: int
    report: VerificationReport
    next_step: str  # "regenerate" | "finalize"
    final_answer: str  # in generator space (placeholders not yet restored)
    kept_claims: list[ClaimResult]
    removed_claims: list[ClaimResult]

    # ── Outcome and Governance Agent ───────────────────────────────────
    outcome: str  # "answered" | "abstained" | "denied"
    outcome_reason: str
    display_answer: str
    display_claims: list[dict[str, Any]]
    trust: dict[str, Any]
    risk: dict[str, Any]
    explanation: dict[str, Any]
    output_redactions: int
    audit_id: str

    # ── accumulated across agents ──────────────────────────────────────
    steps: Annotated[list[dict[str, Any]], operator.add]
    llm_calls: Annotated[int, operator.add]
