import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CalendarClock, Plus, Trash2 } from "lucide-react";
import { useState } from "react";

import { Dialog } from "@/components/ui/overlays";
import { Button, Card, CardBody, CardHeader, EmptyState, ErrorState, Field, Input, Skeleton, Table, Td, Th } from "@/components/ui/primitives";
import { Badge } from "@/components/ui/status";
import { FormError } from "@/features/alerts/shared";
import { kindLabel, localInputToIso, toLocalInput } from "@/features/alerts/util";
import { useAuth } from "@/hooks/useAuth";
import { api, type AlertRule, type Maintenance } from "@/lib/api";
import { formatDateTime } from "@/lib/format";

function windowState(w: Maintenance, now: number): { label: string; tone: "good" | "info" | "neutral" } {
  const start = Date.parse(w.starts_at);
  const end = Date.parse(w.ends_at);
  if (now < start) return { label: "Upcoming", tone: "info" };
  if (now < end) return { label: "Active now", tone: "good" };
  return { label: "Ended", tone: "neutral" };
}

function CreateDialog({ kinds, onClose }: { kinds: string[]; onClose: () => void }) {
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [starts, setStarts] = useState(() => toLocalInput(new Date()));
  const [ends, setEnds] = useState(() => toLocalInput(new Date(Date.now() + 2 * 3600_000)));
  const [selected, setSelected] = useState<string[]>([]);
  const startIso = localInputToIso(starts);
  const endIso = localInputToIso(ends);
  const rangeError =
    !startIso || !endIso
      ? "Enter both a start and an end time."
      : Date.parse(endIso) <= Date.parse(startIso)
        ? "The window must end after it starts."
        : Date.parse(endIso) - Date.parse(startIso) > 14 * 86400_000
          ? "A window may last at most 14 days."
          : null;
  const create = useMutation({
    mutationFn: () => api.post<Maintenance>("/maintenance-windows", { name: name.trim(), starts_at: startIso, ends_at: endIso, alert_kinds: selected }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["maintenance-windows"] });
      onClose();
    },
  });
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title="New maintenance window"
      description="While the window is active, new alerts of the selected kinds are not created. Alerts that are already firing are not touched."
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" loading={create.isPending} disabled={!name.trim() || !!rangeError} onClick={() => create.mutate()}>
            Create window
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <Field label="Name" htmlFor="mw-name" hint="For example “Router firmware update”.">
          <Input id="mw-name" value={name} maxLength={120} onChange={(e) => setName(e.target.value)} autoFocus />
        </Field>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Starts (local time)" htmlFor="mw-start">
            <Input id="mw-start" type="datetime-local" value={starts} onChange={(e) => setStarts(e.target.value)} />
          </Field>
          <Field label="Ends (local time)" htmlFor="mw-end" error={rangeError}>
            <Input id="mw-end" type="datetime-local" value={ends} onChange={(e) => setEnds(e.target.value)} />
          </Field>
        </div>
        <fieldset>
          <legend className="text-xs font-medium text-muted">Alert kinds to suppress</legend>
          <p className="mt-0.5 text-[11px] text-faint">Leave everything unchecked to suppress all kinds.</p>
          <div className="mt-2 grid gap-1.5 sm:grid-cols-2">
            {kinds.map((k) => (
              <label key={k} className="flex cursor-pointer items-center gap-2 rounded-md px-1.5 py-1 text-xs text-ink hover:bg-raised">
                <input
                  type="checkbox"
                  className="h-3.5 w-3.5 accent-cyan-400"
                  checked={selected.includes(k)}
                  onChange={(e) => setSelected((s) => (e.target.checked ? [...s, k] : s.filter((x) => x !== k)))}
                />
                {kindLabel(k)}
              </label>
            ))}
          </div>
        </fieldset>
        <FormError error={create.error} />
      </div>
    </Dialog>
  );
}

export function MaintenanceWindows() {
  const { isAdmin } = useAuth();
  const qc = useQueryClient();
  const [creating, setCreating] = useState(false);
  const windows = useQuery({ queryKey: ["maintenance-windows"], queryFn: () => api.get<Maintenance[]>("/maintenance-windows") });
  const rules = useQuery({ queryKey: ["alert-rules"], queryFn: () => api.get<AlertRule[]>("/alert-rules") });
  const remove = useMutation({
    mutationFn: (id: number) => api.delete<void>(`/maintenance-windows/${id}`),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["maintenance-windows"] }),
  });
  const now = Date.now();

  return (
    <Card>
      <CardHeader
        icon={<CalendarClock className="h-4 w-4" />}
        title="Maintenance windows"
        subtitle="Planned work: suppress new alerts of chosen kinds for a bounded period (max 14 days). Windows from the last 7 days are listed."
        actions={
          isAdmin && (
            <Button size="sm" variant="primary" onClick={() => setCreating(true)}>
              <Plus className="h-3.5 w-3.5" aria-hidden /> New window
            </Button>
          )
        }
      />
      <CardBody className="px-2">
        {windows.isLoading ? (
          <Skeleton className="mx-3 h-24" />
        ) : windows.isError ? (
          <ErrorState error={windows.error} retry={() => void windows.refetch()} />
        ) : windows.data?.length ? (
          <Table>
            <thead>
              <tr>
                <Th>Name</Th>
                <Th>State</Th>
                <Th>Starts</Th>
                <Th>Ends</Th>
                <Th>Suppresses</Th>
                {isAdmin && <Th className="text-right">Actions</Th>}
              </tr>
            </thead>
            <tbody>
              {windows.data.map((w) => {
                const st = windowState(w, now);
                return (
                  <tr key={w.id}>
                    <Td className="font-medium">{w.name}</Td>
                    <Td>
                      <Badge tone={st.tone}>{st.label}</Badge>
                    </Td>
                    <Td className="text-xs text-muted">{formatDateTime(w.starts_at)}</Td>
                    <Td className="text-xs text-muted">{formatDateTime(w.ends_at)}</Td>
                    <Td className="text-xs text-muted">{w.alert_kinds.length ? w.alert_kinds.map(kindLabel).join(", ") : "All alert kinds"}</Td>
                    {isAdmin && (
                      <Td className="text-right">
                        <Button size="sm" variant="ghost" disabled={remove.isPending} onClick={() => remove.mutate(w.id)} aria-label={`Delete window ${w.name}`}>
                          <Trash2 className="h-3.5 w-3.5" aria-hidden /> Delete
                        </Button>
                      </Td>
                    )}
                  </tr>
                );
              })}
            </tbody>
          </Table>
        ) : (
          <EmptyState icon={<CalendarClock className="h-5 w-5" />} title="No maintenance windows">
            Create one before planned work (router reboot, ISP maintenance) so expected latency or heartbeat alerts are not raised.
          </EmptyState>
        )}
        <div className="px-3">
          <FormError error={remove.error} />
        </div>
      </CardBody>
      {creating && <CreateDialog kinds={rules.data?.map((r) => r.kind) ?? []} onClose={() => setCreating(false)} />}
    </Card>
  );
}
