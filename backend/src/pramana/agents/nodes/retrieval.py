"""Retrieval Agent: semantic and keyword passage search inside the caller's access scope."""

from __future__ import annotations

from typing import Any

from pramana.agents.state import AgentDeps, PipelineState


async def run(state: PipelineState, deps: AgentDeps) -> dict[str, Any]:
    scope = state["access"].scope
    candidates = await deps.retriever.search_passages(state["retrieval_query"], scope)
    return {
        "vector_candidates": candidates,
        "_summary": (
            f"{len(candidates.semantic)} semantic and {len(candidates.keyword)} keyword candidates; "
            f"{candidates.withheld_count} relevant passage(s) outside your access"
        ),
        "_details": {
            "semantic_candidates": len(candidates.semantic),
            "keyword_candidates": len(candidates.keyword),
            "withheld": candidates.withheld_count,
            "timings_ms": candidates.timings_ms,
        },
    }
