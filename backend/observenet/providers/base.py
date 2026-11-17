"""Gateway/router provider layer.

A provider is a *read-only* adapter to something that sees the whole network. Each one
declares what it can report through capability negotiation; anything not declared is
``unsupported`` and the UI says so. Adapters are only added for public, documented,
tested interfaces — there are no speculative vendor integrations here.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class RouterCapability(StrEnum):
    DEVICE_INVENTORY = "device_inventory"
    DHCP_LEASES = "dhcp_leases"
    ARP_TABLE = "arp_table"
    WAN_COUNTERS = "wan_interface_counters"
    WIFI_CLIENTS = "wifi_clients_signal"
    PER_DEVICE_TRAFFIC = "per_device_traffic"
    FLOWS = "flow_records"
    ROUTER_SETTINGS = "router_settings_readonly"


class SourceQuality(StrEnum):
    AUTHORITATIVE = "authoritative"  # the device that forwards the traffic / owns the table
    POINT_IN_TIME = "point_in_time"  # a manual export; stale from the moment it is taken
    PARTIAL = "partial"


@dataclass(frozen=True)
class CapabilityReport:
    capability: RouterCapability
    status: str  # supported | unsupported | unavailable
    detail: str
    quality: SourceQuality | None = None
    sample_interval_s: float | None = None
    coverage: str | None = None


@dataclass(frozen=True)
class ClientRecord:
    ip: str
    mac: str | None
    hostname: str | None
    observed_at: datetime


@dataclass(frozen=True)
class WanCounters:
    interface: str
    rx_bytes: int
    tx_bytes: int
    counter_bits: int
    observed_at: datetime


class UnsupportedCapability(Exception):
    """Raised instead of returning invented data."""


class RouterTelemetryProvider(ABC):
    name: str = "unnamed"
    observation_point: str = "gateway"

    @abstractmethod
    def capabilities(self) -> list[CapabilityReport]: ...

    def supports(self, capability: RouterCapability) -> bool:
        return any(c.capability == capability and c.status == "supported" for c in self.capabilities())

    async def read_clients(self) -> list[ClientRecord]:
        raise UnsupportedCapability(RouterCapability.DEVICE_INVENTORY)

    async def read_wan_counters(self) -> WanCounters:
        raise UnsupportedCapability(RouterCapability.WAN_COUNTERS)

    async def read_per_device_counters(self) -> dict[str, tuple[int, int]]:
        raise UnsupportedCapability(RouterCapability.PER_DEVICE_TRAFFIC)

    async def read_wifi_clients(self) -> list[dict[str, object]]:
        raise UnsupportedCapability(RouterCapability.WIFI_CLIENTS)

    async def read_settings(self) -> dict[str, object]:
        raise UnsupportedCapability(RouterCapability.ROUTER_SETTINGS)


class NullRouterProvider(RouterTelemetryProvider):
    """Used when no gateway is configured: everything is honestly unsupported."""

    name = "none"

    def capabilities(self) -> list[CapabilityReport]:
        return [CapabilityReport(c, "unsupported", "No gateway provider is configured.") for c in RouterCapability]


@dataclass(frozen=True)
class PlannedAdapter:
    """Documented extension points. Not implemented: listed so the UI can say so plainly."""

    key: str
    title: str
    would_provide: list[RouterCapability] = field(default_factory=list)
    blocked_on: str = ""


PLANNED_ADAPTERS = [
    PlannedAdapter("unifi", "UniFi Network controller",
                   [RouterCapability.DEVICE_INVENTORY, RouterCapability.WIFI_CLIENTS, RouterCapability.PER_DEVICE_TRAFFIC],
                   "Needs a controller to test against; the local API is not a stable public contract."),
    PlannedAdapter("opnsense", "OPNsense / pfSense",
                   [RouterCapability.DHCP_LEASES, RouterCapability.ARP_TABLE, RouterCapability.WAN_COUNTERS,
                    RouterCapability.ROUTER_SETTINGS],
                   "Needs a firewall instance and a read-only API key to test against."),
    PlannedAdapter("flow", "NetFlow / IPFIX / sFlow collector",
                   [RouterCapability.FLOWS, RouterCapability.PER_DEVICE_TRAFFIC],
                   "Needs an exporter on the gateway; collector not built."),
]  # fmt: skip
