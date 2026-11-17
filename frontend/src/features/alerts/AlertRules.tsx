import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { Switch } from "@/components/ui/overlays";
import { Button, Card, ErrorState, NativeSelect, Input, Skeleton } from "@/components/ui/primitives";
import { Badge, SeverityBadge } from "@/components/ui/status";
import { FormError, SavedFlash } from "@/features/alerts/shared";
import { kindLabel } from "@/features/alerts/util";
import { useAuth } from "@/hooks/useAuth";
import { api, type AlertRule } from "@/lib/api";
import { formatBps } from "@/lib/format";

interface ParamSpec {
  label: string;
  unit: string;
  hint?: string;
  readOnly?: boolean;
}

// Human labels for the parameter names used by backend/observenet/services/alerts.py.
const PARAMS: Record<string, ParamSpec> = {
  threshold_ms: { label: "Fire above", unit: "ms" },
  clear_threshold_ms: { label: "Clear below", unit: "ms", hint: "Hysteresis: must be ≤ the fire threshold." },
  threshold_pct: { label: "Fire above", unit: "%" },
  clear_threshold_pct: { label: "Clear below", unit: "%", hint: "Hysteresis: must be ≤ the fire threshold." },
  threshold_bps: { label: "Fire above", unit: "bit/s" },
  clear_threshold_bps: { label: "Clear below", unit: "bit/s", hint: "Hysteresis: must be ≤ the fire threshold." },
  threshold_s: { label: "Silent for more than", unit: "s" },
  window_s: { label: "Averaging window", unit: "s" },
  for_s: { label: "Debounce — must hold for", unit: "s" },
  clear_for_s: { label: "Clear must hold for", unit: "s" },
  cooldown_s: { label: "Cooldown after resolving", unit: "s" },
  min_samples: { label: "Minimum samples in window", unit: "samples", hint: "Fewer samples = unknown: neither fires nor clears." },
  auto_resolve_hours: { label: "Auto-close after", unit: "h" },
  learning_minutes: {
    label: "Learning period for a new source",
    unit: "min",
    hint: "Shown for reference: this version applies a fixed 10-minute learning period.",
    readOnly: true,
  },
};
const ORDER = Object.keys(PARAMS);

function rank(key: string): number {
  const i = ORDER.indexOf(key);
  return i === -1 ? ORDER.length : i;
}

