"""Background actor contracts, optional webhook, CLI, topology peers and API contract snapshots."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from observenet import cli
from observenet.core.db import session_scope
from observenet.core.redis import INGEST_QUEUE, MAINT_QUEUE
from observenet.core.time import utcnow
from observenet.models import Alert
from observenet.workers import tasks
from tests import helpers as h

BACKEND = Path(__file__).resolve().parents[1]


def test_every_actor_is_bounded_and_retry_limited() -> None:
    actors = {a.actor_name: a for a in (tasks.process_ingest, tasks.run_rollups, tasks.run_retention, tasks.evaluate,
                                        tasks.send_webhook, tasks.sync_graph)}  # fmt: skip
    for name, actor in actors.items():
        assert actor.options.get("time_limit"), f"{name} has no time limit"
        assert actor.options.get("max_retries") is not None and actor.options["max_retries"] <= 8, name
    assert tasks.process_ingest.queue_name == INGEST_QUEUE
    assert tasks.process_ingest.options["max_retries"] >= 3  # ingest must survive a transient DB blip
    for periodic in (tasks.run_rollups, tasks.run_retention, tasks.evaluate, tasks.sync_graph):
        assert periodic.queue_name == MAINT_QUEUE
        assert periodic.options.get("max_age"), (
            f"{periodic.actor_name}: a backlog of stale ticks would be replayed"
        )


def test_scheduler_only_enqueues_known_idempotent_actors() -> None:
    from observenet.workers.scheduler import SCHEDULE

    for interval, name in SCHEDULE:
        assert interval >= 10 and hasattr(tasks, name)
    assert {name for _, name in SCHEDULE} == {"evaluate", "run_rollups", "run_retention", "sync_graph"}


async def test_failing_task_is_counted_and_re_raised_for_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    from prometheus_client import REGISTRY

    async def boom(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("database went away")

    monkeypatch.setattr(tasks.ingest_service, "process_batch", boom)
    before = (
        REGISTRY.get_sample_value(
            "observenet_task_runs_total", {"task": "process_ingest", "outcome": "error"}
        )
        or 0
    )
    with pytest.raises(RuntimeError):  # re-raised so Dramatiq retries with backoff
        await tasks.process_ingest.fn.__wrapped__(1, h.payload(), utcnow().isoformat())
    assert (
        REGISTRY.get_sample_value(
            "observenet_task_runs_total", {"task": "process_ingest", "outcome": "error"}
        )
        == before + 1
    )


async def test_malformed_queued_payload_fails_validation_instead_of_being_applied() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        await tasks.process_ingest.fn.__wrapped__(
            1, {"schema_version": 1, "garbage": True}, utcnow().isoformat()
        )


async def seed_alert() -> int:
    async with session_scope() as session:
        alert = Alert(kind="new_device", dedup_key="k", title="New device", summary="s")
        session.add(alert)
        await session.flush()
        return alert.id


async def test_webhook_is_not_sent_unless_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*_a: Any, **_k: Any) -> None:
        raise AssertionError("no outbound request may be made without a configured webhook URL")

    monkeypatch.setattr(tasks.httpx, "AsyncClient", forbidden)
    await tasks.send_webhook.fn.__wrapped__(await seed_alert(), "fired")  # returns silently


async def test_configured_webhook_is_signed_and_carries_no_inventory(
    set_config: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    set_config(alert_webhook_url="https://hooks.example.com/observenet", alert_webhook_secret="s3cret-s3cret")  # noqa: S106
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(204)

    real_client = httpx.AsyncClient

    def client_factory(**kwargs: Any) -> httpx.AsyncClient:
        assert kwargs.get("follow_redirects") is False
        return real_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(tasks.httpx, "AsyncClient", client_factory)
    await tasks.send_webhook.fn.__wrapped__(await seed_alert(), "fired")
    (request,) = captured
    body = request.content
    assert str(request.url) == "https://hooks.example.com/observenet"
    assert (
        request.headers["x-observenet-signature"]
        == hmac.new(b"s3cret-s3cret", body, hashlib.sha256).hexdigest()
    )
    payload = json.loads(body)
    assert set(payload) == {"event", "id", "kind", "severity", "title", "summary", "state", "started_at"}
    assert payload["event"] == "fired"


async def test_graph_sync_is_a_noop_without_neo4j() -> None:
    await tasks.sync_graph.fn.__wrapped__()  # optional integration: not configured, nothing happens


async def test_topology_peers_falls_back_to_postgresql(admin_client: httpx.AsyncClient) -> None:
    sid = await h.make_source()
    now = utcnow()
    await h.process(
        sid,
        h.payload(
            inventory=h.inventory(
                now, [("192.168.1.20", "3c:22:fb:00:00:0a"), ("192.168.1.21", "3c:22:fb:00:00:0b")]
            )
        ),
    )
    host_id = await h.source_asset_id(sid)
    body = (await admin_client.get(f"/api/v1/topology/peers/{host_id}")).json()
    assert body["backend"] == "postgresql" and body["asset_id"] == host_id
    assert len(body["peers"]) == 2 and all(p["segments"] == ["192.168.1.0/24"] for p in body["peers"])
    assert host_id not in [p["asset_id"] for p in body["peers"]]
    assert (await admin_client.get("/api/v1/topology/peers/999999")).status_code == 404


# ---------------------------------------------------------------- CLI


async def test_cli_create_admin_and_token(capsys: pytest.CaptureFixture[str]) -> None:
    assert await cli._create_admin("root", "short") == 1  # weak password refused, nothing created
    assert "at least 12" in capsys.readouterr().err
    assert await cli._create_admin("root", "a-proper-long-passphrase") == 0
    assert await cli._create_token("laptop", 30) == 0
    out = capsys.readouterr().out
    assert "created admin 'root'" in out and "onet_" in out and "not shown again" in out


def test_cli_exports_and_snapshots_match_the_running_code(tmp_path: Path) -> None:
    """backend/openapi.json and schema.graphql feed the generated frontend types: they must not drift.
    Regenerate with `make contracts` (or the two `python -m observenet.cli export-*` commands)."""
    openapi, sdl = tmp_path / "openapi.json", tmp_path / "schema.graphql"
    assert cli._export_openapi(str(openapi)) == 0 and cli._export_graphql(str(sdl)) == 0
    document = json.loads(openapi.read_text())
    assert (
        "/api/v1/ingest/agent" in document["paths"]
        and "/api/v1/findings/{finding_id}/verify" in document["paths"]
    )
    assert not any(
        p.startswith("/api/v1/demo") for p in document["paths"]
    )  # demo controls exist only in DEMO_MODE
    assert "type Query" in sdl.read_text() and "Mutation" not in sdl.read_text()
    for generated, committed in ((openapi, BACKEND / "openapi.json"), (sdl, BACKEND / "schema.graphql")):
        if committed.exists():
            assert generated.read_text() == committed.read_text(), f"{committed.name} is stale; regenerate it"
