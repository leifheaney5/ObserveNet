import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FlaskConical } from "lucide-react";

import { Switch } from "@/components/ui/overlays";
import { Card, CardBody, CardHeader, ErrorState, Skeleton } from "@/components/ui/primitives";
import { DemoTag } from "@/components/ui/status";
import type { DemoState } from "@/features/security/types";
import { api } from "@/lib/api";

/** DEMO_MODE only: flips a setting on a synthetic host so the Verify loop can be shown. */
export function DemoControls({ isAdmin }: { isAdmin: boolean }) {
  const qc = useQueryClient();
  const state = useQuery({ queryKey: ["demo-state"], queryFn: () => api.get<DemoState>("/demo/state") });
  const fix = useMutation({
    mutationFn: (v: { item: string; fixed: boolean }) => api.post("/demo/fix", v),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["demo-state"] }),
  });
  return (
    <Card className="border-violet/40">
      <CardHeader
        icon={<FlaskConical className="h-4 w-4 text-violet" />}
        title="Demo controls"
        subtitle="Simulates an operator changing a setting on a synthetic host, so the fix → Verify loop can be demonstrated without touching a real machine. Flip a switch, then open the matching finding and choose Verify fix."
        actions={<DemoTag />}
      />
      <CardBody>
        {state.isLoading ? (
          <Skeleton className="h-20" />
        ) : state.isError ? (
          <ErrorState error={state.error} retry={() => void state.refetch()} />
        ) : (
          <ul className="space-y-2.5">
            {state.data?.items.map((i) => (
              <li key={i.item} className="flex items-center justify-between gap-4 text-sm">
                <label htmlFor={`demo-${i.item}`} className="text-ink">
                  {i.label}
                  <span className="ml-2 text-xs text-faint">{i.fixed ? "simulated as fixed" : "not fixed"}</span>
                </label>
                <Switch
                  id={`demo-${i.item}`}
                  label={i.label}
                  checked={i.fixed}
                  disabled={!isAdmin || fix.isPending}
                  onCheckedChange={(fixed) => fix.mutate({ item: i.item, fixed })}
                />
              </li>
            ))}
          </ul>
        )}
        {!isAdmin && <p className="mt-3 text-xs text-faint">Only administrators can change demo controls.</p>}
      </CardBody>
    </Card>
  );
}
