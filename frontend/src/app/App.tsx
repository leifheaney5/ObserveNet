import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { lazy, Suspense } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";

import { Layout } from "@/app/Layout";
import { Spinner } from "@/components/ui/primitives";
import { TooltipProvider } from "@/components/ui/overlays";
import { AuthProvider, useAuth } from "@/hooks/useAuth";
import { PeriodProvider } from "@/hooks/usePeriod";
import { ApiError } from "@/lib/api";
import { LoginPage } from "@/pages/Login";
import { OverviewPage } from "@/pages/Overview";

const DevicesPage = lazy(() => import("@/pages/Devices").then((m) => ({ default: m.DevicesPage })));
const DeviceDetailPage = lazy(() => import("@/pages/DeviceDetail").then((m) => ({ default: m.DeviceDetailPage })));
const TrafficPage = lazy(() => import("@/pages/Traffic").then((m) => ({ default: m.TrafficPage })));
const ConnectivityPage = lazy(() => import("@/pages/Connectivity").then((m) => ({ default: m.ConnectivityPage })));
const SecurityPage = lazy(() => import("@/pages/Security").then((m) => ({ default: m.SecurityPage })));
const FindingDetailPage = lazy(() => import("@/pages/FindingDetail").then((m) => ({ default: m.FindingDetailPage })));
const AlertsPage = lazy(() => import("@/pages/Alerts").then((m) => ({ default: m.AlertsPage })));
const InsightsPage = lazy(() => import("@/pages/Insights").then((m) => ({ default: m.InsightsPage })));
const SettingsPage = lazy(() => import("@/pages/Settings").then((m) => ({ default: m.SettingsPage })));

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      retry: (count, error) => !(error instanceof ApiError && error.status >= 400 && error.status < 500) && count < 2,
    },
  },
});

function Shell() {
  const { me, loading } = useAuth();
  if (loading) return <Spinner label="Starting ObserveNet" />;
  if (!me) return <LoginPage />;
  return (
    <PeriodProvider>
      <Layout>
        <Suspense fallback={<Spinner />}>
          <Routes>
            <Route path="/" element={<OverviewPage />} />
            <Route path="/devices" element={<DevicesPage />} />
            <Route path="/devices/:id" element={<DeviceDetailPage />} />
            <Route path="/traffic" element={<TrafficPage />} />
            <Route path="/connectivity" element={<ConnectivityPage />} />
            <Route path="/security" element={<SecurityPage />} />
            <Route path="/security/findings/:id" element={<FindingDetailPage />} />
            <Route path="/alerts" element={<AlertsPage />} />
            <Route path="/insights" element={<InsightsPage />} />
            <Route path="/settings" element={<SettingsPage />} />
            <Route path="/settings/:tab" element={<SettingsPage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </Suspense>
      </Layout>
    </PeriodProvider>
  );
}

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <TooltipProvider>
        <BrowserRouter>
          <AuthProvider>
            <Shell />
          </AuthProvider>
        </BrowserRouter>
      </TooltipProvider>
    </QueryClientProvider>
  );
}
