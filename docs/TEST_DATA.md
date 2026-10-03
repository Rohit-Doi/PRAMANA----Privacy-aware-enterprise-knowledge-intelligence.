# Testing with your own data

The demo tenant proves that the machinery works. It cannot tell you how the system
behaves on *your* material, because its questions were written by the same process that
built the system. This guide says what data tests which part, where to get it free,
and how to load it.

## The short answer

The most useful thing you can provide is small and you probably already have it:

1. **20–50 real documents from one organisation you know well** — your college, your
   team, a club, a company you have worked with. Policies, rules, handbooks, reports,
   minutes, circulars. PDF, Word, PowerPoint, text or scans.
2. **A label for each one**: which department owns it, and how sensitive it is.
3. **25–40 questions you know the answers to**, including some that the documents do
   *not* answer and some that only certain people should be able to answer.

That set tests retrieval, verification, access control and refusal at once, on text the
system has never seen, with answers only you can judge.

## What each kind of data tests

| You want to know | Data that shows it | Why the demo data is not enough |
|---|---|---|
| Does it find the right passage in *my* documents? | Your documents + your questions | The demo questions are single-fact and share words with the answer |
| Does it refuse when the answer is not there? | Questions about things your documents do not cover | Only 10 such questions in the demo |
| Does access control hold? | Two or more documents with different sensitivity or department, and a question whose answer is only in the protected one | The demo covers this well (149 attempts); your labels are the new part |
| Does it handle questions that need two documents? | Questions such as "which policy applies to the team that owns X?" | **Not measured in the demo at all** — this is the open question about the graph retriever |
| Does it cope with messy files? | Scans, photos of pages, slide decks, long PDFs with tables, e-mail threads | The demo documents are clean |
| Does the checker catch errors in other tools' output? | Paste answers from ChatGPT, Gemini or a colleague into Verify Studio, with the source document loaded | Benchmarks measure this in general, not on your subject |
| Does it mask personal data correctly? | Documents containing names, phone numbers, e-mail addresses, ID numbers (use invented ones) | Formats differ by country; Indian formats (Aadhaar, PAN) are covered but lightly tested |

## Where to get data

### 1. Your own organisation (best)

| What | Where it usually is |
|---|---|
| Academic regulations, syllabus, examination rules, fee and hostel rules, placement policy, anti-ragging and grievance policies | your college's website, usually under *Academics*, *Examination* or *Downloads*; the student handbook PDF |
| Department circulars, meeting minutes, timetables, lab manuals | department notice boards and shared drives |
| Club or project documents: constitution, budget, event reports | whoever keeps the club's drive |
| Anything from an internship or job | **only with permission**, and never real customer or employee data |

A natural labelling for a college: *Public* — prospectus and syllabus; *Internal* —
circulars and timetables; *Confidential* — minutes, budgets, evaluation rubrics;
*Restricted* — marks, disciplinary records (use invented ones).

### 2. Public documents that look like company documents

| Source | What it gives | Link |
|---|---|---|
| Basecamp Employee Handbook | a small, readable company handbook | https://github.com/basecamp/handbook |
| GitLab Handbook (already in the demo) | a very large one | https://handbook.gitlab.com |
| SEC EDGAR | annual reports (10-K) of any US-listed company: long, full of figures and tables | https://www.sec.gov/edgar/search/ |
| Reserve Bank of India | master directions and circulars | https://www.rbi.org.in |
| SEBI | regulations and circulars | https://www.sebi.gov.in |
| India Code | every central Act, as PDF | https://www.indiacode.nic.in |
| UGC, AICTE | regulations and approval handbooks for higher education | https://www.ugc.gov.in · https://www.aicte-india.org |
| NIST | security standards and guidelines | https://csrc.nist.gov/publications |
| EUR-Lex | EU law, including the AI Act that the demo is missing | https://eur-lex.europa.eu |

Annual reports are a good stress test: mix two companies' reports and ask for one
company's figure — the verifier should reject an answer that takes it from the other.

### 3. Datasets that come with questions and answers

These let you measure without writing questions yourself.

