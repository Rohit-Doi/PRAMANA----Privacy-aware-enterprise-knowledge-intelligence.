"use client";

import { useInfiniteQuery, useMutation, useQuery } from "@tanstack/react-query";
import { Download, Link2, ShieldCheck, ShieldX } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { StatusBadge } from "@/components/badges";
import { PageBody, RequirePermission } from "@/components/shell";
import { Button, Card, Dialog, EmptyState, ErrorNote, Input, KeyValue, PageHeader, Select, Skeleton, Table, Td, Th } from "@/components/ui";
import { api, downloadFile } from "@/lib/api";
import type { AuditEntry } from "@/lib/types";
import { dateTime, errorMessage, titleCase } from "@/lib/utils";

interface AuditPage { items: AuditEntry[]; next_before: number | null; total: number }
interface ChainStatus { ok: boolean; entries_checked: number; first_invalid_seq: number | null; reason: string; head_hash: string; verified_at: string }

const ACTION_GROUPS = ["", "query", "verify", "auth", "document", "policy", "user", "review", "audit", "run"];

function AuditPageView() {
  const [action, setAction] = useState("");
  const [decision, setDecision] = useState("");
  const [actor, setActor] = useState("");
  const [appliedActor, setAppliedActor] = useState("");
  const [selected, setSelected] = useState<AuditEntry | null>(null);
  const filters = { action, decision, actor: appliedActor };

  const entries = useInfiniteQuery({
    queryKey: ["audit", filters],
    queryFn: ({ pageParam }) => api<AuditPage>("/audit", { query: { ...filters, before: pageParam, limit: 50 } }),
    initialPageParam: undefined as number | undefined,
    getNextPageParam: (last) => last.next_before ?? undefined,
  });
  const decisions = useQuery({ queryKey: ["audit", "actions"], queryFn: () => api<{ action: string; decision: string; count: number }[]>("/audit/actions") });
  const verify = useMutation({
    mutationFn: () => api<ChainStatus>("/audit/verify", { method: "POST" }),
    onSuccess: (status) => (status.ok ? toast.success(`Chain intact: ${status.entries_checked.toLocaleString()} entries verified`) : toast.error("The audit chain is broken")),
    onError: (error) => toast.error(errorMessage(error)),
  });
  const rows = entries.data?.pages.flatMap((page) => page.items) ?? [];
  const total = entries.data?.pages[0]?.total ?? 0;
  const decisionOptions = [...new Set((decisions.data ?? []).map((d) => d.decision).filter(Boolean))].sort();

  return (
    <PageBody>
      <PageHeader
        title="Audit Logs"
        description="Every sign-in, question, policy change and document change. Entries are linked in a hash chain and the database refuses to alter or delete them, so tampering is detectable."
        actions={
          <>
            <Button variant="outline" loading={verify.isPending} onClick={() => verify.mutate()}>
              <Link2 className="h-4 w-4" /> Verify chain
            </Button>
            <Button variant="outline" onClick={() => void downloadFile("/audit/export", filters, "pramana-audit-log.csv").catch((e) => toast.error(errorMessage(e)))}>
              <Download className="h-4 w-4" /> Export CSV
            </Button>
          </>
        }
      />
      {verify.data && (
        <Card className={`mb-4 flex items-start gap-3 p-4 ${verify.data.ok ? "border-ok/40" : "border-bad/40"}`}>
          {verify.data.ok ? <ShieldCheck className="h-5 w-5 shrink-0 text-ok" /> : <ShieldX className="h-5 w-5 shrink-0 text-bad" />}
          <div className="min-w-0 text-sm">
            <p className="font-medium">
              {verify.data.ok
                ? `The chain is intact. All ${verify.data.entries_checked.toLocaleString()} entries were recomputed and match.`
                : `The chain is broken at entry ${verify.data.first_invalid_seq}: ${verify.data.reason}.`}
            </p>
            <p className="mt-0.5 truncate text-xs text-muted">
              Head hash <code>{verify.data.head_hash}</code> · checked {dateTime(verify.data.verified_at)}
            </p>
          </div>
        </Card>
      )}
      <Card>
        <div className="flex flex-wrap items-end gap-2 border-b p-3">
          <Select aria-label="Action" className="w-44" value={action} onChange={(e) => setAction(e.target.value)}>
            {ACTION_GROUPS.map((group) => <option key={group} value={group}>{group ? `${titleCase(group)} events` : "All events"}</option>)}
          </Select>
          <Select aria-label="Decision" className="w-40" value={decision} onChange={(e) => setDecision(e.target.value)}>
            <option value="">Any decision</option>
            {decisionOptions.map((value) => <option key={value} value={value}>{value}</option>)}
          </Select>
          <form className="flex gap-2" onSubmit={(event) => { event.preventDefault(); setAppliedActor(actor); }}>
            <Input value={actor} onChange={(e) => setActor(e.target.value)} placeholder="Actor email contains" aria-label="Actor email contains" className="w-56" />
            <Button type="submit" variant="outline">Filter</Button>
          </form>
          <span className="ml-auto text-xs text-muted">{total.toLocaleString()} entries</span>
        </div>
        {entries.error && <div className="p-3"><ErrorNote error={entries.error} /></div>}
        <Table>
          <thead>
            <tr><Th>#</Th><Th>Time</Th><Th>Actor</Th><Th>Action</Th><Th>Decision</Th><Th>Resource</Th><Th>Entry hash</Th></tr>
          </thead>
          <tbody>
            {entries.isLoading && Array.from({ length: 10 }).map((_, i) => <tr key={i}><Td colSpan={7}><Skeleton className="h-5" /></Td></tr>)}
            {rows.map((entry) => (
              <tr key={entry.seq} className="cursor-pointer hover:bg-surface-muted" onClick={() => setSelected(entry)}>
                <Td className="tabular-nums text-muted">{entry.seq}</Td>
                <Td className="whitespace-nowrap">{dateTime(entry.ts)}</Td>
                <Td>
                  <span className="block max-w-56 truncate">{entry.actor_email || "system"}</span>
                  {entry.actor_role && <span className="text-xs text-muted">{titleCase(entry.actor_role)}</span>}
                </Td>
                <Td><code className="text-xs">{entry.action}</code></Td>
                <Td>{entry.decision && <StatusBadge status={entry.decision} />}</Td>
                <Td className="text-xs text-muted">{entry.resource_type}{entry.resource_id && ` ${entry.resource_id.slice(0, 8)}`}</Td>
                <Td><code className="text-xs text-muted">{entry.entry_hash.slice(0, 12)}</code></Td>
              </tr>
            ))}
          </tbody>
        </Table>
        {!entries.isLoading && rows.length === 0 && <EmptyState title="No entries match these filters" />}
        {entries.hasNextPage && (
          <div className="p-3 text-center">
            <Button variant="outline" loading={entries.isFetchingNextPage} onClick={() => void entries.fetchNextPage()}>Load older entries</Button>
          </div>
        )}
      </Card>
      <Dialog open={selected !== null} onOpenChange={(open) => !open && setSelected(null)} title={selected ? `Audit entry ${selected.seq}` : ""} wide>
        {selected && (
          <div>
            <KeyValue label="Time">{dateTime(selected.ts)}</KeyValue>
            <KeyValue label="Actor">{selected.actor_email || "system"} {selected.actor_role && `(${titleCase(selected.actor_role)})`}</KeyValue>
            <KeyValue label="Action"><code className="text-xs">{selected.action}</code></KeyValue>
            <KeyValue label="Decision">{selected.decision || "–"}</KeyValue>
            <KeyValue label="Resource">{selected.resource_type} {selected.resource_id}</KeyValue>
            <KeyValue label="Previous hash"><code className="break-all text-xs">{selected.prev_hash}</code></KeyValue>
            <KeyValue label="Entry hash"><code className="break-all text-xs">{selected.entry_hash}</code></KeyValue>
            <p className="mt-3 text-xs font-medium">Details</p>
            <pre className="scroll-thin mt-1 max-h-80 overflow-auto rounded-md bg-surface-muted p-3 text-[11px] leading-5">{JSON.stringify(selected.details, null, 2)}</pre>
          </div>
        )}
      </Dialog>
    </PageBody>
  );
}

export default function Page() {
  return (
    <RequirePermission permission="audit:read">
      <AuditPageView />
    </RequirePermission>
  );
}
