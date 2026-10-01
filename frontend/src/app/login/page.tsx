"use client";

import { KeyRound, ShieldCheck } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";

import { Button, Card, Field, Input } from "@/components/ui";
import { useAuth } from "@/lib/auth";
import { ROLE_LABEL, departmentLabel, errorMessage } from "@/lib/utils";

// Seeded demo accounts. The shared password is DEMO_PASSWORD in the project's .env file.
const PERSONAS = [
  { email: "emma.employee@aurelia-systems.example", name: "Emma Lindqvist", role: "employee", department: "engineering", region: "EMEA" },
  { email: "marcus.manager@aurelia-systems.example", name: "Marcus Okafor", role: "manager", department: "hr", region: "AMER" },
  { email: "fatima.finance@aurelia-systems.example", name: "Fatima Al-Sayed", role: "manager", department: "finance", region: "EMEA" },
  { email: "arjun.analyst@aurelia-systems.example", name: "Arjun Mehta", role: "analyst", department: "security", region: "APAC" },
  { email: "clara.compliance@aurelia-systems.example", name: "Clara Jensen", role: "compliance_officer", department: "compliance", region: "EMEA" },
  { email: "elena.executive@aurelia-systems.example", name: "Elena Vasquez", role: "executive", department: "company", region: "AMER" },
  { email: "adam.admin@aurelia-systems.example", name: "Adam Novak", role: "admin", department: "it", region: "AMER" },
];

function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  const { status, bootstrap, login, completeMfa } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [mfaToken, setMfaToken] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const next = params.get("next") && params.get("next")!.startsWith("/") ? params.get("next")! : "/ask";

  useEffect(() => {
    void bootstrap();
  }, [bootstrap]);

  useEffect(() => {
    if (status === "signed-in") router.replace(next);
  }, [status, router, next]);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (mfaToken) {
        await completeMfa(mfaToken, code.trim());
      } else {
        const result = await login(email.trim(), password);
        if (result.mfaToken) setMfaToken(result.mfaToken);
      }
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid min-h-full lg:grid-cols-[1fr_1.1fr]">
      <div className="flex items-center justify-center p-6">
        <div className="w-full max-w-sm">
          <div className="mb-6 flex items-center gap-2.5">
            <span className="flex h-10 w-10 items-center justify-center rounded-lg bg-primary text-lg font-bold text-primary-foreground">P</span>
            <div>
              <p className="text-lg font-semibold leading-5">PRAMĀṆA</p>
              <p className="text-xs text-muted">Privacy-aware enterprise knowledge</p>
            </div>
          </div>
          <form onSubmit={submit} className="space-y-4">
            {mfaToken ? (
              <>
                <p className="text-sm text-muted">Enter the 6-digit code from your authenticator app, or a recovery code.</p>
                <Field label="Verification code">
                  <Input value={code} onChange={(e) => setCode(e.target.value)} autoFocus inputMode="numeric" autoComplete="one-time-code" required />
                </Field>
              </>
            ) : (
              <>
                <Field label="Email">
                  <Input type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="username" autoFocus required />
                </Field>
                <Field label="Password">
                  <Input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" required />
                </Field>
              </>
            )}
            {error && (
              <p role="alert" className="rounded-md bg-bad-soft px-3 py-2 text-sm text-bad">
                {error}
              </p>
            )}
            <Button type="submit" size="lg" className="w-full" loading={busy}>
              {mfaToken ? <ShieldCheck className="h-4 w-4" /> : <KeyRound className="h-4 w-4" />}
              {mfaToken ? "Verify" : "Sign in"}
            </Button>
            {mfaToken && (
              <Button type="button" variant="ghost" className="w-full" onClick={() => { setMfaToken(null); setCode(""); }}>
                Back
              </Button>
            )}
          </form>
        </div>
      </div>

      <div className="hidden border-l bg-surface p-8 lg:block">
        <h2 className="text-base font-semibold">Demo accounts</h2>
        <p className="mt-1 max-w-lg text-sm text-muted">
          Each account sees a different slice of the same knowledge base. Pick one to fill in the email, then type the demo
          password (the value of DEMO_PASSWORD in the project&apos;s .env file).
        </p>
        <div className="mt-4 grid gap-2 xl:grid-cols-2">
          {PERSONAS.map((persona) => (
            <Card key={persona.email} className="p-0">
              <button
                type="button"
                onClick={() => { setEmail(persona.email); setMfaToken(null); setError(null); }}
                className="block w-full rounded-lg p-3 text-left hover:bg-surface-muted"
              >
                <p className="text-sm font-medium">{persona.name}</p>
                <p className="text-xs text-muted">
                  {ROLE_LABEL[persona.role]} · {departmentLabel(persona.department)} · {persona.region}
                </p>
              </button>
            </Card>
          ))}
        </div>
        <ul className="mt-6 max-w-lg space-y-2 text-sm text-muted">
          <li>Employees read PUBLIC and INTERNAL documents of their region.</li>
          <li>Managers also read CONFIDENTIAL documents of their own department.</li>
          <li>Analysts and compliance officers read CONFIDENTIAL documents of every department.</li>
          <li>Executives read everything, including RESTRICTED documents and personal data.</li>
          <li>Administrators run the platform but get no extra access to content.</li>
        </ul>
      </div>
    </div>
  );
}

export default function LoginPage() {
  return (
    <Suspense>
      <LoginForm />
    </Suspense>
  );
}
