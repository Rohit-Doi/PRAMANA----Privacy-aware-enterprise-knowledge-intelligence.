"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, LogOut, Moon, ShieldCheck, Sun } from "lucide-react";
import { QRCodeSVG } from "qrcode.react";
import { useState } from "react";
import { toast } from "sonner";

import { SensitivityBadge } from "@/components/badges";
import { PageBody } from "@/components/shell";
import { Badge, Button, Card, CardHeader, ErrorNote, Field, Input, KeyValue, PageHeader } from "@/components/ui";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useTheme } from "@/lib/theme";
import { ROLE_LABEL, dateTime, departmentLabel, errorMessage, relativeTime } from "@/lib/utils";

interface SessionRow { id: string; user_agent: string; ip_address: string; created_at: string; last_used_at: string; current: boolean }

function Profile() {
  const me = useAuth((s) => s.me)!;
  return (
    <Card>
      <CardHeader title="Your account" description="Your role and attributes are set by an administrator and decide what you can read." />
      <div className="p-4">
        <KeyValue label="Name">{me.full_name}</KeyValue>
        <KeyValue label="Email">{me.email}</KeyValue>
        <KeyValue label="Role">{ROLE_LABEL[me.role] ?? me.role}</KeyValue>
        <KeyValue label="Department">{[me.department, ...me.extra_departments].map(departmentLabel).join(", ")}</KeyValue>
        <KeyValue label="Region">{me.region.toUpperCase()}</KeyValue>
        <KeyValue label="Own department, reads up to"><SensitivityBadge rank={me.max_rank_own_dept} /></KeyValue>
        <KeyValue label="Other departments, reads up to"><SensitivityBadge rank={me.max_rank_other_dept} /></KeyValue>
        <KeyValue label="Personal data">{me.permissions.includes("pii:read") ? "visible" : "masked"}</KeyValue>
        <p className="mt-3 text-xs font-medium">Permissions</p>
        <div className="mt-1 flex flex-wrap gap-1">
          {me.permissions.map((permission) => <Badge key={permission}>{permission}</Badge>)}
        </div>
      </div>
    </Card>
  );
}

function Appearance() {
  const theme = useTheme((s) => s.theme);
  const toggle = useTheme((s) => s.toggle);
  return (
    <Card>
      <CardHeader title="Appearance" />
      <div className="flex items-center justify-between p-4">
        <p className="text-sm">Theme: {theme === "dark" ? "dark" : "light"}</p>
        <Button variant="outline" onClick={toggle}>
          {theme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />} Switch to {theme === "dark" ? "light" : "dark"}
        </Button>
      </div>
    </Card>
  );
}

function Mfa() {
  const me = useAuth((s) => s.me)!;
  const [setup, setSetup] = useState<{ secret: string; otpauth_uri: string } | null>(null);
  const [code, setCode] = useState("");
  const [recovery, setRecovery] = useState<string[] | null>(null);
  const [password, setPassword] = useState("");
  const status = useQuery({ queryKey: ["mfa-status", me.user_id], queryFn: () => api<{ mfa_enabled: boolean }>("/auth/mfa/status") });
  const queryClient = useQueryClient();
  const refresh = () => void queryClient.invalidateQueries({ queryKey: ["mfa-status"] });

  const begin = useMutation({
    mutationFn: () => api<{ secret: string; otpauth_uri: string }>("/auth/mfa/setup", { method: "POST" }),
    onSuccess: setSetup,
    onError: (error) => toast.error(errorMessage(error)),
  });
  const confirm = useMutation({
    mutationFn: () => api<{ recovery_codes: string[] }>("/auth/mfa/confirm", { method: "POST", body: { code } }),
    onSuccess: (result) => { setRecovery(result.recovery_codes); setSetup(null); setCode(""); refresh(); toast.success("Two-step sign-in is on"); },
    onError: (error) => toast.error(errorMessage(error)),
  });
  const disable = useMutation({
    mutationFn: () => api<void>("/auth/mfa/disable", { method: "POST", body: { password } }),
    onSuccess: () => { setPassword(""); setRecovery(null); refresh(); toast.success("Two-step sign-in is off"); },
    onError: (error) => toast.error(errorMessage(error)),
  });
  const enabled = status.data?.mfa_enabled ?? false;

  return (
    <Card>
      <CardHeader
        title="Two-step sign-in"
        description="A code from an authenticator app is required after your password."
        action={enabled ? <Badge tone="ok"><ShieldCheck className="h-3 w-3" /> On</Badge> : <Badge>Off</Badge>}
      />
      <div className="p-4">
        {recovery && (
          <div className="mb-4 rounded-md bg-warn-soft p-3 text-sm text-warn">
            <p className="font-medium">Save these recovery codes now. They are shown once; each works once.</p>
            <div className="mt-2 grid grid-cols-2 gap-1 font-mono text-xs text-foreground sm:grid-cols-4">
              {recovery.map((value) => <code key={value}>{value}</code>)}
            </div>
            <Button size="sm" variant="outline" className="mt-2" onClick={() => { void navigator.clipboard.writeText(recovery.join("\n")); toast.success("Copied"); }}>
              <Copy className="h-3.5 w-3.5" /> Copy
            </Button>
          </div>
        )}
        {!enabled && !setup && (
          <Button loading={begin.isPending} onClick={() => begin.mutate()}>
            <ShieldCheck className="h-4 w-4" /> Turn on two-step sign-in
          </Button>
        )}
        {setup && (
          <div className="grid gap-4 sm:grid-cols-[auto_1fr]">
            <div className="rounded-md bg-white p-3">
              <QRCodeSVG value={setup.otpauth_uri} size={148} />
            </div>
            <div>
              <p className="text-sm">1. Scan the code with an authenticator app, or enter this key by hand:</p>
              <code className="mt-1 block break-all rounded bg-surface-muted p-2 text-xs">{setup.secret}</code>
              <p className="mt-3 text-sm">2. Enter the 6-digit code the app shows:</p>
              <div className="mt-1 flex gap-2">
                <Input value={code} onChange={(e) => setCode(e.target.value)} inputMode="numeric" maxLength={8} aria-label="Verification code" className="w-40" />
                <Button disabled={code.trim().length < 6} loading={confirm.isPending} onClick={() => confirm.mutate()}>Confirm</Button>
              </div>
            </div>
          </div>
        )}
        {enabled && (
          <div>
            <p className="text-sm text-muted">To turn it off, confirm your password.</p>
            <div className="mt-2 flex gap-2">
              <Input type="password" value={password} onChange={(e) => setPassword(e.target.value)} aria-label="Password" autoComplete="current-password" className="max-w-xs" />
              <Button variant="outline" disabled={!password} loading={disable.isPending} onClick={() => disable.mutate()}>Turn off</Button>
            </div>
          </div>
        )}
      </div>
    </Card>
  );
}

