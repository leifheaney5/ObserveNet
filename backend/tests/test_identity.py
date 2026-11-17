"""Conservative device identity reconciliation (fake neighbour caches)."""

from __future__ import annotations

from datetime import timedelta

import httpx
from sqlalchemy import select

from observenet.core.db import session_scope
from observenet.core.time import utcnow
from observenet.models import AddressObservation, Asset, AssetIdentity, AssetMerge, AuditEvent, Event
from observenet.services import identity
from tests import helpers as h

MAC_A = "3c:22:fb:00:00:0a"  # globally administered
MAC_B = "3c:22:fb:00:00:0b"
MAC_RANDOM = "7a:11:22:33:44:55"  # locally-administered bit set


async def unmanaged() -> list[Asset]:
    return [a for a in await h.all_assets() if not a.managed and a.merged_into_id is None]


async def observations(asset_id: int) -> list[AddressObservation]:
    async with session_scope() as session:
        return list((await session.execute(select(AddressObservation).where(AddressObservation.asset_id == asset_id)
                                           .order_by(AddressObservation.first_seen))).scalars())  # fmt: skip


def test_mac_helpers() -> None:
    assert identity.normalize_mac("AA-BB-CC-00-11-22") == "aa:bb:cc:00:11:22"
    assert identity.normalize_mac("00:00:00:00:00:00") is None
    assert identity.normalize_mac("ff:ff:ff:ff:ff:ff") is None
    assert identity.normalize_mac("not-a-mac") is None and identity.normalize_mac(None) is None
    assert identity.is_locally_administered(MAC_RANDOM) is True
    assert identity.is_locally_administered(MAC_A) is False
    assert identity.is_multicast_mac("01:00:5e:00:00:fb") is True


async def test_same_mac_with_new_ip_keeps_one_asset_dhcp_change() -> None:
    sid = await h.make_source()
    t0 = utcnow() - timedelta(minutes=5)
    await h.process(sid, h.payload(inventory=h.inventory(t0, [("192.168.1.20", MAC_A)], gateway=None)))
    await h.process(
        sid,
        h.payload(inventory=h.inventory(t0 + timedelta(minutes=4), [("192.168.1.33", MAC_A)], gateway=None)),
    )
    assets = await unmanaged()
    assert len(assets) == 1
    obs = await observations(assets[0].id)
    assert [o.ip for o in obs] == ["192.168.1.20", "192.168.1.33"]  # history of addresses kept
    assert assets[0].confidence == "medium" and assets[0].mac_randomized is False
    assert assets[0].last_seen == t0 + timedelta(minutes=4)


async def test_same_ip_with_new_mac_is_a_second_asset() -> None:
    sid = await h.make_source()
    t0 = utcnow() - timedelta(minutes=5)
    await h.process(sid, h.payload(inventory=h.inventory(t0, [("192.168.1.20", MAC_A)], gateway=None)))
    await h.process(
        sid,
        h.payload(inventory=h.inventory(t0 + timedelta(minutes=1), [("192.168.1.20", MAC_B)], gateway=None)),
    )
    assets = await unmanaged()
    assert len(assets) == 2
    # the first device keeps its own last_seen; it was not "updated" by the newcomer
    assert assets[0].last_seen == t0 and assets[1].last_seen == t0 + timedelta(minutes=1)


async def test_locally_administered_mac_is_low_confidence_and_flagged_randomized() -> None:
    sid = await h.make_source()
    await h.process(
        sid, h.payload(inventory=h.inventory(utcnow(), [("192.168.1.55", MAC_RANDOM)], gateway=None))
    )
    (asset,) = await unmanaged()
    assert asset.confidence == "low" and asset.mac_randomized is True
    assert "never merged automatically" in (asset.confidence_reason or "")
    assert asset.vendor is None  # no OUI lookup for a randomized MAC


