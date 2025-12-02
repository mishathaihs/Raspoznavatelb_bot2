from __future__ import annotations

import asyncio
import io
import logging
import re
from pathlib import Path
from typing import Iterable, Literal
from dataclasses import dataclass

try:  # pragma: no cover - optional dependency
    import pdfplumber
except ImportError:  # pragma: no cover - optional dependency
    pdfplumber = None
try:  # pragma: no cover - optional dependency
    from PIL import Image, ImageEnhance, ImageFilter, ImageOps
except ImportError:  # pragma: no cover - optional dependency
    Image = None
    ImageEnhance = None
    ImageFilter = None
    ImageOps = None

from services.exceptions import OcrError
from models.parser_types import OCRDateCandidate, OCRResult
from services.image_preprocessing import preprocess_image_bytes
from services.layout_types import LayoutExtraction, WordBox

try:  # pragma: no cover - optional dependency
    import pytesseract
except ImportError:  # pragma: no cover - optional dependency
    pytesseract = None

logger = logging.getLogger(__name__)

FileType = Literal["pdf", "image"]

OCR_LANG = "rus+eng"
TESSERACT_CONFIG = "--oem 3 --psm 6"
MIN_TEXT_LENGTH = 200

CYRILLIC_RE = re.compile(r"[\u0400-\u04FF]")
LATIN_RE = re.compile(r"[A-Za-z]")
DATE_REGEXPS: list[re.Pattern[str]] = [
    re.compile(r"\b(\d{1,2})[./](\d{1,2})[./](\d{2,4})\b"),
    re.compile(
        r"\b(\d{1,2})\s+"
        r"(января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)"
        r"\s+(\d{4})\b",
        re.IGNORECASE,
    ),
]

MONTH_MAP = {
    "января": 1,
    "февраля": 2,
    "марта": 3,
    "апреля": 4,
    "мая": 5,
    "июня": 6,
    "июля": 7,
    "августа": 8,
    "сентября": 9,
    "октября": 10,
    "ноября": 11,
    "декабря": 12,
}


def _preprocess_image(image: Image.Image) -> Image.Image:
    if ImageEnhance is None or ImageOps is None or ImageFilter is None:  # pragma: no cover - optional dependency
        raise OcrError("Pillow is not installed on the server.")
    oriented = ImageOps.exif_transpose(image)
    grayscale = oriented.convert("L")
    contrasted = ImageOps.autocontrast(grayscale)
    sharpened = contrasted.filter(ImageFilter.SHARPEN)
    threshold_value = 160
    binary = sharpened.point(lambda p: 255 if p > threshold_value else 0)
    return binary


def extract_layout_from_image_bytes(image_bytes: bytes) -> LayoutExtraction:
    if pytesseract is None:  # pragma: no cover - depends on environment
        raise OcrError("pytesseract is not installed on the server.")
    _, processed_image = preprocess_image_bytes(image_bytes)
    data = pytesseract.image_to_data(
        processed_image, lang=OCR_LANG, config=TESSERACT_CONFIG, output_type=pytesseract.Output.DICT
    )
    words: list[WordBox] = []
    lines: list[str] = []
    last_line = None
    for idx, text in enumerate(data.get("text", [])):
        cleaned = text.strip()
        if not cleaned:
            continue
        line_num = data.get("line_num", [None])[idx]
        if last_line is None:
            last_line = line_num
        elif line_num != last_line:
            lines.append(" ".join(w.text for w in words if w.line_num == last_line))
            last_line = line_num
        words.append(
            WordBox(
                text=cleaned,
                x=int(data.get("left", [0])[idx]),
                y=int(data.get("top", [0])[idx]),
                w=int(data.get("width", [0])[idx]),
                h=int(data.get("height", [0])[idx]),
                line_num=int(line_num) if line_num is not None else None,
            )
        )
    if last_line is not None:
        lines.append(" ".join(w.text for w in words if w.line_num == last_line))

    raw_text = "\n".join(line for line in lines if line).strip()
    normalized = normalize_ocr_text(raw_text)
    return LayoutExtraction(words=words, raw_text=normalized)


def _perform_ocr(image: Image.Image) -> str:
    if pytesseract is None:  # pragma: no cover - depends on environment
        raise OcrError("pytesseract is not installed on the server.")
    processed = _preprocess_image(image)
    text = pytesseract.image_to_string(processed, lang=OCR_LANG, config=TESSERACT_CONFIG)
    return text.strip()


async def extract_text_from_file(path: Path) -> OCRResult:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        text = await asyncio.to_thread(_extract_text_from_pdf, path)
        return _build_result(text, "pdf")
    if suffix in {".png", ".jpg", ".jpeg"}:
        text = await asyncio.to_thread(_extract_text_from_image, path)
        return _build_result(text, "image")
    raise OcrError("Unsupported file format. Please send PDF, PNG or JPEG.")


