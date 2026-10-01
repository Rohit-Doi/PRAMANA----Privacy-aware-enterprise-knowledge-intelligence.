"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowRight } from "lucide-react";
import { useState } from "react";

import { StatusBadge } from "@/components/badges";
import { AgentTimeline } from "@/components/run-view";
import { PageBody, RequirePermission } from "@/components/shell";
import { Badge, Card, CardHeader, Dialog, ErrorNote, KeyValue, PageHeader, Select, Skeleton, Spinner, Stat, Table, Tabs, TabsContent, TabsList, TabsTrigger, Td, Th } from "@/components/ui";
import { api } from "@/lib/api";
import type { RunResult } from "@/lib/types";
import { dateTime, duration, percent, relativeTime, titleCase } from "@/lib/utils";

interface AgentStats { agent: string; label: string; runs: number; failures: number; avg_ms: number; p50_ms: number; p95_ms: number; max_ms: number }
interface RunRow {
  run_id: string; kind: string; user_email: string; user_name: string; status: string; outcome: string | null;
  trust_score: number | null; confidence: number | null; risk_score: number | null; latency_ms: number | null;
  llm_calls: number; correction_rounds: number; withheld_count: number; needs_review: boolean; route: string | null;
  error: string | null; started_at: string;
}
interface LlmStatus {
  providers: { provider: string; model: string; calls: number; tokens: number; cooling_down: boolean; external: boolean }[];
  configured: boolean;
  max_calls_per_question: number;
  last_24h: { purpose: string; status: string; calls: number; tokens: number; avg_latency_ms: number; cached: number }[];
  recent: { purpose: string; provider: string; model: string; status: string; cached: boolean; tokens_in: number; tokens_out: number; latency_ms: number; error: string | null; created_at: string }[];
}
interface SystemStatus {
  environment: string; policy_version: number; models: Record<string, unknown>; llm_chain: string[];
  thresholds: Record<string, number>; documents: Record<string, number>; ingestion_jobs: Record<string, number>;
  vectors: number; graph: Record<string, number>;
}

function Pipeline({ agents }: { agents: AgentStats[] }) {
  const longest = Math.max(1, ...agents.map((a) => a.p50_ms));
  return (
    <div className="flex flex-wrap items-stretch gap-1.5">
      {agents.map((agent, index) => (
        <div key={agent.agent} className="flex items-center gap-1.5">
          <div className="w-40 rounded-lg border bg-surface p-3">
            <p className="text-xs font-semibold leading-4">{agent.label}</p>
            <p className="mt-2 text-lg font-semibold tabular-nums">{duration(agent.p50_ms)}</p>
            <p className="text-[11px] text-muted">median · p95 {duration(agent.p95_ms)}</p>
            <div className="mt-2 h-1 overflow-hidden rounded-full bg-surface-muted">
              <div className="h-full rounded-full bg-primary" style={{ width: `${(agent.p50_ms / longest) * 100}%` }} />
            </div>
            <p className="mt-2 text-[11px] text-muted">
              {agent.runs} runs{agent.failures > 0 && <span className="text-bad"> · {agent.failures} failed</span>}
            </p>
          </div>
          {index < agents.length - 1 && <ArrowRight className="h-4 w-4 shrink-0 text-muted" aria-hidden />}
        </div>
      ))}
    </div>
  );
}

function RunDialog({ runId, onClose }: { runId: string; onClose: () => void }) {
  const run = useQuery({ queryKey: ["run", runId], queryFn: () => api<RunResult>(`/runs/${runId}`) });
  return (
    <Dialog open onOpenChange={(open) => !open && onClose()} title="Run trace" description="What each agent did and how long it took. Content of another user's run is hidden." wide>
      {run.isLoading && <Spinner />}
      {run.error && <ErrorNote error={run.error} />}
      {run.data && (
        <div className="grid gap-4 md:grid-cols-[14rem_1fr]">
          <div>
            <KeyValue label="Outcome"><StatusBadge status={run.data.outcome ?? run.data.status} /></KeyValue>
            <KeyValue label="Kind">{run.data.kind}</KeyValue>
            <KeyValue label="Route">{run.data.route}</KeyValue>
            <KeyValue label="Latency">{duration(run.data.latency_ms)}</KeyValue>
            <KeyValue label="LLM calls">{run.data.llm_calls}</KeyValue>
            <KeyValue label="Correction rounds">{run.data.correction_rounds}</KeyValue>
            <KeyValue label="Trust">{run.data.trust ? Math.round(run.data.trust.score) : "–"}</KeyValue>
            <KeyValue label="Confidence">{percent(run.data.confidence)}</KeyValue>
            <KeyValue label="Withheld">{run.data.withheld_count ?? "–"}</KeyValue>
            <KeyValue label="Policy version">{run.data.policy_version ?? "–"}</KeyValue>
            <KeyValue label="Started">{dateTime(run.data.started_at)}</KeyValue>
            {run.data.error && <p className="mt-2 text-xs text-bad">{run.data.error}</p>}
          </div>
          <div className="scroll-thin max-h-[65vh] overflow-y-auto pr-1">
            <AgentTimeline steps={run.data.steps} />
          </div>
        </div>
      )}
    </Dialog>
  );
}