async def test_randomized_mac_is_never_auto_merged_even_when_an_agent_reports_it() -> None:
    linux = await h.make_source()
    await h.process(
        linux, h.payload(inventory=h.inventory(utcnow(), [("192.168.1.55", MAC_RANDOM)], gateway=None))
    )
    win = await h.make_source(
        agent_uid="win-test-agent-0001", name="win-pc", platform="windows", kind="windows_agent"
    )
    body = h.payload(agent_uid="win-test-agent-0001", hostname="win-pc", platform="windows",
                     inventory=h.inventory(utcnow(), [], own_ip="192.168.1.55", own_mac=MAC_RANDOM, gateway=None,
                                           neighbors_status="unsupported"))  # fmt: skip
    await h.process(win, body)
    async with session_scope() as session:
        assert (await session.execute(select(AssetMerge))).first() is None
    assert len(await unmanaged()) == 1  # the randomized-MAC sighting stays its own entry


async def test_ip_only_sighting_is_low_confidence_and_scoped_to_the_reporting_source() -> None:
    a = await h.make_source()
    b = await h.make_source(agent_uid="lnx-test-agent-0002", name="other-host")
    now = utcnow()
    await h.process(a, h.payload(inventory=h.inventory(now, [("192.168.1.99", None)], gateway=None)))
    await h.process(
        a,
        h.payload(inventory=h.inventory(now + timedelta(seconds=30), [("192.168.1.99", None)], gateway=None)),
    )
    await h.process(b, h.payload(agent_uid="lnx-test-agent-0002", hostname="other-host",
                                 inventory=h.inventory(now, [("192.168.1.99", None)], own_ip="192.168.1.6",
                                                       own_mac="3c:22:fb:aa:bb:02", gateway=None)))  # fmt: skip
    assets = await unmanaged()
    assert len(assets) == 2  # same IP from two vantage points is NOT assumed to be one device
    assert {x.confidence for x in assets} == {"low"}
    async with session_scope() as session:
        values = sorted(
            (
                await session.execute(select(AssetIdentity.value).where(AssetIdentity.kind == "ip_only"))
            ).scalars()
        )
    assert values == sorted([f"{a}:192.168.1.99", f"{b}:192.168.1.99"])


async def test_duplicate_rows_in_one_inventory_do_not_create_duplicate_assets() -> None:
    sid = await h.make_source()
    neighbors = [
        ("192.168.1.20", MAC_A),
        ("192.168.1.20", MAC_A),
        ("192.168.1.21", MAC_A),
        ("192.168.1.30", MAC_B.upper()),
    ]
    await h.process(sid, h.payload(inventory=h.inventory(utcnow(), neighbors, gateway=None)))
    await h.process(sid, h.payload(inventory=h.inventory(utcnow(), neighbors, gateway=None)))  # and again
    assets = await unmanaged()
    assert len(assets) == 2
    async with session_scope() as session:
        macs = sorted(
            (await session.execute(select(AssetIdentity.value).where(AssetIdentity.kind == "mac"))).scalars()
        )
        first_seen_events = (
            (await session.execute(select(Event).where(Event.kind == "device.first_seen"))).scalars().all()
        )
    assert macs == [
        MAC_A,
        MAC_B,
        "3c:22:fb:aa:bb:01",
    ]  # two neighbours + the agent's own NIC, each exactly once
    assert len(first_seen_events) == 2


async def test_multicast_loopback_and_own_addresses_are_not_devices() -> None:
    sid = await h.make_source()
    neighbors = [
        ("224.0.0.251", "01:00:5e:00:00:fb"),
        ("127.0.0.1", MAC_A),
        ("192.168.1.5", MAC_B),
        ("not-an-ip", MAC_B),
    ]
    await h.process(sid, h.payload(inventory=h.inventory(utcnow(), neighbors, gateway=None)))
    assert await unmanaged() == []


async def test_default_gateway_is_typed_router_by_labelled_inference() -> None:
    sid = await h.make_source()
    await h.process(
        sid, h.payload(inventory=h.inventory(utcnow(), [("192.168.1.1", MAC_A)], gateway="192.168.1.1"))
    )
    (router,) = await unmanaged()
    assert router.asset_type == "router"
    assert "by inference" in (router.confidence_reason or "")


