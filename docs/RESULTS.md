# Results

Everything here was measured on this repository's code, on one laptop
(Intel i7-1360P, 16 GB RAM, no GPU, Windows 11), on 2 October 2026.
Each run is stored in the database, shown on the **Reports** page of the web app, and
can be repeated with the command given in its section.

> These are results on questions written alongside the system. On unseen documents with
> independent questions the answer rate is about half of what is shown here; safety
> results held. See [BLIND_TEST.md](BLIND_TEST.md).

**Summary**

| Question | Answer |
|---|---|
| Does it answer correctly? | 53 of 55 answerable questions right, none wrong, 2 declined |
| Does it decline when it should? | 10 of 10 unanswerable questions declined |
| Does it leak restricted material? | 0 leaks in 149 unauthorised attempts across 7 roles, and 0 through the full pipeline |
| Does it find the right passage? | In the top 8 for 100 % of questions; first for 96 % |
| Does the verifier catch errors? | 77.5 % of real LLM errors, 95.2 % of injected errors — against 18 % and 46 % for version 1 |
| How fast? | Median 6.1 s per question on CPU |
| What is not good yet? | 14 % false alarms on real LLM answers; a swapped name is caught 84 % of the time; multi-document questions are not measured |

---

## 1. The whole pipeline

**What was run.** 65 hand-written questions about the demo company's 19 documents,
asked against the full corpus of 1,744 documents (9,773 passages), through all seven
agents with the live LLM. 55 have an answer in the documents; 10 do not (for example
*"What is the purchase price of the Corvane Logistics acquisition?"* — there is a
Corvane lawsuit and a different acquisition, but no such deal). An answer is correct
if it contains the expected fact.

```bash
python scripts/run_eval.py --suites end_to_end
```

| Measure | Result | Target | |
|---|---|---|---|
| Answerable questions answered correctly | 53 / 55 = 96.4 % | ≥ 85 % | met |
| — answered wrongly | 0 | | |
| — declined although answerable | 2 | | |
| Unanswerable questions declined | 10 / 10 | ≥ 80 % | met |
| Sentences in final answers verified as supported | 95.1 % | ≥ 95 % | met |
| Restricted facts reaching a role without access (12 checks) | 0 | 0 | met |
| Median response time | 6.1 s | ≤ 10 s | met |
| 95th-percentile response time | 13.7 s | | |
| Mean trust score of answers | 92.2 / 100 | | |
| LLM calls | 50 for 77 runs | ≤ 4 per question | met |
| Sentences removed by the verifier before the answer was shown | 2 | | |

**How the answers were produced.** 38 by the language model on evidence it was allowed
to see; 17 by quoting, on this machine, because the evidence was above the egress
ceiling.

**The two that were declined.** *"What is the internal codename for the Brightwater
transaction?"* and *"What was the root cause of incident INC-2026-0417?"* Both answers
are in RESTRICTED documents, so only quoting was possible; the local reader was not
confident that a single sentence answered, and the system declined rather than guess.

### How this changed during development

The first end-to-end run exposed the weakest part, and it was replaced.

| | First run | Final |
|---|---|---|
| Correct | 51 / 55 | 53 / 55 |
| Wrong answers | 4 | 0 |
| Unanswerable questions declined | 7 / 10 | 10 / 10 |

In the first run, answers from restricted documents were built by ranking sentences by
relevance and quoting the best. That gave answers that were related rather than right
(revenue when asked for net income) and it could not decline (asked for the Corvane
price, it quoted the Brightwater price). It was replaced by a reading model trained to
find an answer *or report that there is none*.

---

## 2. Access control

**What was run.** 28 unique facts ("canaries") were planted in protected documents when
the demo data was generated. Each canary's question is asked as each of 7 roles, at the
retrieval layer. A *leak* is the canary value appearing in any passage returned to a
role whose scope does not include the document.

```bash
python scripts/run_eval.py --suites access
```

| Role | Should not see | Leaks | Should see | Found |
|---|---|---|---|---|
| Employee | 28 | 0 | 0 | — |
| Manager (HR) | 25 | 0 | 3 | 3 |
| Manager (Finance) | 26 | 0 | 2 | 2 |
| Analyst | 21 | 0 | 7 | 7 |
| Compliance officer | 21 | 0 | 7 | 7 |
| Executive | 0 | — | 28 | 28 |
| Administrator | 28 | 0 | 0 | — |
| **Total** | **149** | **0** | **47** | **47** |

The administrator row matters: administrators manage users and policy but read only
what an employee reads.

