# ADR 0003 — REST for commands and time series, GraphQL for inventory reads

Status: accepted

## Context

The role this project prepares for values both REST and GraphQL. Adding GraphQL only to
tick a box would be dishonest; it has to earn its place.

## Decision

- **REST (`/api/v1`)** for: health, authentication, agent ingestion and config, metric
  history and current values, commands (acknowledge, snooze, verify, merge, settings),
  exports and the WebSocket stream. These are command-shaped or stream-shaped, benefit
  from per-endpoint CSRF/audit handling, and have fixed response shapes that map
  directly to generated TypeScript types.
- **GraphQL (`/graphql`, Strawberry)** read-only, for inventory:
  `devices(filter, after, first)`, `device(id)`, `findings(filter, after, first)`,
  `assessments(assetId)`, `networkSummary`. A device fans out to identities, address
  sightings, interfaces, services, findings and relationships; screens need different
  subsets. Per-request dataloaders turn each child collection into one batched query.
- Keyset pagination: devices by `(coalesce(last_seen, epoch), id)` descending, findings
  by `id` descending; cursors are opaque base64.
- No GraphQL mutations. One way to change state (REST) keeps audit and CSRF handling in
  one place.

## Safety measures

Session required (HTTP 401 before execution), CSRF header on POST, GET queries
disabled, depth limit, alias and token limits, page size ≤ 100, introspection and
GraphiQL off in production, admin-only field `Device.auditTrail`.

## Consequences

- Two API styles to document, secure and test.
- Each loader opens its own short-lived database session, because sibling resolvers run
  concurrently and one `AsyncSession` cannot. This costs extra pool checkouts per query.
- Some reads exist in both styles (device list, findings). REST versions back simple
  tables and exports; GraphQL backs detail/graph views.
