# Architecture

PRAMĀṆA answers questions over an organisation's documents and shows, sentence by sentence,
what each statement rests on. Three properties shape every part of the design:

1. **A user only ever gets what they are allowed to read.** Access is decided before
   retrieval, enforced inside each datastore query, and re-checked afterwards.
2. **Nothing is shown unverified.** Each sentence of an answer is checked against the
   evidence by local models; sentences that fail are rewritten once or removed.
3. **Everything is on the record.** Each request leaves an append-only, hash-chained
   audit entry that an auditor can verify without trusting the application.

## The eight layers

```
 1 Users            Web app (Next.js): Ask, Verify Studio, Compare, Sources, Graph,
                    Policies, Users, Audit, Monitoring, Reports, Analytics, Settings
 2 Authentication   Password + TOTP, short-lived access token, rotating refresh cookie
 3 Privacy layer    Access scope from policy (OPA) · personal-data detection (Presidio)
                    · prompt-injection screening · egress ceiling for external models
 4 Orchestrator     LangGraph state machine, seven agents, live progress events
 5 Knowledge        Hybrid GraphRAG: dense + keyword search (Qdrant), entity graph
                    (Neo4j), rank fusion, cross-encoder reranking
 6 Validation       Claim extraction → grounding → NLI → span detection → literal
                    checks → calibrated confidence → optional LLM judge → correction
 7 Trust/governance Trust score, review queue, hash-chained audit log, policy versions
 8 Output           Answer with per-sentence verdicts, citations with quoted passages,
                    withheld-source count, downloadable validation report
```

## Request flow

```
question
  │
  ▼
privacy ──────────► scope from OPA (departments, sensitivity ceilings, regions)
  │                 redact identifiers in the question, flag injection patterns
  ├──────────────┐
  ▼              ▼
retrieval      graph_reasoning        run in parallel, both filtered by the scope
(dense+BM25)   (entities → PageRank)
  └──────┬───────┘
         ▼
verification        fuse (weighted RRF) → rerank → post-retrieval access re-check
  │                 → decide: enough evidence? may it leave the machine?
  ▼
response_generation external LLM on pseudonymised evidence, or — when the evidence is
  │                 above the egress ceiling — an extractive answer built locally
  ▼
fact_checking ────► per-sentence verdicts; one rewrite round, then failing sentences
  │    ▲            are removed
  │    └── loop (at most once)
  ▼
governance          restore names for permitted readers, mask output, trust score,
                    risk score, audit entry, review-queue item when trust is low
```

Every path through the graph ends in `governance`, including refusals and failures, so
there is no way to produce a response that was not audited.

## Access control

A role carries two ceilings: the highest sensitivity it may read **in its own
departments** and the highest it may read **anywhere**. Sensitivity is ranked
PUBLIC 0 · INTERNAL 1 · CONFIDENTIAL 2 · RESTRICTED 3. A document also has a region;
a user sees their own region plus `global` unless the role spans all regions.

| Role | Own department | Other departments | Notes |
|---|---|---|---|
| employee | INTERNAL | INTERNAL | |
| manager | CONFIDENTIAL | INTERNAL | may add documents |
| analyst | CONFIDENTIAL | CONFIDENTIAL | reports, monitoring, analytics |
| compliance_officer | CONFIDENTIAL | CONFIDENTIAL | audit log, policy view, answer review |
| executive | RESTRICTED | RESTRICTED | all regions, may read personal data |
| admin | INTERNAL | INTERNAL | manages users, policy and sources; **no elevated content access** |

The policy is written in Rego and evaluated by OPA. Instead of asking "may this user
read this document?" once per document, the policy returns a **scope**, and the scope is
compiled into three equivalent filters:

| Store | Enforcement |
|---|---|
| Qdrant | payload filter on every vector and keyword search |
| Neo4j | `WHERE` predicate on every chunk and relationship traversed |
| Postgres | SQL predicate on every document and chunk query |

After retrieval, the ids of the selected passages are sent back to OPA (`allowed_ids`)
as an independent second check. The test suite verifies that the Python reference rule,
the three compiled filters and the Rego policy agree for every role, department,
sensitivity and region combination.

Policy versions are immutable rows in Postgres. Publishing a version pushes it to OPA;
each audit entry records the version that decided it.

## Privacy

* **High-risk identifiers** (government ids, bank and card numbers) are masked for
  everyone without the `pii:read` permission and are never sent to an external model.
