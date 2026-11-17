# Remote access: `https://observenet.leafslab.com`

> ## STATUS: NOT DEPLOYED
>
> Nothing in this document has been executed. The build had no access to the
> `leafslab.com` DNS zone, a Cloudflare account, the registrar, or `linux-leif`.
> `observenet.leafslab.com` is **not** configured, **not** live, and has **not** been
> verified. What exists is: an application that is safe to put behind an HTTPS edge
> (trusted hosts, `Secure` cookies, CSRF, no unauthenticated data endpoints), a Compose
> profile for `cloudflared`, and the procedure below.

## Design

```
browser ──HTTPS──> Cloudflare edge ──(Access policy: who may reach the hostname)──>
        Cloudflare Tunnel <──outbound-only connection── cloudflared (Compose profile "tunnel")
                                                             │ http://web:8080 (internal Docker network)
                                                             ▼
                                              nginx ──> ObserveNet API (its own login, CSRF, roles)
```

- **No inbound port.** `cloudflared` dials out; the router needs no port forward and
  the stack keeps publishing only `127.0.0.1:8080`.
- **Defense in depth.** Layer 1: Cloudflare Access decides who may reach the hostname
  at all. Layer 2: ObserveNet's own login (Argon2id, server-side sessions, CSRF, roles).
  Either layer failing alone does not expose the inventory.
- **HTTPS** terminates at Cloudflare with a certificate for the hostname. The hop from
  `cloudflared` to `web` is plain HTTP inside the Docker network on the same host.
- **WebSocket** (`/api/v1/stream`) is proxied by Cloudflare and by nginx on the same
  origin, authenticated by the same session cookie.

Trade-off to accept knowingly: Cloudflare terminates TLS and can technically read the
traffic. See "Alternatives" if that is not acceptable.

## Prerequisite: the DNS zone

Cloudflare Tunnel public hostnames only work for zones that are **active in the same
Cloudflare account**. Check where `leafslab.com` is hosted:

```bash
dig +short NS leafslab.com
```

- Nameservers ending in `.ns.cloudflare.com` → continue below.
- Anything else → the zone is not on Cloudflare. A plain CNAME at another DNS host
  pointing to `<tunnel-id>.cfargotunnel.com` does **not** work (the edge only routes
  hostnames of zones in the tunnel's account; partial/CNAME setup is a paid-plan
  feature). Choose one of the alternatives at the end, or move the zone to Cloudflare
  first (add the site in the Cloudflare dashboard, import records, change nameservers
  at the registrar, wait for "Active").

You must control both the registrar and the Cloudflare account. Protect both with MFA.

## Steps (Cloudflare Tunnel + Access)

Order matters: create the Access application **before** the tunnel's public hostname so
the hostname is never reachable without the edge policy.

### 1. Zero Trust organisation

Cloudflare dashboard → Zero Trust. Pick a team name (it becomes
`<team>.cloudflareaccess.com`). Under Settings → Authentication, keep "One-time PIN" or
add an identity provider (Google, GitHub, …).

### 2. Access application (the edge policy)

Zero Trust → Access → Applications → Add an application → **Self-hosted**.

| Field | Value |
|---|---|
| Application name | ObserveNet |
| Application domain | `observenet.leafslab.com` |
| Session duration | 24 hours (or shorter) |
| Policy name | Owner only |
| Action | Allow |
| Include | Emails → your own address(es) |

Leave "Accept all available identity providers" as you prefer; require MFA at the
identity provider if it supports it.

Only if a remote agent (the Windows PC) must post through the hostname: add a second
self-hosted application limited to the two agent paths —
`observenet.leafslab.com/api/v1/ingest/agent` and
`observenet.leafslab.com/api/v1/agent/config` — with a policy of action
**Service Auth** including a service token (Access → Service Auth → Create service
token). Give the token's client id/secret to the agent (`-AccessHeadersFile`). Those
endpoints still require the ObserveNet collector token. If no remote agent is used,
skip this.

### 3. Tunnel

Zero Trust → Networks → Tunnels → Create a tunnel → **Cloudflared** → name it
`linux-leif`. Copy the tunnel **token** shown in the install command (the long string
after `--token`). Do not run the suggested install command; the Compose profile runs
`cloudflared` for you.

In the tunnel's **Public Hostname** tab add:

| Field | Value |
|---|---|
| Subdomain | `observenet` |
| Domain | `leafslab.com` |
| Type | HTTP |
| URL | `web:8080` |

Cloudflare creates the proxied CNAME record for `observenet.leafslab.com`
automatically.

### 4. Configure the stack on `linux-leif`

Edit `.env`:

```
OBSERVENET_PUBLIC_URL=https://observenet.leafslab.com
OBSERVENET_ALLOWED_HOSTS=observenet.leafslab.com,localhost,127.0.0.1
CLOUDFLARE_TUNNEL_TOKEN=<token from step 3>
COMPOSE_PROFILES=collector,tunnel
```

```bash
docker compose up -d
```

```bash
docker compose logs --tail 20 cloudflared
```

Expect lines reporting registered tunnel connections.

Effects of the `https://` public URL: session cookies get the `Secure` flag and HSTS is
sent. From then on use the HTTPS hostname in the browser; plain
`http://127.0.0.1:8080` may no longer keep a session in browsers that refuse `Secure`
cookies over HTTP. The bundled collector is unaffected (it uses its bearer token on
loopback).

