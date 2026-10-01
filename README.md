# PRAMĀṆA

**Privacy-aware enterprise knowledge intelligence.** Ask a question over your
organisation's documents and get an answer in which every sentence has been checked
against a source you are allowed to read — or no answer at all.

*Pramāṇa* (Sanskrit): a means of valid knowledge; proof.

| | |
|---|---|
| **Access-aware** | Role, department, sensitivity and region decide what a user can retrieve. The rule is enforced inside every datastore query and re-checked afterwards |
| **Verified** | Each sentence is checked by local models (grounding, entailment, unsupported spans, numbers and names). Sentences that fail are rewritten once or removed |
| **Private** | Personal data is masked for readers and pseudonymised before any prompt. Restricted material never leaves the machine: a local reading model quotes the sentence that answers, with no LLM call |
| **Accountable** | Hash-chained audit log, versioned policies, trust score with its components, review queue, stored benchmark runs |
| **One key** | Runs on a laptop CPU with one free API key. Every model except the drafting LLM is local |

## Measured results

Everything below was measured on this repository's code on a laptop CPU (i7-1360P,
16 GB RAM, no GPU). Each run is stored and shown on the **Reports** page, and can be
reproduced with the commands in [docs/RUNBOOK.md](docs/RUNBOOK.md).

### Whole pipeline — 65 golden questions over 1,744 documents

| | Result | Target |
|---|---|---|
| Answerable questions answered correctly | **53 of 55** (96.4 %); 0 wrong, 2 declined | ≥ 85 % |
| Unanswerable questions declined | **10 of 10** | ≥ 80 % |
| Sentences in final answers that are verified | 95.1 % | ≥ 95 % |
| Restricted facts leaked to roles without access | **0** | 0 |
| Median response time | 6.1 s (95th percentile 13.7 s) | ≤ 10 s |
| LLM calls | 50 for 77 runs; 17 answers were quoted locally with none | ≤ 4 per question |

### Access control — 28 planted facts, asked as every role

| | Result |
|---|---|
| Unauthorised attempts | 149 |
| Leaks (planted value in any returned passage) | **0** |
| Authorised lookups that found the fact | 47 of 47 |

### Retrieval — is the passage with the answer in the top 8?

| Configuration | Recall@8 | MRR |
|---|---|---|
| Meaning (dense) only | 92.7 % | 0.900 |
| Keywords (BM25) only | 100 % | 0.975 |
| Meaning + keywords | 100 % | 0.944 |
| + graph, before reranking | 100 % | 0.805 |
| Full pipeline, reranked | **100 %** | **0.977** |

The graph retriever does not help on these single-fact questions (it lowers the order
before reranking, which then repairs it). It is there for questions that need two
documents joined by an entity; that case is not measured here.

### Verifier — does it catch unsupported sentences?

Held-out data, split by source document. "v1" is the word-overlap check of the first
version of this project, reproduced on the same sentences.

| Benchmark | | Errors caught | False alarms | AUROC |
|---|---|---|---|---|
| **RAGTruth** — human-annotated errors in real LLM answers (846 sentences) | v2 | **77.5 %** | 14.0 % | **0.916** |
| | single grounding model | 58.4 % | 14.4 % | 0.817 |
| | v1 | 18.0 % | 8.2 % | 0.726 |
| **Injected errors** in this corpus (485 sentences) | v2 | **95.2 %** | 0.0 % | **0.994** |
| | single grounding model | 76.0 % | 0.0 % | 0.952 |
| | v1 | 46.3 % | 0.0 % | 0.772 |
| **HaluEval** — never used for fitting (245 answers) | v2 | **70.3 %** | 18.5 % | 0.765 |
| | v1 | 10.7 % | 3.2 % | 0.802 |

By kind of injected error, v2 catches: a changed number 100 %, a flipped meaning 93 %,
an unrelated true statement 98 %, a swapped name or entity 84 %. Version 1 caught none of
the first, second and fourth kinds.

What these numbers do not show:

* On RAGTruth, at answer level (flag the answer if any sentence is flagged), F1 is 0.50
  against a target of 0.70 that was set before measuring. Small CPU models do not reach
  it; the target is left in place and shown as not met.
* Roughly one supported sentence in seven is flagged on RAGTruth. In the answer pipeline
  such a sentence gets a second opinion from the LLM judge; in Verify Studio the reader
  sees the passage and decides.
* The golden questions were written for the synthetic company documents by the same
  process that built the system. They show the pipeline works end to end, not how it
  performs on your documents.



## What you can do with it

