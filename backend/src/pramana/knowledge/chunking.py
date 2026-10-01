"""Structure-aware chunking.

Chunks follow the document's own outline: a chunk never straddles a major section unless the
section is too small to stand alone, sentences are never cut, and tables stay whole. Every
chunk is an exact slice of the document's canonical text, so a citation can be highlighted
in the source by character offset.
"""

from __future__ import annotations

from dataclasses import dataclass

from pramana.knowledge.parsers import Block, ParsedDocument
from pramana.shared.text import estimate_tokens, normalize_text, split_sentences

SECTION_SEPARATOR = " › "


@dataclass(frozen=True, slots=True)
class ChunkDraft:
    ordinal: int
    text: str
    section_path: str
    page: int | None
    char_start: int
    char_end: int
    token_count: int


@dataclass(frozen=True, slots=True)
class _Unit:
    """Smallest indivisible piece: a sentence, a list item or a table."""

    start: int
    end: int
    tokens: int
    section_path: str
    page: int | None
    is_heading: bool = False


def embedding_text(*, title: str, section_path: str, text: str) -> str:
    """Text that is embedded and keyword-indexed: the chunk plus where it sits in the document.

    Prepending the location makes "the limit" in a chunk findable by "travel expense limit".
    """
    header = title if not section_path or section_path == title else f"{title}{SECTION_SEPARATOR}{section_path}"
    return f"{header}\n{text}"


def build_canonical_text(parsed: ParsedDocument) -> tuple[str, list[tuple[Block, int, int]]]:
    """Concatenate blocks into the canonical text; return it with each block's span."""
    parts: list[str] = []
    spans: list[tuple[Block, int, int]] = []
    cursor = 0
    for block in parsed.blocks:
        text = normalize_text(block.text)
        if not text:
            continue
        if parts:
            parts.append("\n\n")
            cursor += 2
        parts.append(text)
        spans.append((block, cursor, cursor + len(text)))
        cursor += len(text)
    return "".join(parts), spans


def chunk_document(
    parsed: ParsedDocument,
    *,
    target_tokens: int = 320,
    max_tokens: int = 460,
    min_tokens: int = 48,
    overlap_sentences: int = 1,
) -> tuple[str, list[ChunkDraft]]:
    canonical, block_spans = build_canonical_text(parsed)
    units = _units(canonical, block_spans, max_tokens=max_tokens)

    chunks: list[ChunkDraft] = []
    current: list[_Unit] = []
    carried = 0  # leading units of `current` repeated from the previous chunk as overlap

    def tokens_of(items: list[_Unit]) -> int:
        return sum(u.tokens for u in items)

    def new_body() -> list[_Unit]:
        return [u for u in current[carried:] if not u.is_heading]

    def flush(*, carry_overlap: bool, force: bool = False) -> None:
        nonlocal current, carried
        fresh = new_body()
        if not fresh and force:
            fresh = current[carried:]  # a long run of heading-like lines (a contents page)
        if not fresh:
            return  # nothing but overlap and headings: wait for real content
        start, end = current[0].start, current[-1].end
        chunks.append(
            ChunkDraft(
                ordinal=len(chunks), text=canonical[start:end], section_path=fresh[0].section_path,
                page=fresh[0].page, char_start=start, char_end=end,
                token_count=estimate_tokens(canonical[start:end]),
            )
        )
        tail = fresh[-overlap_sentences:] if carry_overlap and overlap_sentences > 0 else []
        # Do not carry a table or an oversized sentence into the next chunk.
        current = [u for u in tail if u.tokens <= target_tokens // 3]
        carried = len(current)

    for unit in units:
        if unit.is_heading:
            # A new section closes the current chunk, unless that chunk is still too small
            # to stand alone, in which case the small sections are kept together.
            if tokens_of(new_body()) >= min_tokens:
                flush(carry_overlap=False)
            if not new_body():
                # Overlap must not leak across a section boundary.
                current = [u for u in current[carried:] if u.is_heading]
                carried = 0
            if tokens_of(current) + unit.tokens > target_tokens:
                flush(carry_overlap=False, force=True)
            current.append(unit)
            continue
        if new_body() and tokens_of(current) + unit.tokens > max_tokens:
            flush(carry_overlap=True)
        current.append(unit)
        if tokens_of(current) >= target_tokens:
            flush(carry_overlap=True)
    flush(carry_overlap=False)
    return canonical, chunks


def _units(canonical: str, block_spans: list[tuple[Block, int, int]], *, max_tokens: int) -> list[_Unit]:
    units: list[_Unit] = []
    stack: list[tuple[int, str]] = []
    for block, start, end in block_spans:
        text = canonical[start:end]
        if block.kind == "heading":
            level = block.level or 2
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, text))
            path = _section_path(stack)
            units.append(_Unit(start, end, estimate_tokens(text), path, block.page, is_heading=True))
            continue
        path = _section_path(stack)
        if block.kind == "table":
            units.extend(_split_table(canonical, start, end, path, block.page, max_tokens))
            continue
        for s, e in split_sentences(text):
            units.extend(_split_oversized(canonical, start + s, start + e, path, block.page, max_tokens))
    return units


def _section_path(stack: list[tuple[int, str]]) -> str:
    """The document outline down to this point, limited to the four innermost levels."""
    return SECTION_SEPARATOR.join(text[:120] for _, text in stack[-4:])


def _split_oversized(
    canonical: str, start: int, end: int, path: str, page: int | None, max_tokens: int
) -> list[_Unit]:
    tokens = estimate_tokens(canonical[start:end])
    if tokens <= max_tokens:
        return [_Unit(start, end, tokens, path, page)]
    # A "sentence" longer than a whole chunk (legal boilerplate): cut at word boundaries.
    pieces: list[_Unit] = []
    cursor = start
    approx_chars = max(200, int((end - start) * max_tokens / tokens * 0.9))
    while cursor < end:
        cut = min(end, cursor + approx_chars)
        if cut < end:
            space = canonical.rfind(" ", cursor, cut)
            if space > cursor:
                cut = space
        pieces.append(_Unit(cursor, cut, estimate_tokens(canonical[cursor:cut]), path, page))
        cursor = cut
        while cursor < end and canonical[cursor].isspace():
            cursor += 1
    return pieces


def _split_table(
    canonical: str, start: int, end: int, path: str, page: int | None, max_tokens: int
) -> list[_Unit]:
    tokens = estimate_tokens(canonical[start:end])
    if tokens <= max_tokens:
        return [_Unit(start, end, tokens, path, page)]
    # A table larger than a chunk is split between rows.
    units: list[_Unit] = []
    row_start = start
    group_start = start
    group_tokens = 0
    for offset, char in enumerate(canonical[start:end] + "\n"):
        if char != "\n":
            continue
        row_end = start + offset
        row_tokens = estimate_tokens(canonical[row_start:row_end])
        if group_tokens and group_tokens + row_tokens > max_tokens:
            units.append(_Unit(group_start, row_start - 1, group_tokens, path, page))
            group_start, group_tokens = row_start, 0
        group_tokens += row_tokens
        row_start = row_end + 1
    if group_tokens:
        units.append(_Unit(group_start, end, group_tokens, path, page))
    return units
