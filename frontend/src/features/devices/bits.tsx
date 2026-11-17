import { Shuffle, Star } from "lucide-react";
import type { ReactNode } from "react";

import { Tooltip } from "@/components/ui/overlays";
import { Badge, type Tone } from "@/components/ui/status";
import { cn } from "@/lib/cn";
import { titleCase } from "@/lib/format";

/** Focusable tooltip trigger that is a real control (keyboard users can reach the explanation). */
export function Hint({ content, children, label }: { content: ReactNode; children: ReactNode; label?: string }) {
  return (
    <Tooltip content={content}>
      <button type="button" aria-label={label} className="inline-flex cursor-help items-center rounded-full text-left">
        {children}
      </button>
    </Tooltip>
  );
}

const CONFIDENCE_TONE: Record<string, Tone> = { high: "good", medium: "info", low: "neutral" };

export function ConfidenceBadge({ confidence, reason }: { confidence: string; reason?: string | null }) {
  const badge = <Badge tone={CONFIDENCE_TONE[confidence] ?? "neutral"}>{titleCase(confidence)} confidence</Badge>;
  if (!reason) return badge;
  return <Hint content={reason}>{badge}</Hint>;
}

export function RandomizedMacBadge() {
  return (
    <Hint content="This MAC address is locally administered, which phones and laptops use when randomizing. The entry may be the same physical device as another one and is never merged automatically.">
      <Badge tone="violet" icon={Shuffle}>
        Randomized MAC
      </Badge>
    </Hint>
  );
}

export function VendorCell({ vendor, method }: { vendor: string | null; method?: string | null }) {
  if (vendor) {
    return (
      <span title={method ? `Derived via ${method}` : undefined} className="text-ink">
        {vendor}
      </span>
    );
  }
  return (
    <Hint content="Vendor lookup needs an operator-supplied IEEE OUI registry file (OBSERVENET_OUI_FILE). None is bundled, and randomized MAC addresses have no vendor.">
      <span className="text-faint underline decoration-dotted underline-offset-2">Unknown</span>
    </Hint>
  );
}

export function FavoriteButton({
  favorite,
  onToggle,
  disabled,
  name,
}: {
  favorite: boolean;
  onToggle?: () => void;
  disabled?: boolean;
  name: string;
}) {
  const icon = <Star className={cn("h-4 w-4", favorite ? "fill-warn text-warn" : "text-faint")} aria-hidden />;
  if (!onToggle) {
    return (
      <span className="inline-flex p-1" aria-label={favorite ? "Favorite" : "Not a favorite"} role="img">
        {icon}
      </span>
    );
  }
  return (
    <button
      type="button"
      onClick={onToggle}
      disabled={disabled}
      aria-pressed={favorite}
      aria-label={favorite ? `Remove ${name} from favorites` : `Add ${name} to favorites`}
      className="rounded-md p-1 hover:bg-raised disabled:opacity-50"
    >
      {icon}
    </button>
  );
}

export function Mono({ children, className }: { children: ReactNode; className?: string }) {
  return <span className={cn("num font-mono text-xs", className)}>{children}</span>;
}

export function DefinitionList({ items }: { items: { term: string; value: ReactNode }[] }) {
  return (
    <dl className="grid grid-cols-[minmax(110px,auto)_1fr] items-baseline gap-x-4 gap-y-2 text-sm">
      {items.map((i) => (
        <div key={i.term} className="contents">
          <dt className="text-xs text-faint">{i.term}</dt>
          <dd className="min-w-0 break-words text-ink">{i.value}</dd>
        </div>
      ))}
    </dl>
  );
}
