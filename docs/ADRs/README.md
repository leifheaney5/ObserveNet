# Architecture decision records

| # | Decision | Status |
|---|---|---|
| [0001](0001-postgresql-partitioned-timeseries.md) | PostgreSQL with partitioned raw samples and rollups instead of a TSDB | Accepted |
| [0002](0002-dramatiq-redis-async-actors.md) | Dramatiq on Redis with async actors, separate scheduler | Accepted |
| [0003](0003-rest-and-graphql-split.md) | REST for commands/ingest/time series, GraphQL for inventory reads | Accepted |
| [0004](0004-probes-on-agent-scope-validated.md) | Probes run on the agent; targets validated server-side and again on the agent | Accepted |
| [0005](0005-sqlalchemy-pin-and-psycopg3.md) | SQLAlchemy pinned `<2.1`; psycopg 3 for API and workers | Accepted (substitution) |
| [0006](0006-neo4j-optional-projection.md) | Neo4j as an optional projection; PostgreSQL is the source of truth | Accepted |
| [0007](0007-demo-and-real-separation.md) | Demo and real data never mix | Accepted |
| [0008](0008-schema-additions.md) | `metric_series` added; `checks` is the rule catalog with `check_results` per assessment | Accepted |