### 5. Zone settings

In the `leafslab.com` zone: SSL/TLS → Edge Certificates → **Always Use HTTPS: On**,
minimum TLS 1.2. Network → **WebSockets: On** (default).

## Verification

Run these from a machine **outside** the home network unless stated.

DNS resolves to Cloudflare:

```bash
dig +short observenet.leafslab.com
```

TLS certificate and protocol:

```bash
openssl s_client -connect observenet.leafslab.com:443 -servername observenet.leafslab.com </dev/null 2>/dev/null | openssl x509 -noout -subject -issuer -dates
```

HTTP is redirected to HTTPS:

```bash
curl -sI http://observenet.leafslab.com/ | head -n 3
```

Expected: `301` with `Location: https://observenet.leafslab.com/`.

The edge policy is in front (no app content without Access):

```bash
curl -s -o /dev/null -w '%{http_code} %{redirect_url}\n' https://observenet.leafslab.com/
```

Expected: `302` to `https://<team>.cloudflareaccess.com/cdn-cgi/access/login/…`.

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://observenet.leafslab.com/api/v1/devices
```

Expected: `302` (Access), never `200`.

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://observenet.leafslab.com/graphql
```

Expected: `302` (Access).

The application layer still requires its own login (run on `linux-leif`, bypassing the
edge):

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8080/api/v1/devices
```

Expected: `401`.

```bash
curl -s -o /dev/null -w '%{http_code}\n' -X POST -H 'content-type: application/json' -d '{"query":"{ networkSummary { deviceCount } }"}' http://127.0.0.1:8080/graphql
```

Expected: `401`.

The local port is not exposed to the LAN (run on `linux-leif`):

```bash
ss -ltn | grep ':8080'
```

Expected: `127.0.0.1:8080` only.

In a browser: open `https://observenet.leafslab.com`, pass the Access login, then the
ObserveNet login. The header indicator should read **Live** (WebSocket through the
tunnel). In DevTools → Network → WS, `/api/v1/stream` should show status 101 and one
frame per second.

Check after first login: Settings → Audit log should show your real public IP for the
login event. If it shows a Docker-internal address instead, client IPs are not being
forwarded through the proxy chain and the per-IP login rate limit is effectively
shared by all clients; fix the forwarded-header configuration before relying on it.

## What is and is not protected

| Path | Edge (Access) | Application |
|---|---|---|
| `/` (SPA) | login required | static files only |
| `/api/v1/*` | login required | session cookie + CSRF (or agent token for the two agent endpoints) |
| `/api/v1/stream` (WebSocket) | login required | session cookie + Origin check |
| `/graphql` | login required | session cookie + CSRF; introspection and GraphiQL off in production |
| Prometheus metrics | not routable | separate internal listener, not behind nginx |
| PostgreSQL, Redis | not routable | internal Docker networks |
| OpenAPI docs | login required | disabled in production |

Not implemented: the application does not validate the `Cf-Access-Jwt-Assertion`
header. Anyone who can reach `web:8080` directly (i.e. local users on `linux-leif`)
skips the edge layer and faces only the application login.

## Turning remote access off

Remove `tunnel` from `COMPOSE_PROFILES`, then:

```bash
docker compose up -d --remove-orphans
```

Delete the public hostname (and optionally the tunnel) in the Zero Trust dashboard.

## Alternatives if the zone is not on Cloudflare (or Cloudflare is not wanted)

| Option | How | Hostname | Edge auth | Notes |
|---|---|---|---|---|
| Move the zone to Cloudflare | Change nameservers at the registrar | `observenet.leafslab.com` | Cloudflare Access | Then follow this document |
| Tailscale Serve | `tailscale serve --bg 8080` on `linux-leif` | `linux-leif.<tailnet>.ts.net` (tailnet-only) | Tailnet membership + device auth | Most private: not on the public internet at all. Set `OBSERVENET_PUBLIC_URL`/`ALLOWED_HOSTS` to the ts.net name. Does not give the `leafslab.com` hostname. |
| Tailscale Funnel | `tailscale funnel --bg 8080` | `linux-leif.<tailnet>.ts.net` (public) | **None** — only the ObserveNet login | Public exposure with a single auth layer; not recommended for this data. Custom domains are not supported. |
| VPS reverse proxy + WireGuard | Small VPS with Caddy (Let's Encrypt for `observenet.leafslab.com`, A record → VPS) proxying over a WireGuard tunnel that `linux-leif` initiates outbound | `observenet.leafslab.com` | Add one yourself (e.g. oauth2-proxy or mTLS in Caddy) | You operate and patch the VPS; TLS terminates on infrastructure you control. |
| Router port forward to a local reverse proxy | — | — | — | Not recommended: opens an inbound port to the home network. |

For every alternative: keep `OBSERVENET_BIND=127.0.0.1`, set
`OBSERVENET_PUBLIC_URL` to the exact HTTPS origin users will type, list that host in
`OBSERVENET_ALLOWED_HOSTS`, and re-run the application-layer checks above.

## Files

- `infra/cloudflare/README.md` — token-based vs. locally managed tunnels.
- `infra/cloudflare/config.example.yml` — ingress rules for a locally managed tunnel.
