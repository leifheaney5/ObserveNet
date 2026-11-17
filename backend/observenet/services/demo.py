"""DEMO_MODE synthetic data generator.

Everything it emits is flagged ``quality="synthetic"`` and belongs to ``is_demo`` sources,
flows through the *real* ingest → queue → worker → rules → alerts pipeline, and is purged
automatically the next time the server starts in real mode. It never runs in real mode.
"""

from __future__ import annotations

import asyncio
import math
import random
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select

from observenet.auth import service as auth_service
from observenet.auth.crypto import new_agent_token
from observenet.core.config import get_config
from observenet.core.db import session_scope
from observenet.core.logging import get_logger
from observenet.core.redis import get_redis
from observenet.core.time import utcnow
from observenet.models import ApiToken, DataSource, ProbeTarget
from observenet.schemas.ingest import IngestPayload
from observenet.services import ingest as ingest_service

log = get_logger("demo")
DEMO_STATE_KEY = "observenet:demo:fixed"
FIXABLE = {
    "windows-firewall": "Re-enable the Public firewall profile on demo-windows-pc",
    "smb1": "Disable SMBv1 on demo-windows-pc",
    "redis-bind": "Bind Redis to 127.0.0.1 on demo-linux-server",
}
# 00:00:5E:00:53:xx is the IANA documentation MAC range (RFC 7042): guaranteed not to be a real device.
LAN = [
    ("192.168.50.1", "00:00:5e:00:53:01"), ("192.168.50.10", "00:00:5e:00:53:10"), ("192.168.50.21", "00:00:5e:00:53:21"),
    ("192.168.50.22", "00:00:5e:00:53:22"), ("192.168.50.31", "00:00:5e:00:53:31"), ("192.168.50.40", "00:00:5e:00:53:40"),
    ("192.168.50.41", "00:00:5e:00:53:41"), ("192.168.50.55", "7a:11:22:33:44:55"), ("192.168.50.60", "00:00:5e:00:53:60"),
]  # fmt: skip
AGENTS = [
    {"uid": "demo-gateway-0001", "hostname": "demo-gateway", "emulates": "router"},
    {"uid": "demo-linux-0001", "hostname": "demo-linux-server", "emulates": "linux"},
    {"uid": "demo-windows-0001", "hostname": "demo-windows-pc", "emulates": "windows"},
]


def _wave(t: float, base: float, swing: float, period: float, noise: float, rng: random.Random) -> float:
    return max(0.0, base + swing * math.sin(2 * math.pi * t / period) + rng.gauss(0, noise))


