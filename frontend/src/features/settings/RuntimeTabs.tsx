import { Radar, Rocket, ShieldCheck, Timer } from "lucide-react";
import { useState, type FormEvent } from "react";

import { Switch } from "@/components/ui/overlays";
import { Button, Card, CardBody, CardHeader, ErrorState, Field, Input, Notice, Skeleton } from "@/components/ui/primitives";
import { Badge, Timestamp } from "@/components/ui/status";
import { FormError, SavedFlash } from "@/features/alerts/shared";
import { ListEditor, NumberGrid, ReadOnlyNote, SaveRow } from "@/features/settings/shared";
import { DEFAULTS, fromDraft, numbersValid, toDraft, useRuntimeSettings, validateCidrShape, validatePortEntry, type FullSettings, type NumSpec } from "@/features/settings/model";
import { useAuth } from "@/hooks/useAuth";

type SaveFn = ReturnType<typeof useRuntimeSettings>["save"];

/** Confirmation state lives above the forms: a form re-initialises from the saved
 *  document after every successful save, and the "Saved" notice must survive that. */
interface Flash {
  at: (kind: string) => number | null;
  mark: (kind: string) => void;
}

function SettingsLoader({ children }: { children: (settings: FullSettings, save: SaveFn, version: number, flash: Flash) => React.ReactNode }) {
  const { query, settings, save, version } = useRuntimeSettings();
  const [flashed, setFlashed] = useState<{ kind: string; at: number } | null>(null);
  if (query.isLoading) return <Skeleton className="h-64 w-full" />;
  if (query.isError || !settings) return <ErrorState error={query.error} retry={() => void query.refetch()} />;
  const flash: Flash = {
    at: (kind) => (flashed?.kind === kind ? flashed.at : null),
    mark: (kind) => setFlashed({ kind, at: Date.now() }),
  };
  return <>{children(settings, save, version, flash)}</>;
}

// ---------------------------------------------------------------- collection / retention / freshness

const COLLECTION: NumSpec[] = [
  { key: "sample_interval_s", label: "Counter sample interval", unit: "s", min: 1, max: 60, hint: "How often agents read interface counters.", defaultValue: DEFAULTS.collection.sample_interval_s },
  { key: "inventory_interval_s", label: "Inventory interval", unit: "s", min: 10, max: 3600, hint: "Interfaces and neighbour cache.", defaultValue: DEFAULTS.collection.inventory_interval_s },
  { key: "posture_interval_s", label: "Posture snapshot interval", unit: "s", min: 30, max: 86400, hint: "Listening sockets and other read-only checks.", defaultValue: DEFAULTS.collection.posture_interval_s },
];
const RETENTION: NumSpec[] = [
  { key: "raw_hours", label: "Raw 1-second samples", unit: "h", min: 1, max: 336, hint: "Dropped a whole day-partition at a time.", defaultValue: DEFAULTS.retention.raw_hours },
  { key: "rollup_1m_days", label: "1-minute rollups", unit: "d", min: 1, max: 730, defaultValue: DEFAULTS.retention.rollup_1m_days },
  { key: "rollup_1h_days", label: "Hourly rollups", unit: "d", min: 7, max: 3650, defaultValue: DEFAULTS.retention.rollup_1h_days },
  { key: "probe_results_hours", label: "Individual probe results", unit: "h", min: 1, max: 720, defaultValue: DEFAULTS.retention.probe_results_hours },
  { key: "events_days", label: "Events & timeline", unit: "d", min: 7, max: 3650, defaultValue: DEFAULTS.retention.events_days },
  { key: "posture_snapshots_days", label: "Posture snapshots", unit: "d", min: 1, max: 3650, hint: "Snapshots referenced as finding evidence are kept regardless.", defaultValue: DEFAULTS.retention.posture_snapshots_days },
];
const FRESHNESS: NumSpec[] = [
  { key: "stale_after_s", label: "Mark data stale after", unit: "s", min: 3, max: 3600, defaultValue: DEFAULTS.freshness.stale_after_s },
  { key: "disconnected_after_s", label: "Mark source disconnected after", unit: "s", min: 10, max: 86400, hint: "Values are hidden, not shown as zero, once a source is disconnected.", defaultValue: DEFAULTS.freshness.disconnected_after_s },
  { key: "device_seen_recently_s", label: "Device “seen recently” window", unit: "s", min: 30, max: 604800, hint: "After this a device is “not seen recently” — which is not the same as offline.", defaultValue: DEFAULTS.freshness.device_seen_recently_s },
];

