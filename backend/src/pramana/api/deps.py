"""FastAPI dependencies: database session, runtime, authenticated principal, permission checks."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Coroutine
from typing import Annotated, Any

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from pramana.auth.principal import Principal
from pramana.auth.service import AuthService
from pramana.runtime import Runtime, get_runtime
from pramana.shared.db import get_session_factory
from pramana.shared.errors import NotAuthenticated, PermissionDenied
from pramana.shared.types import Permission

_bearer = HTTPBearer(auto_error=False, description="Access token from /auth/login")


async def db_session() -> AsyncIterator[AsyncSession]:
    """One transaction per request: committed if the handler returns, rolled back if it raises."""
    async with get_session_factory()() as session:
        try:
            yield session
            await session.commit()
        except BaseException:
            await session.rollback()
            raise


DbSession = Annotated[AsyncSession, Depends(db_session)]


def runtime() -> Runtime:
    return get_runtime()


RuntimeDep = Annotated[Runtime, Depends(runtime)]


async def current_principal(
    request: Request,
    db: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Principal:
    """Identity is derived only from the verified bearer token and the database."""
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise NotAuthenticated("Missing bearer token")
    principal = await AuthService(db).principal_for_access_token(credentials.credentials)
    request.state.principal = principal
    return principal


CurrentPrincipal = Annotated[Principal, Depends(current_principal)]


def require(*permissions: Permission) -> Callable[..., Coroutine[Any, Any, Principal]]:
    """Dependency factory: the caller must hold every listed permission."""

    async def checker(principal: CurrentPrincipal) -> Principal:
        missing = [str(p) for p in permissions if not principal.has(p)]
        if missing:
            raise PermissionDenied("Missing permission: " + ", ".join(missing))
        return principal

    return checker


def client_ip(request: Request) -> str:
    return request.client.host if request.client else ""
