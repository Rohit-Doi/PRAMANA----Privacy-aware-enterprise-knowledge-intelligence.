"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { KeyRound, UserPlus } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { SensitivityBadge } from "@/components/badges";
import { PageBody, RequirePermission } from "@/components/shell";
import { Badge, Button, Card, Dialog, ErrorNote, Field, Input, PageHeader, Select, Skeleton, Switch, Table, Td, Th } from "@/components/ui";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { RoleView, UserView } from "@/lib/types";
import { DEPARTMENT_LABEL, REGIONS, departmentLabel, errorMessage, relativeTime } from "@/lib/utils";

interface FormState {
  email: string;
  full_name: string;
  password: string;
  role: string;
  department: string;
  region: string;
  extra_departments: string[];
}
const BLANK: FormState = { email: "", full_name: "", password: "", role: "employee", department: "engineering", region: "global", extra_departments: [] };

function UserDialog({ user, roles, onClose }: { user: UserView | "new"; roles: RoleView[]; onClose: () => void }) {
  const queryClient = useQueryClient();
  const isNew = user === "new";
  const [form, setForm] = useState<FormState>(
    isNew ? BLANK : { email: user.email, full_name: user.full_name, password: "", role: user.role, department: user.department, region: user.region, extra_departments: user.extra_departments },
  );
  const [newPassword, setNewPassword] = useState("");
  const set = <K extends keyof FormState>(key: K, value: FormState[K]) => setForm((current) => ({ ...current, [key]: value }));
  const done = (message: string) => { toast.success(message); void queryClient.invalidateQueries({ queryKey: ["users"] }); onClose(); };

  const save = useMutation({
    mutationFn: () =>
      isNew
        ? api<UserView>("/users", { method: "POST", body: form })
        : api<UserView>(`/users/${user.id}`, {
            method: "PATCH",
            body: { full_name: form.full_name, role: form.role, department: form.department, region: form.region, extra_departments: form.extra_departments },
          }),
    onSuccess: () => done(isNew ? "User created" : "User updated"),
    onError: (error) => toast.error(errorMessage(error)),
  });
  const reset = useMutation({
    mutationFn: () => api<void>(`/users/${(user as UserView).id}/password`, { method: "POST", body: { new_password: newPassword } }),
    onSuccess: () => done("Password reset; the user's sessions were signed out"),
    onError: (error) => toast.error(errorMessage(error)),
  });
  const role = roles.find((r) => r.key === form.role);

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()} title={isNew ? "Add a user" : `Edit ${user.full_name}`}>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Full name">
          <Input value={form.full_name} onChange={(e) => set("full_name", e.target.value)} maxLength={128} />
        </Field>
        <Field label="Email">
          <Input type="email" value={form.email} onChange={(e) => set("email", e.target.value)} disabled={!isNew} />
        </Field>
        {isNew && (
          <Field label="Initial password" hint="At least 12 characters, with a letter and a digit" className="sm:col-span-2">
            <Input type="password" value={form.password} onChange={(e) => set("password", e.target.value)} autoComplete="new-password" />
          </Field>
        )}
        <Field label="Role">
          <Select value={form.role} onChange={(e) => set("role", e.target.value)}>
            {roles.map((r) => <option key={r.key} value={r.key}>{r.name}</option>)}
          </Select>
        </Field>
        <Field label="Department">
          <Select value={form.department} onChange={(e) => set("department", e.target.value)}>
            {Object.keys(DEPARTMENT_LABEL).map((key) => <option key={key} value={key}>{departmentLabel(key)}</option>)}
          </Select>
        </Field>
        <Field label="Region">
          <Select value={form.region} onChange={(e) => set("region", e.target.value)}>
            {REGIONS.map((key) => <option key={key} value={key}>{key.toUpperCase()}</option>)}
          </Select>
        </Field>
        <Field label="Also treated as a member of" hint="Hold Ctrl to select several">
          <select
            multiple
            value={form.extra_departments}
            onChange={(e) => set("extra_departments", Array.from(e.target.selectedOptions, (o) => o.value))}
            className="h-20 w-full rounded-md border bg-surface px-2 text-sm"
          >
            {Object.keys(DEPARTMENT_LABEL).filter((key) => key !== form.department).map((key) => <option key={key} value={key}>{departmentLabel(key)}</option>)}
          </select>
        </Field>
      </div>
      {role && (
        <p className="mt-3 rounded-md bg-surface-muted p-2.5 text-xs text-muted">
          As {role.name}, this user reads up to <SensitivityBadge rank={role.max_rank_own_dept} /> in {departmentLabel(form.department)}
          {form.extra_departments.length > 0 && ` and ${form.extra_departments.map(departmentLabel).join(", ")}`}, and up to{" "}
          <SensitivityBadge rank={role.max_rank_other_dept} /> elsewhere.
        </p>
      )}
      <div className="mt-4 flex justify-end gap-2">
        <Button variant="ghost" onClick={onClose}>Cancel</Button>
        <Button loading={save.isPending} disabled={!form.full_name || !form.email || (isNew && form.password.length < 12)} onClick={() => save.mutate()}>
          {isNew ? "Create user" : "Save changes"}
        </Button>
      </div>
      {!isNew && (
        <div className="mt-5 border-t pt-4">
          <p className="text-sm font-medium">Reset password</p>
          <div className="mt-2 flex gap-2">
            <Input type="password" value={newPassword} onChange={(e) => setNewPassword(e.target.value)} placeholder="New password (12+ characters)" autoComplete="new-password" aria-label="New password" />
            <Button variant="outline" disabled={newPassword.length < 12} loading={reset.isPending} onClick={() => reset.mutate()}>
              <KeyRound className="h-4 w-4" /> Reset
            </Button>
          </div>
        </div>
      )}
    </Dialog>
  );
}

