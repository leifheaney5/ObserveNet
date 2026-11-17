import { useQuery } from "@tanstack/react-query";
import { Cable, Network, Server } from "lucide-react";

import { ErrorState, Skeleton } from "@/components/ui/primitives";
import { Badge } from "@/components/ui/status";
import { graphql } from "@/lib/api";
import { titleCase } from "@/lib/format";

interface Relationship {
  kind: string;
  targetType: string;
  targetLabel: string;
  evidence: string;
}

interface DeviceGraph {
  device: {
    relationships: Relationship[];
    services: { proto: string; port: number; process: string | null }[];
  } | null;
}

const QUERY = `
  query DeviceRelationships($id: Int!) {
    device(id: $id) {
      relationships { kind targetType targetLabel evidence }
      services { proto port process }
    }
  }
`;

const ICONS: Record<string, typeof Cable> = { interface: Cable, segment: Network, service: Server };
const KIND_LABEL: Record<string, string> = { HAS_INTERFACE: "has interface", SEEN_ON: "seen on", LISTENS_ON: "listens on" };

/** Observed relationships of one device, loaded through the GraphQL API (one batched round trip). */
export function RelationshipsPanel({ deviceId }: { deviceId: number }) {
  const q = useQuery({
    queryKey: ["device-graph", deviceId],
    queryFn: () => graphql<DeviceGraph>(QUERY, { id: deviceId }),
    staleTime: 30_000,
  });
  if (q.isLoading) return <Skeleton className="h-16 w-full" />;
  if (q.isError) return <ErrorState error={q.error} retry={() => void q.refetch()} />;
  const rels = q.data?.device?.relationships ?? [];
  const groups = ["interface", "segment", "service"].map((type) => ({ type, items: rels.filter((r) => r.targetType === type) }));
  if (!rels.length) {
    return (
      <p className="text-xs text-muted">
        No relationships recorded. Interfaces and listening services are only known for hosts running an agent; subnets appear once a sighting
        falls inside an observed network segment.
      </p>
    );
  }
  return (
    <div className="space-y-3">
      <div className="grid gap-4 md:grid-cols-3">
        {groups.map(({ type, items }) => {
          const Icon = ICONS[type] ?? Cable;
          return (
            <div key={type}>
              <p className="mb-1.5 flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wider text-faint">
                <Icon className="h-3.5 w-3.5" aria-hidden /> {titleCase(type)}s ({items.length})
              </p>
              {items.length ? (
                <ul className="flex flex-wrap gap-1.5">
                  {items.slice(0, 24).map((r) => (
                    <li key={`${r.kind}-${r.targetLabel}-${r.evidence}`}>
                      <Badge title={`${KIND_LABEL[r.kind] ?? r.kind} ${r.targetLabel} — evidence: ${r.evidence}`}>
                        <span className="font-mono">{r.targetLabel}</span>
                        <span className="text-faint">· {r.evidence}</span>
                      </Badge>
                    </li>
                  ))}
                  {items.length > 24 && <li className="text-xs text-faint">+{items.length - 24} more</li>}
                </ul>
              ) : (
                <p className="text-xs text-faint">None observed.</p>
              )}
            </div>
          );
        })}
      </div>
      <p className="text-[11px] text-faint">
        Observed relationships only (asset ↔ interface ↔ subnet ↔ service), each with its evidence. This is not an attack path and implies no
        reachability. Loaded via GraphQL.
      </p>
    </div>
  );
}
