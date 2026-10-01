"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { BarList, TimeColumns, TrendArea } from "@/components/charts";
import { PageBody, RequirePermission } from "@/components/shell";
import { Card, CardHeader, EmptyState, ErrorNote, PageHeader, Select, Skeleton, Stat } from "@/components/ui";
import { api } from "@/lib/api";
import { duration, percent, titleCase } from "@/lib/utils";

interface Overview {
  window_days: number; questions: number; active_users: number; latency_p50_ms: number | null; latency_p95_ms: number | null;
  sources_withheld: number; llm_calls: number; avg_trust: number | null; by_kind: Record<string, number>;
  by_outcome: Record<string, number>; by_department: { department: string; questions: number }[];
  by_route: Record<string, number>; trust_buckets: Record<string, number>;
  top_sources: { title: string; answers: number }[];
  daily: { date: string; questions: number; latency_p50_ms: number | null }[];
  feedback: { positive: number; negative: number };
}

const OUTCOME_LABEL: Record<string, string> = { answered: "Answered", abstained: "No answer given", denied: "Denied by policy", error: "Failed" };
const ROUTE_LABEL: Record<string, string> = { external: "Generated (external model)", local: "Generated (local model)", extractive: "Quoted from sources" };
const TRUST_LABEL: Record<string, string> = { high: "High (80 and above)", medium: "Medium (60 to 79)", low: "Low (below 60)" };

function AnalyticsPage() {
  const [days, setDays] = useState(30);
  const overview = useQuery({ queryKey: ["analytics", days], queryFn: () => api<Overview>("/analytics/overview", { query: { days } }) });
  const data = overview.data;
  const answered = data?.by_outcome.answered ?? 0;
  const feedbackTotal = (data?.feedback.positive ?? 0) + (data?.feedback.negative ?? 0);
  return (
    <PageBody>
      <PageHeader
        title="Analytics"
        description="How the assistant is used, how fast it responds and what it draws on."
        actions={
          <Select aria-label="Time window" className="w-36" value={days} onChange={(e) => setDays(Number(e.target.value))}>
            <option value={7}>Last 7 days</option><option value={30}>Last 30 days</option><option value={90}>Last 90 days</option>
          </Select>
        }
      />
      {overview.isLoading && <Skeleton className="h-96" />}
      {overview.error && <ErrorNote error={overview.error} retry={() => void overview.refetch()} />}
      {data && (
        <div className="space-y-4">
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <Stat label="Questions" value={data.questions.toLocaleString()} hint={`${data.active_users} active user${data.active_users === 1 ? "" : "s"}`} />
            <Stat label="Answered" value={data.questions ? percent(answered / data.questions) : "–"} hint={`${answered.toLocaleString()} of ${data.questions.toLocaleString()}`} />
            <Stat label="Median response time" value={duration(data.latency_p50_ms)} hint={`95th percentile ${duration(data.latency_p95_ms)}`} />
            <Stat label="Average trust score" value={data.avg_trust ?? "–"} hint={`${data.llm_calls.toLocaleString()} LLM calls in total`} />
          </div>
          {data.questions === 0 ? (
            <Card><EmptyState title="No questions in this period" description="Ask something on the Ask page and the figures will appear here." /></Card>
          ) : (
            <div className="grid gap-4 lg:grid-cols-2">
              <Card>
                <CardHeader title="Questions per day" />
                <div className="p-4"><TimeColumns data={data.daily} valueKey="questions" label="Questions" /></div>
              </Card>
              <Card>
                <CardHeader title="Median response time per day" description="Seconds, whole pipeline" />
                <div className="p-4">
                  <TrendArea
                    data={data.daily.map((d) => ({ ...d, seconds: d.latency_p50_ms !== null ? d.latency_p50_ms / 1000 : null }))}
                    valueKey="seconds" label="Median response time" format={(v) => `${v.toFixed(1)} s`}
                  />
                </div>
              </Card>
              <Card>
                <CardHeader title="Outcomes" />
                <div className="p-4">
                  <BarList rows={Object.entries(data.by_outcome).map(([key, value]) => ({ label: OUTCOME_LABEL[key] ?? titleCase(key), value }))} />
                </div>
              </Card>
              <Card>
                <CardHeader title="Trust of answers given" />
                <div className="p-4">
                  <BarList rows={["high", "medium", "low"].filter((k) => data.trust_buckets[k]).map((key) => ({ label: TRUST_LABEL[key], value: data.trust_buckets[key] }))} empty="No answers yet." />
                </div>
              </Card>
              <Card>
                <CardHeader title="Questions by department" description="Department of the person asking" />
                <div className="p-4"><BarList rows={data.by_department.map((row) => ({ label: row.department, value: row.questions }))} /></div>
              </Card>
              <Card>
                <CardHeader title="Most cited documents" description="Number of answers that cite each document" />
                <div className="p-4"><BarList rows={data.top_sources.map((row) => ({ label: row.title, value: row.answers, hint: row.title }))} empty="No citations yet." /></div>
              </Card>
              <Card>
                <CardHeader title="How answers were produced" description="Sensitive evidence is quoted rather than sent to an external model" />
                <div className="p-4"><BarList rows={Object.entries(data.by_route).map(([key, value]) => ({ label: ROUTE_LABEL[key] ?? titleCase(key), value }))} empty="No answers yet." /></div>
              </Card>
              <Card>
                <CardHeader title="Privacy and feedback" />
                <div className="grid grid-cols-2 gap-3 p-4">
                  <Stat label="Sources withheld by policy" value={data.sources_withheld.toLocaleString()} hint="Relevant passages users were not allowed to read" />
                  <Stat label="Helpful ratings" value={feedbackTotal ? percent(data.feedback.positive / feedbackTotal) : "–"} hint={`${data.feedback.positive} helpful, ${data.feedback.negative} reported`} />
                </div>
              </Card>
            </div>
          )}
        </div>
      )}
    </PageBody>
  );
}

export default function Page() {
  return (
    <RequirePermission permission="analytics:read">
      <AnalyticsPage />
    </RequirePermission>
  );
}