class DemoGenerator:
    def __init__(self, seed: int = 7) -> None:
        self.rng = random.Random(seed)  # noqa: S311 - synthetic demo data, not security
        self.tokens: dict[str, ApiToken] = {}
        self.targets: dict[str, int] = {}
        self.tick = 0
        self.burst_until = 0
        self.prev_latency: float | None = None

    async def setup(self) -> None:
        async with session_scope() as session:
            for spec in (
                {"name": "Demo gateway", "kind": "gateway_icmp", "scope": "gateway", "host": "auto", "interval_s": 1.0},
                {"name": "Demo external A (TCP 443)", "kind": "tcp", "scope": "external", "host": "1.1.1.1", "port": 443},
                {"name": "Demo external B (TCP 443)", "kind": "tcp", "scope": "external", "host": "9.9.9.9", "port": 443},
            ):
                row = (await session.execute(select(ProbeTarget).where(ProbeTarget.name == spec["name"]))).scalar_one_or_none()
                if row is None:
                    row = ProbeTarget(**spec, is_demo=True)
                    session.add(row)
                    await session.flush()
                self.targets[spec["name"]] = row.id
            for agent in AGENTS:
                source = (await session.execute(select(DataSource).where(DataSource.agent_uid == agent["uid"]))).scalar_one_or_none()
                token = None
                if source is not None:
                    token = (await session.execute(select(ApiToken).where(ApiToken.source_id == source.id))).scalars().first()
                if token is None:
                    _, token = await auth_service.issue_agent_token(
                        session, name=f"demo generator ({agent['hostname']})", created_by=None, ttl_days=None,
                        raw=new_agent_token(),
                    )  # fmt: skip
                    if source is not None:
                        token.source_id = source.id
                self.tokens[agent["uid"]] = token

    async def fixed(self) -> set[str]:
        try:
            return set(await get_redis().smembers(DEMO_STATE_KEY))  # type: ignore[misc]
        except Exception:  # noqa: BLE001
            return set()

    def _base(self, agent: dict[str, str], now: datetime) -> dict[str, Any]:
        return {
            "schema_version": 1, "batch_id": uuid.uuid4().hex, "sent_at": now,
            "agent": {"agent_uid": agent["uid"], "hostname": agent["hostname"], "platform": "demo",
                      "emulates": agent["emulates"], "agent_version": "demo"},
            "heartbeat": {"ts": now, "cpu_pct": round(self.rng.uniform(3, 18), 1), "mem_pct": round(self.rng.uniform(30, 45), 1),
                          "uptime_s": 86400 + self.tick, "errors": {}, "capabilities": []},
        }  # fmt: skip

    def _sample(self, metric: str, label: str, now: datetime, value: float, unit: str) -> dict[str, Any]:
        return {"metric": metric, "label": label, "ts": now, "value": round(value, 3), "unit": unit, "quality": "synthetic"}

    async def payloads(self, now: datetime, posture_requested: set[str]) -> list[tuple[str, IngestPayload]]:
        t, rng = float(self.tick), self.rng
        if rng.random() < 0.01:
            self.burst_until = self.tick + rng.randint(15, 60)
        burst = 4.0 if self.tick < self.burst_until else 1.0
        fixed = await self.fixed()
        out: list[tuple[str, IngestPayload]] = []

        # --- gateway: WAN counters + per-device accounting (the thing real mode needs a router for)
        gw = self._base(AGENTS[0], now)
        down = _wave(t, 38e6, 22e6, 900, 5e6, rng) * burst
        up = _wave(t, 6e6, 3e6, 700, 1e6, rng) * (burst**0.5)
        gw["samples"] = [self._sample("wan.rx_bps", "wan0", now, down, "bit/s"), self._sample("wan.tx_bps", "wan0", now, up, "bit/s")]
        shares = [0.34, 0.22, 0.16, 0.11, 0.09, 0.05, 0.03]
        for (_ip, mac), share in zip(LAN[1:8], shares, strict=True):
            jitter = max(0.1, rng.gauss(1, 0.25))
            gw["samples"].append(self._sample("device.rx_bps", mac, now, down * share * jitter, "bit/s"))
            gw["samples"].append(self._sample("device.tx_bps", mac, now, up * share * jitter, "bit/s"))
        gw["heartbeat"]["capabilities"] = [
            {"name": "wan_interface_counters", "status": "supported", "detail": "SYNTHETIC demo gateway"},
            {"name": "per_device_traffic", "status": "supported", "detail": "SYNTHETIC demo gateway"},
        ]
        out.append((AGENTS[0]["uid"], IngestPayload.model_validate(gw)))

        # --- linux server: NIC counters, probes, inventory, posture
        lx = self._base(AGENTS[1], now)
        lx["samples"] = [
            self._sample("net.rx_bps", "eth0", now, _wave(t, 4e6, 2.5e6, 600, 8e5, rng) * burst, "bit/s"),
            self._sample("net.tx_bps", "eth0", now, _wave(t, 1.2e6, 8e5, 500, 3e5, rng), "bit/s"),
            self._sample("host.cpu_pct", "", now, lx["heartbeat"]["cpu_pct"], "percent"),
            self._sample("host.mem_pct", "", now, lx["heartbeat"]["mem_pct"], "percent"),
        ]
        latency = max(0.3, rng.gauss(2.4, 0.5) + (rng.uniform(15, 60) if burst > 1 and rng.random() < 0.3 else 0))
        lost = rng.random() < (0.04 if burst > 1 else 0.003)
        probes = [{
            "target_id": self.targets["Demo gateway"], "ts": now, "success": not lost,
            "latency_ms": None if lost else round(latency, 2), "method": "synthetic", "resolved_host": "192.168.50.1",
            "jitter_ms": None if lost or self.prev_latency is None else round(abs(latency - self.prev_latency), 2),
            "error": "timeout" if lost else None,
        }]  # fmt: skip
        self.prev_latency = None if lost else latency
        if self.tick % 30 == 0:
            for name in ("Demo external A (TCP 443)", "Demo external B (TCP 443)"):
                probes.append({"target_id": self.targets[name], "ts": now, "success": True,
                               "latency_ms": round(rng.gauss(14, 3), 2), "method": "synthetic"})  # fmt: skip
        lx["probe_results"] = probes
        lx["heartbeat"]["capabilities"] = [
            {"name": "nic_counters", "status": "supported", "detail": "SYNTHETIC"},
            {"name": "neighbor_cache", "status": "supported", "detail": "SYNTHETIC"},
            {"name": "listening_sockets", "status": "supported", "detail": "SYNTHETIC"},
            {"name": "docker_published_ports", "status": "disabled", "detail": "not enabled in the demo"},
        ]
        if self.tick % 30 == 0:
            present = [n for i, n in enumerate(LAN) if i < 7 or (self.tick // 300 + i) % 2 == 0]
            lx["inventory"] = {
                "collected_at": now,
                "interfaces": [{"name": "eth0", "mac": "00:00:5e:00:53:a0", "is_up": True, "link_speed_mbps": 1000, "mtu": 1500,
                                "addresses": [{"family": "ipv4", "address": "192.168.50.5", "prefix_len": 24}]}],
                "default_gateway": {"ip": "192.168.50.1", "interface": "eth0"},
                "neighbors_status": "ok",
                "neighbors": [{"ip": ip, "mac": mac, "interface": "eth0", "state": "reachable"} for ip, mac in present],
            }  # fmt: skip
        if self.tick % 60 == 0 or AGENTS[1]["uid"] in posture_requested:
            redis_addr = "127.0.0.1" if "redis-bind" in fixed else "0.0.0.0"  # noqa: S104 - synthetic posture
            lx["posture"] = {
                "collected_at": now,
                "listening": {"status": "ok", "method": "SYNTHETIC socket table", "items": [
                    {"proto": "tcp", "address": "0.0.0.0", "port": 22, "process": "sshd"},
                    {"proto": "tcp", "address": redis_addr, "port": 6379, "process": "redis-server"},
                    {"proto": "tcp", "address": "0.0.0.0", "port": 8096, "process": "mediaserver"},
                    {"proto": "tcp", "address": "127.0.0.1", "port": 5432, "process": "postgres"},
                ]},
                "docker": {"status": "disabled", "reason": "not enabled in the demo"},
            }  # fmt: skip
        out.append((AGENTS[1]["uid"], IngestPayload.model_validate(lx)))

        # --- windows pc: posture only + its own NIC
        if self.tick % 5 == 0 or AGENTS[2]["uid"] in posture_requested:
            win = self._base(AGENTS[2], now)
            win["samples"] = [self._sample("net.rx_bps", "Ethernet", now, _wave(t, 9e6, 6e6, 800, 1e6, rng), "bit/s"),
                              self._sample("net.tx_bps", "Ethernet", now, _wave(t, 9e5, 4e5, 650, 1e5, rng), "bit/s")]  # fmt: skip
            for s in win["samples"]:
                s["interval_s"] = 5.0
            win["heartbeat"]["capabilities"] = [
                {"name": "nic_counters", "status": "supported", "detail": "SYNTHETIC"},
                {"name": "windows_firewall", "status": "supported", "detail": "SYNTHETIC"},
                {"name": "listening_sockets", "status": "supported", "detail": "SYNTHETIC"},
            ]
            if self.tick % 60 == 0 or AGENTS[2]["uid"] in posture_requested:
                win["posture"] = {
                    "collected_at": now,
                    "firewall": {"status": "ok", "method": "SYNTHETIC Get-NetFirewallProfile", "profiles": [
                        {"name": "Domain", "enabled": True}, {"name": "Private", "enabled": True},
                        {"name": "Public", "enabled": "windows-firewall" in fixed}]},
                    "smb": {"status": "ok", "method": "SYNTHETIC Get-SmbServerConfiguration",
                            "smb1_enabled": "smb1" not in fixed, "smb2_enabled": True},
                    "listening": {"status": "ok", "method": "SYNTHETIC Get-NetTCPConnection", "items": [
                        {"proto": "tcp", "address": "0.0.0.0", "port": 445, "process": "System"},
                        {"proto": "tcp", "address": "0.0.0.0", "port": 135, "process": "svchost"}]},
                }  # fmt: skip
            if self.tick % 60 == 0:
                win["inventory"] = {
                    "collected_at": now, "neighbors_status": "unsupported", "neighbors": [],
                    "interfaces": [{"name": "Ethernet", "mac": "00:00:5e:00:53:10", "is_up": True, "link_speed_mbps": 1000,
                                    "addresses": [{"family": "ipv4", "address": "192.168.50.10", "prefix_len": 24}]}],
                }  # fmt: skip
            out.append((AGENTS[2]["uid"], IngestPayload.model_validate(win)))
        self.tick += 1
        return out


async def run_demo_generator(stop: asyncio.Event) -> None:
    if not get_config().demo_mode:
        return
    from observenet.workers import tasks

    async def enqueue(source_id: int, payload: dict[str, Any], received_at: str) -> None:
        await asyncio.to_thread(tasks.process_ingest.send, source_id, payload, received_at)

    gen = DemoGenerator()
    await gen.setup()
    log.warning("demo_generator_started", note="ALL DATA IS SYNTHETIC")
    loop = asyncio.get_running_loop()
    next_tick = loop.time()
    while not stop.is_set():
        now = utcnow()
        try:
            async with session_scope() as session:
                requested = set(
                    (await session.execute(select(DataSource.agent_uid).where(
                        DataSource.is_demo.is_(True), DataSource.posture_requested_at.is_not(None)))).scalars()  # fmt: skip
                )
            for uid, payload in await gen.payloads(now, requested):
                async with session_scope() as session:
                    token = await session.merge(gen.tokens[uid])
                    await ingest_service.accept(session, get_redis(), token=token, payload=payload, enqueue=enqueue, now=now)
        except Exception:  # noqa: BLE001
            log.exception("demo_tick_failed")
        next_tick += 1.0
        try:
            await asyncio.wait_for(stop.wait(), timeout=max(0.0, next_tick - loop.time()))
        except TimeoutError:
            pass
