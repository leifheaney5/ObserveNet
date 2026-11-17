import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BellOff, CheckCheck, CheckCircle2, Clock, XCircle } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";

import { Dialog } from "@/components/ui/overlays";
import { Button, Card, CardBody, EmptyState, ErrorState, Field, Input, Skeleton, Table, Td, Th } from "@/components/ui/primitives";
import { Badge, DemoTag, SeverityBadge, Timestamp } from "@/components/ui/status";
import { FormError } from "@/features/alerts/shared";
import { kindLabel, localInputToIso, toLocalInput } from "@/features/alerts/util";
import { useAuth } from "@/hooks/useAuth";
import { api, type Alert } from "@/lib/api";
import { formatDateTime, formatDuration } from "@/lib/format";

function isSnoozed(a: Alert): boolean {
  return !!a.snoozed_until && Date.parse(a.snoozed_until) > Date.now();
}

function SnoozeDialog({ alert, onClose }: { alert: Alert; onClose: () => void }) {
  const qc = useQueryClient();
  const [until, setUntil] = useState(() => toLocalInput(new Date(Date.now() + 3600_000)));
  const iso = localInputToIso(until);
  const inFuture = iso !== null && Date.parse(iso) > Date.now();
  const snooze = useMutation({
    mutationFn: () => api.post<Alert>(`/alerts/${alert.id}/snooze`, { until: iso }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["alerts"] });
      onClose();
    },
  });
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title="Snooze alert"
      description={<>“{alert.title}” stays recorded but is marked snoozed until the time below. It still resolves by itself when the condition clears.</>}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" loading={snooze.isPending} disabled={!inFuture} onClick={() => snooze.mutate()}>
            Snooze
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <div className="flex flex-wrap gap-2" role="group" aria-label="Quick durations">
          {[
            ["1 h", 1],
            ["8 h", 8],
            ["24 h", 24],
            ["7 d", 168],
          ].map(([label, hours]) => (
            <Button key={label} size="sm" onClick={() => setUntil(toLocalInput(new Date(Date.now() + Number(hours) * 3600_000)))}>
              {label}
            </Button>
          ))}
        </div>
        <Field label="Snooze until (your local time)" htmlFor="snooze-until" error={until && !inFuture ? "Pick a time in the future." : null}>
          <Input id="snooze-until" type="datetime-local" value={until} onChange={(e) => setUntil(e.target.value)} />
        </Field>
        <FormError error={snooze.error} />
      </div>
    </Dialog>
  );
}

function AlertLinks({ alert }: { alert: Alert }) {
  if (!alert.asset_id && !alert.finding_id) return null;
  return (
    <span className="flex flex-wrap gap-x-3 gap-y-1 text-xs">
      {alert.asset_id != null && (
        <Link to={`/devices/${alert.asset_id}`} className="text-accent underline-offset-2 hover:underline">
          Open device
        </Link>
      )}
      {alert.finding_id != null && (
        <Link to={`/security/findings/${alert.finding_id}`} className="text-accent underline-offset-2 hover:underline">
          Open finding & evidence
        </Link>
      )}
    </span>
  );
}

