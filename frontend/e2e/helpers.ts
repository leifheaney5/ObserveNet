import { expect, type Page } from "@playwright/test";

export const MODE = (process.env.E2E_MODE ?? "demo") as "demo" | "real";
export const USERNAME = process.env.E2E_USERNAME ?? "admin";
export const PASSWORD = process.env.E2E_PASSWORD ?? "";

export async function login(page: Page): Promise<void> {
  expect(PASSWORD, "E2E_PASSWORD must be set to the admin password").not.toBe("");
  await page.goto("/");
  await page.getByLabel("Username").fill(USERNAME);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Overview", level: 1 })).toBeVisible();
}

/** Parses "12.3 Mbit/s" style text into bits per second. */
export function parseBps(text: string): number | null {
  const m = text.match(/([\d.]+)\s*(G|M|k)?bit\/s/);
  if (!m) return null;
  const mult = m[2] === "G" ? 1e9 : m[2] === "M" ? 1e6 : m[2] === "k" ? 1e3 : 1;
  return Number(m[1]) * mult;
}
