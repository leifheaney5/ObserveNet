# ObserveNet documentation

| Document | Contents |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | Components, data path, technology choices, trade-offs |
| [PRODUCT_SPEC.md](PRODUCT_SPEC.md) | Pages, states, vocabulary, non-goals |
| [TELEMETRY_SOURCES.md](TELEMETRY_SOURCES.md) | What each source can and cannot measure; formulas and policies |
| [SECURITY_MODEL.md](SECURITY_MODEL.md) | Authentication, authorization, hardening, what the product refuses to do |
| [THREAT_MODEL.md](THREAT_MODEL.md) | Assets, trust boundaries, threats, residual risks |
| [ADRs/](ADRs/README.md) | Architecture decision records |
| [DEPLOY_LINUX_LEIF.md](DEPLOY_LINUX_LEIF.md) | Bring-up on the home Docker host, agents, backups, updates |
| [DOMAIN_AND_AUTH.md](DOMAIN_AND_AUTH.md) | `https://observenet.leifslab.com` via tunnel + edge access (not deployed) |
| [OPERATIONS.md](OPERATIONS.md) | Runbook: health, metrics, queues, retention, tokens |
| [INTERVIEW_DEMO.md](INTERVIEW_DEMO.md) | Timed 5-minute demo and talking points |
| [LIMITATIONS.md](LIMITATIONS.md) | What is missing, untested or out of scope |

Related: `agents/linux/README.md`, `agents/windows/README.md`,
`infra/cloudflare/README.md`, `infra/aws-optional/README.md`.