function Password() {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const change = useMutation({
    mutationFn: () => api<void>("/auth/password", { method: "POST", body: { current_password: current, new_password: next } }),
    onSuccess: () => { setCurrent(""); setNext(""); toast.success("Password changed"); },
  });
  return (
    <Card>
      <CardHeader title="Password" description="At least 12 characters, with a letter and a digit." />
      <form className="grid gap-3 p-4 sm:grid-cols-[1fr_1fr_auto]" onSubmit={(event) => { event.preventDefault(); change.mutate(); }}>
        <Field label="Current password">
          <Input type="password" value={current} onChange={(e) => setCurrent(e.target.value)} autoComplete="current-password" />
        </Field>
        <Field label="New password">
          <Input type="password" value={next} onChange={(e) => setNext(e.target.value)} autoComplete="new-password" />
        </Field>
        <div className="flex items-end">
          <Button type="submit" disabled={!current || next.length < 12} loading={change.isPending}>Change</Button>
        </div>
        {change.error && <div className="sm:col-span-3"><ErrorNote error={change.error} /></div>}
      </form>
    </Card>
  );
}

function Sessions() {
  const queryClient = useQueryClient();
  const sessions = useQuery({ queryKey: ["sessions"], queryFn: () => api<SessionRow[]>("/auth/sessions") });
  const revoke = useMutation({
    mutationFn: (id: string) => api<void>(`/auth/sessions/${id}`, { method: "DELETE" }),
    onSuccess: () => { toast.success("Session signed out"); void queryClient.invalidateQueries({ queryKey: ["sessions"] }); },
    onError: (error) => toast.error(errorMessage(error)),
  });
  return (
    <Card>
      <CardHeader title="Where you are signed in" description="Sign out a session you do not recognise." />
      <ul className="divide-y">
        {sessions.data?.map((session) => (
          <li key={session.id} className="flex items-center justify-between gap-3 px-4 py-3">
            <div className="min-w-0">
              <p className="truncate text-sm">{session.user_agent || "Unknown device"} {session.current && <Badge tone="ok">this device</Badge>}</p>
              <p className="text-xs text-muted">{session.ip_address || "unknown address"} · signed in {dateTime(session.created_at)} · active {relativeTime(session.last_used_at)}</p>
            </div>
            {!session.current && (
              <Button size="sm" variant="outline" loading={revoke.isPending} onClick={() => revoke.mutate(session.id)}>
                <LogOut className="h-3.5 w-3.5" /> Sign out
              </Button>
            )}
          </li>
        ))}
      </ul>
    </Card>
  );
}

export default function SettingsPage() {
  const me = useAuth((s) => s.me);
  if (!me) return null;
  return (
    <PageBody className="max-w-4xl">
      <PageHeader title="Settings" />
      <div className="space-y-4">
        <Profile />
        <Appearance />
        <Mfa />
        <Password />
        <Sessions />
      </div>
    </PageBody>
  );
}
