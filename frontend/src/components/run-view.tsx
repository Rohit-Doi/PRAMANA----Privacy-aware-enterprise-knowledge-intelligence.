"use client";

// Everything shown about one run: the answer with per-sentence verdicts, the evidence,
// the claim-by-claim verification, the reasoning path, the agent timeline and the policy view.

import { ChevronDown, ChevronRight, CircleCheck, CircleDashed, CircleX, EyeOff, FileText, Info, Lock, Route } from "lucide-react";
import { useMemo, useState } from "react";

import { SensitivityBadge, TrustBadge, VerdictBadge } from "@/components/badges";
import { Badge, Card, KeyValue, Meter, Spinner, Tabs, TabsContent, TabsList, TabsTrigger, Tooltip } from "@/components/ui";
import type { AgentStep, Claim, Evidence, RunResult } from "@/lib/types";
import { SENSITIVITY, TONE_UNDERLINE, VERDICT_META, cn, departmentLabel, duration, percent, titleCase } from "@/lib/utils";

const CITATION = /\[(E\d+(?:\s*[,;]\s*E\d+)*)\]/g;

// ── Answer with clickable, verdict-coloured sentences ───────────────────────
export function AnswerText({
  run,
  selectedClaim,
  onSelectClaim,
  onSelectEvidence,
}: {
  run: RunResult;
  selectedClaim: number | null;
  onSelectClaim: (ordinal: number | null) => void;
  onSelectEvidence: (ref: string) => void;
}) {
  const claims = run.claims.filter((c) => c.in_answer && c.start !== null && c.end !== null).sort((a, b) => a.start! - b.start!);
  if (!claims.length) return <p className="whitespace-pre-wrap leading-relaxed">{run.answer}</p>;

  const renderWithCitations = (text: string, keyPrefix: string) => {
    const parts: React.ReactNode[] = [];
    let last = 0;
    for (const match of text.matchAll(CITATION)) {
      if (match.index > last) parts.push(text.slice(last, match.index));
      for (const ref of match[1].split(/\s*[,;]\s*/)) {
        parts.push(
          <button
            key={`${keyPrefix}-${match.index}-${ref}`}
            onClick={(event) => {
              event.stopPropagation();
              onSelectEvidence(ref);
            }}
            className="mx-0.5 inline-flex h-4 items-center rounded bg-primary-soft px-1 align-text-top text-[10px] font-semibold text-primary hover:opacity-80"
            aria-label={`Show source ${ref}`}
          >
            {ref}
          </button>,
        );
      }
      last = match.index + match[0].length;
    }
    if (last < text.length) parts.push(text.slice(last));
    return parts;
  };

  const nodes: React.ReactNode[] = [];
  let cursor = 0;
  for (const claim of claims) {
    if (claim.start! > cursor) nodes.push(run.answer.slice(cursor, claim.start!));
    const meta = VERDICT_META[claim.verdict];
    const sentence = run.answer.slice(claim.start!, claim.end!);
    nodes.push(
      claim.kind === "disclaimer" ? (
        <span key={claim.id} className="italic text-muted">
          {sentence}
        </span>
      ) : (
        <span
          key={claim.id}
          role="button"
          tabIndex={0}
          onClick={() => onSelectClaim(selectedClaim === claim.ordinal ? null : claim.ordinal)}
          onKeyDown={(event) => {
            if (event.key === "Enter" || event.key === " ") onSelectClaim(selectedClaim === claim.ordinal ? null : claim.ordinal);
          }}
          title={`${meta.label} · ${percent(claim.confidence)} · ${claim.reason}`}
          className={cn(
            "cursor-pointer rounded-sm underline decoration-2 underline-offset-4",
            TONE_UNDERLINE[meta.tone],
            selectedClaim === claim.ordinal && "bg-primary-soft",
          )}
        >
          {renderWithCitations(sentence, claim.id)}
        </span>
      ),
    );
    cursor = claim.end!;
  }
  if (cursor < run.answer.length) nodes.push(run.answer.slice(cursor));
  return <p className="whitespace-pre-wrap leading-7">{nodes}</p>;
}

