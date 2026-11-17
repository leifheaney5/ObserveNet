/** Formatting helpers. Units are explicit everywhere: a number never appears without one. */

export interface Scaled {
  value: string;
  unit: string;
}

const BIT_UNITS: [number, string][] = [
  [1e9, "Gbit/s"],
  [1e6, "Mbit/s"],
  [1e3, "kbit/s"],
];

export function scaleBps(bps: number | null | undefined): Scaled {
  if (bps === null || bps === undefined || !Number.isFinite(bps)) return { value: "—", unit: "" };
  for (const [div, unit] of BIT_UNITS) {
    if (Math.abs(bps) >= div) {
      const v = bps / div;
      return { value: v >= 100 ? v.toFixed(0) : v >= 10 ? v.toFixed(1) : v.toFixed(2), unit };
    }
  }
  return { value: bps.toFixed(0), unit: "bit/s" };
}

export function formatBps(bps: number | null | undefined): string {
  const s = scaleBps(bps);
  return s.unit ? `${s.value} ${s.unit}` : s.value;
}

export function formatMs(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || !Number.isFinite(ms)) return "—";
  if (ms < 10) return `${ms.toFixed(2)} ms`;
  if (ms < 100) return `${ms.toFixed(1)} ms`;
  return `${ms.toFixed(0)} ms`;
}

export function formatPct(pct: number | null | undefined, digits = 1): string {
  if (pct === null || pct === undefined || !Number.isFinite(pct)) return "—";
  return `${pct.toFixed(digits)}%`;
}

export function formatValue(value: number | null | undefined, unit: string): string {
  if (value === null || value === undefined) return "—";
  switch (unit) {
    case "bit/s":
      return formatBps(value);
    case "ms":
      return formatMs(value);
    case "percent":
      return formatPct(value);
    case "packet/s":
      return `${value.toFixed(0)} pkt/s`;
    default:
      return `${Number.isInteger(value) ? value : value.toFixed(1)}${unit ? ` ${unit}` : ""}`;
  }
}

/** Relative age such as "3 s ago"; the absolute local time goes in a title attribute. */
export function timeAgo(iso: string | Date | null | undefined, now: number = Date.now()): string {
  if (!iso) return "never";
  const t = typeof iso === "string" ? Date.parse(iso) : iso.getTime();
  if (Number.isNaN(t)) return "unknown";
  const s = Math.round((now - t) / 1000);
  if (s < 0) return "just now";
  if (s < 5) return "just now";
  if (s < 60) return `${s} s ago`;
  const m = Math.round(s / 60);
  if (m < 60) return `${m} min ago`;
  const h = Math.round(m / 60);
  if (h < 48) return `${h} h ago`;
  return `${Math.round(h / 24)} d ago`;
}

const DATE_TIME = new Intl.DateTimeFormat(undefined, {
  year: "numeric",
  month: "short",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
});
const TIME = new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" });
const TIME_SHORT = new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit" });
const DAY = new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });

/** Timestamps are stored in UTC and always rendered in the browser's local zone. */
export function formatDateTime(iso: string | number | null | undefined): string {
  if (iso === null || iso === undefined) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "—" : DATE_TIME.format(d);
}

export function formatTick(ms: number, spanSeconds: number): string {
  const d = new Date(ms);
  if (spanSeconds <= 900) return TIME.format(d);
  if (spanSeconds <= 86400) return TIME_SHORT.format(d);
  return DAY.format(d);
}

export function formatDuration(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)} s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} min`;
  if (seconds < 172800) return `${(seconds / 3600).toFixed(1)} h`;
  return `${(seconds / 86400).toFixed(1)} d`;
}

export function titleCase(s: string): string {
  return s.replace(/[_-]+/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}
