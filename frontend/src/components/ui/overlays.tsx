import * as DialogPrimitive from "@radix-ui/react-dialog";
import * as DropdownPrimitive from "@radix-ui/react-dropdown-menu";
import * as SwitchPrimitive from "@radix-ui/react-switch";
import * as TabsPrimitive from "@radix-ui/react-tabs";
import * as TooltipPrimitive from "@radix-ui/react-tooltip";
import { X } from "lucide-react";
import type { ReactNode } from "react";

import { cn } from "@/lib/cn";

export function Dialog({
  open,
  onOpenChange,
  title,
  description,
  children,
  footer,
  wide,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  wide?: boolean;
}) {
  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-40 bg-black/60 backdrop-blur-sm data-[state=open]:animate-fade-in" />
        <DialogPrimitive.Content
          className={cn(
            "fixed left-1/2 top-1/2 z-50 max-h-[88vh] w-[calc(100vw-2rem)] -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-xl border border-line bg-surface p-5 shadow-2xl",
            wide ? "max-w-2xl" : "max-w-md",
          )}
        >
          <div className="mb-4 flex items-start justify-between gap-4">
            <div>
              <DialogPrimitive.Title className="text-base font-semibold text-ink">{title}</DialogPrimitive.Title>
              {description ? (
                <DialogPrimitive.Description className="mt-1 text-xs leading-relaxed text-muted">{description}</DialogPrimitive.Description>
              ) : (
                <DialogPrimitive.Description className="sr-only">{title}</DialogPrimitive.Description>
              )}
            </div>
            <DialogPrimitive.Close className="rounded-md p-1 text-muted hover:bg-raised hover:text-ink" aria-label="Close dialog">
              <X className="h-4 w-4" />
            </DialogPrimitive.Close>
          </div>
          {children}
          {footer && <div className="mt-5 flex justify-end gap-2">{footer}</div>}
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}

export const DropdownMenu = DropdownPrimitive.Root;
export const DropdownMenuTrigger = DropdownPrimitive.Trigger;

export function DropdownMenuContent({ children, align = "end" }: { children: ReactNode; align?: "start" | "end" }) {
  return (
    <DropdownPrimitive.Portal>
      <DropdownPrimitive.Content
        align={align}
        sideOffset={6}
        className="z-50 min-w-[190px] rounded-lg border border-line bg-surface p-1 text-sm shadow-2xl data-[state=open]:animate-fade-in"
      >
        {children}
      </DropdownPrimitive.Content>
    </DropdownPrimitive.Portal>
  );
}

export function DropdownMenuItem({ children, onSelect, danger }: { children: ReactNode; onSelect?: () => void; danger?: boolean }) {
  return (
    <DropdownPrimitive.Item
      onSelect={onSelect}
      className={cn(
        "flex cursor-pointer select-none items-center gap-2 rounded-md px-2.5 py-1.5 outline-none data-[highlighted]:bg-raised",
        danger ? "text-crit" : "text-ink",
      )}
    >
      {children}
    </DropdownPrimitive.Item>
  );
}

export function DropdownMenuLabel({ children }: { children: ReactNode }) {
  return <DropdownPrimitive.Label className="px-2.5 py-1.5 text-xs text-faint">{children}</DropdownPrimitive.Label>;
}

export function DropdownMenuSeparator() {
  return <DropdownPrimitive.Separator className="my-1 h-px bg-line" />;
}

export function Switch({ checked, onCheckedChange, label, disabled, id }: { checked: boolean; onCheckedChange: (v: boolean) => void; label: string; disabled?: boolean; id?: string }) {
  return (
    <SwitchPrimitive.Root
      id={id}
      checked={checked}
      onCheckedChange={onCheckedChange}
      disabled={disabled}
      aria-label={label}
      className="relative h-5 w-9 shrink-0 rounded-full border border-line bg-raised transition-colors data-[state=checked]:border-accent/60 data-[state=checked]:bg-accent/30 disabled:opacity-50"
    >
      <SwitchPrimitive.Thumb className="block h-3.5 w-3.5 translate-x-0.5 rounded-full bg-muted transition-transform data-[state=checked]:translate-x-[18px] data-[state=checked]:bg-accent" />
    </SwitchPrimitive.Root>
  );
}

export const Tabs = TabsPrimitive.Root;

export function TabsList({ children, label }: { children: ReactNode; label: string }) {
  return (
    <TabsPrimitive.List aria-label={label} className="mb-4 flex flex-wrap gap-1 border-b border-line">
      {children}
    </TabsPrimitive.List>
  );
}

export function TabsTrigger({ value, children }: { value: string; children: ReactNode }) {
  return (
    <TabsPrimitive.Trigger
      value={value}
      className="-mb-px flex items-center gap-1.5 border-b-2 border-transparent px-3 py-2 text-sm text-muted transition-colors hover:text-ink data-[state=active]:border-accent data-[state=active]:text-ink"
    >
      {children}
    </TabsPrimitive.Trigger>
  );
}

export const TabsContent = TabsPrimitive.Content;

export const TooltipProvider = TooltipPrimitive.Provider;

export function Tooltip({ content, children }: { content: ReactNode; children: ReactNode }) {
  return (
    <TooltipPrimitive.Root delayDuration={150}>
      <TooltipPrimitive.Trigger asChild>{children}</TooltipPrimitive.Trigger>
      <TooltipPrimitive.Portal>
        <TooltipPrimitive.Content
          sideOffset={6}
          className="z-50 max-w-xs rounded-md border border-line bg-raised px-2.5 py-1.5 text-xs leading-relaxed text-ink shadow-xl"
        >
          {content}
        </TooltipPrimitive.Content>
      </TooltipPrimitive.Portal>
    </TooltipPrimitive.Root>
  );
}
