import { useQuery } from "@tanstack/react-query";
import { BellRing } from "lucide-react";
import { useSearchParams } from "react-router-dom";

import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/overlays";
import { Notice, PageHeader } from "@/components/ui/primitives";
import { ActiveAlerts, AlertHistory } from "@/features/alerts/AlertList";
import { AlertRules } from "@/features/alerts/AlertRules";
import { MaintenanceWindows } from "@/features/alerts/Maintenance";
import { Timeline } from "@/features/alerts/Timeline";
import { api, type Alert } from "@/lib/api";

const TABS = ["active", "history", "rules", "maintenance", "timeline"] as const;
type Tab = (typeof TABS)[number];

export function AlertsPage() {
  const [params, setParams] = useSearchParams();
  const requested = params.get("tab") as Tab | null;
  const tab: Tab = requested && TABS.includes(requested) ? requested : "active";
  const active = useQuery({ queryKey: ["alerts", "active"], queryFn: () => api.get<Alert[]>("/alerts", { state: "active" }), refetchInterval: 15_000 });
  const count = active.data?.length ?? 0;

  return (
    <div className="space-y-4">
      <PageHeader title="Alerts" description="Threshold and change alerts with their source timestamps, acknowledgment state and full history." />
      <Notice tone="info" icon={<BellRing className="h-4 w-4" />} title="How alerts behave">
        A threshold alert fires only after the condition has held for its <strong className="font-medium text-ink">debounce</strong> period, so
        one-second blips do not page you. It clears only after the value has stayed below the lower{" "}
        <strong className="font-medium text-ink">clear threshold</strong> (hysteresis), then cannot re-fire during its{" "}
        <strong className="font-medium text-ink">cooldown</strong>. Each condition has at most one active alert (deduplicated), and missing data
        is treated as unknown — it neither fires nor clears. Alerts are shown in-app only; a webhook is sent only if the operator configured one,
        and nothing is sent to third-party services by default.
      </Notice>

      <Tabs value={tab} onValueChange={(v) => setParams(v === "active" ? {} : { tab: v }, { replace: true })}>
        <TabsList label="Alert views">
          <TabsTrigger value="active">
            Active
            {count > 0 && (
              <span className="num rounded-full bg-crit/15 px-1.5 text-[11px] font-medium text-crit" aria-label={`${count} active`}>
                {count}
              </span>
            )}
          </TabsTrigger>
          <TabsTrigger value="history">History</TabsTrigger>
          <TabsTrigger value="rules">Rules & thresholds</TabsTrigger>
          <TabsTrigger value="maintenance">Maintenance windows</TabsTrigger>
          <TabsTrigger value="timeline">Timeline</TabsTrigger>
        </TabsList>
        <TabsContent value="active">{tab === "active" && <ActiveAlerts />}</TabsContent>
        <TabsContent value="history">{tab === "history" && <AlertHistory />}</TabsContent>
        <TabsContent value="rules">{tab === "rules" && <AlertRules />}</TabsContent>
        <TabsContent value="maintenance">{tab === "maintenance" && <MaintenanceWindows />}</TabsContent>
        <TabsContent value="timeline">{tab === "timeline" && <Timeline />}</TabsContent>
      </Tabs>
    </div>
  );
}
