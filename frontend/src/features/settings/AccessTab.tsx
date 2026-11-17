import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, ShieldCheck, UserRound, Users } from "lucide-react";
import { useState, type FormEvent } from "react";

import { Dialog, Switch } from "@/components/ui/overlays";
import { Button, Card, CardBody, CardHeader, ErrorState, Field, Input, NativeSelect, Skeleton, Table, Td, Th } from "@/components/ui/primitives";
import { Badge, Timestamp } from "@/components/ui/status";
import { FormError, SavedFlash } from "@/features/alerts/shared";
import { useAuth } from "@/hooks/useAuth";
import { api, type User } from "@/lib/api";

const MIN_PASSWORD = 12;

function ChangePassword() {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [savedAt, setSavedAt] = useState<number | null>(null);
  const tooShort = next.length > 0 && next.length < MIN_PASSWORD;
  const mismatch = confirm.length > 0 && confirm !== next;
  const change = useMutation({
    mutationFn: () => api.post<void>("/auth/password", { current_password: current, new_password: next }),
    onSuccess: () => {
      setCurrent("");
      setNext("");
      setConfirm("");
      setSavedAt(Date.now());
    },
  });
  function submit(e: FormEvent) {
    e.preventDefault();
    change.mutate();
  }
  return (
    <Card>
      <CardHeader title="Change your password" subtitle="Changing it signs out every other session of your account." />
      <CardBody>
        <form onSubmit={submit} className="max-w-md space-y-4" aria-label="Change password">
          <Field label="Current password" htmlFor="pw-current">
            <Input id="pw-current" type="password" autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} disabled={change.isPending} />
          </Field>
          <Field label="New password" htmlFor="pw-new" error={tooShort ? `Use at least ${MIN_PASSWORD} characters.` : null} hint={`At least ${MIN_PASSWORD} characters. Known placeholder passwords are rejected.`}>
            <Input id="pw-new" type="password" autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} disabled={change.isPending} />
          </Field>
          <Field label="Repeat new password" htmlFor="pw-confirm" error={mismatch ? "The passwords do not match." : null}>
            <Input id="pw-confirm" type="password" autoComplete="new-password" value={confirm} onChange={(e) => setConfirm(e.target.value)} disabled={change.isPending} />
          </Field>
          <div className="flex flex-wrap items-center gap-3">
            <Button type="submit" variant="primary" loading={change.isPending} disabled={!current || next.length < MIN_PASSWORD || confirm !== next}>
              Change password
            </Button>
            <SavedFlash at={savedAt}>Password changed</SavedFlash>
            <FormError error={change.error} />
          </div>
        </form>
      </CardBody>
    </Card>
  );
}

function CreateUserDialog({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState<"viewer" | "admin">("viewer");
  const nameError = username && !/^[a-zA-Z0-9_.-]{3,64}$/.test(username) ? "3–64 characters: letters, digits, dot, dash, underscore." : null;
  const pwError = password && password.length < MIN_PASSWORD ? `Use at least ${MIN_PASSWORD} characters.` : null;
  const create = useMutation({
    mutationFn: () => api.post<User>("/users", { username, password, role }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["users"] });
      onClose();
    },
  });
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title="New user"
      description="Give the password to the person over a private channel; they can change it under Account & access."
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" loading={create.isPending} disabled={!username || !password || !!nameError || !!pwError} onClick={() => create.mutate()}>
            Create user
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <Field label="Username" htmlFor="nu-name" error={nameError}>
          <Input id="nu-name" autoComplete="off" value={username} autoFocus onChange={(e) => setUsername(e.target.value)} />
        </Field>
        <Field label="Initial password" htmlFor="nu-pw" error={pwError}>
          <Input id="nu-pw" type="password" autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} />
        </Field>
        <Field label="Role" htmlFor="nu-role" hint="Viewers can read everything in the UI but cannot change settings, acknowledge alerts, verify findings or export.">
          <NativeSelect id="nu-role" value={role} onChange={(e) => setRole(e.target.value as "viewer" | "admin")}>
            <option value="viewer">Viewer (read-only)</option>
            <option value="admin">Administrator</option>
          </NativeSelect>
        </Field>
        <FormError error={create.error} />
      </div>
    </Dialog>
  );
}

