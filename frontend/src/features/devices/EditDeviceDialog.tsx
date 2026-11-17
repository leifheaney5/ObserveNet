import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { ASSET_TYPES, parseTags } from "@/features/devices/types";
import { Dialog } from "@/components/ui/overlays";
import { Button, Field, Input, NativeSelect, Textarea } from "@/components/ui/primitives";
import { Badge } from "@/components/ui/status";
import { api, ApiError, type Device } from "@/lib/api";
import { titleCase } from "@/lib/format";

export function EditDeviceDialog({ device, open, onOpenChange }: { device: Device; open: boolean; onOpenChange: (o: boolean) => void }) {
  const qc = useQueryClient();
  const [form, setForm] = useState({ display_name: "", asset_type: "unknown", group_name: "", owner: "", tags: "", notes: "" });

  useEffect(() => {
    if (open) {
      setForm({
        display_name: device.display_name ?? "",
        asset_type: device.asset_type,
        group_name: device.group_name ?? "",
        owner: device.owner ?? "",
        tags: device.tags.join(", "),
        notes: device.notes ?? "",
      });
    }
  }, [open, device]);

  const mutation = useMutation({
    mutationFn: () =>
      api.patch<Device>(`/devices/${device.id}`, {
        display_name: form.display_name.trim(),
        asset_type: form.asset_type,
        group_name: form.group_name.trim(),
        owner: form.owner.trim(),
        tags: parseTags(form.tags),
        notes: form.notes,
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["device", device.id] });
      void qc.invalidateQueries({ queryKey: ["devices"] });
      onOpenChange(false);
    },
  });
  const set = (key: keyof typeof form) => (e: { target: { value: string } }) => setForm((f) => ({ ...f, [key]: e.target.value }));
  const tags = parseTags(form.tags);

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      wide
      title="Edit device details"
      description="These labels are yours: they never change how the device is identified. Every edit is recorded in the audit log."
      footer={
        <>
          <Button onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button variant="primary" loading={mutation.isPending} onClick={() => mutation.mutate()}>
            Save
          </Button>
        </>
      }
    >
      <form
        className="grid gap-4 sm:grid-cols-2"
        onSubmit={(e) => {
          e.preventDefault();
          mutation.mutate();
        }}
      >
        <Field label="Alias" htmlFor="dev-alias" hint={device.hostname ? `Observed hostname: ${device.hostname}` : "Shown instead of the observed address."}>
          <Input id="dev-alias" value={form.display_name} maxLength={120} onChange={set("display_name")} />
        </Field>
        <Field label="Type" htmlFor="dev-type">
          <NativeSelect id="dev-type" value={form.asset_type} onChange={set("asset_type")}>
            {ASSET_TYPES.map((t) => (
              <option key={t} value={t}>
                {titleCase(t)}
              </option>
            ))}
          </NativeSelect>
        </Field>
        <Field label="Group" htmlFor="dev-group">
          <Input id="dev-group" value={form.group_name} maxLength={80} onChange={set("group_name")} placeholder="e.g. Living room" />
        </Field>
        <Field label="Owner" htmlFor="dev-owner">
          <Input id="dev-owner" value={form.owner} maxLength={120} onChange={set("owner")} />
        </Field>
        <div className="sm:col-span-2">
          <Field label="Tags (comma-separated)" htmlFor="dev-tags" hint="Up to 20 tags of 40 characters.">
            <Input id="dev-tags" value={form.tags} onChange={set("tags")} placeholder="iot, guest" />
          </Field>
          {tags.length > 0 && (
            <ul className="mt-2 flex flex-wrap gap-1.5" aria-label="Tags preview">
              {tags.map((t) => (
                <li key={t}>
                  <Badge>{t}</Badge>
                </li>
              ))}
            </ul>
          )}
        </div>
        <div className="sm:col-span-2">
          <Field
            label="Notes"
            htmlFor="dev-notes"
            error={mutation.error instanceof ApiError ? mutation.error.message : mutation.error ? "Could not save." : null}
          >
            <Textarea id="dev-notes" value={form.notes} maxLength={4000} onChange={set("notes")} />
          </Field>
        </div>
        <button type="submit" className="hidden" aria-hidden tabIndex={-1} />
      </form>
    </Dialog>
  );
}
