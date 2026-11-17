import { useQuery } from "@tanstack/react-query";
import { Activity, ArrowDownToLine, ArrowUpFromLine, CircleSlash, Gauge, Info, Router, TrendingDown, TrendingUp } from "lucide-react";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { SERIES_COLORS, TimeSeriesChart } from "@/components/charts/TimeSeriesChart";
import { Button, Card, CardBody, CardHeader, EmptyState, NativeSelect, PageHeader, Skeleton, Table, Td, Th } from "@/components/ui/primitives";
import { DemoTag, Provenance, StateBadge } from "@/components/ui/status";
import { useAuth } from "@/hooks/useAuth";
import { useSeriesHistory } from "@/hooks/useHistory";
import { useLiveLatest } from "@/hooks/useLive";
import { usePeriod, PERIODS } from "@/hooks/usePeriod";
import { usePeriodComparison } from "@/hooks/usePeriodStats";
import { api, type Capabilities, type SeriesMeta, type Source } from "@/lib/api";
import { formatBps, formatDuration, formatPct } from "@/lib/format";
import { seriesKey } from "@/lib/liveStore";

interface TopDevices {
  supported: boolean;
  guidance?: string;
  window_s: number;
  coverage?: string;
  devices: { mac: string; name: string; asset_id?: number; rx_bps: number; tx_bps: number; samples: number; quality: string; source?: string }[];
}

function StatTile({ label, value, sub, icon }: { label: string; value: string; sub?: React.ReactNode; icon?: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-line bg-raised/40 px-3.5 py-2.5">
      <p className="flex items-center gap-1.5 text-[11px] uppercase tracking-wider text-faint">
        {icon}
        {label}
      </p>
      <p className="num mt-1 text-lg font-semibold text-ink">{value}</p>
      {sub && <p className="num mt-0.5 text-[11px] text-muted">{sub}</p>}
    </div>
  );
}

function Change({ pct, prev }: { pct: number | null; prev: number | null }) {
  if (pct === null || prev === null) return <>no data for the previous period</>;
  const Icon = pct >= 0 ? TrendingUp : TrendingDown;
  return (
    <span className="inline-flex items-center gap-1">
      <Icon className="h-3 w-3" aria-hidden />
      {pct >= 0 ? "+" : ""}
      {formatPct(pct, 0)} vs previous ({formatBps(prev)})
    </span>
  );
}

