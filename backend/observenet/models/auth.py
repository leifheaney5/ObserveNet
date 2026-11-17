from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from observenet.models.base import Base


class User(Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("role IN ('admin','viewer')", name="role"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16), default="viewer")
    disabled: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    last_login_at: Mapped[datetime | None]


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)  # HMAC-SHA256 of the cookie value
    csrf_token: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    expires_at: Mapped[datetime]
    last_seen_at: Mapped[datetime | None]
    revoked_at: Mapped[datetime | None]
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(255))


class ApiToken(Base):
    """Scoped, revocable bearer tokens for collectors. Only a hash is stored."""

    __tablename__ = "api_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    token_prefix: Mapped[str] = mapped_column(String(16))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    scopes: Mapped[list[str]] = mapped_column(default=list)  # ["ingest"]
    source_id: Mapped[int | None] = mapped_column(ForeignKey("data_sources.id", ondelete="SET NULL"))
    created_by: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    expires_at: Mapped[datetime | None]
    revoked_at: Mapped[datetime | None]
    last_used_at: Mapped[datetime | None]
    is_bootstrap: Mapped[bool] = mapped_column(default=False)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_events_ts", "ts"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ts: Mapped[datetime] = mapped_column(server_default=func.now())
    actor_user_id: Mapped[int | None] = mapped_column(Integer)
    actor_label: Mapped[str] = mapped_column(String(120))
    action: Mapped[str] = mapped_column(String(64))
    target_type: Mapped[str | None] = mapped_column(String(32))
    target_id: Mapped[str | None] = mapped_column(String(64))
    ip: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[dict[str, Any]] = mapped_column(default=dict)


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(default=dict)
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())
    updated_by: Mapped[int | None] = mapped_column(Integer)
    note: Mapped[str | None] = mapped_column(Text)
