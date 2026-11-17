import { useQuery } from "@tanstack/react-query";
import { CheckCircle2, CircleSlash, Globe, Radio, Timer, Unplug, Wifi } from "lucide-react";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { SERIES_COLORS, TimeSeriesChart } from "@/components/charts/TimeSeriesChart";
import { Button, Card, CardBody, CardHeader, EmptyState, ErrorState, Notice, PageHeader, Skeleton, Table, Td, Th } from "@/components/ui/primitives";
import { Badge, DemoTag, StateBadge, Timestamp, type Tone } from "@/components/ui/status";
import { useAuth } from "@/hooks/useAuth";
import { useSeriesHistory } from "@/hooks/useHistory";
import { usePeriod } from "@/hooks/usePeriod";
import { api, type Connectivity, type SeriesMeta, type TargetSummary } from "@/lib/api";
import { cn } from "@/lib/cn";
import { formatDateTime, formatDuration, formatMs, formatPct, titleCase } from "@/lib/format";

const INTERNET: Record<string, { tone: "good" | "warn" | "crit" | "info"; title: string; icon: typeof Globe }> = {
  reachable: { tone: "good", title: "Internet reachable", icon: CheckCircle2 },
  degraded: { tone: "warn", title: "Internet degraded or a check is failing", icon: Radio },
  unreachable: { tone: "crit", title: "Internet unreachable", icon: Unplug },
  unknown: { tone: "info", title: "Internet status unknown", icon: CircleSlash },
};

const TARGET_STATE: Record<string, { tone: Tone; label: string }> = {
  ok: { tone: "good", label: "Responding" },
  lossy: { tone: "warn", label: "Lossy" },
  not_responding: { tone: "serious", label: "Not responding" },
  no_data: { tone: "neutral", label: "No data" },
  disabled: { tone: "neutral", label: "Disabled" },
};

const lossFromDelivery = (v: number) => (1 - v) * 100;

function TargetCharts({ summary, sourceId }: { summary: TargetSummary; sourceId: number | null }) {
  const { period } = usePeriod();
  const { demoMode } = useAuth();
  const label = `target:${summary.target.id}`;
  const latency = useSeriesHistory({ metric: "probe.latency_ms", sourceId, label }, period);
  const loss = useSeriesHistory({ metric: "probe.success", sourceId, label, transform: lossFromDelivery }, period);
  const jitter = useSeriesHistory({ metric: "probe.jitter_ms", sourceId, label }, period);
  const span = period.seconds;
  return (
    <div className="grid gap-4 xl:grid-cols-3">
      <Card>
        <CardHeader icon={<Timer className="h-4 w-4" />} title="Latency" subtitle={`${summary.target.name} · successful probes only`} actions={demoMode ? <DemoTag /> : undefined} />
        <CardBody>
          {latency.isLoading ? (
            <Skeleton className="h-[200px]" />
          ) : (
            <TimeSeriesChart
              unit="ms"
              height={200}
              spanSeconds={span}
              ariaLabel={`Latency to ${summary.target.name} over the last ${period.label}.`}
              series={[{ key: "lat", name: "Round-trip time", color: SERIES_COLORS[2], points: latency.points }]}
              emptyHint="No successful probes in this period."
            />
          )}
        </CardBody>
      </Card>
      <Card>
        <CardHeader title="Packet loss" subtitle={period.live ? "Per probe: 0% answered, 100% lost" : `Share of unanswered probes per ${formatDuration(period.bucket)} bucket`} />
        <CardBody>
          {loss.isLoading ? (
            <Skeleton className="h-[200px]" />
          ) : (
            <TimeSeriesChart
              unit="percent"
              height={200}
              yMax={100}
              spanSeconds={span}
              ariaLabel={`Packet loss to ${summary.target.name} over the last ${period.label}.`}
              series={[{ key: "loss", name: "Loss", color: SERIES_COLORS[3], points: loss.points }]}
              emptyHint="No probes recorded in this period, so loss is unknown — not 0%."
            />
          )}
        </CardBody>
      </Card>
      <Card>
        <CardHeader title="Jitter" subtitle="Absolute change between consecutive successful probes" />
        <CardBody>
          {jitter.isLoading ? (
            <Skeleton className="h-[200px]" />
          ) : (
            <TimeSeriesChart
              unit="ms"
              height={200}
              spanSeconds={span}
              ariaLabel={`Jitter to ${summary.target.name} over the last ${period.label}.`}
              series={[{ key: "jit", name: "Jitter", color: SERIES_COLORS[1], points: jitter.points }]}
              emptyHint="Jitter needs two consecutive successful probes."
            />
          )}
        </CardBody>
      </Card>
    </div>
  );
}

