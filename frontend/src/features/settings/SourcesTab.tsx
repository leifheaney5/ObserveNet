import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, FlaskConical, Pencil, Radio, Router, ServerCog, XCircle } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";

import { Dialog, Switch } from "@/components/ui/overlays";
import { Button, Card, CardBody, CardHeader, EmptyState, ErrorState, Field, Input, Notice, Skeleton, Table, Td, Th } from "@/components/ui/primitives";
import { Badge, DemoTag, StateBadge, Timestamp } from "@/components/ui/status";
import { FormError } from "@/features/alerts/shared";
import { useAuth } from "@/hooks/useAuth";
import { api, type Capabilities, type Schemas, type Source } from "@/lib/api";
import { titleCase } from "@/lib/format";

type SourceTest = Schemas["SourceTestOut"];

const KIND_LABEL: Record<string, string> = {
  linux_agent: "Linux agent",
  windows_agent: "Windows agent",
  router_snmp: "Router (SNMP)",
  client_import: "Imported client list",
  demo: "Demo generator",
};

function RenameDialog({ source, onClose }: { source: Source; onClose: () => void }) {
  const qc = useQueryClient();
  const [name, setName] = useState(source.name);
  const rename = useMutation({
    mutationFn: () => api.patch<Source>(`/sources/${source.id}`, { name: name.trim() }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["sources"] });
      onClose();
    },
  });
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title="Rename data source"
      description="The name is a label in ObserveNet only; it does not change the host."
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" loading={rename.isPending} disabled={!name.trim() || name.trim() === source.name} onClick={() => rename.mutate()}>
            Save name
          </Button>
        </>
      }
    >
      <Field label="Name" htmlFor="source-name">
        <Input id="source-name" value={name} maxLength={120} autoFocus onChange={(e) => setName(e.target.value)} />
      </Field>
      <div className="mt-2">
        <FormError error={rename.error} />
      </div>
    </Dialog>
  );
}

function DisableDialog({ source, onClose }: { source: Source; onClose: () => void }) {
  const qc = useQueryClient();
  const disable = useMutation({
    mutationFn: () => api.patch<Source>(`/sources/${source.id}`, { enabled: false }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["sources"] });
      onClose();
    },
  });
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title={`Disable “${source.name}”?`}
      description="The backend will refuse this source's data (HTTP 403) until it is enabled again. Existing history is kept. The agent itself keeps running on its host."
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="danger" loading={disable.isPending} onClick={() => disable.mutate()}>
            Disable source
          </Button>
        </>
      }
    >
      <FormError error={disable.error} />
    </Dialog>
  );
}