| Screen | Purpose |
|---|---|
| **Ask** | Question → live view of the seven agents → answer with per-sentence verdicts, citations with highlighted quotes, trust score, reasoning, policy decision, downloadable report |
| **Verify Studio** | Paste any text — from this or any other tool — and check it sentence by sentence against the knowledge base or a reference you paste |
| **Compare** | The same question as two personas, side by side |
| **Sources** | Upload (PDF, DOCX, PPTX, Markdown, HTML, text, e-mail; scanned PDFs and images via local OCR), suggested sensitivity, ingestion progress per stage, passages with personal data masked |
| **Graph** | Explore entities and relations extracted from the documents you can read |
| **Policies** | Role ceilings, egress ceiling, simulator ("what can this user see, and why?"), version history |
| **Users** | Accounts, roles, departments, regions, MFA status |
| **Audit** | Every decision, filterable; one-click verification of the hash chain |
| **Monitoring** | Datastore health, model status, LLM usage and quota, latency per agent |
| **Reports / Analytics** | Verdict distribution, review queue, benchmark runs with pass/fail gates; usage and trust over time |
| **Settings** | Profile, theme, MFA enrolment with recovery codes, sessions |

## Architecture

```
 Users ─► Authentication ─► Privacy layer ─► Multi-agent orchestrator
                                                   │
              ┌────────────────────────────────────┤
              ▼                                    ▼
   Knowledge intelligence                  Validation & fact-checking
   dense + keyword + graph search          grounding · NLI · spans · literal checks
   fusion · reranking                      calibrated confidence · judge · correction
              └────────────────┬───────────────────┘
                               ▼
                    Trust & governance ─► Output
                    trust score · audit chain · review
```

Seven agents in a LangGraph state machine: **privacy → retrieval ∥ graph reasoning →
verification → response generation → fact checking (one correction loop) → governance**.

Details: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) ·
why each choice was made: [docs/DECISIONS.md](docs/DECISIONS.md)

## Quick start

Requirements: Docker Desktop, Python 3.12 with [uv](https://docs.astral.sh/uv/),
Node.js 20+, 16 GB RAM. No GPU.

```bash
python scripts/init_env.py            # creates .env; put your GOOGLE_API_KEY in it
docker compose up -d --wait           # Postgres, Redis, Qdrant, Neo4j, OPA
cd backend && uv sync && uv run alembic upgrade head && cd ..
python scripts/seed.py                # roles, demo personas, first policy version
python scripts/download_models.py     # local models, about 2 GB, once
python scripts/fetch_data.py          # free public corpora
python scripts/generate_synthetic.py  # the fictional company's confidential documents
python scripts/ingest_corpus.py       # index everything (1–2 hours on CPU)
```

Then, in two terminals:

```bash
cd backend && uv run uvicorn pramana.api.app:app --port 8000
```

```bash
cd frontend && npm ci && npm run build && npm run start
```

Open http://localhost:3000 and sign in as a demo persona (password: `DEMO_PASSWORD` in
`.env`). A guided tour is in [docs/DEMO.md](docs/DEMO.md); operations and
troubleshooting are in [docs/RUNBOOK.md](docs/RUNBOOK.md).

## Keys

| Key | Required | Used for |
|---|---|---|
| `GOOGLE_API_KEY` | **yes** | drafting answers and judging uncertain sentences (Gemini free tier) |
| `GROQ_API_KEY`, `CEREBRAS_API_KEY` | no | extra capacity when the free quota runs out |
| `OLLAMA_BASE_URL` | no | a local model instead of, or after, the hosted ones |

Every other secret in `.env` is generated locally by `scripts/init_env.py`.

## Data

| Collection | Source | Licence | Use |
|---|---|---|---|
| Handbook | GitLab Handbook (450 pages) | upstream licence, attribution kept | public and internal policy text |
| Regulations | GDPR, India DPDP Act, HIPAA Part 164, Sarbanes-Oxley, NIST CSF 2.0, NIST AI RMF, NIST SP 800-171 | public law / public domain | compliance questions |
| Contracts | CUAD | CC BY 4.0 | long legal documents |
| E-mail | Enron corpus sample | public record | messy real correspondence |
| Aurelia Systems | generated by `scripts/generate_synthetic.py` | synthetic | confidential material no company publishes: pay bands, board minutes, an acquisition memo, an incident report |
| RAGTruth, HaluEval | public benchmarks | see upstream | training and testing the verifier (not ingested) |

All of it is downloaded or generated by the scripts; nothing has to be collected by hand.
Your own documents go in through **Sources → Upload**.

## Repository

```
backend/     FastAPI service (src/pramana), migrations, tests
frontend/    Next.js web app
policies/    Access policy (Rego) and its tests
scripts/     Setup, data, ingestion, training and evaluation commands
eval/        Golden questions for the demo tenant
docs/        Architecture, decisions, runbook, demo walkthrough
compose.yaml Datastores; `--profile app` adds the API, worker and web app
```

## Quality checks

```bash
cd backend && uv run ruff check src tests ../scripts && uv run mypy && uv run pytest
cd frontend && npx tsc --noEmit && npx eslint src && npm run build
```

## Limits

* Scanned pages are read by a small OCR model. It has been tested only on clean printed pages; tables in scans lose their structure.
* English only: the local models are English models.
* The free LLM tier limits throughput to a few questions per minute.
* Answers from restricted material are a quoted sentence: they do not combine passages, and questions whose answer sits in a restricted table are declined.
* Built and measured on one laptop; not load-tested, and the container images and CI
  workflow have not yet been run.
