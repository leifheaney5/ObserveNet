import { useQuery } from "@tanstack/react-query";
import { CircleSlash, FlaskConical, Info, Lightbulb, Scale } from "lucide-react";

import { Card, CardBody, CardHeader, EmptyState, ErrorState, Notice, PageHeader, Skeleton } from "@/components/ui/primitives";
import { Badge, DemoTag, Timestamp } from "@/components/ui/status";
import { TopologyCard } from "@/features/insights/Topology";
import { useAuth } from "@/hooks/useAuth";
import { api } from "@/lib/api";
import { formatDateTime } from "@/lib/format";

interface Insight {
  key: string;
  title: string;
  severity: "info" | "notice";
  summary: string;
  method: string;
  data: Record<string, unknown>;
}

interface InsightsOut {
  generated_at: string;
  insights: Insight[];
  not_computed: { key: string; reason: string }[];
}

const ISO_TS = /\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})/g;

/** The API writes UTC ISO timestamps into its sentences; show them in the browser's local zone. */
function localizeTimestamps(text: string): string {
  return text.replace(ISO_TS, (iso) => {
    const formatted = formatDateTime(iso);
    return formatted === "—" ? iso : formatted;
  });
}

const KEY_TITLES: Record<string, string> = {
  latency_trend: "Gateway latency vs. yesterday",
  rx_trend: "Host download traffic vs. yesterday",
  tx_trend: "Host upload traffic vs. yesterday",
  traffic_latency: "High traffic and elevated latency",
  new_devices: "Devices first seen in the last 24 h",
  peak_hour: "Busiest hour",
};

export function InsightsPage() {
  const { demoMode } = useAuth();
  const q = useQuery({ queryKey: ["insights"], queryFn: () => api.get<InsightsOut>("/insights"), refetchInterval: 120_000 });

  return (
    <div className="space-y-5">
      <PageHeader
        title="Insights"
        description="Plain comparisons of what changed on your network, each with the method and sample sizes it is based on."
        actions={
          q.data && (
            <span className="text-xs text-faint">
              Computed <Timestamp ts={q.data.generated_at} />
            </span>
          )
        }
      />
      <Notice tone="info" icon={<Scale className="h-4 w-4" />} title="What an insight is — and is not">
        Insights are simple comparisons and co-occurrence counts over stored measurements. They are{" "}
        <strong className="font-medium text-ink">not an AI risk score</strong>, and a correlation between two series is{" "}
        <strong className="font-medium text-ink">not proof that one caused the other</strong>. When there is not enough data, the insight is
        listed under “Not computed” with the reason instead of being estimated.
      </Notice>

      <section aria-label="Insights">
        {q.isLoading ? (
          <div className="grid gap-4 lg:grid-cols-2">
            <Skeleton className="h-44" />
            <Skeleton className="h-44" />
          </div>
        ) : q.isError ? (
          <Card>
            <ErrorState error={q.error} retry={() => void q.refetch()} />
          </Card>
        ) : q.data?.insights.length ? (
          <ul className="grid gap-4 lg:grid-cols-2">
            {q.data.insights.map((i) => (
              <li key={i.key}>
                <Card className="h-full">
                  <CardHeader
                    icon={<Lightbulb className="h-4 w-4" />}
                    title={i.title}
                    actions={
                      <>
                        {demoMode && <DemoTag />}
                        <Badge tone={i.severity === "notice" ? "warn" : "info"} icon={Info}>
                          {i.severity === "notice" ? "Worth a look" : "Informational"}
                        </Badge>
                      </>
                    }
                  />
                  <CardBody>
                    <p className="text-sm leading-relaxed text-ink">{localizeTimestamps(i.summary)}</p>
                    <div className="mt-3 rounded-lg border border-line bg-bg/50 px-3 py-2.5">
                      <p className="flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wider text-faint">
                        <FlaskConical className="h-3 w-3" aria-hidden /> How this was computed
                      </p>
                      <p className="mt-1 text-xs leading-relaxed text-muted">{i.method}</p>
                    </div>
                  </CardBody>
                </Card>
              </li>
            ))}
          </ul>
        ) : (
          <Card>
            <EmptyState icon={<Lightbulb className="h-5 w-5" />} title="No insights yet">
              Nothing could be computed from the data collected so far. The reasons are listed below; insights appear as history accumulates.
            </EmptyState>
          </Card>
        )}
      </section>

      {q.data && q.data.not_computed.length > 0 && (
        <Card>
          <CardHeader
            icon={<CircleSlash className="h-4 w-4" />}
            title="Not computed"
            subtitle="Insufficient data is reported, not hidden. Nothing here was estimated or filled in."
          />
          <CardBody>
            <ul className="divide-y divide-line/60">
              {q.data.not_computed.map((n) => (
                <li key={n.key} className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5 py-2 text-xs">
                  <span className="w-64 shrink-0 font-medium text-ink">{KEY_TITLES[n.key] ?? n.key}</span>
                  <span className="text-muted">{n.reason}</span>
                </li>
              ))}
            </ul>
          </CardBody>
        </Card>
      )}

      <TopologyCard />
    </div>
  );
}
