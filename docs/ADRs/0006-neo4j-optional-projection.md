# ADR 0006 — Neo4j is an optional projection; PostgreSQL is the source of truth for relationships

- Status: Accepted (adapter implemented; optional)
- Date: 2026-11-17

## Context

The brief lists Neo4j / graph modelling as a preferred area, asks that graph analysis
start with PostgreSQL relationships, that Neo4j never be required to run the MVP, and
that no attack paths be invented.

The relationships ObserveNet knows are few and factual: asset ↔ interface, interface ↔
subnet, asset seen on subnet, asset listens on service. They fit comfortably in
relational tables (`interfaces`, `network_segments`, `address_observations`,
`observed_services`) and are served from PostgreSQL by `GET /api/v1/topology` and the
GraphQL `Device.relationships` field.

## Decision

- PostgreSQL is the only source of truth. Nothing is ever read from Neo4j that cannot be
  rebuilt from PostgreSQL.
- Neo4j is an optional **projection**, enabled with the `graph` Compose profile and
  `OBSERVENET_NEO4J_URI=bolt://neo4j:7687`. With the variable empty (the default) no
  Neo4j code runs and the `neo4j` driver is not imported.
- `backend/observenet/services/graph_sync.py` implements it:
  - `sync_topology_to_neo4j` projects exactly the nodes/edges `build_topology` serves.
    It is idempotent: `MERGE` on a unique `uid`, plus a per-run `sync_id` that deletes
    nodes and relationships no longer observed. Labels (`Asset`, `Segment`, `Interface`,
    `Service`) and relationship types (`HAS_INTERFACE`, `ATTACHED_TO`, `SEEN_ON`,
    `LISTENS_ON`) come from fixed whitelists, never from observed data.
  - `segment_peers` answers "which devices were observed on the same subnet(s)?" with
    Cypher when Neo4j is configured and with SQL otherwise. The response's `backend`
    field says which engine answered (`GET /api/v1/topology/peers/{asset_id}`).
- The worker's `sync_graph` task runs every 5 minutes and returns immediately when Neo4j
  is not configured.
- No inferred edges, no reachability, no attack paths — in either store.

## Verification

Run once against a real `neo4j:5-community` container during the build: two consecutive
syncs of the live development topology (idempotent), node/relationship counts matched
the PostgreSQL topology, and `segment_peers` returned identical peers from Neo4j and
from PostgreSQL for every asset. There is **no automated test** for the adapter in CI
(it would need a Neo4j service container); that is listed in `LIMITATIONS.md`.

## Consequences

- The MVP has one fewer moving part by default; the graph store is there for graph-shaped
  questions when someone wants it.
- Two stores can drift for up to one sync interval; consumers that need exactness must
  use the PostgreSQL-backed endpoints.
- The projection is deliberately small. It becomes worthwhile only when a gateway
  provider adds richer relationships (leases, Wi-Fi associations, flows).
