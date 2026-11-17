/**
 * Typed same-origin API client.
 * - Types come from the backend's OpenAPI document (`npm run gen:api`).
 * - Session = HttpOnly cookie (sent automatically); mutations add the CSRF header.
 */
import type { components, paths } from "@/types/openapi";

export type Schemas = components["schemas"];
export type Paths = paths;

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public detail?: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

let csrfToken: string | null = null;
let onUnauthorized: (() => void) | null = null;

export function setCsrfToken(token: string | null): void {
  csrfToken = token;
}

export function setUnauthorizedHandler(fn: (() => void) | null): void {
  onUnauthorized = fn;
}

function cookieCsrf(): string | null {
  const m = document.cookie.match(/(?:^|;\s*)observenet_csrf=([^;]+)/);
  return m?.[1] ? decodeURIComponent(m[1]) : null;
}

function detailMessage(detail: unknown, fallback: string): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((d: { loc?: unknown[]; msg?: string }) => `${(d.loc ?? []).slice(1).join(".")}: ${d.msg ?? "invalid"}`)
      .join("; ");
  }
  return fallback;
}

export async function request<T>(method: string, url: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (method !== "GET") {
    const token = csrfToken ?? cookieCsrf();
    if (token) headers["X-CSRF-Token"] = token;
  }
  let resp: Response;
  try {
    resp = await fetch(url, {
      method,
      headers,
      credentials: "same-origin",
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, "Cannot reach the ObserveNet backend.");
  }
  if (resp.status === 204) return undefined as T;
  const isJson = resp.headers.get("content-type")?.includes("application/json");
  const payload: unknown = isJson ? await resp.json().catch(() => null) : null;
  if (!resp.ok) {
    if (resp.status === 401 && !url.endsWith("/auth/login")) onUnauthorized?.();
    const detail = (payload as { detail?: unknown } | null)?.detail;
    throw new ApiError(resp.status, detailMessage(detail, `Request failed (${resp.status})`), detail);
  }
  return payload as T;
}

type Query = Record<string, string | number | boolean | null | undefined>;

export function qs(params?: Query): string {
  if (!params) return "";
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "") sp.set(k, String(v));
  }
  const s = sp.toString();
  return s ? `?${s}` : "";
}

const base = "/api/v1";
export const api = {
  get: <T>(path: string, params?: Query) => request<T>("GET", `${base}${path}${qs(params)}`),
  post: <T>(path: string, body?: unknown) => request<T>("POST", `${base}${path}`, body ?? {}),
  put: <T>(path: string, body: unknown) => request<T>("PUT", `${base}${path}`, body),
  patch: <T>(path: string, body: unknown) => request<T>("PATCH", `${base}${path}`, body),
  delete: <T>(path: string) => request<T>("DELETE", `${base}${path}`),
};

export async function graphql<T>(query: string, variables?: Record<string, unknown>): Promise<T> {
  const res = await request<{ data?: T; errors?: { message: string }[] }>("POST", "/graphql", { query, variables });
  if (res.errors?.length) throw new ApiError(400, res.errors.map((e) => e.message).join("; "));
  return res.data as T;
}

// ---- response types used across pages (aliases of generated schemas) ----
export type Meta = Schemas["Meta"];
export type Me = Schemas["MeOut"];
export type Kpi = Schemas["Kpi"];
export type Overview = Schemas["OverviewOut"];
export type Device = Schemas["DeviceOut"];
export type DevicePage = Schemas["DevicePage"];
export type DeviceDetail = Schemas["DeviceDetail"];
export type Finding = Schemas["FindingOut"];
export type FindingDetail = Schemas["FindingDetail"];
export type Verification = Schemas["VerificationOut"];
export type Alert = Schemas["AlertOut"];
export type AlertRule = Schemas["AlertRuleOut"];
export type EventRow = Schemas["EventOut"];
export type Source = Schemas["SourceOut"];
export type SeriesMeta = Schemas["SeriesOut"];
export type History = Schemas["HistoryOut"];
export type HistoryPoint = Schemas["HistoryPoint"];
export type CurrentValue = Schemas["CurrentValue"];
export type Connectivity = Schemas["ConnectivityOut"];
export type TargetSummary = Schemas["TargetSummary"];
export type ProbeTarget = Schemas["ProbeTargetOut"];
export type ProbeTargetIn = Schemas["ProbeTargetIn"];
export type Capabilities = Schemas["CapabilitiesOut"];
export type NetCapability = Schemas["NetCapability"];
export type StatusOut = Schemas["StatusOut"];
export type RuntimeSettings = Schemas["RuntimeSettings"];
export type Token = Schemas["TokenOut"];
export type User = Schemas["UserOut"];
export type AuditRow = Schemas["AuditOut"];
export type Maintenance = Schemas["MaintenanceOut"];
