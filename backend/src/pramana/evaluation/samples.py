"""Labelled samples for the verifier evaluations, from three sources:

* RAGTruth   human-annotated hallucination spans in real LLM answers (public benchmark)
* HaluEval   question-answer pairs with a hallucinated/faithful label (public benchmark)
* injected   sentences from this corpus, kept as they are or corrupted in a known way
             (changed number, flipped meaning, swapped entity, statement from elsewhere)
"""

from __future__ import annotations

import hashlib
import json
import random
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from pramana.knowledge.chunking import chunk_document
from pramana.knowledge.parsers import Block, ParsedDocument
from pramana.shared.text import split_sentences


@dataclass(slots=True)
class EvidenceItem:
    """Minimal evidence object accepted by the validation engine."""

    ref: str
    text: str
    document_id: uuid.UUID
    title: str = ""
    section_path: str = ""
    page: int | None = None
    is_current: bool = True
    sensitivity_rank: int = 0


@dataclass(slots=True)
class VerifierSample:
    id: str
    source: str
    group: str  # samples with the same group share source material; they are never split across train/test
    question: str
    response: str
    evidence: list[EvidenceItem]
    hallucinated_spans: list[tuple[int, int]] = field(default_factory=list)
    response_hallucinated: bool | None = None
    error_type: str = ""

    def sentence_is_hallucinated(self, start: int, end: int) -> bool:
        return any(start < span_end and span_start < end for span_start, span_end in self.hallucinated_spans)


def _evidence_from_text(text: str, seed: str) -> list[EvidenceItem]:
    parsed = ParsedDocument(title="", blocks=[Block("text", p) for p in re.split(r"\n\s*\n", text) if p.strip()])
    _, chunks = chunk_document(parsed, target_tokens=220, max_tokens=320, min_tokens=20)
    document_id = uuid.uuid5(uuid.NAMESPACE_URL, seed)
    return [EvidenceItem(f"E{i}", c.text, document_id) for i, c in enumerate(chunks[:12], start=1)]


def _stable_shuffle(items: list, seed: int) -> list:  # type: ignore[type-arg]
    shuffled = list(items)
    random.Random(seed).shuffle(shuffled)  # noqa: S311 - reproducible sampling, not security
    return shuffled


def load_ragtruth(path: Path, *, task_types: tuple[str, ...] = ("QA", "Summary"), limit: int | None = None) -> list[VerifierSample]:
    samples: list[VerifierSample] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if row["task_type"] not in task_types or row.get("quality") != "good":
            continue
        labels = json.loads(row["hallucination_labels"]) if isinstance(row["hallucination_labels"], str) else row["hallucination_labels"]
        group = hashlib.sha256((row["query"] + row["context"]).encode()).hexdigest()[:16]
        samples.append(
            VerifierSample(
                id=f"ragtruth-{row['id']}", source=f"ragtruth-{row['task_type'].lower()}", group=group,
                question=row["query"], response=row["output"],
                evidence=_evidence_from_text(row["context"], group),
                hallucinated_spans=[(int(label["start"]), int(label["end"])) for label in labels],
                response_hallucinated=bool(labels),
            )
        )
    samples = _stable_shuffle(samples, 7)
    return samples[:limit] if limit else samples


def load_halueval(path: Path, *, limit: int | None = None) -> list[VerifierSample]:
    samples: list[VerifierSample] = []
    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
        row = json.loads(line)
        group = hashlib.sha256(row["knowledge"].encode()).hexdigest()[:16]
        samples.append(
            VerifierSample(
                id=f"halueval-{index}", source="halueval-qa", group=group, question=row["question"],
                response=row["answer"],
                evidence=[EvidenceItem("E1", row["knowledge"], uuid.uuid5(uuid.NAMESPACE_URL, group))],
                response_hallucinated=str(row["hallucination"]).strip().lower() == "yes",
            )
        )
    samples = _stable_shuffle(samples, 11)
    return samples[:limit] if limit else samples


