"""Conservative device identity reconciliation.

Principles
----------
* A globally-administered MAC is a strong key. A locally-administered MAC (the bit that
  phones/laptops set when randomizing) is kept as its own low-confidence asset.
* An IP address alone is never a durable key (DHCP reassigns it). It is scoped to the
  reporting source and labelled low confidence.
* A DHCP change (same MAC, new IP) keeps the asset and adds an address observation.
* Nothing is merged silently: the only automatic merge (an agent's self-reported NIC MAC
  equals a neighbour-cache sighting) is recorded as an undoable ``asset_merges`` row.
"""

from __future__ import annotations

import csv
import ipaddress
import re
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from observenet.core.config import get_config
from observenet.models import AddressObservation, Asset, AssetIdentity, AssetMerge, DataSource, NetworkSegment
from observenet.services.events import add_audit, add_event

_MAC_RE = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$")
_NULL_MACS = {"00:00:00:00:00:00", "ff:ff:ff:ff:ff:ff"}


def normalize_mac(mac: str | None) -> str | None:
    if not mac:
        return None
    m = mac.strip().lower().replace("-", ":")
    if not _MAC_RE.match(m) or m in _NULL_MACS:
        return None
    return m


def is_locally_administered(mac: str) -> bool:
    """IEEE 802: bit 1 of the first octet set => locally administered (often randomized)."""
    return bool(int(mac[:2], 16) & 0b10)


def is_multicast_mac(mac: str) -> bool:
    return bool(int(mac[:2], 16) & 0b1)


@lru_cache(maxsize=1)
def _oui_table() -> dict[str, str]:
    """Load the IEEE MA-L registry CSV if the operator provided one (OBSERVENET_OUI_FILE).

    ObserveNet does not bundle a vendor table: without the file, vendor stays unknown.
    """
    path = get_config().oui_file
    if not path or not Path(path).is_file():
        return {}
    table: dict[str, str] = {}
    with open(path, newline="", encoding="utf-8", errors="replace") as fh:
        for row in csv.DictReader(fh):
            assignment = (row.get("Assignment") or "").strip().lower()
            name = (row.get("Organization Name") or "").strip()
            if len(assignment) == 6 and name:
                table[assignment] = name
    return table


def lookup_vendor(mac: str) -> tuple[str | None, str | None]:
    if is_locally_administered(mac):
        return None, None
    name = _oui_table().get(mac.replace(":", "")[:6])
    return (name, "ieee-oui-registry") if name else (None, None)


async def resolve_merged(session: AsyncSession, asset: Asset) -> Asset:
    seen = {asset.id}
    while asset.merged_into_id is not None:
        nxt = await session.get(Asset, asset.merged_into_id)
        if nxt is None or nxt.id in seen:
            break
        seen.add(nxt.id)
        asset = nxt
    return asset


async def _asset_by_identity(
    session: AsyncSession, kind: str, value: str
) -> tuple[Asset, AssetIdentity] | None:
    row = (
        await session.execute(
            select(Asset, AssetIdentity)
            .join(AssetIdentity, AssetIdentity.asset_id == Asset.id)
            .where(AssetIdentity.kind == kind, AssetIdentity.value == value)
        )
    ).first()
    return (row[0], row[1]) if row else None


def _touch(asset: Asset, now: datetime, source_id: int) -> None:
    if asset.first_seen is None or now < asset.first_seen:
        asset.first_seen = now
    if asset.last_seen is None or now > asset.last_seen:
        asset.last_seen = now
        asset.last_seen_source_id = source_id


async def segment_for_ip(session: AsyncSession, ip: str) -> int | None:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return None
    best: tuple[int, int] | None = None
    for seg_id, cidr in (await session.execute(select(NetworkSegment.id, NetworkSegment.cidr))).all():
        net = ipaddress.ip_network(cidr)
        if addr.version == net.version and addr in net and (best is None or net.prefixlen > best[1]):
            best = (seg_id, net.prefixlen)
    return best[0] if best else None


async def upsert_address_observation(
    session: AsyncSession,
    *,
    asset_id: int,
    source_id: int,
    kind: str,
    ip: str,
    mac: str | None,
    hostname: str | None,
    now: datetime,
) -> None:
    segment_id = await segment_for_ip(session, ip)
    stmt = pg_insert(AddressObservation).values(
        asset_id=asset_id, source_id=source_id, kind=kind, ip=ip, mac=mac or "", hostname=hostname,
        segment_id=segment_id, first_seen=now, last_seen=now,
    )  # fmt: skip
    stmt = stmt.on_conflict_do_update(
        constraint="uq_address_observation",
        set_={
            "last_seen": now,
            "segment_id": segment_id,
            "hostname": stmt.excluded.hostname if hostname else AddressObservation.hostname,
        },
        where=AddressObservation.last_seen < now,
    )
    await session.execute(stmt)


