import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

import type { Verdict } from "./types";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export const SENSITIVITY = ["PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED"] as const;

export const SENSITIVITY_STYLE: Record<number, string> = {
  0: "bg-ok-soft text-ok",
  1: "bg-primary-soft text-primary",
  2: "bg-warn-soft text-warn",
  3: "bg-bad-soft text-bad",
};

export const VERDICT_META: Record<Verdict, { label: string; tone: "ok" | "warn" | "bad" | "info"; description: string }> = {
  SUPPORTED: { label: "Supported", tone: "ok", description: "The cited source states this." },
  PARTIAL: { label: "Partly supported", tone: "warn", description: "The source supports the main point, not every detail." },
  UNSUPPORTED: { label: "Not supported", tone: "bad", description: "This could not be found in the sources." },
  CONTRADICTED: { label: "Contradicted", tone: "bad", description: "The source says something different." },
  CONFLICTING: { label: "Sources disagree", tone: "info", description: "One current source supports this and another contradicts it." },
};

export const TONE_TEXT = { ok: "text-ok", warn: "text-warn", bad: "text-bad", info: "text-info" } as const;
export const TONE_SOFT = {
  ok: "bg-ok-soft text-ok",
  warn: "bg-warn-soft text-warn",
  bad: "bg-bad-soft text-bad",
  info: "bg-info-soft text-info",
} as const;
export const TONE_UNDERLINE = {
  ok: "decoration-ok",
  warn: "decoration-warn",
  bad: "decoration-bad",
  info: "decoration-info",
} as const;

export const DEPARTMENT_LABEL: Record<string, string> = {
  company: "Company",
  hr: "People (HR)",
  security: "Security",
  finance: "Finance",
  legal: "Legal",
  engineering: "Engineering",
  it: "IT",
  marketing: "Marketing",
  sales: "Sales",
  operations: "Operations",
  compliance: "Compliance",
};

export const ROLE_LABEL: Record<string, string> = {
  employee: "Employee",
  manager: "Manager",
  analyst: "Analyst",
  compliance_officer: "Compliance Officer",
  executive: "Executive",
  admin: "Administrator",
};

export const REGIONS = ["global", "amer", "emea", "apac"] as const;

export function departmentLabel(key: string) {
  return DEPARTMENT_LABEL[key] ?? key;
}

export function percent(value: number | null | undefined, digits = 0) {
  if (value === null || value === undefined) return "–";
  return `${(value * 100).toFixed(digits)}%`;
}

export function duration(ms: number | null | undefined) {
  if (ms === null || ms === undefined) return "–";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  return `${(ms / 1000).toFixed(1)} s`;
}

export function dateTime(iso: string | null | undefined) {
  if (!iso) return "–";
  return new Date(iso).toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function relativeTime(iso: string | null | undefined) {
  if (!iso) return "–";
  const seconds = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} h ago`;
  return `${Math.floor(seconds / 86400)} d ago`;
}

export function bytes(size: number | null | undefined) {
  if (!size) return "–";
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(0)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

export function titleCase(text: string) {
  return text.replace(/[_-]+/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

export function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : "Something went wrong";
}
