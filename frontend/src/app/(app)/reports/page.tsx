"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, XCircle } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { StatusBadge } from "@/components/badges";
import { BarList, MethodComparison, OutcomeColumns, TrendArea } from "@/components/charts";
import { RunDetail } from "@/components/run-view";
import { PageBody, RequirePermission } from "@/components/shell";
import { Badge, Button, Card, CardHeader, Dialog, EmptyState, ErrorNote, PageHeader, Select, Skeleton, Stat, Table, Tabs, TabsContent, TabsList, TabsTrigger, Td, Textarea, Th } from "@/components/ui";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { EvalRun, RunResult, Verdict } from "@/lib/types";
import { VERDICT_META, dateTime, errorMessage, percent, relativeTime, titleCase } from "@/lib/utils";

interface ValidationReport {
  window_days: number; runs: number; outcomes: Record<string, number>; verdicts: Record<string, number>;
  claims_checked: number; sentences_removed: number; caught_rate: number | null; avg_trust: number | null;
  avg_confidence: number | null; runs_with_correction: number; runs_needing_review: number;
  daily: { date: string; runs: number; avg_trust: number | null; avg_confidence: number | null; answered: number; abstained: number; denied: number }[];
}
interface ReviewRow {
  id: string; run_id: string; reason: string; status: string; resolution: string | null; note: string;
  created_at: string; asked_by: string; trust_score: number | null; confidence: number | null; outcome: string | null;
  kind: string; feedback: { comment: string; rating: number } | null;
}
type MethodBlock = Record<string, { catch_rate: number; false_alarm_rate: number; precision: number; f1: number }>;

const SUITE_TITLE: Record<string, string> = {
  verifier_injected: "Verifier: errors injected into this corpus",
  verifier_ragtruth: "Verifier: RAGTruth benchmark",
  verifier_halueval: "Verifier: HaluEval benchmark",
  retrieval: "Retrieval quality",
  access_control: "Access control",
  end_to_end: "End-to-end answers",
};
const REASON: Record<string, string> = {
  low_trust: "Low trust score", sentences_removed: "Sentences were removed", conflicting_sources: "Sources disagree",
  negative_feedback: "Reported by the reader",
};

function Overview() {
  const [days, setDays] = useState(30);
  const report = useQuery({ queryKey: ["reports", "validation", days], queryFn: () => api<ValidationReport>("/reports/validation", { query: { days } }) });
  if (report.isLoading) return <Skeleton className="h-96" />;
  if (report.error) return <ErrorNote error={report.error} />;
  const data = report.data!;
  const verdictRows = (Object.keys(VERDICT_META) as Verdict[])
    .filter((verdict) => data.verdicts[verdict])
    .map((verdict) => ({ label: VERDICT_META[verdict].label, value: data.verdicts[verdict] }));
  return (
    <div className="space-y-4">
      <div className="flex justify-end">
        <Select aria-label="Time window" className="w-36" value={days} onChange={(e) => setDays(Number(e.target.value))}>
          <option value={7}>Last 7 days</option><option value={30}>Last 30 days</option><option value={90}>Last 90 days</option>
        </Select>
      </div>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Stat label="Questions answered or declined" value={data.runs.toLocaleString()} />
        <Stat label="Sentences checked" value={data.claims_checked.toLocaleString()} />
        <Stat
          label="Caught before reaching the reader"
          value={data.sentences_removed.toLocaleString()}
          hint={data.caught_rate !== null ? `${percent(data.caught_rate, 1)} of drafted sentences failed verification and were removed` : undefined}
          tone={data.sentences_removed ? "warn" : undefined}
        />
        <Stat label="Average trust score" value={data.avg_trust ?? "–"} hint={`average confidence ${percent(data.avg_confidence)}`} />
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader title="Outcomes per day" description="Answered, declined for lack of verified evidence, or denied by policy" />
          <div className="p-4">{data.daily.length ? <OutcomeColumns data={data.daily} /> : <EmptyState title="No runs in this period" />}</div>
        </Card>
        <Card>
          <CardHeader title="Average trust score per day" description="Out of 100" />
          <div className="p-4">{data.daily.some((d) => d.avg_trust !== null) ? <TrendArea data={data.daily} valueKey="avg_trust" label="Average trust" domain={[0, 100]} /> : <EmptyState title="No answered questions in this period" />}</div>
        </Card>
        <Card>
          <CardHeader title="Verdicts on drafted sentences" description="Every sentence the generator wrote, including those removed" />
          <div className="p-4"><BarList rows={verdictRows} empty="No sentences were checked in this period." /></div>
        </Card>
        <Card>
          <CardHeader title="Self-correction" />
          <div className="grid grid-cols-2 gap-3 p-4">
            <Stat label="Runs that needed a rewrite" value={data.runs_with_correction} hint={data.runs ? percent(data.runs_with_correction / data.runs) + " of runs" : undefined} />
            <Stat label="Waiting for human review" value={data.runs_needing_review} tone={data.runs_needing_review ? "warn" : "ok"} />
          </div>
        </Card>
      </div>
    </div>
  );
}

