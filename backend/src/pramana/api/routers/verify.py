"""Verify Studio API: check any text claim by claim."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from pramana.api.deps import RuntimeDep, require
from pramana.auth.principal import Principal
from pramana.shared.types import Permission
from pramana.validation.verify_service import MAX_TEXT_CHARS, VerifyRequest, VerifyService

router = APIRouter(tags=["Verify"])


class VerifyBody(BaseModel):
    text: str = Field(min_length=10, max_length=MAX_TEXT_CHARS, description="The text to check")
    reference_text: str | None = Field(
        default=None,
        max_length=60_000,
        description="Check against this text instead of the knowledge base",
    )
    reference_title: str = Field(default="Supplied reference", max_length=200)


@router.post("/verify", summary="Verify a text sentence by sentence against the knowledge base or a reference")
async def verify(
    body: VerifyBody,
    principal: Annotated[Principal, Depends(require(Permission.VERIFY))],
    runtime: RuntimeDep,
) -> dict[str, Any]:
    services = runtime.services
    service = VerifyService(
        retriever=services.retriever, validation=services.validation, policy=runtime.policy, pii=services.pii
    )
    return await service.verify(
        VerifyRequest(text=body.text, reference_text=body.reference_text, reference_title=body.reference_title),
        principal=principal,
    )
