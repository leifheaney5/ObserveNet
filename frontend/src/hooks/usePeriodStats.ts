import { useQuery } from "@tanstack/react-query";

import type { Period } from "@/hooks/usePeriod";
import { api, type History } from "@/lib/api";

export interface PeriodStats {
  avg: number | null;
  peak: number | null;
  samples: number;
}

/** Sample-weighted average and peak of a series over [end - span, end]. */
export function statsFromHistory(history: History | undefined): PeriodStats {
  const pts = history?.series[0]?.points ?? [];
  let sum = 0;
  let n = 0;
  let peak: number | null = null;
  for (const p of pts) {
    if (p.avg === null || p.n === 0) continue;
    sum += p.avg * p.n;
    n += p.n;
    if (p.max !== null && (peak === null || p.max > peak)) peak = p.max;
  }
  return { avg: n ? sum / n : null, peak, samples: n };
}

/**
 * Current-period vs previous-period statistics for one series ("historical comparison").
 * Both windows use the same bucket size so they read the same resolution table.
 */
export function usePeriodComparison(metric: string, sourceId: number | null | undefined, label: string | null | undefined, period: Period) {
  const enabled = sourceId != null && label != null;
  return useQuery({
    queryKey: ["period-compare", metric, sourceId, label, period.key],
    enabled,
    refetchInterval: 60_000,
    queryFn: async () => {
      const now = Date.now();
      const span = period.seconds * 1000;
      const fetchWindow = (endMs: number) =>
        api.get<History>("/metrics/history", {
          metric,
          source_id: sourceId,
          label,
          from: new Date(endMs - span).toISOString(),
          to: new Date(endMs).toISOString(),
          bucket: period.bucket,
        });
      const [current, previous] = await Promise.all([fetchWindow(now), fetchWindow(now - span)]);
      const cur = statsFromHistory(current);
      const prev = statsFromHistory(previous);
      const change = cur.avg !== null && prev.avg !== null && prev.avg > 0 ? ((cur.avg - prev.avg) / prev.avg) * 100 : null;
      return { current: cur, previous: prev, changePct: change };
    },
  });
}
