"use client";

import { useMutation } from "@tanstack/react-query";
import { ClipboardCheck, Download, FileUp, RotateCcw } from "lucide-react";
import { useState } from "react";

import { VerdictBadge } from "@/components/badges";
import { RunDetail } from "@/components/run-view";
import { PageBody, RequirePermission } from "@/components/shell";
import { Badge, Button, Card, ErrorNote, Field, Input, PageHeader, Stat, Textarea } from "@/components/ui";
import { api } from "@/lib/api";
import { downloadText, runReportMarkdown } from "@/lib/report";
import type { RunResult } from "@/lib/types";
import { TONE_UNDERLINE, VERDICT_META, cn, percent } from "@/lib/utils";

const SAMPLE = `Full-time employees may work remotely for up to 5 days per week without approval. Multi-factor authentication is mandatory for every VPN connection. The nightly hotel limit in high-cost cities is $350. Expenses must be submitted within 30 days of the transaction. Employees receive a $50 monthly internet stipend for remote work.`;

function VerifyStudio() {
  const [text, setText] = useState("");
  const [useReference, setUseReference] = useState(false);
  const [reference, setReference] = useState("");
  const [referenceTitle, setReferenceTitle] = useState("");
  const [selectedClaim, setSelectedClaim] = useState<number | null>(null);
  const [tab, setTab] = useState("evidence");

  const verify = useMutation({
    mutationFn: () =>
      api<RunResult>("/verify", {
        method: "POST",
        body: {
          text,
          reference_text: useReference && reference.trim() ? reference : null,
          reference_title: referenceTitle.trim() || "Supplied reference",
        },
      }),
    onSuccess: () => {
      setSelectedClaim(null);
      setTab("evidence");
    },
  });
  const run = verify.data;
  const claims = run?.claims.filter((c) => c.kind === "claim") ?? [];
  const counts = claims.reduce<Record<string, number>>((acc, claim) => ({ ...acc, [claim.verdict]: (acc[claim.verdict] ?? 0) + 1 }), {});

  async function loadFile(file: File, target: "text" | "reference") {
    const content = await file.text();
    if (target === "text") setText(content.slice(0, 12000));
    else {
      setReference(content.slice(0, 60000));
      setReferenceTitle(file.name);
    }
  }

  return (
    <PageBody>
      <PageHeader
        title="Verify Studio"
        description="Paste any text, such as an AI-generated answer, a draft report or an email, and check each sentence against the knowledge base or against a reference you supply. Nothing is rewritten: you get a verdict and the supporting passage for every sentence."
        actions={
          run && (
            <>
              <Button variant="outline" onClick={() => downloadText(`pramana-verification-${run.run_id.slice(0, 8)}.md`, runReportMarkdown(run))}>
                <Download className="h-4 w-4" /> Download report
              </Button>
              <Button variant="outline" onClick={() => verify.reset()}>
                <RotateCcw className="h-4 w-4" /> Check another text
              </Button>
            </>
          )
        }
      />

      {!run && (
        <div className="grid gap-4 lg:grid-cols-2">
          <Card className="p-4">
            <div className="mb-2 flex items-center justify-between">
              <p className="text-sm font-medium">Text to verify</p>
              <div className="flex items-center gap-1">
                <Button variant="ghost" size="sm" onClick={() => setText(SAMPLE)}>
                  Use an example
                </Button>
                <label className="inline-flex h-8 cursor-pointer items-center gap-1.5 rounded-md px-2.5 text-xs font-medium hover:bg-surface-muted">
                  <FileUp className="h-3.5 w-3.5" /> Load .txt or .md
                  <input type="file" accept=".txt,.md,text/plain,text/markdown" className="sr-only" onChange={(e) => e.target.files?.[0] && void loadFile(e.target.files[0], "text")} />
                </label>
              </div>
            </div>
            <Textarea value={text} onChange={(e) => setText(e.target.value)} rows={14} maxLength={12000} placeholder="Paste the text you want checked…" aria-label="Text to verify" />
            <p className="mt-1 text-xs text-muted">{text.length.toLocaleString()} / 12,000 characters. Up to 20 sentences are checked.</p>
          </Card>

          <Card className="p-4">
            <p className="text-sm font-medium">Check against</p>
            <div className="mt-2 grid grid-cols-2 gap-2">
              {[
                { value: false, title: "Knowledge base", text: "Documents you are allowed to read" },
                { value: true, title: "A reference I supply", text: "Paste or load a source text" },
              ].map((option) => (
                <button
                  key={option.title}
                  type="button"
                  onClick={() => setUseReference(option.value)}
                  aria-pressed={useReference === option.value}
                  className={cn("rounded-lg border p-3 text-left", useReference === option.value ? "border-primary bg-primary-soft" : "hover:bg-surface-muted")}
                >
                  <span className="block text-sm font-medium">{option.title}</span>
                  <span className="block text-xs text-muted">{option.text}</span>
                </button>
              ))}
            </div>
            {useReference && (
              <div className="mt-3 space-y-3">
                <Field label="Reference title">
                  <Input value={referenceTitle} onChange={(e) => setReferenceTitle(e.target.value)} placeholder="For example: Q3 board pack" maxLength={200} />
                </Field>
                <Field label="Reference text" hint="The text is used for this check only and is not added to the knowledge base.">
                  <Textarea value={reference} onChange={(e) => setReference(e.target.value)} rows={7} maxLength={60000} placeholder="Paste the source text…" />
                </Field>
                <label className="inline-flex h-8 cursor-pointer items-center gap-1.5 rounded-md border px-2.5 text-xs font-medium hover:bg-surface-muted">
                  <FileUp className="h-3.5 w-3.5" /> Load reference from a .txt or .md file
                  <input type="file" accept=".txt,.md,text/plain,text/markdown" className="sr-only" onChange={(e) => e.target.files?.[0] && void loadFile(e.target.files[0], "reference")} />
                </label>
              </div>
            )}
            <div className="mt-4">
              {verify.error && <ErrorNote error={verify.error} />}
              <Button
                size="lg"
                className="mt-2 w-full"
                loading={verify.isPending}
                disabled={text.trim().length < 10 || (useReference && !reference.trim())}
                onClick={() => verify.mutate()}
              >
                <ClipboardCheck className="h-4 w-4" /> {verify.isPending ? "Checking each sentence…" : "Verify"}
              </Button>
              <p className="mt-2 text-xs text-muted">
                Each sentence is checked by three local models and by literal checks on numbers and names. Sentences the models
                are unsure about get a second opinion from the language model.
              </p>
            </div>
          </Card>
        </div>
      )}

      {run && (
        <>
          <div className="mb-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <Stat label="Sentences checked" value={claims.length} />
            <Stat label="Supported" value={counts.SUPPORTED ?? 0} tone="ok" />
            <Stat
              label="Not supported or contradicted"
              value={(counts.UNSUPPORTED ?? 0) + (counts.CONTRADICTED ?? 0) + (counts.PARTIAL ?? 0)}
              tone={(counts.UNSUPPORTED ?? 0) + (counts.CONTRADICTED ?? 0) > 0 ? "bad" : undefined}
            />
            <Stat label="Average confidence" value={percent(run.confidence)} hint={run.trust ? `Trust score ${Math.round(run.trust.score)}/100` : undefined} />
          </div>
          <div className="grid gap-4 lg:grid-cols-[1fr_26rem]">
            <Card className="p-4">
              <p className="mb-3 text-xs text-muted">Click a sentence to see the passage it was checked against.</p>
              <div className="space-y-2">
                {claims.map((claim) => {
                  const meta = VERDICT_META[claim.verdict];
                  return (
                    <button
                      key={claim.id}
                      onClick={() => { setSelectedClaim(claim.ordinal === selectedClaim ? null : claim.ordinal); setTab("evidence"); }}
                      className={cn("block w-full rounded-md border p-3 text-left hover:bg-surface-muted", claim.ordinal === selectedClaim && "ring-2 ring-primary")}
                    >
                      <div className="flex items-center gap-2">
                        <VerdictBadge verdict={claim.verdict} />
                        <span className="text-xs tabular-nums text-muted">{percent(claim.confidence)}</span>
                        {claim.citations[0] && <Badge tone="primary">{claim.citations[0].ref}</Badge>}
                      </div>
                      <p className={cn("mt-1.5 text-sm leading-6 underline decoration-2 underline-offset-4", TONE_UNDERLINE[meta.tone])}>{claim.text}</p>
                      <p className="mt-1 text-xs text-muted">{claim.reason}</p>
                    </button>
                  );
                })}
              </div>
              {run.notes.map((note) => (
                <p key={note} className="mt-3 text-xs text-muted">
                  {note}
                </p>
              ))}
            </Card>
            <Card className="h-[70vh] overflow-hidden">
              <RunDetail run={run} selectedClaim={selectedClaim} onSelectClaim={setSelectedClaim} activeEvidence={null} tab={tab} onTabChange={setTab} />
            </Card>
          </div>
        </>
      )}
    </PageBody>
  );
}

export default function Page() {
  return (
    <RequirePermission permission="verify:run">
      <VerifyStudio />
    </RequirePermission>
  );
}
