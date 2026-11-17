import { expect, test } from "@playwright/test";

import { login, MODE } from "./helpers";

/**
 * Find → Fix → Verify, end to end, in DEMO_MODE:
 * a synthetic host has SMBv1 enabled → the finding is open → "the operator" changes the
 * setting (demo control) → Verify re-runs the same check on fresh evidence → Resolved,
 * with before/after evidence. Without the fix, Verify must NOT report resolved.
 */
test.describe("verification loop (demo)", () => {
  test.skip(MODE !== "demo", "uses the demo 'simulate fix' control");

  test("a finding is only resolved after fresh evidence shows the change", async ({ page }) => {
    await login(page);
    const api = async <T>(method: string, path: string, body?: unknown): Promise<T> =>
      page.evaluate(
        async ({ method, path, body }) => {
          const csrf = decodeURIComponent(document.cookie.match(/observenet_csrf=([^;]+)/)?.[1] ?? "");
          const res = await fetch(`/api/v1${path}`, {
            method,
            headers: { "content-type": "application/json", "x-csrf-token": csrf },
            body: body === undefined ? undefined : JSON.stringify(body),
          });
          return (await res.json()) as unknown;
        },
        { method, path, body },
      ) as Promise<T>;

    type Finding = { id: number; rule_id: string; status: string; classification: string; verification_status: string };
    type Detail = { finding: Finding; evidence: { observed: boolean; kind: string; collected_at: string }[]; verifications: { status: string; outcome: string | null; diff: { changes: unknown[] } | null }[] };

    await api("POST", "/demo/fix", { item: "smb1", fixed: false });
    let smb: Finding | undefined;
    await expect
      .poll(
        async () => {
          smb = (await api<Finding[]>("GET", "/findings?status=all")).find((f) => f.rule_id === "WIN-SMB-001");
          return smb?.status;
        },
        { timeout: 120_000, intervals: [2000] },
      )
      .toMatch(/open|acknowledged/);
    const id = smb!.id;

    const waitForRun = async () => {
      let detail!: Detail;
      await expect
        .poll(
          async () => {
            detail = await api<Detail>("GET", `/findings/${id}`);
            return detail.verifications[0]?.status;
          },
          { timeout: 120_000, intervals: [2000] },
        )
        .toBe("completed");
      return detail;
    };

    // 1) Verify without fixing anything: must stay unresolved.
    await api("POST", `/findings/${id}/verify`);
    let detail = await waitForRun();
    expect(detail.verifications[0]?.outcome).toBe("still_observed");
    expect(detail.finding.status).not.toBe("resolved");

    // 2) "Fix" the setting on the synthetic host, then verify again.
    await api("POST", "/demo/fix", { item: "smb1", fixed: true });
    await api("POST", `/findings/${id}/verify`);
    await expect
      .poll(async () => (await api<Detail>("GET", `/findings/${id}`)).finding.status, { timeout: 120_000, intervals: [2000] })
      .toBe("resolved");
    detail = await api<Detail>("GET", `/findings/${id}`);
    expect(detail.finding.classification).toBe("VERIFIED_RESOLUTION");
    const resolvedRun = detail.verifications.find((v) => v.outcome === "resolved");
    expect(resolvedRun?.diff?.changes.length).toBeGreaterThan(0);
    expect(detail.evidence.some((e) => e.observed)).toBeTruthy();
    expect(detail.evidence.some((e) => !e.observed && e.kind === "recheck")).toBeTruthy();

    // The UI shows the verified state and the evidence.
    await page.goto(`/security/findings/${id}`);
    await expect(page.getByText(/Resolved \(verified\)|Verified resolution/).first()).toBeVisible();

    // Leave the demo host as it was.
    await api("POST", "/demo/fix", { item: "smb1", fixed: false });
  });
});
