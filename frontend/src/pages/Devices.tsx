import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, ChevronLeft, ChevronRight, Download, Info, MonitorSmartphone, Search, Upload } from "lucide-react";
import { Fragment, useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { Switch } from "@/components/ui/overlays";
import { Button, Card, EmptyState, ErrorState, Input, NativeSelect, Notice, PageHeader, Skeleton, Table, Td, Th } from "@/components/ui/primitives";
import { Badge, DemoTag, PresenceBadge, Timestamp } from "@/components/ui/status";
import { ConfidenceBadge, FavoriteButton, Mono, RandomizedMacBadge, VendorCell } from "@/features/devices/bits";
import { ImportDialog } from "@/features/devices/ImportDialog";
import { RelationshipsPanel } from "@/features/devices/RelationshipsPanel";
import { ASSET_TYPES } from "@/features/devices/types";
import { useAuth } from "@/hooks/useAuth";
import { api, type Device, type DevicePage } from "@/lib/api";
import { cn } from "@/lib/cn";
import { titleCase } from "@/lib/format";

const PAGE_SIZE = 25;
const EXPORT_LINK =
  "inline-flex h-9 items-center justify-center gap-1.5 rounded-lg border border-line bg-raised px-3.5 text-sm text-ink transition-colors hover:border-faint hover:bg-raised/70";

function useDebounced<T>(value: T, ms: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const id = setTimeout(() => setDebounced(value), ms);
    return () => clearTimeout(id);
  }, [value, ms]);
  return debounced;
}

