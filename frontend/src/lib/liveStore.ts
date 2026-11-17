/**
 * Live metrics store fed by the WebSocket stream.
 *
 * - One socket for the whole app; frames arrive ~1 Hz already coalesced by the server.
 * - Each series keeps a bounded ring buffer (15 min at 1 Hz), so memory is constant.
 * - Subscribers re-render once per frame via useSyncExternalStore (never per sample).
 * - Connection states are explicit: connecting → live → stale (no frame for 5 s)
 *   → disconnected (socket closed; reconnects with capped exponential backoff + jitter).
 */

export type LiveStatus = "connecting" | "live" | "stale" | "disconnected";

export interface LivePoint {
  t: number; // epoch ms of the measurement (source timestamp, not arrival time)
  v: number;
}

interface WireSample {
  source_id: number;
  m: string;
  l: string;
  v: number;
  u: string;
  t: string;
  q?: string;
}

export interface LiveEvent {
  type: string;
  event?: string;
  id?: number;
  kind?: string;
  title?: string;
  severity?: string;
  ts?: string;
}

interface Frame {
  type: "hello" | "tick" | "heartbeat";
  ts: string;
  upstream?: boolean;
  samples?: WireSample[];
  events?: LiveEvent[];
  snapshot?: Record<string, { v: number; t: string; u: string; q?: string }>;
  demo_mode?: boolean;
}

export const MAX_POINTS = 900;
export const STALE_AFTER_MS = 5000;

export function seriesKey(sourceId: number | null | undefined, metric: string, label: string | null | undefined): string {
  return `${sourceId ?? "?"}|${metric}|${label ?? ""}`;
}

export function backoffDelay(attempt: number, rand: () => number = Math.random): number {
  const base = Math.min(15000, 500 * 2 ** Math.min(attempt, 6));
  return Math.round(base * (0.5 + rand() * 0.5));
}

export class LiveStore {
  private buffers = new Map<string, LivePoint[]>();
  private listeners = new Set<() => void>();
  private eventListeners = new Set<(e: LiveEvent) => void>();
  private socket: WebSocket | null = null;
  private attempt = 0;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private staleTimer: ReturnType<typeof setInterval> | null = null;
  private closed = true;

  version = 0;
  status: LiveStatus = "disconnected";
  lastFrameAt: number | null = null;
  lastSampleAt: number | null = null;
  upstreamOk = true;
  reconnects = 0;

  subscribe = (fn: () => void): (() => void) => {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  };

  onEvent(fn: (e: LiveEvent) => void): () => void {
    this.eventListeners.add(fn);
    return () => this.eventListeners.delete(fn);
  }

  getVersion = (): number => this.version;

  series(key: string): LivePoint[] {
    return this.buffers.get(key) ?? EMPTY;
  }

  latest(key: string): LivePoint | undefined {
    const buf = this.buffers.get(key);
    return buf?.[buf.length - 1];
  }

  private emit(): void {
    this.version += 1;
    for (const fn of this.listeners) fn();
  }

  private setStatus(status: LiveStatus): void {
    if (this.status !== status) {
      this.status = status;
      this.emit();
    }
  }

  private push(key: string, point: LivePoint): void {
    let buf = this.buffers.get(key);
    if (!buf) {
      buf = [];
      this.buffers.set(key, buf);
    }
    const last = buf[buf.length - 1];
    if (last && point.t <= last.t) return; // duplicate or out-of-order frame after a reconnect
    buf.push(point);
    if (buf.length > MAX_POINTS) buf.splice(0, buf.length - MAX_POINTS);
  }

  /** Exposed for tests: apply one decoded frame. */
  handleFrame(frame: Frame, now: number = Date.now()): void {
    this.lastFrameAt = now;
    if (frame.upstream !== undefined) this.upstreamOk = frame.upstream;
    if (frame.type === "hello" && frame.snapshot) {
      for (const [key, cur] of Object.entries(frame.snapshot)) {
        const t = Date.parse(cur.t);
        if (now - t < 15000) this.push(key, { t, v: cur.v });
      }
    }
    for (const s of frame.samples ?? []) {
      this.push(seriesKey(s.source_id, s.m, s.l), { t: Date.parse(s.t), v: s.v });
      this.lastSampleAt = now;
    }
    for (const e of frame.events ?? []) for (const fn of this.eventListeners) fn(e);
    this.status = "live";
    this.emit();
  }

  connect(): void {
    this.closed = false;
    if (this.socket) return;
    this.setStatus("connecting");
    const proto = window.location.protocol === "https:" ? "wss" : "ws";
    let socket: WebSocket;
    try {
      socket = new WebSocket(`${proto}://${window.location.host}/api/v1/stream`);
    } catch {
      this.scheduleReconnect();
      return;
    }
    this.socket = socket;
    socket.onopen = () => {
      this.attempt = 0;
    };
    socket.onmessage = (ev: MessageEvent<string>) => {
      try {
        this.handleFrame(JSON.parse(ev.data) as Frame);
      } catch {
        /* ignore malformed frame */
      }
    };
    socket.onclose = () => {
      this.socket = null;
      if (this.closed) return;
      this.setStatus("disconnected");
      this.scheduleReconnect();
    };
    socket.onerror = () => socket.close();
    if (!this.staleTimer) {
      this.staleTimer = setInterval(() => this.checkStale(), 1000);
    }
  }

  checkStale(now: number = Date.now()): void {
    if (this.status === "live" && this.lastFrameAt !== null && now - this.lastFrameAt > STALE_AFTER_MS) {
      this.setStatus("stale");
      // The server heartbeats every second; silence means a dead path. Force a reconnect.
      this.socket?.close();
    }
  }

  private scheduleReconnect(): void {
    if (this.closed || this.reconnectTimer) return;
    const delay = backoffDelay(this.attempt++);
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this.reconnects += 1;
      this.connect();
    }, delay);
  }

  disconnect(): void {
    this.closed = true;
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    if (this.staleTimer) clearInterval(this.staleTimer);
    this.reconnectTimer = null;
    this.staleTimer = null;
    this.socket?.close();
    this.socket = null;
    this.buffers.clear();
    this.status = "disconnected";
    this.emit();
  }
}

const EMPTY: LivePoint[] = [];
export const liveStore = new LiveStore();
