import { Crosshair, Database, Globe, KeyRound, Radar, ScrollText, ServerCog, ShieldCheck, Users } from "lucide-react";
import { Navigate, useNavigate, useParams } from "react-router-dom";

import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/overlays";
import { PageHeader } from "@/components/ui/primitives";
import { AccessTab } from "@/features/settings/AccessTab";
import { AgentsTab } from "@/features/settings/AgentsTab";
import { AuditTab, DeploymentTab } from "@/features/settings/DeploymentTab";
import { ProbesTab } from "@/features/settings/ProbesTab";
import { CollectionTab, PostureTab, ScopeTab } from "@/features/settings/RuntimeTabs";
import { SourcesTab } from "@/features/settings/SourcesTab";

const TABS = [
  { key: "sources", label: "Data sources", icon: ServerCog, render: () => <SourcesTab /> },
  { key: "agents", label: "Agent tokens", icon: KeyRound, render: () => <AgentsTab /> },
  { key: "probes", label: "Probe targets", icon: Crosshair, render: () => <ProbesTab /> },
  { key: "collection", label: "Collection & retention", icon: Database, render: () => <CollectionTab /> },
  { key: "scope", label: "Network scope", icon: Radar, render: () => <ScopeTab /> },
  { key: "posture", label: "Security posture", icon: ShieldCheck, render: () => <PostureTab /> },
  { key: "access", label: "Users & access", icon: Users, render: () => <AccessTab /> },
  { key: "deployment", label: "Domain & backup", icon: Globe, render: () => <DeploymentTab /> },
  { key: "audit", label: "Audit log", icon: ScrollText, render: () => <AuditTab /> },
] as const;

export function SettingsPage() {
  const { tab } = useParams<{ tab?: string }>();
  const navigate = useNavigate();
  const active = TABS.find((t) => t.key === tab);
  if (tab && !active) return <Navigate to="/settings/sources" replace />;
  const current = active ?? TABS[0];

  return (
    <div>
      <PageHeader
        title="Settings"
        description="Data sources and collectors, what is probed and how often, how long data is kept, the approved network scope, and who can sign in."
      />
      <Tabs value={current.key} onValueChange={(v) => navigate(`/settings/${v}`)}>
        <TabsList label="Settings sections">
          {TABS.map(({ key, label, icon: Icon }) => (
            <TabsTrigger key={key} value={key}>
              <Icon className="h-3.5 w-3.5" aria-hidden />
              {label}
            </TabsTrigger>
          ))}
        </TabsList>
        {TABS.map((t) => (
          <TabsContent key={t.key} value={t.key}>
            {t.key === current.key && t.render()}
          </TabsContent>
        ))}
      </Tabs>
    </div>
  );
}