export function DevicesPage() {
  const { isAdmin } = useAuth();
  const qc = useQueryClient();
  const [search, setSearch] = useState("");
  const [assetType, setAssetType] = useState("");
  const [managed, setManaged] = useState("");
  const [favoritesOnly, setFavoritesOnly] = useState(false);
  const [offset, setOffset] = useState(0);
  const [expanded, setExpanded] = useState<number | null>(null);
  const [importOpen, setImportOpen] = useState(false);
  const q = useDebounced(search.trim(), 300);

  useEffect(() => setOffset(0), [q, assetType, managed, favoritesOnly]);

  const params = {
    q: q || undefined,
    asset_type: assetType || undefined,
    managed: managed === "" ? undefined : managed === "yes",
    favorite: favoritesOnly ? true : undefined,
    limit: PAGE_SIZE,
    offset,
  };
  const devices = useQuery({
    queryKey: ["devices", "list", params],
    queryFn: () => api.get<DevicePage>("/devices", params),
    refetchInterval: 30_000,
    placeholderData: keepPreviousData,
  });
  const favorite = useMutation({
    mutationFn: (d: Device) => api.patch<Device>(`/devices/${d.id}`, { favorite: !d.favorite }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["devices"] }),
  });

  const page = devices.data;
  const filtered = !!(q || assetType || managed || favoritesOnly);
  const from = page && page.total ? page.offset + 1 : 0;
  const to = page ? Math.min(page.offset + page.items.length, page.total) : 0;

  return (
    <div className="space-y-4">
      <PageHeader
        title="Devices"
        description="Every device ObserveNet has evidence for, with how it was identified, how confident that identity is, and which source last saw it."
        actions={
          isAdmin && (
            <>
              <Button onClick={() => setImportOpen(true)}>
                <Upload className="h-3.5 w-3.5" aria-hidden /> Import client list
              </Button>
              <a className={EXPORT_LINK} href="/api/v1/export/devices?format=csv" download>
                <Download className="h-3.5 w-3.5" aria-hidden /> CSV
              </a>
              <a className={EXPORT_LINK} href="/api/v1/export/devices?format=json" download>
                <Download className="h-3.5 w-3.5" aria-hidden /> JSON
              </a>
            </>
          )
        }
      />

      {page?.coverage_note && (
        <Notice tone="info" icon={<Info className="h-4 w-4" />}>
          {page.coverage_note}
        </Notice>
      )}

      <Card>
        <div className="flex flex-wrap items-end gap-3 border-b border-line px-4 py-3">
          <div className="relative min-w-[220px] flex-1">
            <label htmlFor="device-search" className="sr-only">
              Search devices
            </label>
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-faint" aria-hidden />
            <Input id="device-search" type="search" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search name, IP, MAC or vendor" className="pl-9" />
          </div>
          <div>
            <label htmlFor="device-type" className="sr-only">
              Filter by type
            </label>
            <NativeSelect id="device-type" value={assetType} onChange={(e) => setAssetType(e.target.value)} className="w-40">
              <option value="">All types</option>
              {ASSET_TYPES.map((t) => (
                <option key={t} value={t}>
                  {titleCase(t)}
                </option>
              ))}
            </NativeSelect>
          </div>
          <div>
            <label htmlFor="device-managed" className="sr-only">
              Filter by agent
            </label>
            <NativeSelect id="device-managed" value={managed} onChange={(e) => setManaged(e.target.value)} className="w-56">
              <option value="">Any agent state</option>
              <option value="yes">Has an agent</option>
              <option value="no">No agent (passive only)</option>
            </NativeSelect>
          </div>
          <div className="flex h-9 items-center gap-2 text-xs text-muted">
            <Switch id="fav-only" checked={favoritesOnly} onCheckedChange={setFavoritesOnly} label="Favorites only" />
            <label htmlFor="fav-only">Favorites only</label>
          </div>
        </div>

        {devices.isLoading ? (
          <div className="space-y-2 p-4">
            {[0, 1, 2, 3].map((i) => (
              <Skeleton key={i} className="h-10 w-full" />
            ))}
          </div>
        ) : devices.isError ? (
          <ErrorState error={devices.error} retry={() => void devices.refetch()} />
        ) : page && page.items.length ? (
          <Table className={cn(devices.isPlaceholderData && "opacity-60")}>
            <caption className="sr-only">Known devices</caption>
            <thead>
              <tr>
                <Th className="w-8">
                  <span className="sr-only">Favorite</span>
                </Th>
                <Th>Device</Th>
                <Th>Type</Th>
                <Th>IP / MAC</Th>
                <Th>Vendor</Th>
                <Th>Presence</Th>
                <Th>Identity</Th>
                <Th>Last seen</Th>
                <Th className="text-right">Findings</Th>
                <Th className="w-8">
                  <span className="sr-only">Relationships</span>
                </Th>
              </tr>
            </thead>
            <tbody>
              {page.items.map((d) => {
                const open = expanded === d.id;
                return (
                  <Fragment key={d.id}>
                    <tr className="hover:bg-raised/40">
                      <Td className="pr-0">
                        <FavoriteButton
                          name={d.name}
                          favorite={d.favorite}
                          onToggle={isAdmin ? () => favorite.mutate(d) : undefined}
                          disabled={favorite.isPending}
                        />
                      </Td>
                      <Td>
                        <Link to={`/devices/${d.id}`} className="font-medium text-ink hover:text-accent">
                          {d.name}
                        </Link>
                        <div className="mt-0.5 flex flex-wrap items-center gap-1.5 text-xs text-faint">
                          {d.display_name && d.hostname && d.hostname !== d.display_name && <span>host: {d.hostname}</span>}
                          {d.managed && <Badge tone="info">Agent</Badge>}
                          {d.is_demo && <DemoTag />}
                          {d.tags.slice(0, 3).map((t) => (
                            <Badge key={t}>{t}</Badge>
                          ))}
                        </div>
                      </Td>
                      <Td className="text-xs text-muted">
                        {titleCase(d.asset_type)}
                        {d.group_name && <div className="text-faint">{d.group_name}</div>}
                      </Td>
                      <Td>
                        <Mono className="block text-ink">{d.primary_ip ?? "—"}</Mono>
                        <Mono className="block text-faint">{d.primary_mac ?? "no MAC seen"}</Mono>
                      </Td>
                      <Td className="text-xs">
                        <VendorCell vendor={d.vendor} method={d.vendor_method} />
                      </Td>
                      <Td>
                        <PresenceBadge presence={d.presence} />
                      </Td>
                      <Td>
                        <div className="flex flex-col items-start gap-1">
                          <ConfidenceBadge confidence={d.confidence} reason={d.confidence_reason} />
                          {d.mac_randomized && <RandomizedMacBadge />}
                        </div>
                      </Td>
                      <Td className="text-xs text-muted">
                        <Timestamp ts={d.last_seen} />
                        {d.last_seen_source && <div className="text-faint">via {d.last_seen_source}</div>}
                      </Td>
                      <Td className="num text-right">
                        {d.open_findings ? (
                          <Link to={`/devices/${d.id}?tab=findings`} className="font-medium text-warn hover:underline">
                            {d.open_findings}
                          </Link>
                        ) : (
                          <span className="text-faint">0</span>
                        )}
                      </Td>
                      <Td className="pl-0">
                        <button
                          type="button"
                          onClick={() => setExpanded(open ? null : d.id)}
                          aria-expanded={open}
                          aria-controls={`rel-${d.id}`}
                          aria-label={`${open ? "Hide" : "Show"} relationships for ${d.name}`}
                          className="rounded-md p-1 text-muted hover:bg-raised hover:text-ink"
                        >
                          <ChevronDown className={cn("h-4 w-4 transition-transform", open && "rotate-180")} aria-hidden />
                        </button>
                      </Td>
                    </tr>
                    {open && (
                      <tr id={`rel-${d.id}`}>
                        <td colSpan={10} className="border-b border-line/50 bg-bg/40 px-5 py-4">
                          <RelationshipsPanel deviceId={d.id} />
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </Table>
        ) : filtered ? (
          <EmptyState icon={<Search className="h-5 w-5" />} title="No device matches these filters">
            Clear the search or filters to see everything that has been observed.
          </EmptyState>
        ) : (
          <EmptyState
            icon={<MonitorSmartphone className="h-5 w-5" />}
            title="No devices observed yet"
            action={
              isAdmin ? (
                <Button size="sm" onClick={() => setImportOpen(true)}>
                  Import client list
                </Button>
              ) : undefined
            }
          >
            This list is empty because nothing has been observed — ObserveNet does not invent devices. Entries appear when an agent reports its
            neighbour cache, when an agent registers itself, or when you import your router's client list. See{" "}
            <Link to="/settings/sources" className="text-accent hover:underline">
              Settings → Data sources
            </Link>
            .
          </EmptyState>
        )}

        {page && page.total > 0 && (
          <div className="flex items-center justify-between gap-3 px-4 py-3 text-xs text-muted">
            <p className="num" aria-live="polite">
              {from}–{to} of {page.total} device(s)
            </p>
            <div className="flex items-center gap-2">
              <Button size="sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>
                <ChevronLeft className="h-3.5 w-3.5" aria-hidden /> Previous
              </Button>
              <Button size="sm" disabled={to >= page.total} onClick={() => setOffset(offset + PAGE_SIZE)}>
                Next <ChevronRight className="h-3.5 w-3.5" aria-hidden />
              </Button>
            </div>
          </div>
        )}
      </Card>

      <ImportDialog open={importOpen} onOpenChange={setImportOpen} />
    </div>
  );
}