async def test_agent_nic_mac_matching_a_neighbour_sighting_is_an_audited_undoable_merge() -> None:
    linux = await h.make_source()
    t0 = utcnow() - timedelta(minutes=2)
    await h.process(linux, h.payload(inventory=h.inventory(t0, [("192.168.1.10", MAC_A)], gateway=None)))
    (sighting,) = await unmanaged()

    win = await h.make_source(
        agent_uid="win-test-agent-0001", name="win-pc", platform="windows", kind="windows_agent"
    )
    await h.process(win, h.payload(agent_uid="win-test-agent-0001", hostname="win-pc", platform="windows",
                                   inventory=h.inventory(utcnow(), [], own_ip="192.168.1.10", own_mac=MAC_A,
                                                         gateway=None, neighbors_status="unsupported")))  # fmt: skip
    agent_asset_id = await h.source_asset_id(win)
    async with session_scope() as session:
        merge = (await session.execute(select(AssetMerge))).scalar_one()
        merged = await session.get(Asset, sighting.id)
        audit = (
            await session.execute(select(AuditEvent).where(AuditEvent.action == "asset.merge"))
        ).scalar_one()
        mac_owner = (
            await session.execute(select(AssetIdentity.asset_id).where(AssetIdentity.value == MAC_A))
        ).scalar_one()
    assert merged is not None and merged.merged_into_id == agent_asset_id
    assert (merge.source_asset_id, merge.target_asset_id, merge.merged_by) == (
        sighting.id,
        agent_asset_id,
        None,
    )
    assert (merge.reason or "").startswith("automatic:")  # not silent: recorded with its reason
    assert audit.actor_label == "system" and audit.details["merge_id"] == merge.id
    assert mac_owner == agent_asset_id

    async with session_scope() as session:  # ...and it can be undone
        await identity.unmerge(session, merge=await session.get(AssetMerge, merge.id), user_id=None)  # type: ignore[arg-type]
    async with session_scope() as session:
        restored = await session.get(Asset, sighting.id)
        mac_owner = (
            await session.execute(select(AssetIdentity.asset_id).where(AssetIdentity.value == MAC_A))
        ).scalar_one()
        merge = await session.get(AssetMerge, merge.id)  # type: ignore[assignment]
    assert restored is not None and restored.merged_into_id is None
    assert mac_owner == sighting.id
    assert merge.undone_at is not None


async def test_two_agents_reporting_the_same_mac_are_left_separate_with_a_conflict_event() -> None:
    a = await h.make_source()
    b = await h.make_source(agent_uid="lnx-test-agent-0002", name="clone")
    await h.process(a, h.payload(inventory=h.inventory(utcnow(), [], own_mac=MAC_A, gateway=None)))
    await h.process(b, h.payload(agent_uid="lnx-test-agent-0002", hostname="clone",
                                 inventory=h.inventory(utcnow(), [], own_ip="192.168.1.6", own_mac=MAC_A, gateway=None)))  # fmt: skip
    async with session_scope() as session:
        assert (await session.execute(select(AssetMerge))).first() is None
        conflict = (
            await session.execute(select(Event).where(Event.kind == "identity.conflict"))
        ).scalar_one()
    assert conflict.severity == "warning"
    assert len([x for x in await h.all_assets() if x.managed]) == 2


