import { AlertTriangle, Inbox, Loader2 } from "lucide-react";
import { forwardRef, type ButtonHTMLAttributes, type HTMLAttributes, type InputHTMLAttributes, type ReactNode, type TextareaHTMLAttributes } from "react";

import { cn } from "@/lib/cn";

export function Card({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("rounded-xl border border-line/80 bg-surface/80 shadow-card backdrop-blur-sm", className)} {...props} />;
}

export function CardHeader({
  title,
  subtitle,
  actions,
  icon,
  className,
}: {
  title: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
  icon?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex flex-wrap items-start justify-between gap-3 px-5 pt-4 sm:flex-nowrap", className)}>
      <div className="flex min-w-0 items-start gap-2.5">
        {icon && <span className="mt-0.5 text-muted">{icon}</span>}
        <div className="min-w-0">
          <h2 className="truncate text-sm font-semibold tracking-tight text-ink">{title}</h2>
          {subtitle && <p className="mt-0.5 text-xs leading-relaxed text-muted">{subtitle}</p>}
        </div>
      </div>
      {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
    </div>
  );
}

export function CardBody({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("px-5 pb-5 pt-3", className)} {...props} />;
}

type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";
interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: "sm" | "md";
  loading?: boolean;
}

const BUTTON_VARIANTS: Record<ButtonVariant, string> = {
  primary: "bg-accent text-slate-950 hover:bg-accent/90 font-semibold",
  secondary: "border border-line bg-raised text-ink hover:border-faint hover:bg-raised/70",
  ghost: "text-muted hover:bg-raised hover:text-ink",
  danger: "border border-crit/40 bg-crit/10 text-crit hover:bg-crit/20",
};

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { className, variant = "secondary", size = "md", loading, disabled, children, type = "button", ...props },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type}
      disabled={disabled || loading}
      className={cn(
        "inline-flex items-center justify-center gap-1.5 rounded-lg transition-colors disabled:cursor-not-allowed disabled:opacity-50",
        size === "sm" ? "h-8 px-2.5 text-xs" : "h-9 px-3.5 text-sm",
        BUTTON_VARIANTS[variant],
        className,
      )}
      {...props}
    >
      {loading && <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />}
      {children}
    </button>
  );
});

const FIELD = "w-full rounded-lg border border-line bg-bg/60 px-3 text-sm text-ink placeholder:text-faint transition-colors hover:border-faint disabled:opacity-50";

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(function Input({ className, ...props }, ref) {
  return <input ref={ref} className={cn(FIELD, "h-9", className)} {...props} />;
});

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(function Textarea(
  { className, ...props },
  ref,
) {
  return <textarea ref={ref} className={cn(FIELD, "min-h-[80px] py-2", className)} {...props} />;
});

export function NativeSelect({ className, ...props }: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return <select className={cn(FIELD, "h-9 pr-8", className)} {...props} />;
}

export function Field({ label, hint, error, children, htmlFor }: { label: string; hint?: ReactNode; error?: string | null; children: ReactNode; htmlFor?: string }) {
  return (
    <div className="space-y-1.5">
      <label htmlFor={htmlFor} className="block text-xs font-medium text-muted">
        {label}
      </label>
      {children}
      {hint && !error && <p className="text-xs leading-relaxed text-faint">{hint}</p>}
      {error && (
        <p role="alert" className="text-xs text-crit">
          {error}
        </p>
      )}
    </div>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div aria-hidden className={cn("animate-pulse rounded-md bg-raised", className)} />;
}

export function Spinner({ label = "Loading" }: { label?: string }) {
  return (
    <div role="status" className="flex items-center justify-center gap-2 py-10 text-sm text-muted">
      <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> {label}…
    </div>
  );
}

export function EmptyState({ icon, title, children, action, className }: { icon?: ReactNode; title: string; children?: ReactNode; action?: ReactNode; className?: string }) {
  return (
    <div className={cn("flex flex-col items-center justify-center px-6 py-10 text-center", className)}>
      <div className="mb-3 flex h-10 w-10 items-center justify-center rounded-full border border-line bg-raised text-muted">
        {icon ?? <Inbox className="h-5 w-5" aria-hidden />}
      </div>
      <p className="text-sm font-medium text-ink">{title}</p>
      {children && <div className="mt-1.5 max-w-md text-xs leading-relaxed text-muted">{children}</div>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

export function ErrorState({ error, retry }: { error: unknown; retry?: () => void }) {
  const message = error instanceof Error ? error.message : "Something went wrong.";
  return (
    <div role="alert" className="flex flex-col items-center px-6 py-8 text-center">
      <AlertTriangle className="mb-2 h-5 w-5 text-warn" aria-hidden />
      <p className="text-sm text-ink">Could not load this data</p>
      <p className="mt-1 max-w-md text-xs text-muted">{message}</p>
      {retry && (
        <Button size="sm" className="mt-3" onClick={retry}>
          Retry
        </Button>
      )}
    </div>
  );
}

export function PageHeader({ title, description, actions }: { title: string; description?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="text-xl font-semibold tracking-tight text-ink">{title}</h1>
        {description && <p className="mt-1 max-w-3xl text-sm leading-relaxed text-muted">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

export function Table({ className, ...props }: HTMLAttributes<HTMLTableElement>) {
  return (
    <div className="overflow-x-auto">
      <table className={cn("w-full border-collapse text-left text-sm", className)} {...props} />
    </div>
  );
}

export function Th({ className, ...props }: React.ThHTMLAttributes<HTMLTableCellElement>) {
  return <th scope="col" className={cn("whitespace-nowrap border-b border-line px-3 py-2 text-[11px] font-medium uppercase tracking-wider text-faint", className)} {...props} />;
}

export function Td({ className, ...props }: React.TdHTMLAttributes<HTMLTableCellElement>) {
  return <td className={cn("border-b border-line/50 px-3 py-2.5 align-middle text-ink", className)} {...props} />;
}

export function Code({ children, className }: { children: ReactNode; className?: string }) {
  return <code className={cn("rounded bg-bg/80 px-1.5 py-0.5 font-mono text-[12px] text-ink ring-1 ring-line/70", className)}>{children}</code>;
}

export function Notice({ tone = "info", title, children, icon }: { tone?: "info" | "warn" | "crit" | "good"; title?: string; children: ReactNode; icon?: ReactNode }) {
  const tones = {
    info: "border-accent/25 bg-accent/[0.06]",
    warn: "border-warn/30 bg-warn/[0.07]",
    crit: "border-crit/30 bg-crit/[0.07]",
    good: "border-good/30 bg-good/[0.07]",
  } as const;
  return (
    <div className={cn("flex gap-3 rounded-lg border px-3.5 py-3 text-xs leading-relaxed text-muted", tones[tone])}>
      {icon && <span className="mt-0.5 shrink-0 text-ink">{icon}</span>}
      <div>
        {title && <p className="mb-0.5 text-sm font-medium text-ink">{title}</p>}
        {children}
      </div>
    </div>
  );
}
