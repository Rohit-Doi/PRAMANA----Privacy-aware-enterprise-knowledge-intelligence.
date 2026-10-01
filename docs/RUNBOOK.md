# Runbook

All commands are run from the repository root unless a directory is given.
Python commands use the backend environment: `backend/.venv/Scripts/python` on Windows,
`backend/.venv/bin/python` elsewhere (shown below as `python`).

## Requirements

| | |
|---|---|
| Docker Desktop | about 2 GB of RAM for the five datastores |
| Python 3.12 and [uv](https://docs.astral.sh/uv/) | backend |
| Node.js 20 or newer | web app |
| RAM | 8 GB free for the backend with all models loaded; 16 GB machine recommended |
| Disk | about 6 GB (models 2 GB, Python environment 2 GB, data and indexes) |
| API key | `GOOGLE_API_KEY` from Google AI Studio (free). Nothing else is required |

No GPU is needed.

## First-time setup

```bash
python scripts/init_env.py            # creates .env with generated secrets; add GOOGLE_API_KEY to it
docker compose up -d --wait           # Postgres, Redis, Qdrant, Neo4j, OPA

cd backend
uv sync                               # Python environment
uv run alembic upgrade head           # database schema
cd ..

python scripts/seed.py                # departments, roles, demo personas, first policy version
python scripts/download_models.py     # local models, about 2 GB, once

python scripts/fetch_data.py          # public corpora (handbook, regulations, contracts, emails, benchmarks)
python scripts/generate_synthetic.py  # the fictional company's confidential documents
python scripts/ingest_corpus.py       # parse, chunk, scan, embed, index, build graph (about 1–2 hours on CPU)

cd frontend && npm ci && cd ..
```

The demo personas share one password: `DEMO_PASSWORD` in `.env`.

## Start and stop

```bash
docker compose up -d --wait

# API (http://localhost:8000, interactive docs at /docs)
cd backend && uv run uvicorn pramana.api.app:app --host 127.0.0.1 --port 8000

# Web app (http://localhost:3000)
cd frontend && npm run build && npm run start
```

The API loads every model at start-up (about 40 seconds) so that the first question is
not slow. `/readyz` reports when every backing service answers.

Stop: `Ctrl+C` in both terminals, then `docker compose stop`. Data is kept in Docker
volumes; `docker compose down -v` deletes it.

### Everything in containers

```bash
docker compose --profile app run --rm setup
docker compose --profile app run --rm models
docker compose --profile app up -d
```

This needs about 7 GB of RAM for Docker.

## Train and evaluate

```bash
python scripts/train_calibrator.py                  # fit the confidence model, benchmark the verifier
python scripts/train_calibrator.py --reuse          # refit from stored signals (seconds)
python scripts/run_eval.py                          # retrieval quality and access control; no LLM calls
python scripts/run_eval.py --suites end_to_end      # whole pipeline on the golden questions; uses LLM quota
```

Stop the API before a long training run: both load the same models and they compete
for memory. Results appear on the **Reports** page and in `data/eval/results/`.
After training, restart the API so it loads the new `calibrator.json`.

## Checks

```bash
cd backend
uv run ruff check src tests ../scripts
uv run mypy
uv run pytest                                        # unit tests + security tests (need the datastores)
docker run --rm -v "$PWD/../policies:/policies:ro" openpolicyagent/opa:1.21.1 test /policies

cd ../frontend
npx tsc --noEmit && npx eslint src && npm run build
```

## Operations

| Task | How |
|---|---|
| Add documents | **Sources → Upload**, or `POST /api/v1/documents`. Ingestion runs in the background; progress is shown per stage |
| Re-run a failed ingestion | **Sources → open the document → Re-index** |
| Change who can read what | **Policies**: edit role ceilings or the egress ceiling, simulate for a user, publish. Each publish creates a new immutable version |
| Check the audit log has not been altered | **Audit → Verify chain**, or `POST /api/v1/audit/verify` |
| Review low-trust answers | **Reports → Review queue** |
| LLM quota exhausted | The gateway moves to the next model in `LLM_CHAIN` and cools the exhausted one down. Add `GROQ_API_KEY` or `CEREBRAS_API_KEY` for more capacity, or set `OLLAMA_BASE_URL` for a local model |
| Run ingestion separately from the API | set `EMBEDDED_WORKER=false` for the API and run `python -m pramana.worker` |
| Rotate secrets | change `JWT_SECRET` in `.env` and restart: all sessions end. `ENCRYPTION_KEY` protects MFA secrets; rotating it requires users to enrol again |

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| API exits with "secrets are not set" | run `python scripts/init_env.py` |
| `/readyz` shows `policy: unavailable` | OPA container is not running: `docker compose up -d opa`, then restart the API (it pushes the policy at start-up) |
| A model fails to load with an offline error | run `python scripts/download_models.py`; at run time nothing is downloaded |
| "No readable text was found in this PDF, even with OCR" | the scan is blank or too poor to read; rescan at 200 dpi or more |
| Answers say "the language model is unavailable" | every provider in the chain is out of quota or unreachable; see *LLM quota exhausted* |
| The machine swaps during ingestion or training | another process is holding the models too; stop the API, or stop other containers |
| Browser shows a network error on every call | the API is not running, or `FRONTEND_ORIGIN` in `.env` does not match the web app's address |

## Production notes

The defaults are for a single machine. Before exposing the system:

* set `APP_ENV=production` (secure cookies, no interactive docs) and serve both API and
  web app over HTTPS behind a reverse proxy on one site;
* remove the demo personas and `DEMO_PASSWORD`; require MFA for privileged roles;
* move secrets from `.env` to a secret manager;
* back up the Postgres volume (source of truth). Qdrant and Neo4j can be rebuilt from it
  by re-ingesting;
* replace the free-tier LLM key with a paid endpoint or a local model, and review the
  egress ceiling with whoever owns data protection.
