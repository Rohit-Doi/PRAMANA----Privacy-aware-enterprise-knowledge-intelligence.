# Blind test

The results in [RESULTS.md](RESULTS.md) come from questions written by the same process
that built the system. This document reports what happened on material the system had
never seen, with questions it was not built around. **The numbers here are much lower,
and they are the ones to believe.**

Both blind tests were run on 3 October 2026. Each is a first run: nothing was tuned on the
questions beforehand and nothing was re-run.

## Summary

| | Questions written with the system | Blind test 1 (university) | Blind test 2 (transit authority), after fixes | HotpotQA |
|---|---|---|---|---|
| Who wrote the questions | the builder | an independent examiner | a second independent examiner | people (public dataset) |
| Answerable questions answered correctly | 96 % (53 / 55) | **51 %** (37 / 72) | **72 %** (56 / 78) | 68 % (68 / 100) |
| Declined although answerable | 4 % | 36 % | 15 % | 22 % |
| Incomplete or from the wrong source | 0 % | 13 % | 13 % | 10 % |
| Invented facts found in the answers that were read | 0 | 0 | 0 | 0 |
| Protected facts leaked | 0 | **0** | **0** | — |
| Unanswerable questions declined | 10 / 10 | 9 / 10 | 10 / 10 | — |

**In one sentence:** on both blind tests the system leaked nothing, invented nothing and
resisted every attempt to talk it round. On the first it was far too cautious. After
the causes were fixed, a second blind test on new documents gave 72 % — plain facts and
tables 24 of 24 — with questions that chain two documents still the clear weakness.

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

## What was changed after the first test

Each change addresses a cause found above. They were developed against the first blind
set, which therefore stopped being blind; its re-run is a development check only.

| Cause found | Change |
|---|---|
| Table rows unusable by model, reader and verifier | A table is stored as one statement per row ("Office: Registrar; Holder: …"). Ruled tables in PDFs are read as tables |
| A third of the text of dense scanned pages was lost | Clean scans are read line by line: 99–100 % of the words recovered, up from about 65 % |
| Good evidence rejected before drafting | The relevance gate is now very low, every selected passage reaches the drafting model, and each search's first hits are guaranteed a place |
| The LLM judge overturned correct, well-grounded sentences | The judge may rescue a sentence the local checks rejected; it can no longer overturn one they accepted |
| No second lookup for "the role is named here, the person there" | The drafting model can name a missing piece (`LOOKUP: …`); one more search is made inside the same access scope and it drafts again |
| Quoting from restricted documents declined too easily | A lower-ranked passage is quoted when the local reader is clearly confident |
| A leak check that matched numbers inside identifiers | The check reads only text a caller can read, as whole tokens |

Development check on the first set after the changes: 50 of 72 answerable questions
correct (was 37), 14 declined (was 26). The original golden questions were unchanged
at 53 of 55.

## Part 3 — the second blind test

To see whether the changes help on material they were not developed against, the whole
exercise was repeated with a new independent author and a new independent examiner:
28 documents of a fictional public transport operator, the *Brennholt Regional Transit
Authority*, and 100 questions. Documents and questions were committed (`fc58520`)
before the system indexed or answered anything; the code was frozen (`ea6a3ec`) before
the run. First run, nothing re-run.

```bash
python scripts/build_blind_test.py --sets blind2
python scripts/ingest_corpus.py --collections blind2
python scripts/run_blind_test.py --set blind2
```

### What it must refuse — 22 questions

| Category | Questions | Refused or protected | Leaks |
|---|---|---|---|
| A protected fact asked by someone not allowed to read it (by level, by department, by region) | 8 | 8 | 0 |
| Attempts to talk the system into revealing restricted content | 2 | 2 | 0 |
| A personal ID or phone number asked by someone without that right | 2 | 2 | 0 |
| Questions the documents do not answer | 10 | 10 | — |

### What it must answer — 78 questions

| Category | Questions | Correct | Declined | Incomplete / wrong source |
|---|---|---|---|---|
| Plain fact | 14 | **14** | 0 | 0 |
| Reworded question | 16 | 12 | 4 | 0 |
| Table lookup | 10 | **10** | 0 | 0 |
| Needs two documents | 16 | 6 | 2 | **8** |
| Rule that changed between versions | 6 | 5 | 1 | 0 |
| Depends on the asker's division | 6 | 1 | 4 | 1 |
| Protected fact, asked by someone allowed | 8 | 7 | 1 | 0 |
| Personal data, asked by the executive | 2 | 1 | 0 | 1 |
| **Total** | **78** | **56** (72 %) | **12** (15 %) | **10** (13 %) |

55 by the strict key; one more on reading (the answer wrote "85 %" with a space where
the key had "85%").

### The two blind tests side by side

| | First blind test | Second blind test |
|---|---|---|
| Answerable questions answered correctly | 51 % (37 / 72) | **72 %** (56 / 78) |
| Declined although answerable | 36 % | **15 %** |
| Incomplete or from the wrong source | 13 % | 13 % |
| Plain facts | 11 / 14 | 14 / 14 |
| Reworded questions | 5 / 14 | 12 / 16 |
| Table lookups | 6 / 8 | 10 / 10 |
| Questions needing two documents | 1 / 14 | 6 / 16 |
| Unanswerable questions declined | 9 / 10 | 10 / 10 |
| Protected facts leaked | 0 | 0 |
| Median response time | 8.6 s | 14.3 s |

The two sets are different documents and different questions by different authors, so
this is not a controlled before-and-after. The size of the change (21 points on about
75 questions, with declines more than halved) is larger than the difference one would
expect between two sets of this kind by chance, but the second set may also simply be
somewhat easier or harder than the first.

### What still fails

* **Chained questions (8 incomplete, 2 declined of 16).** The look-up helps — 6 correct
  against 1 — but most still stop at the first step: "approval by the Director of
  Operations" without the name, or the two figures without their difference. Two of the
  eight needed arithmetic, which the system is told not to do.
* **"My division" (1 of 6).** The session knows the asker's region as `AMER`; the
  documents call it "West Division". Nothing connects the two, so the model declined
  four times and once answered from another organisation's leave policy. An
  organisation would need to configure what its regions are called.
* **Reworded questions (4 declined of 16).** The passage was not among those retrieved,
  or the model did not recognise it as the answer.
* **Quoting from restricted documents (3 of the incomplete answers).** The local reader
  quoted a nearby sentence — a header line, a general rule instead of the specific
  figure. It did not invent anything, but it did not answer.
* **ID numbers for the executive.** Identifiers are removed before text goes to the
  external model and cannot be restored afterwards, so even a reader entitled to see
  them gets the mask in an answer.
* **One question failed because the language model was unavailable** at that moment.
* **Slower.** More passages are read and some questions take a second search: the
  median rose to 14 s on this CPU.

## Limits of this test

* About 100 questions per test: one question is about one percentage point; small
  differences mean nothing.
* The documents of both fictional organisations were written by an AI author to a
  specification. They are unseen and realistic in form, but tidier than real paperwork,
  and the same specification shaped both sets.
* One examiner wrote the keys. Six were found too strict on reading; others may be too
  lenient — several keys are bare numbers that could match by accident.
* HotpotQA paragraphs are Wikipedia text that the drafting model may have seen in
  training. The system only accepts sentences its verifier can ground in the retrieved
  passages, but the model's prior knowledge may still help it draft.
