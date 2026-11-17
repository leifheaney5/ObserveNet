"""ObserveNet Linux collector: sample → batch → POST, once per second.

Design notes
------------
* Read-only: reads /proc and psutil, sends ICMP echo / TCP handshakes / DNS lookups to a
  short validated target list. It never changes the host and executes nothing the
  backend sends.
* Truthful: a section that cannot be read is reported as ``unavailable`` with the reason.
* Resilient: batches that fail to upload stay in a bounded buffer and are retried with
  the same ``batch_id`` (the backend applies each batch at most once).
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import logging
import os
import signal
import socket
import time
import uuid
from collections import deque
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import psutil

from observenet_agent import __version__, collectors, probes
from observenet_agent.rates import CounterTracker

log = logging.getLogger("observenet.agent")
MAX_BUFFERED_BATCHES = 300
CONFIG_POLL_S = 5.0
MAX_DISCOVERY_ADDRESSES = 1024
SPEEDTEST_MIN_INTERVAL_S = 3 * 3600


def utcnow() -> datetime:
    return datetime.now(UTC)


def load_agent_uid(state_dir: str | None) -> str:
    """Stable per-install identity. Prefers a persisted random id; otherwise derives one from
    the machine id (hashed, so the raw machine id never leaves the host)."""
    if state_dir:
        path = Path(state_dir) / "agent_uid"
        try:
            if path.is_file():
                value = path.read_text().strip()
                if len(value) >= 8:
                    return value
            path.parent.mkdir(parents=True, exist_ok=True)
            value = "lnx-" + uuid.uuid4().hex
            path.write_text(value)
            return value
        except OSError:
            pass
    for candidate in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
        try:
            mid = Path(candidate).read_text().strip()
        except OSError:
            continue
        if mid:
            return "lnx-" + hashlib.sha256(("observenet:" + mid).encode()).hexdigest()[:32]
    return "lnx-" + hashlib.sha256(socket.gethostname().encode()).hexdigest()[:32]


class Settings:
    def __init__(self) -> None:
        env = os.environ
        self.url = env.get("OBSERVENET_URL", "http://127.0.0.1:8080").rstrip("/")
        token = env.get("OBSERVENET_AGENT_TOKEN", "")
        token_file = env.get("OBSERVENET_AGENT_TOKEN_FILE")
        if not token and token_file:
            token = Path(token_file).read_text().strip()
        self.token = token
        self.hostname = env.get("OBSERVENET_AGENT_HOSTNAME") or socket.gethostname()
        self.state_dir = env.get("OBSERVENET_AGENT_STATE_DIR")
        self.docker_api_url = env.get("OBSERVENET_DOCKER_API_URL") or None
        self.exclude_ifaces = tuple(
            p for p in env.get("OBSERVENET_EXCLUDE_INTERFACES", "lo,veth,ifb,dummy").split(",") if p
        )
        self.verify_tls = env.get("OBSERVENET_TLS_VERIFY", "true").lower() != "false"
        if self.url.startswith("http://") and not _is_local_url(self.url) and env.get("OBSERVENET_ALLOW_PLAINTEXT") != "1":
            raise SystemExit(
                "Refusing to send the agent token over plain HTTP to a non-local host. Use https:// or set "
                "OBSERVENET_ALLOW_PLAINTEXT=1 for a trusted local-only demo."
            )
        if not self.token:
            raise SystemExit("OBSERVENET_AGENT_TOKEN (or OBSERVENET_AGENT_TOKEN_FILE) is required")


def _is_local_url(url: str) -> bool:
    host = httpx.URL(url).host
    if host in ("localhost",):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class Agent:
    def __init__(self, settings: Settings) -> None:
        self.s = settings
        self.agent_uid = load_agent_uid(settings.state_dir)
        self.client = httpx.AsyncClient(
            base_url=settings.url, timeout=httpx.Timeout(5.0, connect=3.0), verify=settings.verify_tls,
            headers={"Authorization": f"Bearer {settings.token}", "User-Agent": f"observenet-agent/{__version__}"},
        )  # fmt: skip
        self.interval = 1.0
        self.tracker = CounterTracker(interval_s=self.interval)
        self.pending_samples: list[dict[str, Any]] = []
        self.pending_probes: list[dict[str, Any]] = []
        self.buffer: deque[dict[str, Any]] = deque(maxlen=MAX_BUFFERED_BATCHES)
        self.dropped_batches = 0
        self.errors: dict[str, str] = {}
        self.config: dict[str, Any] = {"probes": [], "inventory_interval_s": 30.0, "posture_interval_s": 300.0,
                                       "posture_requested_at": None, "discovery": {}, "speedtest": {}}  # fmt: skip
        self.icmp = probes.IcmpProber()
        self.probe_tasks: dict[int, asyncio.Task[None]] = {}
        self.probe_specs: dict[int, str] = {}
        self.last_latency: dict[int, float | None] = {}
        self.inventory_due = 0.0
        self.posture_due = 0.0
        self.posture_served_request: str | None = None
        self.pending_inventory: dict[str, Any] | None = None
        self.pending_posture: dict[str, Any] | None = None
        self.last_discovery = 0.0
        self.last_speedtest = 0.0
        self.speedtest_served_request: str | None = None
        self.capabilities: dict[str, tuple[str, str]] = {}
        self.stop = asyncio.Event()
        self.started = time.monotonic()
        psutil.cpu_percent(interval=None)

    # ------------------------------------------------------------ sampling

    def _skip(self, iface: str) -> bool:
        return iface.startswith(self.s.exclude_ifaces)

    def sample_counters(self) -> None:
        now_wall, now_mono = utcnow(), time.monotonic()
        try:
            counters = collectors.nic_counters()
        except Exception as exc:  # noqa: BLE001
            self.errors["nic_counters"] = f"{type(exc).__name__}: {exc}"
            self.capabilities["nic_counters"] = ("unavailable", str(exc))
            return
        self.errors.pop("nic_counters", None)
        self.capabilities["nic_counters"] = ("supported", "psutil / /proc/net/dev cumulative counters, 1 s deltas")
        live = set()
        for iface, (rx_b, tx_b, rx_p, tx_p) in counters.items():
            if self._skip(iface):
                continue
            for metric, value, scale, unit in (
                ("net.rx_bps", rx_b, 8.0, "bit/s"), ("net.tx_bps", tx_b, 8.0, "bit/s"),
                ("net.rx_pps", rx_p, 1.0, "packet/s"), ("net.tx_pps", tx_p, 1.0, "packet/s"),
            ):  # fmt: skip
                key = f"{iface}|{metric}"
                live.add(key)
                rate = self.tracker.update(key, value, now_mono, scale=scale)
                if rate is None:
                    continue  # first reading, reset, or gap: emit nothing rather than a guess
                self.pending_samples.append({"metric": metric, "label": iface, "ts": now_wall.isoformat(),
                                             "value": rate.per_second, "unit": unit,
                                             "interval_s": round(rate.elapsed_s, 3), "quality": "measured"})  # fmt: skip
        self.tracker.forget_missing(live)
        for metric, value in (("host.cpu_pct", psutil.cpu_percent(interval=None)),
                              ("host.mem_pct", psutil.virtual_memory().percent)):  # fmt: skip
            self.pending_samples.append({"metric": metric, "label": "", "ts": now_wall.isoformat(), "value": value,
                                         "unit": "percent", "interval_s": self.interval, "quality": "measured"})  # fmt: skip

    def collect_inventory(self) -> dict[str, Any]:
        gw = collectors.default_gateway()
        neigh = collectors.neighbors()
        self.capabilities["neighbor_cache"] = (
            ("supported", "IPv4 neighbour cache of this host: only devices it recently talked to")
            if neigh.status == "ok" else ("unavailable", neigh.reason or "")
        )  # fmt: skip
        if neigh.status != "ok":
            self.errors["neighbor_cache"] = neigh.reason or "unavailable"
        else:
            self.errors.pop("neighbor_cache", None)
        return {
            "collected_at": utcnow().isoformat(),
            "interfaces": collectors.interfaces(),
            "default_gateway": {"ip": gw[0], "interface": gw[1]} if gw else None,
            "neighbors_status": neigh.status,
            "neighbors": [asdict(n) for n in neigh.items],
        }

    async def collect_posture(self) -> dict[str, Any]:
        listening = collectors.listening_sockets()
        docker = await collectors.docker_published_ports(self.s.docker_api_url)
        self.capabilities["listening_sockets"] = (
            ("supported", listening.method) if listening.status == "ok" else (listening.status, listening.reason or "")
        )
        self.capabilities["docker_published_ports"] = (
            ("supported", docker.method) if docker.status == "ok" else (docker.status, docker.reason or "")
        )
        if docker.status == "unavailable":
            self.errors["docker"] = docker.reason or "unavailable"
        else:
            self.errors.pop("docker", None)
        return {
            "collected_at": utcnow().isoformat(),
            "listening": {"status": listening.status, "reason": listening.reason, "method": listening.method[:120],
                          "items": [asdict(s) for s in listening.items]},
            "docker": {"status": docker.status, "reason": docker.reason, "method": docker.method[:120],
                       "published_ports": docker.items},
        }  # fmt: skip

    # ------------------------------------------------------------ probes

    async def _probe_loop(self, spec: dict[str, Any]) -> None:
        target_id, kind = spec["target_id"], spec["kind"]
        interval, timeout = float(spec["interval_s"]), spec["timeout_ms"] / 1000
        loop = asyncio.get_running_loop()
        next_at = loop.time()
        while not self.stop.is_set():
            gw = collectors.default_gateway()
            try:
                host, _scope = probes.validate_target(kind, spec["host"], spec.get("port"), interval,
                                                      default_gateway=gw[0] if gw else None)  # fmt: skip
            except probes.TargetRejected as exc:
                outcome = probes.ProbeOutcome(False, None, "rejected", None, f"refused by agent policy: {exc}")
            else:
                if kind in ("gateway_icmp", "icmp"):
                    outcome = await self.icmp.ping(host, timeout)
                    status = ("unavailable", self.icmp.unavailable_reason or "") if outcome.method == "unavailable" else (
                        "supported", f"ICMP echo via {self.icmp.mode}")
                    self.capabilities["gateway_latency_probe"] = status
                elif kind == "tcp":
                    outcome = await probes.tcp_probe(host, int(spec["port"]), timeout)
                else:
                    outcome = await probes.dns_probe(host, timeout)
            if outcome.method not in ("unavailable",):  # an unavailable prober is not a lost packet
                prev = self.last_latency.get(target_id)
                jitter = abs(outcome.latency_ms - prev) if outcome.success and outcome.latency_ms is not None and prev is not None else None
                self.last_latency[target_id] = outcome.latency_ms if outcome.success else None
                self.pending_probes.append({
                    "target_id": target_id, "ts": utcnow().isoformat(), "success": outcome.success,
                    "latency_ms": outcome.latency_ms, "jitter_ms": round(jitter, 3) if jitter is not None else None,
                    "method": outcome.method, "resolved_host": outcome.resolved_host, "error": (outcome.error or None) and outcome.error[:300],
                })  # fmt: skip
                self.errors.pop("gateway_probe", None)
            else:
                self.errors["gateway_probe"] = (outcome.error or "unavailable")[:300]
            next_at += interval
            delay = next_at - loop.time()
            if delay < 0:  # fell behind: skip missed slots instead of bursting
                next_at = loop.time() + interval
                delay = interval
            try:
                await asyncio.wait_for(self.stop.wait(), timeout=delay)
            except TimeoutError:
                pass

    def reconcile_probes(self) -> None:
        wanted = {p["target_id"]: p for p in self.config.get("probes", [])}
        for tid in [t for t in self.probe_tasks if t not in wanted or self.probe_specs.get(t) != repr(wanted[t])]:
            self.probe_tasks.pop(tid).cancel()
            self.probe_specs.pop(tid, None)
        for tid, spec in wanted.items():
            if tid not in self.probe_tasks:
                self.probe_specs[tid] = repr(spec)
                self.probe_tasks[tid] = asyncio.create_task(self._probe_loop(spec))

    # ------------------------------------------------------------ opt-in extras

    async def maybe_discovery(self) -> None:
        """Opt-in low-rate ICMP sweep. Only inside private subnets this host is attached to;
        results surface through the neighbour cache, not as a separate claim."""
        cfg = self.config.get("discovery") or {}
        if not cfg.get("enabled"):
            self.capabilities["active_discovery"] = ("disabled", "opt-in; off by default")
            return
        interval = max(15.0, float(cfg.get("interval_minutes", 60))) * 60
        if time.monotonic() - self.last_discovery < interval and self.last_discovery:
            return
        self.last_discovery = time.monotonic()
        attached = []
        for iface in collectors.interfaces():
            for a in iface["addresses"]:
                if a["family"] == "ipv4" and a["prefix_len"]:
                    attached.append(ipaddress.ip_network(f"{a['address']}/{a['prefix_len']}", strict=False))
        rate = min(10.0, max(0.1, float(cfg.get("max_probes_per_second", 2.0))))
        sent = 0
        for cidr in (cfg.get("cidrs") or [])[:4]:
            try:
                net = ipaddress.ip_network(cidr, strict=True)
            except ValueError:
                continue
            if net.version != 4 or not net.is_private or net.num_addresses > MAX_DISCOVERY_ADDRESSES:
                continue
            if not any(net.subnet_of(a) for a in attached):  # type: ignore[arg-type]
                continue  # never sweep a network this host is not directly on
            for host in net.hosts():
                if self.stop.is_set():
                    return
                await self.icmp.ping(str(host), 0.3)
                sent += 1
                await asyncio.sleep(1.0 / rate)
        self.capabilities["active_discovery"] = ("supported", f"last sweep sent {sent} echo requests at ≤{rate:g}/s")
        self.inventory_due = 0.0  # read the neighbour cache right after the sweep

    async def maybe_speedtest(self) -> None:
        """Opt-in capacity test: bounded HTTPS download, at most every 3 h unless requested."""
        cfg = self.config.get("speedtest") or {}
        if not cfg.get("enabled") or not str(cfg.get("download_url", "")).startswith("https://"):
            self.capabilities["capacity_test"] = ("disabled", "opt-in; off by default")
            return
        now = time.monotonic()
        requested = cfg.get("requested_at")
        on_demand = bool(requested) and requested != self.speedtest_served_request and (
            not self.last_speedtest or now - self.last_speedtest > 600)
        interval = max(SPEEDTEST_MIN_INTERVAL_S, float(cfg.get("interval_hours", 6)) * 3600)
        scheduled = not self.last_speedtest or now - self.last_speedtest >= interval
        if not (on_demand or scheduled):
            return
        self.last_speedtest = now
        self.speedtest_served_request = requested
        limit = min(int(cfg.get("max_bytes", 25_000_000)), 500_000_000)
        received, start = 0, time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=5.0), follow_redirects=False) as c:
                async with c.stream("GET", cfg["download_url"]) as resp:
                    resp.raise_for_status()
                    async for chunk in resp.aiter_bytes():
                        received += len(chunk)
                        if received >= limit or time.perf_counter() - start > 15:
                            break
        except httpx.HTTPError as exc:
            self.errors["capacity_test"] = type(exc).__name__
            return
        elapsed = time.perf_counter() - start
        self.errors.pop("capacity_test", None)
        if received < 1_000_000 or elapsed <= 0:
            return  # too little data to call it a capacity measurement
        self.pending_samples.append({"metric": "speedtest.download_bps", "label": httpx.URL(cfg["download_url"]).host,
                                     "ts": utcnow().isoformat(), "value": 8 * received / elapsed, "unit": "bit/s",
                                     "interval_s": interval, "quality": "measured"})  # fmt: skip
        self.capabilities["capacity_test"] = ("supported", "single-stream HTTPS download; a lower bound on capacity")

    # ------------------------------------------------------------ transport

    def build_batch(self) -> dict[str, Any]:
        now = utcnow()
        batch = {
            "schema_version": 1,
            "agent": {"agent_uid": self.agent_uid, "hostname": self.s.hostname[:255], "platform": "linux",
                      "agent_version": __version__},
            "batch_id": uuid.uuid4().hex,
            "sent_at": now.isoformat(),
            "samples": self.pending_samples,
            "probe_results": self.pending_probes,
            "heartbeat": {
                "ts": now.isoformat(), "cpu_pct": psutil.cpu_percent(interval=None),
                "mem_pct": psutil.virtual_memory().percent, "uptime_s": round(time.monotonic() - self.started, 1),
                "errors": {k: v[:500] for k, v in self.errors.items()},
                "capabilities": [{"name": k, "status": v[0], "detail": v[1][:500]} for k, v in sorted(self.capabilities.items())],
            },
        }  # fmt: skip
        if self.pending_inventory is not None:
            batch["inventory"], self.pending_inventory = self.pending_inventory, None
        if self.pending_posture is not None:
            batch["posture"], self.pending_posture = self.pending_posture, None
        self.pending_samples, self.pending_probes = [], []
        return batch

    async def flush(self) -> None:
        if len(self.buffer) == self.buffer.maxlen:
            self.dropped_batches += 1
            self.errors["upload_buffer"] = f"buffer full; {self.dropped_batches} oldest batch(es) dropped"
        self.buffer.append(self.build_batch())
        while self.buffer:
            batch = self.buffer[0]
            try:
                resp = await self.client.post("/api/v1/ingest/agent", json=batch)
            except httpx.HTTPError as exc:
                log.warning("upload failed (%s); %d batch(es) buffered", type(exc).__name__, len(self.buffer))
                return
            if resp.status_code in (200, 202):
                self.buffer.popleft()
                continue
            if resp.status_code in (429, 500, 502, 503, 504):
                log.warning("backend busy (%s); %d batch(es) buffered", resp.status_code, len(self.buffer))
                return
            # 4xx: retrying the same payload cannot succeed. Drop it and say why.
            log.error("batch rejected (%s): %s", resp.status_code, resp.text[:300])
            self.buffer.popleft()
            if resp.status_code in (401, 403):
                self.buffer.clear()
                await asyncio.sleep(10)
                return

    async def poll_config(self) -> None:
        while not self.stop.is_set():
            try:
                resp = await self.client.get("/api/v1/agent/config")
                if resp.status_code == 200:
                    self.config = resp.json()
                    self.reconcile_probes()
                    requested = self.config.get("posture_requested_at")
                    if requested and requested != self.posture_served_request:
                        self.posture_served_request = requested
                        self.posture_due = 0.0  # backend asked for fresh evidence (Verify)
            except httpx.HTTPError:
                pass
            try:
                await asyncio.wait_for(self.stop.wait(), timeout=CONFIG_POLL_S)
            except TimeoutError:
                pass

    # ------------------------------------------------------------ main loop

    async def run(self) -> None:
        log.info("agent %s starting; uid=%s backend=%s", __version__, self.agent_uid, self.s.url)
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self.stop.set)
        config_task = asyncio.create_task(self.poll_config())
        extras: asyncio.Task[None] | None = None
        tick = 0
        start = loop.time()
        while not self.stop.is_set():
            self.sample_counters()
            mono = time.monotonic()
            if mono >= self.inventory_due:
                self.inventory_due = mono + float(self.config.get("inventory_interval_s", 30.0))
                self.pending_inventory = self.collect_inventory()
            if mono >= self.posture_due:
                self.posture_due = mono + float(self.config.get("posture_interval_s", 300.0))
                self.pending_posture = await self.collect_posture()
            if extras is None or extras.done():
                extras = asyncio.create_task(self._extras())
            await self.flush()
            tick += 1
            delay = start + tick * self.interval - loop.time()
            if delay < 0:  # overran: resynchronise instead of spinning
                tick = int((loop.time() - start) / self.interval) + 1
                delay = start + tick * self.interval - loop.time()
            try:
                await asyncio.wait_for(self.stop.wait(), timeout=delay)
            except TimeoutError:
                pass
        config_task.cancel()
        for t in self.probe_tasks.values():
            t.cancel()
        await self.client.aclose()

    async def _extras(self) -> None:
        await self.maybe_discovery()
        await self.maybe_speedtest()
