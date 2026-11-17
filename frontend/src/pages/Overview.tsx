import { useQuery } from "@tanstack/react-query";
import { Activity, ArrowDownToLine, ArrowUpFromLine, Bell, CheckCircle2, CircleSlash, Gauge, MonitorSmartphone, Router, ServerCog, ShieldCheck, Timer, Unplug } from "lucide-react";
import { Link } from "react-router-dom";

import { SERIES_COLORS, TimeSeriesChart } from "@/components/charts/TimeSeriesChart";
import { KpiCard } from "@/components/KpiCard";
import { Button, Card, CardBody, CardHeader, EmptyState, ErrorState, Notice, PageHeader, Skeleton, Table, Td, Th } from "@/components/ui/primitives";
import { Badge, DemoTag, HealthBadge, PresenceBadge, SeverityBadge, Timestamp } from "@/components/ui/status";
import { useAuth } from "@/hooks/useAuth";
import { useSeriesHistory } from "@/hooks/useHistory";
import { useLiveLatest } from "@/hooks/useLive";
import { usePeriod } from "@/hooks/usePeriod";
import { api, type DevicePage, type Kpi, type Overview, type StatusOut } from "@/lib/api";
import { formatBps, formatMs, titleCase } from "@/lib/format";
import { seriesKey } from "@/lib/liveStore";

interface TopDevices {
  supported: boolean;
  guidance?: string;
  window_s: number;
  coverage?: string;
  devices: { mac: string; name: string; asset_id?: number; rx_bps: number; tx_bps: number; quality: string; source?: string }[];
}

function useLiveKpi(sourceId: number | null | undefined, metric: string, label: string | null | undefined) {
  const p = useLiveLatest(sourceId != null && label != null ? seriesKey(sourceId, metric, label) : null);
  return p ? { value: p.v, ts: new Date(p.t).toISOString() } : null;
}

const INTERNET: Record<string, { tone: "good" | "warn" | "crit" | "neutral"; label: string }> = {
  reachable: { tone: "good", label: "Internet reachable" },
  degraded: { tone: "warn", label: "Internet degraded" },
  unreachable: { tone: "crit", label: "Internet unreachable" },
  unknown: { tone: "neutral", label: "Internet status unknown" },
};

