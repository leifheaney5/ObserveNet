import { expect, test } from "@playwright/test";

import { login, MODE } from "./helpers";

test.describe("unauthenticated access", () => {
  test("API, GraphQL and exports refuse anonymous requests", async ({ request }) => {
    for (const path of ["/api/v1/overview", "/api/v1/devices", "/api/v1/findings", "/api/v1/metrics/current", "/api/v1/export/devices", "/api/v1/audit"]) {
      const res = await request.get(path);
      expect(res.status(), path).toBe(401);
    }
    const gql = await request.post("/graphql", { data: { query: "{ networkSummary { deviceCount } }" } });
    expect(gql.status()).toBe(401);
    expect((await request.get("/metrics")).status()).toBe(404);
    expect((await request.get("/api/docs")).status()).toBe(404);
  });

  test("health endpoints answer and the login page renders", async ({ page, request }) => {
    expect((await request.get("/api/v1/health")).ok()).toBeTruthy();
    const ready = await request.get("/api/v1/ready");
    expect(ready.ok()).toBeTruthy();
    expect(((await ready.json()) as { checks: unknown }).checks).toEqual({ database: "ok", redis: "ok" });
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
    await expect(page.getByText("There are no default credentials.")).toBeVisible();
  });

  test("a wrong password is rejected", async ({ page }) => {
    await page.goto("/");
    await page.getByLabel("Username").fill("admin");
    await page.getByLabel("Password").fill("definitely-not-the-password");
    await page.getByRole("button", { name: "Sign in" }).click();
    await expect(page.getByRole("alert")).toContainText(/invalid username or password/i);
  });
});

test.describe("dashboard end to end", () => {
  test.beforeEach(async ({ page }) => {
    await login(page);
  });

  test("live data flows collector → API → queue → DB → WebSocket → UI", async ({ page }) => {
    const indicator = page.getByTestId("live-indicator");
    await expect(indicator).toHaveAttribute("data-status", "live", { timeout: 45_000 });

    // A live throughput number is rendered and it keeps changing (1 Hz stream, not a static page).
    const rx = page.getByTestId("live-rx");
    await expect(rx).toHaveText(/\d.*bit\/s/, { timeout: 45_000 });
    const seen = new Set<string>();
    for (let i = 0; i < 10 && seen.size < 2; i++) {
      seen.add((await rx.textContent()) ?? "");
      await page.waitForTimeout(1200);
    }
    expect(seen.size, "live value should change between ticks").toBeGreaterThan(1);

    // History is persisted by the worker and served bucketed by the API.
    const history = await page.evaluate(async () => {
      type Series = { source_id: number; label: string; unit: string; observation_point: string };
      type Hist = { resolution: string; series: { series: Series; points: { n: number }[] }[] };
      const series = (await (await fetch("/api/v1/metrics/series?metric=net.rx_bps")).json()) as Series[];
      const s = series[0]!;
      const to = new Date();
      const from = new Date(to.getTime() - 5 * 60_000);
      const url = `/api/v1/metrics/history?metric=net.rx_bps&source_id=${s.source_id}&label=${encodeURIComponent(s.label)}&from=${from.toISOString()}&to=${to.toISOString()}&bucket=1`;
      const h = (await (await fetch(url)).json()) as Hist;
      const first = h.series[0]!;
      return { resolution: h.resolution, measured: first.points.filter((p) => p.n > 0).length, unit: first.series.unit, point: first.series.observation_point };
    });
    expect(history.resolution).toBe("raw");
    expect(history.measured).toBeGreaterThan(3);
    expect(history.unit).toBe("bit/s");
    expect(history.point).toBe("host_nic");
  });

  test("mode labelling is unmistakable", async ({ page }) => {
    if (MODE === "demo") {
      await expect(page.getByTestId("demo-banner")).toContainText("DEMO MODE");
      await expect(page.getByTestId("demo-banner")).toContainText("synthetic");
      await expect(page.getByTestId("kpi-wan_download")).toHaveAttribute("data-state", /ok|stale/);
      await expect(page.getByTestId("kpi-wan_download").getByText("DEMO")).toBeVisible();
    } else {
      await expect(page.getByTestId("demo-banner")).toHaveCount(0);
      // No gateway integration: WAN and per-device traffic stay honest.
      await expect(page.getByTestId("kpi-wan_download")).toHaveAttribute("data-state", "unsupported");
      await expect(page.getByTestId("kpi-wan_download")).toContainText("Unsupported");
      await expect(page.getByTestId("kpi-wan_download")).not.toContainText("bit/s");
      await expect(page.getByText("Per-device traffic requires supported gateway integration")).toBeVisible();
    }
  });

  test("every main page renders real content", async ({ page }) => {
    const pages: [string, RegExp][] = [
      ["Devices", /Devices/],
      ["Traffic", /Traffic/],
      ["Connectivity", /Connectivity/],
      ["Security", /Security/],
      ["Alerts", /Alerts/],
      ["Insights", /Insights/],
      ["Settings", /Settings/],
    ];
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(e.message));
    for (const [name, heading] of pages) {
      await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name, exact: false }).click();
      await expect(page.getByRole("heading", { level: 1, name: heading })).toBeVisible();
      await expect(page.getByRole("main")).not.toBeEmpty();
    }
    expect(errors, "no uncaught page errors").toEqual([]);
  });

  test("GraphQL serves inventory to an authenticated session", async ({ page }) => {
    const result = await page.evaluate(async () => {
      const csrf = document.cookie.match(/observenet_csrf=([^;]+)/)?.[1] ?? "";
      const res = await fetch("/graphql", {
        method: "POST",
        headers: { "content-type": "application/json", "x-csrf-token": decodeURIComponent(csrf) },
        body: JSON.stringify({ query: "{ networkSummary { deviceCount demoMode sources { name freshness } } devices(first: 3) { totalCount edges { node { id name presence { label } relationships { kind targetLabel } } } } }" }),
      });
      type Gql = { errors?: unknown; data: { networkSummary: { demoMode: boolean }; devices: { totalCount: number } } };
      return { status: res.status, body: (await res.json()) as Gql };
    });
    expect(result.status).toBe(200);
    expect(result.body.errors).toBeUndefined();
    expect(result.body.data.networkSummary.demoMode).toBe(MODE === "demo");
    expect(result.body.data.devices.totalCount).toBeGreaterThan(0);
  });
});
