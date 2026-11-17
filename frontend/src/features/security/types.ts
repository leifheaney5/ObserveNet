import type { Tone } from "@/components/ui/status";

export interface CheckResultRow {
  asset_id: number;
  asset_name: string;
  rule_id: string;
  status: string;
  summary: string;
  at: string | null;
}

export interface CheckRow {
  rule_id: string;
  version: number;
  title: string;
  description: string;
  platform: string;
  category: string;
  default_severity: string;
  classification: string;
  coverage: "evaluated" | "unsupported" | "not_evaluated";
  results: CheckResultRow[];
}

export interface AssessmentRow {
  id: number;
  asset_id: number;
  source_id: number;
  trigger: string;
  engine_version: string;
  started_at: string;
  finished_at: string | null;
  summary: Record<string, unknown>;
  results: { rule_id: string; rule_version: number; status: string; summary: string }[];
}

export interface DemoState {
  items: { item: string; label: string; fixed: boolean }[];
  note: string;
}

export interface DiffChange {
  field: string;
  before: unknown;
  after: unknown;
}

export interface VerificationDiff {
  summary: string;
  before: Record<string, unknown>;
  after: Record<string, unknown>;
  changes: DiffChange[];
}

export interface HistoryRow {
  ts: string;
  kind: string;
  message: string;
  data: Record<string, unknown>;
}

export const ACTIVE_STATUSES = ["open", "acknowledged", "snoozed"];
export const STALE_RULE_ID = "OBS-STALE-001";

/** Verification outcome wording. Only "resolved" may ever read as fixed. */
export const VERIFY_STATUS: Record<string, { tone: Tone; label: string; help: string }> = {
  not_requested: { tone: "neutral", label: "Not requested", help: "No re-check has been requested for this finding." },
  pending: { tone: "info", label: "Verifying…", help: "Waiting for evidence collected after the request." },
  resolved: { tone: "good", label: "Resolved", help: "The same check ran on fresh evidence and no longer observes the condition." },
  still_observed: { tone: "warn", label: "Still observed", help: "Fresh evidence still shows the condition." },
  unknown: { tone: "neutral", label: "Unknown", help: "No conclusive fresh evidence was obtained. The finding was left unchanged." },
};

/** Result of one check run. "pass" is scoped to that check only. */
export const CHECK_STATUS: Record<string, { tone: Tone; label: string }> = {
  pass: { tone: "good", label: "Not observed" },
  fail: { tone: "warn", label: "Observed" },
  unsupported: { tone: "neutral", label: "Unsupported" },
  unknown: { tone: "neutral", label: "Unknown" },
};

export function formatScalar(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return JSON.stringify(value);
}
