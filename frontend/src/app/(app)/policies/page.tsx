"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, FlaskConical, History, X } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { SensitivityBadge } from "@/components/badges";
import { PageBody, RequirePermission } from "@/components/shell";
import { Badge, Button, Card, CardHeader, ErrorNote, Field, KeyValue, PageHeader, Select, Skeleton, Switch, Table, Tabs, TabsContent, TabsList, TabsTrigger, Td, Th } from "@/components/ui";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { ActivePolicy, RoleView, Scope, UserView } from "@/lib/types";
import { DEPARTMENT_LABEL, REGIONS, ROLE_LABEL, SENSITIVITY, dateTime, departmentLabel, errorMessage } from "@/lib/utils";

interface Simulation {
  user: { email: string; full_name: string; role: string; department: string; region: string; extra_departments: string[] };
  scope: Scope;
  egress: { max_rank: number; pseudonymize_names: boolean };
  visible_documents: { sensitivity: string; rank: number; visible: number; total: number }[];
  resource?: { title: string; department: string; sensitivity_rank: number; region: string };
  decision?: { allow: boolean; reasons: string[] };
}

function RoleMatrix({ policy }: { policy: ActivePolicy }) {
  const canWrite = useAuth((s) => s.has("policy:write"));
  const queryClient = useQueryClient();
  const roles = useQuery({ queryKey: ["roles"], queryFn: () => api<RoleView[]>("/roles") });
  const update = useMutation({
    mutationFn: ({ key, body }: { key: string; body: Record<string, unknown> }) => api<RoleView>(`/roles/${key}`, { method: "PATCH", body }),
    onSuccess: () => {
      toast.success("Role updated and a new policy version published");
      void queryClient.invalidateQueries({ queryKey: ["roles"] });
      void queryClient.invalidateQueries({ queryKey: ["policy"] });
    },
    onError: (error) => toast.error(errorMessage(error)),
  });
  if (roles.isLoading) return <Skeleton className="h-64" />;
  if (roles.error) return <ErrorNote error={roles.error} />;
  return (
    <Card>
      <CardHeader
        title="Roles and reading limits"
        description="The highest sensitivity each role may read: in its own department, and in other departments. Changing a limit publishes a new policy version that takes effect at once."
      />
      <Table>
        <thead>
          <tr>
            <Th>Role</Th><Th>Own department, up to</Th><Th>Other departments, up to</Th><Th>Permissions</Th>
          </tr>
        </thead>
        <tbody>
          {roles.data?.map((role) => (
            <tr key={role.key}>
              <Td>
                <p className="font-medium">{role.name}</p>
                <p className="max-w-xs text-xs text-muted">{role.description}</p>
              </Td>
              {(["max_rank_own_dept", "max_rank_other_dept"] as const).map((field) => (
                <Td key={field}>
                  {canWrite ? (
                    <Select
                      aria-label={`${role.name}: ${field === "max_rank_own_dept" ? "own department" : "other departments"}`}
                      className="w-40"
                      value={role[field]}
                      disabled={update.isPending}
                      onChange={(event) => update.mutate({ key: role.key, body: { [field]: Number(event.target.value) } })}
                    >
                      {SENSITIVITY.map((level, rank) => (
                        <option key={level} value={rank}>{level}</option>
                      ))}
                    </Select>
                  ) : (
                    <SensitivityBadge rank={role[field]} />
                  )}
                </Td>
              ))}
              <Td>
                <div className="flex max-w-md flex-wrap gap-1">
                  {role.permissions.map((permission) => (
                    <Badge key={permission} tone={["pii:read", "region:all", "users:simulate", "policy:write"].includes(permission) ? "warn" : "neutral"}>
                      {permission}
                    </Badge>
                  ))}
                </div>
              </Td>
            </tr>
          ))}
        </tbody>
      </Table>
      <p className="border-t px-4 py-2 text-xs text-muted">Active policy version {policy.version}. Administrators deliberately have no elevated access to content.</p>
    </Card>
  );
}

