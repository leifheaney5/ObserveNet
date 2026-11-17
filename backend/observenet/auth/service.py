"""Sessions, users and collector tokens."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from observenet.auth.crypto import (
    hash_password,
    new_agent_token,
    new_secret,
    password_needs_rehash,
    token_digest,
    verify_password,
)
from observenet.core.config import get_config
from observenet.core.time import utcnow
from observenet.models import ApiToken, Session, User

SESSION_COOKIE = "observenet_session"
CSRF_COOKIE = "observenet_csrf"
CSRF_HEADER = "x-csrf-token"
MIN_PASSWORD_LENGTH = 12
# Verifying against a dummy hash keeps login timing similar for unknown usernames.
_DUMMY_HASH = hash_password("observenet-dummy-password-for-timing")


class AuthError(Exception):
    pass


def validate_password_strength(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise AuthError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
    if password.lower() in {"changeme1234", "password1234", "observenet123", "adminadminadmin"}:
        raise AuthError("password is a known placeholder; choose a unique one")


async def user_count(session: AsyncSession) -> int:
    return (await session.execute(select(func.count()).select_from(User))).scalar_one()


async def create_user(session: AsyncSession, *, username: str, password: str, role: str) -> User:
    validate_password_strength(password)
    if role not in ("admin", "viewer"):
        raise AuthError("role must be admin or viewer")
    user = User(username=username.strip().lower(), password_hash=hash_password(password), role=role)
    session.add(user)
    await session.flush()
    return user


async def bootstrap_admin(session: AsyncSession) -> str:
    """Create the first admin from OBSERVENET_ADMIN_PASSWORD. There are no default credentials:
    without the variable (or with a weak value) no account is created."""
    if await user_count(session) > 0:
        return "exists"
    cfg = get_config()
    if not cfg.admin_password:
        return "missing_password"
    try:
        await create_user(session, username=cfg.admin_username, password=cfg.admin_password, role="admin")
    except AuthError:
        return "weak_password"
    return "created"


async def authenticate(session: AsyncSession, username: str, password: str) -> User | None:
    user = (
        await session.execute(select(User).where(User.username == username.strip().lower()))
    ).scalar_one_or_none()
    if user is None or user.disabled:
        verify_password(password, _DUMMY_HASH)
        return None
    if not verify_password(password, user.password_hash):
        return None
    if password_needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    user.last_login_at = utcnow()
    return user


async def create_session(
    session: AsyncSession, user: User, *, ip: str | None, user_agent: str | None
) -> tuple[str, Session]:
    raw = new_secret(32)
    row = Session(
        user_id=user.id, token_hash=token_digest(raw), csrf_token=new_secret(24),
        expires_at=utcnow() + timedelta(hours=get_config().session_ttl_hours), ip=ip,
        user_agent=(user_agent or "")[:255] or None,
    )  # fmt: skip
    session.add(row)
    await session.flush()
    return raw, row


async def resolve_session(session: AsyncSession, raw_token: str | None) -> tuple[Session, User] | None:
    if not raw_token:
        return None
    row = (
        await session.execute(
            select(Session, User)
            .join(User, User.id == Session.user_id)
            .where(Session.token_hash == token_digest(raw_token))
        )
    ).first()
    if row is None:
        return None
    sess, user = row
    now = utcnow()
    if sess.revoked_at is not None or sess.expires_at <= now or user.disabled:
        return None
    if sess.last_seen_at is None or (now - sess.last_seen_at).total_seconds() > 60:
        sess.last_seen_at = now
    return sess, user


async def issue_agent_token(
    session: AsyncSession, *, name: str, created_by: int | None, ttl_days: int | None, is_bootstrap: bool = False,
    raw: str | None = None,
) -> tuple[str, ApiToken]:  # fmt: skip
    raw = raw or new_agent_token()
    expires: datetime | None = utcnow() + timedelta(days=ttl_days) if ttl_days else None
    row = ApiToken(name=name, token_prefix=raw[:10], token_hash=token_digest(raw), scopes=["ingest"],
                   created_by=created_by, expires_at=expires, is_bootstrap=is_bootstrap)  # fmt: skip
    session.add(row)
    await session.flush()
    return raw, row


async def ensure_bootstrap_agent_token(session: AsyncSession) -> None:
    """Register OBSERVENET_BOOTSTRAP_AGENT_TOKEN (used by the bundled collector) once.
    If an operator revoked it, it stays revoked."""
    raw = get_config().bootstrap_agent_token
    if not raw or len(raw) < 32:
        return
    exists = (
        await session.execute(select(ApiToken.id).where(ApiToken.token_hash == token_digest(raw)))
    ).scalar_one_or_none()
    if exists is None:
        await issue_agent_token(session, name="bundled collector (from environment)", created_by=None,
                                ttl_days=None, is_bootstrap=True, raw=raw)  # fmt: skip


async def resolve_agent_token(
    session: AsyncSession, raw: str | None, *, scope: str = "ingest"
) -> ApiToken | None:
    if not raw:
        return None
    token = (
        await session.execute(select(ApiToken).where(ApiToken.token_hash == token_digest(raw)))
    ).scalar_one_or_none()
    now = utcnow()
    if token is None or token.revoked_at is not None or scope not in token.scopes:
        return None
    if token.expires_at is not None and token.expires_at <= now:
        return None
    if token.last_used_at is None or (now - token.last_used_at).total_seconds() > 60:
        token.last_used_at = now
    return token
