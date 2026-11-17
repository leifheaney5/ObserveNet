import { useQuery } from "@tanstack/react-query";
import { Network, Table2 } from "lucide-react";
import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { Button, Card, CardBody, CardHeader, EmptyState, ErrorState, Skeleton, Table, Td, Th } from "@/components/ui/primitives";
import { api } from "@/lib/api";

import { COL, layoutTopology, NODE_H, NODE_STYLE, WIDTH, type DrawnEdge, type Topology } from "@/features/insights/layout";

/** Truncate a node's label and secondary text so both stay inside the node box. */
function fitText(label: string, sub: string | undefined, width: number): { label: string; sub?: string } {
  const maxLabel = Math.max(6, Math.floor((width - 20) / 7.2));
  const shown = label.length > maxLabel ? `${label.slice(0, maxLabel - 1)}…` : label;
  const room = Math.floor((width - 26 - shown.length * 7.2) / 6.2);
  if (!sub || room < 4) return { label: shown };
  return { label: shown, sub: sub.length > room ? `${sub.slice(0, room - 1)}…` : sub };
}

function Graph({ topo }: { topo: Topology }) {
  const navigate = useNavigate();
  const [active, setActive] = useState<string | null>(null);
  const { nodes, edges, height } = useMemo(() => layoutTopology(topo), [topo]);
  const pos = useMemo(() => new Map(nodes.map((n) => [n.id, n])), [nodes]);
  const lit = (e: DrawnEdge) => active !== null && (e.from === active || e.to === active || e.id === active);

  return (
    <div className="overflow-x-auto rounded-lg border border-line bg-bg/40">
      <svg
        viewBox={`0 0 ${WIDTH} ${height}`}
        width="100%"
        style={{ minWidth: 760 }}
        role="group"
        aria-label={`Network relationship diagram: ${nodes.filter((n) => n.type === "segment").length} subnets, ${nodes.filter((n) => n.type === "asset").length} devices. A table of the same relationships is available with the Table button.`}
      >
        {[
          ["Subnets", COL.segment.x],
          ["Devices", COL.asset.x],
          ["Interfaces & listening services", COL.child.x],
        ].map(([label, x]) => (
          <text key={label} x={Number(x)} y={14} fill="rgb(var(--faint))" fontSize="10" style={{ textTransform: "uppercase", letterSpacing: "0.08em" }}>
            {label}
          </text>
        ))}
        <g fill="none">
          {edges.map((e) => {
            const a = pos.get(e.from);
            const b = pos.get(e.to);
            if (!a || !b) return null;
            const x1 = a.x + a.w;
            const x2 = b.x;
            const mid = (x1 + x2) / 2;
            const on = lit(e);
            return (
              <g key={e.id} tabIndex={0} role="img" aria-label={`${a.label} ${e.label} ${b.label}`} onMouseEnter={() => setActive(e.id)} onMouseLeave={() => setActive(null)} onFocus={() => setActive(e.id)} onBlur={() => setActive(null)} style={{ outline: "none" }}>
                <title>{`${a.label} — ${e.label} — ${b.label}`}</title>
                <path d={`M${x1},${a.y} C${mid},${a.y} ${mid},${b.y} ${x2},${b.y}`} stroke="transparent" strokeWidth={12} />
                <path
                  d={`M${x1},${a.y} C${mid},${a.y} ${mid},${b.y} ${x2},${b.y}`}
                  stroke={on ? "rgb(var(--accent))" : "rgb(var(--line))"}
                  strokeWidth={on ? 2 : 1.25}
                  strokeOpacity={active !== null && !on ? 0.45 : 1}
                />
              </g>
            );
          })}
        </g>
        {nodes.map((n) => {
          const style = NODE_STYLE[n.type]!;
          const on = active === n.id;
          const fit = fitText(n.label, n.sub, n.w);
          const body = (
            <g>
              <rect
                x={n.x}
                y={n.y - NODE_H / 2}
                width={n.w}
                height={NODE_H}
                rx={6}
                fill={n.type === "more" ? "transparent" : "rgb(var(--raised))"}
                stroke={on ? "rgb(var(--accent))" : "rgb(var(--line))"}
                strokeDasharray={n.type === "more" ? "3 3" : undefined}
              />
              {n.type !== "more" && <rect x={n.x} y={n.y - NODE_H / 2} width={3} height={NODE_H} rx={1.5} fill={style.stroke} />}
              <text x={n.x + 10} y={n.y + 4} fontSize="11.5" fill={n.type === "more" ? "rgb(var(--faint))" : "rgb(var(--ink))"} fontFamily={n.type === "asset" ? undefined : "ui-monospace, monospace"}>
                {fit.label}
                {fit.sub && (
                  <tspan fill="rgb(var(--faint))" fontSize="10" fontFamily="inherit">
                    {"  "}
                    {fit.sub}
                  </tspan>
                )}
              </text>
              <title>{[style.label, n.label, n.sub].filter(Boolean).join(" · ")}</title>
            </g>
          );
          const handlers = { onMouseEnter: () => setActive(n.id), onMouseLeave: () => setActive(null), onFocus: () => setActive(n.id), onBlur: () => setActive(null) };
          return n.href ? (
            <a
              key={n.id}
              href={n.href}
              aria-label={`Device ${n.label}. Open device details.`}
              onClick={(ev) => {
                ev.preventDefault();
                navigate(n.href!);
              }}
              style={{ cursor: "pointer", outline: "none" }}
              {...handlers}
            >
              {body}
            </a>
          ) : (
            <g key={n.id} tabIndex={n.type === "more" ? undefined : 0} role="img" aria-label={`${style.label}: ${n.label}${n.sub ? `, ${n.sub}` : ""}`} style={{ outline: "none" }} {...handlers}>
              {body}
            </g>
          );
        })}
        {edges.filter(lit).map((e) => {
          const a = pos.get(e.from)!;
          const b = pos.get(e.to)!;
          // Beside a subnet there is only the gap up to the device column to write in.
          const limit = active === e.to && a.type === "segment" ? 28 : 44;
          const text = e.label.length > limit ? `${e.label.slice(0, limit - 1)}…` : e.label;
          const w = text.length * 5.6 + 12;
          // A focused line is labelled at its midpoint. A focused node labels each of its
          // lines next to the node at the *other* end, so the labels sit on separate rows.
          let x = (a.x + a.w + b.x) / 2;
          let y = (a.y + b.y) / 2;
          if (active === e.from) {
            x = b.x - w / 2 - 6;
            y = b.y;
          } else if (active === e.to) {
            x = a.x + a.w + w / 2 + 6;
            y = a.y;
          }
          return (
            <g key={`label-${e.id}`} pointerEvents="none">
              <rect x={x - w / 2} y={y - 9} width={w} height={18} rx={4} fill="rgb(var(--surface))" stroke="rgb(var(--accent))" strokeOpacity={0.6} />
              <text x={x} y={y + 3.5} textAnchor="middle" fontSize="10" fill="rgb(var(--ink))">
                {text}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}

function EdgeTable({ topo }: { topo: Topology }) {
  const byId = new Map(topo.nodes.map((n) => [n.id, n]));
  const name = (id: string) => {
    const n = byId.get(id);
    return n ? `${NODE_STYLE[n.type]?.label ?? n.type}: ${n.label}` : id;
  };
  return (
    <div className="max-h-[480px] overflow-auto rounded-lg border border-line">
      <Table>
        <caption className="sr-only">Observed relationships between devices, interfaces, subnets and services</caption>
        <thead className="sticky top-0 bg-raised">
          <tr>
            <Th>Relationship</Th>
            <Th>From</Th>
            <Th>To</Th>
            <Th>Evidence</Th>
          </tr>
        </thead>
        <tbody>
          {topo.edges.map((e, i) => (
            <tr key={`${e.source}-${e.target}-${e.kind}-${i}`}>
              <Td className="font-mono text-xs text-muted">{e.kind}</Td>
              <Td className="text-xs">
                {e.source.startsWith("asset:") ? (
                  <Link to={`/devices/${e.source.split(":")[1]}`} className="text-ink hover:text-accent">
                    {name(e.source)}
                  </Link>
                ) : (
                  name(e.source)
                )}
              </Td>
              <Td className="text-xs">{name(e.target)}</Td>
              <Td className="text-xs text-muted">{e.evidence}</Td>
            </tr>
          ))}
        </tbody>
      </Table>
    </div>
  );
}

export function TopologyCard() {
  const [table, setTable] = useState(false);
  const topo = useQuery({ queryKey: ["topology"], queryFn: () => api.get<Topology>("/topology"), refetchInterval: 60_000 });
  const data = topo.data;
  return (
    <Card>
      <CardHeader
        icon={<Network className="h-4 w-4" />}
        title="Network relationships"
        subtitle="Device ↔ interface ↔ subnet ↔ service relationships that a source actually reported."
        actions={
          data && data.nodes.length > 0 ? (
            <Button size="sm" variant="ghost" aria-pressed={table} onClick={() => setTable((v) => !v)}>
              <Table2 className="h-3.5 w-3.5" aria-hidden /> {table ? "Diagram" : "Table"}
            </Button>
          ) : undefined
        }
      />
      <CardBody>
        {topo.isLoading ? (
          <Skeleton className="h-64 w-full" />
        ) : topo.isError ? (
          <ErrorState error={topo.error} retry={() => void topo.refetch()} />
        ) : !data || data.nodes.length === 0 ? (
          <EmptyState icon={<Network className="h-5 w-5" />} title="No relationships observed yet">
            The diagram fills in when an agent reports its interfaces, neighbours and listening services.
          </EmptyState>
        ) : (
          <>
            {table ? <EdgeTable topo={data} /> : <Graph topo={data} />}
            <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
              <ul className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted" aria-label="Legend">
                {(["segment", "asset", "interface", "service"] as const).map((t) => (
                  <li key={t} className="flex items-center gap-1.5">
                    <span className="h-3 w-1 rounded-full" style={{ background: NODE_STYLE[t]!.stroke }} aria-hidden />
                    {NODE_STYLE[t]!.label}
                  </li>
                ))}
                <li className="text-faint">Hover or focus a node or line to see the relationship.</li>
              </ul>
            </div>
            <p className="mt-2 text-[11px] leading-relaxed text-faint">{data.note}</p>
          </>
        )}
      </CardBody>
    </Card>
  );
}