function SettingsCard({ policy }: { policy: ActivePolicy }) {
  const canWrite = useAuth((s) => s.has("policy:write"));
  const queryClient = useQueryClient();
  const update = useMutation({
    mutationFn: (body: Record<string, unknown>) => api("/policies/config", { method: "PUT", body }),
    onSuccess: () => { toast.success("New policy version published"); void queryClient.invalidateQueries({ queryKey: ["policy"] }); },
    onError: (error) => toast.error(errorMessage(error)),
  });
  return (
    <Card>
      <CardHeader title="Privacy settings" description="Rules that apply to every request, whatever the role." />
      <div className="space-y-4 p-4">
        <div className="flex items-start justify-between gap-4">
          <div>
            <p className="text-sm font-medium">LLM egress ceiling</p>
            <p className="max-w-xl text-xs text-muted">
              The highest sensitivity that may be sent to an external language model. Evidence above the ceiling is never sent:
              the answer is quoted directly from the sources instead.
            </p>
          </div>
          <Select
            aria-label="LLM egress ceiling"
            className="w-44"
            disabled={!canWrite || update.isPending}
            value={policy.config.egress_max_rank}
            onChange={(event) => update.mutate({ egress_max_rank: Number(event.target.value) })}
          >
            <option value={-1}>Nothing leaves</option>
            {SENSITIVITY.map((level, rank) => (
              <option key={level} value={rank}>Up to {level}</option>
            ))}
          </Select>
        </div>
        <div className="flex items-start justify-between gap-4">
          <div>
            <p className="text-sm font-medium">Replace personal names before LLM calls</p>
            <p className="max-w-xl text-xs text-muted">
              Names, emails and phone numbers in CONFIDENTIAL and RESTRICTED passages are swapped for placeholders before the
              prompt leaves this machine, and restored afterwards only for readers allowed to see personal data.
            </p>
          </div>
          <Switch label="Replace personal names before LLM calls" checked={policy.config.pseudonymize_names} disabled={!canWrite || update.isPending} onCheckedChange={(value) => update.mutate({ pseudonymize_names: value })} />
        </div>
        <div className="flex items-start justify-between gap-4">
          <div>
            <p className="text-sm font-medium">Tell users how many sources were withheld</p>
            <p className="max-w-xl text-xs text-muted">
              When off, a user cannot tell &quot;nothing exists&quot; apart from &quot;something exists that I may not read&quot;.
            </p>
          </div>
          <Switch label="Tell users how many sources were withheld" checked={policy.config.show_withheld_count} disabled={!canWrite || update.isPending} onCheckedChange={(value) => update.mutate({ show_withheld_count: value })} />
        </div>
      </div>
    </Card>
  );
}

