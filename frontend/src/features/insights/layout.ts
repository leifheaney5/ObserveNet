export interface TopoNode {
  id: string;
  type: "segment" | "asset" | "interface" | "service";
  label: string;
  meta: Record<string, unknown>;
}

export interface TopoEdge {
  source: string;
  target: string;
  kind: string;
  evidence: string;
}

export interface Topology {
  nodes: TopoNode[];
  edges: TopoEdge[];
  note: string;
}

const MAX_SERVICES = 6;
const ROW = 30;
export const NODE_H = 22;
const PAD = 34;
export const COL = { segment: { x: 16, w: 190 }, asset: { x: 384, w: 226 }, child: { x: 716, w: 228 } };
export const WIDTH = 960;

const KIND_TEXT: Record<string, string> = {
  HAS_INTERFACE: "has interface",
  ATTACHED_TO: "attached to",
  SEEN_ON: "seen on",
  LISTENS_ON: "listens on",
};

export const NODE_STYLE: Record<string, { stroke: string; label: string }> = {
  segment: { stroke: "var(--series-2)", label: "Subnet" },
  asset: { stroke: "var(--series-1)", label: "Device" },
  interface: { stroke: "rgb(var(--muted))", label: "Interface" },
  service: { stroke: "var(--series-3)", label: "Listening service" },
  more: { stroke: "rgb(var(--line))", label: "" },
};

export interface Placed {
  id: string;
  type: keyof typeof NODE_STYLE;
  label: string;
  sub?: string;
  x: number;
  y: number;
  w: number;
  href?: string;
}

export interface DrawnEdge {
  id: string;
  from: string;
  to: string;
  label: string;
}

/** Deterministic three-column layout: subnets → devices → interfaces and services. */
export function layoutTopology(topo: Topology): { nodes: Placed[]; edges: DrawnEdge[]; height: number } {
  const byId = new Map(topo.nodes.map((n) => [n.id, n]));
  const assets = topo.nodes
    .filter((n) => n.type === "asset")
    .sort((a, b) => Number(!!b.meta.managed) - Number(!!a.meta.managed) || a.label.localeCompare(b.label));
  const children = new Map<string, { interfaces: TopoNode[]; services: TopoNode[] }>();
  for (const a of assets) children.set(a.id, { interfaces: [], services: [] });
  const ifaceOwner = new Map<string, string>();
  for (const e of topo.edges) {
    const target = byId.get(e.target);
    const bucket = children.get(e.source);
    if (!target || !bucket) continue;
    if (e.kind === "HAS_INTERFACE") {
      bucket.interfaces.push(target);
      ifaceOwner.set(target.id, e.source);
    } else if (e.kind === "LISTENS_ON") {
      bucket.services.push(target);
    }
  }

  const nodes: Placed[] = [];
  const edges: DrawnEdge[] = [];
  let row = 0;
  const assetY = new Map<string, number>();
  for (const a of assets) {
    const c = children.get(a.id)!;
    c.interfaces.sort((x, y) => x.label.localeCompare(y.label));
    c.services.sort((x, y) => x.label.localeCompare(y.label, undefined, { numeric: true }));
    const shownServices = c.services.slice(0, MAX_SERVICES);
    const hidden = c.services.length - shownServices.length;
    const kids: Placed[] = [
      ...c.interfaces.map((n) => ({ id: n.id, type: "interface" as const, label: n.label, sub: n.meta.mac ? String(n.meta.mac) : undefined, x: COL.child.x, y: 0, w: COL.child.w })),
      ...shownServices.map((n) => ({
        id: n.id,
        type: "service" as const,
        label: n.label,
        sub: [n.meta.address, n.meta.process].filter(Boolean).join(" · ") || undefined,
        x: COL.child.x,
        y: 0,
        w: COL.child.w,
      })),
    ];
    if (hidden > 0) kids.push({ id: `${a.id}:more`, type: "more", label: `+${hidden} more service${hidden === 1 ? "" : "s"}`, x: COL.child.x, y: 0, w: COL.child.w });
    const span = Math.max(1, kids.length);
    const y = PAD + (row + (span - 1) / 2) * ROW;
    assetY.set(a.id, y);
    nodes.push({
      id: a.id,
      type: "asset",
      label: a.label,
      sub: a.meta.managed ? "agent" : String(a.meta.asset_type ?? ""),
      x: COL.asset.x,
      y,
      w: COL.asset.w,
      href: `/devices/${a.id.split(":")[1]}`,
    });
    kids.forEach((k, i) => {
      k.y = PAD + (row + i) * ROW;
      nodes.push(k);
      if (k.type !== "more") edges.push({ id: `${a.id}>${k.id}`, from: a.id, to: k.id, label: k.type === "interface" ? KIND_TEXT.HAS_INTERFACE! : KIND_TEXT.LISTENS_ON! });
    });
    row += span + 0.5;
  }

  // Subnet ↔ device links (directly observed, or through one of the device's interfaces).
  const links = new Map<string, Set<string>>();
  for (const e of topo.edges) {
    let assetId: string | undefined;
    let text = "";
    if (e.kind === "SEEN_ON") {
      assetId = e.source;
      text = `${KIND_TEXT.SEEN_ON} (${e.evidence})`;
    } else if (e.kind === "ATTACHED_TO") {
      assetId = ifaceOwner.get(e.source);
      text = `${KIND_TEXT.ATTACHED_TO} via ${byId.get(e.source)?.label ?? "interface"}`;
    }
    if (!assetId || !assetY.has(assetId)) continue;
    const key = `${e.target}>${assetId}`;
    if (!links.has(key)) links.set(key, new Set());
    links.get(key)!.add(text);
  }
  const segments = topo.nodes.filter((n) => n.type === "segment");
  const segY = new Map<string, number>();
  for (const s of segments) {
    const ys = [...links.keys()].filter((k) => k.startsWith(`${s.id}>`)).map((k) => assetY.get(k.split(">")[1]!)!);
    segY.set(s.id, ys.length ? ys.reduce((a, b) => a + b, 0) / ys.length : Number.POSITIVE_INFINITY);
  }
  const contentBottom = Math.max(PAD, PAD + (row - 0.5) * ROW);
  let lastY = PAD - ROW;
  for (const s of [...segments].sort((a, b) => segY.get(a.id)! - segY.get(b.id)! || a.label.localeCompare(b.label))) {
    const wanted = Number.isFinite(segY.get(s.id)!) ? segY.get(s.id)! : lastY + ROW;
    const y = Math.max(wanted, lastY + ROW); // never overlap two subnets
    lastY = y;
    nodes.push({ id: s.id, type: "segment", label: s.label, sub: s.meta.name ? String(s.meta.name) : undefined, x: COL.segment.x, y, w: COL.segment.w });
  }
  for (const [key, texts] of links) {
    const [seg, asset] = key.split(">") as [string, string];
    edges.push({ id: key, from: seg, to: asset, label: [...texts].join("; ") });
  }
  return { nodes, edges, height: Math.max(contentBottom, lastY + ROW / 2) + PAD / 2 };
}