function UsersPage() {
  const me = useAuth((s) => s.me);
  const queryClient = useQueryClient();
  const users = useQuery({ queryKey: ["users"], queryFn: () => api<UserView[]>("/users") });
  const roles = useQuery({ queryKey: ["roles"], queryFn: () => api<RoleView[]>("/roles") });
  const [editing, setEditing] = useState<UserView | "new" | null>(null);
  const toggle = useMutation({
    mutationFn: ({ id, is_active }: { id: string; is_active: boolean }) => api<UserView>(`/users/${id}`, { method: "PATCH", body: { is_active } }),
    onSuccess: (user) => { toast.success(user.is_active ? "User reactivated" : "User deactivated and signed out everywhere"); void queryClient.invalidateQueries({ queryKey: ["users"] }); },
    onError: (error) => toast.error(errorMessage(error)),
  });
  const roleByKey = Object.fromEntries((roles.data ?? []).map((r) => [r.key, r]));

  return (
    <PageBody>
      <PageHeader
        title="Users & Roles"
        description="A user's role, department and region decide what they can read. Changes take effect on the user's next request."
        actions={<Button onClick={() => setEditing("new")}><UserPlus className="h-4 w-4" /> Add user</Button>}
      />
      {users.error && <ErrorNote error={users.error} />}
      <Card>
        <Table>
          <thead>
            <tr><Th>Name</Th><Th>Role</Th><Th>Department</Th><Th>Region</Th><Th>Reads up to</Th><Th>MFA</Th><Th>Last sign-in</Th><Th>Active</Th><Th /></tr>
          </thead>
          <tbody>
            {users.isLoading && Array.from({ length: 6 }).map((_, i) => <tr key={i}><Td colSpan={9}><Skeleton className="h-5" /></Td></tr>)}
            {users.data?.map((user) => {
              const role = roleByKey[user.role];
              return (
                <tr key={user.id} className={user.is_active ? undefined : "opacity-60"}>
                  <Td>
                    <p className="font-medium">{user.full_name} {user.is_service_account && <Badge>service account</Badge>}</p>
                    <p className="text-xs text-muted">{user.email}</p>
                  </Td>
                  <Td>{user.role_name}</Td>
                  <Td>
                    {departmentLabel(user.department)}
                    {user.extra_departments.length > 0 && <span className="text-xs text-muted"> +{user.extra_departments.map(departmentLabel).join(", ")}</span>}
                  </Td>
                  <Td>{user.region.toUpperCase()}</Td>
                  <Td>{role && <SensitivityBadge rank={role.max_rank_own_dept} />}</Td>
                  <Td>{user.mfa_enabled ? <Badge tone="ok">on</Badge> : <Badge>off</Badge>}</Td>
                  <Td className="whitespace-nowrap text-muted">{user.last_login_at ? relativeTime(user.last_login_at) : "never"}</Td>
                  <Td>
                    <Switch
                      label={`${user.full_name} is active`}
                      checked={user.is_active}
                      disabled={user.id === me?.user_id || toggle.isPending}
                      onCheckedChange={(value) => toggle.mutate({ id: user.id, is_active: value })}
                    />
                  </Td>
                  <Td>
                    <Button size="sm" variant="outline" onClick={() => setEditing(user)}>Edit</Button>
                  </Td>
                </tr>
              );
            })}
          </tbody>
        </Table>
      </Card>
      {editing && roles.data && <UserDialog key={editing === "new" ? "new" : editing.id} user={editing} roles={roles.data} onClose={() => setEditing(null)} />}
    </PageBody>
  );
}

export default function Page() {
  return (
    <RequirePermission permission="users:manage">
      <UsersPage />
    </RequirePermission>
  );
}
