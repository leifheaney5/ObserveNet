"""Live fan-out: ONE Redis subscription per API process, coalesced to ~1 Hz frames.

* No database query per browser per second: frames come from pub/sub only.
* Coalescing: within a tick only the newest value per series is kept.
* Backpressure: each client has a small bounded queue; a client that cannot keep up
  is disconnected (it reconnects and resyncs from the snapshot) instead of buffering forever.
* Heartbeat: every tick emits a frame even when idle so the browser can detect stalls.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from datetime import datetime
from typing import Any

from fastapi import WebSocket

from observenet.core.logging import get_logger
from observenet.core.redis import CURRENT_HASH, LIVE_CHANNEL, get_redis
from observenet.core.telemetry import WS_CLIENTS, WS_DROPPED
from observenet.core.time import utcnow

log = get_logger("live")
TICK_SECONDS = 1.0
CLIENT_QUEUE_SIZE = 5
SEND_TIMEOUT = 5.0


class Client:
    def __init__(self, ws: WebSocket, user_id: int) -> None:
        self.ws = ws
        self.user_id = user_id
        self.queue: asyncio.Queue[str] = asyncio.Queue(maxsize=CLIENT_QUEUE_SIZE)
        self.dropped = False


class LiveHub:
    def __init__(self) -> None:
        self.clients: set[Client] = set()
        self._pending: dict[str, dict[str, Any]] = {}
        self._events: list[dict[str, Any]] = []
        self._tasks: list[asyncio.Task[None]] = []
        self.connected_to_redis = False
        self.last_frame_at: datetime | None = None

    async def start(self) -> None:
        self._tasks = [asyncio.create_task(self._subscribe()), asyncio.create_task(self._broadcast())]

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t
        self._tasks = []

    def ingest_frame(self, frame: dict[str, Any]) -> None:
        if frame.get("type") == "metrics":
            sid = frame["source_id"]
            for s in frame["samples"]:
                self._pending[f"{sid}|{s['m']}|{s['l']}"] = {"source_id": sid, **s}
        else:
            self._events.append(frame)

    async def _subscribe(self) -> None:
        backoff = 0.5
        while True:
            try:
                pubsub = get_redis().pubsub()
                await pubsub.subscribe(LIVE_CHANNEL)
                self.connected_to_redis = True
                backoff = 0.5
                async for message in pubsub.listen():
                    if message.get("type") == "message":
                        with contextlib.suppress(ValueError, KeyError):
                            self.ingest_frame(json.loads(message["data"]))
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - reconnect with backoff
                self.connected_to_redis = False
                log.warning("live_subscription_lost", error=type(exc).__name__)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 10.0)

    def build_tick(self) -> str:
        now = utcnow()
        samples, events = list(self._pending.values()), self._events
        self._pending, self._events = {}, []
        frame: dict[str, Any] = {"type": "tick" if samples or events else "heartbeat", "ts": now.isoformat(),
                                 "upstream": self.connected_to_redis}  # fmt: skip
        if samples:
            frame["samples"] = samples
            self.last_frame_at = now
        if events:
            frame["events"] = events
        return json.dumps(frame, default=str)

    async def _broadcast(self) -> None:
        while True:
            await asyncio.sleep(TICK_SECONDS)
            if not self.clients:
                self._pending.clear()
                self._events.clear()
                continue
            text = self.build_tick()
            for client in list(self.clients):
                try:
                    client.queue.put_nowait(text)
                except asyncio.QueueFull:
                    client.dropped = True
                    WS_DROPPED.inc()
                    self.clients.discard(client)

    async def snapshot(self) -> dict[str, Any]:
        try:
            raw = await get_redis().hgetall(CURRENT_HASH)  # type: ignore[misc]
        except Exception:  # noqa: BLE001
            return {}
        out = {}
        for key, value in raw.items():
            with contextlib.suppress(ValueError):
                out[key] = json.loads(value)
        return out

    async def serve(self, client: Client) -> None:
        """Pump frames to one client until it disconnects or is dropped for backpressure."""
        self.clients.add(client)
        WS_CLIENTS.set(len(self.clients))
        try:
            while not client.dropped:
                try:
                    text = await asyncio.wait_for(client.queue.get(), timeout=TICK_SECONDS * 5)
                except TimeoutError:
                    continue
                await asyncio.wait_for(client.ws.send_text(text), timeout=SEND_TIMEOUT)
        finally:
            self.clients.discard(client)
            WS_CLIENTS.set(len(self.clients))


hub = LiveHub()
