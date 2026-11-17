import { Check, Copy, Lock, Plus, X } from "lucide-react";
import { useState, type ReactNode } from "react";

import { Button, Card, EmptyState, Input } from "@/components/ui/primitives";
import { numError, type NumSpec } from "@/features/settings/model";
import { cn } from "@/lib/cn";

export function ReadOnlyNote() {
  return (
    <p className="flex items-center gap-1.5 text-xs text-faint">
      <Lock className="h-3.5 w-3.5" aria-hidden /> Your role is read-only. Settings are visible but only an administrator can change them.
    </p>
  );
}

export function AdminOnly({ what }: { what: string }) {
  return (
    <Card>
      <EmptyState icon={<Lock className="h-5 w-5" />} title="Administrator role required">
        {what} is restricted to administrators. Your account has the read-only viewer role; ask an administrator if you need access.
      </EmptyState>
    </Card>
  );
}

export function CopyButton({ text, label = "Copy" }: { text: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  const [failed, setFailed] = useState(false);
  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setFailed(false);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      setFailed(true); // clipboard blocked (e.g. plain HTTP): the text stays selectable
    }
  }
  return (
    <Button size="sm" onClick={() => void copy()} aria-live="polite">
      {copied ? <Check className="h-3.5 w-3.5 text-good" aria-hidden /> : <Copy className="h-3.5 w-3.5" aria-hidden />}
      {copied ? "Copied" : failed ? "Select & copy manually" : label}
    </Button>
  );
}

export function CodeBlock({ children, label, copy = true }: { children: string; label: string; copy?: boolean }) {
  return (
    <div className="min-w-0 rounded-lg border border-line bg-bg/70">
      <div className="flex items-center justify-between gap-2 border-b border-line/70 px-3 py-1.5">
        <span className="text-[11px] font-medium uppercase tracking-wider text-faint">{label}</span>
        {copy && <CopyButton text={children} />}
      </div>
      {/* eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex -- scrollable region must be keyboard reachable */}
      <pre tabIndex={0} aria-label={label} className="overflow-x-auto px-3 py-2.5 font-mono text-xs leading-relaxed text-ink">
        {children}
      </pre>
    </div>
  );
}

/** Grid of labelled numeric inputs with range validation and the default shown. */
export function NumberGrid({
  idPrefix,
  specs,
  draft,
  onChange,
  disabled,
}: {
  idPrefix: string;
  specs: NumSpec[];
  draft: Record<string, string>;
  onChange: (key: string, value: string) => void;
  disabled?: boolean;
}) {
  return (
    <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
      {specs.map((s) => {
        const id = `${idPrefix}-${s.key}`;
        const err = numError(s, draft[s.key]);
        return (
          <div key={s.key} className="space-y-1.5">
            <label htmlFor={id} className="block text-xs font-medium text-muted">
              {s.label} <span className="font-normal text-faint">({s.unit})</span>
            </label>
            <Input
              id={id}
              type="number"
              inputMode="decimal"
              min={s.min}
              max={s.max}
              step={s.step ?? 1}
              value={draft[s.key] ?? ""}
              disabled={disabled}
              aria-invalid={!!err}
              aria-describedby={`${id}-hint`}
              onChange={(e) => onChange(s.key, e.target.value)}
              className="num"
            />
            <p id={`${id}-hint`} className="text-[11px] leading-snug text-faint">
              {err ? <span className="text-crit">{err} </span> : null}
              {s.hint}
              {s.defaultValue !== undefined && (
                <>
                  {s.hint ? " " : ""}Default {s.defaultValue} {s.unit}.
                </>
              )}
            </p>
          </div>
        );
      })}
    </div>
  );
}

/** Editable list of short string entries (CIDRs, proto/port pairs) rendered as removable chips. */
export function ListEditor({
  id,
  label,
  values,
  onChange,
  placeholder,
  validate,
  disabled,
  hint,
  emptyText,
  max = 200,
}: {
  id: string;
  label: string;
  values: string[];
  onChange: (next: string[]) => void;
  placeholder?: string;
  validate?: (value: string) => string | null;
  disabled?: boolean;
  hint?: ReactNode;
  emptyText: string;
  max?: number;
}) {
  const [input, setInput] = useState("");
  const [error, setError] = useState<string | null>(null);
  function add() {
    const v = input.trim();
    if (!v) return;
    const err = values.includes(v) ? "Already in the list." : values.length >= max ? `At most ${max} entries.` : (validate?.(v) ?? null);
    if (err) {
      setError(err);
      return;
    }
    onChange([...values, v]);
    setInput("");
    setError(null);
  }
  return (
    <div className="space-y-2">
      <label htmlFor={id} className="block text-xs font-medium text-muted">
        {label}
      </label>
      {values.length ? (
        <ul className="flex flex-wrap gap-1.5" aria-label={`${label}: current entries`}>
          {values.map((v) => (
            <li key={v} className="inline-flex items-center gap-1 rounded-md border border-line bg-raised py-0.5 pl-2 pr-1 font-mono text-xs text-ink">
              {v}
              <button
                type="button"
                disabled={disabled}
                onClick={() => onChange(values.filter((x) => x !== v))}
                aria-label={`Remove ${v}`}
                className={cn("rounded p-0.5 text-faint hover:bg-bg hover:text-crit disabled:pointer-events-none disabled:opacity-40")}
              >
                <X className="h-3 w-3" aria-hidden />
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-xs text-faint">{emptyText}</p>
      )}
      {!disabled && (
        <div className="flex max-w-md gap-2">
          <Input
            id={id}
            value={input}
            placeholder={placeholder}
            aria-invalid={!!error}
            aria-describedby={`${id}-msg`}
            onChange={(e) => {
              setInput(e.target.value);
              setError(null);
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                add();
              }
            }}
            className="font-mono"
          />
          <Button onClick={add} disabled={!input.trim()}>
            <Plus className="h-3.5 w-3.5" aria-hidden /> Add
          </Button>
        </div>
      )}
      <p id={`${id}-msg`} className="text-[11px] leading-relaxed text-faint">
        {error ? (
          <span role="alert" className="text-crit">
            {error}{" "}
          </span>
        ) : null}
        {hint}
      </p>
    </div>
  );
}

export function SaveRow({ children }: { children: ReactNode }) {
  return <div className="mt-5 flex flex-wrap items-center gap-3 border-t border-line/70 pt-4">{children}</div>;
}
