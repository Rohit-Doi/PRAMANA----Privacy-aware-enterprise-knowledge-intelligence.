"""Draw the figures used in README.md and docs/ (plain SVG, no dependencies).

    python scripts/make_figures.py

The numbers in the charts are the measured results recorded in docs/RESULTS.md. If you re-run the
evaluations and the results change, update RESULTS below and run this script again.
"""

from __future__ import annotations

from html import escape
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "docs" / "images"

# Text and surface colours, and a three-series palette checked for colour-blind separation.
INK, MUTED, FAINT, SURFACE, PANEL, LINE = "#1b2330", "#5a6677", "#8a94a3", "#ffffff", "#f5f7fa", "#d9dee6"
BLUE, ORANGE, GREEN = "#2a78d6", "#eb6834", "#1baf7a"
FONT = 'font-family="Segoe UI, Helvetica, Arial, sans-serif"'

# Measured, held out (see docs/RESULTS.md). Percent of real errors caught.
RESULTS = {
    "RAGTruth\nreal LLM answers": {"v1 word overlap": 18.0, "single grounding model": 58.4, "v2 cascade": 77.5},
    "Injected errors\nin this corpus": {"v1 word overlap": 46.3, "single grounding model": 76.0, "v2 cascade": 95.2},
    "HaluEval\nnever used for fitting": {"v1 word overlap": 10.7, "single grounding model": 60.3, "v2 cascade": 70.3},
}
BY_KIND = {
    "Changed number": {"v1 word overlap": 0.0, "v2 cascade": 100.0},
    "Flipped meaning": {"v1 word overlap": 0.0, "v2 cascade": 93.3},
    "Swapped name or entity": {"v1 word overlap": 0.0, "v2 cascade": 84.0},
    "Unrelated true statement": {"v1 word overlap": 85.3, "v2 cascade": 98.2},
}
SERIES_COLOUR = {"v1 word overlap": ORANGE, "single grounding model": BLUE, "v2 cascade": GREEN}


def text(
    x: float, y: float, content: str, *, size: int = 13, fill: str = INK, weight: str = "400", anchor: str = "start"
) -> str:
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" fill="{fill}" font-weight="{weight}" '
        f'text-anchor="{anchor}" {FONT}>{escape(content)}</text>'
    )


def box(x: float, y: float, w: float, h: float, *, fill: str = PANEL, stroke: str = LINE, radius: int = 8) -> str:
    return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{radius}" fill="{fill}" stroke="{stroke}"/>'


def svg(width: int, height: int, body: list[str], title: str) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
        f'role="img" aria-label="{escape(title)}">\n'
        f'<rect width="{width}" height="{height}" rx="12" fill="{SURFACE}" stroke="{LINE}"/>\n'
        + "\n".join(body)
        + "\n</svg>\n"
    )


def arrow(x: float, y1: float, y2: float) -> str:
    return (
        f'<line x1="{x}" y1="{y1}" x2="{x}" y2="{y2 - 6}" stroke="{FAINT}" stroke-width="1.5"/>'
        f'<path d="M{x - 5},{y2 - 7} L{x},{y2} L{x + 5},{y2 - 7} Z" fill="{FAINT}"/>'
    )


def harrow(x1: float, x2: float, y: float, label: str = "") -> str:
    direction = 1 if x2 > x1 else -1
    out = (
        f'<line x1="{x1}" y1="{y}" x2="{x2 - 6 * direction}" y2="{y}" stroke="{FAINT}" stroke-width="1.5"/>'
        f'<path d="M{x2 - 7 * direction},{y - 5} L{x2},{y} L{x2 - 7 * direction},{y + 5} Z" fill="{FAINT}"/>'
    )
    if label:
        out += text((x1 + x2) / 2, y - 6, label, size=11, fill=MUTED, anchor="middle")
    return out


