import { CheckCircle2 } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";

import { errorMessage } from "@/features/alerts/util";

/** Brief, polite "Saved" confirmation that clears itself. */
export function SavedFlash({ at, children = "Saved" }: { at: number | null; children?: ReactNode }) {
  const [visible, setVisible] = useState(false);
  useEffect(() => {
    if (at === null) return;
    setVisible(true);
    const id = setTimeout(() => setVisible(false), 4000);
    return () => clearTimeout(id);
  }, [at]);
  return (
    <span role="status" aria-live="polite" className="inline-flex items-center gap-1 text-xs text-good">
      {visible && (
        <>
          <CheckCircle2 className="h-3.5 w-3.5" aria-hidden /> {children}
        </>
      )}
    </span>
  );
}

export function FormError({ error }: { error: unknown }) {
  if (!error) return null;
  return (
    <p role="alert" className="text-xs leading-relaxed text-crit">
      {errorMessage(error)}
    </p>
  );
}
