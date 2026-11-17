import { Table2 } from "lucide-react";
import { useId, useMemo, useState } from "react";
import { Area, CartesianGrid, ComposedChart, Line, ResponsiveContainer, Tooltip as RTooltip, XAxis, YAxis } from "recharts";

import type { ChartPoint } from "@/hooks/useHistory";
import { cn } from "@/lib/cn";
import { formatDateTime, formatTick, formatValue } from "@/lib/format";

export interface ChartSeries {
  key: string;
  name: string;
  color: string; // a validated series token: var(--series-N)
  points: ChartPoint[];
  fill?: boolean;
}

interface Props {
  series: ChartSeries[];
  unit: string;
  spanSeconds: number;
  height?: number;
  /** One-sentence description for assistive tech; a generated summary is appended. */
  ariaLabel: string;
  yMax?: number;
  emptyHint?: string;
  className?: string;
}

interface Row {
  t: number;
  [key: string]: number | null;
}

function summarize(series: ChartSeries[], unit: string): string {
  return series
    .map((s) => {
      const vals = s.points.map((p) => p.v).filter((v): v is number => v !== null);
      if (!vals.length) return `${s.name}: no measured data in this period.`;
      const avg = vals.reduce((a, b) => a + b, 0) / vals.length;
      return `${s.name}: latest ${formatValue(vals[vals.length - 1], unit)}, average ${formatValue(avg, unit)}, peak ${formatValue(Math.max(...vals), unit)} over ${vals.length} points.`;
    })
    .join(" ");
}

/**
 * Time-series chart: one unit, one y-axis (never dual-axis). Missing buckets are gaps,
 * not interpolated. Ships with a legend for ≥ 2 series, a crosshair tooltip, a text
 * summary for screen readers and a table view of the same data.
 */
