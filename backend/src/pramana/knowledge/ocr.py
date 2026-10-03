"""Optical character recognition for scanned pages and images.

Runs locally on CPU (RapidOCR: PaddleOCR's detection and recognition models exported to
ONNX, shipped inside the package, so nothing is downloaded). It is used only where a page
has no text layer; born-digital documents never go through it.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from typing import Any

import structlog

from pramana.shared.errors import ValidationFailed

log = structlog.get_logger(__name__)

MIN_CONFIDENCE = 0.55
# 200 dpi is the usual scanning resolution; below ~150 small print is lost, above 300 only time is.
RENDER_DPI = 200
# A side longer than this is scaled down before recognition: bounded memory and time per page.
MAX_SIDE_PIXELS = 2600


@dataclass(frozen=True, slots=True)
class OcrLine:
    text: str
    top: float
    left: float
    height: float
    confidence: float


class OcrEngine:
    """Lazy, thread-safe wrapper. The models load on first use (about 15 MB)."""

    def __init__(self) -> None:
        self._engine: Any = None
        self._lock = threading.Lock()

    def _load(self) -> Any:
        if self._engine is None:
            with self._lock:
                if self._engine is None:
                    try:
                        from rapidocr_onnxruntime import RapidOCR
                    except ImportError as exc:  # the dependency is optional at import time
                        raise ValidationFailed(
                            "This file has no text layer (it looks scanned) and OCR is not installed."
                        ) from exc
                    log.info("model.load", kind="ocr", name="rapidocr-onnxruntime")
                    self._engine = RapidOCR()
        return self._engine

    def read(self, image: Any) -> list[OcrLine]:
        """Recognise text in an image (numpy array, HxWx3). Lines come back in reading order.

        A clean, straight page is cut into text lines by its ink profile and each line is recognised
        on its own. The general text detector is the fallback (photos, skewed or noisy pages): on a
        dense single-spaced page it merges neighbouring lines and loses about a third of the text.
        """
        engine = self._load()
        with self._lock:  # one page at a time: the runtime session is not re-entrant
            by_profile = self._read_by_profile(engine, image)
            if by_profile is not None:
                return by_profile
            result, _ = engine(image)
        lines: list[OcrLine] = []
        for box, text, confidence in result or []:
            text = repair_digits(str(text).strip())
            if not text or float(confidence) < MIN_CONFIDENCE:
                continue
            ys = [float(point[1]) for point in box]
            xs = [float(point[0]) for point in box]
            lines.append(OcrLine(text, min(ys), min(xs), max(ys) - min(ys), float(confidence)))
        return _reading_order(lines)

    def _read_by_profile(self, engine: Any, image: Any) -> list[OcrLine] | None:
        """Line-by-line reading for clean pages; None when the page does not look like one."""
        import numpy as np

        ink = image.min(axis=2) < INK_BELOW
        columns = _text_columns(ink)
        lines: list[OcrLine] = []
        for left, right in columns:
            bands = [(s, e) for s, e in _runs(ink[:, left:right].sum(axis=1) >= 2) if e - s >= MIN_LINE_PIXELS]
            if len(bands) < 3:
                return None
            heights = np.array([e - s for s, e in bands])
            typical = float(np.median(heights))
            # Skew, pictures or handwriting give bands of very uneven height: not a clean page.
            if float(np.mean((heights > 0.45 * typical) & (heights < 2.2 * typical))) < 0.8:
                return None
            for top, bottom in bands:
                height = bottom - top
                glyphs = _runs(ink[top:bottom, left:right].sum(axis=0) >= 1)
                if not glyphs:
                    continue
                # Glyph runs closer than a word space are one word; words are grouped into pieces of
                # moderate width, because the recogniser drops the spaces of a very long line.
                reference = max(typical, 0.8 * height)  # a heading is set larger than the body text
                word_gap = max(4, int(0.28 * reference))
                words = [[glyphs[0][0], glyphs[0][1]]]
                for start, end in glyphs[1:]:
                    if start - words[-1][1] < word_gap:
                        words[-1][1] = end
                    else:
                        words.append([start, end])
                limit = int(14 * reference)
                pieces = [list(words[0])]
                for start, end in words[1:]:
                    if end - pieces[-1][0] <= limit:
                        pieces[-1][1] = end
                    else:
                        pieces.append([start, end])
                pad = max(3, int(0.25 * height))
                texts: list[str] = []
                confidences: list[float] = []
                for start, end in pieces:
                    crop = image[max(0, top - pad) : bottom + pad, max(0, left + start - pad) : left + end + pad]
                    result, _ = engine(np.ascontiguousarray(crop), use_det=False, use_cls=False, use_rec=True)
                    if result and float(result[0][1]) >= MIN_CONFIDENCE and str(result[0][0]).strip():
                        texts.append(str(result[0][0]).strip())
                        confidences.append(float(result[0][1]))
                if texts:
                    lines.append(
                        OcrLine(
                            repair_digits(" ".join(texts)),
                            float(top),
                            float(left + glyphs[0][0]),
                            float(height),
                            min(confidences),
                        )
                    )
        return lines or None


INK_BELOW = 150  # grey level under which a pixel counts as ink
MIN_LINE_PIXELS = 6


def _runs(mask: Any) -> list[tuple[int, int]]:
    """(start, end) of each run of consecutive True values."""
    import numpy as np

    edges = np.diff(np.concatenate([[False], mask, [False]]).astype(np.int8))
    return [(int(s), int(e)) for s, e in zip(np.where(edges == 1)[0], np.where(edges == -1)[0], strict=True)]


def _text_columns(ink: Any) -> list[tuple[int, int]]:
    """One column, or two when a clear vertical gutter runs down the middle of the page."""
    width = ink.shape[1]
    filled = ink.sum(axis=0) >= 1
    used = _runs(filled)
    if not used:
        return [(0, width)]
    first, last = used[0][0], used[-1][1]
    gutters = [
        (s, e)
        for s, e in _runs(~filled[first:last])
        if e - s >= 0.025 * width and 0.3 * width <= first + (s + e) / 2 <= 0.7 * width
    ]
    if len(gutters) != 1:
        return [(first, last)]
    middle = first + (gutters[0][0] + gutters[0][1]) // 2
    return [(first, middle), (middle, last)]


_LETTER_O_IN_NUMBER = re.compile(r"(?<=[\d,.])[oO](?=[\d,.%])|(?<=\d)[oO](?![A-Za-z])")
_LETTER_L_IN_NUMBER = re.compile(r"(?<=[\d,.])[lI](?=[\d,.])")


def repair_digits(text: str) -> str:
    """Undo the commonest recognition slip inside figures: a letter O read for a zero, an l or I for a one.

    Only a letter standing between digits (or digit separators) is changed, so words are untouched.
    """
    return _LETTER_L_IN_NUMBER.sub("1", _LETTER_O_IN_NUMBER.sub("0", text))


def _reading_order(lines: list[OcrLine]) -> list[OcrLine]:
    """Top to bottom, then left to right; boxes on (nearly) the same baseline form one row."""
    rows: list[list[OcrLine]] = []
    for line in sorted(lines, key=lambda item: item.top):
        if rows and abs(rows[-1][0].top - line.top) <= 0.6 * max(rows[-1][0].height, 1.0):
            rows[-1].append(line)
        else:
            rows.append([line])
    return [line for row in rows for line in sorted(row, key=lambda item: item.left)]


def group_paragraphs(lines: list[OcrLine]) -> list[list[OcrLine]]:
    """Split recognised lines into paragraphs at vertical gaps larger than a line."""
    paragraphs: list[list[OcrLine]] = []
    previous: OcrLine | None = None
    for line in lines:
        same_row = previous is not None and abs(previous.top - line.top) <= 0.6 * max(previous.height, 1.0)
        gap = 0.0 if previous is None else line.top - (previous.top + previous.height)
        smaller = 1.0 if previous is None else min(previous.height, line.height)
        larger = 1.0 if previous is None else max(previous.height, line.height)
        size_changed = previous is not None and (larger - smaller) > 0.25 * larger
        if previous is None or (not same_row and (gap > 0.6 * smaller or size_changed)):
            paragraphs.append([line])
        else:
            paragraphs[-1].append(line)
        previous = line
    return paragraphs


def image_from_bytes(data: bytes) -> Any:
    """Decode an image file into an array, scaled down if it is very large."""
    import cv2
    import numpy as np

    image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValidationFailed("The image could not be read.")
    return _bounded(image)


def image_from_pixmap(pixmap: Any) -> Any:
    """A rendered PDF page (PyMuPDF pixmap, RGB) as an array."""
    import numpy as np

    array = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(pixmap.height, pixmap.width, pixmap.n)
    return _bounded(array[:, :, :3])


def _bounded(image: Any) -> Any:
    import cv2

    height, width = image.shape[:2]
    longest = max(height, width)
    if longest <= MAX_SIDE_PIXELS:
        return image
    scale = MAX_SIDE_PIXELS / longest
    return cv2.resize(image, (int(width * scale), int(height * scale)), interpolation=cv2.INTER_AREA)


_engine: OcrEngine | None = None


def get_ocr_engine() -> OcrEngine:
    global _engine
    if _engine is None:
        _engine = OcrEngine()
    return _engine
