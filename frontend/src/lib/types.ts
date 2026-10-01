// Shapes of the PRAMĀṆA API payloads used by the UI.

export type Verdict = "SUPPORTED" | "PARTIAL" | "UNSUPPORTED" | "CONTRADICTED" | "CONFLICTING";

export interface Me {
  user_id: string;
  email: string;
  full_name: string;
  role: string;
  department: string;
  extra_departments: string[];
  region: string;
  max_rank_own_dept: number;
  max_rank_other_dept: number;
  permissions: string[];
  mfa_verified: boolean;
}

export interface TokenResponse {
  access_token: string | null;
  expires_in: number | null;
  mfa_required: boolean;
  mfa_token: string | null;
}

export interface Citation {
  ref: string;
  quote: string;
  quote_start: number | null;
  quote_end: number | null;
  support: number;
  document_id?: string | null;
  document_title?: string;
  section_path?: string;
  page?: number | null;
}

export interface ClaimSignals {
  grounding: number | null;
  entailment: number | null;
  contradiction: number | null;
  span_fraction: number | null;
  numbers_ok: number | null;
  names_ok: number | null;
  overlap: number | null;
  supporting_documents: number | null;
  numbers_missing: string[] | null;
  names_missing: string[] | null;
  evidence_changed: boolean | null;
  judge: { verdict: string; quote_valid: boolean } | null;
  probability: number | null;
}

export interface Claim {
  id: string;
  ordinal: number;
  kind: "claim" | "disclaimer";
  text: string;
  start: number | null;
  end: number | null;
  verdict: Verdict;
  confidence: number;
  reason: string;
  in_answer: boolean;
  citations: Citation[];
  unsupported_spans: { start: number; end: number; confidence: number; text: string }[];
  signals: ClaimSignals;
}

export interface Evidence {
  ref: string;
  chunk_ids: string[];
  document_id: string;
  title: string;
  section_path: string;
  page: number | null;
  text: string;
  department: string;
  sensitivity_rank: number;
  region: string;
  source_type: string;
  collection: string;
  version: string;
  effective_date: string | null;
  is_current: boolean;
  rerank_score: number;
  fused_score: number;
  found_by: string[];
  ranks: Record<string, number>;
}

export interface AgentStep {
  agent: string;
  label: string;
  status: string;
  started_at: string;
  duration_ms: number;
  summary: string;
  details: Record<string, unknown>;
  error?: string | null;
}

export interface TrustScore {
  score: number;
  badge: "high" | "medium" | "low";
  components: Record<string, number>;
  weights: Record<string, number>;
}

export interface RiskAssessment {
  score: number;
  level: "low" | "medium" | "high";
  factors: Record<string, number>;
  notes: string[];
}

export interface GraphEntity {
  key: string;
  name: string;
  type: string;
  matched_by?: string;
}

export interface GraphFact {
  head: string;
  relation: string;
  tail: string;
  document_id: string;
  chunk_id: string;
}

export interface Scope {
  max_rank_own_dept: number;
  max_rank_other_dept: number;
  departments: string[];
  regions: string[];
  all_regions: boolean;
  can_read_pii: boolean;
  policy_version: number;
}

export interface RunResult {
  run_id: string;
  conversation_id: string | null;
  user_id: string;
  kind: "query" | "verify";
  status: string;
  outcome: "answered" | "abstained" | "denied" | "error" | "verified" | "issues_found" | null;
  outcome_reason: string;
  question: string;
  answer: string;
  mode: string;
  route: string;
  confidence: number | null;
  trust: TrustScore | null;
  risk: RiskAssessment | null;
  claims: Claim[];
  evidence: Evidence[];
  graph: { entities: GraphEntity[]; facts: GraphFact[] };
  explanation: {
    evidence_paths?: { ref: string; found_by: string[]; ranks: Record<string, number>; relevance: number }[];
    claim_support?: { claim: number; evidence: string[]; verdict: string }[];
    removed_sentences?: number;
    conflicts?: number[];
    [key: string]: unknown;
  };
  withheld_count: number | null;
  notes: string[];
  steps: AgentStep[];
  policy_version: number | null;
  scope: Partial<Scope>;
  audit_id: string | null;
  latency_ms: number | null;
  llm_calls: number;
  correction_rounds: number;
  needs_review: boolean;
  error: string | null;
  started_at: string;
  finished_at: string | null;
}