class Sighting:
    __slots__ = ("asset", "created")

    def __init__(self, asset: Asset, created: bool) -> None:
        self.asset = asset
        self.created = created


async def reconcile_sighting(
    session: AsyncSession,
    *,
    source: DataSource,
    ip: str,
    mac: str | None,
    hostname: str | None,
    kind: str,
    now: datetime,
) -> Sighting | None:
    """Attach an (ip, mac) sighting to an asset, creating a low/medium-confidence one if new."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return None
    if addr.is_multicast or addr.is_unspecified or addr.is_loopback:
        return None
    mac = normalize_mac(mac)
    if mac and is_multicast_mac(mac):
        return None

    created = False
    if mac:
        id_kind, id_value = "mac", mac
        randomized = is_locally_administered(mac)
        confidence = "low" if randomized else "medium"
        reason = (
            "Locally-administered (possibly randomized) MAC address: this may be the same physical "
            "device as another entry and is never merged automatically."
            if randomized
            else f"Identified by a globally-administered MAC address observed via {kind.replace('_', ' ')}."
        )
    else:
        id_kind, id_value = "ip_only", f"{source.id}:{ip}"
        randomized = False
        confidence = "low"
        reason = "Seen by IP address only (no MAC available); the address may be reassigned by DHCP."

    found = await _asset_by_identity(session, id_kind, id_value)
    if found:
        asset, identity = found
        asset = await resolve_merged(session, asset)
        identity.last_seen = max(identity.last_seen, now)
    else:
        vendor, vendor_method = lookup_vendor(mac) if mac else (None, None)
        asset = Asset(
            hostname=hostname, confidence=confidence, confidence_reason=reason, mac_randomized=randomized,
            vendor=vendor, vendor_method=vendor_method, first_seen=now, last_seen=now,
            last_seen_source_id=source.id, is_demo=source.is_demo,
        )  # fmt: skip
        session.add(asset)
        await session.flush()
        session.add(
            AssetIdentity(
                asset_id=asset.id,
                kind=id_kind,
                value=id_value,
                confidence=confidence,
                source_id=source.id,
                first_seen=now,
                last_seen=now,
            )  # fmt: skip
        )
        created = True
        add_event(
            session, "device.first_seen",
            f"New device observed at {ip}" + (f" ({mac})" if mac else "") + f" via {source.name}",
            asset_id=asset.id, source_id=source.id, is_demo=source.is_demo,
            data={"ip": ip, "mac": mac, "evidence": kind, "confidence": confidence},
        )  # fmt: skip

    if hostname and not asset.hostname:
        asset.hostname = hostname
    _touch(asset, now, source.id)
    await upsert_address_observation(
        session, asset_id=asset.id, source_id=source.id, kind=kind, ip=ip, mac=mac, hostname=hostname, now=now
    )
    return Sighting(asset, created)


async def resolve_agent_asset(
    session: AsyncSession, *, source: DataSource, agent_uid: str, hostname: str, platform: str, now: datetime
) -> Asset:
    """The asset an agent runs on. ``agent_uid`` is a strong, agent-generated identity."""
    found = await _asset_by_identity(session, "agent_uid", agent_uid)
    if found:
        asset = await resolve_merged(session, found[0])
        found[1].last_seen = now
    else:
        asset = Asset(
            hostname=hostname, asset_type={"linux": "server", "router": "router"}.get(platform, "desktop"),
            confidence="high", confidence_reason="Reports directly through an authenticated ObserveNet agent.",
            managed=True, first_seen=now, last_seen=now, last_seen_source_id=source.id, is_demo=source.is_demo,
        )  # fmt: skip
        session.add(asset)
        await session.flush()
        session.add(
            AssetIdentity(
                asset_id=asset.id,
                kind="agent_uid",
                value=agent_uid,
                confidence="high",
                source_id=source.id,
                first_seen=now,
                last_seen=now,
            )  # fmt: skip
        )
        add_event(session, "agent.registered", f"Agent on {hostname} registered", asset_id=asset.id,
                  source_id=source.id, is_demo=source.is_demo)  # fmt: skip
    if asset.hostname != hostname:
        asset.hostname = hostname
    asset.managed = True
    _touch(asset, now, source.id)
    if source.asset_id != asset.id:
        source.asset_id = asset.id
    return asset


async def claim_agent_mac(
    session: AsyncSession, *, asset: Asset, source: DataSource, mac: str, now: datetime
) -> None:
    """Register an agent's own NIC MAC. If a neighbour-cache asset already holds that MAC,
    merge it into the agent's asset *with an audit trail* (undoable), never silently."""
    if is_locally_administered(mac) or is_multicast_mac(mac):
        return
    found = await _asset_by_identity(session, "mac", mac)
    if found is None:
        session.add(AssetIdentity(asset_id=asset.id, kind="mac", value=mac, confidence="high",
                                  source_id=source.id, first_seen=now, last_seen=now))  # fmt: skip
        await session.flush()
        return
    other = await resolve_merged(session, found[0])
    if other.id == asset.id:
        found[1].last_seen = now
        return
    if other.managed:
        add_event(
            session, "identity.conflict",
            f"MAC {mac} is reported by two different agents; assets were left separate.",
            severity="warning", asset_id=asset.id, source_id=source.id, data={"other_asset_id": other.id},
        )  # fmt: skip
        return
    await merge_assets(
        session, source_asset=other, target_asset=asset, user_id=None,
        reason=f"automatic: agent-reported interface MAC {mac} matches a neighbour-cache sighting",
    )  # fmt: skip


