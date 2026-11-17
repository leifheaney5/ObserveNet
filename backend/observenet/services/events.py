from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from observenet.models import AuditEvent, Event


def add_event(
    session: AsyncSession,
    kind: str,
    message: str,
    *,
    severity: str = "info",
    asset_id: int | None = None,
    source_id: int | None = None,
    alert_id: int | None = None,
    finding_id: int | None = None,
    data: dict[str, Any] | None = None,
    is_demo: bool = False,
) -> Event:
    event = Event(
        kind=kind, message=message, severity=severity, asset_id=asset_id, source_id=source_id,
        alert_id=alert_id, finding_id=finding_id, data=data or {}, is_demo=is_demo,
    )  # fmt: skip
    session.add(event)
    return event


def add_audit(
    session: AsyncSession,
    action: str,
    *,
    actor_user_id: int | None = None,
    actor_label: str = "system",
    target_type: str | None = None,
    target_id: str | int | None = None,
    ip: str | None = None,
    details: dict[str, Any] | None = None,
) -> AuditEvent:
    row = AuditEvent(
        action=action, actor_user_id=actor_user_id, actor_label=actor_label, target_type=target_type,
        target_id=str(target_id) if target_id is not None else None, ip=ip, details=details or {},
    )  # fmt: skip
    session.add(row)
    return row
