import { describe, expect, it, vi } from "vitest";

import { backoffDelay, LiveStore, MAX_POINTS, seriesKey, STALE_AFTER_MS } from "@/lib/liveStore";

const sample = (t: string, v: number, m = "net.rx_bps", l = "eth0") => ({ source_id: 1, m, l, v, u: "bit/s", t });

describe("LiveStore", () => {
  it("appends samples per series and notifies subscribers once per frame", () => {
    const store = new LiveStore();
    const listener = vi.fn();
    store.subscribe(listener);
    store.handleFrame({ type: "tick", ts: "2026-01-01T00:00:01Z", samples: [sample("2026-01-01T00:00:01Z", 10), sample("2026-01-01T00:00:01Z", 5, "net.tx_bps")] });
    expect(listener).toHaveBeenCalledTimes(1);
    expect(store.series(seriesKey(1, "net.rx_bps", "eth0"))).toEqual([{ t: Date.parse("2026-01-01T00:00:01Z"), v: 10 }]);
    expect(store.latest(seriesKey(1, "net.tx_bps", "eth0"))?.v).toBe(5);
    expect(store.status).toBe("live");
  });

  it("ignores duplicate and out-of-order samples (e.g. replay after reconnect)", () => {
    const store = new LiveStore();
    store.handleFrame({ type: "tick", ts: "x", samples: [sample("2026-01-01T00:00:02Z", 2)] });
    store.handleFrame({ type: "tick", ts: "x", samples: [sample("2026-01-01T00:00:02Z", 99), sample("2026-01-01T00:00:01Z", 1)] });
    expect(store.series(seriesKey(1, "net.rx_bps", "eth0")).map((p) => p.v)).toEqual([2]);
  });

  it("keeps a bounded ring buffer", () => {
    const store = new LiveStore();
    const base = Date.parse("2026-01-01T00:00:00Z");
    for (let i = 0; i < MAX_POINTS + 50; i++) {
      store.handleFrame({ type: "tick", ts: "x", samples: [sample(new Date(base + i * 1000).toISOString(), i)] });
    }
    const buf = store.series(seriesKey(1, "net.rx_bps", "eth0"));
    expect(buf).toHaveLength(MAX_POINTS);
    expect(buf[0]?.v).toBe(50);
  });

  it("a heartbeat keeps the stream live without inventing samples", () => {
    const store = new LiveStore();
    store.handleFrame({ type: "heartbeat", ts: "x" }, 1000);
    expect(store.status).toBe("live");
    expect(store.lastSampleAt).toBeNull();
    expect(store.series(seriesKey(1, "net.rx_bps", "eth0"))).toEqual([]);
  });

  it("only seeds from a hello snapshot when the value is fresh", () => {
    const store = new LiveStore();
    const now = Date.parse("2026-01-01T00:10:00Z");
    store.handleFrame(
      {
        type: "hello",
        ts: "x",
        snapshot: {
          "1|net.rx_bps|eth0": { v: 7, t: "2026-01-01T00:09:58Z", u: "bit/s" },
          "1|net.rx_bps|old0": { v: 9, t: "2026-01-01T00:00:00Z", u: "bit/s" },
        },
      },
      now,
    );
    expect(store.latest("1|net.rx_bps|eth0")?.v).toBe(7);
    expect(store.latest("1|net.rx_bps|old0")).toBeUndefined();
  });

  it("turns stale when frames stop arriving", () => {
    const store = new LiveStore();
    store.handleFrame({ type: "heartbeat", ts: "x" }, 10_000);
    store.checkStale(10_000 + STALE_AFTER_MS - 1);
    expect(store.status).toBe("live");
    store.checkStale(10_000 + STALE_AFTER_MS + 1);
    expect(store.status).toBe("stale");
  });

  it("forwards alert events to event listeners", () => {
    const store = new LiveStore();
    const onEvent = vi.fn();
    store.onEvent(onEvent);
    store.handleFrame({ type: "tick", ts: "x", events: [{ type: "alert", event: "fired", id: 3 }] });
    expect(onEvent).toHaveBeenCalledWith({ type: "alert", event: "fired", id: 3 });
  });
});

describe("backoffDelay", () => {
  it("grows exponentially, is capped, and is jittered", () => {
    expect(backoffDelay(0, () => 1)).toBe(500);
    expect(backoffDelay(3, () => 1)).toBe(4000);
    expect(backoffDelay(20, () => 1)).toBe(15000);
    expect(backoffDelay(3, () => 0)).toBe(2000);
  });
});
