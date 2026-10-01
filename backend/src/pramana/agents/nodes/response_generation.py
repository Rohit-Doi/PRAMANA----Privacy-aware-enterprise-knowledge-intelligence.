"""Response Generation Agent: writes a cited answer from the evidence, and nothing else."""

from __future__ import annotations

import re
from typing import Any

from pramana.agents.extractive import extractive_answer
from pramana.agents.state import AgentDeps, PipelineState
from pramana.llm.prompts import ANSWER, CORRECTION
from pramana.retrieval.retriever import Evidence
from pramana.shared.errors import DependencyUnavailable
from pramana.shared.types import Sensitivity
from pramana.validation.claims import is_insufficient

MAX_GRAPH_FACTS = 6
ANSWER_MAX_TOKENS = 700


def evidence_block(evidence: list[Evidence]) -> str:
    blocks = []
    for item in evidence:
        status = "CURRENT" if item.is_current else "SUPERSEDED"
        details = [item.title]
        if item.section_path and item.section_path != item.title:
            details.append(item.section_path)
        if item.effective_date:
            details.append(f"effective {item.effective_date}")
        blocks.append(f"[{item.ref}] ({'; '.join(details)}; {status})\n{item.text}")
    return "\n\n".join(blocks)


FIRST_PERSON = re.compile(r"\b(?:I|[Mm]y|[Mm]e|[Mm]ine|[Ww]e|[Oo]ur|[Uu]s)\b")


def build_user_prompt(state: PipelineState, question: str) -> str:
    parts = [f"Question: {question}"]
    if FIRST_PERSON.search(question):
        # "How much leave do I get?" depends on who is asking. Department and region come from the
        # verified session, never from the question.
        principal = state["principal"]
        parts.append(
            f"About the person asking: works in the {principal.department} department, "
            f"{principal.region.upper()} region."
        )
    parts += ["Evidence:", evidence_block(state["llm_evidence"])]
    # Graph facts orient the model but are not citable. Only facts drawn from the evidence
    # documents are used, and none from CONFIDENTIAL or higher go to an external model:
    # entity names are not pseudonymised the way passage text is.
    rank_of = {e.document_id: e.sensitivity_rank for e in state["llm_evidence"]}
    external = state["route"] == "external"
    lines = [
        f"- {f.head} | {f.relation.replace('_', ' ')} | {f.tail}"
        for f in state["retrieval"].graph_facts
        if f.document_id in rank_of and not (external and rank_of[f.document_id] >= Sensitivity.CONFIDENTIAL)
    ][:MAX_GRAPH_FACTS]
    if lines:
        parts.append("Related facts from the knowledge graph (for orientation only, do not cite):\n" + "\n".join(lines))
    return "\n\n".join(parts)


async def run(state: PipelineState, deps: AgentDeps) -> dict[str, Any]:
    route = state["route"]
    round_number = state.get("round", 0)
    reader_question = state["query_redacted"]
    updates: dict[str, Any] = {"round": round_number}

    if route == "extractive":
        draft = await extractive_answer(reader_question, state["llm_evidence"], deps.reader)
        if draft:
            return {
                **updates,
                "draft": draft,
                "_summary": "Extractive answer: quoted from the sources by the local reader, nothing generated",
                "_details": {"route": route, "sentences": draft.count("[E")},
            }
        open_evidence = state.get("open_llm_evidence") or []
        if not open_evidence:
            return {
                **updates,
                "draft": "",
                "outcome": "abstained",
                "outcome_reason": "insufficient_evidence",
                "_summary": "No sentence in the evidence answers the question",
                "_details": {"route": route},
            }
        # No single sentence answers the question (it may need a table, or several passages).
        # The external model is asked instead, and is given only the passages that may leave.
        route = "external"
        note = (
            f"No sentence could be quoted as the answer; the model was given the {len(open_evidence)} passage(s) "
            "at or below the egress ceiling and none above it"
        )
        updates.update(
            {
                "route": route,
                "llm_evidence": open_evidence,
                "evidence_notes": [*state.get("evidence_notes", []), note],
            }
        )
        state = {**state, "route": route, "llm_evidence": open_evidence}

    # Names in the question are replaced by the same placeholders as in the evidence.
    question = reader_question
    if route == "external":
        question = state["pseudonymizer"].protect(
            reader_question,
            sensitivity_rank=int(Sensitivity.CONFIDENTIAL),
            pseudonymize_contact=state["access"].egress.pseudonymize_names
            and any(e.sensitivity_rank >= Sensitivity.CONFIDENTIAL for e in state["llm_evidence"]),
        )
    user_prompt = build_user_prompt(state, question)
    prompt = ANSWER
    if round_number > 0:
        prompt = CORRECTION
        rejected = "\n".join(f'- "{c.text}": {c.reason}' for c in state["report"].failed)
        user_prompt += f"\n\nPrevious draft:\n{state['draft']}\n\nRejected statements:\n{rejected}"

    try:
        result = await deps.llm.complete(
            purpose="answer" if round_number == 0 else "correction",
            system=prompt.system,
            user=user_prompt,
            max_tokens=ANSWER_MAX_TOKENS,
            run_id=state["run_id"],
            prompt_version=prompt.version,
            local_only=route == "local",
        )
    except DependencyUnavailable as exc:
        # Degraded, not faked: fall back to quoting the sources and say so.
        draft = await extractive_answer(reader_question, state["evidence"], deps.reader)
        note = f"The language model was unavailable ({exc.code}); the answer is quoted directly from the sources"
        if not draft:
            return {
                **updates,
                "draft": "",
                "outcome": "abstained",
                "outcome_reason": "llm_unavailable",
                "evidence_notes": [*state.get("evidence_notes", []), note],
                "_summary": note,
                "_details": {"route": "extractive", "error": exc.code},
            }
        return {
            **updates,
            "draft": draft,
            "route": "extractive",
            "llm_evidence": state["evidence"],
            "evidence_notes": [*state.get("evidence_notes", []), note],
            "_summary": note,
            "_details": {"route": "extractive", "error": exc.code},
        }

    draft = result.text.strip()
    updates.update({"draft": draft, "llm_calls": 0 if result.cached else 1})
    if is_insufficient(draft) and len(draft) < 60:
        updates.update({"outcome": "abstained", "outcome_reason": "insufficient_evidence"})
        summary = "The model found the evidence insufficient to answer"
    else:
        summary = f"Draft written by {result.provider}:{result.model}" + (" (cached)" if result.cached else "")
        if round_number:
            summary = f"Correction round {round_number}: " + summary
    updates["_summary"] = summary
    updates["_details"] = {
        "route": route,
        "provider": result.provider,
        "model": result.model,
        "prompt_version": prompt.version,
        "tokens_in": result.tokens_in,
        "tokens_out": result.tokens_out,
        "latency_ms": result.latency_ms,
        "cached": result.cached,
        "round": round_number,
    }
    return updates
