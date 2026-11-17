import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { KeyRound, Plus, ShieldAlert, Terminal, Trash2 } from "lucide-react";
import { useState } from "react";

import { Dialog } from "@/components/ui/overlays";
import { Button, Card, CardBody, CardHeader, Code, EmptyState, ErrorState, Field, Input, Notice, Skeleton, Table, Td, Th } from "@/components/ui/primitives";
import { Badge, Timestamp } from "@/components/ui/status";
import { FormError } from "@/features/alerts/shared";
import { AdminOnly, CodeBlock, CopyButton } from "@/features/settings/shared";
import { useAuth } from "@/hooks/useAuth";
import { api, type Schemas, type Source, type Token } from "@/lib/api";
import { formatDateTime } from "@/lib/format";

type TokenCreated = Schemas["TokenCreated"];

function tokenState(t: Token, now: number): { label: string; tone: "good" | "neutral" | "crit" | "warn" } {
  if (t.revoked_at) return { label: "Revoked", tone: "crit" };
  if (t.expires_at && Date.parse(t.expires_at) <= now) return { label: "Expired", tone: "warn" };
  return { label: "Active", tone: "good" };
}

function CreateTokenDialog({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [ttl, setTtl] = useState("90");
  const [created, setCreated] = useState<TokenCreated | null>(null);
  const ttlNum = Number(ttl);
  const ttlError = !Number.isInteger(ttlNum) || ttlNum < 1 || ttlNum > 365 ? "Enter a whole number of days between 1 and 365." : null;
  const create = useMutation({
    mutationFn: () => api.post<TokenCreated>("/tokens", { name: name.trim(), ttl_days: ttlNum }),
    onSuccess: (res) => {
      setCreated(res);
      void qc.invalidateQueries({ queryKey: ["tokens"] });
    },
  });

  if (created) {
    return (
      <Dialog
        open
        wide
        onOpenChange={(o) => !o && onClose()}
        title="Copy the agent token now"
        description="This is the only time the token is shown. ObserveNet stores a keyed hash, so it cannot be displayed again."
        footer={
          <Button variant="primary" onClick={onClose}>
            I have stored it
          </Button>
        }
      >
        <Notice tone="warn" icon={<ShieldAlert className="h-4 w-4" />} title="Treat this like a password">
          Anyone holding it can submit telemetry as this agent. Put it in a root-readable file on the agent host — not in shell history, chat
          or a repository. Revoke it here if it leaks.
        </Notice>
        <div className="mt-3 flex items-center gap-2 rounded-lg border border-accent/40 bg-bg/70 p-3">
          <code data-testid="new-token" className="min-w-0 flex-1 select-all break-all font-mono text-xs text-ink">
            {created.token}
          </code>
          <CopyButton text={created.token} label="Copy token" />
        </div>
        <p className="mt-3 text-xs leading-relaxed text-muted">
          “{created.info.name}” expires {formatDateTime(created.info.expires_at)}. It binds to the first agent that uses it and is then refused
          for any other agent.
        </p>
      </Dialog>
    );
  }
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title="New agent token"
      description="A scoped, revocable bearer token that lets one collector submit telemetry (scope: ingest). It cannot read data or sign in."
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" loading={create.isPending} disabled={!name.trim() || !!ttlError} onClick={() => create.mutate()}>
            Create token
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <Field label="Name" htmlFor="token-name" hint="Which host it is for, e.g. “linux-leif collector” or “Windows desktop”.">
          <Input id="token-name" value={name} maxLength={120} autoFocus onChange={(e) => setName(e.target.value)} />
        </Field>
        <Field label="Valid for (days)" htmlFor="token-ttl" error={ttlError} hint="Short-lived by design: rotate before it expires. Maximum 365.">
          <Input id="token-ttl" type="number" min={1} max={365} value={ttl} onChange={(e) => setTtl(e.target.value)} className="num" />
        </Field>
        <FormError error={create.error} />
      </div>
    </Dialog>
  );
}