function CollectionForm({ settings, save, flash }: { settings: FullSettings; save: SaveFn; flash: Flash }) {
  const { isAdmin } = useAuth();
  const [collection, setCollection] = useState(() => toDraft(settings.collection));
  const [retention, setRetention] = useState(() => toDraft(settings.retention));
  const [freshness, setFreshness] = useState(() => toDraft(settings.freshness));
  const valid = numbersValid(COLLECTION, collection) && numbersValid(RETENTION, retention) && numbersValid(FRESHNESS, freshness);
  const ordered = valid && Number(freshness.disconnected_after_s) > Number(freshness.stale_after_s);
  const disabled = !isAdmin || save.isPending;

  function submit(e: FormEvent) {
    e.preventDefault();
    save.mutate(
      {
        ...settings,
        collection: fromDraft(settings.collection, collection, COLLECTION.map((s) => s.key)),
        retention: fromDraft(settings.retention, retention, RETENTION.map((s) => s.key)),
        freshness: fromDraft(settings.freshness, freshness, FRESHNESS.map((s) => s.key)),
      },
      { onSuccess: () => flash.mark("saved") },
    );
  }

  return (
    <form onSubmit={submit} className="space-y-4" aria-label="Collection, retention and freshness settings">
      {!isAdmin && <ReadOnlyNote />}
      <Card>
        <CardHeader icon={<Timer className="h-4 w-4" />} title="Collection intervals" subtitle="Applied to every agent on its next configuration poll (within ~10 s)." />
        <CardBody>
          <NumberGrid idPrefix="col" specs={COLLECTION} draft={collection} onChange={(k, v) => setCollection((d) => ({ ...d, [k]: v }))} disabled={disabled} />
        </CardBody>
      </Card>
      <Card>
        <CardHeader
          title="Retention"
          subtitle="Bounds database growth. Raw samples feed 1-minute rollups, which feed hourly rollups; charts read the coarsest table that satisfies the requested period."
        />
        <CardBody>
          <NumberGrid idPrefix="ret" specs={RETENTION} draft={retention} onChange={(k, v) => setRetention((d) => ({ ...d, [k]: v }))} disabled={disabled} />
          <p className="mt-3 text-[11px] leading-relaxed text-faint">
            Shortening retention deletes older data on the next maintenance run (every 15 minutes) and cannot be undone.
          </p>
        </CardBody>
      </Card>
      <Card>
        <CardHeader title="Freshness thresholds" subtitle="When a value is labelled stale or its source disconnected, and how long a passive sighting counts as recent." />
        <CardBody>
          <NumberGrid idPrefix="fresh" specs={FRESHNESS} draft={freshness} onChange={(k, v) => setFreshness((d) => ({ ...d, [k]: v }))} disabled={disabled} />
          {valid && !ordered && (
            <p role="alert" className="mt-3 text-xs text-crit">
              “Disconnected after” must be longer than “stale after”.
            </p>
          )}
        </CardBody>
      </Card>
      {isAdmin && (
        <div className="flex flex-wrap items-center gap-3">
          <Button type="submit" variant="primary" loading={save.isPending} disabled={!ordered}>
            Save changes
          </Button>
          <SavedFlash at={flash.at("saved")} />
          <FormError error={save.error} />
        </div>
      )}
    </form>
  );
}

export function CollectionTab() {
  return <SettingsLoader>{(s, save, v, flash) => <CollectionForm key={v} settings={s} save={save} flash={flash} />}</SettingsLoader>;
}

// ---------------------------------------------------------------- network scope

