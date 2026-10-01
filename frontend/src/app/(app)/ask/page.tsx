"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, MessageSquarePlus, PanelRightClose, PanelRightOpen, SendHorizontal, ThumbsDown, ThumbsUp, Trash2 } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { AgentTimeline, AnswerText, OutcomeIcon, RunDetail, RunSummary } from "@/components/run-view";
import { RequirePermission } from "@/components/shell";
import { Button, Dialog, Spinner, Textarea } from "@/components/ui";
import { api, stream } from "@/lib/api";
import { downloadText, runReportMarkdown } from "@/lib/report";
import type { AgentStep, ConversationDetail, ConversationSummary, RunResult } from "@/lib/types";
import { cn, errorMessage, relativeTime } from "@/lib/utils";

type ThreadItem =
  | { kind: "user"; id: string; text: string }
  | { kind: "answer"; id: string; run: RunResult }
  | { kind: "pending"; id: string; steps: AgentStep[]; running: string[] }
  | { kind: "error"; id: string; message: string };

const EXAMPLES = [
  "How many days per week can employees work remotely, and is MFA required?",
  "What is the nightly hotel limit in high-cost cities?",
  "How many days of annual leave do I get?",
  "What is the proposed purchase price for the acquisition?",
  "What does GDPR say about the right of access by the data subject?",
  "Who must approve purchases above $100,000?",
];

