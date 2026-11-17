import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";

import { Card, CardBody, EmptyState, ErrorState, NativeSelect, Skeleton, Table, Td, Th } from "@/components/ui/primitives";
import { SeverityBadge } from "@/components/ui/status";
import { api, type EventRow } from "@/lib/api";
import { formatDateTime, timeAgo } from "@/lib/format";

const FILTERS: [string, string][] = [
  ["", "All events"],
  ["alert.", "Alert notifications"],
  ["finding.", "Finding changes"],
  ["device.", "Device changes"],
  ["posture.", "Service changes"],
  ["agent.", "Agents"],
  ["inventory.", "Imports"],
];

export function Timeline() {
  const [prefix, setPrefix] = useState("alert.");
  const events = useQuery({
    queryKey: ["events", prefix],
    queryFn: () => api.get<EventRow[]>("/events", { limit: 100, kind_prefix: prefix || undefined }),
    refetchInterval: 30_000,
  });
  return (
    <Card>
      <div className="flex flex-wrap items-end justify-between gap-3 px-5 pt-4">
        <div>
          <h2 className="text-sm font-semibold tracking-tight text-ink">Notification & event history</h2>
          <p className="mt-0.5 text-xs text-muted">Append-only record of what fired, resolved or changed. Latest 100 entries.</p>
        </div>
        <div className="w-52">
          <label htmlFor="timeline-filter" className="mb-1 block text-xs font-medium text-muted">
            Show
          </label>
          <NativeSelect id="timeline-filter" value={prefix} onChange={(e) => setPrefix(e.target.value)}>
            {FILTERS.map(([v, l]) => (
              <option key={v} value={v}>
                {l}
              </option>
            ))}
          </NativeSelect>
        </div>
      </div>
      <CardBody className="px-2">
        {events.isLoading ? (
          <Skeleton className="mx-3 h-40" />
        ) : events.isError ? (
          <ErrorState error={events.error} retry={() => void events.refetch()} />
        ) : events.data?.length ? (
          <Table>
            <thead>
              <tr>
                <Th>When</Th>
                <Th>Severity</Th>
                <Th>Event</Th>
                <Th>What happened</Th>
                <Th>Related</Th>
              </tr>
            </thead>
            <tbody>
              {events.data.map((e) => (
                <tr key={e.id} className="hover:bg-raised/40">
                  <Td className="whitespace-nowrap text-xs text-muted">
                    <time dateTime={e.ts} title={timeAgo(e.ts)}>
                      {formatDateTime(e.ts)}
                    </time>
                  </Td>
                  <Td>
                    <SeverityBadge severity={e.severity} />
                  </Td>
                  <Td className="font-mono text-xs text-muted">{e.kind}</Td>
                  <Td className="max-w-xl text-xs">{e.message}</Td>
                  <Td className="whitespace-nowrap text-xs">
                    {e.asset_id != null && (
                      <Link to={`/devices/${e.asset_id}`} className="mr-3 text-accent underline-offset-2 hover:underline">
                        Device
                      </Link>
                    )}
                    {e.finding_id != null && (
                      <Link to={`/security/findings/${e.finding_id}`} className="text-accent underline-offset-2 hover:underline">
                        Finding
                      </Link>
                    )}
                    {e.asset_id == null && e.finding_id == null && <span className="text-faint">—</span>}
                  </Td>
                </tr>
              ))}
            </tbody>
          </Table>
        ) : (
          <EmptyState title="No events of this kind yet">Events are recorded as collectors report and rules evaluate.</EmptyState>
        )}
      </CardBody>
    </Card>
  );
}
