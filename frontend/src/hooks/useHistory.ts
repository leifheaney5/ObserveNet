import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";

import { useLiveSeries } from "@/hooks/useLive";
import type { Period } from "@/hooks/usePeriod";
import { api, type History } from "@/lib/api";
import { seriesKey, type LivePoint } from "@/lib/liveStore";

export interface ChartPoint {
  t: number;
  v: number | null;
  min?: number | null;
  max?: number | null;
  n?: number;
}

export interface SeriesSelector {
  metric: string;
  sourceId?: number | null;
  label?: string | null;
  /** Transform an averaged bucket value (e.g. delivery ratio → loss %). */
  transform?: (v: number) => number;
}

/** Merge REST history with the live ring buffer: history first, then newer live points. */
export function mergeLive(history: ChartPoint[], live: LivePoint[], fromMs: number, transform?: (v: number) => number): ChartPoint[] {
  const lastHist = history.length ? history[history.length - 1]!.t : 0;
  const lastMeasured = [...history].reverse().find((p) => p.v !== null)?.t ?? 0;
  const out = history.filter((p) => p.t >= fromMs && (p.v !== null || p.t <= lastMeasured || live.length === 0));
  for (const p of live) {
    if (p.t > Math.max(lastMeasured, lastHist - 2000) && p.t >= fromMs) {
      out.push({ t: p.t, v: transform ? transform(p.v) : p.v, n: 1 });
    }
  }
  return out.sort((a, b) => a.t - b.t);
}

/**
 * Chart data for one series over the selected period.
 * Short periods (1 s buckets) load history once and then append live samples; longer
 * periods read rollups and refresh every 30 s. Nothing is interpolated: empty buckets stay null.
 */
export function useSeriesHistory(sel: SeriesSelector | null, period: Period) {
  const enabled = !!sel && sel.sourceId != null && sel.label != null;
  const query = useQuery({
    queryKey: ["history", sel?.metric, sel?.sourceId, sel?.label, period.key],
    enabled,
    queryFn: () => {
      const to = new Date();
      const from = new Date(to.getTime() - period.seconds * 1000);
      return api.get<History>("/metrics/history", {
        metric: sel!.metric,
        source_id: sel!.sourceId,
        label: sel!.label,
        from: from.toISOString(),
        to: to.toISOString(),
        bucket: period.bucket,
      });
    },
    refetchInterval: period.live ? 60_000 : 30_000,
    staleTime: period.live ? 30_000 : 15_000,
  });
  const liveKey = enabled && period.live ? seriesKey(sel!.sourceId, sel!.metric, sel!.label) : null;
  const live = useLiveSeries(liveKey);
  const transform = sel?.transform;

  const base = useMemo<ChartPoint[]>(() => {
    const pts = query.data?.series[0]?.points ?? [];
    return pts.map((p) => ({
      t: Date.parse(p.t),
      v: p.avg === null ? null : transform ? transform(p.avg) : p.avg,
      min: p.min === null ? null : transform ? transform(p.min) : p.min,
      max: p.max === null ? null : transform ? transform(p.max) : p.max,
      n: p.n,
    }));
  }, [query.data, transform]);

  const fromMs = Date.now() - period.seconds * 1000;
  const points = period.live ? mergeLive(base, live, fromMs, transform) : base;
  return {
    points,
    meta: query.data?.series[0]?.series,
    resolution: query.data?.resolution,
    note: query.data?.note ?? null,
    isLoading: enabled && query.isLoading,
    isError: query.isError,
    error: query.error,
    hasSeries: (query.data?.series.length ?? 0) > 0,
  };
}
