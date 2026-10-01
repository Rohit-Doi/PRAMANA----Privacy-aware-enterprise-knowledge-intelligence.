"""Privacy Agent: request intake, personal-data redaction, policy scope, injection signals."""

from __future__ import annotations

import time
from typing import Any

from pramana.agents.state import AgentDeps, PipelineState
from pramana.privacy.pii import Pseudonymizer
from pramana.privacy.risk import injection_flags
from pramana.privacy.scope import EMPTY_SCOPE
from pramana.shared.types import Permission

FOLLOW_UP_MAX_WORDS = 7
_PRONOUNS = frozenset({"it", "its", "they", "them", "their", "that", "those", "this", "these", "he", "she"})
_FOLLOW_UP_OPENERS = ("what about", "how about", "and for", "and what", "and how", "and ")


def _is_follow_up(question: str) -> bool:
    """A question that cannot be searched on its own: very short, or leaning on a pronoun."""
    lowered = question.lower().strip()
    words = lowered.replace("?", " ").split()
    return (
        len(words) <= FOLLOW_UP_MAX_WORDS
        or lowered.startswith(_FOLLOW_UP_OPENERS)
        or any(word in _PRONOUNS for word in words)
    )


async def run(state: PipelineState, deps: AgentDeps) -> dict[str, Any]:
    principal = state["principal"]
    question = state["question"].strip()

    # 1. Personal data in the question. High-risk identifiers never leave this step.
    findings = deps.pii.analyze(question)
    redaction = deps.pii.redact(question, include_contact=False)
    counts: dict[str, int] = {}
    for finding in findings:
        counts[finding.entity_type] = counts.get(finding.entity_type, 0) + 1

    # 2. What this caller may read, decided by the policy engine from database identity.
    access = await deps.policy.access_context(principal)
    injection = injection_flags(question)

    # 3. Request volume, used later by the risk assessment.
    recent = 0
    try:
        key = f"risk:requests:{principal.user_id}:{int(time.time() // 300)}"
        recent = int(await deps.redis.incr(key))
        if recent == 1:
            await deps.redis.expire(key, 600)
    except Exception:  # noqa: S110 - a missing counter must not block the request
        pass

    # 4. A short follow-up ("what about contractors?") is searched together with the question before it.
    retrieval_query = redaction.text
    previous = state.get("previous_question")
    if previous and _is_follow_up(retrieval_query):
        retrieval_query = f"{previous} {retrieval_query}"

    updates: dict[str, Any] = {
        "access": access,
        "query_redacted": redaction.text,
        "retrieval_query": retrieval_query,
        "query_pii": counts,
        "injection": injection,
        "recent_requests": recent,
        "pseudonymizer": Pseudonymizer(deps.pii),
    }
    scope = access.scope
    if scope == EMPTY_SCOPE or not principal.has(Permission.QUERY):
        updates["outcome"] = "denied"
        updates["outcome_reason"] = "no_access_scope" if scope == EMPTY_SCOPE else "missing_permission"

    high_risk = sum(1 for f in findings if f.high_risk)
    updates["_summary"] = (
        f"Scope: own department up to rank {scope.max_rank_own_dept}, others up to {scope.max_rank_other_dept}; "
        f"{high_risk} identifier(s) redacted; policy v{scope.policy_version}"
    )
    updates["_details"] = {
        "scope": scope.as_dict(),
        "pii_found": counts,
        "high_risk_redacted": high_risk,
        "injection_patterns": injection,
        "egress_max_rank": access.egress.max_rank,
        "follow_up_expanded": retrieval_query != redaction.text,
    }
    return updates
