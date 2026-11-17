import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Crosshair, Pencil, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";

import { Dialog, Switch } from "@/components/ui/overlays";
import { Button, Card, CardBody, CardHeader, EmptyState, ErrorState, Field, Input, NativeSelect, Notice, Skeleton, Table, Td, Th } from "@/components/ui/primitives";
import { Badge, DemoTag } from "@/components/ui/status";
import { FormError } from "@/features/alerts/shared";
import { ReadOnlyNote } from "@/features/settings/shared";
import { useAuth } from "@/hooks/useAuth";
import { api, type ProbeTarget, type ProbeTargetIn } from "@/lib/api";

const KIND_LABEL: Record<string, string> = {
  gateway_icmp: "Gateway ICMP echo",
  icmp: "ICMP echo",
  tcp: "TCP handshake",
  dns: "DNS resolution",
};
const TCP_PORTS = [53, 80, 443, 853];

interface Draft {
  name: string;
  kind: ProbeTargetIn["kind"];
  scope: ProbeTargetIn["scope"];
  host: string;
  port: string;
  interval_s: string;
  timeout_ms: string;
  enabled: boolean;
}

function toDraft(t?: ProbeTarget): Draft {
  return t
    ? { name: t.name, kind: t.kind as Draft["kind"], scope: t.scope as Draft["scope"], host: t.host, port: t.port ? String(t.port) : "443", interval_s: String(t.interval_s), timeout_ms: String(t.timeout_ms), enabled: t.enabled }
    : { name: "", kind: "tcp", scope: "external", host: "", port: "443", interval_s: "30", timeout_ms: "3000", enabled: true };
}

// Each probe kind only makes sense with certain scopes (enforced again by the server).
const SCOPES: Record<Draft["kind"], Draft["scope"][]> = {
  gateway_icmp: ["gateway"],
  icmp: ["internal"],
  tcp: ["external", "internal"],
  dns: ["external"],
};