export function ActiveAlerts() {
  const { isAdmin } = useAuth();
  const qc = useQueryClient();
  const [snoozing, setSnoozing] = useState<Alert | null>(null);
  const alerts = useQuery({ queryKey: ["alerts", "active"], queryFn: () => api.get<Alert[]>("/alerts", { state: "active" }), refetchInterval: 15_000 });
  const act = useMutation({
    mutationFn: ({ id, action }: { id: number; action: "acknowledge" | "resolve" }) => api.post<Alert>(`/alerts/${id}/${action}`),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["alerts"] });
      void qc.invalidateQueries({ queryKey: ["overview"] });
    },
  });

  if (alerts.isLoading) return <Skeleton className="h-40 w-full" />;
  if (alerts.isError) return <ErrorState error={alerts.error} retry={() => void alerts.refetch()} />;
  if (!alerts.data?.length) {
    return (
      <Card>
        <EmptyState icon={<CheckCircle2 className="h-5 w-5" />} title="No active alerts">
          Nothing is firing. This reflects only the rules that are enabled and the sources that are reporting — it is not a statement that the
          network is healthy.
        </EmptyState>
      </Card>
    );
  }
  return (
    <div className="space-y-3">
      <FormError error={act.error} />
      <ul className="space-y-3" aria-label="Active alerts">
        {alerts.data.map((a) => (
          <li key={a.id}>
            <Card className="p-4">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <SeverityBadge severity={a.severity} />
                    <Badge tone={a.state === "firing" ? "crit" : "info"} icon={a.state === "acknowledged" ? CheckCheck : undefined}>
                      {a.state === "firing" ? "Firing" : "Acknowledged"}
                    </Badge>
                    {isSnoozed(a) && (
                      <Badge icon={BellOff} title={formatDateTime(a.snoozed_until)}>
                        Snoozed until {formatDateTime(a.snoozed_until)}
                      </Badge>
                    )}
                    <Badge>{kindLabel(a.kind)}</Badge>
                    {a.is_demo && <DemoTag />}
                  </div>
                  <h3 className="mt-2 text-sm font-medium text-ink">{a.title}</h3>
                  <p className="mt-1 max-w-3xl text-xs leading-relaxed text-muted">{a.summary}</p>
                  <dl className="mt-2 flex flex-wrap gap-x-5 gap-y-1 text-[11px] text-faint">
                    <div className="flex gap-1">
                      <dt>Alert started</dt>
                      <dd>
                        <Timestamp ts={a.started_at} className="text-muted" />
                      </dd>
                    </div>
                    <div className="flex gap-1">
                      <dt>Triggering measurement</dt>
                      <dd>{a.source_ts ? <Timestamp ts={a.source_ts} className="text-muted" /> : <span className="text-muted">not recorded</span>}</dd>
                    </div>
                    {a.acknowledged_at && (
                      <div className="flex gap-1">
                        <dt>Acknowledged</dt>
                        <dd>
                          <Timestamp ts={a.acknowledged_at} className="text-muted" />
                        </dd>
                      </div>
                    )}
                  </dl>
                  <div className="mt-2">
                    <AlertLinks alert={a} />
                  </div>
                </div>
                {isAdmin && (
                  <div className="flex shrink-0 flex-wrap gap-2">
                    {a.state === "firing" && (
                      <Button size="sm" disabled={act.isPending} onClick={() => act.mutate({ id: a.id, action: "acknowledge" })}>
                        <CheckCheck className="h-3.5 w-3.5" aria-hidden /> Acknowledge
                      </Button>
                    )}
                    <Button size="sm" onClick={() => setSnoozing(a)}>
                      <Clock className="h-3.5 w-3.5" aria-hidden /> Snooze
                    </Button>
                    <Button size="sm" variant="ghost" disabled={act.isPending} onClick={() => act.mutate({ id: a.id, action: "resolve" })}>
                      <XCircle className="h-3.5 w-3.5" aria-hidden /> Close
                    </Button>
                  </div>
                )}
              </div>
            </Card>
          </li>
        ))}
      </ul>
      {!isAdmin && <p className="text-xs text-faint">Your role is read-only: acknowledging, snoozing and closing alerts needs an administrator.</p>}
      {snoozing && <SnoozeDialog alert={snoozing} onClose={() => setSnoozing(null)} />}
    </div>
  );
}

export function AlertHistory() {
  const alerts = useQuery({ queryKey: ["alerts", "resolved"], queryFn: () => api.get<Alert[]>("/alerts", { state: "resolved", limit: 200 }), refetchInterval: 60_000 });
  if (alerts.isLoading) return <Skeleton className="h-40 w-full" />;
  if (alerts.isError) return <ErrorState error={alerts.error} retry={() => void alerts.refetch()} />;
  return (
    <Card>
      <CardBody className="px-2 pt-2">
        {alerts.data?.length ? (
          <Table>
            <caption className="sr-only">Resolved alerts, newest first</caption>
            <thead>
              <tr>
                <Th>Severity</Th>
                <Th>Alert</Th>
                <Th>Started</Th>
                <Th>Resolved</Th>
                <Th>Duration</Th>
                <Th>How it ended</Th>
              </tr>
            </thead>
            <tbody>
              {alerts.data.map((a) => (
                <tr key={a.id} className="hover:bg-raised/40">
                  <Td>
                    <SeverityBadge severity={a.severity} />
                  </Td>
                  <Td className="max-w-md">
                    <p className="text-ink">{a.title}</p>
                    <p className="mt-0.5 flex flex-wrap items-center gap-x-3 text-xs text-faint">
                      {kindLabel(a.kind)}
                      <AlertLinks alert={a} />
                    </p>
                  </Td>
                  <Td className="text-xs text-muted">{formatDateTime(a.started_at)}</Td>
                  <Td className="text-xs text-muted">{formatDateTime(a.resolved_at)}</Td>
                  <Td className="num text-xs text-muted">
                    {a.resolved_at ? formatDuration((Date.parse(a.resolved_at) - Date.parse(a.started_at)) / 1000) : "—"}
                  </Td>
                  <Td className="text-xs text-muted">{a.resolved_reason ?? "—"}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
        ) : (
          <EmptyState title="No resolved alerts yet">Alerts appear here once their condition clears or an administrator closes them.</EmptyState>
        )}
      </CardBody>
    </Card>
  );
}
