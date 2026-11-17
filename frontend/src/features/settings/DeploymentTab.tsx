import { useQuery } from "@tanstack/react-query";
import { DatabaseBackup, Globe, ScrollText, TriangleAlert } from "lucide-react";

import { Card, CardBody, CardHeader, Code, EmptyState, ErrorState, Notice, Skeleton, Table, Td, Th } from "@/components/ui/primitives";
import { Badge } from "@/components/ui/status";
import { AdminOnly, CodeBlock } from "@/features/settings/shared";
import { useAuth } from "@/hooks/useAuth";
import { api, type AuditRow } from "@/lib/api";
import { formatDateTime } from "@/lib/format";

const TARGET_HOST = "observenet.leifslab.com";

interface DeploymentInfo {
  public_url: string;
  allowed_hosts: string[];
  cookie_secure: boolean;
  https_configured: boolean;
  env: string;
  demo_mode: boolean;
  graphql_introspection: boolean;
  otel_export: boolean;
  neo4j: boolean;
  webhook: boolean;
  oui_file: boolean;
  note: string;
}

function Fact({ label, children, hint }: { label: string; children: React.ReactNode; hint?: string }) {
  return (
    <div className="rounded-lg border border-line bg-bg/40 px-3 py-2.5">
      <dt className="text-[11px] font-medium uppercase tracking-wider text-faint">{label}</dt>
      <dd className="mt-1 break-words text-sm text-ink">{children}</dd>
      {hint && <p className="mt-1 text-[11px] leading-snug text-faint">{hint}</p>}
    </div>
  );
}

function YesNo({ value, yes = "Yes", no = "No", goodWhen = true }: { value: boolean; yes?: string; no?: string; goodWhen?: boolean | null }) {
  const tone = goodWhen === null ? "neutral" : value === goodWhen ? "good" : "warn";
  return <Badge tone={tone}>{value ? yes : no}</Badge>;
}

export function DeploymentTab() {
  const { isAdmin } = useAuth();
  const info = useQuery({ queryKey: ["deployment"], queryFn: () => api.get<DeploymentInfo>("/settings/deployment"), enabled: isAdmin });
  if (!isAdmin) return <AdminOnly what="Deployment configuration" />;
  const d = info.data;
  const matchesTarget = d ? d.public_url.replace(/\/$/, "") === `https://${TARGET_HOST}` : false;
  const hostAllowed = d ? d.allowed_hosts.includes(TARGET_HOST) : false;

  return (
    <div className="space-y-4">
      <Notice tone="warn" icon={<TriangleAlert className="h-4 w-4" />} title="Configured values only — not a liveness check">
        {d?.note ?? "ObserveNet cannot verify DNS, the tunnel or the edge access policy from inside."} Nothing on this page means{" "}
        <span className="font-mono">{TARGET_HOST}</span> is reachable; confirm that from outside your network after following the runbook.
      </Notice>

      <Card>
        <CardHeader icon={<Globe className="h-4 w-4" />} title="Current configuration" subtitle="Read from the API process environment. Secrets are never shown." />
        <CardBody>
          {info.isLoading ? (
            <Skeleton className="h-40" />
          ) : info.isError || !d ? (
            <ErrorState error={info.error} retry={() => void info.refetch()} />
          ) : (
            <dl className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
              <Fact label="Public URL (OBSERVENET_PUBLIC_URL)" hint="Used for the WebSocket origin check and cookie security.">
                <span className="font-mono text-xs">{d.public_url}</span>
              </Fact>
              <Fact label="Allowed Host headers" hint="Requests with any other Host header are rejected.">
                <span className="font-mono text-xs">{d.allowed_hosts.join(", ") || "none"}</span>
              </Fact>
              <Fact label="Environment">
                <Badge>{d.env}</Badge> {d.demo_mode && <Badge tone="violet">demo mode</Badge>}
              </Fact>
              <Fact label="HTTPS public URL" hint="Required for the external hostname.">
                <YesNo value={d.https_configured} yes="Configured" no="Not configured (plain HTTP)" />
              </Fact>
              <Fact label="Secure session cookie" hint="Follows the public URL scheme unless overridden.">
                <YesNo value={d.cookie_secure} yes="Secure flag on" no="Secure flag off" />
              </Fact>
              <Fact label="GraphQL introspection" hint="Should be off for an internet-facing deployment.">
                <YesNo value={d.graphql_introspection} yes="Enabled" no="Disabled" goodWhen={false} />
              </Fact>
              <Fact label="Trace export (OTLP)">
                <YesNo value={d.otel_export} yes="Configured" no="Not configured" goodWhen={null} />
              </Fact>
              <Fact label="Alert webhook" hint="Off unless you configured a URL; nothing is sent to third parties by default.">
                <YesNo value={d.webhook} yes="Configured" no="Not configured" goodWhen={null} />
              </Fact>
              <Fact label="Optional integrations">
                <span className="flex flex-wrap gap-1.5">
                  <Badge tone={d.neo4j ? "info" : "neutral"}>Neo4j {d.neo4j ? "configured" : "off"}</Badge>
                  <Badge tone={d.oui_file ? "info" : "neutral"}>MAC vendor file {d.oui_file ? "provided" : "not provided"}</Badge>
                </span>
              </Fact>
            </dl>
          )}
        </CardBody>
      </Card>

      <Card>
        <CardHeader
          icon={<Globe className="h-4 w-4" />}
          title={`Remote access at ${TARGET_HOST}`}
          subtitle="Intended path: outbound-only Cloudflare Tunnel with a Cloudflare Access policy in front, plus this application's own login. No inbound router port forward."
          actions={d && <Badge tone={matchesTarget && hostAllowed ? "info" : "neutral"}>{matchesTarget && hostAllowed ? "App configured for this hostname" : "App not configured for this hostname"}</Badge>}
        />
        <CardBody className="space-y-3">
          <ol className="list-decimal space-y-1.5 pl-5 text-xs leading-relaxed text-muted">
            <li>
              The <span className="font-mono">leifslab.com</span> DNS zone must be under your control at the tunnel provider. ObserveNet does
              not assume it is.
            </li>
            <li>
              In <Code>.env</Code> set <Code>OBSERVENET_PUBLIC_URL=https://{TARGET_HOST}</Code> and{" "}
              <Code>OBSERVENET_ALLOWED_HOSTS={TARGET_HOST},localhost,127.0.0.1</Code>, then restart the stack.
            </li>
            <li>
              Create the tunnel and the public hostname → <Code>http://web:8080</Code>, start the <Code>tunnel</Code> Compose profile, and add an
              access policy limited to your identity.
            </li>
            <li>Verify from outside: valid TLS, the edge login appears first, and API paths answer 401 without a session.</li>
          </ol>
          <p className="text-xs text-muted">
            Exact commands: <Code>docs/DOMAIN_AND_AUTH.md</Code> (domain, tunnel, access policy, verification) and{" "}
            <Code>docs/DEPLOY_LINUX_LEIF.md</Code> (host setup, agents, updates, rollback).
          </p>
          <p className="text-[11px] leading-relaxed text-faint">
            The status badge above compares configured values only. It does not test DNS, the tunnel, TLS or the access policy.
          </p>
        </CardBody>
      </Card>

      <Card>
        <CardHeader icon={<DatabaseBackup className="h-4 w-4" />} title="Backup & restore" subtitle="All state lives in PostgreSQL. Redis only holds queues and the live cache and can be rebuilt." />
        <CardBody className="space-y-3">
          <div className="grid gap-3 lg:grid-cols-2">
            <CodeBlock label="Create a backup (pg_dump)">make backup</CodeBlock>
            <CodeBlock label="Restore a backup">make restore</CodeBlock>
          </div>
          <p className="text-xs leading-relaxed text-muted">
            Run these in the repository directory on the Docker host. Keep the <Code>.env</Code> file with the backup — sessions and agent-token
            hashes are keyed with <Code>OBSERVENET_SECRET_KEY</Code>, so restoring with a different key invalidates them. Schedule and
            off-host copies are described in <Code>docs/OPERATIONS.md</Code>.
          </p>
        </CardBody>
      </Card>
    </div>
  );
}

