"""Response Generation Agent: writes a cited answer from the evidence, and nothing else."""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Any

from pramana.agents.extractive import extractive_answer
from pramana.agents.state import AgentDeps, PipelineState
from pramana.llm.prompts import ANSWER, CORRECTION
from pramana.privacy.pii import mask_for_reader
from pramana.privacy.risk import injection_flags
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


LOOKUP_LINE = re.compile(r"(?im)^[ \t]*LOOKUP:[ \t]*(.+?)[ \t]*$")
LOOKUP_PASSAGES = 3


def split_lookup(text: str) -> tuple[str, str]:
    """The draft without its ``LOOKUP:`` line, and the query on that line (or "")."""
    match = LOOKUP_LINE.search(text)
    if not match:
        return text.strip(), ""
    return LOOKUP_LINE.sub("", text).strip(), match.group(1).strip().strip("<>\"'")[:200]


async def look_up(
    query: str, state: PipelineState, deps: AgentDeps, route: str
) -> tuple[list[Evidence], list[Evidence]]:
    """Passages for a follow-up query, prepared exactly like the first ones: inside the caller's scope,
    masked for the reader, and pseudonymised or withheld for an external model."""
    access, pseudonymizer = state["access"], state["pseudonymizer"]
    # Placeholders the model saw are turned back into the real names for the local search only.
    result = await deps.retriever.retrieve(
        pseudonymizer.restore(query, reveal=True), principal=state["principal"], scope=access.scope
    )
    known = {chunk for e in state["evidence"] for chunk in e.chunk_ids}
    fresh = [
        e
        for e in result.evidence
        if not (set(e.chunk_ids) & known)
        and not injection_flags(e.text)
        and (route != "external" or e.sensitivity_rank <= access.egress.max_rank)
    ][:LOOKUP_PASSAGES]
    next_ref = max((int(e.ref[1:]) for e in state["evidence"]), default=0) + 1
    for_reader: list[Evidence] = []
    for_llm: list[Evidence] = []
    for offset, item in enumerate(fresh):
        shown = replace(
            item,
            ref=f"E{next_ref + offset}",
            text=mask_for_reader(
                item.text, sensitivity_rank=item.sensitivity_rank, can_read_pii=access.scope.can_read_pii
            ),
        )
        for_reader.append(shown)
        for_llm.append(
            replace(
                shown,
                text=pseudonymizer.protect(
                    shown.text,
                    sensitivity_rank=shown.sensitivity_rank,
                    pseudonymize_contact=access.egress.pseudonymize_names,
                ),
            )
            if route == "external"
            else shown
        )
    return for_reader, for_llm


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

    draft, lookup = split_lookup(result.text)
    llm_calls = 0 if result.cached else 1
    if lookup and round_number == 0:
        # The model named something it needs and cannot see (the holder of an office, the price of a
        # band). Search for that, inside the same access scope, and let it try once more.
        found, found_for_llm = await look_up(lookup, state, deps, route)
        if found:
            evidence = [*state["evidence"], *found]
            llm_evidence = [*state["llm_evidence"], *found_for_llm]
            note = f'Looked up "{lookup}": {len(found)} more passage(s)'
            notes = [*updates.get("evidence_notes", state.get("evidence_notes", [])), note]
            updates.update({"evidence": evidence, "llm_evidence": llm_evidence, "evidence_notes": notes})
            state = {**state, "evidence": evidence, "llm_evidence": llm_evidence}
            try:
                second = await deps.llm.complete(
                    purpose="answer",
                    system=prompt.system,
                    user=build_user_prompt(state, question),
                    max_tokens=ANSWER_MAX_TOKENS,
                    run_id=state["run_id"],
                    prompt_version=prompt.version,
                    local_only=route == "local",
                )
            except DependencyUnavailable:
                second = None
            if second is not None:
                llm_calls += 0 if second.cached else 1
                result = second
                draft, _ = split_lookup(second.text)
    updates.update({"draft": draft, "llm_calls": llm_calls})
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
