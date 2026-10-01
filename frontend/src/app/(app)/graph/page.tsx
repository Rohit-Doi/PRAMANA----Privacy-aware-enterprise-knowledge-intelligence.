"use client";

import { useQuery } from "@tanstack/react-query";
import { forceCenter, forceCollide, forceLink, forceManyBody, forceSimulation, type SimulationLinkDatum, type SimulationNodeDatum } from "d3-force";
import { Search, Waypoints } from "lucide-react";
import { useMemo, useState } from "react";

import { SensitivityBadge } from "@/components/badges";
import { PageBody, RequirePermission } from "@/components/shell";
import { Badge, Button, Card, CardHeader, EmptyState, ErrorNote, Input, PageHeader, Spinner, Stat } from "@/components/ui";
import { api } from "@/lib/api";
import { departmentLabel, titleCase } from "@/lib/utils";

interface EntityHit { key: string; name: string; type: string; mentions: number }
interface Neighbourhood {
  entity: { key: string; name: string; type: string; mentions: number };
  relations: { source: string; target: string; relation: string; key: string; name: string; type: string; confidence: number }[];
  co_mentioned: { key: string; name: string; type: string; shared: number }[];
  passages: { chunk_id: string; document_id: string; title: string; section_path: string; sensitivity_rank: number; department: string; text: string }[];
}
interface Node extends SimulationNodeDatum { id: string; name: string; type: string; center?: boolean }
interface Link extends SimulationLinkDatum<Node> { label?: string; strength: number }

const TYPE_COLOR: Record<string, string> = {
  policy: "#2447d5", regulation: "#6b3fd4", department: "#0f8a4f", role: "#b26a00", person: "#c62828",
  organization: "#0e7490", system: "#7c3aed", process: "#a16207", location: "#475569", concept: "#64748b",
};
const WIDTH = 760;
const HEIGHT = 480;

function GraphCanvas({ data, onSelect }: { data: Neighbourhood; onSelect: (key: string) => void }) {
  const graph = useMemo(() => {
    const nodes = new Map<string, Node>();
    nodes.set(data.entity.key, { id: data.entity.key, name: data.entity.name, type: data.entity.type, center: true });
    const links: Link[] = [];
    for (const relation of data.relations.slice(0, 30)) {
      if (!nodes.has(relation.key)) nodes.set(relation.key, { id: relation.key, name: relation.name, type: relation.type });
      links.push({ source: relation.source, target: relation.target, label: relation.relation.replace(/_/g, " "), strength: 1 });
    }
    for (const other of data.co_mentioned.slice(0, 26)) {
      if (!nodes.has(other.key)) nodes.set(other.key, { id: other.key, name: other.name, type: other.type });
      links.push({ source: data.entity.key, target: other.key, strength: Math.min(1, other.shared / 6) });
    }
    return { nodes: [...nodes.values()], links: links.filter((l) => nodes.has(l.source as string) && nodes.has(l.target as string)) };
  }, [data]);

  // The layout is a pure function of the data: run the simulation to rest once, synchronously.
  const layout = useMemo(() => {
    const nodes = graph.nodes.map((n) => ({ ...n }));
    const links = graph.links.map((l) => ({ ...l }));
    const simulation = forceSimulation(nodes)
      .force("link", forceLink<Node, Link>(links).id((n) => n.id).distance((l) => (l.label ? 110 : 150)))
      .force("charge", forceManyBody().strength(-260))
      .force("center", forceCenter(WIDTH / 2, HEIGHT / 2))
      .force("collide", forceCollide(34))
      .stop();
    simulation.tick(200);
    for (const node of nodes) {
      node.x = Math.max(60, Math.min(WIDTH - 60, node.x ?? WIDTH / 2));
      node.y = Math.max(24, Math.min(HEIGHT - 24, node.y ?? HEIGHT / 2));
    }
    return { nodes, links };
  }, [graph]);

  return (
    <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} className="w-full" role="img" aria-label={`Graph around ${data.entity.name}`}>
      {layout.links.map((link, index) => {
        const source = link.source as Node;
        const target = link.target as Node;
        return (
          <g key={index}>
            <line
              x1={source.x} y1={source.y} x2={target.x} y2={target.y}
              stroke={link.label ? "var(--primary)" : "var(--border)"}
              strokeWidth={link.label ? 1.6 : 0.8 + link.strength * 1.5}
              strokeDasharray={link.label ? undefined : "3 3"}
            />
            {link.label && (
              <text x={((source.x ?? 0) + (target.x ?? 0)) / 2} y={((source.y ?? 0) + (target.y ?? 0)) / 2 - 3} fontSize={9} textAnchor="middle" fill="var(--muted)">
                {link.label}
              </text>
            )}
          </g>
        );
      })}
      {layout.nodes.map((node) => (
        <g
          key={node.id}
          transform={`translate(${node.x},${node.y})`}
          onClick={() => !node.center && onSelect(node.id)}
          className={node.center ? undefined : "cursor-pointer"}
          role={node.center ? undefined : "button"}
          aria-label={node.center ? undefined : `Explore ${node.name}`}
        >
          <circle r={node.center ? 11 : 7} fill={TYPE_COLOR[node.type] ?? TYPE_COLOR.concept} stroke="var(--surface)" strokeWidth={2} />
          <text y={node.center ? 25 : 19} fontSize={node.center ? 12 : 10} fontWeight={node.center ? 600 : 400} textAnchor="middle" fill="var(--foreground)">
            {node.name.length > 26 ? `${node.name.slice(0, 25)}…` : node.name}
          </text>
        </g>
      ))}
    </svg>
  );
}

