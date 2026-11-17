"""Optional Neo4j projection of the observed topology.

PostgreSQL stays the source of truth. When ``OBSERVENET_NEO4J_URI`` is set, a periodic
worker task projects the same documented relationships the REST/GraphQL API serves
(asset ↔ interface ↔ subnet ↔ service) into Neo4j so graph-shaped questions can be asked
in Cypher. The projection is idempotent (MERGE + a sync marker that removes anything no
longer observed) and contains no inferred edges and no attack paths.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from observenet.core.config import get_config
from observenet.models import AddressObservation, Asset
from observenet.services.views import build_topology

LABELS = {"asset": "Asset", "segment": "Segment", "interface": "Interface", "service": "Service"}
REL_KINDS = ("HAS_INTERFACE", "ATTACHED_TO", "SEEN_ON", "LISTENS_ON")


class GraphUnavailable(RuntimeError):
    pass


def _driver() -> Any:
    cfg = get_config()
    if not cfg.neo4j_uri:
        raise GraphUnavailable("Neo4j is not configured (OBSERVENET_NEO4J_URI is empty)")
    try:
        from neo4j import AsyncGraphDatabase
    except ImportError as exc:  # the driver is an optional extra
        raise GraphUnavailable("the 'graph' extra (neo4j driver) is not installed") from exc
    return AsyncGraphDatabase.driver(
        cfg.neo4j_uri, auth=(cfg.neo4j_user, cfg.neo4j_password), connection_timeout=5
    )


async def sync_topology_to_neo4j(session: AsyncSession) -> dict[str, int]:
    topo = await build_topology(session)
    sync_id = uuid.uuid4().hex
    by_label: dict[str, list[dict[str, Any]]] = {label: [] for label in LABELS.values()}
    for node in topo["nodes"]:
        meta = {k: v for k, v in node["meta"].items() if isinstance(v, str | int | float | bool)}
        by_label[LABELS[node["type"]]].append({"uid": node["id"], "label": node["label"], "props": meta})
    by_kind: dict[str, list[dict[str, Any]]] = {kind: [] for kind in REL_KINDS}
    for edge in topo["edges"]:
        if edge["kind"] in by_kind:
            by_kind[edge["kind"]].append(
                {"source": edge["source"], "target": edge["target"], "evidence": edge["evidence"]}
            )

    driver = _driver()
    try:
        async with driver.session() as neo:
            await neo.run(
                "CREATE CONSTRAINT observed_uid IF NOT EXISTS FOR (n:Observed) REQUIRE n.uid IS UNIQUE"
            )
            for label, rows in by_label.items():
                # Labels and relationship types cannot be parameters; both come from the fixed
                # whitelists above, never from observed data.
                await neo.run(
                    f"UNWIND $rows AS row MERGE (n:Observed {{uid: row.uid}}) SET n:{label}, n.label = row.label, "
                    "n += row.props, n.sync_id = $sync",
                    rows=rows, sync=sync_id,
                )  # fmt: skip
            for kind, rows in by_kind.items():
                await neo.run(
                    "UNWIND $rows AS row MATCH (a:Observed {uid: row.source}), (b:Observed {uid: row.target}) "
                    f"MERGE (a)-[r:{kind}]->(b) SET r.evidence = row.evidence, r.sync_id = $sync",
                    rows=rows, sync=sync_id,
                )  # fmt: skip
            await neo.run("MATCH ()-[r]->() WHERE r.sync_id <> $sync DELETE r", sync=sync_id)
            await neo.run("MATCH (n:Observed) WHERE n.sync_id <> $sync DETACH DELETE n", sync=sync_id)
    finally:
        await driver.close()
    return {"nodes": len(topo["nodes"]), "edges": sum(len(v) for v in by_kind.values())}


_PEERS_CYPHER = """
MATCH (a:Asset {uid: $uid})-[:SEEN_ON|HAS_INTERFACE|ATTACHED_TO*1..2]->(s:Segment)
MATCH (b:Asset)-[:SEEN_ON|HAS_INTERFACE|ATTACHED_TO*1..2]->(s)
WHERE b.uid <> a.uid
RETURN b.uid AS uid, b.label AS label, collect(DISTINCT s.label) AS segments
ORDER BY label LIMIT 200
"""


async def segment_peers(session: AsyncSession, asset_id: int) -> dict[str, Any]:
    """Devices observed on the same subnet(s) as ``asset_id``.

    Uses Neo4j when configured, PostgreSQL otherwise — the answer is the same; the field
    ``backend`` says which engine produced it. Sharing a subnet is an observation about
    addressing and implies nothing about reachability between the devices.
    """
    if get_config().neo4j_uri:
        driver = _driver()
        try:
            async with driver.session() as neo:
                result = await neo.run(_PEERS_CYPHER, uid=f"asset:{asset_id}")
                rows = [dict(record) async for record in result]
        finally:
            await driver.close()
        peers = [
            {
                "asset_id": int(r["uid"].split(":", 1)[1]),
                "name": r["label"],
                "segments": sorted(r["segments"]),
            }
            for r in rows
        ]
        return {"backend": "neo4j", "asset_id": asset_id, "peers": peers}

    from observenet.models import NetworkSegment

    own = select(AddressObservation.segment_id).where(
        AddressObservation.asset_id == asset_id, AddressObservation.segment_id.is_not(None)
    )
    sql_rows = (
        await session.execute(
            select(Asset.id, Asset.display_name, Asset.hostname, NetworkSegment.cidr)
            .join(AddressObservation, AddressObservation.asset_id == Asset.id)
            .join(NetworkSegment, NetworkSegment.id == AddressObservation.segment_id)
            .where(
                AddressObservation.segment_id.in_(own), Asset.id != asset_id, Asset.merged_into_id.is_(None)
            )
            .distinct()
        )
    ).all()
    grouped: dict[int, dict[str, Any]] = {}
    for aid, display, host, cidr in sql_rows:
        entry = grouped.setdefault(
            aid, {"asset_id": aid, "name": display or host or f"Device #{aid}", "segments": set()}
        )
        entry["segments"].add(cidr)
    peers = sorted(
        ({**p, "segments": sorted(p["segments"])} for p in grouped.values()), key=lambda p: p["name"]
    )
    return {"backend": "postgresql", "asset_id": asset_id, "peers": peers}
