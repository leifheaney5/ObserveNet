import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { KpiCard } from "@/components/KpiCard";
import type { Kpi } from "@/lib/api";

const base: Kpi = { key: "wan_download", label: "WAN download", value: null, unit: "bit/s", state: "unsupported", detail: {} };
const renderCard = (kpi: Kpi | undefined, live?: { value: number; ts: string } | null) =>
  render(
    <MemoryRouter>
      <KpiCard kpi={kpi} icon={<span />} to="/traffic" live={live} />
    </MemoryRouter>,
  );

describe("KpiCard", () => {
  it("shows the state and reason — not a number — when a metric is unsupported", () => {
    renderCard({ ...base, reason: "No gateway source connected." });
    expect(screen.getByText("Unsupported")).toBeInTheDocument();
    expect(screen.getByText("No gateway source connected.")).toBeInTheDocument();
    expect(screen.queryByText(/bit\/s/)).not.toBeInTheDocument();
    expect(screen.queryByText("0")).not.toBeInTheDocument();
  });

  it("never lets a live value override an unmeasured KPI", () => {
    renderCard({ ...base, state: "unavailable" }, { value: 5_000_000, ts: "2026-01-01T00:00:00Z" });
    expect(screen.getByText("Unavailable")).toBeInTheDocument();
    expect(screen.queryByText("5.00")).not.toBeInTheDocument();
  });

  it("renders a measured value with unit and provenance", () => {
    renderCard({ ...base, key: "host_rx", label: "Host NIC receive", value: 25_300_000, state: "ok", ts: new Date().toISOString(), source: "linux-leif · eth0", observation_point: "host_nic" });
    expect(screen.getByText("25.3")).toBeInTheDocument();
    expect(screen.getByText("Mbit/s")).toBeInTheDocument();
    expect(screen.getByText("linux-leif · eth0")).toBeInTheDocument();
    expect(screen.getByRole("link")).toHaveAttribute("href", "/traffic");
  });

  it("marks stale values as stale", () => {
    renderCard({ ...base, value: 1000, state: "stale", ts: "2026-01-01T00:00:00Z" });
    expect(screen.getByText("Stale")).toBeInTheDocument();
  });

  it("shows a skeleton while loading", () => {
    const { container } = renderCard(undefined);
    expect(container.querySelector("a")).toBeNull();
  });
});
