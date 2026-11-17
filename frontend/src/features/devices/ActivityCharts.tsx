import { SERIES_COLORS, TimeSeriesChart } from "@/components/charts/TimeSeriesChart";
import { Card, CardBody, CardHeader, EmptyState, Skeleton } from "@/components/ui/primitives";
import { DemoTag } from "@/components/ui/status";
import { useSeriesHistory } from "@/hooks/useHistory";
import { usePeriod } from "@/hooks/usePeriod";
import type { SeriesMeta } from "@/lib/api";

function InterfaceChart({ sourceId, label, coverage, synthetic }: { sourceId: number; label: string; coverage: string | null; synthetic: boolean }) {
  const { period } = usePeriod();
  const rx = useSeriesHistory({ metric: "net.rx_bps", sourceId, label }, period);
  const tx = useSeriesHistory({ metric: "net.tx_bps", sourceId, label }, period);
  return (
    <Card>
      <CardHeader
        title={<span className="font-mono">{label}</span>}
        subtitle={coverage ?? "Traffic through this host's own interface only."}
        actions={synthetic ? <DemoTag /> : undefined}
      />
      <CardBody>
        {rx.isLoading ? (
          <Skeleton className="h-[200px] w-full" />
        ) : (
          <TimeSeriesChart
            unit="bit/s"
            height={200}
            spanSeconds={period.seconds}
            ariaLabel={`Throughput on interface ${label} over the last ${period.label}.`}
            series={[
              { key: "rx", name: "Receive", color: SERIES_COLORS[0], points: rx.points },
              { key: "tx", name: "Transmit", color: SERIES_COLORS[1], points: tx.points },
            ]}
            emptyHint="No samples in this period. Gaps mean nothing was measured — they are not zeros."
          />
        )}
        {rx.note && <p className="mt-2 text-[11px] text-faint">{rx.note}</p>}
      </CardBody>
    </Card>
  );
}

/** Traffic history exists only for hosts whose own agent reports NIC counters. */
export function ActivityCharts({ series, managed }: { series: SeriesMeta[]; managed: boolean }) {
  const nics = series.filter((s) => s.metric === "net.rx_bps");
  if (!nics.length) {
    return (
      <Card>
        <EmptyState title="No traffic history for this device">
          {managed
            ? "Its agent has not reported interface counters yet."
            : "Only hosts running an ObserveNet agent have traffic history, because an agent can only count its own interfaces. Per-device traffic for other devices requires a supported gateway integration (per-client counters or flow export)."}
        </EmptyState>
      </Card>
    );
  }
  // Busiest-looking physical interfaces first; virtual bridges last.
  const order = (label: string) => (/^(docker|br-|veth|virbr|lo)/.test(label) ? 1 : 0);
  const sorted = [...nics].sort((a, b) => order(a.label) - order(b.label) || a.label.localeCompare(b.label));
  return (
    <div className="space-y-3">
      <div className="grid gap-4 xl:grid-cols-2">
        {sorted.slice(0, 6).map((s) => (
          <InterfaceChart key={s.id} sourceId={s.source_id} label={s.label} coverage={s.coverage} synthetic={s.quality === "synthetic"} />
        ))}
      </div>
      {sorted.length > 6 && <p className="text-xs text-faint">{sorted.length - 6} more interface(s) not charted.</p>}
      <p className="text-[11px] text-faint">Use the period selector in the header to change the time range. Host NIC traffic is not WAN or whole-network traffic.</p>
    </div>
  );
}
