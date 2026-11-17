import { CircleCheck, CircleDot } from "lucide-react";

import { EmptyState } from "@/components/ui/primitives";
import { Badge } from "@/components/ui/status";
import { JsonDetails } from "@/features/security/bits";
import type { Schemas } from "@/lib/api";
import { cn } from "@/lib/cn";
import { formatDateTime, timeAgo } from "@/lib/format";

const KIND_LABEL: Record<string, string> = {
  initial: "First observation",
  changed: "Observation changed",
  recheck: "Re-check",
};

export function EvidenceTimeline({ evidence, sources }: { evidence: Schemas["EvidenceOut"][]; sources: Record<number, string> }) {
  if (!evidence.length) {
    return <EmptyState title="No evidence records" className="py-6" />;
  }
  return (
    <ol className="space-y-4 border-l border-line pl-5">
      {[...evidence].reverse().map((e) => (
        <li key={e.id} className="relative" id={`evidence-${e.id}`}>
          <span
            className={cn("absolute -left-[27px] top-0.5 flex h-4 w-4 items-center justify-center rounded-full bg-surface", e.observed ? "text-warn" : "text-good")}
            aria-hidden
          >
            {e.observed ? <CircleDot className="h-4 w-4" /> : <CircleCheck className="h-4 w-4" />}
          </span>
          <div className="flex flex-wrap items-center gap-2">
            <Badge>{KIND_LABEL[e.kind] ?? e.kind}</Badge>
            <Badge tone={e.observed ? "warn" : "good"}>{e.observed ? "Condition observed" : "Condition not observed"}</Badge>
            <span className="text-[11px] text-faint">evidence #{e.id}</span>
          </div>
          <p className="mt-1.5 text-sm leading-relaxed text-ink">{e.summary}</p>
          <dl className="mt-1.5 grid gap-x-6 gap-y-0.5 text-xs text-muted sm:grid-cols-2">
            <div>
              <dt className="inline text-faint">Collected: </dt>
              <dd className="inline">
                <time dateTime={e.collected_at} className="num">
                  {formatDateTime(e.collected_at)}
                </time>{" "}
                ({timeAgo(e.collected_at)})
              </dd>
            </div>
            <div>
              <dt className="inline text-faint">Source: </dt>
              <dd className="inline">{e.source_id !== null ? (sources[e.source_id] ?? `source #${e.source_id}`) : "source no longer exists"}</dd>
            </div>
            <div className="sm:col-span-2">
              <dt className="inline text-faint">Method: </dt>
              <dd className="inline">{e.collection_method}</dd>
            </div>
          </dl>
          <JsonDetails summary="Raw evidence data" value={e.data} />
        </li>
      ))}
    </ol>
  );
}
