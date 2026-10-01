"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, FileUp, RefreshCw, Search, Trash2, UploadCloud } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { SensitivityBadge, StatusBadge } from "@/components/badges";
import { PageBody, RequirePermission } from "@/components/shell";
import { Badge, Button, Card, CardHeader, Dialog, EmptyState, ErrorNote, Field, Input, KeyValue, Meter, PageHeader, Select, Skeleton, Stat, Table, Td, Th } from "@/components/ui";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { ChunkView, DocumentPage, DocumentView, JobView } from "@/lib/types";
import { DEPARTMENT_LABEL, REGIONS, SENSITIVITY, bytes, dateTime, departmentLabel, errorMessage, relativeTime, titleCase } from "@/lib/utils";

const PAGE_SIZE = 25;
const STAGES = ["parse", "chunk", "privacy_scan", "embed", "index", "graph", "done"];
const STAGE_LABEL: Record<string, string> = {
  queued: "Queued", starting: "Starting", parse: "Reading the file", chunk: "Splitting into passages",
  privacy_scan: "Scanning for personal data", embed: "Computing embeddings", index: "Indexing",
  graph: "Extracting entities", done: "Ready", failed: "Failed",
};

function UploadDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const me = useAuth((s) => s.me);
  const queryClient = useQueryClient();
  const fileInput = useRef<HTMLInputElement>(null);
  const [files, setFiles] = useState<File[]>([]);
  const [department, setDepartment] = useState(me?.department ?? "");
  const [sensitivity, setSensitivity] = useState("INTERNAL");
  const [region, setRegion] = useState("global");
  const [effective, setEffective] = useState("");
  const [title, setTitle] = useState("");
  const formats = useQuery({ queryKey: ["formats"], queryFn: () => api<{ extensions: string[]; max_bytes: number }>("/documents/formats"), enabled: open });
  const departments = me?.permissions.includes("users:manage") ? Object.keys(DEPARTMENT_LABEL) : [me?.department ?? "", ...(me?.extra_departments ?? [])];

  const upload = useMutation({
    mutationFn: async () => {
      const results = { ok: 0, failed: [] as string[] };
      for (const file of files) {
        const form = new FormData();
        form.append("file", file);
        form.append("department", department);
        form.append("sensitivity", sensitivity);
        form.append("region", region);
        if (effective) form.append("effective_date", effective);
        if (title.trim() && files.length === 1) form.append("title", title.trim());
        try {
          await api<DocumentView>("/documents", { method: "POST", form });
          results.ok += 1;
        } catch (error) {
          results.failed.push(`${file.name}: ${errorMessage(error)}`);
        }
      }
      return results;
    },
    onSuccess: (results) => {
      if (results.ok) toast.success(`${results.ok} document${results.ok === 1 ? "" : "s"} queued for ingestion`);
      results.failed.forEach((message) => toast.error(message));
      void queryClient.invalidateQueries({ queryKey: ["documents"] });
      void queryClient.invalidateQueries({ queryKey: ["jobs"] });
      if (!results.failed.length) {
        setFiles([]);
        setTitle("");
        onClose();
      }
    },
  });

  return (
    <Dialog open={open} onOpenChange={(value) => !value && onClose()} title="Add documents" description="Files are parsed, scanned for personal data, indexed and added to the knowledge graph. The labels below decide who can read them.">
      <div
        onDragOver={(event) => event.preventDefault()}
        onDrop={(event) => { event.preventDefault(); setFiles(Array.from(event.dataTransfer.files)); }}
        className="rounded-lg border-2 border-dashed p-6 text-center"
      >
        <UploadCloud className="mx-auto h-7 w-7 text-muted" aria-hidden />
        <p className="mt-2 text-sm">Drop files here, or</p>
        <Button variant="outline" size="sm" className="mt-2" onClick={() => fileInput.current?.click()}>
          <FileUp className="h-3.5 w-3.5" /> Choose files
        </Button>
        <input ref={fileInput} type="file" multiple className="sr-only" accept={formats.data?.extensions.join(",")} onChange={(e) => setFiles(Array.from(e.target.files ?? []))} />
        <p className="mt-2 text-xs text-muted">
          {formats.data ? `${formats.data.extensions.join(", ")} · up to ${bytes(formats.data.max_bytes)} each` : "PDF, DOCX, PPTX, Markdown, HTML, text, email"}
        </p>
      </div>
      {files.length > 0 && (
        <ul className="mt-3 max-h-28 space-y-1 overflow-y-auto text-xs">
          {files.map((file) => (
            <li key={file.name} className="flex justify-between gap-2">
              <span className="truncate">{file.name}</span>
              <span className="shrink-0 text-muted">{bytes(file.size)}</span>
            </li>
          ))}
        </ul>
      )}
      <div className="mt-4 grid gap-3 sm:grid-cols-2">
        {files.length === 1 && (
          <Field label="Title" hint="Leave empty to use the document's own title" className="sm:col-span-2">
            <Input value={title} onChange={(e) => setTitle(e.target.value)} maxLength={512} />
          </Field>
        )}
        <Field label="Owning department">
          <Select value={department} onChange={(e) => setDepartment(e.target.value)}>
            {departments.filter(Boolean).map((key) => (
              <option key={key} value={key}>
                {departmentLabel(key)}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Sensitivity" hint="The system will suggest a label after scanning; you decide.">
          <Select value={sensitivity} onChange={(e) => setSensitivity(e.target.value)}>
            {SENSITIVITY.map((level) => (
              <option key={level}>{level}</option>
            ))}
          </Select>
        </Field>
        <Field label="Region" hint="“global” is visible in every region">
          <Select value={region} onChange={(e) => setRegion(e.target.value)}>
            {REGIONS.map((key) => (
              <option key={key} value={key}>
                {key.toUpperCase()}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Effective date (optional)">
          <Input type="date" value={effective} onChange={(e) => setEffective(e.target.value)} />
        </Field>
      </div>
      <div className="mt-5 flex justify-end gap-2">
        <Button variant="ghost" onClick={onClose}>
          Cancel
        </Button>
        <Button disabled={!files.length || !department} loading={upload.isPending} onClick={() => upload.mutate()}>
          Add {files.length || ""} document{files.length === 1 ? "" : "s"}
        </Button>
      </div>
    </Dialog>
  );
}

function JobsCard() {
  const jobs = useQuery({
    queryKey: ["jobs"],
    queryFn: () => api<JobView[]>("/ingestion/jobs", { query: { limit: 8 } }),
    refetchInterval: (query) => (query.state.data?.some((job) => ["queued", "running"].includes(job.status)) ? 2000 : 15000),
  });
  const queryClient = useQueryClient();
  const active = jobs.data?.filter((job) => ["queued", "running"].includes(job.status)) ?? [];
  const recent = jobs.data?.slice(0, 5) ?? [];
  const activeCount = active.length;
  // When the last running job finishes, the document list is out of date.
  useEffect(() => {
    if (activeCount === 0) void queryClient.invalidateQueries({ queryKey: ["documents"] });
  }, [activeCount, queryClient]);
  if (!recent.length) return null;
  return (
    <Card className="mb-4">
      <CardHeader title="Ingestion" description={active.length ? `${active.length} document(s) being processed` : "Recent jobs"} />
      <ul className="divide-y">
        {recent.map((job) => (
          <li key={job.id} className="px-4 py-2.5">
            <div className="flex items-center justify-between gap-3">
              <span className="truncate text-sm">{job.document_title}</span>
              <span className="flex shrink-0 items-center gap-2 text-xs text-muted">
                {STAGE_LABEL[job.stage] ?? titleCase(job.stage)} <StatusBadge status={job.status} />
              </span>
            </div>
            {["queued", "running"].includes(job.status) && (
              <div className="mt-1.5 flex items-center gap-2">
                <Meter value={job.progress / 100} label="Progress" />
                <span className="w-9 text-right text-xs tabular-nums text-muted">{job.progress}%</span>
              </div>
            )}
            {job.status === "running" && (
              <ol className="mt-1.5 flex flex-wrap gap-1">
                {STAGES.slice(0, -1).map((stage) => (
                  <li key={stage}>
                    <Badge tone={STAGES.indexOf(stage) < STAGES.indexOf(job.stage) ? "ok" : stage === job.stage ? "primary" : "neutral"}>
                      {STAGE_LABEL[stage]}
                    </Badge>
                  </li>
                ))}
              </ol>
            )}
            {job.error && <p className="mt-1 text-xs text-bad">{job.error}</p>}
          </li>
        ))}
      </ul>
    </Card>
  );
}

function DocumentDialog({ document, onClose }: { document: DocumentView | null; onClose: () => void }) {
  const queryClient = useQueryClient();
  const canWrite = useAuth((s) => s.has("documents:write"));
  const [sensitivity, setSensitivity] = useState("");
  const [department, setDepartment] = useState("");
  const [region, setRegion] = useState("");
  const chunks = useQuery({
    queryKey: ["chunks", document?.id],
    queryFn: () => api<ChunkView[]>(`/documents/${document!.id}/chunks`, { query: { limit: 40 } }),
    enabled: Boolean(document),
  });
  const refresh = () => void queryClient.invalidateQueries({ queryKey: ["documents"] });
  const relabel = useMutation({
    mutationFn: () =>
      api<DocumentView>(`/documents/${document!.id}`, {
        method: "PATCH",
        body: { sensitivity: sensitivity || undefined, department: department || undefined, region: region || undefined },
      }),
    onSuccess: () => { toast.success("Labels updated everywhere: database, vector index and graph"); refresh(); onClose(); },
    onError: (error) => toast.error(errorMessage(error)),
  });
  const reindex = useMutation({
    mutationFn: () => api<JobView>(`/documents/${document!.id}/reindex`, { method: "POST" }),
    onSuccess: () => { toast.success("Queued for re-indexing"); refresh(); void queryClient.invalidateQueries({ queryKey: ["jobs"] }); onClose(); },
    onError: (error) => toast.error(errorMessage(error)),
  });
  const remove = useMutation({
    mutationFn: () => api<void>(`/documents/${document!.id}`, { method: "DELETE" }),
    onSuccess: () => { toast.success("Document removed from every store"); refresh(); onClose(); },
    onError: (error) => toast.error(errorMessage(error)),
  });
  if (!document) return null;
  const suggestionHigher = document.suggested_sensitivity && SENSITIVITY.indexOf(document.suggested_sensitivity as "PUBLIC") > document.sensitivity_rank;
  const changed = (sensitivity && sensitivity !== document.sensitivity) || (department && department !== document.department) || (region && region !== document.region);

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()} title={document.title} wide>
      <div className="grid gap-4 md:grid-cols-2">
        <div>
          <div className="mb-2 flex flex-wrap gap-1.5">
            <SensitivityBadge rank={document.sensitivity_rank} />
            <Badge>{departmentLabel(document.department)}</Badge>
            <Badge>{document.region.toUpperCase()}</Badge>
            <StatusBadge status={document.status} />
            {!document.is_current && <Badge tone="warn">Superseded</Badge>}
          </div>
          <KeyValue label="Source">{titleCase(document.source_type)} · {document.collection}</KeyValue>
          <KeyValue label="File">{document.filename ?? "–"} ({bytes(document.size_bytes)})</KeyValue>
          <KeyValue label="Version">{document.version.slice(0, 12)}{document.effective_date ? ` · effective ${document.effective_date}` : ""}</KeyValue>
          <KeyValue label="Passages">{document.chunk_count} ({document.token_count.toLocaleString()} tokens)</KeyValue>
          <KeyValue label="Graph entities">{document.entity_count} ({document.graph_status ?? "–"})</KeyValue>
          <KeyValue label="Added">{dateTime(document.created_at)}</KeyValue>
          {document.error && <p className="mt-2 text-xs text-bad">{document.error}</p>}
          <p className="mt-3 text-xs font-medium">Personal data found</p>
          {Object.keys(document.pii_counts).length ? (
            <div className="mt-1 flex flex-wrap gap-1.5">
              {Object.entries(document.pii_counts).map(([kind, count]) => (
                <Badge key={kind} tone="warn">
                  {titleCase(kind.toLowerCase())}: {count}
                </Badge>
              ))}
            </div>
          ) : (
            <p className="text-xs text-muted">None detected.</p>
          )}
          {document.suggested_sensitivity && (
            <div className={`mt-3 rounded-md p-2.5 text-xs ${suggestionHigher ? "bg-warn-soft text-warn" : "bg-surface-muted text-muted"}`}>
              <p className="flex items-center gap-1 font-medium">
                {suggestionHigher && <AlertTriangle className="h-3.5 w-3.5" />}
                Suggested label: {document.suggested_sensitivity}
                {suggestionHigher && " (higher than the current label)"}
              </p>
              <ul className="mt-1 list-inside list-disc">
                {document.suggestion_reasons.map((reason) => (
                  <li key={reason}>{reason}</li>
                ))}
              </ul>
            </div>
          )}
          {canWrite && (
            <div className="mt-4 space-y-2 border-t pt-3">
              <p className="text-xs font-medium">Change labels</p>
              <div className="grid grid-cols-3 gap-2">
                <Select aria-label="Sensitivity" value={sensitivity || document.sensitivity} onChange={(e) => setSensitivity(e.target.value)}>
                  {SENSITIVITY.map((level) => <option key={level}>{level}</option>)}
                </Select>
                <Select aria-label="Department" value={department || document.department} onChange={(e) => setDepartment(e.target.value)}>
                  {Object.keys(DEPARTMENT_LABEL).map((key) => <option key={key} value={key}>{departmentLabel(key)}</option>)}
                </Select>
                <Select aria-label="Region" value={region || document.region} onChange={(e) => setRegion(e.target.value)}>
                  {REGIONS.map((key) => <option key={key} value={key}>{key.toUpperCase()}</option>)}
                </Select>
              </div>
              <div className="flex flex-wrap gap-2">
                <Button size="sm" disabled={!changed} loading={relabel.isPending} onClick={() => relabel.mutate()}>
                  Save labels
                </Button>
                <Button size="sm" variant="outline" loading={reindex.isPending} onClick={() => reindex.mutate()}>
                  <RefreshCw className="h-3.5 w-3.5" /> Re-index
                </Button>
                <Button size="sm" variant="danger" loading={remove.isPending} onClick={() => { if (window.confirm(`Remove “${document.title}” from the knowledge base? This cannot be undone.`)) remove.mutate(); }}>
                  <Trash2 className="h-3.5 w-3.5" /> Remove
                </Button>
              </div>
            </div>
          )}
        </div>
        <div>
          <p className="mb-2 text-xs font-medium">Passages {chunks.data ? `(showing ${chunks.data.length} of ${document.chunk_count})` : ""}</p>
          <div className="scroll-thin max-h-[55vh] space-y-2 overflow-y-auto pr-1">
            {chunks.isLoading && <Skeleton className="h-40" />}
            {chunks.error && <ErrorNote error={chunks.error} />}
            {chunks.data?.map((chunk) => (
              <div key={chunk.id} className="rounded-md border p-2.5">
                <p className="mb-1 flex flex-wrap items-center gap-1.5 text-[11px] text-muted">
                  <span className="font-semibold">#{chunk.ordinal + 1}</span>
                  {chunk.section_path && <span className="truncate">{chunk.section_path}</span>}
                  {chunk.page && <span>p. {chunk.page}</span>}
                  {chunk.pii_types.map((kind) => <Badge key={kind} tone="warn">{titleCase(kind.toLowerCase())}</Badge>)}
                </p>
                <p className="whitespace-pre-wrap text-xs leading-5">{chunk.text}</p>
              </div>
            ))}
          </div>
        </div>
      </div>
    </Dialog>
  );
}

function SourcesPage() {
  const canWrite = useAuth((s) => s.has("documents:write"));
  const [uploadOpen, setUploadOpen] = useState(false);
  const [selected, setSelected] = useState<DocumentView | null>(null);
  const [search, setSearch] = useState("");
  const [applied, setApplied] = useState("");
  const [department, setDepartment] = useState("");
  const [sensitivity, setSensitivity] = useState("");
  const [collection, setCollection] = useState("");
  const [status, setStatus] = useState("");
  const [offset, setOffset] = useState(0);

  const facets = useQuery({ queryKey: ["documents", "facets"], queryFn: () => api<Record<string, Record<string, number>>>("/documents/facets") });
  const documents = useQuery({
    queryKey: ["documents", applied, department, sensitivity, collection, status, offset],
    queryFn: () => api<DocumentPage>("/documents", { query: { q: applied, department, sensitivity, collection, status, limit: PAGE_SIZE, offset } }),
    placeholderData: (previous) => previous,
  });
  const total = Object.values(facets.data?.status ?? {}).reduce((sum, n) => sum + n, 0);
  const filter = (set: (value: string) => void) => (event: React.ChangeEvent<HTMLSelectElement>) => { set(event.target.value); setOffset(0); };

  return (
    <PageBody>
      <PageHeader
        title="Knowledge Sources"
        description="The documents the assistant can draw on. You see only the documents your role is allowed to read."
        actions={canWrite && (
          <Button onClick={() => setUploadOpen(true)}>
            <UploadCloud className="h-4 w-4" /> Add documents
          </Button>
        )}
      />
      <div className="mb-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Stat label="Documents you can read" value={facets.data ? total.toLocaleString() : "–"} />
        {SENSITIVITY.slice(1).map((level, index) => (
          <Stat key={level} label={level} value={(facets.data?.sensitivity_rank?.[String(index + 1)] ?? 0).toLocaleString()} />
        ))}
      </div>
      <JobsCard />
      <Card>
        <div className="flex flex-wrap items-end gap-2 border-b p-3">
          <form className="flex min-w-56 flex-1 gap-2" onSubmit={(event) => { event.preventDefault(); setApplied(search); setOffset(0); }}>
            <Input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search titles" aria-label="Search titles" />
            <Button type="submit" variant="outline" size="icon" aria-label="Search">
              <Search className="h-4 w-4" />
            </Button>
          </form>
          <Select aria-label="Department" className="w-40" value={department} onChange={filter(setDepartment)}>
            <option value="">All departments</option>
            {Object.keys(facets.data?.department ?? {}).sort().map((key) => <option key={key} value={key}>{departmentLabel(key)} ({facets.data!.department[key]})</option>)}
          </Select>
          <Select aria-label="Sensitivity" className="w-40" value={sensitivity} onChange={filter(setSensitivity)}>
            <option value="">All sensitivities</option>
            {SENSITIVITY.map((level) => <option key={level}>{level}</option>)}
          </Select>
          <Select aria-label="Collection" className="w-40" value={collection} onChange={filter(setCollection)}>
            <option value="">All collections</option>
            {Object.keys(facets.data?.collection ?? {}).sort().map((key) => <option key={key} value={key}>{titleCase(key)} ({facets.data!.collection[key]})</option>)}
          </Select>
          <Select aria-label="Status" className="w-32" value={status} onChange={filter(setStatus)}>
            <option value="">Any status</option>
            {["ready", "pending", "processing", "failed"].map((value) => <option key={value}>{value}</option>)}
          </Select>
        </div>
        {documents.error && <div className="p-3"><ErrorNote error={documents.error} retry={() => void documents.refetch()} /></div>}
        <Table>
          <thead>
            <tr>
              <Th>Title</Th><Th>Sensitivity</Th><Th>Department</Th><Th>Region</Th><Th>Source</Th><Th className="text-right">Passages</Th><Th>Status</Th><Th>Updated</Th>
            </tr>
          </thead>
          <tbody>
            {documents.isLoading && Array.from({ length: 8 }).map((_, i) => (
              <tr key={i}><Td colSpan={8}><Skeleton className="h-5" /></Td></tr>
            ))}
            {documents.data?.items.map((document) => (
              <tr key={document.id} className="cursor-pointer hover:bg-surface-muted" onClick={() => setSelected(document)}>
                <Td className="max-w-md">
                  <button className="block max-w-full truncate text-left font-medium hover:underline">{document.title}</button>
                  {!document.is_current && <Badge tone="warn" className="mt-0.5">Superseded</Badge>}
                </Td>
                <Td><SensitivityBadge rank={document.sensitivity_rank} /></Td>
                <Td>{departmentLabel(document.department)}</Td>
                <Td>{document.region.toUpperCase()}</Td>
                <Td className="text-muted">{titleCase(document.source_type)}</Td>
                <Td className="text-right tabular-nums">{document.chunk_count}</Td>
                <Td><StatusBadge status={document.status} /></Td>
                <Td className="whitespace-nowrap text-muted">{relativeTime(document.updated_at)}</Td>
              </tr>
            ))}
          </tbody>
        </Table>
        {documents.data?.items.length === 0 && (
          <EmptyState title="No documents match" description="Change the filters, or add documents if your role allows it." />
        )}
        {documents.data && documents.data.total > PAGE_SIZE && (
          <div className="flex items-center justify-between p-3 text-xs text-muted">
            <span>
              {offset + 1}–{Math.min(offset + PAGE_SIZE, documents.data.total)} of {documents.data.total.toLocaleString()}
            </span>
            <span className="flex gap-2">
              <Button size="sm" variant="outline" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>Previous</Button>
              <Button size="sm" variant="outline" disabled={offset + PAGE_SIZE >= documents.data.total} onClick={() => setOffset(offset + PAGE_SIZE)}>Next</Button>
            </span>
          </div>
        )}
      </Card>
      <UploadDialog open={uploadOpen} onClose={() => setUploadOpen(false)} />
      <DocumentDialog key={selected?.id ?? "none"} document={selected} onClose={() => setSelected(null)} />
    </PageBody>
  );
}

export default function Page() {
  return (
    <RequirePermission permission="documents:read">
      <SourcesPage />
    </RequirePermission>
  );
}
