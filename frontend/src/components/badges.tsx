"use client";

import { AlertTriangle, CheckCircle2, CircleSlash, GitCompareArrows, ShieldCheck, ShieldAlert, XCircle } from "lucide-react";

import { Badge, Tooltip } from "@/components/ui";
import type { TrustScore, Verdict } from "@/lib/types";
import { SENSITIVITY, SENSITIVITY_STYLE, VERDICT_META, cn } from "@/lib/utils";

export function SensitivityBadge({ rank, className }: { rank: number; className?: string }) {
  return (
    <span className={cn("inline-flex rounded px-1.5 py-0.5 text-[11px] font-medium leading-4", SENSITIVITY_STYLE[rank], className)}>
      {SENSITIVITY[rank] ?? "UNKNOWN"}
    </span>
  );
}

const VERDICT_ICON: Record<Verdict, React.ComponentType<{ className?: string }>> = {
  SUPPORTED: CheckCircle2,
  PARTIAL: AlertTriangle,
  UNSUPPORTED: CircleSlash,
  CONTRADICTED: XCircle,
  CONFLICTING: GitCompareArrows,
};

export function VerdictBadge({ verdict }: { verdict: Verdict }) {
  const meta = VERDICT_META[verdict];
  const Icon = VERDICT_ICON[verdict];
  return (
    <Tooltip content={meta.description}>
      <span>
        <Badge tone={meta.tone}>
          <Icon className="h-3 w-3" aria-hidden />
          {meta.label}
        </Badge>
      </span>
    </Tooltip>
  );
}

export function TrustBadge({ trust }: { trust: TrustScore }) {
  const tone = trust.badge === "high" ? "ok" : trust.badge === "medium" ? "warn" : "bad";
  const Icon = trust.badge === "high" ? ShieldCheck : ShieldAlert;
  return (
    <Badge tone={tone}>
      <Icon className="h-3 w-3" aria-hidden />
      Trust {Math.round(trust.score)}
    </Badge>
  );
}

export function StatusBadge({ status }: { status: string }) {
  const tone =
    {
      ready: "ok", succeeded: "ok", completed: "ok", answered: "ok", verified: "ok", GRANTED: "ok", ANSWERED: "ok", VALID: "ok",
      pending: "neutral", queued: "neutral", running: "primary", processing: "primary",
      abstained: "warn", ABSTAINED: "warn", issues_found: "warn", open: "warn",
      failed: "bad", denied: "bad", DENIED: "bad", error: "bad", ERROR: "bad", TAMPERED: "bad", deleted: "bad",
    }[status] ?? "neutral";
  return <Badge tone={tone as "ok"}>{status.replace(/_/g, " ").toLowerCase()}</Badge>;
}