function MonitoringPage() {
  const [hours, setHours] = useState(24);
  const [selected, setSelected] = useState<string | null>(null);
  const agents = useQuery({ queryKey: ["monitoring", "agents", hours], queryFn: () => api<{ agents: AgentStats[]; runs: number; runs_with_correction: number }>("/monitoring/agents", { query: { hours } }), refetchInterval: 15000 });
  const runs = useQuery({ queryKey: ["monitoring", "runs"], queryFn: () => api<{ total: number; items: RunRow[] }>("/monitoring/runs", { query: { limit: 40 } }), refetchInterval: 10000 });
  const llm = useQuery({ queryKey: ["monitoring", "llm"], queryFn: () => api<LlmStatus>("/monitoring/llm"), refetchInterval: 15000 });
  const system = useQuery({ queryKey: ["monitoring", "system"], queryFn: () => api<SystemStatus>("/monitoring/system") });
  const totalFailures = agents.data?.agents.reduce((sum, a) => sum + a.failures, 0) ?? 0;

  return (
    <PageBody>
      <PageHeader
        title="Agent Monitoring"
        description="The seven agents, measured. Timings are recorded by the agents themselves on every run."
        actions={
          <Select aria-label="Time window" className="w-36" value={hours} onChange={(e) => setHours(Number(e.target.value))}>
            <option value={1}>Last hour</option><option value={24}>Last 24 hours</option><option value={168}>Last 7 days</option><option value={720}>Last 30 days</option>
          </Select>
        }
      />
      <div className="mb-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Stat label="Runs in window" value={agents.data?.runs ?? "–"} />
        <Stat label="Needed a correction round" value={agents.data ? `${agents.data.runs_with_correction}` : "–"} hint={agents.data?.runs ? percent(agents.data.runs_with_correction / agents.data.runs) + " of runs" : undefined} />
        <Stat label="Agent failures" value={totalFailures} tone={totalFailures ? "bad" : "ok"} />
        <Stat label="LLM calls today" value={llm.data?.providers.reduce((sum, p) => sum + p.calls, 0) ?? "–"} hint={`at most ${llm.data?.max_calls_per_question ?? 4} per question`} />
      </div>

      <Card className="mb-4">
        <CardHeader title="Pipeline" description="Median time per agent. Retrieval and Graph Reasoning run in parallel." />
        <div className="overflow-x-auto p-4">
          {agents.isLoading ? <Skeleton className="h-28" /> : agents.error ? <ErrorNote error={agents.error} /> : <Pipeline agents={agents.data!.agents} />}
        </div>
      </Card>

      <Tabs defaultValue="runs">
        <TabsList className="mb-4 px-0">
          <TabsTrigger value="runs">Recent runs</TabsTrigger>
          <TabsTrigger value="llm">Language model</TabsTrigger>
          <TabsTrigger value="system">System</TabsTrigger>
        </TabsList>
        <TabsContent value="runs">
          <Card>
            {runs.error && <div className="p-3"><ErrorNote error={runs.error} /></div>}
            <Table>
              <thead>
                <tr><Th>Started</Th><Th>User</Th><Th>Kind</Th><Th>Outcome</Th><Th>Route</Th><Th className="text-right">Trust</Th><Th className="text-right">Latency</Th><Th className="text-right">LLM calls</Th><Th>Flags</Th></tr>
              </thead>
              <tbody>
                {runs.isLoading && Array.from({ length: 8 }).map((_, i) => <tr key={i}><Td colSpan={9}><Skeleton className="h-5" /></Td></tr>)}
                {runs.data?.items.map((run) => (
                  <tr key={run.run_id} className="cursor-pointer hover:bg-surface-muted" onClick={() => setSelected(run.run_id)}>
                    <Td className="whitespace-nowrap text-muted">{relativeTime(run.started_at)}</Td>
                    <Td className="max-w-48 truncate">{run.user_name}</Td>
                    <Td>{run.kind}</Td>
                    <Td><StatusBadge status={run.outcome ?? run.status} /></Td>
                    <Td className="text-muted">{run.route ?? "–"}</Td>
                    <Td className="text-right tabular-nums">{run.trust_score !== null ? Math.round(run.trust_score) : "–"}</Td>
                    <Td className="text-right tabular-nums">{duration(run.latency_ms)}</Td>
                    <Td className="text-right tabular-nums">{run.llm_calls}</Td>
                    <Td>
                      <span className="flex flex-wrap gap-1">
                        {run.correction_rounds > 0 && <Badge tone="info">corrected</Badge>}
                        {run.withheld_count > 0 && <Badge tone="warn">{run.withheld_count} withheld</Badge>}
                        {run.needs_review && <Badge tone="warn">review</Badge>}
                      </span>
                    </Td>
                  </tr>
                ))}
              </tbody>
            </Table>
          </Card>
        </TabsContent>
        <TabsContent value="llm">
          <div className="grid gap-4 lg:grid-cols-2">
            <Card>
              <CardHeader title="Provider chain" description="Tried in this order. A provider that reports its quota is exhausted is skipped until it recovers." />
              <ul className="divide-y">
                {llm.data?.providers.length === 0 && <li className="p-4 text-sm text-muted">No provider is configured. Answers are quoted directly from the sources.</li>}
                {llm.data?.providers.map((provider, index) => (
                  <li key={`${provider.provider}-${provider.model}`} className="flex items-center justify-between gap-3 px-4 py-3">
                    <div>
                      <p className="text-sm font-medium">{index + 1}. {provider.model}</p>
                      <p className="text-xs text-muted">{provider.provider} · {provider.external ? "external" : "local"}</p>
                    </div>
                    <div className="text-right text-xs">
                      <p className="tabular-nums">{provider.calls} calls · {provider.tokens.toLocaleString()} tokens today</p>
                      {provider.cooling_down ? <Badge tone="warn">cooling down</Badge> : <Badge tone="ok">available</Badge>}
                    </div>
                  </li>
                ))}
              </ul>
            </Card>
            <Card>
              <CardHeader title="Calls in the last 24 hours" description="By purpose. Cached calls cost nothing." />
              <Table>
                <thead><tr><Th>Purpose</Th><Th>Status</Th><Th className="text-right">Calls</Th><Th className="text-right">Cached</Th><Th className="text-right">Tokens</Th><Th className="text-right">Avg latency</Th></tr></thead>
                <tbody>
                  {llm.data?.last_24h.map((row) => (
                    <tr key={`${row.purpose}-${row.status}`}>
                      <Td>{titleCase(row.purpose)}</Td><Td><StatusBadge status={row.status === "ok" ? "completed" : row.status} /></Td>
                      <Td className="text-right tabular-nums">{row.calls}</Td><Td className="text-right tabular-nums">{row.cached}</Td>
                      <Td className="text-right tabular-nums">{row.tokens.toLocaleString()}</Td><Td className="text-right tabular-nums">{duration(row.avg_latency_ms)}</Td>
                    </tr>
                  ))}
                </tbody>
              </Table>
            </Card>
          </div>
        </TabsContent>
        <TabsContent value="system">
          {system.error && <ErrorNote error={system.error} />}
          {system.data && (
            <div className="grid gap-4 lg:grid-cols-3">
              <Card className="p-4">
                <p className="mb-1 text-sm font-medium">Local models</p>
                {Object.entries(system.data.models).filter(([, v]) => typeof v === "string").map(([key, value]) => (
                  <KeyValue key={key} label={titleCase(key)}><span className="break-all text-xs">{String(value)}</span></KeyValue>
                ))}
              </Card>
              <Card className="p-4">
                <p className="mb-1 text-sm font-medium">Knowledge base</p>
                {Object.entries(system.data.documents).map(([status, count]) => <KeyValue key={status} label={`Documents ${status}`}>{count.toLocaleString()}</KeyValue>)}
                <KeyValue label="Passages indexed">{system.data.vectors.toLocaleString()}</KeyValue>
                <KeyValue label="Graph entities">{(system.data.graph.entities ?? 0).toLocaleString()}</KeyValue>
                <KeyValue label="Graph mentions">{(system.data.graph.mentions ?? 0).toLocaleString()}</KeyValue>
                <KeyValue label="Typed relations">{(system.data.graph.relations ?? 0).toLocaleString()}</KeyValue>
              </Card>
              <Card className="p-4">
                <p className="mb-1 text-sm font-medium">Decision thresholds</p>
                {Object.entries(system.data.thresholds).map(([key, value]) => <KeyValue key={key} label={titleCase(key)}>{value}</KeyValue>)}
                <KeyValue label="Policy version">{system.data.policy_version}</KeyValue>
                <KeyValue label="Environment">{system.data.environment}</KeyValue>
              </Card>
            </div>
          )}
        </TabsContent>
      </Tabs>
      {selected && <RunDialog runId={selected} onClose={() => setSelected(null)} />}
    </PageBody>
  );
}

export default function Page() {
  return (
    <RequirePermission permission="monitoring:read">
      <MonitoringPage />
    </RequirePermission>
  );
}
