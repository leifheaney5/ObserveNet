import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { Dialog } from "@/components/ui/overlays";
import { Button, Code, Field, Notice, Textarea } from "@/components/ui/primitives";
import { api, ApiError } from "@/lib/api";

interface ImportResult {
  created: number;
  matched: number;
  rows: number;
  source_id: number;
  row_errors: string[];
}

const SAMPLE = "ip,mac,hostname\n192.168.1.20,3c:22:fb:12:34:56,living-room-tv";

export function ImportDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const qc = useQueryClient();
  const [content, setContent] = useState("");
  const mutation = useMutation({
    mutationFn: () => api.post<ImportResult>("/devices/import", { content }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["devices"] });
      void qc.invalidateQueries({ queryKey: ["overview"] });
    },
  });
  const result = mutation.data;

  function close(next: boolean) {
    if (!next) {
      mutation.reset();
      setContent("");
    }
    onOpenChange(next);
  }

  return (
    <Dialog
      open={open}
      onOpenChange={close}
      wide
      title="Import router client list"
      description="Paste the DHCP / client table exported from your router. This is a point-in-time snapshot: imported devices are marked 'as of import', never as live."
      footer={
        result ? (
          <Button variant="primary" onClick={() => close(false)}>
            Done
          </Button>
        ) : (
          <>
            <Button onClick={() => close(false)}>Cancel</Button>
            <Button variant="primary" loading={mutation.isPending} disabled={!content.trim()} onClick={() => mutation.mutate()}>
              Import
            </Button>
          </>
        )
      }
    >
      {result ? (
        <div className="space-y-3">
          <Notice tone="good" title="Import finished">
            {result.rows} valid row(s): <strong className="text-ink">{result.created}</strong> new device(s),{" "}
            <strong className="text-ink">{result.matched}</strong> matched existing devices.
          </Notice>
          {result.row_errors.length > 0 && (
            <Notice tone="warn" title={`${result.row_errors.length} row(s) skipped`}>
              <ul className="mt-1 list-disc space-y-0.5 pl-4">
                {result.row_errors.map((e) => (
                  <li key={e}>{e}</li>
                ))}
              </ul>
            </Notice>
          )}
        </div>
      ) : (
        <div className="space-y-3">
          <Field
            label="Client list (CSV with header row, or JSON array)"
            htmlFor="import-content"
            hint={
              <>
                Recognized columns: <Code>ip</Code>, <Code>mac</Code>, <Code>hostname</Code> (common variants such as <Code>ip_address</Code> /{" "}
                <Code>name</Code> also work). Only private LAN addresses are accepted; at most 2000 rows.
              </>
            }
            error={mutation.error instanceof ApiError ? mutation.error.message : mutation.error ? "Import failed." : null}
          >
            <Textarea
              id="import-content"
              value={content}
              onChange={(e) => setContent(e.target.value)}
              placeholder={SAMPLE}
              spellCheck={false}
              className="min-h-[180px] font-mono text-xs"
            />
          </Field>
        </div>
      )}
    </Dialog>
  );
}
