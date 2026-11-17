"""Live fan-out: coalescing, heartbeat, backpressure and WebSocket handshake authorization."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from observenet.core.redis import CURRENT_HASH, LIVE_CHANNEL, get_redis
from observenet.live import hub as hub_module
from observenet.live.hub import CLIENT_QUEUE_SIZE, Client, LiveHub


class FakeWebSocket:
    def __init__(self, *, stall: bool = False) -> None:
        self.sent: list[str] = []
        self.stall = stall

    async def send_text(self, text: str) -> None:
        if self.stall:
            await asyncio.Event().wait()
        self.sent.append(text)


def metrics_frame(source_id: int, *samples: tuple[str, str, float]) -> dict[str, Any]:
    return {"type": "metrics", "source_id": source_id, "ts": "t",
            "samples": [{"m": m, "l": label, "v": v, "u": "bit/s", "t": f"ts-{v}", "q": "measured"} for m, label, v in samples]}  # fmt: skip


def test_coalescing_keeps_only_the_newest_value_per_series_per_tick() -> None:
    hub = LiveHub()
    hub.ingest_frame(metrics_frame(1, ("net.rx_bps", "eth0", 100.0), ("net.tx_bps", "eth0", 5.0)))
    hub.ingest_frame(metrics_frame(1, ("net.rx_bps", "eth0", 200.0)))
    hub.ingest_frame(metrics_frame(1, ("net.rx_bps", "eth0", 300.0)))
    hub.ingest_frame(
        metrics_frame(2, ("net.rx_bps", "eth0", 7.0))
    )  # same metric, different source = different series
    frame = json.loads(hub.build_tick())
    assert frame["type"] == "tick"
    values = {(s["source_id"], s["m"], s["l"]): s["v"] for s in frame["samples"]}
    assert values == {
        (1, "net.rx_bps", "eth0"): 300.0,
        (1, "net.tx_bps", "eth0"): 5.0,
        (2, "net.rx_bps", "eth0"): 7.0,
    }
    assert len(frame["samples"]) == 3  # 5 samples in, 3 series out


def test_idle_tick_is_a_heartbeat_and_state_is_cleared_between_ticks() -> None:
    hub = LiveHub()
    idle = json.loads(hub.build_tick())
    assert idle["type"] == "heartbeat" and "samples" not in idle and "events" not in idle and idle["ts"]
    assert idle["upstream"] is False  # tells the browser the Redis subscription is down
    hub.ingest_frame(metrics_frame(1, ("net.rx_bps", "eth0", 1.0)))
    assert json.loads(hub.build_tick())["type"] == "tick"
    assert json.loads(hub.build_tick())["type"] == "heartbeat"  # nothing new since the last tick


def test_alert_events_are_passed_through_in_order() -> None:
    hub = LiveHub()
    hub.ingest_frame({"type": "alert", "event": "fired", "id": 1})
    hub.ingest_frame({"type": "alert", "event": "resolved", "id": 1})
    frame = json.loads(hub.build_tick())
    assert frame["type"] == "tick" and [e["event"] for e in frame["events"]] == ["fired", "resolved"]


async def test_slow_client_is_dropped_while_healthy_clients_keep_receiving(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(hub_module, "TICK_SECONDS", 0.01)
    hub = LiveHub()
    healthy_ws, slow_ws = FakeWebSocket(), FakeWebSocket(stall=True)
    healthy, slow = Client(healthy_ws, 1), Client(slow_ws, 2)  # type: ignore[arg-type]
    serving = [asyncio.create_task(hub.serve(healthy)), asyncio.create_task(hub.serve(slow))]
    broadcaster = asyncio.create_task(hub._broadcast())
    try:
        for _ in range(200):
            await asyncio.sleep(0.01)
            if slow.dropped and len(healthy_ws.sent) > CLIENT_QUEUE_SIZE + 3:
                break
        assert slow.dropped is True  # its bounded queue filled up: disconnected instead of buffering forever
        assert slow not in hub.clients and healthy in hub.clients
        assert slow.queue.qsize() <= CLIENT_QUEUE_SIZE
        assert len(healthy_ws.sent) > CLIENT_QUEUE_SIZE + 3 and healthy.dropped is False
        assert all(json.loads(t)["type"] == "heartbeat" for t in healthy_ws.sent)
    finally:
        for task in (*serving, broadcaster):
            task.cancel()
        await asyncio.gather(*serving, broadcaster, return_exceptions=True)
    assert hub.clients == set()


async def test_no_work_is_done_when_nobody_is_connected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hub_module, "TICK_SECONDS", 0.01)
    hub = LiveHub()
    hub.ingest_frame(metrics_frame(1, ("net.rx_bps", "eth0", 1.0)))
    task = asyncio.create_task(hub._broadcast())
    await asyncio.sleep(0.05)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert hub._pending == {} and hub._events == []  # nothing accumulates without clients


async def test_one_redis_subscription_feeds_the_hub() -> None:
    hub = LiveHub()
    await hub.start()
    try:
        for _ in range(100):
            if hub.connected_to_redis:
                break
            await asyncio.sleep(0.01)
        assert hub.connected_to_redis is True
        await get_redis().publish(LIVE_CHANNEL, json.dumps(metrics_frame(3, ("net.rx_bps", "eth0", 42.0))))
        await get_redis().publish(
            LIVE_CHANNEL, "not json"
        )  # garbage on the channel must not kill the subscriber
        await get_redis().publish(LIVE_CHANNEL, json.dumps({"type": "alert", "event": "fired", "id": 9}))
        for _ in range(100):
            if hub._events:
                break
            await asyncio.sleep(0.01)
        frame = json.loads(hub.build_tick())
        assert (
            frame["samples"][0]["v"] == 42.0 and frame["events"][0]["id"] == 9 and frame["upstream"] is True
        )
    finally:
        await hub.stop()


async def test_snapshot_reads_current_values_and_survives_bad_entries() -> None:
    await get_redis().hset(
        CURRENT_HASH,
        mapping={"1|net.rx_bps|eth0": json.dumps({"v": 1.5, "t": "x", "u": "bit/s"}), "bad": "{nope"},
    )
    assert await LiveHub().snapshot() == {"1|net.rx_bps|eth0": {"v": 1.5, "t": "x", "u": "bit/s"}}


# ---------------------------------------------------------------- WebSocket handshake (driven at ASGI level)


class WsSession:
    def __init__(self, app: Any, headers: dict[str, str]) -> None:
        self.sent: list[dict[str, Any]] = []
        self._incoming: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._incoming.put_nowait({"type": "websocket.connect"})
        scope = {
            "type": "websocket", "asgi": {"version": "3.0", "spec_version": "2.3"}, "scheme": "ws", "http_version": "1.1",
            "path": "/api/v1/stream", "raw_path": b"/api/v1/stream", "query_string": b"", "root_path": "",
            "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
            "client": ("127.0.0.1", 50000), "server": ("testserver", 80), "subprotocols": [], "state": {},
        }  # fmt: skip
        self.task = asyncio.create_task(app(scope, self._receive, self._send))

    async def _receive(self) -> dict[str, Any]:
        return await self._incoming.get()

    async def _send(self, message: dict[str, Any]) -> None:
        self.sent.append(message)

    async def until(self, predicate: Any, limit_s: float = 5.0) -> None:
        async with asyncio.timeout(limit_s):
            while not predicate(self.sent):
                if self.task.done() and not predicate(self.sent):
                    self.task.result()
                    raise AssertionError(f"connection ended early: {self.sent}")
                await asyncio.sleep(0.01)

    async def disconnect(self) -> None:
        self._incoming.put_nowait({"type": "websocket.disconnect", "code": 1000})
        await asyncio.wait_for(self.task, 5)

    def types(self) -> list[str]:
        return [m["type"] for m in self.sent]


def closed(sent: list[dict[str, Any]]) -> bool:
    return any(m["type"] == "websocket.close" for m in sent)


GOOD = {"host": "testserver", "origin": "http://testserver"}


@pytest.mark.parametrize(
    "headers",
    [
        {"host": "testserver"},  # no Origin at all
        {"host": "testserver", "origin": "https://evil.example"},
        {"host": "testserver", "origin": "http://testserver.evil.example"},
        {"host": "testserver", "origin": "null"},
    ],
)
async def test_websocket_rejects_missing_or_foreign_origin(
    app: Any, admin_client: httpx.AsyncClient, headers: dict[str, str]
) -> None:
    cookie = f"observenet_session={admin_client.cookies['observenet_session']}"  # even with a valid session
    ws = WsSession(app, {**headers, "cookie": cookie})
    await ws.until(closed)
    await asyncio.wait_for(ws.task, 5)
    assert "websocket.accept" not in ws.types()
    assert ws.sent[-1]["code"] == 4403


@pytest.mark.parametrize("cookie", [None, "observenet_session=forged-value", "other=1"])
async def test_websocket_rejects_missing_or_invalid_session(
    app: Any, admin_client: httpx.AsyncClient, cookie: str | None
) -> None:
    headers = {**GOOD, **({"cookie": cookie} if cookie else {})}
    ws = WsSession(app, headers)
    await ws.until(closed)
    await asyncio.wait_for(ws.task, 5)
    assert "websocket.accept" not in ws.types() and "websocket.send" not in ws.types()
    assert ws.sent[-1]["code"] == 4401


async def test_websocket_stream_with_valid_session_sends_hello_snapshot_then_ticks(
    app: Any, admin_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(hub_module, "TICK_SECONDS", 0.02)
    await get_redis().hset(
        CURRENT_HASH, "1|net.rx_bps|eth0", json.dumps({"v": 9.0, "t": "x", "u": "bit/s", "q": "measured"})
    )
    await hub_module.hub.start()
    ws = WsSession(
        app, {**GOOD, "cookie": f"observenet_session={admin_client.cookies['observenet_session']}"}
    )
    try:
        await ws.until(lambda sent: any(m["type"] == "websocket.send" for m in sent))
        assert ws.types()[0] == "websocket.accept"
        hello = json.loads(next(m for m in ws.sent if m["type"] == "websocket.send")["text"])
        assert hello["type"] == "hello" and hello["demo_mode"] is False and hello["tick_s"] == 1
        assert hello["snapshot"] == {"1|net.rx_bps|eth0": {"v": 9.0, "t": "x", "u": "bit/s", "q": "measured"}}

        # a frame published by the ingest path reaches the browser through the shared subscription
        for _ in range(100):
            if hub_module.hub.connected_to_redis:
                break
            await asyncio.sleep(0.01)
        await get_redis().publish(LIVE_CHANNEL, json.dumps(metrics_frame(1, ("net.rx_bps", "eth0", 123.0))))

        def got_tick(sent: list[dict[str, Any]]) -> bool:
            return any(
                m["type"] == "websocket.send" and json.loads(m["text"]).get("type") == "tick" for m in sent
            )

        await ws.until(got_tick)
        frames = [json.loads(m["text"]) for m in ws.sent if m["type"] == "websocket.send"]
        tick = next(f for f in frames if f["type"] == "tick")
        assert tick["samples"] == [
            {
                "source_id": 1,
                "m": "net.rx_bps",
                "l": "eth0",
                "v": 123.0,
                "u": "bit/s",
                "t": "ts-123.0",
                "q": "measured",
            }
        ]
        await ws.until(
            lambda sent: any(
                m["type"] == "websocket.send" and json.loads(m["text"]).get("type") == "heartbeat"
                for m in sent
            )
        )
        assert len(hub_module.hub.clients) == 1
    finally:
        await ws.disconnect()
        await hub_module.hub.stop()
    assert hub_module.hub.clients == set()  # cleaned up on disconnect