// ── Summary strip under an answer ───────────────────────────────────────────
export function RunSummary({ run }: { run: RunResult }) {
  const checked = run.claims.filter((c) => c.kind === "claim");
  const supported = checked.filter((c) => c.in_answer && c.verdict === "SUPPORTED").length;
  const removed = checked.filter((c) => !c.in_answer).length;
  return (
    <div className="flex flex-wrap items-center gap-1.5 text-xs text-muted">
      {run.trust && <TrustBadge trust={run.trust} />}
      {run.confidence !== null && <Badge>Confidence {percent(run.confidence)}</Badge>}
      {checked.length > 0 && (
        <Badge tone={removed ? "warn" : "ok"}>
          {supported}/{checked.filter((c) => c.in_answer).length} sentences verified
          {removed > 0 && `, ${removed} removed`}
        </Badge>
      )}
      {run.route === "extractive" && (
        <Tooltip content="Quoted directly from the sources. Nothing was generated, because this content may not be sent to an external model.">
          <span>
            <Badge tone="info">
              <Lock className="h-3 w-3" /> Quoted, not generated
            </Badge>
          </span>
        </Tooltip>
      )}
      {(run.withheld_count ?? 0) > 0 && (
        <Tooltip content="Relevant passages exist that your access policy does not let you read. They were not used.">
          <span>
            <Badge tone="warn">
              <EyeOff className="h-3 w-3" /> {run.withheld_count} withheld by policy
            </Badge>
          </span>
        </Tooltip>
      )}
      {run.correction_rounds > 0 && <Badge tone="info">Self-corrected once</Badge>}
      {run.latency_ms !== null && <span>{duration(run.latency_ms)}</span>}
    </div>
  );
}

// ── Evidence ────────────────────────────────────────────────────────────────
function highlight(text: string, quotes: string[]) {
  const ranges: [number, number][] = [];
  for (const quote of quotes) {
    if (!quote) continue;
    const index = text.indexOf(quote);
    if (index >= 0) ranges.push([index, index + quote.length]);
  }
  if (!ranges.length) return text;
  ranges.sort((a, b) => a[0] - b[0]);
  const nodes: React.ReactNode[] = [];
  let cursor = 0;
  ranges.forEach(([start, end], i) => {
    if (start < cursor) return;
    nodes.push(text.slice(cursor, start));
    nodes.push(
      <mark key={i} className="rounded bg-ok-soft px-0.5 text-foreground">
        {text.slice(start, end)}
      </mark>,
    );
    cursor = end;
  });
  nodes.push(text.slice(cursor));
  return nodes;
}

const FOUND_BY_LABEL: Record<string, string> = { semantic: "Meaning", keyword: "Keywords", graph: "Graph", reference: "Reference" };

export function EvidenceCard({ evidence, quotes, active }: { evidence: Evidence; quotes: string[]; active: boolean }) {
  const [open, setOpen] = useState(false);
  const long = evidence.text.length > 520;
  const body = open || !long || quotes.length ? evidence.text : `${evidence.text.slice(0, 520)}…`;
  return (
    <Card id={`evidence-${evidence.ref}`} className={cn("p-3 transition-shadow", active && "ring-2 ring-primary")}>
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="rounded bg-primary-soft px-1.5 py-0.5 text-[11px] font-semibold text-primary">{evidence.ref}</span>
        <SensitivityBadge rank={evidence.sensitivity_rank} />
        {evidence.department && <Badge>{departmentLabel(evidence.department)}</Badge>}
        {evidence.region && evidence.region !== "global" && <Badge>{evidence.region.toUpperCase()}</Badge>}
        {!evidence.is_current && <Badge tone="warn">Superseded version</Badge>}
        {evidence.found_by.map((source) => (
          <Tooltip key={source} content={`Found by ${FOUND_BY_LABEL[source]?.toLowerCase() ?? source} search (rank ${evidence.ranks[source] ?? "–"})`}>
            <span>
              <Badge tone="primary">{FOUND_BY_LABEL[source] ?? source}</Badge>
            </span>
          </Tooltip>
        ))}
      </div>
      <p className="mt-2 text-sm font-medium">{evidence.title}</p>
      <p className="text-xs text-muted">
        {[evidence.section_path !== evidence.title ? evidence.section_path : "", evidence.page ? `page ${evidence.page}` : "", evidence.effective_date ? `effective ${evidence.effective_date}` : ""]
          .filter(Boolean)
          .join(" · ")}
      </p>
      <p className="mt-2 whitespace-pre-wrap text-[13px] leading-6 text-foreground/90">{highlight(body, quotes)}</p>
      {long && !quotes.length && (
        <button onClick={() => setOpen((v) => !v)} className="mt-1 text-xs font-medium text-primary hover:underline">
          {open ? "Show less" : "Show the whole passage"}
        </button>
      )}
    </Card>
  );
}

