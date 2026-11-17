# Optional future hosting on AWS

> Nothing here was provisioned. No AWS resources were created, no charges incurred, and
> **no infrastructure-as-code exists or was tested** in this repository. This is a
> design note for a possible future deployment; `linux-leif` remains the intended home
> of the stack.

## What could move, and what cannot

| Part | Can run in AWS? | Why |
|---|---|---|
| API, worker, scheduler, web | Yes | Stateless containers |
| PostgreSQL, Redis | Yes (managed) | |
| **Collector agents** | **No** | They must sit inside the home network to read NIC counters, the neighbour cache and to probe the gateway. In an AWS deployment they become remote agents posting over HTTPS with their bearer tokens. |

So AWS hosting moves the dashboard and storage off-premises; it does not remove the
need for something running at home, and it makes the home network's inventory leave the
house.

## Sketch

```
home LAN                                  AWS (one region, one VPC)
┌────────────────┐   HTTPS + agent token  ┌───────────────────────────────────────────┐
│ Linux agent    │ ─────────────────────▶ │ ALB (ACM certificate, HTTPS only)         │
│ Windows agent  │                        │   ├─ target: web (nginx + SPA)   ┐        │
└────────────────┘                        │   │                              │ ECS    │
browser ── HTTPS ───────────────────────▶ │   └─ /api, /graphql → api        │ Fargate│
                                          │        worker, scheduler (no LB) ┘        │
                                          │ RDS PostgreSQL 16 (private subnets)       │
                                          │ ElastiCache Redis / Valkey (private)      │
                                          │ Secrets Manager · CloudWatch Logs         │
                                          └───────────────────────────────────────────┘
```

- **ECS on Fargate:** four services from the same two images (`web`; `api` / `worker` /
  `scheduler` sharing the backend image with different commands). `migrate` runs as a
  one-off task before each deploy. Exactly one `scheduler` task.
- **RDS for PostgreSQL 16**, single-AZ to start, automated backups, storage encryption,
  no public accessibility. The app only needs standard PostgreSQL (declarative
  partitioning, advisory locks); no extensions.
- **ElastiCache** (Redis OSS or Valkey), one small node, in-transit encryption if the
  client URL is switched to `rediss://`.
- **ALB + ACM** for TLS; listener rules route `/api/*` and `/graphql` to `api`,
  everything else to `web`. WebSockets work through an ALB; the default 60 s idle
  timeout is sufficient because the stream sends a frame every second.
- **Secrets Manager** for `OBSERVENET_SECRET_KEY`, the database password and the admin
  bootstrap password, injected as ECS secrets.
- **Edge authentication:** the ALB's built-in OIDC/Cognito authentication action can
  play the role Cloudflare Access plays in the home design, but the agent ingest paths
  (`/api/v1/ingest/agent`, `/api/v1/agent/config`) must be excluded from it and are
  then protected only by TLS, the collector token, rate limiting and optionally AWS WAF
  IP rules for the home address.
- **Metrics:** the per-process Prometheus listeners (port 9464) stay unexposed; scrape
  them with a sidecar/ADOT collector, or rely on CloudWatch logs and OTLP export.

## Rough monthly cost (estimates only)

These are order-of-magnitude figures from memory of on-demand pricing in a US region.
**Re-check every line against the current AWS pricing pages and the pricing calculator
before acting**; prices change and free-tier eligibility varies.

| Item | Assumption | Estimate (USD/month) |
|---|---|---|
| Fargate | 4 small tasks (0.25 vCPU / 0.5 GB each), always on | 30 – 45 |
| RDS PostgreSQL | `db.t4g.micro`, single-AZ, 20 GB gp3 | 15 – 25 |
| ElastiCache | `cache.t4g.micro`, single node | 12 – 18 |
| Application Load Balancer | one ALB, light traffic | 18 – 25 |
| NAT gateway | only if tasks sit in private subnets without VPC endpoints | 32+ (often the largest surprise) |
| Secrets Manager, CloudWatch Logs, data transfer | small | 3 – 10 |
| **Total** | without / with NAT gateway | **≈ 80 – 120 / ≈ 115 – 160** |

For a single home network this is far more than the electricity `linux-leif` uses,
which is the main reason AWS is optional. Cheaper variants (one small EC2 instance
running the same Compose file; Lightsail) give up the managed services.

## Security implications

- **Data leaves the home.** Inventory, MAC addresses, presence history and posture
  findings would be stored with a cloud provider. Enable RDS and ElastiCache encryption
  at rest, restrict IAM, and consider whether that trade is wanted at all.
- **Public ingest endpoint.** At home the bundled collector talks to the API over
  loopback. In AWS the ingest endpoint is on the internet; the collector token becomes
  the control that matters. Use short expiries, rotate, restrict by source IP where the
  home address is stable.
- **No inbound to the home network** is still true: agents only make outbound HTTPS
  requests.
- **Trusted hosts and cookies:** set `OBSERVENET_PUBLIC_URL` and
  `OBSERVENET_ALLOWED_HOSTS` to the ALB hostname; cookies become `Secure`.
- **Forwarded headers:** the API must trust `X-Forwarded-For` only from the ALB, or the
  per-IP login rate limit keys on the load balancer's address.
- **Least privilege:** separate task roles; only `api`/`worker`/`scheduler` may read the
  database secret; security groups allow PostgreSQL/Redis only from those tasks.
- **Blast radius of AWS credentials:** whoever controls the AWS account controls the
  data; protect it with MFA and avoid long-lived access keys.

## If this is ever built

Write the IaC (OpenTofu/Terraform) in this directory, run `plan` against a sandbox
account, apply, run the same verification checklist as `docs/DOMAIN_AND_AUTH.md`
(unauthenticated 401s, TLS, WebSocket), and only then describe it as tested. Until
then this file is a note, not a deployment path.
