"""Entity and relation extraction for the knowledge graph. Local models only.

Two extractors produce the same output:

* ``fast``  spaCy named entities and noun phrases. Milliseconds per passage. Gives the graph
            its connective tissue: which passages talk about the same things.
* ``full``  GLiNER2 typed entities and typed relations. About a second per passage on a CPU.
            Used for the collections where typed facts matter, and for single uploads.
"""

from __future__ import annotations

import contextlib
import io
import re
import threading
from dataclasses import dataclass, field
from typing import Any

from pramana.shared.config import get_settings
from pramana.shared.logging import get_logger

log = get_logger(__name__)

ENTITY_TYPES = [
    "policy",
    "regulation",
    "department",
    "job role",
    "person",
    "organization",
    "software system",
    "process",
]
RELATION_TYPES = [
    "requires",
    "applies_to",
    "owned_by",
    "reports_to",
    "part_of",
    "responsible_for",
    "approved_by",
]
_TYPE_ALIASES = {"job role": "role", "software system": "system"}
_SPACY_TYPES = {
    "PERSON": "person",
    "ORG": "organization",
    "GPE": "location",
    "LOC": "location",
    "LAW": "regulation",
    "PRODUCT": "system",
    "NORP": "organization",
    "FAC": "location",
    "EVENT": "event",
    "WORK_OF_ART": "document",
}

# Words too generic to be useful graph nodes: they would connect everything to everything.
_GENERIC = frozenset(
    ["policy", "policies", "employee", "employees", "company", "companies", "team", "teams", "manager", "managers", "user", "users", "customer", "customers", "document", "documents", "information", "data", "process", "processes", "system", "systems", "organization", "organisation", "people", "person", "staff", "member", "members", "time", "day", "days", "week", "weeks", "month", "months", "year", "years", "way", "ways", "thing", "things", "part", "parts", "case", "cases", "example", "examples", "section", "sections", "page", "pages", "request", "requests", "requirement", "requirements", "change", "changes", "work", "use", "order", "addition", "purpose", "purposes", "result", "results", "issue", "issues", "question", "questions", "number", "following", "above", "below", "other", "others", "party", "parties", "agreement"]
)
_LEADING_DETERMINER = re.compile(
    r"^(?:the|a|an|this|that|these|those|our|your|their|its|any|all|each|every|such)\s+", re.I
)
_NON_WORD = re.compile(r"[^\w\s\-&/.']")
_SPACES = re.compile(r"\s+")
_LIST_MARKER = re.compile(r"^(?:\(?[0-9a-zA-Z]{1,2}[.)]\s+)+")
_MAX_NAMED_PER_CHUNK = 15
_MAX_CONCEPTS_PER_CHUNK = 10


@dataclass(frozen=True, slots=True)
class ExtractedEntity:
    name: str
    type: str
    confidence: float

    @property
    def key(self) -> str:
        return entity_key(self.name)


@dataclass(frozen=True, slots=True)
class ExtractedRelation:
    head: str
    type: str
    tail: str
    confidence: float


@dataclass(slots=True)
class ChunkExtraction:
    entities: list[ExtractedEntity] = field(default_factory=list)
    relations: list[ExtractedRelation] = field(default_factory=list)


def clean_name(name: str) -> str:
    name = _SPACES.sub(" ", name.replace("\n", " ")).strip(" \t.,;:!?\"'()[]{}*_-")
    name = _LIST_MARKER.sub("", name)  # "1) The salary" -> "The salary"
    return _LEADING_DETERMINER.sub("", name).strip()


def entity_key(name: str) -> str:
    """Canonical identity of an entity: case, punctuation and simple plurals are ignored."""
    key = _NON_WORD.sub(" ", clean_name(name).casefold())
    words = []
    for word in _SPACES.sub(" ", key).strip().split(" "):
        if len(word) > 4 and word.endswith("ies"):
            word = word[:-3] + "y"
        elif len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
            word = word[:-1]
        words.append(word)
    return " ".join(words)


def is_useful(name: str) -> bool:
    cleaned = clean_name(name)
    if not 2 <= len(cleaned) <= 80 or not re.search(r"[A-Za-z]{2}", cleaned):
        return False
    words = cleaned.casefold().split()
    if len(words) > 7:
        return False
    return not all(w in _GENERIC for w in words)


def _looks_like_person(name: str) -> bool:
    words = name.split()
    return 2 <= len(words) <= 4 and all(
        w[:1].isupper() and w.replace("-", "").replace("'", "").replace(".", "").isalpha() for w in words
    )


