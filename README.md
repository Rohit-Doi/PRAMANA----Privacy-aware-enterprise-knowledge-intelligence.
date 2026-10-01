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

<!-- RESULTS -->

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
