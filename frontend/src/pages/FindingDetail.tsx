import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, BellOff, CheckCheck, CircleSlash, Eye, FlaskConical, RefreshCw, RotateCcw, ShieldCheck } from "lucide-react";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";

import { Dialog } from "@/components/ui/overlays";
import { Button, Card, CardBody, CardHeader, EmptyState, ErrorState, Field, Input, Notice, Spinner } from "@/components/ui/primitives";
import { Badge, ClassificationBadge, DemoTag, SeverityBadge, StatusBadge, Timestamp } from "@/components/ui/status";
import { VerifyStatusBadge } from "@/features/security/bits";
import { EvidenceTimeline } from "@/features/security/EvidenceTimeline";
import { RemediationSteps } from "@/features/security/RemediationSteps";
import { ACTIVE_STATUSES, STALE_RULE_ID, type HistoryRow } from "@/features/security/types";
import { VerificationRuns } from "@/features/security/VerificationRuns";
import { useAuth } from "@/hooks/useAuth";
import { api, ApiError, type Finding, type FindingDetail, type Source, type Verification } from "@/lib/api";
import { formatDateTime, titleCase } from "@/lib/format";

type Action = "acknowledge" | "snooze" | "close" | "reopen";

function defaultSnooze(): string {
  const d = new Date(Date.now() + 24 * 3600 * 1000);
  d.setMinutes(d.getMinutes() - d.getTimezoneOffset());
  return d.toISOString().slice(0, 16);
}

