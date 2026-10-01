"use client";

// Charts. One axis per chart, thin marks, recessive grid, a hover tooltip on every plot,
// and a legend whenever there is more than one series. Text uses text colours; only marks
// carry series colour. Colours come from validated tokens in globals.css.

import { AlertTriangle, CheckCircle2, XCircle } from "lucide-react";
import { Area, AreaChart, Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip as ChartTooltip, XAxis, YAxis } from "recharts";

const AXIS = { fontSize: 11, fill: "var(--muted)" };
const GRID = { stroke: "var(--border)", strokeWidth: 1 };
const TOOLTIP_STYLE = {
  background: "var(--surface)",
  border: "1px solid var(--border)",
  borderRadius: 6,
  fontSize: 12,
  color: "var(--foreground)",
  boxShadow: "0 4px 12px rgba(0,0,0,0.12)",
};
const LABEL_STYLE = { color: "var(--muted)", marginBottom: 2 };
const ITEM_STYLE = { color: "var(--foreground)", padding: 0 };

function shortDate(iso: string) {
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

export function Legend({ items }: { items: { label: string; color: string; icon?: React.ReactNode }[] }) {
  return (
    <ul className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted">
      {items.map((item) => (
        <li key={item.label} className="flex items-center gap-1.5">
          <span className="h-2.5 w-2.5 rounded-sm" style={{ background: item.color }} aria-hidden />
          {item.icon}
          {item.label}
        </li>
      ))}
    </ul>
  );
}

/** A single series over time as columns. No legend: the card title names the series. */
export function TimeColumns({ data, valueKey, label, height = 200 }: { data: { date: string }[]; valueKey: string; label: string; height?: number }) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: -16 }}>
        <CartesianGrid vertical={false} {...GRID} />
        <XAxis dataKey="date" tickFormatter={shortDate} tick={AXIS} axisLine={false} tickLine={false} minTickGap={24} />
        <YAxis tick={AXIS} axisLine={false} tickLine={false} allowDecimals={false} />
        <ChartTooltip cursor={{ fill: "var(--surface-muted)" }} contentStyle={TOOLTIP_STYLE} labelStyle={LABEL_STYLE} itemStyle={ITEM_STYLE} labelFormatter={(value) => shortDate(String(value))} />
        <Bar dataKey={valueKey} name={label} fill="var(--series-1)" radius={[4, 4, 0, 0]} maxBarSize={24} />
      </BarChart>
    </ResponsiveContainer>
  );
}

export const OUTCOME_SERIES = [
  { key: "answered", label: "Answered", color: "var(--status-good)", icon: <CheckCircle2 className="h-3 w-3" aria-hidden /> },
  { key: "abstained", label: "No answer given", color: "var(--status-warning)", icon: <AlertTriangle className="h-3 w-3" aria-hidden /> },
  { key: "denied", label: "Denied", color: "var(--status-critical)", icon: <XCircle className="h-3 w-3" aria-hidden /> },
];

/** Outcomes per day, stacked. These are states, so they use the status colours, each with an icon and label. */
export function OutcomeColumns({ data, height = 200 }: { data: { date: string; answered: number; abstained: number; denied: number }[]; height?: number }) {
  return (
    <div>
      <Legend items={OUTCOME_SERIES} />
      <ResponsiveContainer width="100%" height={height}>
        <BarChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: -16 }}>
          <CartesianGrid vertical={false} {...GRID} />
          <XAxis dataKey="date" tickFormatter={shortDate} tick={AXIS} axisLine={false} tickLine={false} minTickGap={24} />
          <YAxis tick={AXIS} axisLine={false} tickLine={false} allowDecimals={false} />
          <ChartTooltip cursor={{ fill: "var(--surface-muted)" }} contentStyle={TOOLTIP_STYLE} labelStyle={LABEL_STYLE} itemStyle={ITEM_STYLE} labelFormatter={(value) => shortDate(String(value))} />
          {OUTCOME_SERIES.map((series, index) => (
            <Bar
              key={series.key}
              dataKey={series.key}
              name={series.label}
              stackId="outcome"
              fill={series.color}
              stroke="var(--surface)"
              strokeWidth={2}
              maxBarSize={24}
              radius={index === OUTCOME_SERIES.length - 1 ? [4, 4, 0, 0] : 0}
            />
          ))}
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

