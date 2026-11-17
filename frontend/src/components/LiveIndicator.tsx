import { useEffect, useState } from "react";

import { Tooltip } from "@/components/ui/overlays";
import { useLiveStatus } from "@/hooks/useLive";
import { cn } from "@/lib/cn";
import { formatDateTime, timeAgo } from "@/lib/format";

const COPY = {
  connecting: { label: "Connecting", dot: "bg-warn", text: "text-warn", help: "Opening the live stream." },
  live: { label: "Live", dot: "bg-good", text: "text-good", help: "Receiving a frame every second." },
  stale: { label: "Stale", dot: "bg-warn", text: "text-warn", help: "No frame for 5 s. Values on screen may be out of date; reconnecting." },
  disconnected: { label: "Disconnected", dot: "bg-crit", text: "text-crit", help: "Live stream is down. Retrying with backoff; charts show stored history only." },
} as const;

export function LiveIndicator() {
  const { status, lastFrameAt, lastSampleAt } = useLiveStatus();
  const [, force] = useState(0);
  useEffect(() => {
    const id = setInterval(() => force((n) => n + 1), 1000);
    return () => clearInterval(id);
  }, []);
  // Connected but no collector is sending samples: say so rather than showing a green "Live".
  const noSamples = status === "live" && (lastSampleAt === null || Date.now() - lastSampleAt > 10_000);
  const c = noSamples
    ? { label: "No live data", dot: "bg-faint", text: "text-muted", help: "The stream is connected but no collector has sent a sample in the last 10 s." }
    : COPY[status];
  return (
    <Tooltip
      content={
        <span>
          {c.help}
          <br />
          Last frame: {lastFrameAt ? formatDateTime(lastFrameAt) : "none yet"}
          <br />
          Last measurement: {lastSampleAt ? formatDateTime(lastSampleAt) : "none yet"}
        </span>
      }
    >
      <button
        type="button"
        data-testid="live-indicator"
        data-status={noSamples ? "no-data" : status}
        aria-label={`Live stream: ${c.label}`}
        className="flex cursor-help items-center gap-2 rounded-full border border-line bg-raised/70 px-2.5 py-1 text-xs"
      >
        <span className="relative flex h-2 w-2">
          {status === "live" && !noSamples && <span className={cn("absolute inset-0 animate-pulse-ring rounded-full", c.dot)} />}
          <span className={cn("relative h-2 w-2 rounded-full", c.dot)} />
        </span>
        <span className={cn("font-medium", c.text)}>{c.label}</span>
        <span className="num hidden text-faint sm:inline">· {lastSampleAt ? timeAgo(new Date(lastSampleAt)) : "no samples"}</span>
      </button>
    </Tooltip>
  );
}
