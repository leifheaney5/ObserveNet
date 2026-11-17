import { KeyRound, Lock } from "lucide-react";
import { useState, type FormEvent } from "react";

import { DemoBanner } from "@/app/Layout";
import { Logo } from "@/components/Logo";
import { Button, Card, Field, Input, Notice } from "@/components/ui/primitives";
import { useAuth } from "@/hooks/useAuth";
import { ApiError } from "@/lib/api";

export function LoginPage() {
  const { login, meta } = useAuth();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(username, password);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Sign-in failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-full flex-col">
      {meta?.demo_mode && <DemoBanner />}
      <div className="flex flex-1 items-center justify-center px-4 py-10">
        <div className="w-full max-w-sm">
          <div className="mb-6 flex justify-center">
            <Logo size={36} />
          </div>
          <Card className="p-6">
            <h1 className="text-base font-semibold text-ink">Sign in</h1>
            <p className="mt-1 text-xs leading-relaxed text-muted">Network data is private. Sign in to view devices, traffic and findings.</p>
            {meta?.setup_required && (
              <div className="mt-4">
                <Notice tone="warn" title="No administrator account exists" icon={<KeyRound className="h-4 w-4" />}>
                  {meta.setup_hint}
                </Notice>
              </div>
            )}
            <form onSubmit={submit} className="mt-5 space-y-4" noValidate>
              <Field label="Username" htmlFor="username">
                <Input id="username" name="username" autoComplete="username" autoFocus value={username} onChange={(e) => setUsername(e.target.value)} required />
              </Field>
              <Field label="Password" htmlFor="password" error={error}>
                <Input id="password" name="password" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required />
              </Field>
              <Button type="submit" variant="primary" className="w-full" loading={busy} disabled={!username || !password}>
                <Lock className="h-3.5 w-3.5" aria-hidden /> Sign in
              </Button>
            </form>
          </Card>
          <p className="mt-4 text-center text-[11px] leading-relaxed text-faint">
            ObserveNet {meta?.version ? `v${meta.version}` : ""} · read-only home-network observability.
            <br />
            There are no default credentials.
          </p>
        </div>
      </div>
    </div>
  );
}
