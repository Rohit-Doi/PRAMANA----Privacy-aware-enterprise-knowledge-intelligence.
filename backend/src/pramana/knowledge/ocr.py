"""Optical character recognition for scanned pages and images.

Runs locally on CPU (RapidOCR: PaddleOCR's detection and recognition models exported to
ONNX, shipped inside the package, so nothing is downloaded). It is used only where a page
has no text layer; born-digital documents never go through it.
"""

from __future__ import annotations

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
        """Recognise text in an image (numpy array, HxWx3). Lines come back in reading order."""
        engine = self._load()
        with self._lock:  # one page at a time: the runtime session is not re-entrant
            result, _ = engine(image)
        lines: list[OcrLine] = []
        for box, text, confidence in result or []:
            text = str(text).strip()
            if not text or float(confidence) < MIN_CONFIDENCE:
                continue
            ys = [float(point[1]) for point in box]
            xs = [float(point[0]) for point in box]
            lines.append(OcrLine(text, min(ys), min(xs), max(ys) - min(ys), float(confidence)))
        return _reading_order(lines)


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
        size_changed = previous is not None and abs(line.height - previous.height) > 0.35 * previous.height
        if previous is None or (not same_row and (gap > 0.9 * previous.height or size_changed)):
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
