"""Graph Reasoning Agent: link the question to graph entities and rank passages by PageRank."""

from __future__ import annotations

from typing import Any

from pramana.agents.state import AgentDeps, PipelineState


async def run(state: PipelineState, deps: AgentDeps) -> dict[str, Any]:
    scope = state["access"].scope
    candidates = await deps.retriever.search_graph(state["retrieval_query"], scope)
    names = [e.name for e in candidates.linked_entities]
    return {
        "graph_candidates": candidates,
        "_summary": (
            f"Linked {len(names)} entit{'y' if len(names) == 1 else 'ies'}"
            + (f" ({', '.join(names[:4])})" if names else "")
            + f"; {len(candidates.chunks)} passages ranked through the graph; {len(candidates.facts)} facts"
        ),
        "_details": {
            "linked_entities": [
                {"name": e.name, "type": e.type, "matched_by": e.matched_by, "score": round(e.score, 3)}
                for e in candidates.linked_entities
            ],
            "graph_passages": len(candidates.chunks),
            "facts": len(candidates.facts),
            "timings_ms": candidates.timings_ms,
        },
    }
