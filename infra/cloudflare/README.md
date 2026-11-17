# Cloudflare Tunnel configuration

> Not deployed. These files are examples; nothing here has been run against a real
> Cloudflare account. Full procedure: `docs/DOMAIN_AND_AUTH.md`.

There are two ways to run a tunnel. Use one.

## A. Token-based (remotely managed) — what the Compose `tunnel` profile expects

- The tunnel and its ingress (public hostname → `http://web:8080`) are defined in the
  Cloudflare Zero Trust dashboard.
- `linux-leif` only needs the tunnel token:

```
CLOUDFLARE_TUNNEL_TOKEN=<token>
COMPOSE_PROFILES=collector,tunnel
```

- `config.example.yml` is **not used** in this mode.
- The token grants the ability to serve the tunnel's hostnames. Treat it like a
  password: it lives only in `.env` (git-ignored, `chmod 600`). Rotate it in the
  dashboard (Tunnels → the tunnel → Refresh token) if it leaks.

## B. Locally managed (credentials file + `config.yml`)

Use this only if you prefer ingress rules in a file under version control. It requires
changing the `cloudflared` service command in `compose.yaml` to
`tunnel --config /etc/cloudflared/config.yml run` and mounting this directory; that
variant is not wired into the provided Compose file.

```bash
cloudflared tunnel login
```

```bash
cloudflared tunnel create linux-leif
```

```bash
cloudflared tunnel route dns linux-leif observenet.leifslab.com
```

Copy `config.example.yml` to `config.yml`, fill in the tunnel UUID, and place the
generated `<UUID>.json` credentials file next to it. Both are secrets: do not commit
them (`*.json` credentials and `config.yml` should stay out of git).

## Either way

- Create the Cloudflare Access application for `observenet.leifslab.com` **before**
  publishing the hostname.
- The origin service is `http://web:8080` on the Compose network — never the API
  container directly, never PostgreSQL/Redis, never the metrics port.
- Do not add a catch-all ingress rule that forwards other hostnames to the stack.
