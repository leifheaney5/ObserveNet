import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ReactNode } from "react";

import { api, ApiError, type RuntimeSettings, type Schemas } from "@/lib/api";

export interface FullSettings {
  approved_cidrs: string[];
  collection: Schemas["CollectionSettings"];
  retention: Schemas["RetentionSettings"];
  freshness: Schemas["FreshnessSettings"];
  discovery: Required<Schemas["DiscoverySettings"]>;
  speedtest: Schemas["SpeedtestSettings"];
  posture: Required<Schemas["PostureSettings"]>;
}

/** Backend defaults (backend/observenet/services/settings.py), used only to label fields. */
export const DEFAULTS = {
  collection: { sample_interval_s: 1, inventory_interval_s: 30, posture_interval_s: 300 },
  retention: { raw_hours: 48, rollup_1m_days: 90, rollup_1h_days: 365, probe_results_hours: 48, events_days: 365, posture_snapshots_days: 90 },
  freshness: { stale_after_s: 10, disconnected_after_s: 90, device_seen_recently_s: 600 },
} as const;

/** The API always returns every section; this only narrows the generated optional types. */
export function normalizeSettings(s: RuntimeSettings): FullSettings {
  return {
    approved_cidrs: s.approved_cidrs ?? [],
    collection: s.collection ?? { ...DEFAULTS.collection },
    retention: s.retention ?? { ...DEFAULTS.retention },
    freshness: s.freshness ?? { ...DEFAULTS.freshness },
    discovery: { enabled: false, interval_minutes: 60, max_probes_per_second: 2, ...s.discovery, cidrs: s.discovery?.cidrs ?? [] },
    speedtest: s.speedtest ?? { enabled: false, download_url: "", max_bytes: 25_000_000, interval_hours: 6, requested_at: null },
    posture: {
      verify_timeout_s: 90,
      ...s.posture,
      listener_allowlist: s.posture?.listener_allowlist ?? [],
      docker_port_allowlist: s.posture?.docker_port_allowlist ?? [],
      windows_listener_allowlist: s.posture?.windows_listener_allowlist ?? [],
    },
  };
}

export function useRuntimeSettings() {
  const qc = useQueryClient();
  const query = useQuery({ queryKey: ["settings"], queryFn: () => api.get<RuntimeSettings>("/settings") });
  const save = useMutation({
    // The backend replaces the whole document, so the full object is always sent.
    mutationFn: (next: FullSettings) => api.put<RuntimeSettings>("/settings", next),
    onSuccess: (data) => qc.setQueryData(["settings"], data),
  });
  return { query, settings: query.data ? normalizeSettings(query.data) : undefined, save, version: query.dataUpdatedAt };
}

export function errorText(e: unknown): string {
  if (e instanceof ApiError) return e.message;
  return e instanceof Error ? e.message : "Request failed.";
}

export interface NumSpec {
  key: string;
  label: string;
  unit: string;
  min: number;
  max: number;
  step?: number;
  hint?: ReactNode;
  defaultValue?: number;
}

export function numbersValid(specs: NumSpec[], draft: Record<string, string>): boolean {
  return specs.every((s) => numError(s, draft[s.key]) === null);
}

export function numError(spec: NumSpec, raw: string | undefined): string | null {
  if (raw === undefined || raw.trim() === "") return "Required.";
  const n = Number(raw);
  if (!Number.isFinite(n)) return "Enter a number.";
  if (n < spec.min || n > spec.max) return `Must be between ${spec.min} and ${spec.max}.`;
  return null;
}

export function toDraft(values: object): Record<string, string> {
  return Object.fromEntries(Object.entries(values).map(([k, v]) => [k, String(v)]));
}

export function fromDraft<T extends object>(base: T, draft: Record<string, string>, keys: string[]): T {
  const out = { ...base } as Record<string, unknown>;
  for (const k of keys) out[k] = Number(draft[k]);
  return out as T;
}

export function validatePortEntry(v: string): string | null {
  const m = /^(tcp|udp)\/(\d{1,5})$/.exec(v);
  if (!m) return "Use the form tcp/443 or udp/53.";
  const port = Number(m[2]);
  return port >= 1 && port <= 65535 ? null : "Port must be between 1 and 65535.";
}

export function validateCidrShape(v: string): string | null {
  return /^[0-9a-fA-F:.]+\/\d{1,3}$/.test(v) ? null : "Use CIDR notation, for example 192.168.1.0/24.";
}