class MergeError(ValueError):
    pass


async def merge_assets(
    session: AsyncSession,
    *,
    source_asset: Asset,
    target_asset: Asset,
    user_id: int | None,
    reason: str | None,
) -> AssetMerge:
    if source_asset.id == target_asset.id:
        raise MergeError("cannot merge an asset into itself")
    if source_asset.merged_into_id is not None or target_asset.merged_into_id is not None:
        raise MergeError("one of the assets is already merged; unmerge it first")
    if source_asset.managed:
        raise MergeError(
            "the asset being merged away has a reporting agent; merge the other one into it instead"
        )

    await session.execute(
        update(AssetIdentity)
        .where(AssetIdentity.asset_id == source_asset.id)
        .values(asset_id=target_asset.id, original_asset_id=source_asset.id)
    )
    existing = {
        (o.ip, o.mac, o.source_id, o.kind)
        for o in (
            await session.execute(
                select(AddressObservation).where(AddressObservation.asset_id == target_asset.id)
            )
        ).scalars()
    }
    for obs in (
        await session.execute(
            select(AddressObservation).where(AddressObservation.asset_id == source_asset.id)
        )
    ).scalars():
        if (obs.ip, obs.mac, obs.source_id, obs.kind) in existing:
            continue  # identical sighting already on the target; leave the original in place
        obs.asset_id = target_asset.id
        obs.original_asset_id = source_asset.id
    source_asset.merged_into_id = target_asset.id
    if source_asset.last_seen and (
        target_asset.last_seen is None or source_asset.last_seen > target_asset.last_seen
    ):
        target_asset.last_seen = source_asset.last_seen
    if source_asset.first_seen and (
        target_asset.first_seen is None or source_asset.first_seen < target_asset.first_seen
    ):
        target_asset.first_seen = source_asset.first_seen
    merge = AssetMerge(
        source_asset_id=source_asset.id, target_asset_id=target_asset.id, merged_by=user_id, reason=reason
    )
    session.add(merge)
    await session.flush()
    add_event(session, "device.merged", f"Device #{source_asset.id} merged into #{target_asset.id}",
              asset_id=target_asset.id, data={"merge_id": merge.id, "reason": reason, "source_asset_id": source_asset.id})  # fmt: skip
    add_audit(session, "asset.merge", actor_user_id=user_id, actor_label="user" if user_id else "system",
              target_type="asset", target_id=target_asset.id,
              details={"source_asset_id": source_asset.id, "reason": reason, "merge_id": merge.id})  # fmt: skip
    return merge


async def unmerge(session: AsyncSession, *, merge: AssetMerge, user_id: int | None) -> None:
    if merge.undone_at is not None:
        raise MergeError("this merge was already undone")
    source_asset = await session.get(Asset, merge.source_asset_id)
    if source_asset is None:
        raise MergeError("the original asset no longer exists")
    await session.execute(
        update(AssetIdentity)
        .where(
            AssetIdentity.asset_id == merge.target_asset_id,
            AssetIdentity.original_asset_id == source_asset.id,
        )
        .values(asset_id=source_asset.id, original_asset_id=None)
    )
    await session.execute(
        update(AddressObservation)
        .where(AddressObservation.asset_id == merge.target_asset_id,
               AddressObservation.original_asset_id == source_asset.id)
        .values(asset_id=source_asset.id, original_asset_id=None)
    )  # fmt: skip
    source_asset.merged_into_id = None
    from observenet.core.time import utcnow

    merge.undone_at = utcnow()
    merge.undone_by = user_id
    add_event(session, "device.unmerged", f"Device #{source_asset.id} separated from #{merge.target_asset_id}",
              asset_id=source_asset.id, data={"merge_id": merge.id})  # fmt: skip
    add_audit(session, "asset.unmerge", actor_user_id=user_id, actor_label="user" if user_id else "system",
              target_type="asset", target_id=source_asset.id, details={"merge_id": merge.id})  # fmt: skip
