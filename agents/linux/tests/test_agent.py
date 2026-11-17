"""Agent transport + sampling behaviour with fake collectors and a mock HTTP transport."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from observenet_agent import agent as agent_mod
from observenet_agent.agent import MAX_BUFFERED_BATCHES, Agent, Settings, load_agent_uid


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    monkeypatch.setenv("OBSERVENET_URL", "http://127.0.0.1:8080")
    monkeypatch.setenv("OBSERVENET_AGENT_TOKEN", "onet_test-token")
    monkeypatch.setenv("OBSERVENET_AGENT_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("OBSERVENET_AGENT_HOSTNAME", "test-host")


def make_agent(handler: Any) -> Agent:
    a = Agent(Settings())
    a.client = httpx.AsyncClient(base_url="http://127.0.0.1:8080", transport=httpx.MockTransport(handler))
    return a


async def test_failed_upload_keeps_batch_and_retries_with_same_batch_id(env: None) -> None:
    seen: list[str] = []
    fail = {"on": True}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content)["batch_id"])
        if fail["on"]:
            raise httpx.ConnectError("backend down")
        return httpx.Response(202, json={"status": "accepted"})

    a = make_agent(handler)
    await a.flush()
    assert len(a.buffer) == 1  # kept, not dropped
    first_id = a.buffer[0]["batch_id"]
    fail["on"] = False
    await a.flush()  # sends the buffered batch first, then the new one
    assert len(a.buffer) == 0
    assert seen[0] == seen[1] == first_id  # the retry reused the batch_id → idempotent on the backend
    assert len(set(seen)) == 2 and len(seen) == 3
    await a.client.aclose()


@pytest.mark.parametrize("status", [429, 500, 503])
async def test_backend_busy_keeps_batch_for_retry(env: None, status: int) -> None:
    a = make_agent(lambda request: httpx.Response(status))
    await a.flush()
    await a.flush()
    assert len(a.buffer) == 2  # oldest first, nothing lost
    await a.client.aclose()


async def test_validation_error_drops_the_poison_batch(env: None) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(422, json={"detail": "bad"})

    a = make_agent(handler)
    await a.flush()
    assert len(a.buffer) == 0 and calls["n"] == 1  # retrying identical bytes cannot succeed
    await a.client.aclose()


async def test_buffer_is_bounded_and_reports_drops(env: None) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    a = make_agent(handler)
    for _ in range(MAX_BUFFERED_BATCHES + 5):
        await a.flush()
    assert len(a.buffer) == MAX_BUFFERED_BATCHES
    assert a.dropped_batches == 5
    assert "buffer full" in a.errors["upload_buffer"]
    await a.client.aclose()


def test_sample_counters_emits_nothing_on_first_reading_then_exact_rates(
    env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    readings = iter([
        {"eth0": (1_000, 2_000, 10, 20), "lo": (5, 5, 1, 1)},
        {"eth0": (126_000, 2_000, 110, 20), "lo": (9, 9, 2, 2)},
    ])  # fmt: skip
    clock = iter([100.0, 101.0])
    monkeypatch.setattr(agent_mod.collectors, "nic_counters", lambda: next(readings))
    monkeypatch.setattr(agent_mod.time, "monotonic", lambda: next(clock))
    a = Agent.__new__(Agent)  # avoid constructing an HTTP client / reading the real clock
    a.s = Settings()
    a.interval = 1.0
    a.tracker = agent_mod.CounterTracker(interval_s=1.0)
    a.pending_samples, a.errors, a.capabilities = [], {}, {}

    a.sample_counters()
    net = [s for s in a.pending_samples if s["metric"].startswith("net.")]
    assert net == []  # baseline only: no fabricated first value

    a.pending_samples = []
    a.sample_counters()
    by_metric = {s["metric"]: s for s in a.pending_samples if s["label"] == "eth0"}
    assert by_metric["net.rx_bps"]["value"] == 8 * 125_000 / 1.0
    assert by_metric["net.rx_bps"]["unit"] == "bit/s"
    assert by_metric["net.tx_bps"]["value"] == 0.0
    assert by_metric["net.rx_pps"]["value"] == 100.0
    assert by_metric["net.rx_pps"]["unit"] == "packet/s"
    assert by_metric["net.rx_bps"]["quality"] == "measured"
    assert not [s for s in a.pending_samples if s["label"] == "lo"]  # excluded interface


def test_collector_failure_is_reported_not_hidden(env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom() -> dict[str, Any]:
        raise OSError("no /proc")

    monkeypatch.setattr(agent_mod.collectors, "nic_counters", boom)
    a = Agent.__new__(Agent)
    a.s = Settings()
    a.interval = 1.0
    a.tracker = agent_mod.CounterTracker(interval_s=1.0)
    a.pending_samples, a.errors, a.capabilities = [], {}, {}
    a.sample_counters()
    assert a.pending_samples == []
    assert "nic_counters" in a.errors
    assert a.capabilities["nic_counters"][0] == "unavailable"


def test_batch_shape_matches_wire_contract(env: None) -> None:
    a = make_agent(lambda request: httpx.Response(202))
    a.errors["neighbor_cache"] = "cannot read"
    a.capabilities["nic_counters"] = ("supported", "x")
    batch = a.build_batch()
    assert batch["schema_version"] == 1
    assert batch["agent"]["platform"] == "linux" and batch["agent"]["hostname"] == "test-host"
    assert len(batch["batch_id"]) == 32
    assert batch["heartbeat"]["errors"] == {"neighbor_cache": "cannot read"}
    assert batch["heartbeat"]["capabilities"] == [
        {"name": "nic_counters", "status": "supported", "detail": "x"}
    ]
    assert "posture" not in batch and "inventory" not in batch


def test_agent_uid_is_stable_across_restarts(tmp_path: Any) -> None:
    first = load_agent_uid(str(tmp_path))
    assert first.startswith("lnx-") and load_agent_uid(str(tmp_path)) == first


def test_plain_http_to_remote_host_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OBSERVENET_URL", "http://observenet.example.com")
    monkeypatch.setenv("OBSERVENET_AGENT_TOKEN", "onet_x")
    monkeypatch.delenv("OBSERVENET_ALLOW_PLAINTEXT", raising=False)
    with pytest.raises(SystemExit, match="plain HTTP"):
        Settings()


def test_missing_token_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OBSERVENET_URL", "http://127.0.0.1:8080")
    monkeypatch.delenv("OBSERVENET_AGENT_TOKEN", raising=False)
    monkeypatch.delenv("OBSERVENET_AGENT_TOKEN_FILE", raising=False)
    with pytest.raises(SystemExit, match="TOKEN"):
        Settings()


async def test_discovery_never_sweeps_networks_the_host_is_not_on(
    env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    pinged: list[str] = []

    async def fake_ping(host: str, timeout_s: float) -> Any:
        pinged.append(host)

    async def no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr(agent_mod.collectors, "interfaces", lambda: [
        {"name": "eth0", "addresses": [{"family": "ipv4", "address": "192.168.7.5", "prefix_len": 24}]}])  # fmt: skip
    monkeypatch.setattr(agent_mod.asyncio, "sleep", no_sleep)
    a = make_agent(lambda request: httpx.Response(202))
    monkeypatch.setattr(a.icmp, "ping", fake_ping)

    a.config["discovery"] = {"enabled": False, "cidrs": ["192.168.7.0/30"]}
    await a.maybe_discovery()
    assert pinged == [] and a.capabilities["active_discovery"][0] == "disabled"  # off by default

    a.config["discovery"] = {"enabled": True, "max_probes_per_second": 10,
                             "cidrs": ["8.8.8.0/30", "10.9.9.0/30", "192.168.0.0/16", "192.168.7.0/30"]}  # fmt: skip
    await a.maybe_discovery()
    # public range, unattached private range and an oversized range are all skipped
    assert pinged == ["192.168.7.1", "192.168.7.2"]
    await a.client.aclose()
