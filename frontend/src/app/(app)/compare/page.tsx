"use client";

import { useQuery } from "@tanstack/react-query";
import { GitCompareArrows } from "lucide-react";
import { useState } from "react";

import { SensitivityBadge } from "@/components/badges";
import { AnswerText, OutcomeIcon, RunSummary } from "@/components/run-view";
import { PageBody, RequirePermission } from "@/components/shell";
import { Badge, Button, Card, ErrorNote, Field, Input, PageHeader, Select, Spinner } from "@/components/ui";
import { api } from "@/lib/api";
import type { RunResult, UserView } from "@/lib/types";
import { ROLE_LABEL, departmentLabel, errorMessage } from "@/lib/utils";

const QUESTIONS = [
  "What is the proposed purchase price for the acquisition?",
  "What is the maximum base salary for level L4?",
  "How many days of annual leave do employees get?",
  "How many customer records were exposed in incident INC-2026-0417?",
  "Who must approve purchases above $100,000?",
];

interface Side {
  loading: boolean;
  run: RunResult | null;
  error: string | null;
}
const EMPTY: Side = { loading: false, run: null, error: null };

function Column({ user, side }: { user: UserView | undefined; side: Side }) {
  return (
    <Card className="flex min-h-64 flex-col p-4">
      {user && (
        <div className="mb-3 border-b pb-3">
          <p className="text-sm font-semibold">{user.full_name}</p>
          <p className="text-xs text-muted">
            {ROLE_LABEL[user.role] ?? user.role} · {departmentLabel(user.department)} · {user.region.toUpperCase()}
          </p>
        </div>
      )}
      {side.loading && (
        <p className="flex items-center gap-2 text-sm text-muted">
          <Spinner /> Running the pipeline as this user…
        </p>
      )}
      {side.error && <ErrorNote error={new Error(side.error)} />}
      {side.run && (
        <>
          <div className="mb-2 flex items-center gap-2 text-xs font-medium text-muted">
            <OutcomeIcon outcome={side.run.outcome} />
            {side.run.outcome === "answered" ? "Verified answer" : side.run.outcome === "denied" ? "Access denied" : "No answer given"}
          </div>
          <div className="text-[15px]">
            <AnswerText run={side.run} selectedClaim={null} onSelectClaim={() => undefined} onSelectEvidence={() => undefined} />
          </div>
          <div className="mt-3">
            <RunSummary run={side.run} />
          </div>
          <p className="mt-4 text-xs font-medium text-muted">Sources this user could draw on</p>
          <ul className="mt-1 space-y-1">
            {side.run.evidence.slice(0, 5).map((item) => (
              <li key={item.ref} className="flex items-center gap-1.5 text-xs">
                <Badge tone="primary">{item.ref}</Badge>
                <SensitivityBadge rank={item.sensitivity_rank} />
                <span className="truncate">{item.title}</span>
              </li>
            ))}
            {side.run.evidence.length === 0 && <li className="text-xs text-muted">None within this user&apos;s access.</li>}
          </ul>
        </>
      )}
      {!side.loading && !side.run && !side.error && <p className="text-sm text-muted">The answer will appear here.</p>}
    </Card>
  );
}

function ComparePage() {
  const users = useQuery({ queryKey: ["users"], queryFn: () => api<UserView[]>("/users") });
  const people = (users.data ?? []).filter((u) => u.is_active && !u.is_service_account);
  const [leftId, setLeftId] = useState("");
  const [rightId, setRightId] = useState("");
  const [question, setQuestion] = useState("");
  const [left, setLeft] = useState<Side>(EMPTY);
  const [right, setRight] = useState<Side>(EMPTY);

  const leftUser = people.find((u) => u.id === leftId) ?? people.find((u) => u.role === "employee");
  const rightUser = people.find((u) => u.id === rightId) ?? people.find((u) => u.role === "executive");

  async function run(text: string) {
    if (!text.trim() || !leftUser || !rightUser) return;
    const ask = async (user: UserView, set: (side: Side) => void) => {
      set({ loading: true, run: null, error: null });
      try {
        set({ loading: false, error: null, run: await api<RunResult>("/chat/ask", { method: "POST", body: { question: text, as_user_id: user.id } }) });
      } catch (error) {
        set({ loading: false, run: null, error: errorMessage(error) });
      }
    };
    // One after the other: the local models are CPU-bound, so two at once would not be faster.
    await ask(leftUser, setLeft);
    await ask(rightUser, setRight);
  }

  return (
    <PageBody>
      <PageHeader
        title="Compare roles"
        description="Ask one question as two different users to see access control at work. The same pipeline runs twice; only the identity changes. Every simulated run is recorded in the audit log under your name."
      />
      {users.error && <ErrorNote error={users.error} />}
      <Card className="mb-4 p-4">
        <div className="grid gap-3 md:grid-cols-[1fr_1fr]">
          <Field label="First user">
            <Select value={leftUser?.id ?? ""} onChange={(e) => setLeftId(e.target.value)}>
              {people.map((u) => (
                <option key={u.id} value={u.id}>
                  {u.full_name} ({ROLE_LABEL[u.role] ?? u.role}, {departmentLabel(u.department)})
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Second user">
            <Select value={rightUser?.id ?? ""} onChange={(e) => setRightId(e.target.value)}>
              {people.map((u) => (
                <option key={u.id} value={u.id}>
                  {u.full_name} ({ROLE_LABEL[u.role] ?? u.role}, {departmentLabel(u.department)})
                </option>
              ))}
            </Select>
          </Field>
        </div>
        <form className="mt-3 flex gap-2" onSubmit={(e) => { e.preventDefault(); void run(question); }}>
          <Input value={question} onChange={(e) => setQuestion(e.target.value)} placeholder="Question to ask as both users" aria-label="Question" maxLength={2000} />
          <Button type="submit" disabled={!question.trim() || left.loading || right.loading}>
            <GitCompareArrows className="h-4 w-4" /> Compare
          </Button>
        </form>
        <div className="mt-2 flex flex-wrap gap-1.5">
          {QUESTIONS.map((q) => (
            <button key={q} onClick={() => { setQuestion(q); void run(q); }} className="rounded-full border px-2.5 py-1 text-xs hover:bg-surface-muted">
              {q}
            </button>
          ))}
        </div>
      </Card>
      <div className="grid gap-4 lg:grid-cols-2">
        <Column user={leftUser} side={left} />
        <Column user={rightUser} side={right} />
      </div>
    </PageBody>
  );
}

export default function Page() {
  return (
    <RequirePermission permission="users:simulate">
      <ComparePage />
    </RequirePermission>
  );
}
