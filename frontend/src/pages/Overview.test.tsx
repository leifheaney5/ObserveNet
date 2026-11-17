import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DemoBanner } from "@/app/Layout";
import { TooltipProvider } from "@/components/ui/overlays";
import { AuthProvider } from "@/hooks/useAuth";
import { PeriodProvider } from "@/hooks/usePeriod";
import type { Kpi, Overview } from "@/lib/api";
import { OverviewPage } from "@/pages/Overview";

vi.mock("@/lib/liveStore", async (orig) => {
  const mod = await orig<typeof import("@/lib/liveStore")>();
  mod.liveStore.connect = () => {};
  mod.liveStore.disconnect = () => {};
  return mod;
});

const kpi = (key: string, label: string, unit: string, state: string, value: number | null = null, reason?: string): Kpi => ({ key, label, unit, state, value, reason, detail: {} });

function emptyRealOverview(): Overview {
  return {
    generated_at: new Date().toISOString(),
    demo_mode: false,
    kpis: [
      kpi("devices", "Known devices", "devices", "unknown", null, "No device has been observed yet."),
      kpi("wan_download", "WAN download", "bit/s", "unsupported", null, "No gateway source is connected."),
      kpi("wan_upload", "WAN upload", "bit/s", "unsupported", null, "No gateway source is connected."),
      kpi("gateway_latency", "Gateway latency", "ms", "unavailable", null, "No gateway probe results in the last 60 s."),
      kpi("packet_loss", "Packet loss", "percent", "unavailable", null, "No gateway probe results in the last 60 s."),
      kpi("open_findings", "Open findings", "findings", "unknown", null, "No agent has reported posture data yet."),
    ],
    primary: { source_id: null, source_name: null, interface: null, gateway_target_id: null, wan_source_id: null, wan_label: null },
    internet: { status: "unknown", reason: "No recent results from external checks.", signals_total: 0, signals_ok: 0 },
    active_alerts: [],
    recent_device_events: [],
    per_device_traffic: { supported: false, guidance: "needs gateway", sources: [] },
    posture: { open_by_severity: {}, last_observed_at: null, checked: false },
  };
}

function mockApi(demo: boolean, overview: Overview) {
  const routes: Record<string, unknown> = {
    "/api/v1/meta": { name: "ObserveNet", version: "0.1.0", demo_mode: demo, setup_required: false },
    "/api/v1/auth/me": { user: { id: 1, username: "admin", role: "admin", disabled: false, created_at: "2026-01-01T00:00:00Z", last_login_at: null }, csrf_token: "t", demo_mode: demo },
    "/api/v1/overview": { ...overview, demo_mode: demo },
    "/api/v1/status": { version: "0.1.0", demo_mode: demo, components: [{ name: "API", state: "ok", detail: "v0.1.0" }], queues: {}, ws_clients: 0 },
    "/api/v1/devices": { items: [], total: 0, limit: 8, offset: 0, coverage_note: "partial" },
    "/api/v1/traffic/top-devices": { supported: false, guidance: "Per-device traffic requires a supported gateway integration.", window_s: 300, devices: [] },
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const path = new URL(String(input), "http://localhost").pathname;
      const body = routes[path];
      return new Response(JSON.stringify(body ?? { detail: "not found" }), { status: body ? 200 : 404, headers: { "content-type": "application/json" } });
    }),
  );
}

function renderOverview() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <TooltipProvider>
        <MemoryRouter>
          <AuthProvider>
            <PeriodProvider>
              <OverviewPage />
            </PeriodProvider>
          </AuthProvider>
        </MemoryRouter>
      </TooltipProvider>
    </QueryClientProvider>,
  );
}

describe("Overview in real mode with an empty database", () => {
  beforeEach(() => mockApi(false, emptyRealOverview()));
  afterEach(() => vi.unstubAllGlobals());

  it("shows honest states instead of numbers", async () => {
    renderOverview();
    await waitFor(() => expect(screen.getByText("No collector has reported yet")).toBeInTheDocument());
    expect(screen.getAllByText("Unsupported")).toHaveLength(2); // WAN download + upload
    expect(screen.getAllByText("Unavailable").length).toBeGreaterThanOrEqual(2); // latency + loss
    expect(screen.getAllByText("Unknown").length).toBeGreaterThanOrEqual(2); // devices + findings
    for (const key of ["wan_download", "wan_upload", "gateway_latency", "packet_loss"]) {
      expect(screen.getByTestId(`kpi-${key}`)).not.toHaveTextContent(/\d+(\.\d+)?\s*(bit\/s|ms|%)/);
    }
    expect(screen.queryByText("DEMO")).not.toBeInTheDocument();
  });

  it("explains missing per-device traffic instead of estimating it", async () => {
    renderOverview();
    await waitFor(() => expect(screen.getByText("Per-device traffic requires supported gateway integration")).toBeInTheDocument());
    expect(screen.getByText("Not checked yet")).toBeInTheDocument();
  });
});

describe("Demo labelling", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("tags KPI cards as DEMO when the backend is in demo mode", async () => {
    const o = emptyRealOverview();
    o.kpis = o.kpis.map((k) => (k.key === "wan_download" ? { ...k, state: "ok", value: 40_000_000, ts: new Date().toISOString(), source: "gateway" } : k));
    mockApi(true, o);
    renderOverview();
    await waitFor(() => expect(screen.getAllByText("DEMO").length).toBeGreaterThan(0));
    expect(screen.getByText("40.0")).toBeInTheDocument();
  });

  it("the demo banner states that everything is synthetic", () => {
    render(<DemoBanner />);
    expect(screen.getByTestId("demo-banner")).toHaveTextContent(/DEMO MODE/);
    expect(screen.getByTestId("demo-banner")).toHaveTextContent(/synthetic/);
  });
});
