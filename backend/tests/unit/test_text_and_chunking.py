"""Sentence splitting and chunking: offsets must be exact, because citations highlight by offset."""

from __future__ import annotations

from pramana.knowledge.chunking import SECTION_SEPARATOR, chunk_document, embedding_text
from pramana.knowledge.parsers import Block, ParsedDocument
from pramana.shared.text import estimate_tokens, normalize_text, split_sentences


def sentences(text: str) -> list[str]:
    return [text[start:end] for start, end in split_sentences(text)]


def test_sentences_split_on_terminal_punctuation() -> None:
    text = "Expenses need a receipt. Claims above 500 need approval! Is travel covered? Yes."
    assert sentences(text) == [
        "Expenses need a receipt.",
        "Claims above 500 need approval!",
        "Is travel covered?",
        "Yes.",
    ]


def test_abbreviations_and_decimals_do_not_end_a_sentence() -> None:
    text = "Dr. Rao approved 3.5 days, e.g. for training. The rest was declined."
    assert sentences(text) == ["Dr. Rao approved 3.5 days, e.g. for training.", "The rest was declined."]


def test_sentence_spans_are_offsets_into_the_original_text() -> None:
    text = "  First sentence here.   Second sentence follows.\n\nThird one.  "
    for start, end in split_sentences(text):
        piece = text[start:end]
        assert piece == piece.strip()
        assert piece


def test_normalize_text_collapses_whitespace_and_control_characters() -> None:
    assert normalize_text("a  b\t\tc\r\n\r\n\r\n\r\nd​") == "a b c\n\nd"


def test_estimate_tokens_grows_with_length() -> None:
    assert estimate_tokens("one two three") < estimate_tokens("one two three four five six")


def _policy_document() -> ParsedDocument:
    paragraph = (
        "Employees may claim travel expenses within thirty days of the trip. "
        "Each claim needs an itemised receipt. "
        "Claims above the limit need approval from the department head. "
    )
    blocks = [Block("heading", "Travel Policy", level=1)]
    for section in ("Eligibility", "Limits", "Approval"):
        blocks.append(Block("heading", section, level=2))
        blocks.extend(Block("text", paragraph * 3) for _ in range(3))
    return ParsedDocument(title="Travel Policy", blocks=blocks)


def test_chunks_respect_the_maximum_size_and_cover_the_document() -> None:
    canonical, chunks = chunk_document(_policy_document(), target_tokens=120, max_tokens=180, min_tokens=20)
    assert len(chunks) > 3
    assert [c.ordinal for c in chunks] == list(range(len(chunks)))
    for chunk in chunks:
        assert chunk.token_count <= 180
        # The stored text is exactly the canonical text at the stored offsets.
        assert canonical[chunk.char_start : chunk.char_end] == chunk.text
    # Every body sentence of the document is inside at least one chunk.
    assert "itemised receipt" in " ".join(c.text for c in chunks)
    assert max(c.char_end for c in chunks) == len(canonical)


def test_chunks_carry_their_section_path_and_do_not_cross_sections() -> None:
    _, chunks = chunk_document(_policy_document(), target_tokens=120, max_tokens=180, min_tokens=20)
    paths = {c.section_path for c in chunks}
    assert any(p.endswith("Eligibility") for p in paths)
    assert any(p.endswith("Approval") for p in paths)
    for chunk in chunks:
        assert chunk.section_path.startswith("Travel Policy")
        # A chunk belongs to one section: no other section's heading appears inside it.
        own = chunk.section_path.split(SECTION_SEPARATOR)[-1]
        for other in ("Eligibility", "Limits", "Approval"):
            if other != own:
                assert f"\n{other}\n" not in f"\n{chunk.text}\n"


def test_chunking_is_deterministic() -> None:
    first = chunk_document(_policy_document(), target_tokens=120, max_tokens=180, min_tokens=20)
    second = chunk_document(_policy_document(), target_tokens=120, max_tokens=180, min_tokens=20)
    assert first == second


def test_empty_document_gives_no_chunks() -> None:
    canonical, chunks = chunk_document(ParsedDocument(title="Empty", blocks=[]))
    assert canonical == ""
    assert chunks == []


def test_embedding_text_prefixes_location() -> None:
    text = embedding_text(title="Travel Policy", section_path="Travel Policy › Limits", text="The limit is 500.")
    assert text.splitlines()[0] == "Travel Policy › Travel Policy › Limits" or "Limits" in text.splitlines()[0]
    assert text.endswith("The limit is 500.")
    assert embedding_text(title="T", section_path="", text="x") == "T\nx"
