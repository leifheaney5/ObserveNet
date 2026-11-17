"""Payload builders and small DB helpers shared by the tests (fake collectors)."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select

from observenet.auth import service as auth_service
from observenet.core.db import session_scope
from observenet.core.time import utcnow
from observenet.models import ApiToken, Asset, DataSource, ProbeTarget
from observenet.schemas.ingest import IngestPayload
from observenet.services import ingest as ingest_service

AGENT_UID = "lnx-test-agent-0001"


def sample(
    metric: str, value: float, ts: datetime, *, label: str = "eth0", unit: str | None = None, **extra: Any
) -> dict[str, Any]:
    from observenet.schemas.ingest import METRIC_UNITS

    return {"metric": metric, "label": label, "ts": ts.isoformat(), "value": value,
            "unit": unit or METRIC_UNITS[metric], **extra}  # fmt: skip


def listening(
    *sockets: tuple[str, str, int], status: str = "ok", reason: str | None = None
) -> dict[str, Any]:
    return {"status": status, "reason": reason, "method": "fixture socket table",
            "items": [{"proto": p, "address": a, "port": port, "process": None} for p, a, port in sockets]}  # fmt: skip


def posture(collected_at: datetime, **sections: Any) -> dict[str, Any]:
    return {"collected_at": collected_at.isoformat(), **sections}


def inventory(collected_at: datetime, neighbors: list[tuple[str, str | None]], *, own_ip: str = "192.168.1.5",
              own_mac: str | None = "3c:22:fb:aa:bb:01", gateway: str | None = "192.168.1.1",
              neighbors_status: str = "ok") -> dict[str, Any]:  # fmt: skip
    return {
        "collected_at": collected_at.isoformat(),
        "interfaces": [{"name": "eth0", "mac": own_mac, "is_up": True, "link_speed_mbps": 1000, "mtu": 1500,
                        "addresses": [{"family": "ipv4", "address": own_ip, "prefix_len": 24}]}],
        "default_gateway": {"ip": gateway, "interface": "eth0"} if gateway else None,
        "neighbors_status": neighbors_status,
        "neighbors": [{"ip": ip, "mac": mac, "interface": "eth0", "state": "reachable"} for ip, mac in neighbors],
    }  # fmt: skip


def payload(*, agent_uid: str = AGENT_UID, hostname: str = "test-host", platform: str = "linux",
            emulates: str | None = None, batch_id: str | None = None, now: datetime | None = None,
            **parts: Any) -> dict[str, Any]:  # fmt: skip
    now = now or utcnow()
    return {
        "schema_version": 1,
        "agent": {"agent_uid": agent_uid, "hostname": hostname, "platform": platform, "emulates": emulates,
                  "agent_version": "test"},
        "batch_id": batch_id or uuid.uuid4().hex,
        "sent_at": now.isoformat(),
        **parts,
    }  # fmt: skip


async def make_source(*, agent_uid: str = AGENT_UID, name: str = "test-host", platform: str = "linux",
                      kind: str = "linux_agent", is_demo: bool = False) -> int:  # fmt: skip
    async with session_scope() as session:
        source = DataSource(kind=kind, name=name, agent_uid=agent_uid, platform=platform, is_demo=is_demo)
        session.add(source)
        await session.flush()
        return source.id


async def process(
    source_id: int, body: dict[str, Any], *, received_at: datetime | None = None
) -> dict[str, Any]:
    """Run the worker-side batch processing in one transaction, like the actor does."""
    parsed = IngestPayload.model_validate(body)
    async with session_scope() as session:
        return await ingest_service.process_batch(session, source_id=source_id, payload=parsed,
                                                  received_at=received_at or utcnow())  # fmt: skip


async def source_asset_id(source_id: int) -> int:
    async with session_scope() as session:
        source = await session.get(DataSource, source_id)
        assert source is not None and source.asset_id is not None
        return source.asset_id


async def all_assets() -> list[Asset]:
    async with session_scope() as session:
        return list((await session.execute(select(Asset).order_by(Asset.id))).scalars())


async def make_target(name: str = "gw", *, kind: str = "gateway_icmp", scope: str = "gateway", host: str = "auto",
                      port: int | None = None, interval_s: float = 1.0, enabled: bool = True) -> int:  # fmt: skip
    async with session_scope() as session:
        target = ProbeTarget(
            name=name, kind=kind, scope=scope, host=host, port=port, interval_s=interval_s, enabled=enabled
        )
        session.add(target)
        await session.flush()
        return target.id


async def issue_token(name: str = "t", *, ttl_days: int | None = 30) -> tuple[str, int]:
    async with session_scope() as session:
        raw, row = await auth_service.issue_agent_token(
            session, name=name, created_by=None, ttl_days=ttl_days
        )
        return raw, row.id


async def get_token(token_id: int) -> ApiToken:
    async with session_scope() as session:
        token = await session.get(ApiToken, token_id)
        assert token is not None
        return token


def ago(seconds: float, now: datetime | None = None) -> datetime:
    return (now or utcnow()) - timedelta(seconds=seconds)
