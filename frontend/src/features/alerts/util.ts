import { ApiError } from "@/lib/api";

export function errorMessage(e: unknown): string {
  if (e instanceof ApiError) return e.message;
  if (e instanceof Error) return e.message;
  return "Request failed.";
}

/** <input type="datetime-local"> value (browser-local time) for a Date. */
export function toLocalInput(d: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** Local datetime-local string → ISO-8601 UTC, or null when unparsable. */
export function localInputToIso(value: string): string | null {
  if (!value) return null;
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? null : d.toISOString();
}

export const KIND_LABELS: Record<string, string> = {
  gateway_latency_high: "Gateway latency",
  packet_loss_high: "Packet loss",
  collector_heartbeat_missing: "Collector heartbeat",
  interface_bandwidth_high: "Interface throughput",
  new_device: "New device",
  stale_source: "Source degraded",
  posture_changed: "Posture change",
};

export function kindLabel(kind: string): string {
  return KIND_LABELS[kind] ?? kind.replace(/_/g, " ");
}
