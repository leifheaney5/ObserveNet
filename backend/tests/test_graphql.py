"""GraphQL: authorization, pagination, relationships, limits and batched loading."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import event

from observenet.core.db import get_engine, session_scope
from observenet.core.time import utcnow
from observenet.models import Asset
from tests import helpers as h
from tests.conftest import ADMIN_PASSWORD, login


async def gql(
    client: httpx.AsyncClient, query: str, variables: dict[str, Any] | None = None
) -> dict[str, Any]:
    resp = await client.post("/graphql", json={"query": query, "variables": variables or {}})
    assert resp.status_code == 200, resp.text
    return resp.json()


async def seed_network(n_neighbors: int = 7) -> int:
    """One agent host with interfaces, services and a finding + N passively seen neighbours."""
    sid = await h.make_source()
    now = utcnow()
    for i in range(n_neighbors):  # one inventory per neighbour so each has a distinct last_seen
        seen = now - timedelta(minutes=10 * (n_neighbors - i))
        await h.process(sid, h.payload(inventory=h.inventory(seen, [(f"192.168.1.{20 + i}", f"3c:22:fb:00:00:{i:02x}")])),
                        received_at=seen)  # fmt: skip
    await h.process(
        sid,
        h.payload(
            posture=h.posture(
                now, listening=h.listening(("tcp", "0.0.0.0", 6379), ("tcp", "127.0.0.1", 5432))
            )
        ),
    )
    return sid


# ---------------------------------------------------------------- query-level authorization


async def test_unauthenticated_graphql_is_401_not_errors_as_data(client: httpx.AsyncClient) -> None:
    resp = await client.post("/graphql", json={"query": "{ networkSummary { deviceCount } }"})
    assert resp.status_code == 401
    assert "data" not in resp.text


async def test_graphql_requires_csrf_header(admin_client: httpx.AsyncClient) -> None:
    del admin_client.headers["x-csrf-token"]
    resp = await admin_client.post("/graphql", json={"query": "{ networkSummary { deviceCount } }"})
    assert resp.status_code == 403


async def test_queries_via_get_and_graphiql_are_disabled(admin_client: httpx.AsyncClient) -> None:
    by_get = await admin_client.get("/graphql", params={"query": "{ networkSummary { deviceCount } }"})
    assert by_get.status_code != 200
    ide = await admin_client.get("/graphql", headers={"Accept": "text/html"})
    assert ide.status_code != 200 and "graphiql" not in ide.text.lower()


async def test_schema_is_read_only(admin_client: httpx.AsyncClient) -> None:
    body = await gql(admin_client, "mutation { deleteEverything }")
    assert body.get("data") is None and body["errors"]


# ---------------------------------------------------------------- queries


async def test_devices_keyset_pagination_has_no_duplicates_or_skips(admin_client: httpx.AsyncClient) -> None:
    await seed_network(7)
    async with session_scope() as session:  # plus one never-seen device (NULL last_seen sorts last)
        session.add(Asset(display_name="never-seen", confidence="low"))
    query = """query($after: String) { devices(first: 3, after: $after) {
        totalCount pageInfo { hasNextPage endCursor } edges { cursor node { id name lastSeen } } } }"""
    seen: list[tuple[int, str | None]] = []
    after = None
    pages = 0
    while True:
        page = (await gql(admin_client, query, {"after": after}))["data"]["devices"]
        assert page["totalCount"] == 9  # host + 7 neighbours + never-seen
        assert len(page["edges"]) <= 3
        seen += [(e["node"]["id"], e["node"]["lastSeen"]) for e in page["edges"]]
        pages += 1
        if not page["pageInfo"]["hasNextPage"]:
            break
        after = page["pageInfo"]["endCursor"]
        assert after == page["edges"][-1]["cursor"]
    assert pages == 3
    ids = [i for i, _ in seen]
    assert len(ids) == len(set(ids)) == 9  # nothing repeated, nothing skipped
    stamps = [s for _, s in seen if s is not None]
    assert stamps == sorted(stamps, reverse=True)  # newest sighting first
    assert seen[-1][1] is None  # the never-seen device comes last


async def test_devices_pagination_is_stable_when_last_seen_ties(admin_client: httpx.AsyncClient) -> None:
    same = utcnow() - timedelta(minutes=1)
    async with session_scope() as session:
        for i in range(5):
            session.add(Asset(display_name=f"tie-{i}", confidence="low", last_seen=same, first_seen=same))
    query = "query($after: String) { devices(first: 2, after: $after) { pageInfo { hasNextPage endCursor } edges { node { id } } } }"
    ids: list[int] = []
    after = None
    for _ in range(5):
        page = (await gql(admin_client, query, {"after": after}))["data"]["devices"]
        ids += [e["node"]["id"] for e in page["edges"]]
        after = page["pageInfo"]["endCursor"]
        if not page["pageInfo"]["hasNextPage"]:
            break
    assert sorted(ids, reverse=True) == ids and len(set(ids)) == 5


async def test_devices_filter(admin_client: httpx.AsyncClient) -> None:
    await seed_network(3)
    q = "query($f: DeviceFilter) { devices(filter: $f) { totalCount edges { node { name managed assetType } } } }"
    managed = (await gql(admin_client, q, {"f": {"managed": True}}))["data"]["devices"]
    assert managed["totalCount"] == 1 and managed["edges"][0]["node"]["name"] == "test-host"
    routers = (await gql(admin_client, q, {"f": {"assetType": "router"}}))["data"]["devices"]
    assert routers["totalCount"] == 0  # 192.168.1.1 was never in the neighbour list
    search = (await gql(admin_client, q, {"f": {"search": "TEST-HO"}}))["data"]["devices"]
    assert search["totalCount"] == 1


async def test_device_detail_with_observed_relationships(admin_client: httpx.AsyncClient) -> None:
    sid = await seed_network(1)
    host_id = await h.source_asset_id(sid)
    query = """query($id: Int!) { device(id: $id) {
        id name managed confidence presence { state label basis }
        identities { kind value confidence }
        addresses { ip mac evidence source }
        interfaces { name mac isUp linkSpeedMbps addresses }
        services { proto address port origin }
        findings { ruleId severity classification status device { id } }
        relationships { kind targetType targetLabel evidence }
    } }"""
    device = (await gql(admin_client, query, {"id": host_id}))["data"]["device"]
    assert device["managed"] is True and device["confidence"] == "high"
    assert device["presence"]["state"] == "reporting"
    assert {"kind": "agent_uid", "value": h.AGENT_UID, "confidence": "high"} in device["identities"]
    assert device["addresses"][0] == {
        "ip": "192.168.1.5",
        "mac": "3c:22:fb:aa:bb:01",
        "evidence": "agent_self",
        "source": "test-host",
    }
    assert device["interfaces"] == [
        {
            "name": "eth0",
            "mac": "3c:22:fb:aa:bb:01",
            "isUp": True,
            "linkSpeedMbps": 1000,
            "addresses": ["192.168.1.5"],
        }
    ]
    assert {(s["address"], s["port"]) for s in device["services"]} == {("0.0.0.0", 6379), ("127.0.0.1", 5432)}
    (finding,) = device["findings"]
    assert finding["ruleId"] == "LNX-LISTEN-001" and finding["classification"] == "POTENTIAL_EXPOSURE"
    assert finding["device"]["id"] == host_id
    rels = {(r["kind"], r["targetType"], r["targetLabel"]) for r in device["relationships"]}
    assert ("HAS_INTERFACE", "interface", "eth0") in rels
    assert ("SEEN_ON", "segment", "192.168.1.0/24") in rels
    assert ("LISTENS_ON", "service", "tcp/6379") in rels
    assert all(r["evidence"] for r in device["relationships"])  # every edge says what it is based on
    assert not any(
        "attack" in r["kind"].lower() or "reach" in r["kind"].lower() for r in device["relationships"]
    )

    assert (await gql(admin_client, "{ device(id: 999999) { id } }"))["data"]["device"] is None


async def test_findings_filter_and_pagination(admin_client: httpx.AsyncClient) -> None:
    sid = await h.make_source()
    now = utcnow()
    sockets = [("tcp", "0.0.0.0", p) for p in (6379, 8080, 8081, 9000)]
    await h.process(sid, h.payload(posture=h.posture(h.ago(60, now), listening=h.listening(*sockets))))
    await h.process(sid, h.payload(posture=h.posture(now, listening=h.listening(*sockets[:3]))))  # 9000 fixed
    q = """query($f: FindingFilter, $after: String, $first: Int!) { findings(filter: $f, after: $after, first: $first) {
        totalCount pageInfo { hasNextPage endCursor } edges { node { id title severity status classification } } } }"""
    active = (await gql(admin_client, q, {"first": 10}))["data"]["findings"]
    assert active["totalCount"] == 3 and {e["node"]["status"] for e in active["edges"]} == {
        "open"
    }  # default = active only
    everything = (await gql(admin_client, q, {"f": {"status": "all"}, "first": 10}))["data"]["findings"]
    assert everything["totalCount"] == 4
    resolved = (await gql(admin_client, q, {"f": {"status": "resolved"}, "first": 10}))["data"]["findings"]
    assert [e["node"]["classification"] for e in resolved["edges"]] == ["VERIFIED_RESOLUTION"]
    medium = (await gql(admin_client, q, {"f": {"severity": "medium"}, "first": 10}))["data"]["findings"]
    assert medium["totalCount"] == 1 and "6379" in medium["edges"][0]["node"]["title"]
    assert (await gql(admin_client, q, {"f": {"assetId": 424242}, "first": 10}))["data"]["findings"][
        "totalCount"
    ] == 0
    assert (await gql(admin_client, q, {"f": {"category": "observability"}, "first": 10}))["data"][
        "findings"
    ]["totalCount"] == 0

    first = (await gql(admin_client, q, {"f": {"status": "all"}, "first": 3}))["data"]["findings"]
    second = (
        await gql(
            admin_client, q, {"f": {"status": "all"}, "first": 3, "after": first["pageInfo"]["endCursor"]}
        )
    )["data"]["findings"]
    ids = [e["node"]["id"] for e in first["edges"] + second["edges"]]
    assert first["pageInfo"]["hasNextPage"] is True and second["pageInfo"]["hasNextPage"] is False
    assert ids == sorted(ids, reverse=True) and len(set(ids)) == 4


async def test_assessment_history_for_an_asset(admin_client: httpx.AsyncClient) -> None:
    sid = await h.make_source()
    now = utcnow()
    await h.process(
        sid, h.payload(posture=h.posture(h.ago(60, now), listening=h.listening(("tcp", "0.0.0.0", 6379))))
    )
    await h.process(sid, h.payload(posture=h.posture(now, listening=h.listening())))
    asset_id = await h.source_asset_id(sid)
    q = "query($id: Int!) { assessments(assetId: $id) { id trigger engineVersion startedAt results { ruleId ruleVersion status summary } } }"
    runs = (await gql(admin_client, q, {"id": asset_id}))["data"]["assessments"]
    assert len(runs) == 2 and runs[0]["startedAt"] >= runs[1]["startedAt"]  # newest first
    newest = {r["ruleId"]: r["status"] for r in runs[0]["results"]}
    oldest = {r["ruleId"]: r["status"] for r in runs[1]["results"]}
    assert newest == {"LNX-LISTEN-001": "pass", "DOCKER-PORT-001": "unknown"}
    assert oldest["LNX-LISTEN-001"] == "fail"
    assert (await gql(admin_client, q, {"id": 999999}))["data"]["assessments"] == []


async def test_network_summary(admin_client: httpx.AsyncClient) -> None:
    await seed_network(2)
    q = """{ networkSummary { demoMode deviceCount managedDeviceCount activeAlerts internetStatus internetStatusReason
        inventoryCoverage openFindings { severity count } sources { name kind freshness lastSeenAt } } }"""
    summary = (await gql(admin_client, q))["data"]["networkSummary"]
    assert summary["demoMode"] is False
    assert (summary["deviceCount"], summary["managedDeviceCount"]) == (3, 1)
    assert summary["openFindings"] == [{"severity": "medium", "count": 1}]
    assert summary["internetStatus"] == "unknown" and summary["internetStatusReason"]
    assert summary["sources"] == [
        {
            "name": "test-host",
            "kind": "linux_agent",
            "freshness": "live",
            "lastSeenAt": summary["sources"][0]["lastSeenAt"],
        }
    ]
    assert "partial" in summary["inventoryCoverage"]


# ---------------------------------------------------------------- object/field-level authorization


async def test_audit_trail_field_is_admin_only(
    admin_client: httpx.AsyncClient, viewer_client: httpx.AsyncClient
) -> None:
    sid = await seed_network(1)
    host_id = await h.source_asset_id(sid)
    assert (
        await admin_client.patch(f"/api/v1/devices/{host_id}", json={"display_name": "renamed"})
    ).status_code == 200
    q = "query($id: Int!) { device(id: $id) { name auditTrail { actor action ts } } }"
    as_admin = await gql(admin_client, q, {"id": host_id})
    assert as_admin["data"]["device"]["auditTrail"] == [
        {"actor": "admin", "action": "asset.update", "ts": as_admin["data"]["device"]["auditTrail"][0]["ts"]}
    ]
    as_viewer = await gql(viewer_client, q, {"id": host_id})
    assert "administrator role required" in as_viewer["errors"][0]["message"]
    assert (
        as_viewer["data"] is None or as_viewer["data"]["device"] is None
    )  # nothing leaks alongside the error
    # the same viewer may read everything else about the device
    ok = await gql(
        viewer_client, "query($id: Int!) { device(id: $id) { name findings { ruleId } } }", {"id": host_id}
    )
    assert "errors" not in ok and ok["data"]["device"]["name"] == "renamed"


# ---------------------------------------------------------------- abuse limits


async def test_depth_limit_rejects_an_over_deep_query(admin_client: httpx.AsyncClient) -> None:
    await seed_network(1)
    deep = "{ devices { edges { node { findings { device { findings { device { findings { device { findings { device { id } } } } } } } } } } } }"
    body = await gql(admin_client, deep)
    assert body.get("data") is None
    assert "depth" in body["errors"][0]["message"].lower()
    shallow = await gql(admin_client, "{ devices { edges { node { findings { device { id } } } } } }")
    assert "errors" not in shallow


@pytest.mark.parametrize("first", [0, -1, 101, 100000])
async def test_page_size_is_capped(admin_client: httpx.AsyncClient, first: int) -> None:
    for field in ("devices", "findings"):
        body = await gql(admin_client, f"{{ {field}(first: {first}) {{ totalCount }} }}")
        assert "between 1 and 100" in body["errors"][0]["message"], field
    body = await gql(admin_client, f"{{ assessments(assetId: 1, first: {first}) {{ id }} }}")
    assert "between 1 and 100" in body["errors"][0]["message"]


async def test_invalid_cursor_is_rejected(admin_client: httpx.AsyncClient) -> None:
    for cursor in ("not-base64!!", "aGVsbG8=", ""):
        body = await gql(
            admin_client, "query($c: String) { devices(after: $c) { totalCount } }", {"c": cursor or "%%%"}
        )
        assert body["errors"], cursor


async def test_alias_flood_is_rejected(admin_client: httpx.AsyncClient) -> None:
    aliases = " ".join(f"a{i}: networkSummary {{ deviceCount }}" for i in range(20))
    body = await gql(admin_client, f"{{ {aliases} }}")
    assert body.get("data") is None and "alias" in body["errors"][0]["message"].lower()


async def test_introspection_is_enabled_only_outside_hardened_deployments(
    admin_client: httpx.AsyncClient, set_config: Any
) -> None:
    from observenet.api.app import create_app
    from observenet.core.config import get_config

    introspect = "{ __schema { queryType { name } } }"
    assert (await gql(admin_client, introspect))["data"]["__schema"]["queryType"][
        "name"
    ] == "Query"  # test env: on

    set_config(env="production")
    assert get_config().introspection_enabled is False  # production default: off
    set_config(env="test", graphql_introspection="false")
    hardened = create_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=hardened), base_url="http://testserver"
    ) as c:
        assert (await login(c, "admin", ADMIN_PASSWORD)).status_code == 200
        body = await gql(c, introspect)
        assert body.get("data") is None and body["errors"]
        ordinary = await gql(c, "{ networkSummary { deviceCount } }")  # normal queries still work
        assert "errors" not in ordinary


# ---------------------------------------------------------------- batching (no N+1)


class StatementCounter:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def __call__(self, _conn: Any, _cursor: Any, statement: str, *_: Any) -> None:
        self.statements.append(statement)

    def __enter__(self) -> StatementCounter:
        event.listen(get_engine().sync_engine, "before_cursor_execute", self)
        return self

    def __exit__(self, *_: Any) -> None:
        event.remove(get_engine().sync_engine, "before_cursor_execute", self)


CHILDREN_QUERY = """{ devices(first: 100) { edges { node {
    id presence { state } identities { kind } addresses { ip source } interfaces { name } services { port }
    findings { ruleId device { id } } relationships { kind targetLabel }
} } } }"""


async def count_statements(client: httpx.AsyncClient) -> tuple[int, int]:
    with StatementCounter() as counter:
        body = await gql(client, CHILDREN_QUERY)
    assert "errors" not in body
    return len(counter.statements), len(body["data"]["devices"]["edges"])


async def test_child_collections_are_batched_so_statements_do_not_grow_with_devices(
    admin_client: httpx.AsyncClient,
) -> None:
    sid = await h.make_source()
    now = utcnow()
    await h.process(sid, h.payload(inventory=h.inventory(now, [(f"192.168.1.{20 + i}", f"3c:22:fb:00:01:{i:02x}") for i in range(3)]),
                                   posture=h.posture(now, listening=h.listening(("tcp", "0.0.0.0", 6379)))))  # fmt: skip
    small_statements, small_devices = await count_statements(admin_client)

    more = [(f"192.168.1.{60 + i}", f"3c:22:fb:00:02:{i:02x}") for i in range(30)]
    await h.process(sid, h.payload(inventory=h.inventory(now + timedelta(seconds=1), more)))
    big_statements, big_devices = await count_statements(admin_client)

    assert (small_devices, big_devices) == (4, 34)
    # (the first request may add one throttled "session last seen" UPDATE, so compare with <=)
    assert big_statements <= small_statements, (
        f"{small_statements} statements for {small_devices} devices but {big_statements} for {big_devices}: N+1 resolver"
    )
    assert small_statements <= 16  # session lookup + page + count + one batched query per child collection
