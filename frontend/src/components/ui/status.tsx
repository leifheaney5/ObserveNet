import { AlertOctagon, AlertTriangle, Ban, CheckCircle2, CircleDashed, CircleHelp, Clock, Info, MinusCircle, ShieldCheck, type LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

import { Tooltip } from "@/components/ui/overlays";
import { cn } from "@/lib/cn";
import { formatDateTime, timeAgo, titleCase } from "@/lib/format";

export type Tone = "good" | "warn" | "serious" | "crit" | "info" | "neutral" | "violet";

const TONES: Record<Tone, string> = {
  good: "border-good/30 bg-good/10 text-good",
  warn: "border-warn/30 bg-warn/10 text-warn",
  serious: "border-serious/30 bg-serious/10 text-serious",
  crit: "border-crit/30 bg-crit/10 text-crit",
  info: "border-accent/30 bg-accent/10 text-accent",
  violet: "border-violet/40 bg-violet/10 text-violet",
  neutral: "border-line bg-raised text-muted",
};

/** Status is never colour alone: every badge carries an icon and a text label. */
export function Badge({ tone = "neutral", icon: Icon, children, className, title }: { tone?: Tone; icon?: LucideIcon; children: ReactNode; className?: string; title?: string }) {
  return (
    <span title={title} className={cn("inline-flex items-center gap-1 whitespace-nowrap rounded-full border px-2 py-0.5 text-[11px] font-medium", TONES[tone], className)}>
      {Icon && <Icon className="h-3 w-3" aria-hidden />}
      {children}
    </span>
  );
}

const DATA_STATES: Record<string, { tone: Tone; icon: LucideIcon; label: string }> = {
  ok: { tone: "good", icon: CheckCircle2, label: "Measured" },
  live: { tone: "good", icon: CheckCircle2, label: "Live" },
  stale: { tone: "warn", icon: Clock, label: "Stale" },
  unknown: { tone: "neutral", icon: CircleHelp, label: "Unknown" },
  unavailable: { tone: "neutral", icon: MinusCircle, label: "Unavailable" },
  unsupported: { tone: "neutral", icon: Ban, label: "Unsupported" },
  disabled: { tone: "neutral", icon: CircleDashed, label: "Disabled" },
  disconnected: { tone: "crit", icon: AlertOctagon, label: "Disconnected" },
  never: { tone: "neutral", icon: CircleDashed, label: "No data yet" },
  supported: { tone: "good", icon: CheckCircle2, label: "Supported" },
  partial: { tone: "warn", icon: AlertTriangle, label: "Partial" },
  not_implemented: { tone: "neutral", icon: Ban, label: "Not implemented" },
};

/** Measurement / capability state badge (ok, stale, unknown, unavailable, unsupported …). */
export function StateBadge({ state, className }: { state: string; className?: string }) {
  const s = DATA_STATES[state] ?? { tone: "neutral" as Tone, icon: CircleHelp, label: titleCase(state) };
  return (
    <Badge tone={s.tone} icon={s.icon} className={className}>
      {s.label}
    </Badge>
  );
}

const SEVERITY: Record<string, { tone: Tone; icon: LucideIcon }> = {
  high: { tone: "crit", icon: AlertOctagon },
  critical: { tone: "crit", icon: AlertOctagon },
  medium: { tone: "serious", icon: AlertTriangle },
  warning: { tone: "warn", icon: AlertTriangle },
  low: { tone: "warn", icon: Info },
  info: { tone: "info", icon: Info },
};

export function SeverityBadge({ severity }: { severity: string }) {
  const s = SEVERITY[severity] ?? { tone: "neutral" as Tone, icon: Info };
  return (
    <Badge tone={s.tone} icon={s.icon}>
      {titleCase(severity)}
    </Badge>
  );
}

const CLASSIFICATION: Record<string, { tone: Tone; label: string; help: string }> = {
  OBSERVED_CONFIGURATION: { tone: "info", label: "Observed configuration", help: "A setting or state that was read directly from the device. Fact, not inference." },
  POTENTIAL_EXPOSURE: { tone: "violet", label: "Potential exposure", help: "The observation is factual; the exposure is inferred and was not tested. This is not proof of exploitability." },
  VERIFIED_RESOLUTION: { tone: "good", label: "Verified resolution", help: "The same check was re-run on fresh evidence and no longer observes the condition." },
};

export function ClassificationBadge({ value }: { value: string }) {
  const c = CLASSIFICATION[value] ?? { tone: "neutral" as Tone, label: titleCase(value), help: "" };
  return (
    <Tooltip content={c.help}>
      <span tabIndex={0} className="inline-flex rounded-full">
        <Badge tone={c.tone} icon={value === "VERIFIED_RESOLUTION" ? ShieldCheck : undefined}>
          {c.label}
        </Badge>
      </span>
    </Tooltip>
  );
}

const FINDING_STATUS: Record<string, { tone: Tone; label: string }> = {
  open: { tone: "warn", label: "Open" },
  acknowledged: { tone: "info", label: "Acknowledged" },
  snoozed: { tone: "neutral", label: "Snoozed" },
  resolved: { tone: "good", label: "Resolved (verified)" },
  closed_unverified: { tone: "neutral", label: "Closed — not verified" },
  firing: { tone: "crit", label: "Firing" },
};

export function StatusBadge({ status }: { status: string }) {
  const s = FINDING_STATUS[status] ?? { tone: "neutral" as Tone, label: titleCase(status) };
  return <Badge tone={s.tone}>{s.label}</Badge>;
}

const PRESENCE: Record<string, Tone> = {
  reporting: "good",
  seen_recently: "good",
  reporting_stale: "warn",
  agent_silent: "serious",
  not_seen_recently: "neutral",
  unknown: "neutral",
};

export function PresenceBadge({ presence }: { presence: { state: string; label: string; basis: string } }) {
  return (
    <Tooltip content={presence.basis}>
      <span tabIndex={0} className="inline-flex rounded-full">
        <Badge tone={PRESENCE[presence.state] ?? "neutral"}>
          <span className={cn("h-1.5 w-1.5 rounded-full bg-current")} aria-hidden />
          {presence.label}
        </Badge>
      </span>
    </Tooltip>
  );
}

/** "3 s ago · source" with the absolute local timestamp on hover. */
export function Provenance({ ts, source, point, className }: { ts?: string | null; source?: string | null; point?: string | null; className?: string }) {
  if (!ts && !source) return null;
  return (
    <p className={cn("truncate text-[11px] text-faint", className)}>
      {ts && (
        <time dateTime={ts} title={formatDateTime(ts)}>
          {timeAgo(ts)}
        </time>
      )}
      {ts && source && " · "}
      {source && <span title={point ? `Observation point: ${titleCase(point)}` : undefined}>{source}</span>}
    </p>
  );
}

export function Timestamp({ ts, className }: { ts?: string | null; className?: string }) {
  if (!ts) return <span className={cn("text-faint", className)}>—</span>;
  return (
    <time dateTime={ts} title={formatDateTime(ts)} className={cn("num whitespace-nowrap", className)}>
      {timeAgo(ts)}
    </time>
  );
}

const HEALTH: Record<string, { tone: Tone; icon: LucideIcon; label: string }> = {
  ok: { tone: "good", icon: CheckCircle2, label: "Healthy" },
  degraded: { tone: "warn", icon: AlertTriangle, label: "Degraded" },
  down: { tone: "crit", icon: AlertOctagon, label: "Down" },
};

/** Health of an ObserveNet component (API, worker, …) — distinct from measurement state. */
export function HealthBadge({ state }: { state: string }) {
  const h = HEALTH[state] ?? { tone: "neutral" as Tone, icon: CircleHelp, label: titleCase(state) };
  return (
    <Badge tone={h.tone} icon={h.icon}>
      {h.label}
    </Badge>
  );
}

export function DemoTag() {
  return (
    <Badge tone="violet" title="Synthetic demo data — not a real measurement">
      DEMO
    </Badge>
  );
}