const DISCOVERY: NumSpec[] = [
  { key: "interval_minutes", label: "Sweep interval", unit: "min", min: 15, max: 1440, defaultValue: 60 },
  { key: "max_probes_per_second", label: "Maximum probe rate", unit: "probes/s", min: 0.1, max: 10, step: 0.1, defaultValue: 2 },
];
const SPEEDTEST: NumSpec[] = [
  { key: "max_bytes", label: "Maximum download size", unit: "bytes", min: 1_000_000, max: 500_000_000, hint: "The test stops at this size or after 15 s.", defaultValue: 25_000_000 },
  { key: "interval_hours", label: "Scheduled interval", unit: "h", min: 3, max: 168, step: 0.5, hint: "Never more often than every 3 h.", defaultValue: 6 },
];

function ScopeForm({ settings, save, flash }: { settings: FullSettings; save: SaveFn; flash: Flash }) {
  const { isAdmin } = useAuth();
  const [cidrs, setCidrs] = useState(settings.approved_cidrs);
  const [discEnabled, setDiscEnabled] = useState(settings.discovery.enabled);
  const [discCidrs, setDiscCidrs] = useState(settings.discovery.cidrs);
  const [disc, setDisc] = useState(() => toDraft({ interval_minutes: settings.discovery.interval_minutes, max_probes_per_second: settings.discovery.max_probes_per_second }));
  const [stEnabled, setStEnabled] = useState(settings.speedtest.enabled);
  const [stUrl, setStUrl] = useState(settings.speedtest.download_url);
  const [st, setSt] = useState(() => toDraft({ max_bytes: settings.speedtest.max_bytes, interval_hours: settings.speedtest.interval_hours }));
  const disabled = !isAdmin || save.isPending;

  const urlError = stUrl && !stUrl.startsWith("https://") ? "The URL must start with https://." : stEnabled && !stUrl ? "A download URL is required to enable the test." : null;
  const discError = discEnabled && discCidrs.length === 0 ? "Add at least one sweep range before enabling discovery." : null;
  const valid = numbersValid(DISCOVERY, disc) && numbersValid(SPEEDTEST, st) && !urlError && !discError;

  function build(requested_at: string | null | undefined): FullSettings {
    return {
      ...settings,
      approved_cidrs: cidrs,
      discovery: { enabled: discEnabled, cidrs: discCidrs, interval_minutes: Number(disc.interval_minutes), max_probes_per_second: Number(disc.max_probes_per_second) },
      speedtest: { enabled: stEnabled, download_url: stUrl.trim(), max_bytes: Number(st.max_bytes), interval_hours: Number(st.interval_hours), requested_at },
    };
  }

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate(build(settings.speedtest.requested_at), { onSuccess: () => flash.mark("saved") });
      }}
      className="space-y-4"
      aria-label="Network scope settings"
    >
      {!isAdmin && <ReadOnlyNote />}
      <Card>
        <CardHeader
          icon={<ShieldCheck className="h-4 w-4" />}
          title="Approved network scope"
          subtitle="Your own private ranges. Gateway and internal probe targets, and any discovery sweep, must fall inside one of them."
        />
        <CardBody>
          <ListEditor
            id="approved-cidrs"
            label="Approved CIDRs"
            values={cidrs}
            onChange={setCidrs}
            disabled={disabled}
            max={16}
            placeholder="192.168.1.0/24"
            validate={validateCidrShape}
            emptyText="No ranges approved. The automatic default-gateway probe still works; other internal targets and discovery are refused."
            hint="Private LAN space only (RFC 1918 / IPv6 ULA), /16 or smaller, with host bits zero. Public, loopback, link-local and carrier-NAT ranges are rejected by the server."
          />
        </CardBody>
      </Card>

      <Card>
        <CardHeader
          icon={<Radar className="h-4 w-4" />}
          title="Active device discovery (opt-in)"
          subtitle="Off by default. ObserveNet is passive unless you turn this on."
          actions={
            <div className="flex items-center gap-2 text-xs text-muted">
              <Badge tone={discEnabled ? "warn" : "neutral"}>{discEnabled ? "Enabled" : "Disabled"}</Badge>
              <Switch checked={discEnabled} onCheckedChange={setDiscEnabled} disabled={disabled} label="Enable active device discovery" />
            </div>
          }
        />
        <CardBody className="space-y-4">
          <Notice tone="info">
            When enabled, an agent sends low-rate ICMP echo requests to each address in the ranges below so that quiet devices show up in its
            neighbour cache. It only sweeps private ranges that are inside the approved scope <em>and</em> that the agent's host is directly
            attached to, at most 1,024 addresses per range, never public addresses, and never port scans. Results appear as ordinary
            neighbour-cache sightings.
          </Notice>
          <ListEditor
            id="discovery-cidrs"
            label="Sweep ranges"
            values={discCidrs}
            onChange={setDiscCidrs}
            disabled={disabled}
            max={4}
            placeholder="192.168.1.0/24"
            validate={validateCidrShape}
            emptyText="No sweep ranges."
            hint="IPv4, inside an approved CIDR, /22 or smaller (≤ 1,024 addresses). Up to 4 ranges."
          />
          <NumberGrid idPrefix="disc" specs={DISCOVERY} draft={disc} onChange={(k, v) => setDisc((d) => ({ ...d, [k]: v }))} disabled={disabled} />
          {discError && (
            <p role="alert" className="text-xs text-crit">
              {discError}
            </p>
          )}
        </CardBody>
      </Card>

      <Card>
        <CardHeader
          icon={<Rocket className="h-4 w-4" />}
          title="Internet capacity test (opt-in)"
          subtitle="Off by default. A capacity test is a different metric from utilization and briefly uses your connection."
          actions={
            <div className="flex items-center gap-2 text-xs text-muted">
              <Badge tone={stEnabled ? "warn" : "neutral"}>{stEnabled ? "Enabled" : "Disabled"}</Badge>
              <Switch checked={stEnabled} onCheckedChange={setStEnabled} disabled={disabled} label="Enable capacity test" />
            </div>
          }
        />
        <CardBody className="space-y-4">
          <Notice tone="info">
            The agent downloads a bounded payload from an HTTPS URL you choose and reports the single-stream rate as “tested download
            capacity” — a lower bound, shown separately from every-second throughput. Upload capacity is not measured. Scheduled runs are
            limited to once every 3 hours or less often.
          </Notice>
          <Field label="Download URL (HTTPS)" htmlFor="st-url" error={urlError} hint="A large static file on a server you trust. No default is provided, so nothing is contacted unless you set one.">
            <Input id="st-url" type="url" inputMode="url" value={stUrl} placeholder="https://example.net/100MB.bin" disabled={disabled} onChange={(e) => setStUrl(e.target.value)} />
          </Field>
          <NumberGrid idPrefix="st" specs={SPEEDTEST} draft={st} onChange={(k, v) => setSt((d) => ({ ...d, [k]: v }))} disabled={disabled} />
          {isAdmin && (
            <div className="flex flex-wrap items-center gap-3">
              <Button
                disabled={!valid || !stEnabled || !stUrl || save.isPending}
                onClick={() => save.mutate(build(new Date().toISOString()), { onSuccess: () => flash.mark("requested") })}
              >
                Save & run once now
              </Button>
              <SavedFlash at={flash.at("requested")}>Requested — the agent runs it on its next config poll (at most once per 10 min)</SavedFlash>
              {settings.speedtest.requested_at && (
                <span className="text-xs text-faint">
                  Last on-demand request <Timestamp ts={settings.speedtest.requested_at} />
                </span>
              )}
            </div>
          )}
        </CardBody>
      </Card>

      {isAdmin && (
        <div className="flex flex-wrap items-center gap-3">
          <Button type="submit" variant="primary" loading={save.isPending} disabled={!valid}>
            Save changes
          </Button>
          <SavedFlash at={flash.at("saved")} />
          <FormError error={save.error} />
        </div>
      )}
    </form>
  );
}