// ── Claims ──────────────────────────────────────────────────────────────────
const SIGNAL_LABEL: [keyof Claim["signals"], string, string][] = [
  ["grounding", "Grounding", "Probability, from the grounding model, that the passage supports the sentence"],
  ["entailment", "Entailment", "Probability that the passage logically implies the sentence"],
  ["contradiction", "Contradiction", "Probability that the passage contradicts the sentence"],
  ["overlap", "Word overlap", "Share of the sentence's content words found in the passage"],
  ["span_fraction", "Unsupported span", "Share of the sentence flagged as unsupported by the span detector"],
];

export function ClaimCard({ claim, selected, onSelect }: { claim: Claim; selected: boolean; onSelect: () => void }) {
  const [open, setOpen] = useState(false);
  const tone = VERDICT_META[claim.verdict].tone;
  return (
    <Card className={cn("p-3", selected && "ring-2 ring-primary", !claim.in_answer && "opacity-80")}>
      <button onClick={onSelect} className="block w-full text-left">
        <div className="flex flex-wrap items-center gap-1.5">
          <VerdictBadge verdict={claim.verdict} />
          {!claim.in_answer && <Badge tone="bad">Removed from the answer</Badge>}
          {claim.signals.judge && <Badge tone="info">Checked by LLM judge</Badge>}
          {claim.signals.evidence_changed && <Badge tone="warn">Cited the wrong passage</Badge>}
          <span className="ml-auto text-xs tabular-nums text-muted">{percent(claim.confidence)}</span>
        </div>
        <p className={cn("mt-2 text-sm leading-6", !claim.in_answer && "line-through decoration-bad/50")}>{claim.text}</p>
        <p className="mt-1 text-xs text-muted">{claim.reason}</p>
      </button>
      <Meter value={claim.confidence} tone={tone === "info" ? "primary" : tone} label="Confidence" />
      {claim.citations.map((citation) => (
        <blockquote key={citation.ref} className="mt-2 border-l-2 border-ok pl-2 text-xs leading-5 text-muted">
          <span className="font-semibold text-primary">{citation.ref}</span> “{citation.quote}”
        </blockquote>
      ))}
      <button onClick={() => setOpen((v) => !v)} className="mt-2 flex items-center gap-1 text-xs font-medium text-primary">
        {open ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />} How this was checked
      </button>
      {open && (
        <div className="mt-2 space-y-1.5 rounded-md bg-surface-muted p-2.5">
          {SIGNAL_LABEL.map(([key, label, help]) => {
            const value = claim.signals[key];
            if (typeof value !== "number") return null;
            return (
              <Tooltip key={key} content={help}>
                <div className="flex items-center gap-2 text-xs">
                  <span className="w-28 shrink-0 text-muted">{label}</span>
                  <Meter value={value} tone={key === "contradiction" || key === "span_fraction" ? "bad" : "primary"} label={label} />
                  <span className="w-10 shrink-0 text-right tabular-nums">{percent(value)}</span>
                </div>
              </Tooltip>
            );
          })}
          <KeyValue label="Numbers and identifiers">
            {claim.signals.numbers_ok ? "all found in the source" : `not in the source: ${(claim.signals.numbers_missing ?? []).join(", ")}`}
          </KeyValue>
          <KeyValue label="Names">
            {claim.signals.names_ok ? "all found in the source" : `not in the source: ${(claim.signals.names_missing ?? []).join(", ")}`}
          </KeyValue>
          <KeyValue label="Independent supporting documents">{claim.signals.supporting_documents ?? 0}</KeyValue>
          {claim.signals.judge && (
            <KeyValue label="LLM judge">
              {claim.signals.judge.verdict.toLowerCase()}
              {claim.signals.judge.quote_valid ? ", quote verified" : ", no valid quote"}
            </KeyValue>
          )}
        </div>
      )}
    </Card>
  );
}