In addition, the test suite checks that the four statements of the access rule — the
Python reference, the Qdrant filter, the Neo4j predicate and the Rego policy — agree
for every combination of role, department, sensitivity and region, and that "preview
as another user" can never widen access (checked exhaustively).

---

## 3. Retrieval

**What was run.** The 55 answerable questions, searched across all 9,773 passages.
A hit is a returned passage containing the expected answer.

```bash
python scripts/run_eval.py --suites retrieval
```

| Configuration | Answer first | In top 3 | In top 8 | MRR | Median time |
|---|---|---|---|---|---|
| Meaning (dense vectors) only | 87.3 % | 92.7 % | 92.7 % | 0.900 | 55 ms |
| Keywords (BM25) only | 96.4 % | 98.2 % | 100 % | 0.975 | 51 ms |
| Meaning + keywords | 90.9 % | 96.4 % | 100 % | 0.944 | 58 ms |
| Meaning + keywords + graph | 67.3 % | 92.7 % | 100 % | 0.805 | 216 ms |
| **Full pipeline (reranked)** | **96.4 %** | **98.2 %** | **100 %** | **0.977** | 2.5 s |

Targets: top-8 ≥ 90 % (met), MRR ≥ 0.70 (met).

**Reading this honestly.**

* Keywords alone do as well as the full pipeline here. These questions name the thing
  they ask about; meaning-based search earns its place on questions phrased differently
  from the document, which this set has few of.
* Adding the graph *lowers* the order before reranking, and reranking repairs it. The
  graph retriever is built for questions that join two documents through an entity.
  This set has none, so its value is **not demonstrated** by these results.
  [TEST_DATA.md](TEST_DATA.md) names two public datasets that would test it.
* Reranking costs about 2.4 s of the response time on this CPU.

---

## 4. The verifier

The verifier decides, for one sentence and its evidence, *supported or not*. It is
tested apart from the rest because it is the part version 1 faked.

**Three test sets**, all held out — the model that turns signals into a confidence was
fitted on different documents:

| Set | What it is | Size (held out) |
|---|---|---|
| **RAGTruth** | Real answers by LLMs to real questions; people marked the unsupported spans | 846 sentences from 141 answers |
| **Injected** | Sentences from this corpus, unchanged or with one known error: a changed number, a flipped meaning, a swapped name or entity, or a true sentence from another document | 485 sentences |
| **HaluEval** | Question–answer pairs labelled correct or hallucinated. Never used for fitting | 245 answers |

**Three methods on the same sentences:**

* **v1** — the word-overlap formula of the first version of this project
* **single model** — one grounding model with a fixed threshold
* **v2** — the full cascade: grounding, two entailment readings, unsupported spans,
  number and name checks, a trained calibrator, and three overriding rules

```bash
python scripts/train_calibrator.py           # about 100 minutes on this laptop
python scripts/train_calibrator.py --reuse   # refit and re-score from stored signals, seconds
```

![Errors caught by each method on the three benchmarks](images/verifier-benchmarks.svg)

### Sentence level

| Set | Method | Errors caught | False alarms | Precision | F1 | AUROC |
|---|---|---|---|---|---|---|
| RAGTruth | v1 | 18.0 % | 8.2 % | 0.205 | 0.192 | 0.726 |
| | single model | 58.4 % | 14.4 % | 0.323 | 0.416 | 0.817 |
| | **v2** | **77.5 %** | 14.0 % | **0.394** | **0.523** | **0.916** |
| Injected | v1 | 46.3 % | 0.0 % | 1.000 | 0.633 | 0.772 |
| | single model | 76.0 % | 0.0 % | 1.000 | 0.864 | 0.952 |
| | **v2** | **95.2 %** | 0.0 % | 1.000 | **0.976** | **0.994** |
| HaluEval | v1 | 10.7 % | 3.2 % | 0.765 | 0.188 | 0.802 |
| | single model | 60.3 % | 24.2 % | 0.709 | 0.652 | 0.707 |
| | **v2** | **70.3 %** | 18.5 % | **0.787** | **0.742** | 0.765 |

*Errors caught*: of the sentences that really are unsupported, the share flagged.
*False alarms*: of the sentences that are fine, the share flagged anyway.
*AUROC*: how well the confidence value ranks bad sentences below good ones, whatever
threshold is used (0.5 is chance, 1.0 is perfect).

### By kind of injected error

![Errors caught by kind of error, v1 against v2](images/verifier-by-error-type.svg)

