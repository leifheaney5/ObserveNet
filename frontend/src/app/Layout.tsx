import { useQuery } from "@tanstack/react-query";
import { Activity, Bell, FlaskConical, Gauge, LayoutDashboard, Lightbulb, LogOut, Menu, MonitorSmartphone, Settings, ShieldCheck, UserRound, Wifi, X } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { NavLink, useLocation, useNavigate } from "react-router-dom";

import { LiveIndicator } from "@/components/LiveIndicator";
import { Logo } from "@/components/Logo";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/ui/overlays";
import { useAuth } from "@/hooks/useAuth";
import { PERIODS, usePeriod } from "@/hooks/usePeriod";
import { api, type Alert } from "@/lib/api";
import { cn } from "@/lib/cn";
import { liveStore } from "@/lib/liveStore";
import { useQueryClient } from "@tanstack/react-query";

const NAV = [
  { to: "/", label: "Overview", icon: LayoutDashboard, end: true },
  { to: "/devices", label: "Devices", icon: MonitorSmartphone },
  { to: "/traffic", label: "Traffic", icon: Activity },
  { to: "/connectivity", label: "Connectivity", icon: Wifi },
  { to: "/security", label: "Security", icon: ShieldCheck },
  { to: "/alerts", label: "Alerts", icon: Bell },
  { to: "/insights", label: "Insights", icon: Lightbulb },
  { to: "/settings", label: "Settings", icon: Settings },
];

export function DemoBanner() {
  return (
    <div
      role="note"
      data-testid="demo-banner"
      className="flex items-center justify-center gap-2 border-b border-violet/40 bg-violet/15 px-4 py-1.5 text-center text-xs font-medium text-ink"
    >
      <FlaskConical className="h-3.5 w-3.5 shrink-0 text-violet" aria-hidden />
      <span>
        <strong className="tracking-wide">DEMO MODE</strong> — every device, metric, alert and finding on this page is synthetic. Nothing here describes a real network.
      </span>
    </div>
  );
}

export function PeriodSelector() {
  const { period, setPeriod } = usePeriod();
  return (
    <div role="group" aria-label="Time period" className="flex rounded-lg border border-line bg-raised/60 p-0.5">
      {PERIODS.map((p) => (
        <button
          key={p.key}
          type="button"
          aria-pressed={period.key === p.key}
          onClick={() => setPeriod(p.key)}
          className={cn(
            "rounded-md px-2 py-1 text-xs transition-colors",
            period.key === p.key ? "bg-accent/15 font-medium text-accent" : "text-muted hover:text-ink",
          )}
        >
          {p.key}
        </button>
      ))}
    </div>
  );
}