export function OverviewPage() {
  const { period } = usePeriod();
  const { demoMode } = useAuth();
  const overview = useQuery({ queryKey: ["overview"], queryFn: () => api.get<Overview>("/overview"), refetchInterval: 5000 });
  const status = useQuery({ queryKey: ["status"], queryFn: () => api.get<StatusOut>("/status"), refetchInterval: 15_000 });
  const devices = useQuery({ queryKey: ["devices", "overview"], queryFn: () => api.get<DevicePage>("/devices", { limit: 8 }), refetchInterval: 30_000 });
  const top = useQuery({ queryKey: ["top-devices"], queryFn: () => api.get<TopDevices>("/traffic/top-devices"), refetchInterval: 10_000 });

  const o = overview.data;
  const kpi = (key: string): Kpi | undefined => o?.kpis.find((k) => k.key === key);
  const primary = (o?.primary ?? {}) as {
    source_id?: number | null;
    source_name?: string | null;
    interface?: string | null;
    gateway_target_id?: number | null;
    wan_source_id?: number | null;
    wan_label?: string | null;
  };
  const hasWan = primary.wan_source_id != null;
  const tSource = hasWan ? primary.wan_source_id : primary.source_id;
  const tLabel = hasWan ? primary.wan_label : primary.interface;
  const rxMetric = hasWan ? "wan.rx_bps" : "net.rx_bps";
  const txMetric = hasWan ? "wan.tx_bps" : "net.tx_bps";

  const rx = useSeriesHistory({ metric: rxMetric, sourceId: tSource, label: tLabel }, period);
  const tx = useSeriesHistory({ metric: txMetric, sourceId: tSource, label: tLabel }, period);
  const gwLabel = primary.gateway_target_id != null ? `target:${primary.gateway_target_id}` : null;
  const latency = useSeriesHistory({ metric: "probe.latency_ms", sourceId: primary.source_id, label: gwLabel }, period);

  const liveWanRx = useLiveKpi(primary.wan_source_id, "wan.rx_bps", primary.wan_label);
  const liveWanTx = useLiveKpi(primary.wan_source_id, "wan.tx_bps", primary.wan_label);
  const liveRx = useLiveKpi(tSource, rxMetric, tLabel);
  const liveTx = useLiveKpi(tSource, txMetric, tLabel);

  if (overview.isError && !o) return <ErrorState error={overview.error} retry={() => void overview.refetch()} />;

  const devKpi = kpi("devices");
  const devDetail = (devKpi?.detail ?? {}) as { present?: number; not_seen_recently?: number; unknown?: number };
  const findKpi = kpi("open_findings");
  const bySev = ((findKpi?.detail as { by_severity?: Record<string, number> } | undefined)?.by_severity ?? {}) as Record<string, number>;
  const latDetail = (kpi("gateway_latency")?.detail ?? {}) as { window_s?: number; sample_size?: number; jitter_ms?: number | null };
  const inet = INTERNET[(o?.internet as { status?: string } | undefined)?.status ?? "unknown"] ?? INTERNET.unknown!;
  const nothingYet = o && !primary.source_id && !devKpi?.value;

  return (
    <div className="space-y-5">
      <PageHeader
        title="Overview"
        description="What is measured right now, where each number comes from, and what is not observable yet."
        actions={
          o && (
            <Link to="/connectivity" className="rounded-full focus-visible:outline-none">
              <Badge tone={inet.tone} icon={inet.tone === "good" ? CheckCircle2 : inet.tone === "neutral" ? CircleSlash : Unplug} title={(o.internet as { reason?: string }).reason}>
                {inet.label}
              </Badge>
            </Link>
          )
        }
      />

      {nothingYet && (
        <Notice tone="info" title="No collector has reported yet" icon={<ServerCog className="h-4 w-4" />}>
          This database is empty because nothing has been measured — ObserveNet never shows placeholder numbers in real mode. Start the bundled Linux
          collector or add an agent in{" "}
          <Link to="/settings/sources" className="text-accent underline-offset-2 hover:underline">
            Settings → Data sources
          </Link>
          . Cards fill in as soon as the first samples arrive.
        </Notice>
      )}

      <section aria-label="Key indicators" className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <KpiCard
          kpi={devKpi}
          icon={<MonitorSmartphone className="h-4 w-4" />}
          to="/devices"
          demo={demoMode}
          footer={
            devKpi?.value != null && (
              <p className="num mb-0.5 text-[11px] text-muted">
                <span className="text-good">{devDetail.present ?? 0} present</span> · {devDetail.not_seen_recently ?? 0} not seen recently
                {devDetail.unknown ? ` · ${devDetail.unknown} unknown` : ""}
              </p>
            )
          }
        />
        <KpiCard kpi={kpi("wan_download")} icon={<ArrowDownToLine className="h-4 w-4" />} to="/traffic" live={liveWanRx} demo={demoMode} />
        <KpiCard kpi={kpi("wan_upload")} icon={<ArrowUpFromLine className="h-4 w-4" />} to="/traffic" live={liveWanTx} demo={demoMode} />
        <KpiCard
          kpi={kpi("gateway_latency")}
          icon={<Timer className="h-4 w-4" />}
          to="/connectivity"
          demo={demoMode}
          footer={
            kpi("gateway_latency")?.value != null && (
              <p className="num mb-0.5 text-[11px] text-muted">
                avg of {latDetail.sample_size} probes / {latDetail.window_s} s{latDetail.jitter_ms != null ? ` · jitter ${formatMs(latDetail.jitter_ms)}` : ""}
              </p>
            )
          }
        />
        <KpiCard
          kpi={kpi("packet_loss")}
          icon={<Gauge className="h-4 w-4" />}
          to="/connectivity"
          demo={demoMode}
          footer={
            kpi("packet_loss")?.value != null && (
              <p className="num mb-0.5 text-[11px] text-muted">
                {latDetail.sample_size} probes / {latDetail.window_s} s window
              </p>
            )
          }
        />
        <KpiCard
          kpi={findKpi}
          icon={<ShieldCheck className="h-4 w-4" />}
          to="/security"
          demo={demoMode}
          footer={
            findKpi?.value != null && (
              <p className="num mb-0.5 text-[11px] text-muted">
                {["high", "medium", "low", "info"].filter((s) => bySev[s]).map((s) => `${bySev[s]} ${s}`).join(" · ") || "none open"}
              </p>
            )
          }
        />
      </section>

      <section className="grid gap-4 xl:grid-cols-3">
        <Card className="xl:col-span-2">
          <CardHeader
            icon={<Activity className="h-4 w-4" />}
            title={hasWan ? "WAN throughput" : "Host NIC throughput"}
            subtitle={
              tSource == null ? (
                "Waiting for a collector to report interface counters."
              ) : hasWan ? (
                <>Measured at the gateway WAN interface ({primary.wan_label}). Utilization, not link capacity.</>
              ) : (
                <>
                  Traffic through <strong className="font-medium text-ink">{primary.source_name}</strong> · {primary.interface} only — this host's own
                  interface, <strong className="font-medium text-ink">not</strong> whole-network or WAN traffic.
                </>
              )
            }
            actions={
              <div className="flex items-center gap-4 text-right">
                {demoMode && <DemoTag />}
                <div>
                  <p className="text-[10px] uppercase tracking-wider text-faint">Receive</p>
                  <p className="num text-sm font-semibold text-ink" data-testid="live-rx">
                    {liveRx ? formatBps(liveRx.value) : "—"}
                  </p>
                </div>
                <div>
                  <p className="text-[10px] uppercase tracking-wider text-faint">Transmit</p>
                  <p className="num text-sm font-semibold text-ink">{liveTx ? formatBps(liveTx.value) : "—"}</p>
                </div>
              </div>
            }
          />
          <CardBody>
            {rx.isLoading ? (
              <Skeleton className="h-[220px] w-full" />
            ) : (
              <TimeSeriesChart
                unit="bit/s"
                spanSeconds={period.seconds}
                ariaLabel={`${hasWan ? "WAN" : "Host NIC"} throughput over the last ${period.label}.`}
                series={[
                  { key: "rx", name: hasWan ? "Download" : "Receive", color: SERIES_COLORS[0], points: rx.points },
                  { key: "tx", name: hasWan ? "Upload" : "Transmit", color: SERIES_COLORS[1], points: tx.points },
                ]}
                emptyHint={tSource == null ? "No throughput has been measured yet. Start a collector to see live data." : "No samples in this period. Gaps mean nothing was measured — they are not zeros."}
              />
            )}
            <p className="mt-2 text-[11px] text-faint">
              {period.live ? "1 s samples, appended live." : `Averaged per ${period.bucket >= 60 ? `${period.bucket / 60} min` : `${period.bucket} s`} bucket from stored rollups.`} Speed
              tests measure capacity and are a separate, opt-in metric.
            </p>
          </CardBody>
        </Card>

        <Card>
          <CardHeader
            icon={<Timer className="h-4 w-4" />}
            title="Gateway latency"
            subtitle={gwLabel ? <>Round trip from {primary.source_name ?? "the collector"} to its default gateway.</> : "No gateway probe is enabled."}
            actions={demoMode ? <DemoTag /> : undefined}
          />
          <CardBody>
            {latency.isLoading ? (
              <Skeleton className="h-[220px] w-full" />
            ) : (
              <TimeSeriesChart
                unit="ms"
                spanSeconds={period.seconds}
                ariaLabel={`Gateway latency over the last ${period.label}.`}
                series={[{ key: "lat", name: "Round-trip time", color: SERIES_COLORS[2], points: latency.points }]}
                emptyHint="No successful gateway probes in this period. A gateway that filters ICMP produces no latency samples; check Connectivity for the other signals."
              />
            )}
            <p className="mt-2 text-[11px] text-faint">Lost probes appear as gaps, not as zero latency.</p>
          </CardBody>
        </Card>
      </section>

      <section className="grid gap-4 xl:grid-cols-3">
        <Card className="xl:col-span-2">
          <CardHeader
            icon={<MonitorSmartphone className="h-4 w-4" />}
            title="Device activity"
            subtitle="Most recently seen devices. Presence depends on the source that saw them."
            actions={
              <Link to="/devices">
                <Button size="sm" variant="ghost">
                  All devices
                </Button>
              </Link>
            }
          />
          <CardBody className="px-2">
            {devices.isLoading ? (
              <Skeleton className="mx-3 h-40" />
            ) : devices.data?.items.length ? (
              <Table>
                <thead>
                  <tr>
                    <Th>Device</Th>
                    <Th>Address</Th>
                    <Th>Presence</Th>
                    <Th>Last seen</Th>
                    <Th className="text-right">Findings</Th>
                  </tr>
                </thead>
                <tbody>
                  {devices.data.items.map((d) => (
                    <tr key={d.id} className="hover:bg-raised/40">
                      <Td>
                        <Link to={`/devices/${d.id}`} className="font-medium text-ink hover:text-accent">
                          {d.name}
                        </Link>
                        <span className="ml-2 text-xs text-faint">{titleCase(d.asset_type)}</span>
                      </Td>
                      <Td className="num font-mono text-xs text-muted">{d.primary_ip ?? "—"}</Td>
                      <Td>
                        <PresenceBadge presence={d.presence} />
                      </Td>
                      <Td className="text-xs text-muted">
                        <Timestamp ts={d.last_seen} />
                        {d.last_seen_source && <span className="text-faint"> · {d.last_seen_source}</span>}
                      </Td>
                      <Td className="num text-right">{d.open_findings || <span className="text-faint">0</span>}</Td>
                    </tr>
                  ))}
                </tbody>
              </Table>
            ) : (
              <EmptyState title="No devices observed yet">Devices appear when an agent reports its neighbour cache or you import your router's client list.</EmptyState>
            )}
          </CardBody>
        </Card>

        <Card>
          <CardHeader
            icon={<Router className="h-4 w-4" />}
            title="Top devices by traffic"
            subtitle={top.data?.supported ? `Average over the last ${Math.round(top.data.window_s / 60)} min, from gateway per-client counters.` : undefined}
            actions={demoMode && top.data?.supported ? <DemoTag /> : undefined}
          />
          <CardBody>
            {top.isLoading ? (
              <Skeleton className="h-40" />
            ) : top.data?.supported ? (
              <ul className="space-y-2.5" aria-label="Top devices by traffic">
                {top.data.devices.slice(0, 6).map((d) => {
                  const total = d.rx_bps + d.tx_bps;
                  const max = (top.data!.devices[0]?.rx_bps ?? 0) + (top.data!.devices[0]?.tx_bps ?? 0) || 1;
                  return (
                    <li key={d.mac}>
                      <div className="flex items-baseline justify-between gap-3 text-xs">
                        {d.asset_id ? (
                          <Link to={`/devices/${d.asset_id}`} className="truncate text-ink hover:text-accent">
                            {d.name}
                          </Link>
                        ) : (
                          <span className="truncate font-mono text-muted">{d.mac}</span>
                        )}
                        <span className="num shrink-0 text-muted">{formatBps(total)}</span>
                      </div>
                      <div className="mt-1 h-1.5 rounded-full bg-raised" aria-hidden>
                        <div className="h-1.5 rounded-full" style={{ width: `${Math.max(2, (total / max) * 100)}%`, background: "var(--series-1)" }} />
                      </div>
                    </li>
                  );
                })}
              </ul>
            ) : (
              <EmptyState
                icon={<CircleSlash className="h-5 w-5" />}
                title="Per-device traffic requires supported gateway integration"
                action={
                  <Link to="/settings/sources">
                    <Button size="sm">See data sources</Button>
                  </Link>
                }
              >
                {top.data?.guidance ?? "No source can attribute traffic to individual devices."}
              </EmptyState>
            )}
          </CardBody>
        </Card>
      </section>

      <section className="grid gap-4 lg:grid-cols-2 xl:grid-cols-4">
        <Card>
          <CardHeader icon={<Bell className="h-4 w-4" />} title="Active alerts" actions={<Link to="/alerts" className="text-xs text-accent hover:underline">View all</Link>} />
          <CardBody>
            {o?.active_alerts.length ? (
              <ul className="space-y-2.5">
                {(o.active_alerts as { id: number; severity: string; title: string; started_at: string; state: string }[]).map((a) => (
                  <li key={a.id} className="flex items-start gap-2 text-xs">
                    <SeverityBadge severity={a.severity} />
                    <div className="min-w-0">
                      <Link to="/alerts" className="line-clamp-2 text-ink hover:text-accent">
                        {a.title}
                      </Link>
                      <p className="text-faint">
                        <Timestamp ts={a.started_at} /> · {a.state}
                      </p>
                    </div>
                  </li>
                ))}
              </ul>
            ) : (
              <EmptyState icon={<CheckCircle2 className="h-5 w-5" />} title="No active alerts" className="py-6" />
            )}
          </CardBody>
        </Card>

        <Card>
          <CardHeader icon={<MonitorSmartphone className="h-4 w-4" />} title="Recently joined" subtitle="New identities seen by a collector." />
          <CardBody>
            {o?.recent_device_events.length ? (
              <ul className="space-y-2.5">
                {(o.recent_device_events as { id: number; ts: string; message: string; asset_id: number | null }[]).slice(0, 5).map((e) => (
                  <li key={e.id} className="text-xs">
                    {e.asset_id ? (
                      <Link to={`/devices/${e.asset_id}`} className="line-clamp-2 text-ink hover:text-accent">
                        {e.message}
                      </Link>
                    ) : (
                      <span className="text-ink">{e.message}</span>
                    )}
                    <p className="text-faint">
                      <Timestamp ts={e.ts} />
                    </p>
                  </li>
                ))}
              </ul>
            ) : (
              <EmptyState title="Nothing new" className="py-6">
                No new device sightings recorded.
              </EmptyState>
            )}
          </CardBody>
        </Card>

        <Card>
          <CardHeader icon={<ShieldCheck className="h-4 w-4" />} title="Security posture" subtitle="Read-only configuration checks." actions={<Link to="/security" className="text-xs text-accent hover:underline">Open</Link>} />
          <CardBody>
            {(o?.posture as { checked?: boolean } | undefined)?.checked || Object.keys(bySev).length ? (
              <>
                <dl className="grid grid-cols-4 gap-2 text-center">
                  {["high", "medium", "low", "info"].map((s) => (
                    <div key={s} className="rounded-lg border border-line bg-raised/50 py-2">
                      <dd className="num text-lg font-semibold text-ink">{bySev[s] ?? 0}</dd>
                      <dt className="text-[10px] uppercase tracking-wider text-faint">{s}</dt>
                    </div>
                  ))}
                </dl>
                <p className="mt-3 text-[11px] leading-relaxed text-faint">
                  Findings are observed configurations or potential exposures. ObserveNet does not exploit anything and does not claim exploitability.
                </p>
              </>
            ) : (
              <EmptyState icon={<CircleSlash className="h-5 w-5" />} title="Not checked yet" className="py-6">
                No agent has reported posture data, so nothing has been assessed. That is “unknown”, not “secure”.
              </EmptyState>
            )}
          </CardBody>
        </Card>

        <Card>
          <CardHeader icon={<ServerCog className="h-4 w-4" />} title="Service health" subtitle="ObserveNet's own components." />
          <CardBody>
            {status.data ? (
              <ul className="space-y-2">
                {(status.data.components as { name: string; state: string; detail: string }[]).map((c) => (
                  <li key={c.name} className="flex items-center justify-between gap-2 text-xs">
                    <span className="text-ink">{c.name}</span>
                    <span className="flex items-center gap-2">
                      <span className="hidden text-faint sm:inline">{c.detail}</span>
                      <HealthBadge state={c.state} />
                    </span>
                  </li>
                ))}
              </ul>
            ) : status.isError ? (
              <ErrorState error={status.error} />
            ) : (
              <Skeleton className="h-28" />
            )}
          </CardBody>
        </Card>
      </section>
    </div>
  );
}
