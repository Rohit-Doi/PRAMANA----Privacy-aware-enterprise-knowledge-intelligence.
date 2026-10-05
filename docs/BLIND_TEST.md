# Blind test

The results in [RESULTS.md](RESULTS.md) come from questions written by the same process
that built the system. This document reports what happened on material the system had
never seen, with questions it was not built around. **The numbers here are much lower,
and they are the ones to believe.**

The first two blind tests were run on 3 October 2026, the third on 4 October and the fourth
on 5 October. Each
figure reported as blind is a first run: nothing was tuned on the questions beforehand.
Re-runs made after fixes are labelled as such and are not blind.

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

**In one sentence:** on all four blind tests the system leaked nothing, invented nothing
and resisted every attempt to talk it round. The share of answerable questions it answers
correctly on unseen documents went 51 % → 72 % → 68 % → **72 %** (76 % on reading), with fixes
between the tests: each fix worked for the failure it was made for, and each new test found
a different one. Expect roughly **70–75 %** on new material, with nearly all of the rest
declined rather than wrong. See [Part 4](#part-4--the-third-blind-test) and
[Part 5](#part-5--the-fourth-blind-test).

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
at 53 of 55 correct, though slower (median 8.6 s, was 6.1 s), and the share of answer
sentences marked plainly "supported" fell from 95 % to 91 %: with more passages in view,
more sentences are flagged as conflicting with some other document in the mixed library.

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
* **Quoting from restricted documents (4 of the incomplete answers).** The local reader
  quoted a nearby sentence — a header line, a general rule instead of the specific
  figure. It did not invent anything, but it did not answer.
* **ID numbers for the executive.** Identifiers are removed before text goes to the
  external model and cannot be restored afterwards, so even a reader entitled to see
  them gets the mask in an answer.
* **One question failed because the language model was unavailable** at that moment.
* **Slower.** More passages are read and some questions take a second search: the
  median rose to 14 s on this CPU.

## Part 4 — the third blind test

After the second test, each remaining failure was traced to a cause and fixed in a general
way (not for the particular question). The second set then became development data, and a
third organisation was written to test the fixes on material nobody had seen: *Marrowgate
Quill Mutual*, a fictional member-owned financial cooperative — 28 documents by an
independent author, 100 questions by an independent examiner, both committed to git
before the documents were indexed (`eval/blind3/`).

### What was changed before it

| Problem in test 2 | Change |
|---|---|
| Answers stop one step short ("approved by the Director of Operations") | A deterministic check of whether the draft contains the kind of thing asked for (a person, a date, an amount). If not, the offices, bodies and codes the draft names are looked up in one more search inside the same access scope, and the answer is drafted again. On the quoting route the local reader is asked "Who is the …?" and a second sentence is quoted. |
| "Do not compute" lost questions needing a difference or a rate | The model may calculate if it writes the calculation out (`$46,800 / 2,080 = $22.50`). The verifier checks the arithmetic exactly and that every operand is a figure in the passages. |
| A correct sentence resting on two passages was rejected | A claim one passage does not carry is read once more against that passage and its best complement together. |
| "My division" not connected to the session's region | A regional document's passages are labelled with the region they apply to. |
| ID numbers masked even for the executive | For a reader holding `pii:read` an identifier is replaced by a numbered placeholder; the value never leaves the machine and is put back locally. A claim that links placeholders found together in one row is checked literally. |
| "Conflicting" flags between different organisations' policies | Conflicts are reported only within one collection. |
| "Language model unavailable" | The gateway waits out a per-minute rate limit once instead of failing. |

On the second set's own questions (development data, **not blind**) this gave 67 of 78
answerable (86 %, from 72 %): chained questions 13 of 16 (from 6), region 4 of 6 (from 1),
identifiers 2 of 2; 22 of 22 refusals, no leaks. The original regression questions:
52 of 55, 10 of 10 unanswerable declined, no leaks.

### The blind result — 100 questions, one run

| | Result |
|---|---|
| Must refuse (22): access 8, personal data 2, injection 2, unanswerable 10 | **20 correct**; 0 leaks |
| Must answer (78) | **53 correct (68 %)**, 21 declined, 4 wrong or incomplete |

| Kind of answerable question | Correct | Test 2 (first run) |
|---|---|---|
| Fact stated plainly | 12 / 14 | 14 / 14 |
| Reworded in everyday language | 9 / 16 | 12 / 16 |
| A cell of a table | 8 / 10 | 10 / 10 |
| Needs two documents | 11 / 16 | 6 / 16 |
| Superseded and current version | 3 / 6 | 5 / 6 |
| "My area" (regional variants) | 4 / 6 | 1 / 6 |
| Confidential content, authorised reader | 6 / 8 | 7 / 8 |
| Personal data, entitled reader | 0 / 2 | 1 / 2 |

One answer was regraded by hand from wrong to correct: the model wrote "11 working days"
with a narrow no-break space, which the string comparison did not match. The grader now
treats all space characters alike.

**What the fixes did.** The two things test 2 was worst at improved on unseen material:
questions needing two documents 11 of 16 (6 before), regional questions 4 of 6 (1 before).

**What went wrong instead.** Plain and reworded questions fell (21 of 30, against 26 of 30).
Tracing the declined ones showed one cause for most of them: nearly every question
named the organisation ("…at Marrowgate Quill Mutual?"), a rare name that appears on the
title page of every one of its documents. Both searches matched the name, so the evidence
filled with title pages and the passage holding the answer was left out; the model then
correctly said the evidence was insufficient. In test 2 the questions mostly said "BRTA"
while the documents spelled the name out, so the effect did not show.

Other failures:

* **The two refusals that failed** were unanswerable questions put by the executive on
  the quoting route (restricted evidence, no local model): the reader quoted a sentence
  about the right topic that did not answer the question (the chief executive of this
  society for a question about another one). Nothing protected was disclosed, but it is
  a wrong answer.
* **Personal data for the executive (0 of 2).** Once the row was not retrieved (the cause
  above); once the reader quoted the neighbouring row of the register — another person's
  record. The reader was entitled to see it, but it was the wrong person's number.
* **One figure was misread by OCR** on a scanned page ("$1 65,000").
* **The primary language model's free daily quota was used up** before this run, so most
  answers were drafted by the weaker fallback models, and the median time per question
  rose to 20 s.

### Changes made after it (not confirmed blind)

* **The organisation's name is used as a filter, not as a search word.** A name found in
  most documents of the collection the search leads to says which documents are meant
  and nothing about which passage. It is taken out of the wording passages are ranked
  by, and the search is repeated within that collection.
* **A quoted row must be about the person the question names**; otherwise nothing is quoted.
* **A sentence that repeats a register row** nearly word for word may be put to the judge
  even when the local models find no support (they read prose, not rows).
* **When no language model is available the system declines** instead of quoting the
  nearest sentence.

Seven of the declined questions were traced again after these changes: five now answer
correctly. A full re-run was attempted, but the free quota of every configured provider
ran out part-way (20 of 100 questions found no model available), so **there is no valid
after-fix measurement of this set, and these changes have not been tested on unseen
material.** They pass the unit tests, the access-control suite (149 attempts, no leak) and
the retrieval check on the original questions (recall@8 1.00, MRR 0.975 — unchanged).

### What three tests say together

| | Test 1 | Test 2 | Test 3 |
|---|---|---|---|
| Answerable questions correct (first run) | 51 % | 72 % | 68 % |
| Must-refuse questions handled correctly | 23 / 24 | 22 / 22 | 20 / 22 |
| Protected facts leaked | 0 | 0 | 0 |
| Invented facts found | 0 | 0 | 0 |

* The safety properties held every time, including on questions designed to break them.
  They are enforced before retrieval and by literal checks, and do not depend on the
  language model.
* The answer rate on unseen material is about **70 %**, and has not been shown to be
  higher. A fix made for one test's failures worked for those failures; the next test
  found others. It is likely that a fourth test would find more.
* When the system does not answer correctly it mostly declines (21 of 25 in test 3)
  rather than answering wrongly. That is the designed behaviour, but a declined question
  is still a question the user did not get answered.
* No system of this kind can promise that failures will not occur on new documents.
  What can be promised is what was measured: it does not leak, and it prefers saying
  "I cannot verify this" to guessing.

## Part 5 — the fourth blind test

The changes made after the third test had no valid measurement (the free language-model
quota ran out during the re-run). They were measured the next day, and then tested on a
fourth unseen organisation.

**Checks on the earlier material first (development data, not blind):** the original
regression questions pass every gate on the final code — 52 of 55 correct, 10 of 10
unanswerable declined, no leaks, median 8.3 s. The third set re-run gave 55 of 78 (from 53)
with 21 of 22 refusals; tracing what still declined found two more causes, fixed before
the fourth test:

* **A name was a placeholder in the passage and plain text in the question.** Name
  recognition depends on context: the same name was recognised in a register row and
  missed in the question, so the model could not connect them. A value that has a
  placeholder is now replaced wherever it occurs, and names in the question are
  registered first.
* **The verifier rejected "staff in the EMEA region get 27 days"**: the drafting model
  was told the document's region and the verifier was not. Both now read the same header.

### The blind result — 100 questions, one run

*Ombrellick Growers' Cooperative Society* (a fictional farmers' cooperative): 28 documents
by an independent author, 100 questions by an independent examiner, committed to git
before indexing (`eval/blind4/`). No model outage affected the run.

| | Result |
|---|---|
| Must refuse (22) | **21 correct**; 0 leaks |
| Must answer (78), mechanical grading | **56 correct (72 %)**, 16 declined, 6 other |
| Must answer (78), on reading | **59 correct (76 %)** |

The three regraded answers gave the correct current figure and added what the older
version said ("…14.5 %. The 2025 schedule applied 12 %"). The examiner's key forbade the
old figure; the answer is right and says more than was asked. The one failed refusal
answered "the date of the 2027 meeting is to be announced in February" to a question
about that date — a true sentence from the calendar, not the date asked for.

| Kind of answerable question | Test 4 | Test 3 | Test 2 |
|---|---|---|---|
| Fact stated plainly | 14 / 14 | 12 / 14 | 14 / 14 |
| Reworded in everyday language | 14 / 16 | 9 / 16 | 12 / 16 |
| A cell of a table | 8 / 10 | 8 / 10 | 10 / 10 |
| Needs two documents | 10 / 16 | 11 / 16 | 6 / 16 |
| Superseded and current version | 3 / 6 (6 / 6 on reading) | 3 / 6 | 5 / 6 |
| "My area" (regional variants) | 1 / 6 | 4 / 6 | 1 / 6 |
| Confidential content, authorised reader | 5 / 8 | 6 / 8 | 7 / 8 |
| Personal data, entitled reader | 1 / 2 | 0 / 2 | 1 / 2 |

**Confirmed on unseen material:** the fix for the third test's main failure. Plain and
reworded questions: 28 of 30 (21 of 30 in test 3). Median time per question fell to 11 s.

**What failed this time**

* **"My area" (5 of 6 declined).** The organisation-name filter decided which
  organisation a question was about from the collection that dominated the first search
  hits. For "how many days of annual leave do I get" the hits were spread over every
  organisation's leave policy, no collection dominated, and the filter did not engage.
  *Fixed afterwards:* the organisation is the one in whose documents a name from the
  question is ubiquitous, whichever collection the hits favour. Re-run on this set (not
  blind): regional questions 5 of 6, total 59 of 78 mechanically, 62 on reading, refusals
  and leaks unchanged.
* **The executive's questions about RESTRICTED documents (7 declined).** This is a
  limit of the design, not a bug. RESTRICTED text may not be sent to an external model.
  Without a local model the system can only *quote* a sentence from it, and it declines
  when no single sentence answers. Installing a local model (Ollama; see the runbook)
  removes the limit; this machine has too little memory to run one alongside the rest.
* **Two-document questions (10 of 16).** Four declined, two stopped at the office or the
  meeting without the name or date.

### Four tests together

| | Test 1 | Test 2 | Test 3 | Test 4 |
|---|---|---|---|---|
| Answerable questions correct (first run) | 51 % | 72 % | 68 % | 72 % (76 % on reading) |
| Must-refuse questions handled correctly | 23 / 24 | 22 / 22 | 20 / 22 | 21 / 22 |
| Protected facts leaked | 0 | 0 | 0 | 0 |
| Invented facts found | 0 | 0 | 0 | 0 |

The pattern is consistent. Safety held in 400 questions on four organisations it had
never seen. The answer rate on new material is in the low seventies and has moved little
since the second test, although every specific failure found was fixed and the fix then
confirmed: a new set of documents finds a new way to miss. Further gains now depend more
on the parts that were held constant — a small reranker and embedding model chosen to run
without a GPU, a free-tier drafting model, and no local model for restricted text — than
on more rules.

## Limits of this test

* About 100 questions per test: one question is about one percentage point; small
  differences mean nothing. The difference between 72 % and 68 % is within that noise.
* The tests ran on a free language-model tier. Which model drafted an answer depended on
  how much quota was left that day; test 3 ran mostly on the fallback models.
* The documents of the four fictional organisations were written by an AI author to a
  specification. They are unseen and realistic in form, but tidier than real paperwork,
  and similar specifications shaped all four sets.
* One examiner wrote the keys. Six were found too strict on reading; others may be too
  lenient — several keys are bare numbers that could match by accident.
* HotpotQA paragraphs are Wikipedia text that the drafting model may have seen in
  training. The system only accepts sentences its verifier can ground in the retrieved
  passages, but the model's prior knowledge may still help it draft.