function SourceCard({ source }: { source: Source }) {
  const { isAdmin } = useAuth();
  const qc = useQueryClient();
  const [renaming, setRenaming] = useState(false);
  const [disabling, setDisabling] = useState(false);
  const [result, setResult] = useState<SourceTest | null>(null);
  const enable = useMutation({
    mutationFn: () => api.patch<Source>(`/sources/${source.id}`, { enabled: true }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["sources"] }),
  });
  const test = useMutation({
    mutationFn: () => api.post<SourceTest>(`/sources/${source.id}/test`),
    onSuccess: (r) => {
      setResult(r);
      void qc.invalidateQueries({ queryKey: ["sources"] });
    },
  });
  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-3 px-5 pt-4">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="text-sm font-semibold text-ink">{source.name}</h3>
            <Badge>{KIND_LABEL[source.kind] ?? titleCase(source.kind)}</Badge>
            <StateBadge state={source.enabled ? source.freshness : "disabled"} />
            {source.is_demo && <DemoTag />}
          </div>
          <p className="mt-1 text-xs text-muted">
            {[source.platform && `platform ${source.platform}`, source.version && `agent ${source.version}`, `observation point: ${titleCase(source.observation_point)}`]
              .filter(Boolean)
              .join(" · ")}
          </p>
          <p className="mt-0.5 text-xs text-faint">
            Last data: <Timestamp ts={source.last_seen_at} className="text-muted" /> · added <Timestamp ts={source.created_at} />
            {source.asset_id != null && (
              <>
                {" · "}
                <Link to={`/devices/${source.asset_id}`} className="text-accent underline-offset-2 hover:underline">
                  host device
                </Link>
              </>
            )}
          </p>
        </div>
        {isAdmin && (
          <div className="flex flex-wrap items-center gap-2">
            <Button size="sm" loading={test.isPending} onClick={() => test.mutate()}>
              <FlaskConical className="h-3.5 w-3.5" aria-hidden /> Test
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setRenaming(true)}>
              <Pencil className="h-3.5 w-3.5" aria-hidden /> Rename
            </Button>
            <span className="flex items-center gap-2 pl-1 text-xs text-muted">
              {source.enabled ? "Enabled" : "Disabled"}
              <Switch
                checked={source.enabled}
                disabled={enable.isPending}
                label={`${source.enabled ? "Disable" : "Enable"} source ${source.name}`}
                onCheckedChange={(v) => (v ? enable.mutate() : setDisabling(true))}
              />
            </span>
          </div>
        )}
      </div>
      <CardBody className="space-y-3">
        {source.last_error && (
          <Notice tone="warn" icon={<AlertTriangle className="h-4 w-4" />} title="Collector reported a problem">
            <span className="break-words font-mono">{source.last_error}</span>
          </Notice>
        )}
        <FormError error={test.error ?? enable.error} />
        {result && (
          <div className="rounded-lg border border-line bg-bg/50 p-3" role="status" aria-live="polite">
            <p className="flex items-center gap-2 text-xs font-medium text-ink">
              {result.ok ? <CheckCircle2 className="h-4 w-4 text-good" aria-hidden /> : <XCircle className="h-4 w-4 text-warn" aria-hidden />}
              Test {result.ok ? "passed" : "found problems"} · freshness: {result.freshness}
            </p>
            <ul className="mt-2 space-y-1">
              {(result.checks as { name: string; ok: boolean; detail: string }[]).map((c) => (
                <li key={c.name} className="flex items-start gap-2 text-xs">
                  <Badge tone={c.ok ? "good" : "warn"} icon={c.ok ? CheckCircle2 : AlertTriangle}>
                    {c.ok ? "OK" : "Check"}
                  </Badge>
                  <span className="text-muted">
                    <span className="font-mono text-ink">{c.name}</span> — {c.detail}
                  </span>
                </li>
              ))}
            </ul>
            <p className="mt-2 text-[11px] leading-relaxed text-faint">
              {result.note} The test only evaluates what the backend already received; it sends no probe of its own.
            </p>
          </div>
        )}
        {source.capabilities.length ? (
          <Table>
            <caption className="sr-only">Capabilities reported by {source.name}</caption>
            <thead>
              <tr>
                <Th>Capability</Th>
                <Th>Status</Th>
                <Th>Detail</Th>
                <Th>Updated</Th>
              </tr>
            </thead>
            <tbody>
              {source.capabilities.map((c) => (
                <tr key={c.name}>
                  <Td className="whitespace-nowrap font-mono text-xs">{c.name}</Td>
                  <Td>
                    <StateBadge state={c.status} />
                  </Td>
                  <Td className="text-xs leading-relaxed text-muted">{c.detail ?? "—"}</Td>
                  <Td className="text-xs text-faint">
                    <Timestamp ts={c.updated_at} />
                  </Td>
                </tr>
              ))}
            </tbody>
          </Table>
        ) : (
          <p className="text-xs text-faint">
            {source.kind === "client_import"
              ? "A point-in-time import: it carries device identities only — no counters, signal or settings. Re-import to refresh."
              : "This source has not reported its capabilities yet."}
          </p>
        )}
      </CardBody>
      {renaming && <RenameDialog source={source} onClose={() => setRenaming(false)} />}
      {disabling && <DisableDialog source={source} onClose={() => setDisabling(false)} />}
    </Card>
  );
}

interface RouterProvider {
  configured: string;
  capabilities: { capability: string; status: string; detail: string }[];
  planned_adapters: { key: string; title: string; would_provide: string[]; status: string; blocked_on: string }[];
}