function AskPage() {
  const queryClient = useQueryClient();
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [thread, setThread] = useState<ThreadItem[]>([]);
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [selectedRun, setSelectedRun] = useState<string | null>(null);
  const [selectedClaim, setSelectedClaim] = useState<number | null>(null);
  const [activeEvidence, setActiveEvidence] = useState<string | null>(null);
  const [tab, setTab] = useState("evidence");
  const [panelOpen, setPanelOpen] = useState(true);
  const [feedbackFor, setFeedbackFor] = useState<RunResult | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  const conversations = useQuery({
    queryKey: ["conversations"],
    queryFn: () => api<ConversationSummary[]>("/chat/conversations"),
  });

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [thread]);

  const openConversation = useCallback(async (id: string) => {
    setConversationId(id);
    setSelectedRun(null);
    setSelectedClaim(null);
    setThread([]);
    try {
      const detail = await api<ConversationDetail>(`/chat/conversations/${id}`);
      const runIds = detail.messages.filter((m) => m.role === "assistant" && m.run_id).map((m) => m.run_id!);
      const runs = Object.fromEntries(
        (await Promise.all(runIds.map((runId) => api<RunResult>(`/runs/${runId}`).catch(() => null))))
          .filter((run): run is RunResult => run !== null)
          .map((run) => [run.run_id, run]),
      );
      const items: ThreadItem[] = [];
      for (const message of detail.messages) {
        if (message.role === "user") items.push({ kind: "user", id: message.id, text: message.content });
        else if (message.run_id && runs[message.run_id]) items.push({ kind: "answer", id: message.id, run: runs[message.run_id] });
      }
      setThread(items);
      const last = [...items].reverse().find((item) => item.kind === "answer");
      if (last && last.kind === "answer") setSelectedRun(last.run.run_id);
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }, []);

  function newConversation() {
    setConversationId(null);
    setThread([]);
    setSelectedRun(null);
    setSelectedClaim(null);
  }

  async function ask(text: string) {
    const trimmed = text.trim();
    if (!trimmed || busy) return;
    setBusy(true);
    setQuestion("");
    const pendingId = `pending-${Date.now()}`;
    setThread((items) => [...items, { kind: "user", id: `user-${Date.now()}`, text: trimmed }, { kind: "pending", id: pendingId, steps: [], running: [] }]);
    const update = (change: (item: Extract<ThreadItem, { kind: "pending" }>) => ThreadItem) =>
      setThread((items) => items.map((item) => (item.id === pendingId && item.kind === "pending" ? change(item) : item)));
    try {
      await stream("/chat/ask/stream", { question: trimmed, conversation_id: conversationId }, (event) => {
        switch (event.type) {
          case "run.started":
            if (event.conversation_id) setConversationId(event.conversation_id);
            break;
          case "agent.started":
            update((item) => ({ ...item, running: [...item.running, event.label] }));
            break;
          case "agent.finished":
            update((item) => ({ ...item, steps: [...item.steps, event], running: item.running.filter((label) => label !== event.label) }));
            break;
          case "run.finished":
            setThread((items) => items.map((item) => (item.id === pendingId ? { kind: "answer", id: event.result.run_id, run: event.result } : item)));
            setSelectedRun(event.result.run_id);
            setSelectedClaim(null);
            setTab("evidence");
            break;
          case "run.failed":
          case "error":
            setThread((items) => items.map((item) => (item.id === pendingId ? { kind: "error", id: pendingId, message: event.error } : item)));
            break;
        }
      });
    } catch (error) {
      setThread((items) => items.map((item) => (item.id === pendingId ? { kind: "error", id: pendingId, message: errorMessage(error) } : item)));
    } finally {
      setBusy(false);
      void queryClient.invalidateQueries({ queryKey: ["conversations"] });
    }
  }

  async function sendFeedback(run: RunResult, rating: 1 | -1, comment = "") {
    try {
      await api<void>(`/runs/${run.run_id}/feedback`, { method: "POST", body: { rating, comment } });
      toast.success(rating > 0 ? "Thanks for the feedback" : "Sent to human review");
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  async function removeConversation(id: string) {
    try {
      await api<void>(`/chat/conversations/${id}`, { method: "DELETE" });
      if (id === conversationId) newConversation();
      void queryClient.invalidateQueries({ queryKey: ["conversations"] });
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  const activeRun = thread.find((item): item is Extract<ThreadItem, { kind: "answer" }> => item.kind === "answer" && item.run.run_id === selectedRun)?.run ?? null;

  return (
    <div className="flex h-full min-h-0">
      {/* Conversations */}
      <aside className="hidden w-60 shrink-0 flex-col border-r bg-surface xl:flex">
        <div className="p-3">
          <Button variant="outline" className="w-full" onClick={newConversation}>
            <MessageSquarePlus className="h-4 w-4" /> New conversation
          </Button>
        </div>
        <div className="scroll-thin min-h-0 flex-1 overflow-y-auto px-2 pb-3">
          {conversations.isLoading && <Spinner className="m-3" />}
          {conversations.data?.length === 0 && <p className="px-2 text-xs text-muted">No conversations yet.</p>}
          {conversations.data?.map((conversation) => (
            <div key={conversation.id} className={cn("group flex items-center rounded-md", conversation.id === conversationId && "bg-primary-soft")}>
              <button onClick={() => void openConversation(conversation.id)} className="min-w-0 flex-1 px-2 py-1.5 text-left">
                <span className="block truncate text-sm">{conversation.title}</span>
                <span className="block text-[11px] text-muted">{relativeTime(conversation.updated_at)}</span>
              </button>
              <button
                onClick={() => void removeConversation(conversation.id)}
                className="mr-1 hidden rounded p-1 text-muted hover:bg-surface-muted hover:text-bad group-hover:block"
                aria-label={`Delete conversation ${conversation.title}`}
              >
                <Trash2 className="h-3.5 w-3.5" />
              </button>
            </div>
          ))}
        </div>
      </aside>

      {/* Thread */}
      <section className="flex min-w-0 flex-1 flex-col">
        <div className="scroll-thin min-h-0 flex-1 overflow-y-auto">
          <div className="mx-auto max-w-3xl px-4 py-5">
            {thread.length === 0 && (
              <div className="py-10">
                <h1 className="text-xl font-semibold">Ask the knowledge base</h1>
                <p className="mt-1 text-sm text-muted">
                  Answers come only from documents you are allowed to read. Every sentence is checked against its source before
                  you see it; anything that cannot be verified is removed.
                </p>
                <div className="mt-5 grid gap-2 sm:grid-cols-2">
                  {EXAMPLES.map((example) => (
                    <button key={example} onClick={() => void ask(example)} className="rounded-lg border bg-surface p-3 text-left text-sm hover:bg-surface-muted">
                      {example}
                    </button>
                  ))}
                </div>
              </div>
            )}
            <div className="space-y-4">
              {thread.map((item) => {
                if (item.kind === "user") {
                  return (
                    <div key={item.id} className="flex justify-end">
                      <p className="max-w-[85%] whitespace-pre-wrap rounded-lg bg-primary px-3.5 py-2 text-sm text-primary-foreground">{item.text}</p>
                    </div>
                  );
                }
                if (item.kind === "pending") {
                  return (
                    <div key={item.id} className="rounded-lg border bg-surface p-3">
                      <p className="mb-2 flex items-center gap-2 text-sm font-medium">
                        <Spinner /> Working through the agents
                      </p>
                      <AgentTimeline steps={item.steps} running={item.running} />
                    </div>
                  );
                }
                if (item.kind === "error") {
                  return (
                    <div key={item.id} role="alert" className="rounded-lg border border-bad/30 bg-bad-soft p-3 text-sm text-bad">
                      The request failed: {item.message}
                    </div>
                  );
                }
                const run = item.run;
                const selected = run.run_id === selectedRun;
                return (
                  <article
                    key={item.id}
                    className={cn("rounded-lg border bg-surface p-4", selected && "ring-1 ring-primary")}
                    onClick={() => { if (!selected) { setSelectedRun(run.run_id); setSelectedClaim(null); } }}
                  >
                    <div className="mb-2 flex items-center gap-2 text-xs font-medium text-muted">
                      <OutcomeIcon outcome={run.outcome} />
                      {run.outcome === "answered" ? "Verified answer" : run.outcome === "denied" ? "Access denied" : "No answer given"}
                    </div>
                    <div className="text-[15px]">
                      <AnswerText
                        run={run}
                        selectedClaim={selected ? selectedClaim : null}
                        onSelectClaim={(ordinal) => { setSelectedRun(run.run_id); setSelectedClaim(ordinal); setTab("evidence"); setPanelOpen(true); }}
                        onSelectEvidence={(ref) => { setSelectedRun(run.run_id); setActiveEvidence(ref); setTab("evidence"); setPanelOpen(true); document.getElementById(`evidence-${ref}`)?.scrollIntoView({ behavior: "smooth", block: "center" }); }}
                      />
                    </div>
                    <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
                      <RunSummary run={run} />
                      <div className="flex items-center gap-0.5">
                        <Button variant="ghost" size="icon" aria-label="Helpful" onClick={(e) => { e.stopPropagation(); void sendFeedback(run, 1); }}>
                          <ThumbsUp className="h-4 w-4" />
                        </Button>
                        <Button variant="ghost" size="icon" aria-label="Report a problem with this answer" onClick={(e) => { e.stopPropagation(); setFeedbackFor(run); }}>
                          <ThumbsDown className="h-4 w-4" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon"
                          aria-label="Download report"
                          onClick={(e) => { e.stopPropagation(); downloadText(`pramana-answer-${run.run_id.slice(0, 8)}.md`, runReportMarkdown(run)); }}
                        >
                          <Download className="h-4 w-4" />
                        </Button>
                      </div>
                    </div>
                  </article>
                );
              })}
            </div>
            <div ref={bottomRef} />
          </div>
        </div>
        <form
          onSubmit={(event) => { event.preventDefault(); void ask(question); }}
          className="border-t bg-surface p-3"
        >
          <div className="mx-auto flex max-w-3xl items-end gap-2">
            <Textarea
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  void ask(question);
                }
              }}
              placeholder="Ask a question about your organisation's documents"
              aria-label="Your question"
              rows={1}
              maxLength={2000}
              className="max-h-40 min-h-10 flex-1 resize-none"
            />
            <Button type="submit" size="icon" className="h-10 w-10" disabled={!question.trim()} loading={busy} aria-label="Send">
              {!busy && <SendHorizontal className="h-4 w-4" />}
            </Button>
            <Button type="button" variant="outline" size="icon" className="hidden h-10 w-10 lg:inline-flex" onClick={() => setPanelOpen((v) => !v)} aria-label={panelOpen ? "Hide details" : "Show details"}>
              {panelOpen ? <PanelRightClose className="h-4 w-4" /> : <PanelRightOpen className="h-4 w-4" />}
            </Button>
          </div>
        </form>
      </section>

      {/* Evidence and trust */}
      {panelOpen && (
        <aside className="hidden w-[26rem] shrink-0 border-l bg-background lg:block">
          {activeRun ? (
            <RunDetail
              run={activeRun}
              selectedClaim={selectedClaim}
              onSelectClaim={setSelectedClaim}
              activeEvidence={activeEvidence}
              tab={tab}
              onTabChange={setTab}
            />
          ) : (
            <div className="p-6 text-sm text-muted">
              <p className="font-medium text-foreground">Evidence and verification</p>
              <p className="mt-1">
                Ask a question, then click any sentence of the answer to see the exact passage that supports it, how it was
                checked, and which policy applied.
              </p>
            </div>
          )}
        </aside>
      )}

      <FeedbackDialog run={feedbackFor} onClose={() => setFeedbackFor(null)} onSend={sendFeedback} />
    </div>
  );
}

function FeedbackDialog({
  run,
  onClose,
  onSend,
}: {
  run: RunResult | null;
  onClose: () => void;
  onSend: (run: RunResult, rating: 1 | -1, comment: string) => Promise<void>;
}) {
  const [comment, setComment] = useState("");
  return (
    <Dialog open={run !== null} onOpenChange={(open) => !open && onClose()} title="Report a problem" description="The answer is sent to a reviewer together with your note.">
      <Textarea value={comment} onChange={(event) => setComment(event.target.value)} placeholder="What is wrong or missing?" maxLength={2000} autoFocus />
      <div className="mt-4 flex justify-end gap-2">
        <Button variant="ghost" onClick={onClose}>
          Cancel
        </Button>
        <Button
          onClick={async () => {
            if (run) await onSend(run, -1, comment);
            setComment("");
            onClose();
          }}
        >
          Send to review
        </Button>
      </div>
    </Dialog>
  );
}

export default function Page() {
  return (
    <RequirePermission permission="query:run">
      <AskPage />
    </RequirePermission>
  );
}