function RuleCard({ rule }: { rule: AlertRule }) {
  const { isAdmin } = useAuth();
  const qc = useQueryClient();
  const [enabled, setEnabled] = useState(rule.enabled);
  const [severity, setSeverity] = useState(rule.severity);
  const [params, setParams] = useState<Record<string, string>>(() => Object.fromEntries(Object.entries(rule.params).map(([k, v]) => [k, String(v)])));
  const [savedAt, setSavedAt] = useState<number | null>(null);

  const keys = Object.keys(rule.params).sort((a, b) => rank(a) - rank(b));
  const invalid = keys.filter((k) => !PARAMS[k]?.readOnly && (params[k]?.trim() === "" || !Number.isFinite(Number(params[k])) || Number(params[k]) < 0));
  const dirty =
    enabled !== rule.enabled || severity !== rule.severity || keys.some((k) => !PARAMS[k]?.readOnly && Number(params[k]) !== Number(rule.params[k]));

  const save = useMutation({
    mutationFn: () => {
      const changed: Record<string, number> = {};
      for (const k of keys) {
        if (!PARAMS[k]?.readOnly && Number(params[k]) !== Number(rule.params[k])) changed[k] = Number(params[k]);
      }
      return api.patch<AlertRule>(`/alert-rules/${rule.id}`, {
        enabled: enabled !== rule.enabled ? enabled : undefined,
        severity: severity !== rule.severity ? severity : undefined,
        params: Object.keys(changed).length ? changed : undefined,
      });
    },
    onSuccess: () => {
      setSavedAt(Date.now());
      void qc.invalidateQueries({ queryKey: ["alert-rules"] });
    },
  });

  return (
    <Card className="p-4">
      <form
        onSubmit={(e) => {
          e.preventDefault();
          save.mutate();
        }}
        aria-label={`Rule: ${rule.name}`}
      >
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h3 className="text-sm font-medium text-ink">{rule.name}</h3>
              <Badge>{kindLabel(rule.kind)}</Badge>
              <SeverityBadge severity={severity} />
              {!enabled && <Badge>Disabled</Badge>}
            </div>
            {rule.description && <p className="mt-1 max-w-3xl text-xs leading-relaxed text-muted">{rule.description}</p>}
          </div>
          <div className="flex items-center gap-2 text-xs text-muted">
            <span id={`rule-${rule.id}-enabled`}>{enabled ? "Enabled" : "Disabled"}</span>
            <Switch checked={enabled} onCheckedChange={setEnabled} disabled={!isAdmin || save.isPending} label={`Enable rule ${rule.name}`} />
          </div>
        </div>

        <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          <div className="space-y-1.5">
            <label htmlFor={`rule-${rule.id}-severity`} className="block text-xs font-medium text-muted">
              Severity
            </label>
            <NativeSelect id={`rule-${rule.id}-severity`} value={severity} disabled={!isAdmin || save.isPending} onChange={(e) => setSeverity(e.target.value)}>
              <option value="info">Info</option>
              <option value="warning">Warning</option>
              <option value="critical">Critical</option>
            </NativeSelect>
          </div>
          {keys.map((k) => {
            const spec = PARAMS[k] ?? { label: k.replace(/_/g, " "), unit: "" };
            const id = `rule-${rule.id}-${k}`;
            const bad = invalid.includes(k);
            return (
              <div key={k} className="space-y-1.5">
                <label htmlFor={id} className="block text-xs font-medium text-muted">
                  {spec.label} {spec.unit && <span className="font-normal text-faint">({spec.unit})</span>}
                </label>
                <Input
                  id={id}
                  type="number"
                  inputMode="decimal"
                  min={0}
                  step="any"
                  value={params[k] ?? ""}
                  disabled={!isAdmin || save.isPending || spec.readOnly}
                  aria-invalid={bad}
                  aria-describedby={`${id}-hint`}
                  onChange={(e) => setParams((p) => ({ ...p, [k]: e.target.value }))}
                  className="num"
                />
                <p id={`${id}-hint`} className="min-h-[14px] text-[11px] leading-snug text-faint">
                  {bad ? <span className="text-crit">Enter a number ≥ 0.</span> : spec.unit === "bit/s" && Number(params[k]) > 0 ? `= ${formatBps(Number(params[k]))}. ${spec.hint ?? ""}` : spec.hint}
                </p>
              </div>
            );
          })}
        </div>

        {isAdmin && (
          <div className="mt-3 flex flex-wrap items-center gap-3">
            <Button type="submit" size="sm" variant="primary" loading={save.isPending} disabled={!dirty || invalid.length > 0}>
              Save rule
            </Button>
            <SavedFlash at={savedAt} />
            <FormError error={save.error} />
          </div>
        )}
      </form>
    </Card>
  );
}

export function AlertRules() {
  const { isAdmin } = useAuth();
  const rules = useQuery({ queryKey: ["alert-rules"], queryFn: () => api.get<AlertRule[]>("/alert-rules") });
  if (rules.isLoading) return <Skeleton className="h-60 w-full" />;
  if (rules.isError) return <ErrorState error={rules.error} retry={() => void rules.refetch()} />;
  return (
    <div className="space-y-3">
      {!isAdmin && <p className="text-xs text-faint">Your role is read-only: rule thresholds can be viewed but only an administrator can change them.</p>}
      {rules.data?.map((r) => <RuleCard key={`${r.id}-${JSON.stringify(r.params)}-${r.enabled}-${r.severity}`} rule={r} />)}
    </div>
  );
}
