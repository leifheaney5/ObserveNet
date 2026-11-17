import { Loader2 } from "lucide-react";
import { useEffect, useState } from "react";

import { EmptyState, Table, Td, Th } from "@/components/ui/primitives";
import { Badge } from "@/components/ui/status";
import { JsonDetails, VerifyStatusBadge } from "@/features/security/bits";
import { formatScalar, type VerificationDiff } from "@/features/security/types";
import type { Verification } from "@/lib/api";
import { formatDateTime } from "@/lib/format";

function Countdown({ deadline }: { deadline: string }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);
  const left = Math.max(0, Math.round((Date.parse(deadline) - now) / 1000));
  return (
    <span className="num" role="timer" aria-live="off">
      {left > 0 ? `${left} s left before the result is recorded as Unknown` : "deadline reached — finalizing"}
    </span>
  );
}

function Run({ run }: { run: Verification }) {
  const diff = run.diff as unknown as VerificationDiff | null;
  const pending = run.status === "pending";
  return (
    <li className="rounded-lg border border-line bg-raised/30 p-3.5">
      <div className="flex flex-wrap items-center gap-2">
        <VerifyStatusBadge status={pending ? "pending" : (run.outcome ?? "unknown")} />
        <Badge>{run.trigger === "manual" ? "Requested manually" : "Continuous re-check"}</Badge>
        <span className="num text-xs text-faint">requested {formatDateTime(run.requested_at)}</span>
      </div>
      {pending ? (
        <div className="mt-2 flex items-start gap-2 text-xs text-muted" role="status">
          <Loader2 className="mt-0.5 h-3.5 w-3.5 shrink-0 animate-spin text-accent" aria-hidden />
          <div>
            <p>Waiting for fresh evidence from the collector — only a snapshot collected after the request counts.</p>
            <p className="mt-0.5 text-faint">
              <Countdown deadline={run.deadline_at} />
            </p>
          </div>
        </div>
      ) : (
        <>
          <p className="mt-2 text-sm leading-relaxed text-ink">{run.outcome_reason}</p>
          <p className="mt-1 text-[11px] text-faint">
            Completed <span className="num">{formatDateTime(run.completed_at)}</span> · algorithm <span className="font-mono">{run.algorithm_version}</span>
            {run.before_evidence_id !== null && <> · before: evidence #{run.before_evidence_id}</>}
            {run.after_evidence_id !== null && <> · after: evidence #{run.after_evidence_id}</>}
          </p>
          {diff && diff.changes.length > 0 && (
            <div className="mt-3 overflow-hidden rounded-lg border border-line">
              <Table>
                <caption className="sr-only">Evidence before and after</caption>
                <thead>
                  <tr>
                    <Th>Field</Th>
                    <Th>Before</Th>
                    <Th>After</Th>
                  </tr>
                </thead>
                <tbody>
                  {diff.changes.map((c) => (
                    <tr key={c.field}>
                      <Td className="font-mono text-xs text-muted">{c.field}</Td>
                      <Td className="break-all font-mono text-xs text-warn">{formatScalar(c.before)}</Td>
                      <Td className="break-all font-mono text-xs text-good">{formatScalar(c.after)}</Td>
                    </tr>
                  ))}
                </tbody>
              </Table>
            </div>
          )}
          {diff && diff.changes.length === 0 && run.outcome !== "unknown" && (
            <p className="mt-2 text-xs text-muted">The evidence fields were identical before and after.</p>
          )}
          {diff && (Object.keys(diff.before).length > 0 || Object.keys(diff.after).length > 0) && (
            <JsonDetails summary="Raw before / after evidence" value={{ before: diff.before, after: diff.after }} />
          )}
        </>
      )}
    </li>
  );
}

export function VerificationRuns({ runs }: { runs: Verification[] }) {
  if (!runs.length) {
    return (
      <EmptyState title="Not verified yet" className="py-6">
        No re-check has run. After you change the configuration on the device, choose Verify fix: ObserveNet re-runs the same read-only check on
        fresh evidence and records the result with a before/after comparison.
      </EmptyState>
    );
  }
  return (
    <ul className="space-y-3">
      {runs.map((r) => (
        <Run key={r.id} run={r} />
      ))}
    </ul>
  );
}