def _extract_text_from_pdf(path: Path) -> str:
    if pdfplumber is None:  # pragma: no cover - optional dependency
        raise OcrError("pdfplumber is not installed on the server.")
    with pdfplumber.open(path) as pdf:
        text_parts = [page.extract_text() or "" for page in pdf.pages]

        extracted = "\n".join(part.strip() for part in text_parts if part).strip()
        if extracted:
            return extracted

        text_parts = []
        for idx, page in enumerate(pdf.pages, start=1):
            page_image = page.to_image(resolution=300)
            buffer = io.BytesIO()
            page_image.save(buffer, format="PNG")
            buffer.seek(0)
            image = Image.open(buffer)
            ocr_text = _perform_ocr(image)
            if ocr_text:
                text_parts.append(f"--- page {idx} ---\n{ocr_text.strip()}")

    text = "\n\n".join(text_parts).strip()
    if not text or len(text) < MIN_TEXT_LENGTH:
        raise OcrError("No text could be extracted from PDF.")
    return text


def _extract_text_from_image(path: Path) -> str:
    if Image is None:  # pragma: no cover - optional dependency
        raise OcrError("Pillow is not installed on the server.")
    try:
        image = Image.open(path)
    except Exception as exc:  # pragma: no cover - filesystem related
        raise OcrError("Failed to open image for OCR") from exc
    text = _perform_ocr(image)
    if len(text) < MIN_TEXT_LENGTH:
        raise OcrError("Extracted text is too short; document quality is low")
    return text


def normalize_ocr_text(text: str) -> str:
    normalized_digits = _normalize_digits(text)
    merged = _merge_broken_words(normalized_digits)
    merged = merged.replace("\r", "\n")
    merged = _fix_common_ocr_errors(merged)
    merged = re.sub(r"[ \t]+", " ", merged)
    merged = re.sub(r"\n{2,}", "\n\n", merged)
    lines = [line.strip() for line in merged.splitlines()]
    return "\n".join(lines).strip()


def _normalize_digits(text: str) -> str:
    digit_map = {ord(ch): str(idx) for idx, ch in enumerate("٠١٢٣٤٥٦٧٨٩")}
    digit_map.update({ord(ch): str(idx) for idx, ch in enumerate("۰۱۲۳۴۵۶۷۸۹")})
    return text.translate(digit_map)


def _merge_broken_words(text: str) -> str:
    return re.sub(r"(?<=[A-Za-zА-Яа-яЁё])\s*\n\s*(?=[A-Za-zА-Яа-яЁё])", "", text)


def _fix_common_ocr_errors(text: str) -> str:
    # Replace Latin lookalikes inside Cyrillic words to reduce noisy OCR output
    latin_to_cyr = {
        "A": "А",
        "B": "В",
        "E": "Е",
        "K": "К",
        "M": "М",
        "H": "Н",
        "O": "О",
        "P": "Р",
        "C": "С",
        "T": "Т",
        "X": "Х",
        "a": "а",
        "e": "е",
        "o": "о",
        "p": "р",
        "c": "с",
        "y": "у",
        "x": "х",
    }

    def _replace(match: re.Match[str]) -> str:
        ch = match.group(0)
        return latin_to_cyr.get(ch, ch)

    text = re.sub(r"(?<=[А-Яа-яЁё])[A-Za-z](?=[А-Яа-яЁё])", _replace, text)
    text = re.sub(r"(?<=[А-Яа-яЁё])0(?=[А-Яа-яЁё])", "О", text)
    text = re.sub(r"(?<=[А-Яа-яЁё])1(?=[А-Яа-яЁё])", "І", text)
    return text


def _extract_date_candidates(text: str) -> list[OCRDateCandidate]:
    candidates: list[OCRDateCandidate] = []
    for pattern in DATE_REGEXPS:
        for match in pattern.finditer(text):
            start = max(0, match.start() - 40)
            end = min(len(text), match.end() + 40)
            candidates.append(
                OCRDateCandidate(
                    raw_value=match.group(0),
                    context=text[start:end],
                    start_index=match.start(),
                )
            )
    return candidates


def _build_result(text: str, file_type: FileType) -> OCRResult:
    normalized = normalize_ocr_text(text)
    meta = {"dates": _extract_date_candidates(normalized)}
    return OCRResult(text=normalized, language="ru", meta=meta, file_type=file_type)


__all__ = [
    "extract_text_from_file",
    "MIN_TEXT_LENGTH",
    "OCR_LANG",
    "normalize_ocr_text",
]