export function ScopeTab() {
  return <SettingsLoader>{(s, save, v, flash) => <ScopeForm key={v} settings={s} save={save} flash={flash} />}</SettingsLoader>;
}

// ---------------------------------------------------------------- posture

const POSTURE: NumSpec[] = [
  { key: "verify_timeout_s", label: "Verification deadline", unit: "s", min: 15, max: 900, hint: "If no fresh evidence arrives in time, a re-check ends as Unknown — never as Resolved.", defaultValue: 90 },
];

function PostureForm({ settings, save, flash }: { settings: FullSettings; save: SaveFn; flash: Flash }) {
  const { isAdmin } = useAuth();
  const [listeners, setListeners] = useState(settings.posture.listener_allowlist);
  const [docker, setDocker] = useState(settings.posture.docker_port_allowlist);
  const [windows, setWindows] = useState(settings.posture.windows_listener_allowlist);
  const [nums, setNums] = useState(() => toDraft({ verify_timeout_s: settings.posture.verify_timeout_s }));
  const disabled = !isAdmin || save.isPending;
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate(
          { ...settings, posture: { listener_allowlist: listeners, docker_port_allowlist: docker, windows_listener_allowlist: windows, verify_timeout_s: Number(nums.verify_timeout_s) } },
          { onSuccess: () => flash.mark("saved") },
        );
      }}
      className="space-y-4"
      aria-label="Security posture settings"
    >
      {!isAdmin && <ReadOnlyNote />}
      <Card>
        <CardHeader
          icon={<ShieldCheck className="h-4 w-4" />}
          title="Expected listeners"
          subtitle="Services that are meant to listen on all interfaces. Allowlisted entries do not raise a finding; an existing finding for them resolves on the next snapshot."
        />
        <CardBody className="space-y-5">
          <ListEditor
            id="listener-allowlist"
            label="Linux listener allowlist"
            values={listeners}
            onChange={setListeners}
            disabled={disabled}
            placeholder="tcp/22"
            validate={validatePortEntry}
            emptyText="Nothing allowlisted: every wildcard listener is reported."
            hint="Format proto/port, for example tcp/22 or udp/5353. Applies to Linux hosts running an agent."
          />
          <ListEditor
            id="windows-listener-allowlist"
            label="Windows listener allowlist"
            values={windows}
            onChange={setWindows}
            disabled={disabled}
            placeholder="tcp/445"
            validate={validatePortEntry}
            emptyText="Nothing allowlisted: every wildcard listener on Windows hosts is reported."
            hint="Defaults cover listeners every stock Windows installation has (RPC, NetBIOS/SMB, mDNS/LLMNR/SSDP, WS-Discovery). SMB exposure is judged by the firewall and SMBv1 checks instead. These defaults were not validated on a real Windows host."
          />
          <ListEditor
            id="docker-allowlist"
            label="Docker published-port allowlist"
            values={docker}
            onChange={setDocker}
            disabled={disabled}
            placeholder="tcp/8080"
            validate={validatePortEntry}
            emptyText="Nothing allowlisted."
            hint="Host ports that containers may publish on all interfaces. Only evaluated when the opt-in Docker inventory collector is enabled."
          />
        </CardBody>
      </Card>
      <Card>
        <CardHeader title="Verification" subtitle="How long a manual “Verify fix” waits for evidence collected after the request." />
        <CardBody>
          <NumberGrid idPrefix="posture" specs={POSTURE} draft={nums} onChange={(k, v) => setNums((d) => ({ ...d, [k]: v }))} disabled={disabled} />
        </CardBody>
      </Card>
      {isAdmin && (
        <SaveRow>
          <Button type="submit" variant="primary" loading={save.isPending} disabled={!numbersValid(POSTURE, nums)}>
            Save changes
          </Button>
          <SavedFlash at={flash.at("saved")} />
          <FormError error={save.error} />
        </SaveRow>
      )}
    </form>
  );
}

export function PostureTab() {
  return <SettingsLoader>{(s, save, v, flash) => <PostureForm key={v} settings={s} save={save} flash={flash} />}</SettingsLoader>;
}
