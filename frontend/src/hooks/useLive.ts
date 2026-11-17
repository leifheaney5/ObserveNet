import { useSyncExternalStore } from "react";

import { liveStore, type LivePoint, type LiveStatus } from "@/lib/liveStore";

/** Re-renders at most once per server frame (~1 Hz). */
export function useLiveVersion(): number {
  return useSyncExternalStore(liveStore.subscribe, liveStore.getVersion, liveStore.getVersion);
}

export function useLiveStatus(): { status: LiveStatus; lastFrameAt: number | null; lastSampleAt: number | null } {
  useLiveVersion();
  return { status: liveStore.status, lastFrameAt: liveStore.lastFrameAt, lastSampleAt: liveStore.lastSampleAt };
}

export function useLiveSeries(key: string | null): LivePoint[] {
  useLiveVersion();
  return key ? liveStore.series(key) : [];
}

export function useLiveLatest(key: string | null): LivePoint | undefined {
  useLiveVersion();
  return key ? liveStore.latest(key) : undefined;
}
