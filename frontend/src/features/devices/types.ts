/** Local shapes for the loosely-typed arrays returned by GET /devices/{id}. */

export const ASSET_TYPES = [
  "unknown", "server", "desktop", "laptop", "phone", "tablet", "iot", "printer",
  "router", "switch", "access_point", "tv", "console", "nas", "other",
] as const; // mirrors backend/observenet/models/inventory.py ASSET_TYPES

export interface IdentityRow {
  kind: string;
  value: string;
  confidence: string;
  source: string | null;
  first_seen: string;
  last_seen: string;
  locally_administered: boolean;
  merged_from: number | null;
}

export interface AddressRow {
  ip: string;
  mac: string | null;
  hostname: string | null;
  evidence: string;
  source: string | null;
  first_seen: string;
  last_seen: string;
  merged_from: number | null;
}

export interface InterfaceRow {
  id: number;
  name: string;
  mac: string | null;
  is_up: boolean | null;
  link_speed_mbps: number | null;
  mtu: number | null;
  addresses: { family: string; address: string; prefix_len: number | null }[];
  is_default_route: boolean;
  last_seen: string;
}

export interface ServiceRow {
  id: number;
  origin: string;
  proto: string;
  address: string;
  port: number;
  process: string | null;
  container: string | null;
  active: boolean;
  first_seen: string;
  last_seen: string;
}

export interface MergeRow {
  id: number;
  source_asset_id: number;
  target_asset_id: number;
  merged_at: string;
  undone_at: string | null;
  reason: string | null;
  automatic: boolean;
}

export const EVIDENCE_LABELS: Record<string, { label: string; help: string }> = {
  neighbor_cache: {
    label: "Neighbour cache",
    help: "Seen in an agent host's ARP/neighbour table. Weak, passive evidence: only devices that recently talked to that host appear.",
  },
  agent_self: { label: "Agent (self-reported)", help: "The address is configured on an interface of the host running the agent." },
  client_import: { label: "Imported client list", help: "From a manually imported router client list. Point-in-time, not live." },
  dhcp_lease: { label: "DHCP lease", help: "Reported by a gateway provider's lease table." },
};

export const IDENTITY_LABELS: Record<string, string> = {
  agent_uid: "Agent ID",
  mac: "MAC address",
  hostname: "Hostname",
  ip_only: "IP address only",
};

/** "iot, guest , iot" -> ["iot", "guest"]; bounded like the API (20 tags × 40 chars). */
export function parseTags(raw: string): string[] {
  const seen = new Set<string>();
  for (const part of raw.split(",")) {
    const t = part.trim().slice(0, 40);
    if (t) seen.add(t);
  }
  return [...seen].slice(0, 20);
}