// ── Trust and risk ──────────────────────────────────────────────────────────
const COMPONENT_LABEL: Record<string, string> = {
  verification: "Verification",
  evidence_quality: "Evidence quality",
  citation_integrity: "Citation integrity",
  policy_compliance: "Policy compliance",
};

export function TrustPanel({ run }: { run: RunResult }) {
  return (
    <div className="space-y-3">
      {run.trust ? (
        <Card className="p-4">
          <div className="flex items-end justify-between">
            <div>
              <p className="text-xs text-muted">Trust score</p>
              <p className={cn("text-3xl font-semibold tabular-nums", run.trust.badge === "high" ? "text-ok" : run.trust.badge === "medium" ? "text-warn" : "text-bad")}>
                {Math.round(run.trust.score)}
                <span className="text-base text-muted">/100</span>
              </p>
            </div>
            <TrustBadge trust={run.trust} />
          </div>
          <div className="mt-3 space-y-2">
            {Object.entries(run.trust.components).map(([key, value]) => (
              <div key={key}>
                <div className="mb-1 flex justify-between text-xs">
                  <span>
                    {COMPONENT_LABEL[key] ?? titleCase(key)}{" "}
                    <span className="text-muted">({Math.round((run.trust!.weights[key] ?? 0) * 100)}% of the score)</span>
                  </span>
                  <span className="tabular-nums">{percent(value)}</span>
                </div>
                <Meter value={value} tone={value >= 0.8 ? "ok" : value >= 0.6 ? "warn" : "bad"} label={COMPONENT_LABEL[key]} />
              </div>
            ))}
          </div>
        </Card>
      ) : (
        <Card className="p-4 text-sm text-muted">No trust score: no answer was given.</Card>
      )}
      {run.risk && (
        <Card className="p-4">
          <div className="flex items-center justify-between">
            <p className="text-sm font-medium">Privacy risk of this request</p>
            <Badge tone={run.risk.level === "high" ? "bad" : run.risk.level === "medium" ? "warn" : "ok"}>
              {run.risk.level} · {run.risk.score.toFixed(2)}
            </Badge>
          </div>
          <div className="mt-2">
            {Object.entries(run.risk.factors)
              .filter(([, value]) => value > 0)
              .map(([key, value]) => (
                <KeyValue key={key} label={titleCase(key)}>
                  +{value.toFixed(2)}
                </KeyValue>
              ))}
            {run.risk.notes.map((note) => (
              <p key={note} className="mt-1 text-xs text-muted">
                {note}
              </p>
            ))}
            {!Object.values(run.risk.factors).some((v) => v > 0) && <p className="text-xs text-muted">No risk factors.</p>}
          </div>
        </Card>
      )}
    </div>
  );
}