class GraphExtractor:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._gliner: Any = None
        self._spacy: Any = None

    # ── fast ────────────────────────────────────────────────────────────
    def _nlp(self) -> Any:
        if self._spacy is None:
            with self._lock:
                if self._spacy is None:
                    import spacy

                    self._spacy = spacy.load(get_settings().spacy_model, disable=["lemmatizer"])
        return self._spacy

    def extract_fast(self, texts: list[str]) -> list[ChunkExtraction]:
        results: list[ChunkExtraction] = []
        for doc in self._nlp().pipe(texts, batch_size=32):
            found: dict[str, ExtractedEntity] = {}
            for ent in doc.ents:
                kind = _SPACY_TYPES.get(ent.label_)
                name = clean_name(ent.text)
                if kind is None or not is_useful(name) or len(found) >= _MAX_NAMED_PER_CHUNK:
                    continue
                if kind == "person" and not _looks_like_person(name):
                    continue
                found.setdefault(entity_key(name), ExtractedEntity(name, kind, 0.8))
            concepts = 0
            for chunk in doc.noun_chunks:
                if concepts >= _MAX_CONCEPTS_PER_CHUNK:
                    break
                tokens = [t for t in chunk if not (t.is_stop or t.is_punct or t.pos_ in ("DET", "PRON", "NUM"))]
                if not 2 <= len(tokens) <= 4 or chunk.root.pos_ not in ("NOUN", "PROPN"):
                    continue
                name = clean_name(chunk.doc[tokens[0].i : tokens[-1].i + 1].text)
                key = entity_key(name)
                if is_useful(name) and len(name.split()) >= 2 and key not in found:
                    found[key] = ExtractedEntity(name, "concept", 0.6)
                    concepts += 1
            results.append(ChunkExtraction(entities=list(found.values())))
        return results

    # ── full ────────────────────────────────────────────────────────────
    def _model(self) -> Any:
        if self._gliner is None:
            with self._lock:
                if self._gliner is None:
                    from gliner2 import GLiNER2

                    name = get_settings().extraction_model
                    log.info("model.load", kind="extraction", name=name)
                    # The library prints a banner on load; keep it out of our logs.
                    with contextlib.redirect_stdout(io.StringIO()):
                        self._gliner = GLiNER2.from_pretrained(name)
        return self._gliner

    def extract_full(self, texts: list[str], *, batch_size: int = 8) -> list[ChunkExtraction]:
        model = self._model()
        raw_entities = model.batch_extract_entities(
            texts, ENTITY_TYPES, batch_size=batch_size, threshold=0.6, include_confidence=True
        )
        raw_relations = model.batch_extract_relations(
            texts, RELATION_TYPES, batch_size=batch_size, threshold=0.6, include_confidence=True
        )
        results: list[ChunkExtraction] = []
        for entity_doc, relation_doc in zip(raw_entities, raw_relations, strict=True):
            found: dict[str, ExtractedEntity] = {}
            for raw_type, items in (entity_doc.get("entities") or {}).items():
                kind = _TYPE_ALIASES.get(raw_type, raw_type)
                for item in items:
                    name = clean_name(item["text"])
                    if not is_useful(name):
                        continue
                    actual = kind
                    if kind == "person" and not _looks_like_person(name):
                        actual = "role"  # "manager", "department head": a role, not a person
                    key = entity_key(name)
                    previous = found.get(key)
                    confidence = float(item.get("confidence", 0.6))
                    if previous is None or confidence > previous.confidence:
                        found[key] = ExtractedEntity(name, actual, confidence)

            relations: list[ExtractedRelation] = []
            for relation_type, pairs in (relation_doc.get("relation_extraction") or {}).items():
                for pair in pairs:
                    head, tail = clean_name(pair["head"]["text"]), clean_name(pair["tail"]["text"])
                    head_conf = float(pair["head"].get("confidence", 0.6))
                    tail_conf = float(pair["tail"].get("confidence", 0.6))
                    if not (is_useful(head) and is_useful(tail)) or entity_key(head) == entity_key(tail):
                        continue
                    confidence = min(head_conf, tail_conf)
                    # Both ends must be recognised entities, or the model must be very sure.
                    for name, conf in ((head, head_conf), (tail, tail_conf)):
                        if entity_key(name) not in found and conf >= 0.85:
                            found[entity_key(name)] = ExtractedEntity(name, "concept", conf)
                    if entity_key(head) in found and entity_key(tail) in found:
                        relations.append(ExtractedRelation(head, relation_type, tail, confidence))
            results.append(ChunkExtraction(entities=list(found.values()), relations=relations))
        return results

    def extract(self, texts: list[str], *, mode: str) -> list[ChunkExtraction]:
        if mode == "full":
            return self.extract_full(texts)
        if mode == "fast":
            return self.extract_fast(texts)
        raise ValueError(f"Unknown extraction mode: {mode}")

    def query_entities(self, text: str) -> list[ExtractedEntity]:
        """Entities mentioned in a question (fast extractor: this runs on every request)."""
        return self.extract_fast([text])[0].entities


_extractor: GraphExtractor | None = None


def get_graph_extractor() -> GraphExtractor:
    global _extractor
    if _extractor is None:
        _extractor = GraphExtractor()
    return _extractor
