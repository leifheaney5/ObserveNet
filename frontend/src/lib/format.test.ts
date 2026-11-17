import { describe, expect, it } from "vitest";

import { formatBps, formatDuration, formatMs, formatPct, formatValue, scaleBps, timeAgo } from "@/lib/format";

describe("formatBps", () => {
  it("scales with explicit units", () => {
    expect(formatBps(0)).toBe("0 bit/s");
    expect(formatBps(999)).toBe("999 bit/s");
    expect(formatBps(1_500)).toBe("1.50 kbit/s");
    expect(formatBps(25_300_000)).toBe("25.3 Mbit/s");
    expect(formatBps(940_000_000)).toBe("940 Mbit/s");
    expect(formatBps(2_500_000_000)).toBe("2.50 Gbit/s");
  });

  it("renders a dash, never zero, for missing measurements", () => {
    expect(formatBps(null)).toBe("—");
    expect(formatBps(undefined)).toBe("—");
    expect(formatBps(Number.NaN)).toBe("—");
    expect(scaleBps(null)).toEqual({ value: "—", unit: "" });
  });
});

describe("other formatters", () => {
  it("formats latency with sensible precision", () => {
    expect(formatMs(0.389)).toBe("0.39 ms");
    expect(formatMs(23.44)).toBe("23.4 ms");
    expect(formatMs(250.4)).toBe("250 ms");
    expect(formatMs(null)).toBe("—");
  });

  it("formats percentages and unknowns", () => {
    expect(formatPct(0)).toBe("0.0%");
    expect(formatPct(12.345)).toBe("12.3%");
    expect(formatPct(null)).toBe("—");
  });

  it("dispatches on unit", () => {
    expect(formatValue(8_000_000, "bit/s")).toBe("8.00 Mbit/s");
    expect(formatValue(3, "ms")).toBe("3.00 ms");
    expect(formatValue(null, "ms")).toBe("—");
    expect(formatValue(42, "packet/s")).toBe("42 pkt/s");
  });

  it("describes ages relative to now", () => {
    const now = Date.parse("2026-01-01T12:00:00Z");
    expect(timeAgo("2026-01-01T11:59:58Z", now)).toBe("just now");
    expect(timeAgo("2026-01-01T11:59:30Z", now)).toBe("30 s ago");
    expect(timeAgo("2026-01-01T11:30:00Z", now)).toBe("30 min ago");
    expect(timeAgo("2026-01-01T02:00:00Z", now)).toBe("10 h ago");
    expect(timeAgo(null, now)).toBe("never");
  });

  it("formats durations", () => {
    expect(formatDuration(45)).toBe("45 s");
    expect(formatDuration(600)).toBe("10 min");
    expect(formatDuration(7200)).toBe("2.0 h");
  });
});