// ── Agent timeline ──────────────────────────────────────────────────────────
export function AgentTimeline({ steps, running }: { steps: AgentStep[]; running?: string[] }) {
  const [open, setOpen] = useState<number | null>(null);
  const longest = Math.max(1, ...steps.map((s) => s.duration_ms));
  return (
    <ol className="space-y-1.5">
      {steps.map((step, index) => (
        <li key={`${step.agent}-${index}`}>
          <button
            onClick={() => setOpen(open === index ? null : index)}
            className="w-full rounded-md border bg-surface px-3 py-2 text-left hover:bg-surface-muted"
            aria-expanded={open === index}
          >
            <div className="flex items-center gap-2">
              {step.status === "completed" ? <CircleCheck className="h-4 w-4 shrink-0 text-ok" /> : <CircleX className="h-4 w-4 shrink-0 text-bad" />}
              <span className="text-sm font-medium">{step.label}</span>
              <span className="ml-auto text-xs tabular-nums text-muted">{duration(step.duration_ms)}</span>
            </div>
            <div className="mt-1.5 h-1 overflow-hidden rounded-full bg-surface-muted">
              <div className="h-full rounded-full bg-primary/60" style={{ width: `${Math.max(2, (step.duration_ms / longest) * 100)}%` }} />
            </div>
            {step.summary && <p className="mt-1.5 text-xs leading-5 text-muted">{step.summary}</p>}
          </button>
          {open === index && (
            <pre className="scroll-thin mt-1 max-h-72 overflow-auto rounded-md bg-surface-muted p-3 text-[11px] leading-5">
              {JSON.stringify(step.details, null, 2)}
            </pre>
          )}
        </li>
      ))}
      {(running ?? []).map((label) => (
        <li key={label} className="flex items-center gap-2 rounded-md border border-dashed px-3 py-2 text-sm">
          <Spinner /> {label}
        </li>
      ))}
    </ol>
  );
}

// ── Reasoning path ──────────────────────────────────────────────────────────
export function ReasoningPath({ run }: { run: RunResult }) {
  const claims = run.claims.filter((c) => c.in_answer && c.kind === "claim");
  const evidence = run.evidence;
  const rowHeight = 34;
  const height = Math.max(evidence.length, claims.length, 1) * rowHeight + 20;
  const y = (index: number, count: number) => 10 + (height - 20) * ((index + 0.5) / Math.max(count, 1));
  const evidenceIndex = Object.fromEntries(evidence.map((e, i) => [e.ref, i]));
  return (
    <div className="space-y-3">
      <Card className="p-3">
        <p className="mb-2 text-xs text-muted">Which passage supports which sentence. Line thickness shows the strength of support.</p>
        {claims.length && evidence.length ? (
          <svg viewBox={`0 0 520 ${height}`} className="w-full" role="img" aria-label="Links between evidence passages and answer sentences">
            {claims.flatMap((claim, ci) =>
              claim.citations
                .filter((citation) => citation.ref in evidenceIndex)
                .map((citation) => (
                  <path
                    key={`${claim.id}-${citation.ref}`}
                    d={`M 150 ${y(evidenceIndex[citation.ref], evidence.length)} C 250 ${y(evidenceIndex[citation.ref], evidence.length)}, 270 ${y(ci, claims.length)}, 370 ${y(ci, claims.length)}`}
                    fill="none"
                    stroke="var(--primary)"
                    strokeOpacity={0.55}
                    strokeWidth={1 + citation.support * 3}
                  />
                )),
            )}
            {evidence.map((item, index) => (
              <g key={item.ref}>
                <rect x={10} y={y(index, evidence.length) - 12} width={140} height={24} rx={4} fill="var(--primary-soft)" />
                <text x={18} y={y(index, evidence.length) + 4} fontSize={11} fill="var(--primary)" fontWeight={600}>
                  {item.ref}
                </text>
                <text x={42} y={y(index, evidence.length) + 4} fontSize={10} fill="var(--foreground)">
                  {item.title.length > 20 ? `${item.title.slice(0, 19)}…` : item.title}
                </text>
              </g>
            ))}
            {claims.map((claim, index) => (
              <g key={claim.id}>
                <rect x={370} y={y(index, claims.length) - 12} width={140} height={24} rx={4} fill="var(--surface-muted)" />
                <text x={378} y={y(index, claims.length) + 4} fontSize={10} fill="var(--foreground)">
                  {claim.text.length > 26 ? `${claim.text.slice(0, 25)}…` : claim.text}
                </text>
              </g>
            ))}
          </svg>
        ) : (
          <p className="text-sm text-muted">No verified sentences to trace.</p>
        )}
      </Card>
      <Card className="p-3">
        <p className="text-sm font-medium">Entities recognised in the question</p>
        {run.graph.entities.length ? (
          <div className="mt-2 flex flex-wrap gap-1.5">
            {run.graph.entities.map((entity) => (
              <Tooltip key={entity.key} content={`${titleCase(entity.type)} · matched by ${entity.matched_by ?? "name"}`}>
                <span>
                  <Badge tone="primary">{entity.name}</Badge>
                </span>
              </Tooltip>
            ))}
          </div>
        ) : (
          <p className="mt-1 text-xs text-muted">None of the question&apos;s terms matched a graph entity you can see.</p>
        )}
        {run.graph.facts.length > 0 && (
          <>
            <p className="mt-3 text-sm font-medium">Graph facts considered</p>
            <ul className="mt-1 space-y-1 text-xs">
              {run.graph.facts.map((fact, index) => (
                <li key={index} className="flex flex-wrap items-center gap-1">
                  <Badge>{fact.head}</Badge>
                  <span className="text-muted">{fact.relation.replace(/_/g, " ")}</span>
                  <Badge>{fact.tail}</Badge>
                </li>
              ))}
            </ul>
          </>
        )}
      </Card>
    </div>
  );
}

