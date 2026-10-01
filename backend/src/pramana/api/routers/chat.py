"""Ask questions (with live progress), browse conversations, read past runs, give feedback."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sse_starlette.sse import EventSourceResponse

from pramana.agents.models import Conversation, Message, QueryRun
from pramana.agents.pipeline import public_error
from pramana.agents.run_view import run_payload
from pramana.api.deps import DbSession, RuntimeDep, require
from pramana.auth.models import User
from pramana.auth.principal import Principal, principal_from_user
from pramana.governance.audit import AuditTrail
from pramana.shared.db import utcnow
from pramana.shared.errors import NotFound, PermissionDenied, ValidationFailed
from pramana.shared.logging import get_logger
from pramana.shared.types import Permission
from pramana.validation.models import Claim, Feedback, ReviewItem

log = get_logger(__name__)
router = APIRouter(tags=["Ask"])
Asker = Annotated[Principal, Depends(require(Permission.QUERY))]


class AskRequest(BaseModel):
    question: str = Field(min_length=2, max_length=2000)
    conversation_id: uuid.UUID | None = None
    mode: Literal["strict", "live"] = "strict"
    as_user_id: uuid.UUID | None = Field(
        default=None, description="Preview the answer another user would get (requires users:simulate)."
    )


class ConversationSummary(BaseModel):
    id: uuid.UUID
    title: str
    updated_at: datetime
    message_count: int


class MessageView(BaseModel):
    id: uuid.UUID
    role: str
    content: str
    run_id: uuid.UUID | None
    created_at: datetime


class ConversationDetail(BaseModel):
    id: uuid.UUID
    title: str
    messages: list[MessageView]


class FeedbackRequest(BaseModel):
    rating: Literal[-1, 1]
    comment: str = Field(default="", max_length=2000)
    claim_id: uuid.UUID | None = None


async def _effective_principal(body: AskRequest, principal: Principal, db: DbSession) -> tuple[Principal, bool]:
    """The identity the question runs as. Simulation is a privileged, audited preview."""
    if body.as_user_id is None or body.as_user_id == principal.user_id:
        return principal, True
    if not principal.has(Permission.SIMULATE_USER):
        raise PermissionDenied("Missing permission: users:simulate")
    target = await db.get(User, body.as_user_id)
    if target is None or not target.is_active:
        raise NotFound("User not found")
    await AuditTrail().record(
        action="user.simulate",
        decision="GRANTED",
        principal=principal,
        resource_type="user",
        resource_id=str(target.id),
        details={"target_email": target.email, "target_role": target.role.key},
    )
    return principal_from_user(target, simulated_by=principal.user_id), False


@router.post("/chat/ask", summary="Ask a question and receive the verified answer")
async def ask(body: AskRequest, principal: Asker, db: DbSession, runtime: RuntimeDep) -> dict[str, Any]:
    effective, persist = await _effective_principal(body, principal, db)
    return await runtime.services.query.run(
        principal=effective,
        question=body.question,
        conversation_id=body.conversation_id if persist else None,
        mode=body.mode,
        persist_conversation=persist,
    )


@router.post("/chat/ask/stream", summary="Ask a question; agent progress and the answer arrive as server-sent events")
async def ask_stream(body: AskRequest, principal: Asker, db: DbSession, runtime: RuntimeDep) -> EventSourceResponse:
    effective, persist = await _effective_principal(body, principal, db)
    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

    async def emit(event: dict[str, Any]) -> None:
        await queue.put(event)

    async def work() -> None:
        try:
            await runtime.services.query.run(
                principal=effective,
                question=body.question,
                conversation_id=body.conversation_id if persist else None,
                mode=body.mode,
                emit=emit,
                persist_conversation=persist,
            )
        except Exception as exc:
            # The pipeline has already recorded and audited the failure; tell the client.
            await queue.put({"type": "error", "error": public_error(exc)})
        finally:
            await queue.put(None)

    # The run continues even if the browser disconnects, so that it is always recorded.
    task = asyncio.create_task(work())
    runtime.extras.setdefault("tasks", set()).add(task)
    task.add_done_callback(runtime.extras["tasks"].discard)

    async def events() -> AsyncIterator[dict[str, str]]:
        while True:
            event = await queue.get()
            if event is None:
                break
            yield {"event": event["type"], "data": json.dumps(event, default=str)}

    return EventSourceResponse(events(), ping=15)


@router.get("/chat/conversations", response_model=list[ConversationSummary], summary="Your conversations")
async def conversations(principal: Asker, db: DbSession, limit: int = 50) -> list[ConversationSummary]:
    rows = await db.execute(
        select(Conversation, func.count(Message.id))
        .outerjoin(Message, Message.conversation_id == Conversation.id)
        .where(Conversation.user_id == principal.user_id)
        .group_by(Conversation.id)
        .order_by(Conversation.updated_at.desc())
        .limit(min(limit, 200))
    )
    return [
        ConversationSummary(id=c.id, title=c.title, updated_at=c.updated_at, message_count=count)
        for c, count in rows.all()
    ]


async def _own_conversation(conversation_id: uuid.UUID, principal: Principal, db: DbSession) -> Conversation:
    conversation = await db.get(Conversation, conversation_id)
    if conversation is None or conversation.user_id != principal.user_id:
        raise NotFound("Conversation not found")
    return conversation


@router.get(
    "/chat/conversations/{conversation_id}", response_model=ConversationDetail, summary="Messages of a conversation"
)
async def conversation(conversation_id: uuid.UUID, principal: Asker, db: DbSession) -> ConversationDetail:
    found = await _own_conversation(conversation_id, principal, db)
    messages = await db.execute(
        select(Message).where(Message.conversation_id == conversation_id).order_by(Message.created_at, Message.id)
    )
    return ConversationDetail(
        id=found.id,
        title=found.title,
        messages=[
            MessageView(id=m.id, role=m.role, content=m.content, run_id=m.run_id, created_at=m.created_at)
            for m in messages.scalars()
        ],
    )


@router.delete("/chat/conversations/{conversation_id}", status_code=204, summary="Delete a conversation")
async def delete_conversation(conversation_id: uuid.UUID, principal: Asker, db: DbSession) -> None:
    await _own_conversation(conversation_id, principal, db)
    # Runs and their audit entries are kept: deleting the chat view does not erase the record.
    await db.execute(delete(Message).where(Message.conversation_id == conversation_id))
    await db.execute(delete(Conversation).where(Conversation.id == conversation_id))


async def _readable_run(run_id: uuid.UUID, principal: Principal, db: DbSession) -> QueryRun:
    run = await db.get(QueryRun, run_id)
    if run is None or (run.user_id != principal.user_id and not principal.has(Permission.MONITORING_READ)):
        raise NotFound("Run not found")
    return run


@router.get("/runs/{run_id}", summary="Everything recorded about one run: answer, claims, evidence, agent steps")
async def run_detail(run_id: uuid.UUID, principal: Asker, db: DbSession) -> dict[str, Any]:
    run = await _readable_run(run_id, principal, db)
    payload = await run_payload(db, run)
    if run.user_id != principal.user_id:
        # An operator sees how the run went, not the content another user was entitled to.
        payload["evidence"] = [
            {**e, "text": "[hidden: belongs to another user's access scope]"} for e in payload["evidence"]
        ]
        payload["answer"] = "[hidden: belongs to another user's access scope]"
        for claim in payload["claims"]:
            claim["text"] = "[hidden]"
            for citation in claim["citations"]:
                citation["quote"] = "[hidden]"
    return payload


@router.post("/runs/{run_id}/feedback", status_code=204, summary="Rate an answer or report a wrong sentence")
async def feedback(run_id: uuid.UUID, body: FeedbackRequest, principal: Asker, db: DbSession) -> None:
    run = await db.get(QueryRun, run_id)
    if run is None or run.user_id != principal.user_id:
        raise NotFound("Run not found")
    if body.claim_id is not None:
        claim = await db.get(Claim, body.claim_id)
        if claim is None or claim.run_id != run_id:
            raise ValidationFailed("That sentence does not belong to this answer")
    db.add(
        Feedback(
            run_id=run_id,
            user_id=principal.user_id,
            claim_id=body.claim_id,
            rating=body.rating,
            comment=body.comment,
            created_at=utcnow(),
        )
    )
    if body.rating < 0:
        # A reader disputing an answer sends it to human review.
        open_item = (
            await db.execute(select(ReviewItem.id).where(ReviewItem.run_id == run_id, ReviewItem.status == "open"))
        ).first()
        if open_item is None:
            db.add(ReviewItem(run_id=run_id, reason="negative_feedback", note=body.comment[:500]))
        run.needs_review = True
    await AuditTrail().record(
        action="run.feedback",
        decision="RECORDED",
        principal=principal,
        resource_type="run",
        resource_id=str(run_id),
        details={"rating": body.rating, "claim_id": str(body.claim_id) if body.claim_id else None},
    )