export type StreamEvent =
  | { type: "run.started"; run_id: string; conversation_id: string | null }
  | { type: "agent.started"; agent: string; label: string }
  | ({ type: "agent.finished" } & AgentStep)
  | { type: "agent.failed"; agent: string; label: string; duration_ms: number; error: string }
  | { type: "run.finished"; result: RunResult }
  | { type: "run.failed"; run_id: string; error: string }
  | { type: "error"; error: string };

export interface ConversationSummary {
  id: string;
  title: string;
  updated_at: string;
  message_count: number;
}

export interface ConversationDetail {
  id: string;
  title: string;
  messages: { id: string; role: "user" | "assistant"; content: string; run_id: string | null; created_at: string }[];
}

export interface DocumentView {
  id: string;
  title: string;
  source_type: string;
  source_uri: string;
  collection: string;
  department: string;
  sensitivity: string;
  sensitivity_rank: number;
  suggested_sensitivity: string | null;
  suggestion_reasons: string[];
  region: string;
  version: string;
  effective_date: string | null;
  is_current: boolean;
  supersedes_id: string | null;
  status: string;
  error: string | null;
  chunk_count: number;
  token_count: number;
  entity_count: number;
  pii_counts: Record<string, number>;
  graph_status: string | null;
  filename: string | null;
  size_bytes: number | null;
  owned_by_me: boolean;
  created_at: string;
  updated_at: string;
}

export interface DocumentPage {
  items: DocumentView[];
  total: number;
  limit: number;
  offset: number;
}

export interface ChunkView {
  id: string;
  ordinal: number;
  section_path: string;
  page: number | null;
  text: string;
  token_count: number;
  pii_types: string[];
}

export interface JobView {
  id: string;
  document_id: string;
  document_title: string;
  kind: string;
  status: string;
  stage: string;
  progress: number;
  attempts: number;
  error: string | null;
  events: { at: string; stage: string; progress: number; message: string }[];
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface PreviewUser {
  id: string;
  full_name: string;
  role: string;
  role_name: string;
  department: string;
  region: string;
  is_self: boolean;
}

export interface UserView {
  id: string;
  email: string;
  full_name: string;
  role: string;
  role_name: string;
  department: string;
  region: string;
  extra_departments: string[];
  extra_permissions: string[];
  is_active: boolean;
  is_service_account: boolean;
  mfa_enabled: boolean;
  last_login_at: string | null;
  created_at: string;
}

export interface RoleView {
  key: string;
  name: string;
  description: string;
  max_rank_own_dept: number;
  max_rank_other_dept: number;
  permissions: string[];
}

export interface ActivePolicy {
  version: number;
  note: string;
  rego_sha256: string;
  created_at: string;
  rego: string;
  roles: Record<string, { max_rank_own_dept: number; max_rank_other_dept: number; permissions: string[] }>;
  config: { egress_max_rank: number; pseudonymize_names: boolean; show_withheld_count: boolean };
  sensitivity_levels: { rank: number; name: string; description: string }[];
}

export interface AuditEntry {
  seq: number;
  id: string;
  ts: string;
  actor_id: string | null;
  actor_email: string;
  actor_role: string;
  action: string;
  resource_type: string;
  resource_id: string;
  decision: string;
  details: Record<string, unknown>;
  prev_hash: string;
  entry_hash: string;
}

export interface EvalRun {
  id: string;
  suite: string;
  status: string;
  sample_count: number;
  metrics: Record<string, unknown>;
  gates: { criteria?: { name: string; value: number | null; minimum: number | null; maximum: number | null; passed: boolean }[] };
  gate_passed: boolean | null;
  notes: string;
  started_at: string;
  finished_at: string | null;
}