// ── Policy view ─────────────────────────────────────────────────────────────
const ROUTE_TEXT: Record<string, string> = {
  external: "Generated by the external LLM from evidence within the egress ceiling; personal names replaced by placeholders first.",
  local: "Generated by the local model only: the evidence is above the egress ceiling.",
  extractive: "Quoted directly from the sources. Nothing was sent to a language model.",
  verify: "Checked by the local detectors against the sources.",
  none: "No sources were consulted.",
};

export function PolicyPanel({ run }: { run: RunResult }) {
  const scope = run.scope;
  return (
    <div className="space-y-3">
      <Card className="p-4">
        <p className="mb-1 flex items-center gap-1.5 text-sm font-medium">
          <Lock className="h-4 w-4" /> Access scope applied to this request
        </p>
        <KeyValue label="Own department(s)">{(scope.departments ?? []).map(departmentLabel).join(", ") || "–"}</KeyValue>
        <KeyValue label="Highest sensitivity, own department">{SENSITIVITY[scope.max_rank_own_dept ?? 0] ?? "none"}</KeyValue>
        <KeyValue label="Highest sensitivity, other departments">{SENSITIVITY[scope.max_rank_other_dept ?? 0] ?? "none"}</KeyValue>
        <KeyValue label="Regions">{scope.all_regions ? "all" : (scope.regions ?? []).join(", ").toUpperCase() || "–"}</KeyValue>
        <KeyValue label="May see personal data">{scope.can_read_pii ? "yes" : "no, masked"}</KeyValue>
        <KeyValue label="Policy version">{run.policy_version ?? "–"}</KeyValue>
      </Card>
      <Card className="p-4">
        <p className="mb-1 flex items-center gap-1.5 text-sm font-medium">
          <Route className="h-4 w-4" /> How the answer was produced
        </p>
        <p className="text-sm text-muted">{ROUTE_TEXT[run.route] ?? run.route}</p>
        {run.notes.map((note) => (
          <p key={note} className="mt-2 flex gap-1.5 text-xs text-muted">
            <Info className="mt-0.5 h-3 w-3 shrink-0" /> {note}
          </p>
        ))}
        <KeyValue label="LLM calls">{run.llm_calls}</KeyValue>
        {run.withheld_count !== null && <KeyValue label="Relevant sources withheld by policy">{run.withheld_count}</KeyValue>}
      </Card>
      <Card className="p-4">
        <p className="mb-1 flex items-center gap-1.5 text-sm font-medium">
          <FileText className="h-4 w-4" /> Audit record
        </p>
        <KeyValue label="Run">
          <code className="text-xs">{run.run_id}</code>
        </KeyValue>
        <KeyValue label="Audit entry">
          <code className="text-xs">{run.audit_id ?? "–"}</code>
        </KeyValue>
        <p className="mt-1 text-xs text-muted">The entry is part of a hash chain: any later change to it is detectable.</p>
      </Card>
    </div>
  );
}

