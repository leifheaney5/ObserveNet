import { ArrowUpRight } from "lucide-react";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";

import { Card, Skeleton } from "@/components/ui/primitives";
import { DemoTag, Provenance, StateBadge } from "@/components/ui/status";
import type { Kpi } from "@/lib/api";
import { cn } from "@/lib/cn";
import { scaleBps } from "@/lib/format";

function display(value: number | null | undefined, unit: string): { value: string; unit: string } {
  if (value === null || value === undefined) return { value: "—", unit: "" };
  switch (unit) {
    case "bit/s":
      return scaleBps(value);
    case "ms":
      return { value: value < 10 ? value.toFixed(2) : value < 100 ? value.toFixed(1) : value.toFixed(0), unit: "ms" };
    case "percent":
      return { value: value.toFixed(value > 0 && value < 10 ? 1 : 0), unit: "%" };
    default:
      return { value: String(Math.round(value)), unit: "" };
  }
}

interface Props {
  kpi: Kpi | undefined;
  icon: ReactNode;
  to: string;
  /** Fresher value from the live stream, if any (overrides the polled KPI value). */
  live?: { value: number; ts: string } | null;
  footer?: ReactNode;
  demo?: boolean;
}

/**
 * A KPI is only a number when it was measured. Otherwise the card shows the state
 * (Unknown / Unavailable / Unsupported) and the reason — never a zero or a guess.
 */
export function KpiCard({ kpi, icon, to, live, footer, demo }: Props) {
  if (!kpi) {
    return (
      <Card className="p-4">
        <Skeleton className="h-3 w-24" />
        <Skeleton className="mt-3 h-7 w-20" />
        <Skeleton className="mt-3 h-3 w-28" />
      </Card>
    );
  }
  const measured = kpi.value !== null && kpi.value !== undefined;
  const value = live && measured ? live.value : kpi.value;
  const ts = live && measured ? live.ts : kpi.ts;
  const d = display(value, kpi.unit);
  return (
    <Link
      to={to}
      data-testid={`kpi-${kpi.key}`}
      data-state={kpi.state}
      className="group block rounded-xl focus-visible:outline-none"
      aria-label={`${kpi.label}: ${measured ? `${d.value} ${d.unit}` : kpi.state}. Open details.`}
    >
      <Card className="h-full p-4 transition-colors group-hover:border-accent/40">
        <div className="flex items-center justify-between gap-2">
          <span className="flex items-center gap-2 text-xs font-medium text-muted">
            <span className="text-faint">{icon}</span>
            {kpi.label}
          </span>
          <ArrowUpRight className="h-3.5 w-3.5 shrink-0 text-faint opacity-0 transition-opacity group-hover:opacity-100" aria-hidden />
        </div>
        {measured ? (
          <p className="mt-2 flex items-baseline gap-1.5">
            <span className={cn("num text-[26px] font-semibold leading-none tracking-tight", kpi.state === "stale" ? "text-muted" : "text-ink")}>{d.value}</span>
            <span className="text-xs text-muted">{d.unit}</span>
            {kpi.state === "stale" && <StateBadge state="stale" className="ml-1" />}
            {demo && (
              <span className="ml-auto self-center">
                <DemoTag />
              </span>
            )}
          </p>
        ) : (
          <div className="mt-2.5 flex items-center justify-between gap-2">
            <StateBadge state={kpi.state} />
            {demo && <DemoTag />}
          </div>
        )}
        <div className="mt-2 min-h-[30px]">
          {footer}
          {!measured && kpi.reason ? (
            <p title={kpi.reason} className="line-clamp-2 text-[11px] leading-snug text-faint">
              {kpi.reason}
            </p>
          ) : (
            <Provenance ts={ts} source={kpi.source} point={kpi.observation_point} />
          )}
        </div>
      </Card>
    </Link>
  );
}