function RevokeDialog({ token, onClose }: { token: Token; onClose: () => void }) {
  const qc = useQueryClient();
  const revoke = useMutation({
    mutationFn: () => api.delete<void>(`/tokens/${token.id}`),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["tokens"] });
      onClose();
    },
  });
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title={`Revoke “${token.name}”?`}
      description={
        <>
          Revocation takes effect immediately and cannot be undone: the agent using this token starts receiving 401 and its data stops.
          {token.is_bootstrap && " This is the bundled collector's token from the environment file; once revoked it stays revoked even after a restart."}
        </>
      }
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="danger" loading={revoke.isPending} onClick={() => revoke.mutate()}>
            Revoke token
          </Button>
        </>
      }
    >
      <FormError error={revoke.error} />
    </Dialog>
  );
}

function SetupSnippets() {
  const origin = window.location.origin;
  const local = /^https?:\/\/(localhost|127\.0\.0\.1)(:|$)/.test(origin);
  const linux = `# On the Linux host, as root. Paste the token when prompted (it is not echoed).
install -d -m 0750 /etc/observenet /var/lib/observenet-agent
read -rs TOKEN && printf '%s' "$TOKEN" > /etc/observenet/agent-token && unset TOKEN
chmod 0640 /etc/observenet/agent-token

export OBSERVENET_URL=${origin}
export OBSERVENET_AGENT_TOKEN_FILE=/etc/observenet/agent-token
export OBSERVENET_AGENT_STATE_DIR=/var/lib/observenet-agent
python -m observenet_agent        # from agents/linux (uv sync), or the installed observenet-agent command`;
  const windows = `# Windows 11, elevated PowerShell (only this storage step needs elevation).
New-Item -ItemType Directory -Force -Path "$env:ProgramData\\ObserveNet" | Out-Null
Read-Host -Prompt 'Paste the collector token' | Set-Content -Path "$env:ProgramData\\ObserveNet\\agent.token" -Encoding ASCII -NoNewline
icacls "$env:ProgramData\\ObserveNet\\agent.token" /inheritance:r /grant:r "$($env:USERNAME):(R)" "SYSTEM:(R)" "Administrators:(F)"

# Then, as a normal user: print what would be sent without sending it.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\\observenet-agent.ps1 -Url ${origin}${local ? " -AllowLocalHttp" : ""} -DryRun
# Remove -DryRun once the printed payload looks right.`;
  return (
    <div className="grid gap-4 xl:grid-cols-2">
      <Card className="min-w-0">
        <CardHeader
          icon={<Terminal className="h-4 w-4" />}
          title="Linux agent"
          subtitle="Read-only: interface counters, neighbour cache, listening sockets, and the probe targets configured here. Runs unprivileged."
        />
        <CardBody className="space-y-3">
          <CodeBlock label="Run a Linux agent">{linux}</CodeBlock>
          <ul className="list-disc space-y-1 pl-5 text-xs leading-relaxed text-muted">
            <li>
              The Compose stack already runs one collector for its own host using <Code>OBSERVENET_BOOTSTRAP_AGENT_TOKEN</Code> from <Code>.env</Code>;
              create a token here only for additional hosts or a native install.
            </li>
            <li>
              The agent refuses to send its token over plain HTTP to a non-local address. Use the HTTPS hostname, or set{" "}
              <Code>OBSERVENET_ALLOW_PLAINTEXT=1</Code> only on a trusted LAN demo.
            </li>
            <li>
              <Code>OBSERVENET_AGENT_TOKEN</Code> (inline) is also accepted, but a file keeps the token out of process listings. Optional:{" "}
              <Code>OBSERVENET_DOCKER_API_URL</Code> for the opt-in read-only Docker inventory.
            </li>
            <li>
              systemd unit and details: <Code>agents/linux/README.md</Code>, <Code>docs/DEPLOY_LINUX_LEIF.md</Code>.
            </li>
          </ul>
        </CardBody>
      </Card>
      <Card className="min-w-0">
        <CardHeader
          icon={<Terminal className="h-4 w-4" />}
          title="Windows agent (optional)"
          subtitle="PowerShell script: NIC counters, firewall profile state, SMB configuration, listening ports. No administrator rights required; unreadable checks are skipped and reported as unavailable."
        />
        <CardBody className="space-y-3">
          <Notice tone="warn" title="Not executed in the build environment">
            The Windows agent was written against the ingest schema but could not be run while building ObserveNet (no Windows host was
            available). Start with <Code>-DryRun</Code> and review what it would send.
          </Notice>
          <CodeBlock label="Run the Windows agent">{windows}</CodeBlock>
          <p className="text-xs leading-relaxed text-muted">
            Script and Task Scheduler registration: <Code>agents/windows/README.md</Code>. HTTPS is required unless the URL is localhost and{" "}
            <Code>-AllowLocalHttp</Code> is passed.
          </p>
        </CardBody>
      </Card>
    </div>
  );
}

