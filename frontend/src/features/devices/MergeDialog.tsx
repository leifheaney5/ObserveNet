import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { Dialog } from "@/components/ui/overlays";
import { Button, Field, Input, Notice, Skeleton } from "@/components/ui/primitives";
import { api, ApiError, type Device, type DevicePage } from "@/lib/api";
import { cn } from "@/lib/cn";

/** Explicit, audited merge of *this* device into a chosen target. */
export function MergeDialog({ device, open, onOpenChange }: { device: Device; open: boolean; onOpenChange: (o: boolean) => void }) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [q, setQ] = useState("");
  const [targetId, setTargetId] = useState<number | null>(null);
  const [reason, setReason] = useState("");

  const candidates = useQuery({
    queryKey: ["devices", "merge-candidates", q],
    queryFn: () => api.get<DevicePage>("/devices", { q: q || undefined, limit: 25 }),
    enabled: open,
  });
  const merge = useMutation({
    mutationFn: () => api.post<{ merge_id: number; target_asset_id: number }>(`/devices/${device.id}/merge`, { into_id: targetId, reason: reason.trim() || null }),
    onSuccess: (res) => {
      void qc.invalidateQueries({ queryKey: ["devices"] });
      void qc.invalidateQueries({ queryKey: ["device"] });
      onOpenChange(false);
      navigate(`/devices/${res.target_asset_id}`);
    },
  });
  const items = (candidates.data?.items ?? []).filter((d) => d.id !== device.id);

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        if (!o) {
          merge.reset();
          setTargetId(null);
          setReason("");
          setQ("");
        }
        onOpenChange(o);
      }}
      wide
      title={`Merge “${device.name}” into another device`}
      description="Use this when two entries are the same physical device (for example a randomized MAC and its real one). Identities and address sightings move to the target; the merge is recorded in the audit log and can be undone."
      footer={
        <>
          <Button onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button variant="primary" disabled={targetId === null} loading={merge.isPending} onClick={() => merge.mutate()}>
            Merge
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <Field label="Find the device to keep" htmlFor="merge-search">
          <Input id="merge-search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Name, IP or MAC" autoComplete="off" />
        </Field>
        <fieldset>
          <legend className="mb-1.5 text-xs font-medium text-muted">Target device</legend>
          {candidates.isLoading ? (
            <Skeleton className="h-24" />
          ) : items.length ? (
            <ul className="max-h-56 space-y-1 overflow-y-auto rounded-lg border border-line p-1">
              {items.map((d) => (
                <li key={d.id}>
                  <label
                    className={cn(
                      "flex cursor-pointer items-center gap-3 rounded-md px-2.5 py-1.5 text-sm hover:bg-raised",
                      targetId === d.id && "bg-accent/10 ring-1 ring-inset ring-accent/30",
                    )}
                  >
                    <input type="radio" name="merge-target" className="accent-cyan-400" checked={targetId === d.id} onChange={() => setTargetId(d.id)} />
                    <span className="min-w-0 flex-1 truncate text-ink">{d.name}</span>
                    <span className="num font-mono text-xs text-muted">{d.primary_ip ?? "—"}</span>
                    <span className="num hidden font-mono text-xs text-faint sm:inline">{d.primary_mac ?? ""}</span>
                  </label>
                </li>
              ))}
            </ul>
          ) : (
            <p className="rounded-lg border border-dashed border-line px-3 py-4 text-center text-xs text-muted">No other device matches.</p>
          )}
        </fieldset>
        <Field label="Reason (recorded in the audit trail)" htmlFor="merge-reason">
          <Input id="merge-reason" value={reason} maxLength={500} onChange={(e) => setReason(e.target.value)} placeholder="e.g. phone with private Wi-Fi address" />
        </Field>
        {merge.error && (
          <Notice tone="crit" title="Merge refused">
            {merge.error instanceof ApiError ? merge.error.message : "The merge could not be completed."}
          </Notice>
        )}
      </div>
    </Dialog>
  );
}
