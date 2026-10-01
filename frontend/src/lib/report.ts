import type { RunResult } from "./types";
import { SENSITIVITY, VERDICT_META } from "./utils";

/** A self-contained Markdown record of a run: answer, per-sentence verdicts, sources, audit id. */
export function runReportMarkdown(run: RunResult): string {
  const lines: string[] = [];
  lines.push(`# ${run.kind === "verify" ? "Verification report" : "Answer report"}`, "");
  lines.push(`- Date: ${new Date(run.started_at).toLocaleString()}`);
  lines.push(`- Outcome: ${run.outcome ?? "unknown"}`);
  if (run.trust) lines.push(`- Trust score: ${Math.round(run.trust.score)}/100 (${run.trust.badge})`);
  if (run.confidence !== null) lines.push(`- Confidence: ${(run.confidence * 100).toFixed(0)}%`);
  lines.push(`- Policy version: ${run.policy_version ?? "n/a"}`);
  lines.push(`- Audit entry: ${run.audit_id ?? "n/a"}`, `- Run: ${run.run_id}`, "");
  lines.push(run.kind === "verify" ? "## Text checked" : "## Question", "", run.question, "");
  if (run.kind !== "verify") lines.push("## Answer", "", run.answer, "");

  const claims = run.claims.filter((c) => c.kind === "claim");
  if (claims.length) {
    lines.push("## Sentence-by-sentence verification", "");
    for (const claim of claims) {
      const meta = VERDICT_META[claim.verdict];
      lines.push(`- **${meta.label}** (${(claim.confidence * 100).toFixed(0)}%)${claim.in_answer ? "" : " — removed from the answer"}: ${claim.text}`);
      lines.push(`  - Why: ${claim.reason}`);
      for (const citation of claim.citations) {
        lines.push(`  - ${citation.ref}: "${citation.quote}"`);
      }
    }
    lines.push("");
  }
  if (run.evidence.length) {
    lines.push("## Sources", "");
    for (const item of run.evidence) {
      const where = [item.section_path !== item.title ? item.section_path : "", item.page ? `page ${item.page}` : ""].filter(Boolean).join(", ");
      lines.push(`- **${item.ref}** ${item.title}${where ? ` (${where})` : ""} — ${SENSITIVITY[item.sensitivity_rank]}, ${item.department}${item.is_current ? "" : ", superseded version"}`);
    }
    lines.push("");
  }
  if (run.notes.length) lines.push("## Notes", "", ...run.notes.map((note) => `- ${note}`), "");
  return lines.join("\n");
}

export function downloadText(filename: string, text: string, type = "text/markdown") {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}