* **Contact-level data** (names, email addresses, phone numbers) is ordinary business
  information in PUBLIC and INTERNAL documents, and personal data in CONFIDENTIAL and
  RESTRICTED ones. There it is replaced by stable placeholders (`[PERSON_1]`) before a
  prompt is built; the mapping stays in memory for that one request and the answer is
  restored locally, only for readers allowed to see it.
* **Egress ceiling.** Evidence above the configured sensitivity (default CONFIDENTIAL)
  never leaves the machine. The question is then answered *extractively*: the most
  relevant sentences are quoted verbatim, selected by the local cross-encoder, with no
  LLM call.
* **Withheld sources.** If relevant passages exist that the user may not read, roles
  permitted to know are told how many were withheld — never what they contain.

## Retrieval

| Step | Method | Why |
|---|---|---|
| Chunking | structure-aware, ~320 tokens, one sentence of overlap, never across sections | a passage keeps its heading path, so "the limit" is findable by "travel expense limit" |
| Dense search | `bge-small-en-v1.5` (384-d, local, ONNX) | meaning |
| Keyword search | BM25 sparse vectors with IDF in Qdrant | exact terms, ids, numbers |
| Graph search | question entities → entity–passage subgraph → personalised PageRank | multi-hop questions where the answer passage shares no words with the question |
| Fusion | weighted reciprocal rank fusion (k = 60; 1.0 / 1.0 / 0.7) | no score normalisation needed |
| Reranking | `ms-marco-MiniLM-L-12` cross-encoder on the top 18 | precision at the top |
| Abstention | best reranker score below a threshold → "not enough information" | no answer beats a wrong answer |

## Validation

For each sentence of a draft answer:

1. **Align** — find the evidence window with the highest lexical overlap.
2. **Grounding** — HHEM-2.1-open scores whether the window supports the sentence.
3. **Entailment** — DeBERTa-v3 NLI gives entailment and contradiction probabilities,
   also across documents (two sources that disagree are reported as CONFLICTING).
4. **Spans** — LettuceDetect marks unsupported character spans.
5. **Literal checks** — every number, identifier and proper name in the sentence must
   appear in the evidence.
6. **Calibration** — a logistic model, trained on labelled data, turns the signals into
   one probability. `scripts/train_calibrator.py` fits it and picks the decision
   threshold under a false-alarm limit.
7. **Judge** — only sentences in the uncertain band go to the LLM, which must return a
   verbatim quote; the quote is checked against the evidence.

Verdicts: SUPPORTED · PARTIAL · UNSUPPORTED · CONTRADICTED · CONFLICTING.
The same engine powers **Verify Studio**, where a user pastes any text (from any tool)
and checks it against their documents.

## Governance

* **Audit log** — `entry_hash = SHA-256(previous_hash ‖ canonical_json(entry))`. Database
  triggers reject UPDATE, DELETE and TRUNCATE. `POST /audit/verify` re-computes the chain.
* **Trust score** — verification 50 %, evidence quality 25 %, citation integrity 15 %,
  policy compliance 10 %. The components are shown, not just the number.
* **Review queue** — low-trust answers and negative feedback are queued for a reviewer.
* **Evaluation runs** — every benchmark run is stored with its configuration and
  pass/fail gates, and shown on the Reports page.

## Components

| Component | Technology | Role |
|---|---|---|
| API | FastAPI, SQLAlchemy 2 (async), Pydantic | one modular service: `auth`, `privacy`, `knowledge`, `graph`, `retrieval`, `llm`, `validation`, `agents`, `governance`, `evaluation` |
| Orchestration | LangGraph | explicit state machine with a bounded correction loop |
| Relational store | PostgreSQL 16 | users, documents, chunks, runs, claims, audit, policy versions, job queue |
| Vector store | Qdrant | dense and sparse vectors with payload filtering |
| Graph store | Neo4j Community | entities, relations, provenance |
| Policy engine | Open Policy Agent | access decisions, testable policy |
| Cache / limits | Redis | rate limits, LLM response cache, provider cool-downs |
| Background work | Postgres queue (`FOR UPDATE SKIP LOCKED`) | ingestion; survives restarts, no extra broker |
| Web app | Next.js, React, Tailwind, TanStack Query | all screens |
| Language model | Gemini free tier over HTTP, optional fallbacks | drafting and judging only |

Everything except the drafting model runs locally on CPU.
