"""The multi-agent workflow as a LangGraph state machine.

    START ─► privacy ─┬─► retrieval ───────┐
                      └─► graph_reasoning ─┴─► verification ─► response_generation ─► fact_checking ─► governance ─► END
                                                    │                 ▲    │                │  │
              (denied) ─────────────────────────────┼─────────────────┼────┼────────────────┼──┤
                                    (insufficient) ─┘                 └────┼── (rewrite) ───┘  │
                                                             (abstain) ───┘                    ▼

Every path ends in the Governance & Audit Agent, so every request is scored and recorded,
including the ones that are denied or that end without an answer.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from pramana.agents.nodes import (
    fact_checking,
    governance,
    graph_reasoning,
    privacy,
    response_generation,
    retrieval,
    verification,
)
from pramana.agents.state import AGENT_LABELS, AgentDeps, PipelineState
from pramana.shared.db import utcnow
from pramana.shared.logging import get_logger

log = get_logger(__name__)
AgentFn = Callable[[PipelineState, AgentDeps], Awaitable[dict[str, Any]]]


def _instrument(name: str, fn: AgentFn, deps: AgentDeps) -> Callable[..., Awaitable[dict[str, Any]]]:
    """Wrap an agent so that it reports its own start, end, duration and summary."""

    async def node(state: PipelineState, config: RunnableConfig) -> dict[str, Any]:
        emit = config["configurable"]["emit"]
        label = AGENT_LABELS[name]
        started_at = utcnow()
        started = time.perf_counter()
        await emit({"type": "agent.started", "agent": name, "label": label})
        try:
            updates = await fn(state, deps)
        except Exception as exc:
            duration = int((time.perf_counter() - started) * 1000)
            await emit(
                {
                    "type": "agent.failed",
                    "agent": name,
                    "label": label,
                    "duration_ms": duration,
                    "started_at": started_at.isoformat(),
                    "error": type(exc).__name__,
                }
            )
            raise
        duration = int((time.perf_counter() - started) * 1000)
        step = {
            "agent": name,
            "label": label,
            "status": "completed",
            "started_at": started_at.isoformat(),
            "duration_ms": duration,
            "summary": updates.pop("_summary", ""),
            "details": updates.pop("_details", {}),
        }
        await emit({"type": "agent.finished", **step})
        updates["steps"] = [step]
        return updates

    return node


def _after_privacy(state: PipelineState) -> list[str] | str:
    return "governance" if state.get("outcome") == "denied" else ["retrieval", "graph_reasoning"]


def _after_verification(state: PipelineState) -> str:
    return "governance" if state.get("outcome") == "abstained" else "response_generation"


def _after_generation(state: PipelineState) -> str:
    return "governance" if state.get("outcome") == "abstained" else "fact_checking"


def _after_fact_checking(state: PipelineState) -> str:
    return "response_generation" if state.get("next_step") == "regenerate" else "governance"


def build_workflow(deps: AgentDeps) -> Any:
    graph = StateGraph(PipelineState)
    agents: dict[str, AgentFn] = {
        "privacy": privacy.run,
        "retrieval": retrieval.run,
        "graph_reasoning": graph_reasoning.run,
        "verification": verification.run,
        "response_generation": response_generation.run,
        "fact_checking": fact_checking.run,
        "governance": governance.run,
    }
    for name, fn in agents.items():
        graph.add_node(name, _instrument(name, fn, deps))

    graph.add_edge(START, "privacy")
    graph.add_conditional_edges("privacy", _after_privacy, ["retrieval", "graph_reasoning", "governance"])
    graph.add_edge(["retrieval", "graph_reasoning"], "verification")  # waits for both branches
    graph.add_conditional_edges("verification", _after_verification, ["response_generation", "governance"])
    graph.add_conditional_edges("response_generation", _after_generation, ["fact_checking", "governance"])
    graph.add_conditional_edges("fact_checking", _after_fact_checking, ["response_generation", "governance"])
    graph.add_edge("governance", END)
    return graph.compile()