function TargetDialog({ target, onClose }: { target?: ProbeTarget; onClose: () => void }) {
  const qc = useQueryClient();
  const [d, setD] = useState<Draft>(() => toDraft(target));
  const set = <K extends keyof Draft>(k: K, v: Draft[K]) => setD((prev) => ({ ...prev, [k]: v }));
  const interval = Number(d.interval_s);
  const timeout = Number(d.timeout_ms);
  const minInterval = d.scope === "external" ? 10 : 1;
  const intervalError = !Number.isFinite(interval) || interval < minInterval || interval > 3600 ? `Between ${minInterval} and 3600 s for ${d.scope} targets.` : null;
  const timeoutError = !Number.isInteger(timeout) || timeout < 100 || timeout > 10000 ? "Between 100 and 10000 ms." : null;
  const valid = d.name.trim() && d.host.trim() && !intervalError && !timeoutError;

  const save = useMutation({
    mutationFn: () => {
      const body: ProbeTargetIn = {
        name: d.name.trim(),
        kind: d.kind,
        scope: d.scope,
        host: d.host.trim(),
        port: d.kind === "tcp" ? Number(d.port) : null,
        interval_s: interval,
        timeout_ms: timeout,
        enabled: d.enabled,
      };
      return target ? api.patch<ProbeTarget>(`/probe-targets/${target.id}`, body) : api.post<ProbeTarget>("/probe-targets", body);
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["probe-targets"] });
      void qc.invalidateQueries({ queryKey: ["connectivity"] });
      onClose();
    },
  });

  const hostHint =
    d.kind === "gateway_icmp"
      ? "“auto” = each agent's own default gateway. A literal IP must be inside an approved CIDR."
      : d.kind === "dns"
        ? "A public hostname to resolve through the agent's system resolver, e.g. example.com."
        : d.scope === "external"
          ? "A public IP address or hostname."
          : "A literal private IP address inside an approved CIDR.";

  return (
    <Dialog
      open
      wide
      onOpenChange={(o) => !o && onClose()}
      title={target ? `Edit “${target.name}”` : "New probe target"}
      description="Probes run on your agents, not on the server. Every target is validated against the network scope by the server and again by the agent."
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" loading={save.isPending} disabled={!valid} onClick={() => save.mutate()}>
            {target ? "Save target" : "Create target"}
          </Button>
        </>
      }
    >
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="sm:col-span-2">
          <Field label="Name" htmlFor="pt-name">
            <Input id="pt-name" value={d.name} maxLength={120} autoFocus onChange={(e) => set("name", e.target.value)} />
          </Field>
        </div>
        <Field label="Probe type" htmlFor="pt-kind">
          <NativeSelect
            id="pt-kind"
            value={d.kind}
            onChange={(e) => {
              const kind = e.target.value as Draft["kind"];
              setD((prev) => ({ ...prev, kind, scope: SCOPES[kind][0]!, host: kind === "gateway_icmp" && !prev.host ? "auto" : prev.host }));
            }}
          >
            {Object.entries(KIND_LABEL).map(([k, l]) => (
              <option key={k} value={k}>
                {l}
              </option>
            ))}
          </NativeSelect>
        </Field>
        <Field label="Scope" htmlFor="pt-scope" hint={d.scope === "external" ? "A public endpoint; checked at most every 10 s." : "Your own network; needs an approved CIDR."}>
          <NativeSelect id="pt-scope" value={d.scope} disabled={SCOPES[d.kind].length === 1} onChange={(e) => set("scope", e.target.value as Draft["scope"])}>
            {SCOPES[d.kind].map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </NativeSelect>
        </Field>
        <Field label={d.kind === "dns" ? "Name to resolve" : "Host"} htmlFor="pt-host" hint={hostHint}>
          <Input id="pt-host" value={d.host} maxLength={255} onChange={(e) => set("host", e.target.value)} className="font-mono" />
        </Field>
        {d.kind === "tcp" ? (
          <Field label="TCP port" htmlFor="pt-port" hint="Limited to an allowlist so probes cannot be used as a port scanner.">
            <NativeSelect id="pt-port" value={d.port} onChange={(e) => set("port", e.target.value)}>
              {TCP_PORTS.map((p) => (
                <option key={p} value={p}>
                  {p}
                </option>
              ))}
            </NativeSelect>
          </Field>
        ) : (
          <div />
        )}
        <Field label="Interval (s)" htmlFor="pt-interval" error={intervalError}>
          <Input id="pt-interval" type="number" min={minInterval} max={3600} step="any" value={d.interval_s} onChange={(e) => set("interval_s", e.target.value)} className="num" />
        </Field>
        <Field label="Timeout (ms)" htmlFor="pt-timeout" error={timeoutError}>
          <Input id="pt-timeout" type="number" min={100} max={10000} value={d.timeout_ms} onChange={(e) => set("timeout_ms", e.target.value)} className="num" />
        </Field>
        <div className="flex items-center gap-2 text-sm text-ink sm:col-span-2">
          <Switch checked={d.enabled} onCheckedChange={(v) => set("enabled", v)} label="Target enabled" />
          <span>{d.enabled ? "Enabled — agents probe this target" : "Disabled — kept but not probed"}</span>
        </div>
        <div className="sm:col-span-2">
          <FormError error={save.error} />
        </div>
      </div>
    </Dialog>
  );
}

function DeleteDialog({ target, onClose }: { target: ProbeTarget; onClose: () => void }) {
  const qc = useQueryClient();
  const remove = useMutation({
    mutationFn: () => api.delete<void>(`/probe-targets/${target.id}`),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["probe-targets"] });
      void qc.invalidateQueries({ queryKey: ["connectivity"] });
      onClose();
    },
  });
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title={`Delete “${target.name}”?`}
      description="Agents stop probing it and its stored probe results are deleted. To pause it instead, edit the target and disable it."
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="danger" loading={remove.isPending} onClick={() => remove.mutate()}>
            Delete target
          </Button>
        </>
      }
    >
      <FormError error={remove.error} />
    </Dialog>
  );
}

