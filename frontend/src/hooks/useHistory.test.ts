import { describe, expect, it } from "vitest";

import { mergeLive, type ChartPoint } from "@/hooks/useHistory";
import { statsFromHistory } from "@/hooks/usePeriodStats";
import type { History } from "@/lib/api";

describe("mergeLive", () => {
  const hist: ChartPoint[] = [
    { t: 1000, v: 1 },
    { t: 2000, v: null }, // measured gap inside history
    { t: 3000, v: 3 },
    { t: 4000, v: null }, // trailing empty bucket (not yet measured)
  ];

  it("appends live points newer than history and keeps real gaps as nulls", () => {
    const out = mergeLive(hist, [{ t: 3000, v: 99 }, { t: 4100, v: 4 }, { t: 5100, v: 5 }], 0);
    expect(out.map((p) => [p.t, p.v])).toEqual([
      [1000, 1],
      [2000, null],
      [3000, 3],
      [4100, 4],
      [5100, 5],
    ]);
  });

  it("drops points before the visible window", () => {
    const out = mergeLive(hist, [{ t: 5100, v: 5 }], 2500);
    expect(out.map((p) => p.t)).toEqual([3000, 5100]);
  });

  it("applies the transform to live values (delivery ratio → loss %)", () => {
    const out = mergeLive([], [{ t: 10, v: 0 }, { t: 20, v: 1 }], 0, (v) => (1 - v) * 100);
    expect(out.map((p) => p.v)).toEqual([100, 0]);
  });

  it("never fabricates points when there is no live data", () => {
    expect(mergeLive(hist, [], 0).map((p) => p.v)).toEqual([1, null, 3, null]);
  });
});

describe("statsFromHistory", () => {
  const history = (points: { avg: number | null; max: number | null; n: number }[]): History =>
    ({ series: [{ series: {}, points: points.map((p, i) => ({ t: `2026-01-01T00:00:0${i}Z`, min: p.avg, ...p })) }] }) as unknown as History;

  it("weights the average by sample count and ignores empty buckets", () => {
    const s = statsFromHistory(history([{ avg: 10, max: 30, n: 1 }, { avg: null, max: null, n: 0 }, { avg: 20, max: 25, n: 3 }]));
    expect(s.avg).toBeCloseTo(17.5);
    expect(s.peak).toBe(30);
    expect(s.samples).toBe(4);
  });

  it("returns null (unknown) rather than 0 when nothing was measured", () => {
    expect(statsFromHistory(history([{ avg: null, max: null, n: 0 }]))).toEqual({ avg: null, peak: null, samples: 0 });
    expect(statsFromHistory(undefined)).toEqual({ avg: null, peak: null, samples: 0 });
  });
});
