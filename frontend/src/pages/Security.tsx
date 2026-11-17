import { useQuery } from "@tanstack/react-query";
import { CircleSlash, Download, ShieldCheck } from "lucide-react";
import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/overlays";
import { Card, CardBody, CardHeader, EmptyState, ErrorState, NativeSelect, Notice, PageHeader, Skeleton, Table, Td, Th } from "@/components/ui/primitives";
import { Badge, ClassificationBadge, DemoTag, SeverityBadge, StateBadge, StatusBadge, Timestamp } from "@/components/ui/status";
import { CheckStatusBadge, StatTile, VerifyStatusBadge } from "@/features/security/bits";
import { DemoControls } from "@/features/security/DemoControls";
import { ACTIVE_STATUSES, STALE_RULE_ID, type AssessmentRow, type CheckRow } from "@/features/security/types";
import { useAuth } from "@/hooks/useAuth";
import { api, type DevicePage, type Finding } from "@/lib/api";
import { formatDateTime, titleCase } from "@/lib/format";

const SEVERITIES = ["high", "medium", "low", "info"] as const;
const EXPORT_LINK =
  "inline-flex h-9 items-center justify-center gap-1.5 rounded-lg border border-line bg-raised px-3.5 text-sm text-ink transition-colors hover:border-faint hover:bg-raised/70";
const STATUS_FILTERS = [
  { value: "active", label: "Active" },
  { value: "resolved", label: "Resolved (verified)" },
  { value: "closed_unverified", label: "Closed — not verified" },
  { value: "all", label: "All statuses" },
];

function FindingsTab({ findings, anyAssessment }: { findings: Finding[]; anyAssessment: boolean }) {
  const [status, setStatus] = useState("active");
  const [severity, setSeverity] = useState("");
  const [category, setCategory] = useState("");
  const rows = findings.filter(
    (f) =>
      (status === "all" || (status === "active" ? ACTIVE_STATUSES.includes(f.status) : f.status === status)) &&
      (!severity || f.severity === severity) &&
      (!category || f.category === category),
  );
  return (
    <Card>
      <div className="flex flex-wrap items-end gap-3 border-b border-line px-4 py-3">
        <div>
          <label htmlFor="f-status" className="mb-1 block text-[11px] text-faint">
            Status
          </label>
          <NativeSelect id="f-status" value={status} onChange={(e) => setStatus(e.target.value)} className="w-56">
            {STATUS_FILTERS.map((s) => (
              <option key={s.value} value={s.value}>
                {s.label}
              </option>
            ))}
          </NativeSelect>
        </div>
        <div>
          <label htmlFor="f-severity" className="mb-1 block text-[11px] text-faint">
            Severity
          </label>
          <NativeSelect id="f-severity" value={severity} onChange={(e) => setSeverity(e.target.value)} className="w-36">
            <option value="">Any</option>
            {SEVERITIES.map((s) => (
              <option key={s} value={s}>
                {titleCase(s)}
              </option>
            ))}
          </NativeSelect>
        </div>
        <div>
          <label htmlFor="f-category" className="mb-1 block text-[11px] text-faint">
            Category
          </label>
          <NativeSelect id="f-category" value={category} onChange={(e) => setCategory(e.target.value)} className="w-44">
            <option value="">Any</option>
            <option value="security">Security posture</option>
            <option value="observability">Observability</option>
          </NativeSelect>
        </div>
        <p className="num ml-auto text-xs text-faint" aria-live="polite">
          {rows.length} of {findings.length} finding(s)
        </p>
      </div>
      {rows.length ? (
        <Table>
          <caption className="sr-only">Posture findings</caption>
          <thead>
            <tr>
              <Th>Severity</Th>
              <Th>Finding</Th>
              <Th>Device</Th>
              <Th>Classification</Th>
              <Th>Confidence</Th>
              <Th>Status</Th>
              <Th>Verification</Th>
              <Th>Last observed</Th>
            </tr>
          </thead>
          <tbody>
            {rows.map((f) => (
              <tr key={f.id} className="hover:bg-raised/40">
                <Td>
                  <SeverityBadge severity={f.severity} />
                </Td>
                <Td className="max-w-md">
                  <Link to={`/security/findings/${f.id}`} className="font-medium text-ink hover:text-accent">
                    {f.title}
                  </Link>
                  <div className="mt-0.5 flex items-center gap-2 text-[11px] text-faint">
                    <span className="font-mono">
                      {f.rule_id} v{f.rule_version}
                    </span>
                    {f.category === "observability" && <Badge>Observability</Badge>}
                    {f.is_demo && <DemoTag />}
                  </div>
                </Td>
                <Td>
                  <Link to={`/devices/${f.asset_id}`} className="text-sm text-ink hover:text-accent">
                    {f.asset_name}
                  </Link>
                </Td>
                <Td>
                  <ClassificationBadge value={f.classification} />
                </Td>
                <Td className="text-xs text-muted">{titleCase(f.confidence)}</Td>
                <Td>
                  <StatusBadge status={f.status} />
                </Td>
                <Td>
                  <VerifyStatusBadge status={f.verification_status} />
                </Td>
                <Td className="text-xs text-muted">
                  <Timestamp ts={f.last_observed_at} />
                </Td>
              </tr>
            ))}
          </tbody>
        </Table>
      ) : findings.length ? (
        <EmptyState title="No finding matches these filters">Change the status, severity or category filter.</EmptyState>
      ) : anyAssessment ? (
        <EmptyState icon={<ShieldCheck className="h-5 w-5" />} title="No findings from the checks that ran">
          The checks that could run did not observe any of their conditions. That covers only hosts with an agent and only the rules listed under
          Checks — it is not a statement that the network is secure.
        </EmptyState>
      ) : (
        <EmptyState icon={<CircleSlash className="h-5 w-5" />} title="Nothing has been assessed yet">
          No agent has reported posture data, so no check has run. The security state is <strong className="text-ink">unknown, not secure</strong>.
          Findings appear after an agent sends its first posture snapshot.
        </EmptyState>
      )}
    </Card>
  );
}