export function SourcesTab() {
  const sources = useQuery({ queryKey: ["sources"], queryFn: () => api.get<Source[]>("/sources"), refetchInterval: 10_000 });
  const caps = useQuery({ queryKey: ["capabilities"], queryFn: () => api.get<Capabilities>("/capabilities"), refetchInterval: 30_000 });
  const router = caps.data?.router_provider as RouterProvider | undefined;

  return (
    <div className="space-y-5">
      <section aria-labelledby="sources-heading" className="space-y-3">
        <div>
          <h2 id="sources-heading" className="text-sm font-semibold text-ink">
            Connected sources
          </h2>
          <p className="mt-0.5 text-xs text-muted">Every number in ObserveNet traces back to one of these. A source can only report what it can observe from where it sits.</p>
        </div>
        {sources.isLoading ? (
          <Skeleton className="h-40 w-full" />
        ) : sources.isError ? (
          <Card>
            <ErrorState error={sources.error} retry={() => void sources.refetch()} />
          </Card>
        ) : sources.data?.length ? (
          sources.data.map((s) => <SourceCard key={s.id} source={s} />)
        ) : (
          <Card>
            <EmptyState
              icon={<ServerCog className="h-5 w-5" />}
              title="No data source has reported yet"
              action={
                <Link to="/settings/agents">
                  <Button size="sm">Set up an agent</Button>
                </Link>
              }
            >
              Start the bundled Linux collector (<span className="font-mono">docker compose --profile collector up -d</span>) or create an agent
              token and run an agent. Until then every metric stays empty — ObserveNet does not invent data.
            </EmptyState>
          </Card>
        )}
      </section>

      <Card>
        <CardHeader
          icon={<Radio className="h-4 w-4" />}
          title="What can be observed right now"
          subtitle="Network-level capability matrix, derived from the sources above. “Unsupported” means no connected source can measure it — not that the value is zero."
        />
        <CardBody className="px-2">
          {caps.isLoading ? (
            <Skeleton className="mx-3 h-48" />
          ) : caps.isError ? (
            <ErrorState error={caps.error} retry={() => void caps.refetch()} />
          ) : (
            <Table>
              <thead>
                <tr>
                  <Th>Measurement</Th>
                  <Th>Status</Th>
                  <Th>Coverage & limits</Th>
                  <Th>Provided by</Th>
                </tr>
              </thead>
              <tbody>
                {caps.data?.network.map((c) => (
                  <tr key={c.key}>
                    <Td className="font-medium">{c.title}</Td>
                    <Td>
                      <StateBadge state={c.status} />
                    </Td>
                    <Td className="max-w-xl text-xs leading-relaxed text-muted">
                      {c.detail}
                      {c.guidance && <p className="mt-1 text-faint">{c.guidance}</p>}
                    </Td>
                    <Td className="text-xs text-muted">{c.sources?.length ? c.sources.join(", ") : <span className="text-faint">none</span>}</Td>
                  </tr>
                ))}
              </tbody>
            </Table>
          )}
        </CardBody>
      </Card>

      <Card>
        <CardHeader
          icon={<Router className="h-4 w-4" />}
          title="Gateway / router provider"
          subtitle="Whole-network and per-device measurements need a source at the gateway. ObserveNet only ships adapters for interfaces that were actually tested."
          actions={router && <Badge tone={router.configured === "none" ? "neutral" : "info"}>Configured: {router.configured}</Badge>}
        />
        <CardBody className="space-y-4">
          {caps.isLoading ? (
            <Skeleton className="h-40" />
          ) : router ? (
            <>
              <Table>
                <caption className="sr-only">Router provider capabilities</caption>
                <thead>
                  <tr>
                    <Th>Router capability</Th>
                    <Th>Status</Th>
                    <Th>Detail</Th>
                  </tr>
                </thead>
                <tbody>
                  {router.capabilities.map((c) => (
                    <tr key={c.capability}>
                      <Td className="font-mono text-xs">{c.capability}</Td>
                      <Td>
                        <StateBadge state={c.status} />
                      </Td>
                      <Td className="text-xs text-muted">{c.detail}</Td>
                    </tr>
                  ))}
                </tbody>
              </Table>
              <div>
                <h3 className="text-xs font-medium uppercase tracking-wider text-faint">Planned adapters — not implemented</h3>
                <ul className="mt-2 grid gap-3 md:grid-cols-3">
                  {router.planned_adapters.map((p) => (
                    <li key={p.key} className="rounded-lg border border-dashed border-line p-3">
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <span className="text-sm font-medium text-ink">{p.title}</span>
                        <StateBadge state="not_implemented" />
                      </div>
                      <p className="mt-1.5 text-xs text-muted">Would provide: {p.would_provide.map((w) => w.replace(/_/g, " ")).join(", ")}.</p>
                      <p className="mt-1 text-xs text-faint">Blocked on: {p.blocked_on}</p>
                    </li>
                  ))}
                </ul>
              </div>
              <Notice tone="info" title="Vendor-neutral path available today: import your router's client list">
                Any router that can show or export its DHCP / client table can feed the inventory. Open{" "}
                <Link to="/devices" className="text-accent underline-offset-2 hover:underline">
                  Devices
                </Link>{" "}
                and use “Import client list” (CSV with a header row, or JSON). It is a point-in-time snapshot: it adds identities and
                hostnames, not live presence or traffic.
              </Notice>
            </>
          ) : (
            <ErrorState error={caps.error} retry={() => void caps.refetch()} />
          )}
        </CardBody>
      </Card>
    </div>
  );
}