function ThroughputPanel({
  title,
  subtitle,
  sourceId,
  label,
  rxMetric,
  txMetric,
  names,
  demo,
}: {
  title: string;
  subtitle: React.ReactNode;
  sourceId: number;
  label: string;
  rxMetric: string;
  txMetric: string;
  names: [string, string];
  demo: boolean;
}) {
  const { period } = usePeriod();
  const rx = useSeriesHistory({ metric: rxMetric, sourceId, label }, period);
  const tx = useSeriesHistory({ metric: txMetric, sourceId, label }, period);
  const liveRx = useLiveLatest(seriesKey(sourceId, rxMetric, label));
  const liveTx = useLiveLatest(seriesKey(sourceId, txMetric, label));
  const cmpRx = usePeriodComparison(rxMetric, sourceId, label, period);
  const cmpTx = usePeriodComparison(txMetric, sourceId, label, period);
  const fresh = (p: { t: number } | undefined) => !!p && Date.now() - p.t < 10_000;

  return (
    <Card>
      <CardHeader icon={<Activity className="h-4 w-4" />} title={title} subtitle={subtitle} actions={demo ? <DemoTag /> : undefined} />
      <CardBody className="space-y-4">
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          <StatTile
            label={`${names[0]} now`}
            icon={<ArrowDownToLine className="h-3 w-3" aria-hidden />}
            value={fresh(liveRx) ? formatBps(liveRx!.v) : "—"}
            sub={fresh(liveRx) ? "1 s sample" : "no live sample"}
          />
          <StatTile
            label={`${names[1]} now`}
            icon={<ArrowUpFromLine className="h-3 w-3" aria-hidden />}
            value={fresh(liveTx) ? formatBps(liveTx!.v) : "—"}
            sub={fresh(liveTx) ? "1 s sample" : "no live sample"}
          />
          <StatTile
            label={`${names[0]} avg · ${period.label}`}
            value={formatBps(cmpRx.data?.current.avg)}
            sub={cmpRx.data ? <Change pct={cmpRx.data.changePct} prev={cmpRx.data.previous.avg} /> : "…"}
          />
          <StatTile
            label={`${names[1]} avg · ${period.label}`}
            value={formatBps(cmpTx.data?.current.avg)}
            sub={cmpTx.data ? <Change pct={cmpTx.data.changePct} prev={cmpTx.data.previous.avg} /> : "…"}
          />
        </div>
        {rx.isLoading ? (
          <Skeleton className="h-[260px]" />
        ) : (
          <TimeSeriesChart
            unit="bit/s"
            height={260}
            spanSeconds={period.seconds}
            ariaLabel={`${title} over the last ${period.label}.`}
            series={[
              { key: "rx", name: names[0], color: SERIES_COLORS[0], points: rx.points },
              { key: "tx", name: names[1], color: SERIES_COLORS[1], points: tx.points },
            ]}
          />
        )}
        <div className="flex flex-wrap items-center justify-between gap-2 text-[11px] text-faint">
          <span>
            Peak in period: {names[0].toLowerCase()} {formatBps(cmpRx.data?.current.peak)} · {names[1].toLowerCase()} {formatBps(cmpTx.data?.current.peak)} ·{" "}
            {rx.resolution === "raw" ? "1 s samples" : rx.resolution === "1m" ? "1-minute rollups" : rx.resolution === "1h" ? "hourly rollups" : ""}
            {rx.meta ? ` · sampled every ${rx.meta.interval_s} s` : ""}
          </span>
          {rx.meta && <Provenance ts={rx.meta.last_ts} source={rx.meta.coverage ?? undefined} />}
        </div>
        {rx.note && <p className="text-[11px] text-warn">{rx.note}</p>}
      </CardBody>
    </Card>
  );
}

const GLOSSARY: { term: string; meaning: string; capability: string | null }[] = [
  { term: "WAN throughput", meaning: "Bits per second crossing the gateway's WAN interface right now. Needs counters from the gateway.", capability: "wan_throughput" },
  { term: "Host NIC traffic", meaning: "Traffic through one host's own network interface (a host running an agent). Not network-wide.", capability: "host_nic_throughput" },
  { term: "Per-device traffic", meaning: "Traffic attributed to each device. Only the gateway, a managed switch/AP, flow export or per-device agents can provide it.", capability: "per_device_traffic" },
  { term: "LAN traffic", meaning: "Device-to-device traffic inside the network. Invisible to the gateway's WAN counters and to other hosts; not measured.", capability: null },
  { term: "Wi-Fi PHY link rate", meaning: "The negotiated radio rate of a Wi-Fi client. A ceiling, not usage; needs an access-point provider.", capability: "wifi_clients_signal" },
  { term: "Tested capacity", meaning: "Result of an occasional, opt-in speed test. A separate metric from utilization.", capability: "capacity_test" },
  { term: "ISP advertised speed", meaning: "What your plan promises. ObserveNet does not know it and never infers it.", capability: null },
];