export function Layout({ children }: { children: ReactNode }) {
  const { me, demoMode, logout } = useAuth();
  const [open, setOpen] = useState(false);
  const location = useLocation();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const alerts = useQuery({ queryKey: ["alerts", "active"], queryFn: () => api.get<Alert[]>("/alerts", { state: "active" }), refetchInterval: 30_000 });
  const firing = alerts.data?.filter((a) => a.state === "firing").length ?? 0;

  useEffect(() => setOpen(false), [location.pathname]);
  // Alert transitions pushed over the live stream refresh the alert-related queries immediately.
  useEffect(
    () =>
      liveStore.onEvent((e) => {
        if (e.type === "alert") {
          void qc.invalidateQueries({ queryKey: ["alerts"] });
          void qc.invalidateQueries({ queryKey: ["overview"] });
        }
      }),
    [qc],
  );

  return (
    <div className="flex min-h-full flex-col">
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:left-3 focus:top-3 focus:z-50 focus:rounded-md focus:bg-accent focus:px-3 focus:py-1.5 focus:text-sm focus:text-slate-950">
        Skip to content
      </a>
      {demoMode && <DemoBanner />}
      <div className="flex min-h-0 flex-1">
        <aside
          className={cn(
            "fixed inset-y-0 left-0 z-30 w-60 shrink-0 border-r border-line bg-surface/95 backdrop-blur transition-transform lg:static lg:translate-x-0",
            open ? "translate-x-0" : "-translate-x-full",
          )}
        >
          <div className="flex h-14 items-center justify-between px-4">
            <Logo />
            <button type="button" className="rounded-md p-1 text-muted hover:bg-raised lg:hidden" onClick={() => setOpen(false)} aria-label="Close navigation">
              <X className="h-4 w-4" />
            </button>
          </div>
          <nav aria-label="Main" className="mt-2 space-y-0.5 px-2">
            {NAV.map(({ to, label, icon: Icon, end }) => (
              <NavLink
                key={to}
                to={to}
                end={end}
                className={({ isActive }) =>
                  cn(
                    "group flex items-center gap-3 rounded-lg px-3 py-2 text-sm transition-colors",
                    isActive ? "bg-accent/10 font-medium text-ink ring-1 ring-inset ring-accent/25" : "text-muted hover:bg-raised hover:text-ink",
                  )
                }
              >
                {({ isActive }) => (
                  <>
                    <Icon className={cn("h-4 w-4", isActive ? "text-accent" : "text-faint group-hover:text-muted")} aria-hidden />
                    <span className="flex-1">{label}</span>
                    {label === "Alerts" && firing > 0 && (
                      <span className="num rounded-full bg-crit/15 px-1.5 text-[11px] font-medium text-crit" aria-label={`${firing} firing`}>
                        {firing}
                      </span>
                    )}
                  </>
                )}
              </NavLink>
            ))}
          </nav>
          <div className="absolute inset-x-0 bottom-0 border-t border-line px-4 py-3 text-[11px] leading-relaxed text-faint">
            <span className="flex items-center gap-1.5">
              <Gauge className="h-3.5 w-3.5" aria-hidden /> Read-only observation.
            </span>
            No scanning, exploitation or remote control.
          </div>
        </aside>
        {open && <button type="button" aria-label="Close navigation" className="fixed inset-0 z-20 bg-black/50 lg:hidden" onClick={() => setOpen(false)} />}

        <div className="flex min-w-0 flex-1 flex-col">
          <header className="sticky top-0 z-10 flex h-14 items-center gap-3 border-b border-line bg-bg/80 px-4 backdrop-blur lg:px-6">
            <button type="button" className="rounded-md p-1.5 text-muted hover:bg-raised lg:hidden" onClick={() => setOpen(true)} aria-label="Open navigation">
              <Menu className="h-5 w-5" />
            </button>
            <div className="lg:hidden">
              <Logo withText={false} size={24} />
            </div>
            <div className="flex-1" />
            <LiveIndicator />
            <PeriodSelector />
            <button type="button" className="hidden rounded-md p-1.5 text-muted hover:bg-raised hover:text-ink sm:block" onClick={() => navigate("/settings")} aria-label="Settings">
              <Settings className="h-4 w-4" />
            </button>
            <DropdownMenu>
              <DropdownMenuTrigger className="flex items-center gap-2 rounded-full border border-line bg-raised/70 py-1 pl-1 pr-2.5 text-xs text-ink hover:border-faint" aria-label="Account menu">
                <span className="flex h-6 w-6 items-center justify-center rounded-full bg-violet/20 text-violet">
                  <UserRound className="h-3.5 w-3.5" aria-hidden />
                </span>
                <span className="hidden sm:inline">{me?.user.username}</span>
              </DropdownMenuTrigger>
              <DropdownMenuContent>
                <DropdownMenuLabel>
                  Signed in as {me?.user.username} · {me?.user.role}
                </DropdownMenuLabel>
                <DropdownMenuSeparator />
                <DropdownMenuItem onSelect={() => navigate("/settings/access")}>
                  <UserRound className="h-4 w-4" aria-hidden /> Account & access
                </DropdownMenuItem>
                <DropdownMenuItem onSelect={() => void logout()} danger>
                  <LogOut className="h-4 w-4" aria-hidden /> Sign out
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </header>
          <main id="main" className="mx-auto w-full max-w-[1500px] flex-1 animate-fade-in px-4 py-5 lg:px-6">
            {children}
          </main>
        </div>
      </div>
    </div>
  );
}
