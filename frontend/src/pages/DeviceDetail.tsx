import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, GitMerge, Pencil, Undo2 } from "lucide-react";
import { useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";

import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/overlays";
import { Button, Card, CardBody, CardHeader, EmptyState, ErrorState, Notice, Spinner, Table, Td, Th } from "@/components/ui/primitives";
import { Badge, ClassificationBadge, DemoTag, PresenceBadge, SeverityBadge, StatusBadge, Timestamp } from "@/components/ui/status";
import { ActivityCharts } from "@/features/devices/ActivityCharts";
import { ConfidenceBadge, DefinitionList, FavoriteButton, Hint, Mono, RandomizedMacBadge, VendorCell } from "@/features/devices/bits";
import { EditDeviceDialog } from "@/features/devices/EditDeviceDialog";
import { MergeDialog } from "@/features/devices/MergeDialog";
import { RelationshipsPanel } from "@/features/devices/RelationshipsPanel";
import { EVIDENCE_LABELS, IDENTITY_LABELS, type AddressRow, type IdentityRow, type InterfaceRow, type MergeRow, type ServiceRow } from "@/features/devices/types";
import { useAuth } from "@/hooks/useAuth";
import { api, ApiError, type Alert, type Device, type DeviceDetail, type EventRow, type Finding } from "@/lib/api";
import { cn } from "@/lib/cn";
import { formatDateTime, titleCase } from "@/lib/format";

const TABS = ["summary", "addresses", "interfaces", "activity", "findings", "alerts", "history", "identity"] as const;

export function DeviceDetailPage() {
  const { id } = useParams();
  const deviceId = Number(id);
  const { isAdmin } = useAuth();
  const qc = useQueryClient();
  const [search, setSearch] = useSearchParams();
  const [editOpen, setEditOpen] = useState(false);
  const [mergeOpen, setMergeOpen] = useState(false);
  const requested = search.get("tab") ?? "summary";
  const tab = (TABS as readonly string[]).includes(requested) ? requested : "summary";

  const detail = useQuery({
    queryKey: ["device", deviceId],
    queryFn: () => api.get<DeviceDetail>(`/devices/${deviceId}`),
    enabled: Number.isFinite(deviceId),
    refetchInterval: 30_000,
  });
  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: ["device", deviceId] });
    void qc.invalidateQueries({ queryKey: ["devices"] });
  };
  const favorite = useMutation({
    mutationFn: (d: Device) => api.patch<Device>(`/devices/${d.id}`, { favorite: !d.favorite }),
    onSuccess: invalidate,
  });
  const undo = useMutation({
    mutationFn: (mergeId: number) => api.post<{ merge_id: number; restored_asset_id: number }>(`/devices/merges/${mergeId}/undo`),
    onSuccess: invalidate,
  });

  const back = (
    <Link to="/devices" className="mb-3 inline-flex items-center gap-1.5 text-xs text-muted hover:text-ink">
      <ArrowLeft className="h-3.5 w-3.5" aria-hidden /> All devices
    </Link>
  );

  if (!Number.isFinite(deviceId) || (detail.error instanceof ApiError && detail.error.status === 404)) {
    return (
      <div>
        {back}
        <Card>
          <EmptyState title="Device not found">
            No device has this ID. It may have been removed, or the link is wrong.
          </EmptyState>
        </Card>
      </div>
    );
  }
  if (detail.isLoading) return <Spinner label="Loading device" />;
  if (detail.isError || !detail.data) return <ErrorState error={detail.error} retry={() => void detail.refetch()} />;

  const data = detail.data;
  const d = data.device;
  const identities = data.identities as unknown as IdentityRow[];
  const addresses = data.addresses as unknown as AddressRow[];
  const interfaces = data.interfaces as unknown as InterfaceRow[];
  const services = data.services as unknown as ServiceRow[];
  const findings = data.findings as unknown as Finding[];
  const alerts = data.alerts as unknown as Alert[];
  const events = data.events as unknown as EventRow[];
  const merges = data.merges as unknown as MergeRow[];
  const activeFindings = findings.filter((f) => ["open", "acknowledged", "snoozed"].includes(f.status));

  return (
    <div className="space-y-4">
      <div>
        {back}
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <FavoriteButton name={d.name} favorite={d.favorite} onToggle={isAdmin ? () => favorite.mutate(d) : undefined} disabled={favorite.isPending} />
              <h1 className="truncate text-xl font-semibold tracking-tight text-ink">{d.name}</h1>
              {d.is_demo && <DemoTag />}
            </div>
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <Badge>{titleCase(d.asset_type)}</Badge>
              {d.managed && <Badge tone="info">Agent installed</Badge>}
              <PresenceBadge presence={d.presence} />
              <ConfidenceBadge confidence={d.confidence} reason={d.confidence_reason} />
              {d.mac_randomized && <RandomizedMacBadge />}
              {d.tags.map((t) => (
                <Badge key={t}>{t}</Badge>
              ))}
            </div>
          </div>
          {isAdmin && (
            <Button onClick={() => setEditOpen(true)}>
              <Pencil className="h-3.5 w-3.5" aria-hidden /> Edit details
            </Button>
          )}
        </div>
      </div>

      {d.merged_into_id !== null && (
        <Notice tone="warn" title="This entry was merged into another device">
          Its identities and sightings now belong to{" "}
          <Link to={`/devices/${d.merged_into_id}`} className="text-accent hover:underline">
            device #{d.merged_into_id}
          </Link>
          . It is kept so the merge can be undone from the Identity tab.
        </Notice>
      )}

      <Tabs value={tab} onValueChange={(v) => setSearch(v === "summary" ? {} : { tab: v }, { replace: true })}>
        <TabsList label="Device sections">
          <TabsTrigger value="summary">Summary</TabsTrigger>
          <TabsTrigger value="addresses">Addresses ({addresses.length})</TabsTrigger>
          <TabsTrigger value="interfaces">Interfaces & services</TabsTrigger>
          <TabsTrigger value="activity">Activity</TabsTrigger>
          <TabsTrigger value="findings">Findings ({activeFindings.length})</TabsTrigger>
          <TabsTrigger value="alerts">Alerts ({alerts.length})</TabsTrigger>
          <TabsTrigger value="history">History</TabsTrigger>
          <TabsTrigger value="identity">Identity management</TabsTrigger>
        </TabsList>

        <TabsContent value="summary" className="space-y-4">
          <div className="grid gap-4 lg:grid-cols-2">
            <Card>
              <CardHeader title="Details" />
              <CardBody>
                <DefinitionList
                  items={[
                    { term: "Alias", value: d.display_name ?? <span className="text-faint">not set</span> },
                    { term: "Hostname", value: d.hostname ?? <span className="text-faint">not observed</span> },
                    { term: "Primary address", value: <Mono>{d.primary_ip ?? "—"}</Mono> },
                    { term: "MAC address", value: <Mono>{d.primary_mac ?? "none seen"}</Mono> },
                    { term: "Vendor", value: <VendorCell vendor={d.vendor} method={d.vendor_method} /> },
                    { term: "Group", value: d.group_name ?? <span className="text-faint">—</span> },
                    { term: "Owner", value: d.owner ?? <span className="text-faint">—</span> },
                    { term: "Notes", value: d.notes ? <span className="whitespace-pre-wrap">{d.notes}</span> : <span className="text-faint">—</span> },
                  ]}
                />
              </CardBody>
            </Card>
            <Card>
              <CardHeader title="Presence & last seen" subtitle="What the sources actually support saying." />
              <CardBody className="space-y-3">
                <DefinitionList
                  items={[
                    { term: "State", value: <PresenceBadge presence={d.presence} /> },
                    { term: "Basis", value: <span className="text-xs text-muted">{d.presence.basis}</span> },
                    {
                      term: "First seen",
                      value: d.first_seen ? <span className="num text-xs">{formatDateTime(d.first_seen)}</span> : <span className="text-faint">never</span>,
                    },
                    {
                      term: "Last seen",
                      value: d.last_seen ? (
                        <span className="text-xs">
                          <span className="num">{formatDateTime(d.last_seen)}</span> (<Timestamp ts={d.last_seen} />)
                          {d.last_seen_source && <span className="text-faint"> via {d.last_seen_source}</span>}
                        </span>
                      ) : (
                        <span className="text-faint">never observed by a live source</span>
                      ),
                    },
                  ]}
                />
                <p className="text-[11px] leading-relaxed text-faint">
                  {d.managed
                    ? "Last seen is the time of the agent's most recent report."
                    : "Last seen is the most recent passive sighting. It is uncertain in both directions: neighbour-cache entries can linger a few minutes after a device leaves, and a quiet device can be present without being seen. “Not seen recently” never means disconnected."}
                </p>
              </CardBody>
            </Card>
          </div>

          <Card>
            <CardHeader title="Identities" subtitle="The keys this device is recognized by, and how much each one can be trusted." />
            <CardBody className="px-2">
              {identities.length ? (
                <Table>
                  <thead>
                    <tr>
                      <Th>Kind</Th>
                      <Th>Value</Th>
                      <Th>Confidence</Th>
                      <Th>Source</Th>
                      <Th>First seen</Th>
                      <Th>Last seen</Th>
                    </tr>
                  </thead>
                  <tbody>
                    {identities.map((i) => (
                      <tr key={`${i.kind}-${i.value}`}>
                        <Td className="text-xs text-muted">{IDENTITY_LABELS[i.kind] ?? titleCase(i.kind)}</Td>
                        <Td>
                          <Mono className="break-all">{i.value}</Mono>
                          <span className="ml-2 inline-flex flex-wrap gap-1 align-middle">
                            {i.locally_administered && <RandomizedMacBadge />}
                            {i.merged_from !== null && <Badge title={`Moved here by a merge from device #${i.merged_from}`}>merged from #{i.merged_from}</Badge>}
                          </span>
                        </Td>
                        <Td>
                          <ConfidenceBadge confidence={i.confidence} />
                        </Td>
                        <Td className="text-xs text-muted">{i.source ?? "—"}</Td>
                        <Td className="text-xs text-muted">
                          <Timestamp ts={i.first_seen} />
                        </Td>
                        <Td className="text-xs text-muted">
                          <Timestamp ts={i.last_seen} />
                        </Td>
                      </tr>
                    ))}
                  </tbody>
                </Table>
              ) : (
                <EmptyState title="No identities" className="py-6" />
              )}
            </CardBody>
          </Card>

          <Card>
            <CardHeader title="Relationships" subtitle="Observed links to interfaces, subnets and services." />
            <CardBody>
              <RelationshipsPanel deviceId={d.id} />
            </CardBody>
          </Card>
        </TabsContent>

        <TabsContent value="addresses">
          <Card>
            <CardHeader title="Address sightings" subtitle="Most recent 50 (IP, MAC) observations. Each row says which source saw it and what kind of evidence it is." />
            <CardBody className="px-2">
              {addresses.length ? (
                <Table>
                  <thead>
                    <tr>
                      <Th>IP address</Th>
                      <Th>MAC</Th>
                      <Th>Hostname</Th>
                      <Th>Evidence</Th>
                      <Th>Source</Th>
                      <Th>First seen</Th>
                      <Th>Last seen</Th>
                    </tr>
                  </thead>
                  <tbody>
                    {addresses.map((a) => {
                      const ev = EVIDENCE_LABELS[a.evidence];
                      return (
                        <tr key={`${a.ip}-${a.mac}-${a.source}-${a.evidence}`}>
                          <Td>
                            <Mono>{a.ip}</Mono>
                          </Td>
                          <Td>
                            <Mono className="text-muted">{a.mac ?? "—"}</Mono>
                          </Td>
                          <Td className="text-xs text-muted">{a.hostname ?? "—"}</Td>
                          <Td>
                            {ev ? (
                              <Hint content={ev.help}>
                                <Badge>{ev.label}</Badge>
                              </Hint>
                            ) : (
                              <Badge>{titleCase(a.evidence)}</Badge>
                            )}
                            {a.merged_from !== null && <span className="ml-1.5 text-[11px] text-faint">merged from #{a.merged_from}</span>}
                          </Td>
                          <Td className="text-xs text-muted">{a.source ?? "—"}</Td>
                          <Td className="text-xs text-muted">
                            <Timestamp ts={a.first_seen} />
                          </Td>
                          <Td className="text-xs text-muted">
                            <Timestamp ts={a.last_seen} />
                          </Td>
                        </tr>
                      );
                    })}
                  </tbody>
                </Table>
              ) : (
                <EmptyState title="No address sightings" className="py-6">
                  No source has reported an address for this device.
                </EmptyState>
              )}
            </CardBody>
          </Card>
        </TabsContent>

        <TabsContent value="interfaces" className="space-y-4">
          <Card>
            <CardHeader title="Interfaces" subtitle="Reported by the device's own agent." />
            <CardBody className="px-2">
              {interfaces.length ? (
                <Table>
                  <thead>
                    <tr>
                      <Th>Name</Th>
                      <Th>State</Th>
                      <Th>MAC</Th>
                      <Th>Addresses</Th>
                      <Th>Link rate (not throughput)</Th>
                      <Th>MTU</Th>
                      <Th>Last reported</Th>
                    </tr>
                  </thead>
                  <tbody>
                    {interfaces.map((i) => (
                      <tr key={i.id}>
                        <Td>
                          <Mono>{i.name}</Mono>
                          {i.is_default_route && (
                            <Badge tone="info" className="ml-2">
                              default route
                            </Badge>
                          )}
                        </Td>
                        <Td>{i.is_up === null ? <Badge>Unknown</Badge> : i.is_up ? <Badge tone="good">Up</Badge> : <Badge>Down</Badge>}</Td>
                        <Td>
                          <Mono className="text-muted">{i.mac ?? "—"}</Mono>
                        </Td>
                        <Td>
                          {i.addresses.length ? (
                            i.addresses.map((a) => (
                              <Mono key={a.address} className="block">
                                {a.address}
                                {a.prefix_len !== null ? `/${a.prefix_len}` : ""}
                              </Mono>
                            ))
                          ) : (
                            <span className="text-faint">—</span>
                          )}
                        </Td>
                        <Td className="num text-xs">
                          {i.link_speed_mbps ? (
                            <span title="Negotiated link (PHY) rate reported by the driver. It is a ceiling, not a measurement of traffic.">{i.link_speed_mbps} Mbit/s</span>
                          ) : (
                            <span className="text-faint">not reported</span>
                          )}
                        </Td>
                        <Td className="num text-xs text-muted">{i.mtu ?? "—"}</Td>
                        <Td className="text-xs text-muted">
                          <Timestamp ts={i.last_seen} />
                        </Td>
                      </tr>
                    ))}
                  </tbody>
                </Table>
              ) : (
                <EmptyState title="No interface data" className="py-6">
                  Interfaces are only known for hosts running an ObserveNet agent.
                </EmptyState>
              )}
            </CardBody>
          </Card>
          <Card>
            <CardHeader
              title="Observed services"
              subtitle="Listening sockets and published container ports reported by the agent on this host. Read from its socket table — nothing was scanned."
            />
            <CardBody className="px-2">
              {services.length ? (
                <Table>
                  <thead>
                    <tr>
                      <Th>Service</Th>
                      <Th>Bind address</Th>
                      <Th>Process / container</Th>
                      <Th>Origin</Th>
                      <Th>State</Th>
                      <Th>First seen</Th>
                      <Th>Last seen</Th>
                    </tr>
                  </thead>
                  <tbody>
                    {services.map((s) => (
                      <tr key={s.id} className={cn(!s.active && "opacity-50")}>
                        <Td>
                          <Mono>
                            {s.proto}/{s.port}
                          </Mono>
                        </Td>
                        <Td>
                          <Mono className="text-muted">{s.address}</Mono>
                        </Td>
                        <Td className="text-xs">{s.process ?? s.container ?? <span className="text-faint">not visible to the collector</span>}</Td>
                        <Td className="text-xs text-muted">{titleCase(s.origin)}</Td>
                        <Td>{s.active ? <Badge tone="info">Listening</Badge> : <Badge>No longer observed</Badge>}</Td>
                        <Td className="text-xs text-muted">
                          <Timestamp ts={s.first_seen} />
                        </Td>
                        <Td className="text-xs text-muted">
                          <Timestamp ts={s.last_seen} />
                        </Td>
                      </tr>
                    ))}
                  </tbody>
                </Table>
              ) : (
                <EmptyState title="No services observed" className="py-6">
                  {d.managed
                    ? "The agent has not reported a socket table yet."
                    : "Services are only known for hosts running an agent. ObserveNet does not port-scan other devices."}
                </EmptyState>
              )}
            </CardBody>
          </Card>
        </TabsContent>

        <TabsContent value="activity">
          <ActivityCharts series={data.series} managed={d.managed} />
        </TabsContent>

        <TabsContent value="findings">
          <Card>
            <CardHeader title="Posture findings" subtitle="Observed configurations and potential exposures on this device. Never proof of exploitability." />
            <CardBody className="px-2">
              {findings.length ? (
                <Table>
                  <thead>
                    <tr>
                      <Th>Severity</Th>
                      <Th>Finding</Th>
                      <Th>Classification</Th>
                      <Th>Status</Th>
                      <Th>Last observed</Th>
                    </tr>
                  </thead>
                  <tbody>
                    {findings.map((f) => (
                      <tr key={f.id} className="hover:bg-raised/40">
                        <Td>
                          <SeverityBadge severity={f.severity} />
                        </Td>
                        <Td>
                          <Link to={`/security/findings/${f.id}`} className="font-medium text-ink hover:text-accent">
                            {f.title}
                          </Link>
                          <div className="font-mono text-[11px] text-faint">{f.rule_id}</div>
                        </Td>
                        <Td>
                          <ClassificationBadge value={f.classification} />
                        </Td>
                        <Td>
                          <StatusBadge status={f.status} />
                        </Td>
                        <Td className="text-xs text-muted">
                          <Timestamp ts={f.last_observed_at} />
                        </Td>
                      </tr>
                    ))}
                  </tbody>
                </Table>
              ) : (
                <EmptyState title="No findings recorded" className="py-6">
                  {d.managed
                    ? "No check has produced a finding for this host."
                    : "Posture checks only run on hosts with an agent, so this device has not been assessed. That means unknown — not secure."}
                </EmptyState>
              )}
            </CardBody>
          </Card>
        </TabsContent>

        <TabsContent value="alerts">
          <Card>
            <CardHeader title="Alerts" subtitle="Most recent 20 alerts that reference this device." />
            <CardBody className="px-2">
              {alerts.length ? (
                <Table>
                  <thead>
                    <tr>
                      <Th>Severity</Th>
                      <Th>Alert</Th>
                      <Th>State</Th>
                      <Th>Started</Th>
                      <Th>Resolved</Th>
                    </tr>
                  </thead>
                  <tbody>
                    {alerts.map((a) => (
                      <tr key={a.id}>
                        <Td>
                          <SeverityBadge severity={a.severity} />
                        </Td>
                        <Td>
                          <Link to="/alerts" className="text-ink hover:text-accent">
                            {a.title}
                          </Link>
                          <p className="line-clamp-2 text-xs text-faint">{a.summary}</p>
                        </Td>
                        <Td>
                          <StatusBadge status={a.state} />
                        </Td>
                        <Td className="text-xs text-muted">
                          <Timestamp ts={a.started_at} />
                        </Td>
                        <Td className="text-xs text-muted">
                          <Timestamp ts={a.resolved_at} />
                        </Td>
                      </tr>
                    ))}
                  </tbody>
                </Table>
              ) : (
                <EmptyState title="No alerts for this device" className="py-6" />
              )}
            </CardBody>
          </Card>
        </TabsContent>

        <TabsContent value="history">
          <Card>
            <CardHeader title="History" subtitle="Most recent 50 events: sightings, edits, merges, finding and alert transitions." />
            <CardBody>
              {events.length ? (
                <ol className="space-y-3 border-l border-line pl-4">
                  {events.map((e) => (
                    <li key={e.id} className="relative text-sm">
                      <span className="absolute -left-[21px] top-1.5 h-2 w-2 rounded-full bg-line ring-2 ring-surface" aria-hidden />
                      <p className="text-ink">{e.message}</p>
                      <p className="text-xs text-faint">
                        <span className="num">{formatDateTime(e.ts)}</span> · <span className="font-mono">{e.kind}</span>
                      </p>
                    </li>
                  ))}
                </ol>
              ) : (
                <EmptyState title="No events recorded" className="py-6" />
              )}
            </CardBody>
          </Card>
        </TabsContent>

        <TabsContent value="identity" className="space-y-4">
          <Notice tone="info" title="Merges are explicit, audited and reversible">
            ObserveNet never silently merges uncertain identities. The only automatic merge — an agent's own NIC MAC matching a neighbour-cache
            sighting — is recorded here like any other and can be undone. Undo moves the original identities and sightings back.
          </Notice>
          <Card>
            <CardHeader
              title="Merge into another device"
              subtitle="When two entries are the same physical device, merge this one into the entry you want to keep."
              actions={
                isAdmin && (
                  <Button onClick={() => setMergeOpen(true)} disabled={d.managed || d.merged_into_id !== null}>
                    <GitMerge className="h-3.5 w-3.5" aria-hidden /> Merge…
                  </Button>
                )
              }
            />
            <CardBody>
              {d.managed ? (
                <p className="text-xs text-muted">
                  This device has a reporting agent, so it cannot be merged away. Open the other entry and merge it into this one instead.
                </p>
              ) : d.merged_into_id !== null ? (
                <p className="text-xs text-muted">Already merged. Undo the merge below before merging again.</p>
              ) : !isAdmin ? (
                <p className="text-xs text-muted">Only administrators can merge devices.</p>
              ) : (
                <p className="text-xs text-muted">Nothing is deleted: this entry is hidden from lists and kept so the merge can be reversed.</p>
              )}
            </CardBody>
          </Card>
          <Card>
            <CardHeader title="Merge history" />
            <CardBody className="px-2">
              {merges.length ? (
                <Table>
                  <thead>
                    <tr>
                      <Th>When</Th>
                      <Th>Merge</Th>
                      <Th>Reason</Th>
                      <Th>How</Th>
                      <Th>State</Th>
                      <Th>
                        <span className="sr-only">Actions</span>
                      </Th>
                    </tr>
                  </thead>
                  <tbody>
                    {merges.map((m) => (
                      <tr key={m.id}>
                        <Td className="num text-xs text-muted">{formatDateTime(m.merged_at)}</Td>
                        <Td className="text-xs">
                          <Link to={`/devices/${m.source_asset_id}`} className="text-accent hover:underline">
                            #{m.source_asset_id}
                          </Link>{" "}
                          →{" "}
                          <Link to={`/devices/${m.target_asset_id}`} className="text-accent hover:underline">
                            #{m.target_asset_id}
                          </Link>
                        </Td>
                        <Td className="max-w-xs text-xs text-muted">{m.reason ?? "—"}</Td>
                        <Td>{m.automatic ? <Badge tone="violet">Automatic</Badge> : <Badge>Manual</Badge>}</Td>
                        <Td>
                          {m.undone_at ? (
                            <Badge title={formatDateTime(m.undone_at)}>Undone</Badge>
                          ) : (
                            <Badge tone="info">Active</Badge>
                          )}
                        </Td>
                        <Td className="text-right">
                          {isAdmin && !m.undone_at && (
                            <Button size="sm" loading={undo.isPending && undo.variables === m.id} onClick={() => undo.mutate(m.id)}>
                              <Undo2 className="h-3.5 w-3.5" aria-hidden /> Undo
                            </Button>
                          )}
                        </Td>
                      </tr>
                    ))}
                  </tbody>
                </Table>
              ) : (
                <EmptyState title="No merges" className="py-6">
                  This device has never been merged with another.
                </EmptyState>
              )}
              {undo.error && (
                <p role="alert" className="px-3 pt-2 text-xs text-crit">
                  {undo.error instanceof ApiError ? undo.error.message : "Undo failed."}
                </p>
              )}
            </CardBody>
          </Card>
        </TabsContent>
      </Tabs>

      {isAdmin && <EditDeviceDialog device={d} open={editOpen} onOpenChange={setEditOpen} />}
      {isAdmin && <MergeDialog device={d} open={mergeOpen} onOpenChange={setMergeOpen} />}
    </div>
  );
}