# ── Injected errors ─────────────────────────────────────────────────────────
_NUMBER = re.compile(r"(?<![\w.])\d+(?:[.,]\d+)?")
_FLIPS = (
    (r"\bmust not\b", "must"), (r"\bmay not\b", "may"), (r"\bis mandatory\b", "is optional"),
    (r"\bare required\b", "are not required"), (r"\bis required\b", "is not required"),
    (r"\bis prohibited\b", "is permitted"), (r"\bare prohibited\b", "are permitted"),
    (r"\bmust\b", "need not"), (r"\brequires?\b", "does not require"), (r"\bmay\b", "may not"),
    (r"\bis not\b", "is"), (r"\bare not\b", "are"), (r"\bcannot\b", "can"), (r"\bwill\b", "will not"),
    (r"\bshould\b", "should not"), (r"\bbefore\b", "after"), (r"\bat least\b", "at most"),
    (r"\bup to\b", "at least"), (r"\bmore than\b", "less than"), (r"\ball\b", "no"),
)
_ENTITY_SWAPS = (
    ("Chief Financial Officer", "Chief Executive Officer"), ("Chief Executive Officer", "Chief People Officer"),
    ("Chief Information Security Officer", "Chief Financial Officer"), ("department head", "team lead"),
    ("Security department", "Finance department"), ("Finance department", "Legal department"),
    ("People department", "Security department"), ("Legal department", "People department"),
    ("EMEA", "APAC"), ("APAC", "AMER"), ("AMER", "EMEA"), ("manager", "director"),
    ("employees", "contractors"), ("Employees", "Contractors"), ("customers", "vendors"),
    ("weekly", "monthly"), ("monthly", "quarterly"), ("annual", "monthly"), ("GitLab", "GitHub"),
    ("team members", "contractors"), ("Team members", "Contractors"),
)


def _corrupt_number(sentence: str, rng: random.Random) -> str | None:
    matches = [m for m in _NUMBER.finditer(sentence) if not re.fullmatch(r"(?:19|20)\d{2}", m.group(0))]
    if not matches:
        return None
    match = rng.choice(matches)
    raw = match.group(0)
    try:
        value = float(raw.replace(",", ""))
    except ValueError:
        return None
    changed = value * rng.choice((2, 3, 0.5)) if value >= 10 else value + rng.choice((2, 3, 5))
    text = f"{changed:,.1f}".rstrip("0").rstrip(".") if "." in raw else f"{round(changed):,}" if "," in raw else str(round(changed))
    if text == raw:
        return None
    return sentence[: match.start()] + text + sentence[match.end() :]


def _corrupt_meaning(sentence: str, rng: random.Random) -> str | None:
    options = [(pattern, replacement) for pattern, replacement in _FLIPS if re.search(pattern, sentence)]
    if not options:
        return None
    pattern, replacement = options[0]
    return re.sub(pattern, replacement, sentence, count=1)


def _corrupt_entity(sentence: str, rng: random.Random) -> str | None:
    options = [(old, new) for old, new in _ENTITY_SWAPS if re.search(rf"\b{re.escape(old)}\b", sentence)]
    if not options:
        return None
    old, new = rng.choice(options)
    return re.sub(rf"\b{re.escape(old)}\b", new, sentence, count=1)


def build_injected_samples(chunks: list[tuple[str, str, str]], *, seed: int = 13) -> list[VerifierSample]:
    """``chunks`` is a list of (chunk id, document id, text).

    For each usable sentence: the sentence itself (supported), and up to one corruption of each
    kind (not supported). A corruption that changes nothing is discarded.
    """
    rng = random.Random(seed)  # noqa: S311 - reproducible fixtures, not security
    usable: list[tuple[str, str, str, str]] = []
    for chunk_id, document_id, text in chunks:
        for start, end in split_sentences(text):
            sentence = text[start:end].strip()
            words = sentence.split()
            if 8 <= len(words) <= 40 and sentence.endswith(".") and sentence[0].isupper() and "|" not in sentence:
                usable.append((chunk_id, document_id, text, sentence))
    samples: list[VerifierSample] = []

    def add(index: int, kind: str, claim: str, hallucinated: bool, document_id: str, text: str) -> None:
        samples.append(
            VerifierSample(
                id=f"injected-{index}-{kind}", source="injected", group=document_id, question="",
                response=claim, evidence=[EvidenceItem("E1", text, uuid.UUID(document_id))],
                hallucinated_spans=[(0, len(claim))] if hallucinated else [],
                response_hallucinated=hallucinated, error_type=kind,
            )
        )

    for index, (_, document_id, text, sentence) in enumerate(usable):
        add(index, "original", sentence, False, document_id, text)
        for kind, corrupt in (("number", _corrupt_number), ("meaning", _corrupt_meaning), ("entity", _corrupt_entity)):
            changed = corrupt(sentence, rng)
            if changed and changed != sentence:
                add(index, kind, changed, True, document_id, text)
        # A true statement from a different document: fluent, plausible, and not in this evidence.
        other = rng.choice(usable)
        if other[1] != document_id:
            add(index, "unrelated", other[3], True, document_id, text)
    return samples


def split_by_group(samples: list[VerifierSample], *, train_share: float = 0.5, seed: int = 3) -> tuple[list[VerifierSample], list[VerifierSample]]:
    """Split so that samples built from the same source material stay on the same side."""
    groups = sorted({s.group for s in samples})
    random.Random(seed).shuffle(groups)  # noqa: S311
    train_groups = set(groups[: int(len(groups) * train_share)])
    train = [s for s in samples if s.group in train_groups]
    test = [s for s in samples if s.group not in train_groups]
    return train, test