function GraphPage() {
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("remote work");
  const [chosen, setSelected] = useState<string | null>(null);

  const stats = useQuery({ queryKey: ["graph", "stats"], queryFn: () => api<Record<string, number>>("/graph/stats") });
  const results = useQuery({
    queryKey: ["graph", "search", query],
    queryFn: () => api<EntityHit[]>("/graph/search", { query: { q: query, limit: 24 } }),
    enabled: query.length >= 2,
  });
  // Until the user picks one, the first search result is shown.
  const selected = chosen ?? results.data?.[0]?.key ?? null;
  const neighbourhood = useQuery({
    queryKey: ["graph", "entity", selected],
    queryFn: () => api<Neighbourhood>("/graph/entity", { query: { key: selected! } }),
    enabled: Boolean(selected),
  });

  return (
    <PageBody>
      <PageHeader
        title="Knowledge Graph"
        description="Entities found in the documents and how they connect. The graph you see is limited to entities mentioned in documents you can read; entities that exist only in restricted material do not appear."
      />
      <div className="mb-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Stat label="Entities" value={stats.data?.entities?.toLocaleString() ?? "–"} />
        <Stat label="Mentions in passages" value={stats.data?.mentions?.toLocaleString() ?? "–"} />
        <Stat label="Typed relations" value={stats.data?.relations?.toLocaleString() ?? "–"} />
        <Stat label="Passages" value={stats.data?.chunks?.toLocaleString() ?? "–"} />
      </div>
      <div className="grid gap-4 lg:grid-cols-[18rem_1fr]">
        <Card className="self-start">
          <form className="flex gap-2 border-b p-3" onSubmit={(event) => { event.preventDefault(); if (search.trim().length >= 2) { setQuery(search.trim()); setSelected(null); } }}>
            <Input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Find an entity" aria-label="Find an entity" />
            <Button type="submit" variant="outline" size="icon" aria-label="Search">
              <Search className="h-4 w-4" />
            </Button>
          </form>
          <div className="scroll-thin max-h-[60vh] overflow-y-auto p-1.5">
            {results.isLoading && <Spinner className="m-3" />}
            {results.error && <ErrorNote error={results.error} />}
            {results.data?.length === 0 && <p className="p-3 text-sm text-muted">No entity matches “{query}” in documents you can read.</p>}
            {results.data?.map((entity) => (
              <button
                key={entity.key}
                onClick={() => setSelected(entity.key)}
                className={`flex w-full items-center justify-between gap-2 rounded-md px-2 py-1.5 text-left text-sm hover:bg-surface-muted ${selected === entity.key ? "bg-primary-soft" : ""}`}
              >
                <span className="min-w-0">
                  <span className="block truncate">{entity.name}</span>
                  <span className="block text-[11px] text-muted">{titleCase(entity.type)}</span>
                </span>
                <span className="shrink-0 text-xs tabular-nums text-muted">{entity.mentions}</span>
              </button>
            ))}
          </div>
        </Card>

        <div className="space-y-4">
          <Card>
            <CardHeader
              title={neighbourhood.data ? neighbourhood.data.entity.name : "Select an entity"}
              description={neighbourhood.data ? `${titleCase(neighbourhood.data.entity.type)} · mentioned in ${neighbourhood.data.entity.mentions} passages you can read. Solid lines are typed relations; dashed lines connect entities mentioned together. Click a node to explore it.` : undefined}
            />
            <div className="p-2">
              {neighbourhood.isLoading && <div className="flex h-64 items-center justify-center"><Spinner className="h-6 w-6" /></div>}
              {neighbourhood.error && <ErrorNote error={neighbourhood.error} />}
              {neighbourhood.data && <GraphCanvas data={neighbourhood.data} onSelect={setSelected} />}
              {!selected && !results.isLoading && <EmptyState icon={<Waypoints className="h-8 w-8" />} title="Search for an entity to explore its connections" />}
            </div>
            <div className="flex flex-wrap gap-2 border-t px-4 py-2">
              {Object.entries(TYPE_COLOR).map(([type, color]) => (
                <span key={type} className="flex items-center gap-1 text-[11px] text-muted">
                  <span className="h-2 w-2 rounded-full" style={{ background: color }} /> {titleCase(type)}
                </span>
              ))}
            </div>
          </Card>
          {neighbourhood.data && (
            <div className="grid gap-4 xl:grid-cols-2">
              <Card>
                <CardHeader title="Typed relations" description="Extracted from the passages shown on the right" />
                <ul className="divide-y text-sm">
                  {neighbourhood.data.relations.length === 0 && <li className="p-4 text-sm text-muted">No typed relations for this entity.</li>}
                  {neighbourhood.data.relations.slice(0, 14).map((relation, index) => (
                    <li key={index} className="flex flex-wrap items-center gap-1.5 px-4 py-2">
                      <Badge tone={relation.source === neighbourhood.data!.entity.key ? "primary" : "neutral"}>{relation.source === neighbourhood.data!.entity.key ? neighbourhood.data!.entity.name : relation.name}</Badge>
                      <span className="text-xs text-muted">{relation.relation.replace(/_/g, " ")}</span>
                      <Badge tone={relation.target === neighbourhood.data!.entity.key ? "primary" : "neutral"}>{relation.target === neighbourhood.data!.entity.key ? neighbourhood.data!.entity.name : relation.name}</Badge>
                    </li>
                  ))}
                </ul>
              </Card>
              <Card>
                <CardHeader title="Source passages" description="Where this entity is mentioned" />
                <ul className="scroll-thin max-h-96 divide-y overflow-y-auto">
                  {neighbourhood.data.passages.map((passage) => (
                    <li key={passage.chunk_id} className="px-4 py-2.5">
                      <p className="flex flex-wrap items-center gap-1.5 text-xs">
                        <SensitivityBadge rank={passage.sensitivity_rank} />
                        <Badge>{departmentLabel(passage.department)}</Badge>
                        <span className="font-medium">{passage.title}</span>
                      </p>
                      <p className="mt-1 line-clamp-4 whitespace-pre-wrap text-xs leading-5 text-muted">{passage.text}</p>
                    </li>
                  ))}
                </ul>
              </Card>
            </div>
          )}
        </div>
      </div>
    </PageBody>
  );
}

export default function Page() {
  return (
    <RequirePermission permission="graph:read">
      <GraphPage />
    </RequirePermission>
  );
}