export function ConnectivityPage() {
  const summary = useQuery({ queryKey: ["connectivity"], queryFn: () => api.get<Connectivity>("/connectivity/summary", { window_s: 60 }), refetchInterval: 5000 });
  const series = useQuery({ queryKey: ["series", "probe.success"], queryFn: () => api.get<SeriesMeta[]>("/metrics/series", { metric: "probe.success" }), refetchInterval: 60_000 });
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const sourceByLabel = useMemo(() => new Map((series.data ?? []).map((s) => [s.label, s.source_id])), [series.data]);

  if (summary.isError && !summary.data) return <ErrorState error={summary.error} retry={() => void summary.refetch()} />;
  const data = summary.data;
  const inet = data ? (INTERNET[(data.internet as { status: string }).status] ?? INTERNET.unknown!) : null;
  const selected = data?.targets.find((t) => t.target.id === selectedId) ?? data?.targets.find((t) => t.target.scope === "gateway") ?? data?.targets[0];
  const outages = (data?.outages ?? []) as { id: number; kind: string; started_at: string; ended_at: string | null; duration_s: number; evidence: { reason?: string } }[];

  return (
    <div className="space-y-5">
      <PageHeader
        title="Connectivity"
        description="Low-rate health probes run from your collector. Status combines several independent signals — one unanswered ping is never treated as an outage."
        actions={
          <Link to="/settings/probes">
            <Button size="sm">Probe targets</Button>
          </Link>
        }
      />

      {data && inet ? (
        <Notice tone={inet.tone} title={inet.title} icon={<inet.icon className="h-4 w-4" />}>
          {(data.internet as { reason: string }).reason}{" "}
          <span className="text-faint">
            ({(data.internet as { signals_ok: number }).signals_ok} of {(data.internet as { signals_total: number }).signals_total} external signals OK · evaluated{" "}
            {formatDateTime(data.generated_at)})
          </span>
        </Notice>
      ) : (
        <Skeleton className="h-16" />
      )}

      <Card>
        <CardHeader
          icon={<Globe className="h-4 w-4" />}
          title="Probe targets"
          subtitle="Statistics over an explicit rolling window. Sample size is shown because 0% loss over 3 probes means little."
        />
        <CardBody className="px-2">
          {!data ? (
            <Skeleton className="mx-3 h-40" />
          ) : data.targets.length ? (
            <Table>
              <thead>
                <tr>
                  <Th>Target</Th>
                  <Th>State</Th>
                  <Th className="text-right">Loss</Th>
                  <Th className="text-right">Avg</Th>
                  <Th className="text-right">p95</Th>
                  <Th className="text-right">Jitter</Th>
                  <Th className="text-right">Samples / window</Th>
                  <Th>Last result</Th>
                </tr>
              </thead>
              <tbody>
                {data.targets.map((t) => {
                  const st = TARGET_STATE[t.state] ?? { tone: "neutral" as Tone, label: titleCase(t.state) };
                  const active = selected?.target.id === t.target.id;
                  return (
                    <tr key={t.target.id} className={cn("cursor-pointer hover:bg-raised/40", active && "bg-accent/[0.06]")} onClick={() => setSelectedId(t.target.id)}>
                      <Td>
                        <button type="button" className="text-left" aria-pressed={active} onClick={() => setSelectedId(t.target.id)}>
                          <span className="font-medium text-ink">{t.target.name}</span>
                          <span className="block font-mono text-[11px] text-faint">
                            {t.target.kind === "gateway_icmp" ? "icmp" : t.target.kind} · {t.target.host === "auto" ? (t.resolved_host ?? "default gateway") : t.target.host}
                            {t.target.port ? `:${t.target.port}` : ""} · every {t.target.interval_s} s
                          </span>
                        </button>
                      </Td>
                      <Td>
                        <Badge tone={st.tone}>{st.label}</Badge>
                        {t.note && <p className="mt-1 max-w-[280px] text-[11px] leading-snug text-faint">{t.note}</p>}
                      </Td>
                      <Td className="num text-right">{formatPct(t.loss_pct)}</Td>
                      <Td className="num text-right">{formatMs(t.latency_avg_ms)}</Td>
                      <Td className="num text-right">{formatMs(t.latency_p95_ms)}</Td>
                      <Td className="num text-right">{formatMs(t.jitter_ms)}</Td>
                      <Td className="num text-right text-muted">
                        {t.sample_size} / {t.window_s} s
                      </Td>
                      <Td className="text-xs text-muted">
                        <Timestamp ts={t.last_ts} />
                        {t.last_method && <span className="text-faint"> · {t.last_method.replace("_", " ")}</span>}
                        {t.last_error && <span className="block text-[11px] text-faint">last error: {t.last_error}</span>}
                      </Td>
                    </tr>
                  );
                })}
              </tbody>
            </Table>
          ) : (
            <EmptyState title="No probe targets configured">Add a gateway or external target in Settings to measure latency and reachability.</EmptyState>
          )}
        </CardBody>
      </Card>

      {selected && <TargetCharts key={selected.target.id} summary={selected} sourceId={sourceByLabel.get(`target:${selected.target.id}`) ?? null} />}

      <div className="grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader icon={<Unplug className="h-4 w-4" />} title="Outages" subtitle="Recorded only when every independent external signal fails several times in a row." />
          <CardBody className={outages.length ? "px-2" : undefined}>
            {outages.length ? (
              <Table>
                <thead>
                  <tr>
                    <Th>Started</Th>
                    <Th>Duration</Th>
                    <Th>Status</Th>
                    <Th>Evidence</Th>
                  </tr>
                </thead>
                <tbody>
                  {outages.map((o) => (
                    <tr key={o.id}>
                      <Td className="num text-xs">{formatDateTime(o.started_at)}</Td>
                      <Td className="num">{formatDuration(o.duration_s)}</Td>
                      <Td>{o.ended_at ? <Badge tone="good">Recovered</Badge> : <Badge tone="crit">Ongoing</Badge>}</Td>
                      <Td className="text-xs text-muted">{o.evidence.reason ?? "—"}</Td>
                    </tr>
                  ))}
                </tbody>
              </Table>
            ) : (
              <EmptyState icon={<CheckCircle2 className="h-5 w-5" />} title="No outages recorded" className="py-6">
                This covers only the time a collector was running and reporting.
              </EmptyState>
            )}
          </CardBody>
        </Card>
        <Card>
          <CardHeader icon={<Wifi className="h-4 w-4" />} title="Wi-Fi signal" actions={<StateBadge state="unsupported" />} />
          <CardBody>
            <EmptyState icon={<CircleSlash className="h-5 w-5" />} title="Not available" className="py-6">
              {(data?.wifi as { detail?: string } | undefined)?.detail ?? "Wi-Fi client and signal data needs an access-point or controller provider."}
            </EmptyState>
          </CardBody>
        </Card>
      </div>
    </div>
  );
}
