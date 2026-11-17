"""Vendor-neutral inventory path: import the router's DHCP / client list (CSV or JSON).

Works with any router that can show or export its client table. It is a point-in-time
snapshot — presence is 'as of import', never live.
"""

from __future__ import annotations

import csv
import io
import ipaddress
import json
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from observenet.models import DataSource
from observenet.providers.base import (
    CapabilityReport,
    ClientRecord,
    RouterCapability,
    RouterTelemetryProvider,
    SourceQuality,
)
from observenet.services import identity
from observenet.services.events import add_event

MAX_ROWS = 2000
_IP_KEYS = ("ip", "ip_address", "ipaddress", "address", "ipv4", "ip address")
_MAC_KEYS = ("mac", "mac_address", "macaddress", "hwaddr", "hardware_address", "mac address")
_NAME_KEYS = ("hostname", "host", "name", "device", "device_name", "client", "device name")


class ImportError_(ValueError):
    pass


def _pick(row: dict[str, object], keys: tuple[str, ...]) -> str | None:
    lowered = {str(k).strip().lower(): v for k, v in row.items() if k is not None}
    for k in keys:
        v = lowered.get(k)
        if v not in (None, ""):
            return str(v).strip()
    return None


def parse_client_list(content: str, observed_at: datetime) -> tuple[list[ClientRecord], list[str]]:
    """Parse CSV (with header) or a JSON array of objects. Returns (records, row errors)."""
    content = content.strip()
    if not content:
        raise ImportError_("the file is empty")
    rows: list[dict[str, object]]
    if content[0] in "[{":
        try:
            data = json.loads(content)
        except ValueError as exc:
            raise ImportError_(f"invalid JSON: {exc}") from exc
        if isinstance(data, dict):
            data = data.get("clients") or data.get("devices") or data.get("leases") or []
        if not isinstance(data, list) or not all(isinstance(r, dict) for r in data):
            raise ImportError_("JSON must be an array of objects with ip / mac / hostname fields")
        rows = data
    else:
        reader = csv.DictReader(io.StringIO(content))
        if not reader.fieldnames:
            raise ImportError_("CSV needs a header row (ip, mac, hostname)")
        rows = [dict(r) for r in reader]
    if len(rows) > MAX_ROWS:
        raise ImportError_(f"too many rows ({len(rows)}); the limit is {MAX_ROWS}")

    records, errors = [], []
    for n, row in enumerate(rows, start=1):
        ip, mac, name = (
            _pick(row, _IP_KEYS),
            identity.normalize_mac(_pick(row, _MAC_KEYS)),
            _pick(row, _NAME_KEYS),
        )
        if not ip:
            errors.append(f"row {n}: no IP address column found")
            continue
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            errors.append(f"row {n}: {ip!r} is not an IP address")
            continue
        if not addr.is_private or addr.is_loopback or addr.is_link_local:
            errors.append(f"row {n}: {ip} is not a private LAN address; skipped")
            continue
        records.append(
            ClientRecord(
                ip=str(addr), mac=mac, hostname=(name or None) and name[:255], observed_at=observed_at
            )
        )
    return records, errors


class ClientListImportProvider(RouterTelemetryProvider):
    name = "client-list-import"

    def __init__(self, records: list[ClientRecord]) -> None:
        self._records = records

    def capabilities(self) -> list[CapabilityReport]:
        supported = CapabilityReport(
            RouterCapability.DEVICE_INVENTORY, "supported",
            "Manual export of the router's client list.", SourceQuality.POINT_IN_TIME, None,
            "Devices the router listed at export time. Not live; re-import to refresh.",
        )  # fmt: skip
        rest = [
            CapabilityReport(
                c, "unsupported", "A static client list carries no counters, signal or settings."
            )
            for c in RouterCapability
            if c != RouterCapability.DEVICE_INVENTORY
        ]
        return [supported, *rest]

    async def read_clients(self) -> list[ClientRecord]:
        return self._records


async def apply_import(
    session: AsyncSession, provider: RouterTelemetryProvider, *, now: datetime, is_demo: bool = False
) -> dict[str, int]:
    source = (
        (await session.execute(select(DataSource).where(DataSource.kind == "client_import")))
        .scalars()
        .first()
    )
    if source is None:
        source = DataSource(kind="client_import", name="Imported router client list", observation_point="gateway",
                            expected_interval_s=86400, is_demo=is_demo)  # fmt: skip
        session.add(source)
        await session.flush()
    created = matched = 0
    for rec in await provider.read_clients():
        sighting = await identity.reconcile_sighting(session, source=source, ip=rec.ip, mac=rec.mac,
                                                     hostname=rec.hostname, kind="client_import", now=rec.observed_at)  # fmt: skip
        if sighting is None:
            continue
        created += sighting.created
        matched += not sighting.created
    source.last_seen_at = now
    add_event(session, "inventory.imported", f"Client list imported: {created} new, {matched} matched existing devices",
              source_id=source.id, data={"created": created, "matched": matched}, is_demo=is_demo)  # fmt: skip
    return {"created": created, "matched": matched, "source_id": source.id}
