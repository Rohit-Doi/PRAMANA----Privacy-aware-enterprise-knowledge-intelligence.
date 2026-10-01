# Decisions

Each entry records what was chosen, what it replaced or was weighed against, and why.
Where the build departed from the original plan, that is stated.

## 1. Rebuild instead of repairing version 1

Version 1 looked like the architecture diagram but did not implement it: the vector
search returned a hard-coded passage, the knowledge graph was a fixed list of triples,
the "verifier" scored word overlap, the privacy gate was a keyword list, the user's
clearance came from the request body, and the evaluation printed fixed numbers.
There was nothing to build on, so the idea was kept and the implementation replaced.

## 2. One service with modules, not microservices

Seven "agents" suggest seven services. For one team and one machine that adds network
hops, deployment work and failure modes without adding capability. The backend is a
single FastAPI service whose modules have explicit boundaries; a module can be split
out later because nothing reaches into another module's tables.

## 3. LangGraph for orchestration

The pipeline has parallel branches (retrieval and graph reasoning), a bounded loop
(rewrite after failed verification) and a rule that every path ends in governance.
A state machine makes these explicit and testable. The agents are deterministic
functions with a fixed order — not free-running LLM agents — so behaviour is repeatable
and the cost per question is bounded (at most four LLM calls).

## 4. Qdrant for vectors, Neo4j for the graph

Qdrant holds dense and sparse vectors in one collection and filters on payload inside
the search, which is what scope-based access control needs. Neo4j Community gives real
traversals with a predicate on every hop. Both are free and run in a container.

## 5. Local models for everything but drafting

Embedding, keyword vectors, reranking, entity extraction, grounding, NLI, span detection
and personal-data detection all run on CPU. Consequences: no per-request cost, no
document text leaves the machine for these steps, and results are reproducible.
Model files are downloaded once; at run time the process is offline to the model hub
and the one model that ships its own code is pinned to an exact revision.

## 6. One required API key

Version 1 asked for five to seven keys and used one. Version 2 requires only
`GOOGLE_API_KEY` (free tier). Groq and Cerebras keys are optional extra capacity; a
local Ollama endpoint is optional. Providers are called over plain HTTP in the
OpenAI-compatible format, so no vendor SDK is installed.

## 7. Policy as code in OPA, compiled to query filters

A per-document permission check cannot be applied inside a vector search. The policy
therefore returns a scope, which is translated into a Qdrant filter, a Cypher predicate
and a SQL predicate. A second, independent check runs after retrieval. Tests assert
that all four formulations agree.

## 8. Administrators do not get content access

Managing users and policy is separated from reading restricted documents. The admin
role reads what an ordinary employee reads. This is deliberate separation of duties.

## 9. Egress ceiling with an extractive fallback

Rather than trusting a third party with the most sensitive material, evidence above a
configurable sensitivity is never put in a prompt. The answer is assembled from quoted
sentences selected by the local reranker. It is less fluent and cannot synthesise
across passages; it also cannot hallucinate or leak.

## 10. A cascade of small verifiers instead of one LLM judge

An LLM judging an LLM is slow, costs quota and shares the failure modes of the thing it
judges. Small specialised models are fast and independent, and literal checks catch
what models miss (a changed number). The LLM judge is used only for the uncertain band
and must quote its evidence verbatim.

## 11. A trained calibrator

Raw model scores are not probabilities and the detectors disagree. A logistic model
over seven signals, fitted on labelled data and scored on held-out documents, gives one
number with a measured error. The decision threshold is chosen for the highest catch
rate under a 10 % false-alarm limit.

## 12. Hash-chained audit log in Postgres

An audit log that the application can edit is not evidence. Entries are chained by
hash, and triggers reject modification. Verification needs only the table.

---

## Departures from the plan

| Planned | Built | Reason |
|---|---|---|
| Taskiq + Redis for background jobs | Postgres queue with `FOR UPDATE SKIP LOCKED` | one less moving part; jobs are transactional with the documents they describe |
| Docling for parsing, with OCR | PyMuPDF, python-docx, python-pptx, BeautifulSoup; **no OCR** | lighter: the development machine (16 GB RAM, no GPU) was already at its limit with the verification models, so Docling was not attempted. Scanned PDFs are rejected with a clear message rather than ingested empty |
| LangGraph Postgres checkpointer | none | a request completes in seconds; the run, its steps and claims are stored in normal tables |
| GLiNER2 extraction for every document | GLiNER2 for the company and uploaded collections; spaCy for the bulk corpus | 3–6 s per passage on CPU would have meant about twelve hours for the 9,800 passages |
| Strict/live answer mode toggle | answers are always delivered after verification | the safer default; streaming unverified text conflicts with the product's purpose |
| EU AI Act in the corpus | not included | the publisher's site blocked automated download; GDPR and the DPDP Act are included |
| Observability stack (OpenTelemetry, Grafana) | Prometheus metrics endpoint and a Monitoring page | the built-in page covers what a single-node deployment needs |
| Helm chart | Docker Compose `app` profile | no cluster to verify a chart against |