async def test_manual_merge_and_unmerge_round_trip_restores_identities(
    admin_client: httpx.AsyncClient,
) -> None:
    sid = await h.make_source()
    now = utcnow()
    await h.process(
        sid,
        h.payload(
            inventory=h.inventory(now, [("192.168.1.20", MAC_A), ("192.168.1.21", MAC_B)], gateway=None)
        ),
    )
    first, second = await unmanaged()

    resp = await admin_client.post(
        f"/api/v1/devices/{second.id}/merge",
        json={"into_id": first.id, "reason": "same laptop, wifi+ethernet"},
    )
    assert resp.status_code == 200, resp.text
    merge_id = resp.json()["merge_id"]

    listing = (await admin_client.get("/api/v1/devices")).json()
    assert second.id not in [d["id"] for d in listing["items"]]  # merged-away entry is hidden
    detail = (await admin_client.get(f"/api/v1/devices/{first.id}")).json()
    assert {i["value"] for i in detail["identities"] if i["kind"] == "mac"} == {MAC_A, MAC_B}
    assert {a["ip"] for a in detail["addresses"]} == {"192.168.1.20", "192.168.1.21"}
    assert (
        detail["merges"][0]["automatic"] is False
        and detail["merges"][0]["reason"] == "same laptop, wifi+ethernet"
    )

    # a later sighting of the merged MAC lands on the surviving asset
    await h.process(
        sid,
        h.payload(
            inventory=h.inventory(now + timedelta(seconds=30), [("192.168.1.21", MAC_B)], gateway=None)
        ),
    )
    assert len(await unmanaged()) == 1

    undo = await admin_client.post(f"/api/v1/devices/merges/{merge_id}/undo")
    assert undo.status_code == 200 and undo.json()["restored_asset_id"] == second.id
    restored = (await admin_client.get(f"/api/v1/devices/{second.id}")).json()
    assert [i["value"] for i in restored["identities"]] == [MAC_B]
    remaining = (await admin_client.get(f"/api/v1/devices/{first.id}")).json()
    assert [i["value"] for i in remaining["identities"] if i["kind"] == "mac"] == [MAC_A]
    assert (
        await admin_client.post(f"/api/v1/devices/merges/{merge_id}/undo")
    ).status_code == 409  # only once

    audit = (await admin_client.get("/api/v1/audit")).json()
    assert {"asset.merge", "asset.unmerge"} <= {a["action"] for a in audit}


async def test_merge_guards(admin_client: httpx.AsyncClient) -> None:
    sid = await h.make_source()
    await h.process(
        sid,
        h.payload(
            inventory=h.inventory(utcnow(), [("192.168.1.20", MAC_A), ("192.168.1.21", MAC_B)], gateway=None)
        ),
    )
    managed_id = await h.source_asset_id(sid)
    first, second = await unmanaged()
    # merging a managed (agent) asset away is refused
    resp = await admin_client.post(f"/api/v1/devices/{managed_id}/merge", json={"into_id": first.id})
    assert resp.status_code == 409 and "reporting agent" in resp.json()["detail"]
    assert (
        await admin_client.post(f"/api/v1/devices/{first.id}/merge", json={"into_id": first.id})
    ).status_code == 409
    assert (
        await admin_client.post(f"/api/v1/devices/{first.id}/merge", json={"into_id": 999999})
    ).status_code == 404
    assert (
        await admin_client.post(f"/api/v1/devices/{second.id}/merge", json={"into_id": first.id})
    ).status_code == 200
    # an already-merged asset cannot be merged again until it is unmerged
    assert (
        await admin_client.post(f"/api/v1/devices/{second.id}/merge", json={"into_id": managed_id})
    ).status_code == 409


async def test_device_presence_never_says_offline(admin_client: httpx.AsyncClient) -> None:
    sid = await h.make_source()
    old = utcnow() - timedelta(hours=3)
    await h.process(
        sid, h.payload(inventory=h.inventory(old, [("192.168.1.20", MAC_A)], gateway=None)), received_at=old
    )
    await h.process(sid, h.payload(inventory=h.inventory(utcnow(), [("192.168.1.21", MAC_B)], gateway=None)))
    page = (await admin_client.get("/api/v1/devices")).json()
    by_ip = {d["primary_ip"]: d for d in page["items"]}
    assert by_ip["192.168.1.21"]["presence"]["state"] == "seen_recently"
    quiet = by_ip["192.168.1.20"]["presence"]
    assert quiet["state"] == "not_seen_recently" and "not proof it is offline" in quiet["basis"]
    assert by_ip["192.168.1.5"]["presence"]["state"] == "reporting"  # the agent host itself
    assert "offline" not in {d["presence"]["label"].lower() for d in page["items"]}
    assert "not guaranteed complete" in page["coverage_note"]
    assert by_ip["192.168.1.20"]["last_seen_source"] == "test-host"