export function FindingDetailPage() {
  const { id } = useParams();
  const findingId = Number(id);
  const { isAdmin, demoMode } = useAuth();
  const qc = useQueryClient();
  const [closeOpen, setCloseOpen] = useState(false);
  const [snoozeOpen, setSnoozeOpen] = useState(false);
  const [snoozeUntil, setSnoozeUntil] = useState(defaultSnooze);
  const [note, setNote] = useState("");

  const detail = useQuery({
    queryKey: ["finding", findingId],
    queryFn: () => api.get<FindingDetail>(`/findings/${findingId}`),
    enabled: Number.isFinite(findingId),
    // Poll quickly only while a verification is waiting for fresh evidence.
    refetchInterval: (query) => (query.state.data?.verifications.some((v) => v.status === "pending") ? 2000 : 30_000),
  });
  const sources = useQuery({ queryKey: ["sources"], queryFn: () => api.get<Source[]>("/sources"), staleTime: 60_000 });
  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: ["finding", findingId] });
    void qc.invalidateQueries({ queryKey: ["findings"] });
    void qc.invalidateQueries({ queryKey: ["overview"] });
  };
  const verify = useMutation({ mutationFn: () => api.post<Verification>(`/findings/${findingId}/verify`), onSuccess: invalidate });
  const act = useMutation({
    mutationFn: (body: { action: Action; until?: string; note?: string }) => api.post<Finding>(`/findings/${findingId}/status`, body),
    onSuccess: () => {
      invalidate();
      setCloseOpen(false);
      setSnoozeOpen(false);
      setNote("");
    },
  });

  const back = (
    <Link to="/security" className="mb-3 inline-flex items-center gap-1.5 text-xs text-muted hover:text-ink">
      <ArrowLeft className="h-3.5 w-3.5" aria-hidden /> All findings
    </Link>
  );
  if (!Number.isFinite(findingId) || (detail.error instanceof ApiError && detail.error.status === 404)) {
    return (
      <div>
        {back}
        <Card>
          <EmptyState title="Finding not found">No finding has this ID.</EmptyState>
        </Card>
      </div>
    );
  }
  if (detail.isLoading) return <Spinner label="Loading finding" />;
  if (detail.isError || !detail.data) return <ErrorState error={detail.error} retry={() => void detail.refetch()} />;

  const { finding: f, evidence, remediation, verifications, disclaimer } = detail.data;
  const history = detail.data.history as unknown as HistoryRow[];
  const sourceNames = Object.fromEntries((sources.data ?? []).map((s) => [s.id, s.name]));
  const pending = verifications.some((v) => v.status === "pending");
  const isStaleRule = f.rule_id === STALE_RULE_ID;
  const active = ACTIVE_STATUSES.includes(f.status);
  const canVerify = isAdmin && !isStaleRule && f.source_id !== null;
  const actionError = act.error instanceof ApiError ? act.error.message : act.error ? "The action failed." : null;
  const verifyError = verify.error instanceof ApiError ? verify.error.message : verify.error ? "Verification could not be requested." : null;

  return (
    <div className="space-y-4">
      <div>
        {back}
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <h1 className="text-xl font-semibold tracking-tight text-ink">{f.title}</h1>
            <p className="mt-1 text-sm text-muted">
              on{" "}
              <Link to={`/devices/${f.asset_id}`} className="text-accent hover:underline">
                {f.asset_name}
              </Link>{" "}
              · rule <span className="font-mono text-xs">{f.rule_id} v{f.rule_version}</span> · subject <span className="font-mono text-xs">{f.subject}</span>
            </p>
            <div className="mt-2.5 flex flex-wrap items-center gap-2">
              <SeverityBadge severity={f.severity} />
              <ClassificationBadge value={f.classification} />
              <StatusBadge status={f.status} />
              <Badge>{titleCase(f.confidence)} confidence</Badge>
              {f.category === "observability" && <Badge>Observability</Badge>}
              {f.is_demo && <DemoTag />}
            </div>
          </div>
        </div>
      </div>

      {f.status === "closed_unverified" && (
        <Notice tone="warn" title="Closed — not verified" icon={<CircleSlash className="h-4 w-4" />}>
          A person closed this finding without a successful re-check. ObserveNet does not know whether the condition was fixed and does not report
          it as resolved. It will reopen by itself if the check observes the condition again; use Verify fix to get an evidence-based result.
        </Notice>
      )}
      {f.status === "resolved" && (
        <Notice tone="good" title="Resolved — verified by re-check" icon={<ShieldCheck className="h-4 w-4" />}>
          The same check ran on evidence collected {f.resolved_at ? <>at {formatDateTime(f.resolved_at)}</> : "after the fix"} and no longer observes
          the condition. See the verification record below for the before/after comparison.
        </Notice>
      )}
      {f.status === "snoozed" && f.snoozed_until && (
        <Notice tone="info" title="Snoozed" icon={<BellOff className="h-4 w-4" />}>
          Snoozed until {formatDateTime(f.snoozed_until)}. The condition is still tracked.
        </Notice>
      )}

      <div className="grid gap-4 xl:grid-cols-3">
        <div className="space-y-4 xl:col-span-2">
          <Card>
            <CardHeader title="What was found" subtitle="Facts and inferences are kept apart on purpose." />
            <CardBody className="space-y-4">
              <section aria-labelledby="observed-h" className="rounded-lg border border-accent/25 bg-accent/[0.05] p-3.5">
                <h3 id="observed-h" className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-accent">
                  <Eye className="h-3.5 w-3.5" aria-hidden /> Observed (fact)
                </h3>
                <p className="mt-1.5 text-sm leading-relaxed text-ink">{f.observation}</p>
                <p className="mt-1.5 text-[11px] text-faint">
                  First observed <Timestamp ts={f.first_observed_at} /> · last observed <Timestamp ts={f.last_observed_at} />
                </p>
              </section>
              {f.inference && (
                <section aria-labelledby="inferred-h" className="rounded-lg border border-violet/30 bg-violet/[0.06] p-3.5">
                  <h3 id="inferred-h" className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-violet">
                    <FlaskConical className="h-3.5 w-3.5" aria-hidden /> Inferred (not tested)
                  </h3>
                  <p className="mt-1.5 text-sm leading-relaxed text-ink">{f.inference}</p>
                </section>
              )}
              <section aria-labelledby="impact-h">
                <h3 id="impact-h" className="text-xs font-semibold uppercase tracking-wider text-faint">
                  Why it matters
                </h3>
                <p className="mt-1.5 text-sm leading-relaxed text-ink">{f.impact}</p>
              </section>
              <section aria-labelledby="severity-h">
                <h3 id="severity-h" className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-faint">
                  Severity rationale <SeverityBadge severity={f.severity} />
                </h3>
                <p className="mt-1.5 text-sm leading-relaxed text-muted">{f.severity_rationale}</p>
              </section>
              <p className="border-t border-line pt-3 text-[11px] leading-relaxed text-faint">{disclaimer}</p>
            </CardBody>
          </Card>

          <Card>
            <CardHeader
              icon={<RefreshCw className="h-4 w-4" />}
              title="Verification"
              subtitle={
                isStaleRule
                  ? "This finding tracks a silent agent."
                  : "After changing the configuration yourself, re-run the same read-only check on fresh evidence."
              }
              actions={
                <span className="flex items-center gap-2">
                  <VerifyStatusBadge status={pending ? "pending" : f.verification_status} />
                  {canVerify && (
                    <Button variant="primary" onClick={() => verify.mutate()} loading={verify.isPending || pending} disabled={pending}>
                      {pending ? "Verifying" : "Verify fix"}
                    </Button>
                  )}
                </span>
              }
            />
            <CardBody className="space-y-3">
              {isStaleRule ? (
                <Notice tone="info">
                  There is nothing to verify manually: this finding resolves automatically, with evidence, as soon as the agent reports again.
                </Notice>
              ) : f.source_id === null ? (
                <Notice tone="warn">The source that produced this finding no longer exists, so it cannot be re-checked.</Notice>
              ) : !isAdmin ? (
                <p className="text-xs text-faint">Only administrators can request a verification.</p>
              ) : null}
              {verifyError && (
                <p role="alert" className="text-xs text-crit">
                  {verifyError}
                </p>
              )}
              <VerificationRuns runs={verifications} />
              <p className="text-[11px] leading-relaxed text-faint">
                Outcomes: <strong className="text-muted">Resolved</strong> — fresh evidence no longer shows the condition;{" "}
                <strong className="text-muted">Still observed</strong> — it does; <strong className="text-muted">Unknown</strong> — no conclusive
                evidence arrived in time, and the finding is left unchanged. Only evidence collected after the request counts.
              </p>
            </CardBody>
          </Card>

          <Card>
            <CardHeader title="Evidence" subtitle="Every snapshot that changed what is known about this finding, newest first." />
            <CardBody>
              <EvidenceTimeline evidence={evidence} sources={sourceNames} />
            </CardBody>
          </Card>
        </div>

        <div className="space-y-4">
          {isAdmin && (
            <Card>
              <CardHeader title="Triage" subtitle="These change how the finding is tracked — not whether it is fixed." />
              <CardBody className="space-y-2">
                <div className="flex flex-wrap gap-2">
                  {(f.status === "open" || f.status === "snoozed") && (
                    <Button size="sm" loading={act.isPending && act.variables?.action === "acknowledge"} onClick={() => act.mutate({ action: "acknowledge" })}>
                      <CheckCheck className="h-3.5 w-3.5" aria-hidden /> Acknowledge
                    </Button>
                  )}
                  {active && (
                    <Button size="sm" onClick={() => setSnoozeOpen(true)}>
                      <BellOff className="h-3.5 w-3.5" aria-hidden /> Snooze…
                    </Button>
                  )}
                  {active && (
                    <Button size="sm" variant="danger" onClick={() => setCloseOpen(true)}>
                      <CircleSlash className="h-3.5 w-3.5" aria-hidden /> Close without verification…
                    </Button>
                  )}
                  {(f.status === "acknowledged" || f.status === "snoozed" || f.status === "closed_unverified") && (
                    <Button size="sm" loading={act.isPending && act.variables?.action === "reopen"} onClick={() => act.mutate({ action: "reopen" })}>
                      <RotateCcw className="h-3.5 w-3.5" aria-hidden /> Reopen
                    </Button>
                  )}
                </div>
                {f.status === "resolved" && (
                  <p className="text-xs text-muted">A verified-resolved finding reopens by itself if the check observes the condition again.</p>
                )}
                {actionError && !closeOpen && !snoozeOpen && (
                  <p role="alert" className="text-xs text-crit">
                    {actionError}
                  </p>
                )}
              </CardBody>
            </Card>
          )}

          <Card>
            <CardHeader title="How to fix it" subtitle="Manual steps. ObserveNet never changes a device." />
            <CardBody>
              <RemediationSteps steps={remediation} />
              {canVerify && remediation.length > 0 && <p className="mt-4 text-xs text-muted">When done, choose Verify fix to re-check with fresh evidence.</p>}
            </CardBody>
          </Card>

          <Card>
            <CardHeader title="History" subtitle="Status changes and verification events." />
            <CardBody>
              {history.length ? (
                <ol className="space-y-3 border-l border-line pl-4">
                  {history.map((h) => (
                    <li key={`${h.ts}-${h.kind}`} className="relative text-xs">
                      <span className="absolute -left-[21px] top-1 h-2 w-2 rounded-full bg-line ring-2 ring-surface" aria-hidden />
                      <p className="text-ink">{h.message}</p>
                      {typeof h.data.note === "string" && h.data.note && <p className="mt-0.5 italic text-muted">“{h.data.note}”</p>}
                      <p className="text-faint">
                        <span className="num">{formatDateTime(h.ts)}</span> · <span className="font-mono">{h.kind}</span>
                      </p>
                    </li>
                  ))}
                </ol>
              ) : (
                <EmptyState title="No history yet" className="py-6" />
              )}
            </CardBody>
          </Card>
        </div>
      </div>

      <Dialog
        open={closeOpen}
        onOpenChange={setCloseOpen}
        title="Close without verification?"
        description="Use this for accepted risks or false positives. The finding will be marked “Closed — not verified”. It will NOT be marked resolved, because no re-check confirmed a fix."
        footer={
          <>
            <Button onClick={() => setCloseOpen(false)}>Cancel</Button>
            <Button variant="danger" loading={act.isPending} onClick={() => act.mutate({ action: "close", note: note.trim() || undefined })}>
              Close as not verified
            </Button>
          </>
        }
      >
        <Field label="Reason (recorded in the history and audit log)" htmlFor="close-note" error={closeOpen ? actionError : null}>
          <Input id="close-note" value={note} maxLength={1000} onChange={(e) => setNote(e.target.value)} placeholder="e.g. intended: media server for the LAN" />
        </Field>
        <p className="mt-3 text-xs leading-relaxed text-muted">
          Prefer Verify fix if you changed the configuration: it produces an evidence-based “Resolved” instead.
        </p>
      </Dialog>

      <Dialog
        open={snoozeOpen}
        onOpenChange={setSnoozeOpen}
        title="Snooze finding"
        description="Hides it from the active list until the chosen time. The check keeps running and evidence keeps being recorded."
        footer={
          <>
            <Button onClick={() => setSnoozeOpen(false)}>Cancel</Button>
            <Button
              variant="primary"
              loading={act.isPending}
              disabled={!snoozeUntil || Number.isNaN(new Date(snoozeUntil).getTime())}
              onClick={() => act.mutate({ action: "snooze", until: new Date(snoozeUntil).toISOString(), note: note.trim() || undefined })}
            >
              Snooze
            </Button>
          </>
        }
      >
        <div className="space-y-3">
          <Field label="Snooze until (your local time)" htmlFor="snooze-until" error={snoozeOpen ? actionError : null}>
            <Input id="snooze-until" type="datetime-local" value={snoozeUntil} onChange={(e) => setSnoozeUntil(e.target.value)} />
          </Field>
          <Field label="Note (optional)" htmlFor="snooze-note">
            <Input id="snooze-note" value={note} maxLength={1000} onChange={(e) => setNote(e.target.value)} />
          </Field>
        </div>
      </Dialog>

      {demoMode && !isStaleRule && (
        <p className="text-xs text-faint">
          Demo mode: use the Demo controls on the{" "}
          <Link to="/security" className="text-accent hover:underline">
            Security page
          </Link>{" "}
          to simulate a fix on the synthetic host, then choose Verify fix here.
        </p>
      )}
    </div>
  );
}
