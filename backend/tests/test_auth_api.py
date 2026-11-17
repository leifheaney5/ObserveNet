"""Authentication, authorization, CSRF, tokens and edge hardening."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from observenet.auth import service as auth_service
from observenet.auth.crypto import hash_password, token_digest, verify_password
from observenet.core.config import get_config
from observenet.core.db import session_scope
from observenet.core.time import utcnow
from observenet.models import ApiToken, AuditEvent, Session, User
from tests import helpers as h
from tests.conftest import ADMIN_PASSWORD, VIEWER_PASSWORD, _client, login

PUBLIC = {
    ("get", "/api/v1/health"),
    ("get", "/api/v1/ready"),
    ("get", "/api/v1/meta"),
    ("post", "/api/v1/auth/login"),
}


def fill(path: str) -> str:
    import re

    return re.sub(r"\{[^}]+\}", "1", path)


# ---------------------------------------------------------------- unauthenticated surface


async def test_every_route_except_health_meta_and_login_requires_authentication(
    app: Any, client: httpx.AsyncClient
) -> None:
    checked = 0
    for path, item in app.openapi()["paths"].items():
        for method in item:
            if method not in ("get", "post", "put", "patch", "delete") or (method, path) in PUBLIC:
                continue
            resp = await client.request(method.upper(), fill(path), json={})
            assert resp.status_code == 401, (
                f"{method.upper()} {path} answered {resp.status_code} without credentials"
            )
            checked += 1
    assert checked > 45  # the whole product API, not a sample
    for method, path in PUBLIC - {("post", "/api/v1/auth/login")}:
        assert (await client.request(method.upper(), path)).status_code == 200


async def test_unauthenticated_meta_leaks_nothing_but_setup_state(client: httpx.AsyncClient) -> None:
    body = (await client.get("/api/v1/meta")).json()
    assert set(body) == {"name", "version", "demo_mode", "setup_required", "setup_hint"}
    assert body["setup_required"] is True and "OBSERVENET_ADMIN_PASSWORD" in body["setup_hint"]
    health = (await client.get("/api/v1/health")).json()
    assert health == {"status": "ok"}


async def test_api_docs_and_openapi_are_not_served_outside_development(client: httpx.AsyncClient) -> None:
    for path in (
        "/api/docs",
        "/api/openapi.json",
        "/docs",
        "/openapi.json",
        "/redoc",
        "/metrics",
        "/internal/metrics",
    ):
        assert (await client.get(path)).status_code == 404, path


async def test_security_headers_on_every_response(client: httpx.AsyncClient) -> None:
    for path in ("/api/v1/health", "/api/v1/devices", "/nope"):
        headers = (await client.get(path)).headers
        assert headers["x-content-type-options"] == "nosniff"
        assert headers["x-frame-options"] == "DENY"
        assert "frame-ancestors 'none'" in headers["content-security-policy"]
        assert "default-src 'self'" in headers["content-security-policy"]
        assert headers["referrer-policy"] == "same-origin"
    assert (await client.get("/api/v1/devices")).headers["cache-control"] == "no-store"
    assert (
        "access-control-allow-origin"
        not in (await client.get("/api/v1/health", headers={"Origin": "https://evil.example"})).headers
    )  # no CORS: cross-origin reads are refused by the browser


async def test_untrusted_host_header_is_rejected(set_config: Any) -> None:
    from observenet.api.app import create_app

    set_config(env="development", allowed_hosts="observenet.leafslab.com")
    app = create_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://evil.example"
    ) as bad:
        assert (await bad.get("/api/v1/health")).status_code == 400
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://observenet.leafslab.com"
    ) as good:
        assert (await good.get("/api/v1/health")).status_code == 200


# ---------------------------------------------------------------- bootstrap (no default credentials)


async def test_bootstrap_admin_refuses_missing_and_weak_passwords(set_config: Any) -> None:
    async with session_scope() as session:
        assert await auth_service.bootstrap_admin(session) == "missing_password"
        assert await auth_service.user_count(session) == 0
    for weak in ("short", "changeme1234", "Password1234", "adminadminadmin"):
        set_config(admin_password=weak)
        async with session_scope() as session:
            assert await auth_service.bootstrap_admin(session) == "weak_password", weak
            assert await auth_service.user_count(session) == 0
    set_config(admin_password="a-unique-long-passphrase", admin_username="Leif")
    async with session_scope() as session:
        assert await auth_service.bootstrap_admin(session) == "created"
    async with session_scope() as session:
        assert await auth_service.bootstrap_admin(session) == "exists"
        user = (await session.execute(select(User))).scalar_one()
    assert (user.username, user.role) == ("leif", "admin")
    assert (
        user.password_hash.startswith("$argon2id$") and "a-unique-long-passphrase" not in user.password_hash
    )


async def test_no_default_account_can_log_in(client: httpx.AsyncClient) -> None:
    for username, password in (
        ("admin", "admin"),
        ("admin", "password"),
        ("admin", ""),
        ("observenet", "observenet"),
    ):
        resp = await client.post(
            "/api/v1/auth/login", json={"username": username, "password": password or "x"}
        )
        assert resp.status_code == 401


async def test_production_refuses_to_start_without_a_real_secret_key(set_config: Any) -> None:
    from observenet.api.app import create_app, lifespan

    set_config(env="production", secret_key="short")
    with pytest.raises(RuntimeError, match="OBSERVENET_SECRET_KEY"):
        async with lifespan(create_app()):
            pass


# ---------------------------------------------------------------- login / session / cookies


async def test_login_success_sets_hardened_cookies_and_me_works(
    admin_client: httpx.AsyncClient, app: Any
) -> None:
    async with _client(app) as c:
        resp = await login(c, "ADMIN", ADMIN_PASSWORD)  # usernames are case-insensitive
        assert resp.status_code == 200 and resp.json()["user"]["role"] == "admin"
        assert "password" not in resp.text.lower().replace("password_hash", "")
        cookies = {h_.split("=", 1)[0]: h_.lower() for h_ in resp.headers.get_list("set-cookie")}
        session_cookie, csrf_cookie = cookies["observenet_session"], cookies["observenet_csrf"]
        assert (
            "httponly" in session_cookie and "samesite=lax" in session_cookie and "path=/" in session_cookie
        )
        assert "httponly" not in csrf_cookie  # the SPA must read it to echo it back
        assert "secure" not in session_cookie  # plain-http public URL in this test
        me = await c.get("/api/v1/auth/me")
        assert me.status_code == 200 and me.json()["csrf_token"] == resp.json()["csrf_token"]
    async with session_scope() as session:
        stored = (await session.execute(select(Session).order_by(Session.id.desc()))).scalars().first()
    assert stored is not None and len(stored.token_hash) == 64  # only a keyed digest is stored


async def test_session_cookie_is_secure_when_public_url_is_https(
    admin_client: httpx.AsyncClient, app: Any, set_config: Any
) -> None:
    set_config(public_url="https://observenet.leafslab.com")
    assert get_config().cookie_secure_effective is True
    async with _client(app) as c:
        resp = await c.post("/api/v1/auth/login", json={"username": "admin", "password": ADMIN_PASSWORD})
        assert all("secure" in h_.lower() for h_ in resp.headers.get_list("set-cookie"))


async def test_login_failure_is_generic_and_audited(
    admin_client: httpx.AsyncClient, client: httpx.AsyncClient
) -> None:
    wrong_password = await client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": "not-the-password"}
    )
    unknown_user = await client.post(
        "/api/v1/auth/login", json={"username": "nobody", "password": "not-the-password"}
    )
    assert wrong_password.status_code == unknown_user.status_code == 401
    assert wrong_password.json() == unknown_user.json()  # no username enumeration
    assert "set-cookie" not in wrong_password.headers
    async with session_scope() as session:
        failed = (
            (await session.execute(select(AuditEvent).where(AuditEvent.action == "auth.login_failed")))
            .scalars()
            .all()
        )
    assert len(failed) == 2 and all("not-the-password" not in str(a.details) for a in failed)


async def test_login_is_rate_limited(
    admin_client: httpx.AsyncClient, client: httpx.AsyncClient, set_config: Any
) -> None:
    set_config(login_rate_limit_per_minute="3")
    codes = []
    for _ in range(5):
        codes.append(
            (
                await client.post(
                    "/api/v1/auth/login", json={"username": "admin", "password": "wrong-wrong-wrong"}
                )
            ).status_code
        )
    # the admin_client fixture's own login already used one of the three attempts for this address
    assert codes == [401, 401, 429, 429, 429]
    # even the correct password is refused while limited
    assert (
        await client.post("/api/v1/auth/login", json={"username": "admin", "password": ADMIN_PASSWORD})
    ).status_code == 429


async def test_logout_revokes_the_session_server_side(admin_client: httpx.AsyncClient, app: Any) -> None:
    stolen_cookie = admin_client.cookies["observenet_session"]
    assert (await admin_client.post("/api/v1/auth/logout")).status_code == 204
    async with _client(app) as replay:
        replay.cookies.set("observenet_session", stolen_cookie)
        assert (await replay.get("/api/v1/auth/me")).status_code == 401


async def test_expired_or_tampered_session_is_rejected(admin_client: httpx.AsyncClient, app: Any) -> None:
    async with _client(app) as forged:
        forged.cookies.set("observenet_session", "A" * 43)
        assert (await forged.get("/api/v1/devices")).status_code == 401
    async with session_scope() as session:
        for sess in (await session.execute(select(Session))).scalars():
            sess.expires_at = utcnow() - timedelta(seconds=1)
    assert (await admin_client.get("/api/v1/devices")).status_code == 401


async def test_disabled_user_loses_access_immediately(
    admin_client: httpx.AsyncClient, viewer_client: httpx.AsyncClient
) -> None:
    viewer = next(u for u in (await admin_client.get("/api/v1/users")).json() if u["username"] == "viewer")
    assert (await viewer_client.get("/api/v1/devices")).status_code == 200
    assert (
        await admin_client.patch(f"/api/v1/users/{viewer['id']}", json={"disabled": True})
    ).status_code == 200
    assert (await viewer_client.get("/api/v1/devices")).status_code == 401
    assert (
        await viewer_client.post(
            "/api/v1/auth/login", json={"username": "viewer", "password": VIEWER_PASSWORD}
        )
    ).status_code == 401


# ---------------------------------------------------------------- CSRF + roles


MUTATIONS: list[tuple[str, str, dict[str, Any] | None]] = [
    ("PATCH", "/api/v1/devices/1", {"display_name": "x"}),
    ("POST", "/api/v1/devices/1/merge", {"into_id": 2}),
    ("POST", "/api/v1/devices/merges/1/undo", None),
    ("POST", "/api/v1/devices/import", {"content": "ip\n192.168.1.9\n"}),
    ("POST", "/api/v1/findings/1/verify", None),
    ("POST", "/api/v1/findings/1/status", {"action": "close"}),
    ("POST", "/api/v1/alerts/1/acknowledge", None),
    ("POST", "/api/v1/alerts/1/snooze", {"until": "2099-01-01T00:00:00Z"}),
    ("POST", "/api/v1/alerts/1/resolve", None),
    ("PATCH", "/api/v1/alert-rules/1", {"enabled": False}),
    (
        "POST",
        "/api/v1/maintenance-windows",
        {"name": "w", "starts_at": "2099-01-01T00:00:00Z", "ends_at": "2099-01-01T01:00:00Z"},
    ),
    ("DELETE", "/api/v1/maintenance-windows/1", None),
    (
        "POST",
        "/api/v1/probe-targets",
        {"name": "t", "kind": "tcp", "scope": "external", "host": "1.1.1.1", "port": 443},
    ),
    (
        "PATCH",
        "/api/v1/probe-targets/1",
        {"name": "t", "kind": "tcp", "scope": "external", "host": "1.1.1.1", "port": 443},
    ),
    ("DELETE", "/api/v1/probe-targets/1", None),
    ("PUT", "/api/v1/settings", {}),
    ("POST", "/api/v1/tokens", {"name": "t"}),
    ("DELETE", "/api/v1/tokens/1", None),
    ("PATCH", "/api/v1/sources/1", {"enabled": False}),
    ("POST", "/api/v1/sources/1/test", None),
    ("POST", "/api/v1/users", {"username": "eve", "password": "a-long-enough-password"}),
    ("PATCH", "/api/v1/users/1", {"role": "admin"}),
]
ADMIN_ONLY_READS = ["/api/v1/tokens", "/api/v1/users", "/api/v1/audit", "/api/v1/settings/deployment",
                    "/api/v1/export/devices", "/api/v1/export/findings"]  # fmt: skip
READS = ["/api/v1/overview", "/api/v1/devices", "/api/v1/findings", "/api/v1/alerts", "/api/v1/alert-rules", "/api/v1/sources",
         "/api/v1/capabilities", "/api/v1/status", "/api/v1/metrics/current", "/api/v1/metrics/series", "/api/v1/settings",
         "/api/v1/insights", "/api/v1/topology", "/api/v1/checks", "/api/v1/assessments", "/api/v1/events",
         "/api/v1/probe-targets", "/api/v1/connectivity/summary", "/api/v1/traffic/top-devices", "/api/v1/maintenance-windows"]  # fmt: skip


async def test_mutations_require_the_csrf_header(admin_client: httpx.AsyncClient) -> None:
    token = admin_client.headers.pop("x-csrf-token")
    for method, path, body in MUTATIONS:
        resp = await admin_client.request(method, path, json=body)
        assert resp.status_code == 403 and "CSRF" in resp.json()["detail"], (
            f"{method} {path} -> {resp.status_code}"
        )
    wrong = await admin_client.patch(
        "/api/v1/devices/1", json={"display_name": "x"}, headers={"x-csrf-token": "x" * len(token)}
    )
    assert wrong.status_code == 403
    assert (await admin_client.get("/api/v1/devices")).status_code == 200  # safe methods need no token
    ok = await admin_client.patch(
        "/api/v1/devices/1", json={"display_name": "x"}, headers={"x-csrf-token": token}
    )
    assert ok.status_code == 404  # passed CSRF + auth; the device just does not exist


async def test_csrf_token_of_another_session_is_rejected(
    admin_client: httpx.AsyncClient, viewer_client: httpx.AsyncClient
) -> None:
    resp = await admin_client.post(
        "/api/v1/tokens", json={"name": "t"}, headers={"x-csrf-token": viewer_client.headers["x-csrf-token"]}
    )
    assert resp.status_code == 403


async def test_viewer_can_read_but_never_mutate(viewer_client: httpx.AsyncClient) -> None:
    for path in READS:
        assert (await viewer_client.get(path)).status_code == 200, path
    for method, path, body in MUTATIONS:
        resp = await viewer_client.request(method, path, json=body)
        assert resp.status_code == 403 and "administrator" in resp.json()["detail"], (
            f"{method} {path} -> {resp.status_code}"
        )
    for path in ADMIN_ONLY_READS:
        assert (await viewer_client.get(path)).status_code == 403, path
    async with session_scope() as session:
        assert (await session.execute(select(ApiToken))).first() is None  # nothing slipped through


async def test_admin_can_read_everything(admin_client: httpx.AsyncClient) -> None:
    for path in READS + ADMIN_ONLY_READS:
        assert (await admin_client.get(path)).status_code == 200, path


# ---------------------------------------------------------------- users / passwords


async def test_password_change_revokes_other_sessions(admin_client: httpx.AsyncClient, app: Any) -> None:
    async with _client(app) as other_device:
        assert (await login(other_device, "admin", ADMIN_PASSWORD)).status_code == 200
        bad = await admin_client.post(
            "/api/v1/auth/password",
            json={"current_password": "wrong", "new_password": "another-long-passphrase"},
        )
        assert bad.status_code == 403
        short = await admin_client.post(
            "/api/v1/auth/password", json={"current_password": ADMIN_PASSWORD, "new_password": "short"}
        )
        assert short.status_code == 422
        ok = await admin_client.post(
            "/api/v1/auth/password",
            json={"current_password": ADMIN_PASSWORD, "new_password": "another-long-passphrase"},
        )
        assert ok.status_code == 204
        assert (
            await admin_client.get("/api/v1/auth/me")
        ).status_code == 200  # the session that changed it survives
        assert (await other_device.get("/api/v1/auth/me")).status_code == 401  # everything else is signed out
    async with _client(app) as fresh:
        assert (
            await fresh.post("/api/v1/auth/login", json={"username": "admin", "password": ADMIN_PASSWORD})
        ).status_code == 401
        assert (
            await fresh.post(
                "/api/v1/auth/login", json={"username": "admin", "password": "another-long-passphrase"}
            )
        ).status_code == 200


async def test_last_admin_cannot_be_demoted_or_disabled(admin_client: httpx.AsyncClient) -> None:
    admin_id = (await admin_client.get("/api/v1/auth/me")).json()["user"]["id"]
    demote = await admin_client.patch(f"/api/v1/users/{admin_id}", json={"role": "viewer"})
    assert demote.status_code == 409 and "last active administrator" in demote.json()["detail"]
    assert (await admin_client.patch(f"/api/v1/users/{admin_id}", json={"disabled": True})).status_code == 409
    created = await admin_client.post(
        "/api/v1/users", json={"username": "second", "password": "second-admin-password", "role": "admin"}
    )
    assert created.status_code == 201 and "password" not in created.text
    assert (
        await admin_client.patch(f"/api/v1/users/{admin_id}", json={"role": "viewer"})
    ).status_code == 200  # now allowed


async def test_user_management_validation(admin_client: httpx.AsyncClient) -> None:
    assert (
        await admin_client.post("/api/v1/users", json={"username": "bob", "password": "short"})
    ).status_code == 422
    assert (
        await admin_client.post("/api/v1/users", json={"username": "bob", "password": "changeme1234"})
    ).status_code == 422
    assert (
        await admin_client.post(
            "/api/v1/users", json={"username": "b b", "password": "a-long-enough-password"}
        )
    ).status_code == 422
    assert (
        await admin_client.post(
            "/api/v1/users", json={"username": "bob", "password": "a-long-enough-password", "role": "root"}
        )
    ).status_code == 422
    assert (
        await admin_client.post(
            "/api/v1/users", json={"username": "bob", "password": "a-long-enough-password"}
        )
    ).status_code == 201
    assert (
        await admin_client.post(
            "/api/v1/users", json={"username": "BOB", "password": "a-long-enough-password"}
        )
    ).status_code == 409
    assert (await admin_client.patch("/api/v1/users/999999", json={"role": "viewer"})).status_code == 404


def test_password_hashing_is_argon2id_and_verifies() -> None:
    hashed = hash_password("correct horse battery staple")
    assert hashed.startswith("$argon2id$")
    assert verify_password("correct horse battery staple", hashed) is True
    assert verify_password("wrong", hashed) is False
    assert verify_password("anything", "not-a-hash") is False


# ---------------------------------------------------------------- collector tokens


async def test_token_is_shown_once_and_only_a_digest_is_stored(admin_client: httpx.AsyncClient) -> None:
    created = await admin_client.post("/api/v1/tokens", json={"name": "windows pc", "ttl_days": 30})
    assert created.status_code == 201
    raw = created.json()["token"]
    assert raw.startswith("onet_") and len(raw) > 40
    info = created.json()["info"]
    assert (
        info["scopes"] == ["ingest"] and info["expires_at"] is not None and info["token_prefix"] == raw[:10]
    )
    listing = await admin_client.get("/api/v1/tokens")
    assert raw not in listing.text and "token_hash" not in listing.text
    async with session_scope() as session:
        row = (await session.execute(select(ApiToken))).scalar_one()
    assert row.token_hash == token_digest(raw) and raw not in row.token_hash
    assert (
        await admin_client.post("/api/v1/tokens", json={"name": "x", "ttl_days": 9999})
    ).status_code == 422


async def test_agent_token_revocation_takes_effect_immediately(admin_client: httpx.AsyncClient) -> None:
    raw = (await admin_client.post("/api/v1/tokens", json={"name": "agent"})).json()["token"]
    token_id = (await admin_client.get("/api/v1/tokens")).json()[0]["id"]
    headers = {"Authorization": f"Bearer {raw}"}
    assert (
        await admin_client.post("/api/v1/ingest/agent", json=h.payload(), headers=headers)
    ).status_code == 202
    assert (await admin_client.get("/api/v1/agent/config", headers=headers)).status_code == 200
    assert (await admin_client.delete(f"/api/v1/tokens/{token_id}")).status_code == 204
    assert (
        await admin_client.post("/api/v1/ingest/agent", json=h.payload(), headers=headers)
    ).status_code == 401
    assert (await admin_client.get("/api/v1/agent/config", headers=headers)).status_code == 401
    assert (await admin_client.get("/api/v1/tokens")).json()[0]["revoked_at"] is not None


async def test_expired_agent_token_is_rejected(client: httpx.AsyncClient) -> None:
    raw, token_id = await h.issue_token(ttl_days=1)
    headers = {"Authorization": f"Bearer {raw}"}
    assert (await client.post("/api/v1/ingest/agent", json=h.payload(), headers=headers)).status_code == 202
    async with session_scope() as session:
        (await session.get(ApiToken, token_id)).expires_at = utcnow() - timedelta(seconds=1)  # type: ignore[union-attr]
    assert (await client.post("/api/v1/ingest/agent", json=h.payload(), headers=headers)).status_code == 401


async def test_agent_token_cannot_be_used_as_a_user_session(
    client: httpx.AsyncClient, agent_token: str
) -> None:
    headers = {"Authorization": f"Bearer {agent_token}"}
    for path in ("/api/v1/devices", "/api/v1/findings", "/api/v1/tokens", "/api/v1/overview"):
        assert (await client.get(path, headers=headers)).status_code == 401  # ingest scope only
    client.cookies.set("observenet_session", agent_token)
    assert (await client.get("/api/v1/devices")).status_code == 401


async def test_bootstrap_agent_token_registers_once_and_stays_revoked(set_config: Any) -> None:
    raw = "onet_bootstrap-token-0123456789-abcdefghijkl"
    set_config(bootstrap_agent_token=raw)
    async with session_scope() as session:
        await auth_service.ensure_bootstrap_agent_token(session)
        await auth_service.ensure_bootstrap_agent_token(session)
        token = (await session.execute(select(ApiToken))).scalar_one()
        assert token.is_bootstrap and token.token_hash == token_digest(raw)
        token.revoked_at = utcnow()
    async with session_scope() as session:  # restart: a revoked bootstrap token is not resurrected
        await auth_service.ensure_bootstrap_agent_token(session)
        assert await auth_service.resolve_agent_token(session, raw) is None
        assert len((await session.execute(select(ApiToken))).scalars().all()) == 1
    set_config(bootstrap_agent_token="too-short")
    async with session_scope() as session:
        await auth_service.ensure_bootstrap_agent_token(session)
        assert len((await session.execute(select(ApiToken))).scalars().all()) == 1


# ---------------------------------------------------------------- exports + audit


async def test_exports_contain_inventory_but_no_secrets(admin_client: httpx.AsyncClient) -> None:
    sid = await h.make_source()
    await h.process(sid, h.payload(inventory=h.inventory(utcnow(), [("192.168.1.20", "3c:22:fb:00:00:0a")], gateway=None),
                                   posture=h.posture(utcnow(), listening=h.listening(("tcp", "0.0.0.0", 6379)))))  # fmt: skip
    raw = (await admin_client.post("/api/v1/tokens", json={"name": "t"})).json()["token"]
    for kind in ("devices", "findings"):
        csv_resp = await admin_client.get(f"/api/v1/export/{kind}", params={"format": "csv"})
        json_resp = await admin_client.get(f"/api/v1/export/{kind}", params={"format": "json"})
        assert csv_resp.status_code == 200 and csv_resp.headers["content-type"].startswith("text/csv")
        assert "attachment" in csv_resp.headers["content-disposition"]
        assert json_resp.status_code == 200 and isinstance(json_resp.json(), list) and json_resp.json()
        for body in (csv_resp.text, json_resp.text):
            lowered = body.lower()
            assert raw not in body and ADMIN_PASSWORD not in body
            assert "token" not in lowered and "password" not in lowered and "secret" not in lowered
    assert "192.168.1.20" in (await admin_client.get("/api/v1/export/devices")).text
    assert "tcp/*:6379" not in (await admin_client.get("/api/v1/export/devices")).text
    assert "LNX-LISTEN-001" in (await admin_client.get("/api/v1/export/findings")).text
    actions = [a["action"] for a in (await admin_client.get("/api/v1/audit")).json()]
    assert actions.count("export.devices") >= 2 and "export.findings" in actions


async def test_device_edits_are_validated_and_audited(admin_client: httpx.AsyncClient) -> None:
    sid = await h.make_source()
    await h.process(sid, h.payload())
    device_id = await h.source_asset_id(sid)
    patch = {"display_name": "linux-leif", "asset_type": "server", "group_name": "Lab", "tags": ["docker", "prod"],
             "notes": "rack 1", "favorite": True, "owner": "Leif"}  # fmt: skip
    resp = await admin_client.patch(f"/api/v1/devices/{device_id}", json=patch)
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "linux-leif" and body["tags"] == ["docker", "prod"] and body["favorite"] is True
    assert (
        await admin_client.patch(f"/api/v1/devices/{device_id}", json={"asset_type": "spaceship"})
    ).status_code == 422
    assert (
        await admin_client.patch(f"/api/v1/devices/{device_id}", json={"tags": ["x"] * 30})
    ).status_code == 422
    assert (await admin_client.get("/api/v1/devices", params={"tag": "docker"})).json()["total"] == 1
    assert (await admin_client.get("/api/v1/devices", params={"tag": "nope"})).json()["total"] == 0
    assert (await admin_client.get("/api/v1/devices", params={"q": "LEIF"})).json()["total"] == 1
    assert (await admin_client.get("/api/v1/devices", params={"favorite": "true", "managed": "true"})).json()[
        "total"
    ] == 1
    cleared = await admin_client.patch(f"/api/v1/devices/{device_id}", json={"display_name": ""})
    assert cleared.json()["display_name"] is None and cleared.json()["name"] == "test-host"
    audit = [a for a in (await admin_client.get("/api/v1/audit")).json() if a["action"] == "asset.update"]
    assert (
        audit
        and audit[-1]["details"]["after"]["display_name"] == "linux-leif"
        and audit[-1]["actor_label"] == "admin"
    )
    events = (await admin_client.get("/api/v1/devices/" + str(device_id))).json()["events"]
    assert any(e["kind"] == "device.edited" for e in events)