# ── Architecture ────────────────────────────────────────────────────────────────
def architecture() -> str:
    width, left, inner = 980, 40, 900
    body = [
        text(left, 40, "PRAMĀṆA — architecture", size=20, weight="600"),
        text(
            left,
            62,
            "Eight layers. Everything except the drafting model runs on your own machine.",
            size=13,
            fill=MUTED,
        ),
    ]
    layers = [
        (
            "1",
            "Users",
            [
                "Web app: Ask · Verify Studio · Compare roles · Sources · Knowledge graph",
                "Policies · Users · Audit log · Monitoring · Reports · Analytics · Settings",
            ],
            BLUE,
        ),
        (
            "2",
            "Authentication",
            [
                "Password (Argon2id) + optional TOTP · 15-minute access token · rotating refresh cookie",
                "Identity always comes from the verified session and the database, never from the request",
            ],
            BLUE,
        ),
        (
            "3",
            "Privacy layer",
            [
                "Access scope from policy (OPA): role, department, sensitivity, region",
                "Personal-data detection (Presidio) · prompt-injection screening · egress ceiling for external models",
            ],
            ORANGE,
        ),
        (
            "4",
            "Multi-agent orchestrator",
            [
                "LangGraph state machine, seven agents, live progress events:",
                "privacy → retrieval ∥ graph reasoning → verification → response → fact-checking ⟲ → governance",
            ],
            ORANGE,
        ),
    ]
    y = 84
    for number, name, lines, colour in layers:
        body.append(box(left, y, inner, 72))
        body.append(f'<rect x="{left}" y="{y}" width="6" height="72" rx="3" fill="{colour}"/>')
        body.append(text(left + 20, y + 28, f"{number}  {name}", size=14, weight="600"))
        for i, line in enumerate(lines):
            body.append(text(left + 250, y + 28 + i * 20, line, size=12, fill=MUTED))
        body.append(arrow(left + inner / 2, y + 72, y + 90))
        y += 90

    # Layers 5 and 6 side by side.
    half = (inner - 20) / 2
    for i, (number, name, lines) in enumerate(
        [
            (
                "5",
                "Knowledge intelligence",
                [
                    "Hybrid GraphRAG:",
                    "meaning (dense vectors) + keywords (BM25)",
                    "+ entity graph with PageRank",
                    "→ rank fusion → cross-encoder reranking",
                    "Every query filtered by the access scope",
                ],
            ),
            (
                "6",
                "Validation and fact-checking",
                [
                    "Per sentence: grounding · entailment (two readings)",
                    "· unsupported spans · numbers and names",
                    "→ calibrated confidence → overriding rules",
                    "→ LLM judge with verbatim quote (uncertain only)",
                    "Failed sentences are rewritten once or removed",
                ],
            ),
        ]
    ):
        x = left + i * (half + 20)
        body.append(box(x, y, half, 138))
        body.append(f'<rect x="{x}" y="{y}" width="6" height="138" rx="3" fill="{GREEN}"/>')
        body.append(text(x + 20, y + 28, f"{number}  {name}", size=14, weight="600"))
        for j, line in enumerate(lines):
            body.append(text(x + 20, y + 52 + j * 18, line, size=12, fill=MUTED))
    body.append(harrow(left + half + 2, left + half + 18, y + 69))
    body.append(arrow(left + inner / 2, y + 138, y + 156))
    y += 156

    for number, name, lines in [
        (
            "7",
            "Trust and governance",
            [
                "Trust score with its components · review queue for low-trust answers",
                "Hash-chained, append-only audit log · immutable policy versions",
            ],
        ),
        (
            "8",
            "Output",
            [
                "Answer with a verdict per sentence · citations with the quoted passage highlighted",
                "Count of withheld sources · downloadable validation report — or an honest refusal",
            ],
        ),
    ]:
        body.append(box(left, y, inner, 72))
        body.append(f'<rect x="{left}" y="{y}" width="6" height="72" rx="3" fill="{BLUE}"/>')
        body.append(text(left + 20, y + 28, f"{number}  {name}", size=14, weight="600"))
        for i, line in enumerate(lines):
            body.append(text(left + 250, y + 28 + i * 20, line, size=12, fill=MUTED))
        if number == "7":
            body.append(arrow(left + inner / 2, y + 72, y + 90))
        y += 90

    y += 6
    body.append(text(left, y + 14, "What it runs on", size=13, weight="600"))
    y += 26
    third = (inner - 40) / 3
    columns = [
        (
            "Datastores (Docker)",
            [
                "PostgreSQL — users, documents, runs, audit",
                "Qdrant — dense + keyword vectors",
                "Neo4j — entities and relations",
                "Redis — limits, cache · OPA — policy",
            ],
        ),
        (
            "Local models (CPU)",
            [
                "bge-small embeddings · BM25 · MiniLM reranker",
                "HHEM grounding · DeBERTa NLI · LettuceDetect",
                "RoBERTa reader · GLiNER2 / spaCy extraction",
                "Presidio personal data · RapidOCR",
            ],
        ),
        (
            "External (one free key)",
            [
                "Gemini — drafts answers and judges",
                "uncertain sentences only.",
                "Sees pseudonymised text; never sees",
                "evidence above the egress ceiling.",
            ],
        ),
    ]
    for i, (name, lines) in enumerate(columns):
        x = left + i * (third + 20)
        body.append(box(x, y, third, 112, fill=SURFACE))
        body.append(text(x + 14, y + 24, name, size=13, weight="600"))
        for j, line in enumerate(lines):
            body.append(text(x + 14, y + 46 + j * 18, line, size=11, fill=MUTED))
    y += 112
    return svg(
        width, y + 28, body, "PRAMANA architecture: eight layers, datastores, local models and one external model"
    )