/** One measure over time as a line with a light wash. */
export function TrendArea({ data, valueKey, label, domain, height = 200, format }: { data: { date: string }[]; valueKey: string; label: string; domain?: [number, number]; height?: number; format?: (value: number) => string }) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: -16 }}>
        <CartesianGrid vertical={false} {...GRID} />
        <XAxis dataKey="date" tickFormatter={shortDate} tick={AXIS} axisLine={false} tickLine={false} minTickGap={24} />
        <YAxis tick={AXIS} axisLine={false} tickLine={false} domain={domain ?? ["auto", "auto"]} tickFormatter={format} />
        <ChartTooltip
          cursor={{ stroke: "var(--muted)", strokeWidth: 1 }}
          contentStyle={TOOLTIP_STYLE}
          labelStyle={LABEL_STYLE}
          itemStyle={ITEM_STYLE}
          labelFormatter={(value) => shortDate(String(value))}
          formatter={(value) => [format ? format(Number(value)) : value, label]}
        />
        <Area
          type="monotone"
          dataKey={valueKey}
          name={label}
          stroke="var(--series-1)"
          strokeWidth={2}
          fill="var(--series-1)"
          fillOpacity={0.1}
          connectNulls
          dot={data.length <= 14 ? { r: 4, fill: "var(--series-1)", stroke: "var(--surface)", strokeWidth: 2 } : false}
          activeDot={{ r: 5, stroke: "var(--surface)", strokeWidth: 2 }}
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}

/** Ranked horizontal bars with the label and value as text: readable without colour, and a table by nature. */
export function BarList({ rows, format, color = "var(--series-1)", empty = "No data yet." }: { rows: { label: string; value: number; hint?: string }[]; format?: (value: number) => string; color?: string; empty?: string }) {
  const max = Math.max(1, ...rows.map((row) => row.value));
  if (!rows.length) return <p className="text-sm text-muted">{empty}</p>;
  return (
    <ul className="space-y-2.5">
      {rows.map((row) => (
        <li key={row.label} title={row.hint}>
          <div className="mb-1 flex items-baseline justify-between gap-3 text-xs">
            <span className="truncate">{row.label}</span>
            <span className="shrink-0 tabular-nums text-muted">{format ? format(row.value) : row.value.toLocaleString()}</span>
          </div>
          <div className="h-2 rounded-r bg-surface-muted">
            <div className="h-full rounded-r" style={{ width: `${Math.max(1, (row.value / max) * 100)}%`, background: color }} />
          </div>
        </li>
      ))}
    </ul>
  );
}

export const METHOD_SERIES = [
  { key: "v1_overlap", label: "v1 word overlap", color: "var(--series-2)" },
  { key: "grounding_only", label: "Single grounding model", color: "var(--series-3)" },
  { key: "cascade", label: "v2 cascade", color: "var(--series-1)" },
];

/** Three methods compared on a few measures, all on the same 0–100% axis. */
export function MethodComparison({ rows, height = 240 }: { rows: { measure: string; v1_overlap: number; grounding_only: number; cascade: number }[]; height?: number }) {
  const percent = (value: number) => `${Math.round(value * 100)}%`;
  return (
    <div>
      <Legend items={METHOD_SERIES} />
      <ResponsiveContainer width="100%" height={height}>
        <BarChart data={rows} margin={{ top: 12, right: 8, bottom: 0, left: -10 }} barCategoryGap="28%" barGap={2}>
          <CartesianGrid vertical={false} {...GRID} />
          <XAxis dataKey="measure" tick={AXIS} axisLine={false} tickLine={false} interval={0} />
          <YAxis tick={AXIS} axisLine={false} tickLine={false} domain={[0, 1]} tickFormatter={percent} />
          <ChartTooltip cursor={{ fill: "var(--surface-muted)" }} contentStyle={TOOLTIP_STYLE} labelStyle={LABEL_STYLE} itemStyle={ITEM_STYLE} formatter={(value, name) => [percent(Number(value)), name]} />
          {METHOD_SERIES.map((series) => (
            <Bar key={series.key} dataKey={series.key} name={series.label} fill={series.color} radius={[4, 4, 0, 0]} maxBarSize={24} />
          ))}
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