function ReviewQueue() {
  const canReview = useAuth((s) => s.has("review:write"));
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<"open" | "resolved">("open");
  const [selected, setSelected] = useState<ReviewRow | null>(null);
  const [note, setNote] = useState("");
  const [tab, setTab] = useState("claims");
  const queue = useQuery({ queryKey: ["reports", "review", status], queryFn: () => api<ReviewRow[]>("/reports/review", { query: { status } }) });
  const detail = useQuery({
    queryKey: ["reports", "review", "item", selected?.id],
    queryFn: () => api<{ run: RunResult }>(`/reports/review/${selected!.id}`),
    enabled: Boolean(selected) && canReview,
  });
  const resolve = useMutation({
    mutationFn: (resolution: string) => api<void>(`/reports/review/${selected!.id}/resolve`, { method: "POST", body: { resolution, note } }),
    onSuccess: () => { toast.success("Decision recorded"); setSelected(null); setNote(""); void queryClient.invalidateQueries({ queryKey: ["reports"] }); },
    onError: (error) => toast.error(errorMessage(error)),
  });
  return (
    <Card>
      <CardHeader
        title="Human review queue"
        description="Answers with a low trust score, removed sentences, disagreeing sources or a reader's report. A reviewer's decision becomes evaluation data."
        action={
          <Select aria-label="Status" className="w-32" value={status} onChange={(e) => setStatus(e.target.value as "open")}>
            <option value="open">Open</option><option value="resolved">Resolved</option>
          </Select>
        }
      />
      {queue.error && <div className="p-3"><ErrorNote error={queue.error} /></div>}
      <Table>
        <thead><tr><Th>Raised</Th><Th>Why</Th><Th>Asked by</Th><Th className="text-right">Trust</Th><Th>Reader&apos;s note</Th><Th>Status</Th><Th /></tr></thead>
        <tbody>
          {queue.data?.map((row) => (
            <tr key={row.id}>
              <Td className="whitespace-nowrap text-muted">{relativeTime(row.created_at)}</Td>
              <Td>{REASON[row.reason] ?? titleCase(row.reason)}</Td>
              <Td className="max-w-48 truncate">{row.asked_by}</Td>
              <Td className="text-right tabular-nums">{row.trust_score !== null ? Math.round(row.trust_score) : "–"}</Td>
              <Td className="max-w-xs truncate text-muted">{row.feedback?.comment || row.note || "–"}</Td>
              <Td>{row.resolution ? <Badge tone="ok">{titleCase(row.resolution)}</Badge> : <StatusBadge status={row.status} />}</Td>
              <Td>{canReview && <Button size="sm" variant="outline" onClick={() => setSelected(row)}>Review</Button>}</Td>
            </tr>
          ))}
        </tbody>
      </Table>
      {queue.data?.length === 0 && <EmptyState title={status === "open" ? "Nothing is waiting for review" : "No resolved items yet"} />}
      <Dialog open={selected !== null} onOpenChange={(open) => !open && setSelected(null)} title="Review an answer" description={selected ? REASON[selected.reason] ?? selected.reason : ""} wide>
        {detail.isLoading && <Skeleton className="h-64" />}
        {detail.error && <ErrorNote error={detail.error} />}
        {detail.data && (
          <div>
            <p className="text-xs text-muted">Question</p>
            <p className="text-sm">{detail.data.run.question}</p>
            <p className="mt-3 text-xs text-muted">Answer given</p>
            <p className="whitespace-pre-wrap text-sm">{detail.data.run.answer}</p>
            <div className="mt-3 h-80 overflow-hidden rounded-md border">
              <RunDetail run={detail.data.run} selectedClaim={null} onSelectClaim={() => undefined} activeEvidence={null} tab={tab} onTabChange={setTab} />
            </div>
            {selected?.status === "open" && (
              <>
                <Textarea className="mt-3" value={note} onChange={(e) => setNote(e.target.value)} placeholder="Reviewer's note (optional)" maxLength={2000} />
                <div className="mt-3 flex flex-wrap justify-end gap-2">
                  <Button variant="outline" loading={resolve.isPending} onClick={() => resolve.mutate("not_reviewable")}>Cannot judge</Button>
                  <Button variant="outline" loading={resolve.isPending} onClick={() => resolve.mutate("partially_correct")}>Partly correct</Button>
                  <Button variant="danger" loading={resolve.isPending} onClick={() => resolve.mutate("incorrect")}><XCircle className="h-4 w-4" /> Incorrect</Button>
                  <Button loading={resolve.isPending} onClick={() => resolve.mutate("correct")}><CheckCircle2 className="h-4 w-4" /> Correct</Button>
                </div>
              </>
            )}
          </div>
        )}
      </Dialog>
    </Card>
  );
}