export function TrafficPage() {
  const { demoMode } = useAuth();
  const series = useQuery({ queryKey: ["series", "throughput"], queryFn: () => api.get<SeriesMeta[]>("/metrics/series"), refetchInterval: 60_000 });
  const sources = useQuery({ queryKey: ["sources"], queryFn: () => api.get<Source[]>("/sources"), refetchInterval: 30_000 });
  const caps = useQuery({ queryKey: ["capabilities"], queryFn: () => api.get<Capabilities>("/capabilities"), refetchInterval: 60_000 });
  const top = useQuery({ queryKey: ["top-devices", 300], queryFn: () => api.get<TopDevices>("/traffic/top-devices", { window_s: 300 }), refetchInterval: 10_000 });
  const [selected, setSelected] = useState<string | null>(null);

  const sourceName = useMemo(() => new Map((sources.data ?? []).map((s) => [s.id, s.name])), [sources.data]);
  const nics = useMemo(
    () =>
      (series.data ?? [])
        .filter((s) => s.metric === "net.rx_bps")
        .sort((a, b) => (b.last_ts ?? "").localeCompare(a.last_ts ?? "") || a.label.localeCompare(b.label)),
    [series.data],
  );
  const wan = (series.data ?? []).find((s) => s.metric === "wan.rx_bps");
  const speed = (series.data ?? []).filter((s) => s.metric === "speedtest.download_bps");
  const preferred = nics.find((s) => !/^(docker|br-|veth|lo)/.test(s.label)) ?? nics[0];
  const current = nics.find((s) => `${s.source_id}|${s.label}` === selected) ?? preferred;
  const cap = (key: string) => caps.data?.network.find((c) => c.key === key);
  const wanCap = cap("wan_throughput");
  const speedPeriod = PERIODS[4]!;
  const speedHistory = useSeriesHistory(speed[0] ? { metric: "speedtest.download_bps", sourceId: speed[0].source_id, label: speed[0].label } : null, speedPeriod);

  return (
    <div className="space-y-5">
      <PageHeader
        title="Traffic"
        description="Measured throughput by observation point. Each chart says where it was measured and what it does not cover."
      />

      {wan ? (
        <ThroughputPanel
          title="WAN throughput"
          subtitle={<>Counted at the gateway WAN interface ({wan.label}) by {sourceName.get(wan.source_id) ?? "the gateway source"}. Utilization, not capacity.</>}
          sourceId={wan.source_id}
          label={wan.label}
          rxMetric="wan.rx_bps"
          txMetric="wan.tx_bps"
          names={["Download", "Upload"]}
          demo={demoMode}
        />
      ) : (
        <Card>
          <CardHeader icon={<Router className="h-4 w-4" />} title="WAN throughput" actions={<StateBadge state={wanCap?.status ?? "unsupported"} />} />
          <CardBody>
            <EmptyState
              icon={<CircleSlash className="h-5 w-5" />}
              title="No gateway source is connected"
              action={
                <Link to="/settings/sources">
                  <Button size="sm">Data sources</Button>
                </Link>
              }
            >
              {wanCap?.guidance ??
                "WAN throughput needs counters from the gateway's WAN interface. Until a gateway source reports them, this stays empty instead of showing host numbers under a WAN label."}
            </EmptyState>
          </CardBody>
        </Card>
      )}

      {series.isLoading ? (
        <Skeleton className="h-96" />
      ) : current ? (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            <label htmlFor="nic" className="text-xs text-muted">
              Host interface
            </label>
            <NativeSelect id="nic" className="w-auto min-w-[220px]" value={`${current.source_id}|${current.label}`} onChange={(e) => setSelected(e.target.value)}>
              {nics.map((s) => (
                <option key={s.id} value={`${s.source_id}|${s.label}`}>
                  {sourceName.get(s.source_id) ?? `source ${s.source_id}`} · {s.label}
                </option>
              ))}
            </NativeSelect>
          </div>
          <ThroughputPanel
            key={`${current.source_id}|${current.label}`}
            title={`Host NIC throughput — ${sourceName.get(current.source_id) ?? "host"} · ${current.label}`}
            subtitle="Derived from the interface's cumulative byte counters once per second: 8 × Δbytes ÷ Δt (monotonic clock). Covers this host's own traffic only."
            sourceId={current.source_id}
            label={current.label}
            rxMetric="net.rx_bps"
            txMetric="net.tx_bps"
            names={["Receive", "Transmit"]}
            demo={demoMode}
          />
        </div>
      ) : (
        <Card>
          <CardBody>
            <EmptyState title="No interface counters yet">
              Throughput appears once an agent reports its NIC counters. Nothing is shown until something is measured.
            </EmptyState>
          </CardBody>
        </Card>
      )}

      <div className="grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader
            icon={<Router className="h-4 w-4" />}
            title="Top devices by traffic"
            subtitle={top.data?.supported ? `Average over the last ${formatDuration(top.data.window_s)}. ${top.data.coverage ?? ""}` : undefined}
            actions={top.data?.supported ? demoMode ? <DemoTag /> : undefined : <StateBadge state="unsupported" />}
          />
          <CardBody className={top.data?.supported ? "px-2" : undefined}>
            {top.isLoading ? (
              <Skeleton className="h-40" />
            ) : top.data?.supported ? (
              <Table>
                <thead>
                  <tr>
                    <Th>Device</Th>
                    <Th className="text-right">Download</Th>
                    <Th className="text-right">Upload</Th>
                    <Th>Source</Th>
                  </tr>
                </thead>
                <tbody>
                  {top.data.devices.map((d) => (
                    <tr key={d.mac}>
                      <Td>
                        {d.asset_id ? (
                          <Link to={`/devices/${d.asset_id}`} className="text-ink hover:text-accent">
                            {d.name}
                          </Link>
                        ) : (
                          <span className="font-mono text-xs text-muted">{d.mac}</span>
                        )}
                      </Td>
                      <Td className="num text-right">{formatBps(d.rx_bps)}</Td>
                      <Td className="num text-right">{formatBps(d.tx_bps)}</Td>
                      <Td className="text-xs text-muted">{d.source ?? "—"}</Td>
                    </tr>
                  ))}
                </tbody>
              </Table>
            ) : (
              <EmptyState icon={<CircleSlash className="h-5 w-5" />} title="Per-device traffic requires supported gateway integration">
                {top.data?.guidance}
              </EmptyState>
            )}
          </CardBody>
        </Card>

        <Card>
          <CardHeader
            icon={<Gauge className="h-4 w-4" />}
            title="Internet capacity tests"
            subtitle="Opt-in and separate from utilization. Off by default; never run continuously."
            actions={<StateBadge state={cap("capacity_test")?.status ?? "disabled"} />}
          />
          <CardBody>
            {speed[0] ? (
              <TimeSeriesChart
                unit="bit/s"
                spanSeconds={speedPeriod.seconds}
                ariaLabel="Capacity test results over the last 7 days."
                series={[{ key: "dl", name: `Tested download (${speed[0].label})`, color: SERIES_COLORS[3], points: speedHistory.points, fill: false }]}
                emptyHint="No capacity test has completed in the last 7 days."
              />
            ) : (
              <EmptyState icon={<CircleSlash className="h-5 w-5" />} title="No capacity tests have run">
                A capacity test downloads a bounded file to estimate what the line can carry. It is disabled unless you enable it in{" "}
                <Link to="/settings/scope" className="text-accent hover:underline">
                  Settings
                </Link>
                , runs at most every few hours, and is never confused with the live throughput above.
              </EmptyState>
            )}
          </CardBody>
        </Card>
      </div>

      <Card>
        <CardHeader icon={<Info className="h-4 w-4" />} title="What these numbers are — and are not" subtitle="Different metrics that are easy to conflate. ObserveNet keeps them separate." />
        <CardBody className="px-2">
          <Table>
            <thead>
              <tr>
                <Th>Metric</Th>
                <Th>Meaning</Th>
                <Th>Here</Th>
              </tr>
            </thead>
            <tbody>
              {GLOSSARY.map((g) => (
                <tr key={g.term}>
                  <Td className="whitespace-nowrap font-medium">{g.term}</Td>
                  <Td className="text-xs leading-relaxed text-muted">{g.meaning}</Td>
                  <Td>{g.capability ? <StateBadge state={cap(g.capability)?.status ?? "unknown"} /> : <StateBadge state="unsupported" />}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
        </CardBody>
      </Card>
    </div>
  );
}