# ── Request flow ────────────────────────────────────────────────────────────────
def request_flow() -> str:
    width = 980
    body = [
        text(40, 40, "What happens to one question", size=20, weight="600"),
        text(
            40,
            62,
            "Seven agents. Every path, including refusals and failures, ends in the audit log.",
            size=13,
            fill=MUTED,
        ),
    ]
    cx, w, h = 330, 380, 58

    def node(y: float, title: str, detail: str, colour: str = BLUE) -> None:
        body.append(box(cx - w / 2, y, w, h))
        body.append(f'<rect x="{cx - w / 2}" y="{y}" width="6" height="{h}" rx="3" fill="{colour}"/>')
        body.append(text(cx - w / 2 + 18, y + 24, title, size=13, weight="600"))
        body.append(text(cx - w / 2 + 18, y + 44, detail, size=11, fill=MUTED))

    def side(y: float, title: str, lines: list[str]) -> None:
        x, sw = 600, 340
        height = 26 + 16 * len(lines)
        body.append(box(x, y, sw, height, fill=SURFACE))
        body.append(text(x + 14, y + 20, title, size=12, weight="600"))
        for i, line in enumerate(lines):
            body.append(text(x + 14, y + 37 + i * 16, line, size=11, fill=MUTED))
        body.append(harrow(cx + w / 2, x, y + 29))

    y = 86
    body.append(text(cx, y + 4, "Question from a signed-in user", size=13, fill=INK, anchor="middle", weight="600"))
    body.append(arrow(cx, y + 12, y + 32))
    y += 32
    node(y, "1  Privacy agent", "access scope from policy · redact identifiers · flag injection", ORANGE)
    side(y, "No scope, or no permission", ["→ refused, recorded"])
    body.append(arrow(cx, y + h, y + h + 22))
    y += h + 22

    # Two parallel retrieval agents.
    half = (w - 16) / 2
    for i, (title, detail) in enumerate(
        [("2  Retrieval agent", "meaning + keywords"), ("3  Graph reasoning agent", "entities → PageRank")]
    ):
        x = cx - w / 2 + i * (half + 16)
        body.append(box(x, y, half, h))
        body.append(f'<rect x="{x}" y="{y}" width="6" height="{h}" rx="3" fill="{GREEN}"/>')
        body.append(text(x + 16, y + 24, title, size=13, weight="600"))
        body.append(text(x + 16, y + 44, detail, size=11, fill=MUTED))
    body.append(text(cx + w / 2 + 10, y + 34, "in parallel, both inside the scope", size=11, fill=MUTED))
    body.append(arrow(cx, y + h, y + h + 22))
    y += h + 22

    node(y, "4  Verification agent", "fuse · rerank · re-check access · is the evidence good enough?", GREEN)
    side(
        y,
        "Evidence too weak",
        ["→ “not enough information”, plus how many", "relevant sources were withheld (if policy allows)"],
    )
    body.append(arrow(cx, y + h, y + h + 22))
    y += h + 22

    node(y, "5  Response generation agent", "evidence may leave the machine?  yes → LLM   no → quote locally", ORANGE)
    side(
        y,
        "Evidence above the egress ceiling",
        [
            "→ a local reader quotes the answering sentence",
            "→ if none: LLM with the permitted passages only",
            "→ otherwise decline",
        ],
    )
    body.append(arrow(cx, y + h, y + h + 22))
    y += h + 22

    node(y, "6  Fact-checking agent", "verdict per sentence · rewrite once · remove what still fails", GREEN)
    # Correction loop back to generation.
    lx = cx - w / 2 - 34
    body.append(
        f'<path d="M{cx - w / 2},{y + h / 2} H{lx} V{y - 22 - h / 2} H{cx - w / 2 - 6}" fill="none" stroke="{FAINT}" stroke-width="1.5"/>'
        f'<path d="M{cx - w / 2 - 7},{y - 22 - h / 2 - 5} L{cx - w / 2},{y - 22 - h / 2} L{cx - w / 2 - 7},{y - 22 - h / 2 + 5} Z" fill="{FAINT}"/>'
    )
    body.append(text(lx - 8, y - 11, "one", size=11, fill=MUTED, anchor="end"))
    body.append(text(lx - 8, y + 3, "rewrite", size=11, fill=MUTED, anchor="end"))
    side(y, "Nothing verifiable is left", ["→ no answer is given"])
    body.append(arrow(cx, y + h, y + h + 22))
    y += h + 22

    node(y, "7  Governance and audit agent", "restore names for permitted readers · trust score · audit entry", BLUE)
    body.append(arrow(cx, y + h, y + h + 22))
    y += h + 22
    body.append(
        text(
            cx, y + 14, "Verified answer with citations — or a recorded refusal", size=13, anchor="middle", weight="600"
        )
    )
    return svg(width, int(y + 40), body, "Request flow through the seven agents")


