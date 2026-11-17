import { Check, Copy } from "lucide-react";
import { useEffect, useState } from "react";

import { Badge } from "@/components/ui/status";
import { CHECK_STATUS, VERIFY_STATUS } from "@/features/security/types";
import { cn } from "@/lib/cn";
import { titleCase } from "@/lib/format";

export function VerifyStatusBadge({ status }: { status: string }) {
  const s = VERIFY_STATUS[status] ?? { tone: "neutral" as const, label: titleCase(status), help: "" };
  return (
    <Badge tone={s.tone} title={s.help}>
      {s.label}
    </Badge>
  );
}

export function CheckStatusBadge({ status }: { status: string }) {
  const s = CHECK_STATUS[status] ?? { tone: "neutral" as const, label: titleCase(status) };
  return <Badge tone={s.tone}>{s.label}</Badge>;
}

export function CopyButton({ text, label = "Copy" }: { text: string; label?: string }) {
  const [state, setState] = useState<"idle" | "copied" | "failed">("idle");
  useEffect(() => {
    if (state === "idle") return;
    const id = setTimeout(() => setState("idle"), 1800);
    return () => clearTimeout(id);
  }, [state]);
  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setState("copied");
    } catch {
      setState("failed");
    }
  }
  return (
    <button
      type="button"
      onClick={() => void copy()}
      className="inline-flex items-center gap-1 rounded-md border border-line bg-raised px-2 py-1 text-[11px] text-muted hover:border-faint hover:text-ink"
    >
      {state === "copied" ? <Check className="h-3 w-3 text-good" aria-hidden /> : <Copy className="h-3 w-3" aria-hidden />}
      <span aria-live="polite">{state === "copied" ? "Copied" : state === "failed" ? "Select and copy manually" : label}</span>
    </button>
  );
}

export function JsonBlock({ value, className }: { value: unknown; className?: string }) {
  return (
    <pre className={cn("max-h-72 overflow-auto rounded-lg border border-line bg-bg/70 p-3 font-mono text-[11px] leading-relaxed text-muted", className)}>
      {JSON.stringify(value, null, 2)}
    </pre>
  );
}

export function JsonDetails({ summary, value }: { summary: string; value: unknown }) {
  return (
    <details className="group mt-2">
      <summary className="cursor-pointer select-none rounded text-xs text-accent hover:underline">{summary}</summary>
      <JsonBlock value={value} className="mt-2" />
    </details>
  );
}

export function StatTile({ label, value, hint, emphasis }: { label: string; value: number; hint?: string; emphasis?: boolean }) {
  return (
    <div className="rounded-lg border border-line bg-raised/50 px-3 py-2.5" title={hint}>
      <dd className={cn("num text-xl font-semibold", emphasis && value > 0 ? "text-ink" : value > 0 ? "text-ink" : "text-faint")}>{value}</dd>
      <dt className="text-[10px] uppercase tracking-wider text-faint">{label}</dt>
    </div>
  );
}