export function AgentsTab() {
  const { isAdmin } = useAuth();
  const [creating, setCreating] = useState(false);
  const [revoking, setRevoking] = useState<Token | null>(null);
  const tokens = useQuery({ queryKey: ["tokens"], queryFn: () => api.get<Token[]>("/tokens"), enabled: isAdmin });
  const sources = useQuery({ queryKey: ["sources"], queryFn: () => api.get<Source[]>("/sources"), enabled: isAdmin });
  if (!isAdmin) return <AdminOnly what="Managing agent tokens" />;
  const sourceName = (id: number | null) => (id == null ? null : (sources.data?.find((s) => s.id === id)?.name ?? `source #${id}`));
  const now = Date.now();

  return (
    <div className="space-y-5">
      <Card>
        <CardHeader
          icon={<KeyRound className="h-4 w-4" />}
          title="Agent tokens"
          subtitle="Each collector authenticates with its own token. Only a keyed hash is stored; the prefix below is for identification."
          actions={
            <Button size="sm" variant="primary" onClick={() => setCreating(true)}>
              <Plus className="h-3.5 w-3.5" aria-hidden /> New token
            </Button>
          }
        />
        <CardBody className="px-2">
          {tokens.isLoading ? (
            <Skeleton className="mx-3 h-32" />
          ) : tokens.isError ? (
            <ErrorState error={tokens.error} retry={() => void tokens.refetch()} />
          ) : tokens.data?.length ? (
            <Table>
              <thead>
                <tr>
                  <Th>Name</Th>
                  <Th>Prefix</Th>
                  <Th>State</Th>
                  <Th>Bound to</Th>
                  <Th>Created</Th>
                  <Th>Expires</Th>
                  <Th>Last used</Th>
                  <Th className="text-right">Actions</Th>
                </tr>
              </thead>
              <tbody>
                {tokens.data.map((t) => {
                  const st = tokenState(t, now);
                  return (
                    <tr key={t.id} className={t.revoked_at ? "opacity-60" : undefined}>
                      <Td>
                        <span className="font-medium">{t.name}</span>
                        {t.is_bootstrap && <Badge className="ml-2">from .env</Badge>}
                      </Td>
                      <Td className="font-mono text-xs text-muted">{t.token_prefix}…</Td>
                      <Td>
                        <Badge tone={st.tone}>{st.label}</Badge>
                      </Td>
                      <Td className="text-xs text-muted">{sourceName(t.source_id) ?? <span className="text-faint">not used yet</span>}</Td>
                      <Td className="text-xs text-muted">
                        <Timestamp ts={t.created_at} />
                      </Td>
                      <Td className="text-xs text-muted">{t.expires_at ? formatDateTime(t.expires_at) : <span className="text-faint">no expiry</span>}</Td>
                      <Td className="text-xs text-muted">{t.last_used_at ? <Timestamp ts={t.last_used_at} /> : <span className="text-faint">never</span>}</Td>
                      <Td className="text-right">
                        {!t.revoked_at && (
                          <Button size="sm" variant="ghost" onClick={() => setRevoking(t)} aria-label={`Revoke token ${t.name}`}>
                            <Trash2 className="h-3.5 w-3.5" aria-hidden /> Revoke
                          </Button>
                        )}
                      </Td>
                    </tr>
                  );
                })}
              </tbody>
            </Table>
          ) : (
            <EmptyState icon={<KeyRound className="h-5 w-5" />} title="No agent tokens">
              Create a token for each additional collector you want to connect.
            </EmptyState>
          )}
        </CardBody>
      </Card>
      <SetupSnippets />
      {creating && <CreateTokenDialog onClose={() => setCreating(false)} />}
      {revoking && <RevokeDialog token={revoking} onClose={() => setRevoking(null)} />}
    </div>
  );
}
