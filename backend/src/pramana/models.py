"""Imports every ORM model so that ``Base.metadata`` is complete (used by Alembic and tests)."""

from pramana.agents.models import AgentStep, Conversation, LLMCall, Message, QueryRun
from pramana.auth.models import Department, Role, Session, User
from pramana.governance.models import AppSetting, AuditLog, EvalRun, PolicyVersion
from pramana.knowledge.models import Chunk, Document, IngestionJob
from pramana.shared.db import Base
from pramana.validation.models import Citation, Claim, Feedback, ReviewItem

__all__ = [
    "AgentStep",
    "AppSetting",
    "AuditLog",
    "Base",
    "Chunk",
    "Citation",
    "Claim",
    "Conversation",
    "Department",
    "Document",
    "EvalRun",
    "Feedback",
    "IngestionJob",
    "LLMCall",
    "Message",
    "PolicyVersion",
    "QueryRun",
    "ReviewItem",
    "Role",
    "Session",
    "User",
]