function Gates({ run }: { run: EvalRun }) {
  return (
    <ul className="space-y-1.5">
      {(run.gates.criteria ?? []).map((criterion) => (
        <li key={criterion.name} className="flex items-center justify-between gap-3 text-sm">
          <span className="flex items-center gap-1.5">
            {criterion.passed ? <CheckCircle2 className="h-4 w-4 shrink-0 text-ok" aria-label="Met" /> : <XCircle className="h-4 w-4 shrink-0 text-bad" aria-label="Not met" />}
            {criterion.name}
          </span>
          <span className="shrink-0 tabular-nums">
            {criterion.value ?? "not measured"}
            <span className="text-xs text-muted"> (target {criterion.minimum !== null ? `≥ ${criterion.minimum}` : `≤ ${criterion.maximum}`})</span>
          </span>
        </li>
      ))}
    </ul>
  );
}

function Benchmarks() {
  const latest = useQuery({ queryKey: ["reports", "evals", "latest"], queryFn: () => api<EvalRun[]>("/reports/evals/latest") });
  if (latest.isLoading) return <Skeleton className="h-96" />;
  if (latest.error) return <ErrorNote error={latest.error} />;
  if (!latest.data?.length) {
    return (
      <Card>
        <EmptyState
          title="No evaluation has been run yet"
          description="Run scripts/train_calibrator.py for the verifier benchmarks and scripts/run_eval.py for retrieval, access control and end-to-end results. They appear here."
        />
      </Card>
    );
  }
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      {latest.data.map((run) => {
        const sentence = run.metrics.sentence_level as MethodBlock | undefined;
        const response = run.metrics.response_level as MethodBlock | undefined;
        const block = run.suite === "verifier_halueval" ? response : sentence;
        const configurations = run.metrics.configurations as Record<string, Record<string, number>> | undefined;
        return (
          <Card key={run.id} className={run.suite.startsWith("verifier") || run.suite === "retrieval" ? "lg:col-span-2" : undefined}>
            <CardHeader
              title={SUITE_TITLE[run.suite] ?? titleCase(run.suite)}
              description={`${run.sample_count.toLocaleString()} samples · ${dateTime(run.started_at)}`}
              action={run.gate_passed === null ? null : run.gate_passed ? <Badge tone="ok">All targets met</Badge> : <Badge tone="warn">Some targets not met</Badge>}
            />
            <div className="grid gap-4 p-4 md:grid-cols-2">
              <div>
                <p className="mb-2 text-xs text-muted">{run.notes}</p>
                <Gates run={run} />
              </div>
              {block && (
                <div>
                  <p className="mb-2 text-xs font-medium">Compared with the earlier approaches, on the same held-out data</p>
                  <MethodComparison
                    rows={[
                      { measure: "Errors caught", v1_overlap: block.v1_overlap.catch_rate, grounding_only: block.grounding_only.catch_rate, cascade: block.cascade.catch_rate },
                      { measure: "False alarms", v1_overlap: block.v1_overlap.false_alarm_rate, grounding_only: block.grounding_only.false_alarm_rate, cascade: block.cascade.false_alarm_rate },
                      { measure: "F1", v1_overlap: block.v1_overlap.f1, grounding_only: block.grounding_only.f1, cascade: block.cascade.f1 },
                    ]}
                  />
                </div>
              )}
              {configurations && (
                <div className="md:col-span-2">
                  <Table>
                    <thead><tr><Th>Retrieval configuration</Th><Th className="text-right">Recall@1</Th><Th className="text-right">Recall@3</Th><Th className="text-right">Recall@8</Th><Th className="text-right">MRR</Th><Th className="text-right">Median latency</Th></tr></thead>
                    <tbody>
                      {Object.entries(configurations).map(([name, row]) => (
                        <tr key={name}>
                          <Td className="font-medium">{titleCase(name)}</Td>
                          <Td className="text-right tabular-nums">{percent(row["recall@1"])}</Td>
                          <Td className="text-right tabular-nums">{percent(row["recall@3"])}</Td>
                          <Td className="text-right tabular-nums">{percent(row["recall@8"])}</Td>
                          <Td className="text-right tabular-nums">{row.mrr?.toFixed(3)}</Td>
                          <Td className="text-right tabular-nums">{row.latency_p50_ms} ms</Td>
                        </tr>
                      ))}
                    </tbody>
                  </Table>
                </div>
              )}
              {run.suite === "access_control" && (
                <div className="text-sm">
                  <p>Canary facts tested: <strong>{String(run.metrics.canaries)}</strong> across <strong>{String(run.metrics.roles)}</strong> roles</p>
                  <p>Unauthorised attempts: <strong>{String(run.metrics.unauthorized_cases)}</strong>, leaks: <strong>{(run.metrics.leaks as unknown[]).length}</strong></p>
                  <p>Authorised recall: <strong>{percent(run.metrics.authorized_recall as number)}</strong></p>
                </div>
              )}
              {run.suite === "end_to_end" && (
                <div className="text-sm">
                  <p>Correct answers: <strong>{String(run.metrics.correct)}</strong> of {String(run.metrics.answerable)}</p>
                  <p>Correctly declined: <strong>{String(run.metrics.correct_abstentions)}</strong> of {String(run.metrics.unanswerable)} unanswerable questions</p>
                  <p>Median latency: <strong>{String(run.metrics.latency_p50_ms)} ms</strong> · LLM calls: {String(run.metrics.llm_calls)}</p>
                  <p>Sentences removed by the verifier: {String(run.metrics.sentences_removed_by_verifier)}</p>
                </div>
              )}
            </div>
          </Card>
        );
      })}
    </div>
  );
}

function ReportsPage() {
  return (
    <PageBody>
      <PageHeader
        title="Validation Reports"
        description="How well answers are verifying in use, which answers need a human decision, and how the system scores on benchmarks. Every number on this page is measured."
      />
      <Tabs defaultValue="overview">
        <TabsList className="mb-4 px-0">
          <TabsTrigger value="overview">In use</TabsTrigger>
          <TabsTrigger value="review">Review queue</TabsTrigger>
          <TabsTrigger value="benchmarks">Benchmarks</TabsTrigger>
        </TabsList>
        <TabsContent value="overview"><Overview /></TabsContent>
        <TabsContent value="review"><ReviewQueue /></TabsContent>
        <TabsContent value="benchmarks"><Benchmarks /></TabsContent>
      </Tabs>
    </PageBody>
  );
}

export default function Page() {
  return (
    <RequirePermission permission="reports:read">
      <ReportsPage />
    </RequirePermission>
  );
}
