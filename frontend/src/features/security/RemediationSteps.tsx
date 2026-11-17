import { Terminal } from "lucide-react";

import { EmptyState } from "@/components/ui/primitives";
import { CopyButton } from "@/features/security/bits";
import type { Schemas } from "@/lib/api";

export function RemediationSteps({ steps }: { steps: Schemas["RemediationOut"][] }) {
  if (!steps.length) {
    return (
      <EmptyState title="No remediation steps recorded" className="py-6">
        This rule did not attach manual instructions.
      </EmptyState>
    );
  }
  return (
    <ol className="space-y-4">
      {steps.map((s) => (
        <li key={s.position} className="flex gap-3">
          <span className="num mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full border border-line bg-raised text-[11px] font-medium text-muted" aria-hidden>
            {s.position}
          </span>
          <div className="min-w-0 flex-1">
            <p className="text-sm leading-relaxed text-ink">
              <span className="sr-only">Step {s.position}: </span>
              {s.instruction}
            </p>
            {s.command && (
              <div className="mt-2 rounded-lg border border-line bg-bg/70">
                <div className="flex items-center justify-between gap-2 border-b border-line/70 px-3 py-1.5">
                  <span className="flex min-w-0 items-center gap-1.5 text-[11px] leading-snug text-faint">
                    <Terminal className="h-3.5 w-3.5 shrink-0" aria-hidden />
                    <span>Run this yourself — ObserveNet never executes commands</span>
                  </span>
                  <CopyButton text={s.command} />
                </div>
                <pre className="overflow-x-auto px-3 py-2 font-mono text-xs text-ink">
                  <code>{s.command}</code>
                </pre>
              </div>
            )}
          </div>
        </li>
      ))}
    </ol>
  );
}