export function ProbesTab() {
  const { isAdmin } = useAuth();
  const [editing, setEditing] = useState<ProbeTarget | "new" | null>(null);
  const [deleting, setDeleting] = useState<ProbeTarget | null>(null);
  const targets = useQuery({ queryKey: ["probe-targets"], queryFn: () => api.get<ProbeTarget[]>("/probe-targets") });

  return (
    <div className="space-y-4">
      {!isAdmin && <ReadOnlyNote />}
      <Notice tone="info" icon={<Crosshair className="h-4 w-4" />} title="Probe limits">
        Probes are narrow health checks, not scans. Gateway and internal targets must be literal private addresses inside an approved CIDR (
        <Link to="/settings/scope" className="text-accent underline-offset-2 hover:underline">
          Network scope
        </Link>
        ); <span className="font-mono">auto</span> means each agent's own default gateway. External targets are checked at most every 10 s. TCP
        probes complete a handshake on port 53, 80, 443 or 853 and send no application data. Loopback, link-local and cloud-metadata addresses
        are always refused. At most 20 targets.
      </Notice>
      <Card>
        <CardHeader
          title="Probe targets"
          subtitle="What agents measure latency and reachability against. Changes reach agents on their next configuration poll."
          actions={
            isAdmin && (
              <Button size="sm" variant="primary" onClick={() => setEditing("new")}>
                <Plus className="h-3.5 w-3.5" aria-hidden /> New target
              </Button>
            )
          }
        />
        <CardBody className="px-2">
          {targets.isLoading ? (
            <Skeleton className="mx-3 h-40" />
          ) : targets.isError ? (
            <ErrorState error={targets.error} retry={() => void targets.refetch()} />
          ) : targets.data?.length ? (
            <Table>
              <thead>
                <tr>
                  <Th>Name</Th>
                  <Th>Type</Th>
                  <Th>Scope</Th>
                  <Th>Host</Th>
                  <Th>Port</Th>
                  <Th>Interval</Th>
                  <Th>Timeout</Th>
                  <Th>State</Th>
                  {isAdmin && <Th className="text-right">Actions</Th>}
                </tr>
              </thead>
              <tbody>
                {targets.data.map((t) => (
                  <tr key={t.id}>
                    <Td className="font-medium">
                      {t.name} {t.is_demo && <DemoTag />}
                    </Td>
                    <Td className="text-xs text-muted">{KIND_LABEL[t.kind] ?? t.kind}</Td>
                    <Td>
                      <Badge>{t.scope}</Badge>
                    </Td>
                    <Td className="font-mono text-xs">{t.host}</Td>
                    <Td className="num text-xs text-muted">{t.port ?? "—"}</Td>
                    <Td className="num text-xs text-muted">{t.interval_s} s</Td>
                    <Td className="num text-xs text-muted">{t.timeout_ms} ms</Td>
                    <Td>
                      <Badge tone={t.enabled ? "good" : "neutral"}>{t.enabled ? "Enabled" : "Disabled"}</Badge>
                    </Td>
                    {isAdmin && (
                      <Td className="whitespace-nowrap text-right">
                        <Button size="sm" variant="ghost" onClick={() => setEditing(t)} aria-label={`Edit target ${t.name}`}>
                          <Pencil className="h-3.5 w-3.5" aria-hidden /> Edit
                        </Button>
                        <Button size="sm" variant="ghost" onClick={() => setDeleting(t)} aria-label={`Delete target ${t.name}`}>
                          <Trash2 className="h-3.5 w-3.5" aria-hidden /> Delete
                        </Button>
                      </Td>
                    )}
                  </tr>
                ))}
              </tbody>
            </Table>
          ) : (
            <EmptyState icon={<Crosshair className="h-5 w-5" />} title="No probe targets">
              Without targets, gateway latency, packet loss and internet reachability are unavailable.
            </EmptyState>
          )}
        </CardBody>
      </Card>
      {editing && <TargetDialog target={editing === "new" ? undefined : editing} onClose={() => setEditing(null)} />}
      {deleting && <DeleteDialog target={deleting} onClose={() => setDeleting(null)} />}
    </div>
  );
}
