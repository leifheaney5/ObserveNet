import { createContext, useContext, useMemo, useState, type ReactNode } from "react";

export interface Period {
  key: "1m" | "15m" | "1h" | "24h" | "7d";
  label: string;
  seconds: number;
  bucket: number; // seconds per chart bucket requested from the API
  live: boolean; // true when 1 s live samples are appended to the chart
}

// eslint-disable-next-line react-refresh/only-export-components
export const PERIODS: Period[] = [
  { key: "1m", label: "1 min", seconds: 60, bucket: 1, live: true },
  { key: "15m", label: "15 min", seconds: 900, bucket: 1, live: true },
  { key: "1h", label: "1 h", seconds: 3600, bucket: 10, live: false },
  { key: "24h", label: "24 h", seconds: 86400, bucket: 300, live: false },
  { key: "7d", label: "7 d", seconds: 604800, bucket: 1800, live: false },
];

const Ctx = createContext<{ period: Period; setPeriod: (k: Period["key"]) => void } | null>(null);

export function PeriodProvider({ children }: { children: ReactNode }) {
  const [key, setKey] = useState<Period["key"]>(() => {
    try {
      const saved = localStorage.getItem("observenet.period") as Period["key"] | null;
      return PERIODS.some((p) => p.key === saved) ? (saved as Period["key"]) : "15m";
    } catch {
      return "15m";
    }
  });
  const value = useMemo(
    () => ({
      period: PERIODS.find((p) => p.key === key) ?? PERIODS[1]!,
      setPeriod: (k: Period["key"]) => {
        setKey(k);
        try {
          localStorage.setItem("observenet.period", k);
        } catch {
          /* private mode */
        }
      },
    }),
    [key],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

// eslint-disable-next-line react-refresh/only-export-components
export function usePeriod() {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("usePeriod must be used inside PeriodProvider");
  return ctx;
}
