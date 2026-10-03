# Blind test

The results in [RESULTS.md](RESULTS.md) come from questions written by the same process
that built the system. This document reports what happened on material the system had
never seen, with questions it was not built around. **The numbers here are much lower,
and they are the ones to believe.**

Run on 3 October 2026. First run, nothing tuned, nothing re-run.

## Summary

| | Earlier golden set | Blind: institute | Blind: HotpotQA |
|---|---|---|---|
| Who wrote the questions | the builder | an independent examiner | people (public dataset) |
| Answerable questions answered correctly | 96 % (53 / 55) | **51 %** (37 / 72) | **68 %** (68 / 100) |
| Declined although answerable | 4 % | **36 %** | 22 % |
| Incomplete or from the wrong source | 0 % | 13 % | 10 % |
| Invented facts found in the answers that were read | 0 | 0 | 0 |
| Protected facts leaked | 0 | **0** | — |
| Unanswerable questions declined | 10 / 10 | 9 / 10 | — |

**In one sentence:** the system stayed safe — it leaked nothing, invented nothing and
resisted every attempt to talk it round — but it was far too cautious, declining a
third of the questions it should have answered, and it could rarely combine two
documents of the institute.

## How the test was made blind

1. **New documents.** A separate author, with no access to this repository's code,
   wrote 28 documents for a fictional university, the *Halvorsen Institute of
   Technology*: regulations, policies, minutes, budgets, a personnel register, e-mails.
   Every fact is invented, so no language model can know it. The domain differs from the
   demo company, and the files were converted to Word, PDF, slides and scanned PDFs
   (pictures of text that must be read by OCR).
2. **Independent questions.** A second examiner, who saw only the documents and the
   access rules — not the system — wrote 96 questions with answer keys in ten
   categories. A script checked that every key phrase occurs in its source document and
   that every access expectation follows from the access rule.
3. **Human-written multi-document questions.** 100 questions sampled at random from
   HotpotQA (the "hard" development set), each needing two Wikipedia paragraphs, with
   the 979 paragraphs offered alongside them — two useful and eight distracting per
   question.
4. **Frozen first.** Documents and questions were committed to git (`efa5349`) before
   the system answered any of them. What is reported is the first run.
5. **A mixed library.** The new documents were added to the existing 1,744, so the
   system had to find them among another organisation's policies.

Everything is in `eval/blind/`, including the full record of both runs — every question, the
reply, and the verdict — in `eval/blind/results/`. To repeat:

```bash
python scripts/build_blind_test.py --hotpot 100
python scripts/ingest_corpus.py --collections blind hotpot
python scripts/run_blind_test.py            # institute
python scripts/run_blind_test.py --hotpot   # HotpotQA
```

## Part 1 — the institute (96 questions)

### What it must refuse — 24 questions

| Category | Questions | Refused or protected | Leaks |
|---|---|---|---|
| A protected fact asked by someone not allowed to read it | 8 | 8 | 0 |
| Attempts to talk the system into revealing restricted content | 4 | 4 | 0 |
| A personal ID or phone number asked by someone without that right | 2 | 2 (one refused, one answered with the number masked) | 0 |
| Questions the documents do not answer | 10 | 9 | — |

The denied side included an administrator asking for executive pay and for a legal
file, two managers asking for each other's confidential documents, and two "own
department" traps (a document in the asker's department but above their level). All
were refused. The one miss among the unanswerable questions: asked for the *dates* of
drop-in clinics at one campus, it replied that staff there "will receive separate
invitations" — true, cited, and not an answer to the question.

### What it must answer — 72 questions

| Category | Questions | Correct (strict key) | Correct (after reading) | Declined | Incomplete / wrong source |
|---|---|---|---|---|---|
| Plain fact | 14 | 11 | 11 | 3 | 0 |
| Reworded question | 14 | 5 | 5 | **9** | 0 |
| Table lookup | 8 | 4 | 6 | 2 | 0 |
| Needs two documents | 14 | 1 | 1 | 7 | **6** |
| Rule that changed between versions | 6 | 1 | 5 | 1 | 0 |
| Depends on the asker's campus | 6 | 3 | 3 | 0 | 3 |
| Protected fact, asked by someone allowed | 8 | 6 | 6 | 2 | 0 |
| Personal data, asked by the executive | 2 | 0 | 0 | 2 | 0 |
| **Total** | **72** | **31** (43 %) | **37** (51 %) | **26** (36 %) | **9** (13 %) |

"After reading" corrects six verdicts where the answer was right and the mechanical key
was wrong: two answers were marked down because the *evidence shown beside them*
contained another row of the same table (a fault in the grading script, since fixed),
and four gave the current rule correctly and then added what the old rule had been,
which the key forbade. Every other verdict stands as graded.

### Why it declined 26 answerable questions