export function TimeSeriesChart({ series, unit, spanSeconds, height = 220, ariaLabel, yMax, emptyHint, className }: Props) {
  const [showTable, setShowTable] = useState(false);
  const gid = useId().replace(/:/g, "");
  const rows = useMemo<Row[]>(() => {
    const byT = new Map<number, Row>();
    for (const s of series) {
      for (const p of s.points) {
        let row = byT.get(p.t);
        if (!row) {
          row = { t: p.t };
          byT.set(p.t, row);
        }
        row[s.key] = p.v;
      }
    }
    return [...byT.values()].sort((a, b) => a.t - b.t);
  }, [series]);
  const hasData = series.some((s) => s.points.some((p) => p.v !== null));
  const summary = useMemo(() => summarize(series, unit), [series, unit]);
  const domainEnd = rows.length ? Math.max(rows[rows.length - 1]!.t, Date.now() - 1500) : Date.now();
  const domain: [number, number] = [domainEnd - spanSeconds * 1000, domainEnd];

  return (
    <figure className={cn("relative", className)}>
      <div className="mb-2 flex items-center justify-between gap-3">
        {series.length > 1 ? (
          <ul className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted" aria-label="Legend">
            {series.map((s) => (
              <li key={s.key} className="flex items-center gap-1.5">
                <span className="h-0.5 w-4 rounded-full" style={{ background: s.color }} aria-hidden />
                {s.name}
              </li>
            ))}
          </ul>
        ) : (
          <span />
        )}
        <button
          type="button"
          onClick={() => setShowTable((v) => !v)}
          aria-pressed={showTable}
          className="flex items-center gap-1 rounded-md px-1.5 py-1 text-[11px] text-faint hover:bg-raised hover:text-ink"
        >
          <Table2 className="h-3.5 w-3.5" aria-hidden /> {showTable ? "Chart" : "Table"}
        </button>
      </div>
      <figcaption className="sr-only">
        {ariaLabel} {summary}
      </figcaption>
      {showTable ? (
        <div className="overflow-auto rounded-lg border border-line" style={{ height }}>
          <table className="w-full text-left text-xs">
            <thead className="sticky top-0 bg-raised text-faint">
              <tr>
                <th scope="col" className="px-3 py-1.5 font-medium">
                  Time
                </th>
                {series.map((s) => (
                  <th key={s.key} scope="col" className="px-3 py-1.5 font-medium">
                    {s.name}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="num">
              {[...rows].reverse().slice(0, 300).map((r) => (
                <tr key={r.t} className="border-t border-line/50">
                  <td className="px-3 py-1 text-muted">{formatDateTime(r.t)}</td>
                  {series.map((s) => (
                    <td key={s.key} className="px-3 py-1">
                      {r[s.key] === null || r[s.key] === undefined ? <span className="text-faint">no data</span> : formatValue(r[s.key], unit)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : hasData ? (
        <div style={{ height }} aria-hidden>
          <ResponsiveContainer width="100%" height="100%">
            <ComposedChart data={rows} margin={{ top: 6, right: 8, bottom: 0, left: 0 }}>
              <defs>
                {series.map((s) => (
                  <linearGradient key={s.key} id={`${gid}-${s.key}`} x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor={s.color} stopOpacity={0.28} />
                    <stop offset="100%" stopColor={s.color} stopOpacity={0} />
                  </linearGradient>
                ))}
              </defs>
              <CartesianGrid stroke="rgb(var(--line))" strokeOpacity={0.55} vertical={false} />
              <XAxis
                dataKey="t"
                type="number"
                scale="time"
                domain={domain}
                allowDataOverflow
                tickFormatter={(t: number) => formatTick(t, spanSeconds)}
                tick={{ fill: "rgb(var(--faint))", fontSize: 11 }}
                tickLine={false}
                axisLine={{ stroke: "rgb(var(--line))" }}
                minTickGap={48}
              />
              <YAxis
                width={84}
                domain={[0, yMax ?? "auto"]}
                tickFormatter={(v: number) => formatValue(v, unit)}
                tick={{ fill: "rgb(var(--faint))", fontSize: 11 }}
                tickLine={false}
                axisLine={false}
                tickCount={5}
              />
              <RTooltip
                isAnimationActive={false}
                cursor={{ stroke: "rgb(var(--faint))", strokeDasharray: "3 3" }}
                content={({ active, payload, label }) => {
                  if (!active || !payload?.length) return null;
                  return (
                    <div className="rounded-lg border border-line bg-raised/95 px-3 py-2 text-xs shadow-xl">
                      <p className="mb-1 text-faint">{formatDateTime(label as number)}</p>
                      {series.map((s) => {
                        const item = payload.find((p) => p.dataKey === s.key);
                        const v = item?.value as number | null | undefined;
                        return (
                          <p key={s.key} className="flex items-center justify-between gap-4 text-ink">
                            <span className="flex items-center gap-1.5 text-muted">
                              <span className="h-0.5 w-3 rounded-full" style={{ background: s.color }} />
                              {s.name}
                            </span>
                            <span className="num font-medium">{v === null || v === undefined ? "no data" : formatValue(v, unit)}</span>
                          </p>
                        );
                      })}
                    </div>
                  );
                }}
              />
              {series.map((s) =>
                s.fill !== false ? (
                  <Area
                    key={s.key}
                    type="monotone"
                    dataKey={s.key}
                    name={s.name}
                    stroke={s.color}
                    strokeWidth={2}
                    fill={`url(#${gid}-${s.key})`}
                    dot={false}
                    activeDot={{ r: 4, stroke: "rgb(var(--surface))", strokeWidth: 2 }}
                    connectNulls={false}
                    isAnimationActive={false}
                  />
                ) : (
                  <Line
                    key={s.key}
                    type="monotone"
                    dataKey={s.key}
                    name={s.name}
                    stroke={s.color}
                    strokeWidth={2}
                    dot={false}
                    activeDot={{ r: 4, stroke: "rgb(var(--surface))", strokeWidth: 2 }}
                    connectNulls={false}
                    isAnimationActive={false}
                  />
                ),
              )}
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      ) : (
        <div className="flex items-center justify-center rounded-lg border border-dashed border-line text-center text-xs text-muted" style={{ height }}>
          <p className="max-w-sm px-4">{emptyHint ?? "No measured data in this period."}</p>
        </div>
      )}
    </figure>
  );
}

// eslint-disable-next-line react-refresh/only-export-components
export const SERIES_COLORS = ["var(--series-1)", "var(--series-2)", "var(--series-3)", "var(--series-4)"] as const;