/** Coverage of one rule, derived from its latest per-device results. "Unsupported" and
 * "Unknown" results are never presented as an evaluation that passed. */
function CoverageBadge({ check }: { check: CheckRow }) {
  const conclusive = check.results.filter((r) => r.status === "pass" || r.status === "fail").length;
  if (check.rule_id === STALE_RULE_ID) {
    return <Badge tone="info" title="Evaluated every few seconds from agent heartbeats, not from posture snapshots.">Continuous</Badge>;
  }
  if (check.coverage === "unsupported") return <StateBadge state="unsupported" />;
  if (check.coverage === "not_evaluated" || !check.results.length) {
    return <Badge title="No agent of the matching platform has reported the data this rule needs in the last 7 days.">Not evaluated</Badge>;
  }
  if (conclusive === 0) {
    const allUnsupported = check.results.every((r) => r.status === "unsupported");
    return allUnsupported ? <StateBadge state="unsupported" /> : <StateBadge state="unknown" />;
  }
  return (
    <Badge tone="info">
      Evaluated on {conclusive} of {check.results.length} device(s)
    </Badge>
  );
}

function ChecksTab() {
  const checks = useQuery({ queryKey: ["checks"], queryFn: () => api.get<CheckRow[]>("/checks"), refetchInterval: 60_000 });
  if (checks.isLoading) return <Skeleton className="h-64 w-full" />;
  if (checks.isError) return <ErrorState error={checks.error} retry={() => void checks.refetch()} />;
  return (
    <div className="space-y-3">
      <p className="text-xs leading-relaxed text-muted">
        Every rule is deterministic and reads only what a device's own agent reports. A rule that cannot get its input says so — it is never
        counted as a pass.
      </p>
      {checks.data?.map((c) => (
        <Card key={c.rule_id}>
          <CardHeader
            title={
              <span className="flex flex-wrap items-center gap-2">
                <span className="font-mono text-xs text-muted">
                  {c.rule_id} v{c.version}
                </span>
                <span>{c.title}</span>
              </span>
            }
            subtitle={c.description}
            actions={<CoverageBadge check={c} />}
          />
          <CardBody>
            <div className="mb-3 flex flex-wrap items-center gap-2 text-xs text-muted">
              <Badge>Platform: {titleCase(c.platform)}</Badge>
              <Badge>{c.category === "observability" ? "Observability" : "Security posture"}</Badge>
              <ClassificationBadge value={c.classification} />
              <span className="flex items-center gap-1.5">
                Default severity <SeverityBadge severity={c.default_severity} />
              </span>
            </div>
            {c.results.length ? (
              <ul className="divide-y divide-line/50 rounded-lg border border-line">
                {c.results.map((r) => (
                  <li key={r.asset_id} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2 text-xs">
                    <CheckStatusBadge status={r.status} />
                    <Link to={`/devices/${r.asset_id}`} className="font-medium text-ink hover:text-accent">
                      {r.asset_name}
                    </Link>
                    <span className="min-w-0 flex-1 text-muted">{r.summary}</span>
                    <span className="text-faint">
                      <Timestamp ts={r.at} />
                    </span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-xs text-faint">
                {c.coverage === "unsupported"
                  ? "No source can provide the data this rule needs, so it has never run. Unsupported is not a pass."
                  : c.rule_id === "OBS-STALE-001"
                    ? "Evaluated continuously from agent heartbeats rather than from posture snapshots; it opens a finding when an agent goes silent."
                    : "No result in the last 7 days. The state this rule would check is unknown."}
              </p>
            )}
          </CardBody>
        </Card>
      ))}
    </div>
  );
}

function AssessmentsTab({ names }: { names: Record<number, string> }) {
  const runs = useQuery({ queryKey: ["assessments"], queryFn: () => api.get<AssessmentRow[]>("/assessments", { limit: 50 }), refetchInterval: 60_000 });
  if (runs.isLoading) return <Skeleton className="h-64 w-full" />;
  if (runs.isError) return <ErrorState error={runs.error} retry={() => void runs.refetch()} />;
  if (!runs.data?.length) {
    return (
      <Card>
        <EmptyState icon={<CircleSlash className="h-5 w-5" />} title="No assessments have run">
          An assessment runs each time an agent sends a changed posture snapshot or a verification is requested. Until then the posture is unknown,
          not secure.
        </EmptyState>
      </Card>
    );
  }
  return (
    <Card>
      <CardHeader title="Assessment history" subtitle="Most recent 50 runs. Identical consecutive snapshots are re-confirmed without a new run." />
      <CardBody className="px-2">
        <Table>
          <thead>
            <tr>
              <Th>Started</Th>
              <Th>Device</Th>
              <Th>Trigger</Th>
              <Th>Engine</Th>
              <Th>Results per rule</Th>
            </tr>
          </thead>
          <tbody>
            {runs.data.map((a) => (
              <tr key={a.id} className="align-top">
                <Td className="num whitespace-nowrap text-xs text-muted">{formatDateTime(a.started_at)}</Td>
                <Td>
                  <Link to={`/devices/${a.asset_id}`} className="text-sm text-ink hover:text-accent">
                    {names[a.asset_id] ?? `Device #${a.asset_id}`}
                  </Link>
                </Td>
                <Td>
                  <Badge tone={a.trigger === "verify" ? "info" : "neutral"}>{a.trigger === "verify" ? "Verification" : "Scheduled"}</Badge>
                </Td>
                <Td className="font-mono text-xs text-muted">{a.engine_version}</Td>
                <Td>
                  <ul className="space-y-1">
                    {a.results.map((r) => (
                      <li key={r.rule_id} className="flex flex-wrap items-center gap-2 text-xs">
                        <CheckStatusBadge status={r.status} />
                        <span className="font-mono text-muted">{r.rule_id}</span>
                        <span className="min-w-0 text-faint">{r.summary}</span>
                      </li>
                    ))}
                  </ul>
                </Td>
              </tr>
            ))}
          </tbody>
        </Table>
      </CardBody>
    </Card>
  );
}

export function SecurityPage() {
  const { isAdmin, demoMode } = useAuth();
  const [search, setSearch] = useSearchParams();
  const tab = ["findings", "checks", "assessments"].includes(search.get("tab") ?? "") ? (search.get("tab") as string) : "findings";
  const findings = useQuery({ queryKey: ["findings", "all"], queryFn: () => api.get<Finding[]>("/findings", { status: "all" }), refetchInterval: 15_000 });
  const latest = useQuery({ queryKey: ["assessments", "latest"], queryFn: () => api.get<AssessmentRow[]>("/assessments", { limit: 1 }), refetchInterval: 60_000 });
  const devices = useQuery({ queryKey: ["devices", "names"], queryFn: () => api.get<DevicePage>("/devices", { limit: 500, include_merged: true }), staleTime: 60_000 });
  const names = useMemo(() => Object.fromEntries((devices.data?.items ?? []).map((d) => [d.id, d.name])), [devices.data]);

  const all = useMemo(() => findings.data ?? [], [findings.data]);
  const active = all.filter((f) => ACTIVE_STATUSES.includes(f.status));
  const count = (pred: (f: Finding) => boolean) => all.filter(pred).length;
  const lastRun = latest.data?.[0];

  return (
    <div className="space-y-4">
      <PageHeader
        title="Security"
        description="Read-only posture checks with the evidence behind each finding, manual fix instructions and a verify loop."
        actions={
          isAdmin && (
            <>
              <a className={EXPORT_LINK} href="/api/v1/export/findings?format=csv" download>
                <Download className="h-3.5 w-3.5" aria-hidden /> CSV
              </a>
              <a className={EXPORT_LINK} href="/api/v1/export/findings?format=json" download>
                <Download className="h-3.5 w-3.5" aria-hidden /> JSON
              </a>
            </>
          )
        }
      />

      <Notice tone="info" title="What these findings are — and are not" icon={<ShieldCheck className="h-4 w-4" />}>
        ObserveNet reads configuration and state that each device's own agent reports. It does not scan the network, probe ports or attempt to
        exploit anything. A finding is therefore an <strong className="text-ink">observed configuration</strong> or a{" "}
        <strong className="text-ink">potential exposure</strong> — never proof of exploitability — and a finding is only called resolved after the
        same check re-runs on fresh evidence.
      </Notice>

      {findings.isError ? (
        <ErrorState error={findings.error} retry={() => void findings.refetch()} />
      ) : findings.isLoading ? (
        <Skeleton className="h-24 w-full" />
      ) : (
        <section aria-label="Findings summary" className="grid gap-4 lg:grid-cols-2">
          <Card>
            <CardHeader title="Active findings by severity" subtitle={`${active.length} open, acknowledged or snoozed.`} />
            <CardBody>
              <dl className="grid grid-cols-4 gap-2 text-center">
                {SEVERITIES.map((s) => (
                  <StatTile key={s} label={s} value={active.filter((f) => f.severity === s).length} />
                ))}
              </dl>
            </CardBody>
          </Card>
          <Card>
            <CardHeader
              title="All findings by status"
              subtitle={
                lastRun ? (
                  <>
                    Last assessment <Timestamp ts={lastRun.started_at} />.
                  </>
                ) : (
                  "No assessment has run yet — state unknown, not secure."
                )
              }
            />
            <CardBody>
              <dl className="grid grid-cols-5 gap-2 text-center">
                <StatTile label="Open" value={count((f) => f.status === "open")} />
                <StatTile label="Ack'd" value={count((f) => f.status === "acknowledged")} hint="Acknowledged" />
                <StatTile label="Snoozed" value={count((f) => f.status === "snoozed")} />
                <StatTile label="Verified" value={count((f) => f.status === "resolved")} hint="Resolved: the same check re-ran on fresh evidence and no longer observes the condition" />
                <StatTile label="Unverified" value={count((f) => f.status === "closed_unverified")} hint="Closed by a person without a successful re-check — not counted as fixed" />
              </dl>
            </CardBody>
          </Card>
        </section>
      )}

      {demoMode && <DemoControls isAdmin={isAdmin} />}

      <Tabs value={tab} onValueChange={(v) => setSearch(v === "findings" ? {} : { tab: v }, { replace: true })}>
        <TabsList label="Security sections">
          <TabsTrigger value="findings">Findings</TabsTrigger>
          <TabsTrigger value="checks">Checks</TabsTrigger>
          <TabsTrigger value="assessments">Assessments</TabsTrigger>
        </TabsList>
        <TabsContent value="findings">
          {findings.isLoading ? <Skeleton className="h-48 w-full" /> : <FindingsTab findings={all} anyAssessment={!!lastRun} />}
        </TabsContent>
        <TabsContent value="checks">
          <ChecksTab />
        </TabsContent>
        <TabsContent value="assessments">
          <AssessmentsTab names={names} />
        </TabsContent>
      </Tabs>
    </div>
  );
}