# ── Charts ──────────────────────────────────────────────────────────────────────
def grouped_bars(data: dict[str, dict[str, float]], title: str, subtitle: str, *, width: int = 980) -> str:
    series = list(next(iter(data.values())))
    left, right, top, plot_h = 70, 30, 120, 260
    plot_w = width - left - right
    body = [text(40, 40, title, size=20, weight="600"), text(40, 62, subtitle, size=13, fill=MUTED)]
    # Legend.
    lx = 40
    for name in series:
        body.append(f'<rect x="{lx}" y="78" width="12" height="12" rx="3" fill="{SERIES_COLOUR[name]}"/>')
        body.append(text(lx + 18, 89, name, size=12, fill=MUTED))
        lx += 34 + 7 * len(name)
    # Grid.
    for tick in (0, 25, 50, 75, 100):
        y = top + plot_h - plot_h * tick / 100
        body.append(f'<line x1="{left}" y1="{y}" x2="{left + plot_w}" y2="{y}" stroke="{LINE}" stroke-width="1"/>')
        body.append(text(left - 10, y + 4, f"{tick}%", size=11, fill=FAINT, anchor="end"))
    group_w = plot_w / len(data)
    bar_w, gap = 46, 2
    for g, (group, values) in enumerate(data.items()):
        total = len(values) * bar_w + (len(values) - 1) * gap
        x0 = left + g * group_w + (group_w - total) / 2
        for i, (name, value) in enumerate(values.items()):
            x = x0 + i * (bar_w + gap)
            height = max(plot_h * value / 100, 2)
            y = top + plot_h - height
            r = min(4, height)
            body.append(
                f'<path d="M{x},{top + plot_h} V{y + r} Q{x},{y} {x + r},{y} H{x + bar_w - r} Q{x + bar_w},{y} {x + bar_w},{y + r} '
                f'V{top + plot_h} Z" fill="{SERIES_COLOUR[name]}"><title>{escape(group.replace(chr(10), " "))} — {escape(name)}: {value:.1f}%</title></path>'
            )
            body.append(
                text(
                    x + bar_w / 2,
                    y - 6,
                    f"{value:.0f}%",
                    size=12,
                    anchor="middle",
                    weight="600" if name == "v2 cascade" else "400",
                )
            )
        for j, line in enumerate(group.split("\n")):
            body.append(
                text(
                    left + g * group_w + group_w / 2,
                    top + plot_h + 22 + j * 16,
                    line,
                    size=12,
                    fill=INK if j == 0 else MUTED,
                    anchor="middle",
                    weight="600" if j == 0 else "400",
                )
            )
    return svg(width, top + plot_h + 60, body, title)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    figures = {
        "architecture.svg": architecture(),
        "request-flow.svg": request_flow(),
        "verifier-benchmarks.svg": grouped_bars(
            RESULTS,
            "Unsupported sentences caught",
            "Share of real errors flagged, on held-out data. Higher is better.",
        ),
        "verifier-by-error-type.svg": grouped_bars(
            BY_KIND,
            "Errors caught, by kind of error",
            "Sentences from this corpus with one known error injected. Held-out half.",
        ),
    }
    for name, content in figures.items():
        (OUT / name).write_text(content, encoding="utf-8")
        print("wrote", OUT / name)


if __name__ == "__main__":
    main()