| Cause | Count | Example |
|---|---|---|
| The language model judged the evidence insufficient | 11 | *"Who is the Registrar?"* — the register of officers was the top result, but the answer sits in a table row and the model did not use it |
| Stopped before drafting: evidence scored too weak | 7 | *"I lost my room key — what will I be billed?"* The right handbook was found; the relevance threshold, tuned on easier questions, rejected it |
| The fact-checker removed a sentence that was correct | 5 | *"Founders' Day is on 14 November 2025"* — drafted correctly, then rejected against a calendar laid out as a list |
| Restricted document: the local reader was not confident enough to quote | 2 | the acquisition price in the board minutes |
| Nothing retrieved | 1 | a question about a scanned document, phrased very differently from it |

### The nine incomplete or misdirected answers

* **Six two-document questions** got the first half only. Asked *who, by name,* can
  evict a resident, it answered "the Chief Warden" — correct, but the name is in another
  document that was never retrieved, because nothing in the question points to it.
* **Three first-person questions** ("How many days of annual leave do I get at my
  campus?") were answered from the *other* organisation's leave policy, which is also in
  the library and also matches the asker's region. The answer was true to its source and
  cited; it was the wrong organisation. A library holding two organisations' policies is
  an artificial situation, but it shows that the system has no notion of which
  organisation the asker belongs to.

None of the nine contains a statement that is not in a document.

## Part 2 — HotpotQA (100 questions needing two paragraphs)

| | Count |
|---|---|
| Correct by automatic match (the reference answer appears in the reply) | 53 |
| Correct after reading (same answer in other words, or both facts stated for a yes/no question) | 15 |
| **Correct in total** | **68** |
| Reference answer disputed (the reply answers the question as written; the dataset's key names something else) | 1 |
| Incomplete — the first step only, usually followed by "not covered" | 9 |
| Declined | 22 |

Both needed paragraphs were among the passages used for 71 questions; at least one for
all 100. So retrieval found one side every time and both sides seven times in ten.
The 25 replies that could not be graded automatically were read one by one; the list
with each verdict is in the result file.

HotpotQA went much better than the institute's two-document questions (68 % against
1 of 14) for a reason worth knowing: a HotpotQA question usually names or describes
both things it needs, so both paragraphs can be found from the question. The institute
questions needed a hop the question does not mention — a policy names a *role*, and a
register elsewhere says who holds it. That second lookup is not something the system
does.

## What this says about the system

**Confirmed on unseen data**

* Access control: 0 leaks, including the administrator and same-department traps.
* Resistance to manipulation: 4 of 4.
* No invented fact in any answer that was read: all 59 replies that were wrong, incomplete
  or could not be graded automatically were read one by one. (Replies that matched their
  key were not all read.)
* Declining when the answer does not exist: 9 of 10.
* Version handling: the current rule was stated first in 5 of 6 cases.
* OCR, Word, PDF, slides, e-mail and HTML were all ingested and searchable.

**Not confirmed — the earlier figure was too optimistic**

* *"96 % of answerable questions correct"* does not survive. On unseen documents with
  independent questions it is about half.

**New weaknesses found**

1. **Too cautious.** A threshold set on easy questions rejects good evidence for
   reworded ones (7), and the fact-checker's false alarms turn a right answer into no
   answer (5).
2. **Tables.** Facts in table rows are hard for the drafting model to use and for the
   verifier to confirm; tables inside PDFs lose their structure when read.
3. **Chained lookups.** No second search is made for a name or figure that the first
   document only refers to.
4. **Quoting from restricted documents** declines too easily.
5. **No sense of "my organisation".**

## What to do about it

In order of expected gain. None of this has been done: the point of this document is
the unimproved result.

| Change | Addresses |
|---|---|
| Lower the pre-drafting relevance threshold and let the drafting model decide | 7 declines on reworded questions |
| When the fact-checker rejects every sentence, ask the LLM judge before giving up; treat table and list evidence as quotable | 5 wrongly rejected answers |
| Give tables to the model as rows with their headers; keep table structure when reading PDFs | table questions, the Registrar-type misses |
| A second retrieval step: if the draft names a role, committee or document it could not resolve, search for that and try once more | the two-document failures |
| Relax the local reader's confidence for restricted material, or quote the two best sentences | 2 declines |

After any of these, the same 96 questions are no longer a blind test. To confirm an
improvement honestly, a new set has to be written the same way.

## Limits of this test

* 96 and 100 questions: one question is about one percentage point; small differences
  mean nothing.
* The institute documents were written by an AI author to a specification. They are
  unseen and realistic in form, but tidier than real institutional paperwork.
* One examiner wrote the keys. Six were found too strict on reading; others may be too
  lenient — several keys are bare numbers that could match by accident.
* HotpotQA paragraphs are Wikipedia text that the drafting model may have seen in
  training. The system only accepts sentences its verifier can ground in the retrieved
  passages, but the model's prior knowledge may still help it draft.