export function AuditTab() {
  const { isAdmin } = useAuth();
  const audit = useQuery({ queryKey: ["audit"], queryFn: () => api.get<AuditRow[]>("/audit", { limit: 200 }), enabled: isAdmin, refetchInterval: 60_000 });
  if (!isAdmin) return <AdminOnly what="The audit log" />;
  return (
    <Card>
      <CardHeader
        icon={<ScrollText className="h-4 w-4" />}
        title="Audit log"
        subtitle="Who did what: sign-ins, settings changes, token and user management, device edits and merges, exports. Latest 200 entries; secrets are never recorded."
      />
      <CardBody className="px-2">
        {audit.isLoading ? (
          <Skeleton className="mx-3 h-48" />
        ) : audit.isError ? (
          <ErrorState error={audit.error} retry={() => void audit.refetch()} />
        ) : audit.data?.length ? (
          <Table>
            <thead>
              <tr>
                <Th>When</Th>
                <Th>Actor</Th>
                <Th>Action</Th>
                <Th>Target</Th>
                <Th>From</Th>
                <Th>Details</Th>
              </tr>
            </thead>
            <tbody>
              {audit.data.map((a) => (
                <tr key={a.id} className="align-top hover:bg-raised/40">
                  <Td className="whitespace-nowrap text-xs text-muted">{formatDateTime(a.ts)}</Td>
                  <Td className="text-xs">{a.actor_label}</Td>
                  <Td className="font-mono text-xs">{a.action}</Td>
                  <Td className="text-xs text-muted">{a.target_type ? `${a.target_type} ${a.target_id ?? ""}` : "—"}</Td>
                  <Td className="font-mono text-xs text-muted">{a.ip ?? "—"}</Td>
                  <Td className="max-w-md break-words font-mono text-[11px] leading-relaxed text-faint">
                    {Object.keys(a.details).length ? JSON.stringify(a.details) : "—"}
                  </Td>
                </tr>
              ))}
            </tbody>
          </Table>
        ) : (
          <EmptyState icon={<ScrollText className="h-5 w-5" />} title="No audit entries yet" />
        )}
      </CardBody>
    </Card>
  );
}
