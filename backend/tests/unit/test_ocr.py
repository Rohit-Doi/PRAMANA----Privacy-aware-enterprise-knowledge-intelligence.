"""Scanned pages and images are read by the local OCR model."""

from __future__ import annotations

import pymupdf
import pytest

from pramana.knowledge.ocr import OcrLine, group_paragraphs, repair_digits
from pramana.knowledge.parsers import detect_mime, parse_document

pytest.importorskip("rapidocr_onnxruntime")

LINES = [
    "Visitor Access Procedure",
    "All visitors must sign in at reception before entering.",
    "Badges are returned at the end of the visit.",
    "Escorts are required in the data centre at all times.",
]


def _page_image(dpi: int = 200) -> pymupdf.Pixmap:
    """Render a page of text to pixels: the text layer is gone, as in a scan."""
    source = pymupdf.open()
    page = source.new_page()
    page.insert_text((72, 100), LINES[0], fontsize=22)
    for index, line in enumerate(LINES[1:]):
        page.insert_text((72, 170 + index * 22), line, fontsize=12)
    pixmap = page.get_pixmap(dpi=dpi)
    source.close()
    return pixmap


def _scanned_pdf() -> bytes:
    pixmap = _page_image()
    scan = pymupdf.open()
    page = scan.new_page()
    page.insert_image(page.rect, pixmap=pixmap)
    data = scan.tobytes()
    scan.close()
    return data


def _normalise(text: str) -> str:
    return " ".join(text.lower().replace(",", " ").replace(".", " ").split())


def test_a_scanned_pdf_is_read_with_ocr() -> None:
    parsed = parse_document(_scanned_pdf(), filename="visitor-procedure.pdf")
    text = _normalise(" ".join(block.text for block in parsed.blocks))
    assert parsed.meta["ocr_pages"] == 1
    assert "visitors must sign in at reception" in text
    assert "escorts are required in the data centre" in text
    assert {block.page for block in parsed.blocks} == {1}
    # The large first line is recognised as the heading and becomes the title.
    assert "visitor access procedure" in _normalise(parsed.title)


def test_an_image_is_read_with_ocr() -> None:
    parsed = parse_document(_page_image().tobytes("png"), filename="photo.png")
    text = _normalise(" ".join(block.text for block in parsed.blocks))
    assert "badges are returned at the end of the visit" in text


def test_a_born_digital_pdf_does_not_use_ocr() -> None:
    document = pymupdf.open()
    document.new_page().insert_text((72, 100), "This page has a real text layer.", fontsize=12)
    parsed = parse_document(document.tobytes(), filename="digital.pdf")
    document.close()
    assert parsed.meta["ocr_pages"] == 0


def test_image_types_are_accepted() -> None:
    assert detect_mime("scan.PNG") == "image/png"
    assert detect_mime("scan.jpeg") == "image/jpeg"


def test_lines_are_grouped_into_paragraphs_by_vertical_gaps() -> None:
    lines = [
        OcrLine("Heading", top=10, left=0, height=30, confidence=0.9),
        OcrLine("First line of the paragraph", top=80, left=0, height=14, confidence=0.9),
        OcrLine("second line of the paragraph.", top=98, left=0, height=14, confidence=0.9),
        OcrLine("A new paragraph after a gap.", top=150, left=0, height=14, confidence=0.9),
    ]
    paragraphs = group_paragraphs(lines)
    assert [[line.text for line in paragraph] for paragraph in paragraphs] == [
        ["Heading"],
        ["First line of the paragraph", "second line of the paragraph."],
        ["A new paragraph after a gap."],
    ]


@pytest.mark.parametrize(
    ("read", "meant"),
    [
        ("above 85,0o0 dollars", "above 85,000 dollars"),
        ("a limit of 1O0 per day", "a limit of 100 per day"),
        ("version 2.O.1", "version 2.0.1"),
        ("room 1l4", "room 114"),
        ("growth of 2O%", "growth of 20%"),
        ("costs 50O", "costs 500"),
        # Words and identifiers are left alone.
        ("the Ohio office", "the Ohio office"),
        ("ticket SEC-2041 for Olga", "ticket SEC-2041 for Olga"),
        ("go to level 3 or 4", "go to level 3 or 4"),
    ],
)
def test_letters_read_for_digits_inside_figures_are_repaired(read: str, meant: str) -> None:
    assert repair_digits(read) == meant
