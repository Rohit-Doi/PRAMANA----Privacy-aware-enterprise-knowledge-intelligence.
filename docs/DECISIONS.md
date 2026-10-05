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

Two consequences are enforced in code. *Previewing as another user* (the Compare page) is
allowed only when that user's access lies within the previewer's own, so it can never be
used to read more. And every policy change is an audit entry under the administrator's
name. What is **not** prevented: an administrator can raise a role's ceiling, including
their own, by publishing a new policy version. That is visible in the audit log; a
production deployment should require a second person to approve policy changes.

## 9. Egress ceiling with an extractive reader

Rather than trusting a third party with the most sensitive material, evidence above a
configurable sensitivity is never put in a prompt. The answer is the sentence a local
question-answering model points to, quoted verbatim. It is less fluent and cannot
synthesise across passages; it also cannot hallucinate or leak.

The first version ranked sentences by relevance and quoted the best ones. On the golden
questions that produced answers that were *related* rather than *right* (revenue when
asked for net income) and it could not decline: asked for the price of an acquisition
that does not exist, it quoted the price of the one that does. A SQuAD 2.0 reader is
trained on exactly that distinction. Measured as the only answering method on all 65
golden questions: 49 right, 1 wrong, 5 declined, and none of the 10 unanswerable
questions answered.

## 10. A cascade of small verifiers instead of one LLM judge

An LLM judging an LLM is slow, costs quota and shares the failure modes of the thing it
judges. Small specialised models are fast and independent, and literal checks catch
what models miss (a changed number). The LLM judge is used only for the uncertain band
and must quote its evidence verbatim.

## 11. A trained, constrained calibrator

Raw model scores are not probabilities and the detectors disagree. A logistic model
over seven signals, fitted on labelled data and scored on held-out documents, gives one
number with a measured error. Three choices were made from measurements
(`scripts/analyze_signals.py` reproduces them):

* **Weights keep their expected sign.** The first, unconstrained fit gave lexical
  overlap a *negative* weight: the injected test errors are near-copies of the source,
  so "almost word for word" had become a sign of error. That is a property of how the
  test errors were made, not of evidence, and would have penalised honest quoting.
* **A simple model.** Gradient boosting was no better on real LLM answers (RAGTruth)
  and clearly worse on HaluEval, the benchmark neither model was fitted on (33 % false
  alarms against 19 %). The simpler model generalises better and can be read.
* **A rule that was rejected.** "Nearly verbatim but not entailed" found 284 injected
  errors without a false alarm; on RAGTruth it found 5 real errors and raised 80 false
  alarms. It detects how the test errors were made, not errors.

The threshold is set so that the model alone raises at most 10 % false alarms on the
training half; the overriding rules (missing figure, stated opposite, no support at
all) apply on top. This is deliberately on the strict side: in the answer pipeline a
flagged sentence gets a second opinion from the LLM judge, and an unverified sentence
left out costs less than a wrong one left in.

## 12. Hash-chained audit log in Postgres

An audit log that the application can edit is not evidence. Entries are chained by
hash, and triggers reject modification. Verification needs only the table.

---

## 13. What four blind tests changed

Each blind test (see [BLIND_TEST.md](BLIND_TEST.md)) was followed by changes aimed at the
cause of a failure, never at a question. The ones that shaped the design:

* **A second search instead of a bigger first one.** Retrieving more passages did not
  help chained questions; the second passage is relevant to the first answer, not to
  the question. A deterministic check of what the draft lacks decides when to look again.
* **Arithmetic is allowed, but only shown and checked.** Forbidding calculation lost
  questions whose answer is a difference of two stated figures. The model must write
  the calculation out; the verifier recomputes it.
* **The organisation's name is a filter, not a search word.** Found on the third test:
  a rare name on every title page dominates both searches.
* **The relevance gate before drafting was removed.** A reworded question can score
  very low on the right passage. Whether passages answer is left to the drafting model
  and the verifier; the cost is one model call for questions that have no answer.
* **No quoting when no model is available.** With the gate gone, quoting the nearest
  sentence as a fallback produced answers that only sat near the topic. The system
  now says the model is unavailable.
* **Tried and kept out:** stronger rerankers (little gain on CPU), accepting a sentence
  because its words all occur in one table row (a swapped role would pass).

What the tests did not change: restricted text never goes to an external model. Without
a local model, questions about RESTRICTED documents are answered by quotation or
declined; this was the largest single group of declined questions in the fourth test.

## Departures from the plan

| Planned | Built | Reason |
|---|---|---|
| Taskiq + Redis for background jobs | Postgres queue with `FOR UPDATE SKIP LOCKED` | one less moving part; jobs are transactional with the documents they describe |
| Docling for parsing and OCR | PyMuPDF, python-docx, python-pptx, BeautifulSoup; RapidOCR (ONNX, about 15 MB) for scanned pages and images | lighter: the development machine (16 GB RAM, no GPU) was already at its limit with the verification models. OCR runs only on pages without a text layer |
| LangGraph Postgres checkpointer | none | a request completes in seconds; the run, its steps and claims are stored in normal tables |
| GLiNER2 extraction for every document | GLiNER2 for the company and uploaded collections; spaCy for the bulk corpus | 3–6 s per passage on CPU would have meant about twelve hours for the 9,800 passages |
| Strict/live answer mode toggle | answers are always delivered after verification | the safer default; streaming unverified text conflicts with the product's purpose |
| EU AI Act in the corpus | not included | the publisher's site blocked automated download; GDPR and the DPDP Act are included |
| Observability stack (OpenTelemetry, Grafana) | Prometheus metrics endpoint and a Monitoring page | the built-in page covers what a single-node deployment needs |
| Helm chart | Docker Compose `app` profile | no cluster to verify a chart against |