| Kind of error | Example | Sentences | v1 | single model | **v2** |
|---|---|---|---|---|---|
| Changed number | "within 30 days" → "within 60 days" | 33 | 0 % | 87.9 % | **100 %** |
| Flipped meaning | "is prohibited" → "is permitted" | 60 | 0 % | 36.7 % | **93.3 %** |
| Swapped name or entity | "Finance department" → "Legal department" | 50 | 0 % | 36.0 % | **84.0 %** |
| Unrelated true statement | a sentence from another document | 170 | 85.3 % | 99.4 % | **98.2 %** |

Version 1 scored a sentence by how many of its words appear in the evidence. A sentence
with one number changed shares almost every word with the source, so v1 passed it every
time.

### Answer level on RAGTruth

Flag the whole answer if any sentence is flagged: v2 catches 81.8 % of answers that
contain an error, with 44.4 % false alarms, F1 0.50. **The target was 0.70 and is not
met.** The single grounding model reaches 0.58 here, by flagging more of everything.
Small CPU models do not reach 0.70 on this benchmark at answer level; the target was
set before measuring and is left as it was.

### How well the confidence means what it says

Calibration error (the average gap between the stated confidence and the observed
share of supported sentences): 0.055 on RAGTruth, 0.096 on injected errors, 0.166 on
HaluEval. Target ≤ 0.10: met on the first two, not on HaluEval, whose one-word answers
are unlike anything the calibrator was fitted on.

### The trained model

Fitted on 1,427 sentences (912 RAGTruth, 515 injected) from documents that do not
appear in the test sets. Threshold 0.74.

| Signal | Weight | Reading |
|---|---|---|
| Grounding (HHEM) | +2.18 | the strongest single positive signal |
| Entailment, window | +1.15 | the passage around the best sentence entails the claim |
| Entailment, closest sentence | +1.01 | the single best sentence entails it |
| Contradiction, window | −1.19 | |
| Contradiction, closest sentence | −2.78 | a near-copy with one word changed shows up here |
| Share of the sentence marked unsupported | −3.57 | from the span detector |
| Names all found in the source | +0.45 | |

No single signal does the job alone: across the held-out data the best one reaches an
AUROC of 0.86 and the weakest 0.58. What was tried and rejected, with the measurements, is in
[DECISIONS.md](DECISIONS.md) §11 and reproduced by `python scripts/analyze_signals.py`.

### How the verifier changed during development

| | First trained version | Final |
|---|---|---|
| RAGTruth: errors caught / false alarms | 69.7 % / 10.3 % | 77.5 % / 14.0 % |
| RAGTruth AUROC | 0.890 | 0.916 |
| Injected: errors caught | 88.2 % | 95.2 % |
| — swapped name or entity | 46 % | 84 % |
| HaluEval F1 | 0.693 | 0.742 |

The gains came from reading each sentence a second time against its single closest
source sentence, from judging one-word answers together with their question, and from
a rule that a sentence no detector finds any support for is never called supported.
The final setting is deliberately stricter: more errors caught, at the price of more
false alarms.

---

## 5. Software quality

| Check | Result |
|---|---|
| Automated tests | 140 pass: 100 unit, 40 against the real datastores (authentication, access control, every read endpoint as every role) |
| Access policy tests (OPA) | 26 pass |
| Lint (ruff), formatting | clean |
| Type check (mypy, strict mode) | clean, 97 files |
| Frontend type check, lint, production build | clean, 14 routes |
| Container images | both build; web image serves pages; backend image imports the application |

Defects found by the tests and evaluations and fixed along the way include: citations
credited to the wrong sentence; e-mail addresses on internal domains and written phone
numbers not recognised as personal data; an administrator able to preview answers at an
executive's access level; a superseded policy shown as the source when the current one
also matched.

---

## 6. What these results do not show

* **Your documents.** The questions were written for the synthetic company by the same
  process that built the system, and most are single-fact questions that share words
  with their answer. Expect lower numbers on real material. [TEST_DATA.md](TEST_DATA.md)
  explains how to measure it.
* **Multi-document reasoning.** Not measured.
* **Scale and load.** One user at a time, 1,744 documents. Not load-tested.
* **Other languages.** The local models are English models.
* **Messy scans.** OCR was tested on clean printed pages only.
* **The LLM's limits.** The free tier's quota was not found; the longest run made 50
  calls in about ten minutes without being throttled.
* **Statistical margins.** With 55 questions, one question is 1.8 percentage points.
  The difference between 96 % and 93 % is two questions.