// ── The tabbed detail panel ─────────────────────────────────────────────────
export function RunDetail({
  run,
  selectedClaim,
  onSelectClaim,
  activeEvidence,
  tab,
  onTabChange,
}: {
  run: RunResult;
  selectedClaim: number | null;
  onSelectClaim: (ordinal: number | null) => void;
  activeEvidence: string | null;
  tab: string;
  onTabChange: (tab: string) => void;
}) {
  const selected = run.claims.find((c) => c.ordinal === selectedClaim && c.in_answer) ?? null;
  const quotesByRef = useMemo(() => {
    const map: Record<string, string[]> = {};
    for (const claim of selected ? [selected] : []) {
      for (const citation of claim.citations) (map[citation.ref] ??= []).push(citation.quote);
    }
    return map;
  }, [selected]);
  const citedRefs = new Set(selected?.citations.map((c) => c.ref) ?? []);
  const evidence = selected ? [...run.evidence].sort((a, b) => Number(citedRefs.has(b.ref)) - Number(citedRefs.has(a.ref))) : run.evidence;
  const kept = run.claims.filter((c) => c.in_answer && c.kind === "claim");
  const removed = run.claims.filter((c) => !c.in_answer);

  return (
    <Tabs value={tab} onValueChange={onTabChange} className="flex h-full min-h-0 flex-col">
      <TabsList>
        <TabsTrigger value="evidence">Evidence ({run.evidence.length})</TabsTrigger>
        <TabsTrigger value="claims">Claims ({kept.length + removed.length})</TabsTrigger>
        <TabsTrigger value="trust">Trust</TabsTrigger>
        <TabsTrigger value="reasoning">Reasoning</TabsTrigger>
        <TabsTrigger value="agents">Agents</TabsTrigger>
        <TabsTrigger value="policy">Policy</TabsTrigger>
      </TabsList>
      <div className="scroll-thin min-h-0 flex-1 overflow-y-auto p-3">
        <TabsContent value="evidence" className="space-y-2">
          {selected && (
            <p className="rounded-md bg-primary-soft px-2.5 py-1.5 text-xs text-primary">
              Showing where the selected sentence is supported. The quoted text is highlighted.
            </p>
          )}
          {evidence.length ? (
            evidence.map((item) => (
              <EvidenceCard key={item.ref} evidence={item} quotes={quotesByRef[item.ref] ?? []} active={activeEvidence === item.ref || citedRefs.has(item.ref)} />
            ))
          ) : (
            <p className="p-4 text-sm text-muted">No passages were used.</p>
          )}
        </TabsContent>
        <TabsContent value="claims" className="space-y-2">
          {kept.map((claim) => (
            <ClaimCard key={claim.id} claim={claim} selected={claim.ordinal === selectedClaim} onSelect={() => onSelectClaim(claim.ordinal === selectedClaim ? null : claim.ordinal)} />
          ))}
          {removed.length > 0 && (
            <>
              <p className="pt-2 text-xs font-medium text-muted">
                Drafted, failed verification, and removed before you saw the answer
              </p>
              {removed.map((claim) => (
                <ClaimCard key={claim.id} claim={claim} selected={false} onSelect={() => undefined} />
              ))}
            </>
          )}
          {!kept.length && !removed.length && <p className="p-4 text-sm text-muted">Nothing was checked: no answer was drafted.</p>}
        </TabsContent>
        <TabsContent value="trust">
          <TrustPanel run={run} />
        </TabsContent>
        <TabsContent value="reasoning">
          <ReasoningPath run={run} />
        </TabsContent>
        <TabsContent value="agents">
          <AgentTimeline steps={run.steps} />
        </TabsContent>
        <TabsContent value="policy">
          <PolicyPanel run={run} />
        </TabsContent>
      </div>
    </Tabs>
  );
}

export function OutcomeIcon({ outcome }: { outcome: RunResult["outcome"] }) {
  if (outcome === "answered" || outcome === "verified") return <CircleCheck className="h-4 w-4 text-ok" aria-label="Answered" />;
  if (outcome === "denied" || outcome === "error") return <CircleX className="h-4 w-4 text-bad" aria-label={outcome} />;
  return <CircleDashed className="h-4 w-4 text-warn" aria-label="No answer" />;
}