| Dataset | Tests | Link |
|---|---|---|
| **HotpotQA** | questions that need two passages — the unmeasured graph case | https://hotpotqa.github.io |
| **MultiHop-RAG** | multi-document questions over news articles, built for RAG | https://github.com/yixuantt/MultiHop-RAG |
| **FinanceBench** | questions over real company filings, with the evidence page | https://github.com/patronus-ai/financebench |
| **SQuAD 2.0** | answerable and deliberately unanswerable questions | https://rajpurkar.github.io/SQuAD-explorer/ |
| **CUAD** (already in the demo, questions unused) | 41 kinds of clause to find in contracts | https://www.atticusprojectai.org/cuad |
| **RAGBench** | RAG answers labelled for support, across several domains | https://huggingface.co/datasets/rungalileo/ragbench |
| **RAGTruth**, **HaluEval** (already used) | hallucination detection | https://github.com/ParticleMedia/RAGTruth · https://github.com/RUCAIBox/HaluEval |
| **FEVER**, **SciFact** | claims labelled supported / refuted against evidence — a direct test for Verify Studio | https://fever.ai · https://github.com/allenai/scifact |
| **ai4privacy pii-masking-200k** | text with labelled personal data | https://huggingface.co/datasets/ai4privacy/pii-masking-200k |
| **deepset prompt-injections** | injection attempts and harmless prompts | https://huggingface.co/datasets/deepset/prompt-injections |
| **FUNSD** | scanned forms, for OCR | https://guillaumejaume.github.io/FUNSD/ |

If you want only one: **HotpotQA** or **MultiHop-RAG**, because multi-document
questions are the one capability this project has built but not measured.

### 4. Data you make in five minutes

* **A scan test** — print any page, photograph it with your phone, upload the photo.
* **A version test** — take one policy, change three facts, save it as a new version
  and upload it as *superseding* the first. Ask about the changed facts.
* **An error test** — ask another AI tool a question about a document, then change one
  number and one name in its answer and paste the result into Verify Studio.
* **A personal-data test** — a staff list with invented names, phone numbers and ID
  numbers, uploaded as Confidential. Read it as an employee and as an executive.

## How to load it

### A few documents: the web app

**Sources → Upload.** Choose the department, sensitivity and region. The platform
suggests a sensitivity from the content; you decide. Progress is shown per stage.

### A folder: the manifest

Put the files anywhere under `data/custom/` and describe them in
`data/custom/manifest.jsonl`, one JSON object per line:

```json
{"path": "custom/exam-rules-2026.pdf", "title": "Examination Rules 2026", "department": "operations", "sensitivity": "INTERNAL", "region": "global", "collection": "custom", "effective_date": "2026-06-01", "version": "2"}
{"path": "custom/exam-rules-2024.pdf", "title": "Examination Rules 2024", "department": "operations", "sensitivity": "INTERNAL", "region": "global", "collection": "custom", "effective_date": "2024-06-01", "version": "1"}
{"path": "custom/budget-minutes.docx", "title": "Budget Committee Minutes", "department": "finance", "sensitivity": "CONFIDENTIAL", "region": "global", "collection": "custom"}
```

| Field | Values |
|---|---|
| `path` | relative to `data/` |
| `department` | `company`, `hr`, `security`, `finance`, `legal`, `engineering`, `it`, `marketing`, `sales`, `operations`, `compliance` |
| `sensitivity` | `PUBLIC`, `INTERNAL`, `CONFIDENTIAL`, `RESTRICTED` |
| `region` | `global`, `amer`, `emea`, `apac` |
| `collection` | any name; use `custom`. Add `"supersedes": "custom/older-file.pdf"` to mark a newer version |

Then, with the API stopped:

```bash
python scripts/ingest_corpus.py --collections custom
```

Documents in the `custom` collection are indexed for search but get the fast entity
extraction; upload through the web app if you want the fuller graph for a few of them.

### Your questions

One JSON object per line, in any file:

```json
{"id": "q01", "question": "How many attempts are allowed for a supplementary examination?", "expect": [["two attempts", "2 attempts"]]}
{"id": "q02", "question": "Who approves a budget above 5 lakh?", "expect": [["Principal"], ["Finance Committee"]]}
{"id": "q03", "question": "What is the hostel fee for international students?", "answerable": false}
```

`expect` is a list of groups; the answer is correct if it contains at least one phrase
from **every** group (q02 needs both "Principal" and "Finance Committee"; q01 needs
either wording). Mark questions your documents do not answer with `"answerable": false`.

```bash
python scripts/run_eval.py --suites retrieval end_to_end --golden my-questions.jsonl
```

The run asks every question as the executive persona (who can read everything), so it
measures retrieval and answering. To test access control on your documents, use
**Compare roles** in the web app, or sign in as different personas and ask.

## What to hand over if you want me to run it

A folder with the documents, the labels (a spreadsheet or the manifest above is fine),
and the question file. With that I can ingest it, run the evaluation, and report the
same tables as in [RESULTS.md](RESULTS.md) for your data — including the questions it
got wrong and why.

Do not include real personal data, real marks, or anything you are not allowed to
share. Invented values test the system just as well.
