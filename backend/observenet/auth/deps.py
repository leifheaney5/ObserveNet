"""FastAPI auth dependencies: cookie session + CSRF for browsers, bearer tokens for collectors."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from observenet.auth import service
from observenet.auth.crypto import constant_time_equals
from observenet.core.db import get_session
from observenet.models import ApiToken, User

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
DbSession = Annotated[AsyncSession, Depends(get_session)]


@dataclass
class Principal:
    user: User
    session_id: int
    csrf_token: str

    @property
    def is_admin(self) -> bool:
        return self.user.role == "admin"


def client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


async def get_principal(request: Request, db: DbSession) -> Principal:
    resolved = await service.resolve_session(db, request.cookies.get(service.SESSION_COOKIE))
    if resolved is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "authentication required")
    sess, user = resolved
    if request.method not in SAFE_METHODS:
        # Double-submit token bound to the server-side session. SameSite=Lax is the first
        # layer; this defeats same-site / subdomain cross-origin form posts.
        sent = request.headers.get(service.CSRF_HEADER, "")
        if not sent or not constant_time_equals(sent, sess.csrf_token):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "missing or invalid CSRF token")
    return Principal(user=user, session_id=sess.id, csrf_token=sess.csrf_token)


CurrentUser = Annotated[Principal, Depends(get_principal)]


async def require_admin(principal: CurrentUser) -> Principal:
    if not principal.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "administrator role required")
    return principal


AdminUser = Annotated[Principal, Depends(require_admin)]


async def get_agent_token(request: Request, db: DbSession) -> ApiToken:
    header = request.headers.get("authorization", "")
    scheme, _, raw = header.partition(" ")
    token = await service.resolve_agent_token(db, raw.strip() if scheme.lower() == "bearer" else None)
    if token is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid, expired or revoked agent token",
                            headers={"WWW-Authenticate": "Bearer"})  # fmt: skip
    return token


AgentToken = Annotated[ApiToken, Depends(get_agent_token)]