function UsersCard() {
  const { me } = useAuth();
  const qc = useQueryClient();
  const [creating, setCreating] = useState(false);
  const users = useQuery({ queryKey: ["users"], queryFn: () => api.get<User[]>("/users") });
  const patch = useMutation({
    mutationFn: ({ id, body }: { id: number; body: { role?: string; disabled?: boolean } }) => api.patch<User>(`/users/${id}`, body),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["users"] }),
  });
  return (
    <Card>
      <CardHeader
        icon={<Users className="h-4 w-4" />}
        title="Users"
        subtitle="Disabling a user or changing their role takes effect immediately; disabling also ends their sessions."
        actions={
          <Button size="sm" variant="primary" onClick={() => setCreating(true)}>
            <Plus className="h-3.5 w-3.5" aria-hidden /> New user
          </Button>
        }
      />
      <CardBody className="px-2">
        {users.isLoading ? (
          <Skeleton className="mx-3 h-24" />
        ) : users.isError ? (
          <ErrorState error={users.error} retry={() => void users.refetch()} />
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>User</Th>
                <Th>Role</Th>
                <Th>Created</Th>
                <Th>Last sign-in</Th>
                <Th>Enabled</Th>
              </tr>
            </thead>
            <tbody>
              {users.data?.map((u) => {
                const self = u.id === me?.user.id;
                return (
                  <tr key={u.id}>
                    <Td className="font-medium">
                      {u.username} {self && <Badge className="ml-1">you</Badge>}
                    </Td>
                    <Td>
                      <label className="sr-only" htmlFor={`role-${u.id}`}>
                        Role of {u.username}
                      </label>
                      <NativeSelect
                        id={`role-${u.id}`}
                        className="h-8 w-44 text-xs"
                        value={u.role}
                        disabled={patch.isPending}
                        onChange={(e) => patch.mutate({ id: u.id, body: { role: e.target.value } })}
                      >
                        <option value="admin">Administrator</option>
                        <option value="viewer">Viewer (read-only)</option>
                      </NativeSelect>
                    </Td>
                    <Td className="text-xs text-muted">
                      <Timestamp ts={u.created_at} />
                    </Td>
                    <Td className="text-xs text-muted">{u.last_login_at ? <Timestamp ts={u.last_login_at} /> : <span className="text-faint">never</span>}</Td>
                    <Td>
                      <span className="flex items-center gap-2 text-xs text-muted">
                        <Switch
                          checked={!u.disabled}
                          disabled={patch.isPending}
                          label={`${u.disabled ? "Enable" : "Disable"} user ${u.username}`}
                          onCheckedChange={(v) => patch.mutate({ id: u.id, body: { disabled: !v } })}
                        />
                        {u.disabled ? "Disabled" : "Active"}
                      </span>
                    </Td>
                  </tr>
                );
              })}
            </tbody>
          </Table>
        )}
        <div className="px-3 pt-2">
          <FormError error={patch.error} />
        </div>
      </CardBody>
      {creating && <CreateUserDialog onClose={() => setCreating(false)} />}
    </Card>
  );
}

export function AccessTab() {
  const { me, isAdmin } = useAuth();
  return (
    <div className="space-y-4">
      <div className="grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader icon={<UserRound className="h-4 w-4" />} title="Your account" />
          <CardBody>
            <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-2 text-sm">
              <dt className="text-muted">Username</dt>
              <dd className="font-medium text-ink">{me?.user.username}</dd>
              <dt className="text-muted">Role</dt>
              <dd>
                <Badge tone={isAdmin ? "info" : "neutral"}>{isAdmin ? "Administrator" : "Viewer (read-only)"}</Badge>
              </dd>
              <dt className="text-muted">Last sign-in</dt>
              <dd className="text-ink">{me?.user.last_login_at ? <Timestamp ts={me.user.last_login_at} /> : "—"}</dd>
            </dl>
          </CardBody>
        </Card>
        <Card>
          <CardHeader icon={<ShieldCheck className="h-4 w-4" />} title="How sign-in is protected" />
          <CardBody>
            <ul className="list-disc space-y-1.5 pl-5 text-xs leading-relaxed text-muted">
              <li>
                <strong className="font-medium text-ink">Roles.</strong> Administrators can change settings and act on alerts and findings.
                Viewers are read-only.
              </li>
              <li>
                <strong className="font-medium text-ink">Passwords</strong> are stored as Argon2id hashes; there are no default credentials and
                the first administrator comes from the environment or the CLI.
              </li>
              <li>
                <strong className="font-medium text-ink">Sessions</strong> are opaque, server-side and expire; the cookie is HttpOnly and
                SameSite=Lax (and Secure when the public URL is HTTPS). Signing out or a password change revokes them.
              </li>
              <li>
                <strong className="font-medium text-ink">CSRF.</strong> Every change needs a per-session token header in addition to the cookie.
              </li>
              <li>Sign-in attempts are rate-limited, and administrative actions are written to the audit log.</li>
              <li>For remote access, keep an edge access policy in front of this login as a second layer (see the Domain &amp; backup tab).</li>
            </ul>
          </CardBody>
        </Card>
      </div>
      <ChangePassword />
      {isAdmin ? <UsersCard /> : <p className="text-xs text-faint">User management is available to administrators.</p>}
    </div>
  );
}