function Simulator() {
  const users = useQuery({ queryKey: ["users"], queryFn: () => api<UserView[]>("/users"), retry: false });
  const me = useAuth((s) => s.me);
  const [userId, setUserId] = useState("");
  const [department, setDepartment] = useState("hr");
  const [sensitivity, setSensitivity] = useState("CONFIDENTIAL");
  const [region, setRegion] = useState("global");
  const simulate = useMutation({
    mutationFn: () => api<Simulation>("/policies/simulate", { method: "POST", body: { user_id: userId || me!.user_id, department, sensitivity, region } }),
  });
  const result = simulate.data;
  return (
    <Card>
      <CardHeader title="Policy simulator" description="Ask the policy engine what a user can read, and whether a document with given labels would be allowed. The decision and its reasons come from the same engine that guards every request." />
      <div className="grid gap-3 p-4 md:grid-cols-4">
        <Field label="User" className="md:col-span-4">
          <Select value={userId || me?.user_id || ""} onChange={(e) => setUserId(e.target.value)}>
            {users.data ? (
              users.data.filter((u) => u.is_active).map((u) => (
                <option key={u.id} value={u.id}>{u.full_name} ({ROLE_LABEL[u.role] ?? u.role}, {departmentLabel(u.department)}, {u.region.toUpperCase()})</option>
              ))
            ) : (
              <option value={me?.user_id}>{me?.full_name} (you)</option>
            )}
          </Select>
        </Field>
        <Field label="Document department">
          <Select value={department} onChange={(e) => setDepartment(e.target.value)}>
            {Object.keys(DEPARTMENT_LABEL).map((key) => <option key={key} value={key}>{departmentLabel(key)}</option>)}
          </Select>
        </Field>
        <Field label="Document sensitivity">
          <Select value={sensitivity} onChange={(e) => setSensitivity(e.target.value)}>
            {SENSITIVITY.map((level) => <option key={level}>{level}</option>)}
          </Select>
        </Field>
        <Field label="Document region">
          <Select value={region} onChange={(e) => setRegion(e.target.value)}>
            {REGIONS.map((key) => <option key={key} value={key}>{key.toUpperCase()}</option>)}
          </Select>
        </Field>
        <div className="flex items-end">
          <Button className="w-full" loading={simulate.isPending} onClick={() => simulate.mutate()}>
            <FlaskConical className="h-4 w-4" /> Simulate
          </Button>
        </div>
      </div>
      {simulate.error && <div className="px-4 pb-4"><ErrorNote error={simulate.error} /></div>}
      {result && (
        <div className="grid gap-4 border-t p-4 md:grid-cols-2">
          <div>
            {result.decision && (
              <div className={`rounded-lg p-3 ${result.decision.allow ? "bg-ok-soft text-ok" : "bg-bad-soft text-bad"}`}>
                <p className="flex items-center gap-1.5 text-sm font-semibold">
                  {result.decision.allow ? <Check className="h-4 w-4" /> : <X className="h-4 w-4" />}
                  {result.decision.allow ? "Allowed" : "Denied"}
                </p>
                <ul className="mt-1 list-inside list-disc text-xs">
                  {result.decision.reasons.map((reason) => <li key={reason}>{reason}</li>)}
                </ul>
              </div>
            )}
            <p className="mt-3 text-sm font-medium">Scope of {result.user.full_name}</p>
            <KeyValue label="Own department(s)">{result.scope.departments.map(departmentLabel).join(", ")}</KeyValue>
            <KeyValue label="Own department, up to">{SENSITIVITY[result.scope.max_rank_own_dept] ?? "nothing"}</KeyValue>
            <KeyValue label="Other departments, up to">{SENSITIVITY[result.scope.max_rank_other_dept] ?? "nothing"}</KeyValue>
            <KeyValue label="Regions">{result.scope.all_regions ? "all" : result.scope.regions.join(", ").toUpperCase()}</KeyValue>
            <KeyValue label="Sees personal data">{result.scope.can_read_pii ? "yes" : "no, masked"}</KeyValue>
          </div>
          <div>
            <p className="text-sm font-medium">Documents this user can read today</p>
            <div className="mt-2 space-y-2">
              {result.visible_documents.map((row) => (
                <div key={row.sensitivity}>
                  <div className="mb-1 flex justify-between text-xs">
                    <SensitivityBadge rank={row.rank} />
                    <span className="tabular-nums">{row.visible.toLocaleString()} of {row.total.toLocaleString()}</span>
                  </div>
                  <div className="h-1.5 overflow-hidden rounded-full bg-surface-muted">
                    <div className="h-full rounded-full bg-primary" style={{ width: `${row.total ? (row.visible / row.total) * 100 : 0}%` }} />
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </Card>
  );
}

function PoliciesPage() {
  const policy = useQuery({ queryKey: ["policy", "active"], queryFn: () => api<ActivePolicy>("/policies/active") });
  const versions = useQuery({
    queryKey: ["policy", "versions"],
    queryFn: () => api<{ version: number; note: string; is_active: boolean; rego_sha256: string; created_at: string }[]>("/policies/versions"),
  });
  return (
    <PageBody>
      <PageHeader
        title="Policies"
        description="Who may read what. The rules are written as policy code and evaluated by a policy engine; the limits and settings below are its data. Every change creates a new, immutable version."
        actions={policy.data && <Badge tone="primary">Active version {policy.data.version}</Badge>}
      />
      {policy.isLoading && <Skeleton className="h-96" />}
      {policy.error && <ErrorNote error={policy.error} />}
      {policy.data && (
        <Tabs defaultValue="roles">
          <TabsList className="mb-4 px-0">
            <TabsTrigger value="roles">Roles and limits</TabsTrigger>
            <TabsTrigger value="settings">Privacy settings</TabsTrigger>
            <TabsTrigger value="simulate">Simulator</TabsTrigger>
            <TabsTrigger value="levels">Sensitivity levels</TabsTrigger>
            <TabsTrigger value="code">Policy code</TabsTrigger>
            <TabsTrigger value="history">History</TabsTrigger>
          </TabsList>
          <TabsContent value="roles"><RoleMatrix policy={policy.data} /></TabsContent>
          <TabsContent value="settings"><SettingsCard policy={policy.data} /></TabsContent>
          <TabsContent value="simulate"><Simulator /></TabsContent>
          <TabsContent value="levels">
            <Card>
              <CardHeader title="Sensitivity levels" description="Every document carries exactly one. A region label restricts a document further to users of that region." />
              <ul className="divide-y">
                {policy.data.sensitivity_levels.map((level) => (
                  <li key={level.rank} className="flex items-center gap-3 px-4 py-3">
                    <SensitivityBadge rank={level.rank} />
                    <span className="text-sm">{level.description}</span>
                    {level.rank > policy.data.config.egress_max_rank && <Badge tone="info" className="ml-auto">Never sent to an external model</Badge>}
                  </li>
                ))}
              </ul>
            </Card>
          </TabsContent>
          <TabsContent value="code">
            <Card>
              <CardHeader title="Policy code (Rego)" description={`Read-only. SHA-256 ${policy.data.rego_sha256.slice(0, 16)}… · changed only through a reviewed deployment`} />
              <pre className="scroll-thin max-h-[65vh] overflow-auto p-4 text-xs leading-5">{policy.data.rego}</pre>
            </Card>
          </TabsContent>
          <TabsContent value="history">
            <Card>
              <CardHeader title="Version history" description="Each answer's audit entry names the policy version that applied." action={<History className="h-4 w-4 text-muted" />} />
              <Table>
                <thead><tr><Th>Version</Th><Th>Change</Th><Th>Published</Th><Th>Code hash</Th></tr></thead>
                <tbody>
                  {versions.data?.map((version) => (
                    <tr key={version.version}>
                      <Td className="font-medium">{version.version} {version.is_active && <Badge tone="ok">active</Badge>}</Td>
                      <Td>{version.note}</Td>
                      <Td className="whitespace-nowrap text-muted">{dateTime(version.created_at)}</Td>
                      <Td><code className="text-xs text-muted">{version.rego_sha256.slice(0, 12)}</code></Td>
                    </tr>
                  ))}
                </tbody>
              </Table>
            </Card>
          </TabsContent>
        </Tabs>
      )}
    </PageBody>
  );
}

export default function Page() {
  return (
    <RequirePermission permission="policy:read">
      <PoliciesPage />
    </RequirePermission>
  );
}
