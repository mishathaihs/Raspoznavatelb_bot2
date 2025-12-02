from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Tuple

try:  # pragma: no cover - optional dependency
    import pytesseract
except ImportError:  # pragma: no cover - optional dependency
    pytesseract = None  # type: ignore
try:  # pragma: no cover - optional dependency
    from PIL import Image, ImageFilter, ImageOps
except ImportError:  # pragma: no cover - optional dependency
    Image = None
    ImageFilter = None
    ImageOps = None

try:  # pragma: no cover - optional dependency
    import cv2
except ImportError:  # pragma: no cover - optional dependency
    cv2 = None

logger = logging.getLogger(__name__)

SUPPLIER_BOX_RATIO: Tuple[float, float, float, float] = (0.05, 0.21, 0.95, 0.24)
CUSTOMER_BOX_RATIO: Tuple[float, float, float, float] = (0.05, 0.24, 0.95, 0.27)
HEADER_BOX_RATIO: Tuple[float, float, float, float] = (0.02, 0.17, 0.98, 0.33)
BOX_PADDING_RATIO: float = 0.005
MIN_VALID_LENGTH = 10


@dataclass
class PartyInfo:
    supplier_name: str | None = None
    customer_name: str | None = None
    supplier_tax_id: str | None = None
    customer_tax_id: str | None = None
    supplier_line_ocr: str | None = None
    customer_line_ocr: str | None = None

    # backward compatibility for previous field names
    @property
    def supplier_name_raw(self) -> str | None:  # pragma: no cover - compatibility
        return self.supplier_name

    @property
    def customer_name_raw(self) -> str | None:  # pragma: no cover - compatibility
        return self.customer_name

    @property
    def supplier_text(self) -> str | None:  # pragma: no cover - compatibility
        return self.supplier_name

    @property
    def customer_text(self) -> str | None:  # pragma: no cover - compatibility
        return self.customer_name


@dataclass
class OcrParties:
    supplier_name: str | None
    supplier_tax_id: str | None
    customer_name: str | None
    customer_tax_id: str | None
    header_text: str


def _clamp(value: int, min_value: int, max_value: int) -> int:
    return max(min_value, min(value, max_value))


def _ensure_pil(image: Image.Image | "np.ndarray") -> Image.Image:
    if Image is None:  # pragma: no cover - defensive
        raise ImportError("Pillow is required for OCR party extraction")

    try:
        import numpy as np  # type: ignore

        if hasattr(image, "shape"):
            return Image.fromarray(image)
    except Exception:  # pragma: no cover - defensive
        pass
    return image


def _expand_box(box: Tuple[int, int, int, int], h: int) -> Tuple[int, int, int, int]:
    left, top, right, bottom = box
    pad = int(BOX_PADDING_RATIO * h)
    return (
        left,
        _clamp(top - pad, 0, h),
        right,
        _clamp(bottom + pad, 0, h),
    )


def _prepare_line_image(image: Image.Image, *, scale: float = 2.0) -> Image.Image:
    if ImageOps is None or ImageFilter is None or Image is None:  # pragma: no cover - defensive
        raise ImportError("Pillow is required for OCR party extraction")

    gray = ImageOps.grayscale(ImageOps.exif_transpose(image))
    width, height = gray.size
    scaled = gray.resize((int(width * scale), int(height * scale)), Image.Resampling.LANCZOS)
    contrasted = ImageOps.autocontrast(scaled)
    blurred = contrasted.filter(ImageFilter.GaussianBlur(radius=1))
    thresholded = None
    try:
        if cv2 is not None:
            import numpy as np  # lazy import

            arr = np.array(blurred)
            _, thresh = cv2.threshold(arr, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            thresholded = Image.fromarray(thresh)
    except Exception:  # pragma: no cover - runtime safety
        thresholded = None
    if thresholded is None:
        threshold = 180
        thresholded = blurred.point(lambda p: 255 if p > threshold else 0)
    return thresholded


def _run_tesseract(image: Image.Image, *, config: str = "--psm 7") -> str:
    if pytesseract is None:  # pragma: no cover - defensive
        raise ImportError("pytesseract is required for OCR party extraction")

    text = pytesseract.image_to_string(image, lang="rus+eng", config=config)
    if not text or len(text.strip()) < MIN_VALID_LENGTH:
        fallback_config = "--psm 6" if "--psm 7" in config else config
        fallback = pytesseract.image_to_string(image, lang="rus+eng", config=fallback_config)
        if fallback and len(fallback.strip()) > len(text.strip() if text else ""):
            text = fallback
    return text or ""


def _crop_by_ratio(image: Image.Image, box_ratio: Tuple[float, float, float, float]) -> Image.Image:
    width, height = image.size
    left = int(box_ratio[0] * width)
    top = int(box_ratio[1] * height)
    right = int(box_ratio[2] * width)
    bottom = int(box_ratio[3] * height)
    expanded = _expand_box((left, top, right, bottom), height)
    return image.crop(expanded)


def _normalize_for_match(value: str) -> str:
    import re

    keep = re.sub(r"[^A-Za-zА-Яа-яЁё ]+", " ", value.upper())
    return " ".join(keep.split())


def _find_party_line(lines: list[str], marker: str) -> str | None:
    from rapidfuzz import fuzz

    best: tuple[int, str | None] = (0, None)
    for line in lines:
        normalized = _normalize_for_match(line)
        score = max(fuzz.partial_ratio(normalized, marker), fuzz.token_set_ratio(normalized, marker))
        if score >= 70 and score > best[0]:
            best = (score, line)
    return best[1]


def extract_party_name_from_line(line: str) -> str:
    cleaned = " ".join((line or "").split())
    if not cleaned:
        return ""
    prefixes = (
        "Грузоотправитель",
        "Грузополучатель",
        "ГРУЗОПОЛУЧАТЕЛЬ",
        "ГРУЗООТПРАВИТЕЛЬ",
    )
    for prefix in prefixes:
        if prefix in cleaned:
            cleaned = cleaned.split(prefix, 1)[-1]
    if ":" in cleaned:
        cleaned = cleaned.split(":", 1)[-1]
    if "-" in cleaned[:5]:  # allow "Грузоотправитель - ..."
        cleaned = cleaned.split("-", 1)[-1]
    cleaned = cleaned.strip(" -")
    if "," in cleaned:
        cleaned = cleaned.split(",", 1)[0]
    cleaned = " ".join(cleaned.split())
    return cleaned


def _line_from_box(image: Image.Image, box_ratio: Tuple[float, float, float, float]) -> tuple[str | None, str | None]:
    cropped = _crop_by_ratio(image, box_ratio)
    processed = _prepare_line_image(cropped)
    raw_line = _run_tesseract(processed, config="--psm 7").strip()
    if len(raw_line) < MIN_VALID_LENGTH or not any(ch.isalpha() for ch in raw_line):
        return None, None
    name = extract_party_name_from_line(raw_line)
    if len(name) < 5:
        name = None
    return name, raw_line


def _save_debug_artifacts(base_dir: Path, name: str, image: Image.Image, text: str) -> None:
    base_dir.mkdir(parents=True, exist_ok=True)
    image.save(base_dir / f"{name}.png")
    (base_dir / f"{name}.txt").write_text(text, encoding="utf-8")


def extract_parties_waybill(image: Image.Image | "np.ndarray", *, doc_id: str | None = None) -> PartyInfo:
    """
    OCR сторон для белорусской товарной накладной (форма ЮС-1).
    На входе - первая страница документа (RGB/BGR, np.ndarray или PIL.Image).
    На выходе - PartyInfo с полями supplier_text / customer_text.
    """

    pil_image = _ensure_pil(image)

    header_crop = _crop_by_ratio(pil_image, HEADER_BOX_RATIO)
    header_processed = _prepare_line_image(header_crop)
    header_text = _run_tesseract(header_processed, config="--psm 6 --oem 3")
    header_lines = [line for line in (header_text or "").splitlines() if line.strip()]

    supplier_line = _find_party_line(header_lines, "ГРУЗООТПРАВИТЕЛЬ")
    customer_line = _find_party_line(header_lines, "ГРУЗОПОЛУЧАТЕЛЬ")

    supplier_name = extract_party_name_from_line(supplier_line or "") if supplier_line else None
    customer_name = extract_party_name_from_line(customer_line or "") if customer_line else None

    if not supplier_name or len(supplier_name) < 5:
        supplier_name, supplier_line = _line_from_box(pil_image, SUPPLIER_BOX_RATIO)
    if not customer_name or len(customer_name) < 5:
        customer_name, customer_line = _line_from_box(pil_image, CUSTOMER_BOX_RATIO)

    if supplier_name and len(supplier_name) < 5:
        supplier_name = None
    if customer_name and len(customer_name) < 5:
        customer_name = None

    logger.info(
        '[ocr_party_extractor] header_text="%s"',
        " ".join(header_lines),
    )
    logger.info(
        '[ocr_party_extractor] supplier_ocr="%s" customer_ocr="%s"',
        supplier_line,
        customer_line,
    )
    logger.info(
        '[ocr_party_extractor] supplier_name="%s" | customer_name="%s"',
        supplier_name,
        customer_name,
    )

    if os.getenv("DEBUG_PARTIES", "false").lower() == "true":
        base_dir = Path("debug/parties") / (doc_id or "waybill")
        _save_debug_artifacts(base_dir, "header_box", header_crop, header_text)
        _save_debug_artifacts(base_dir, "supplier_box", _crop_by_ratio(pil_image, SUPPLIER_BOX_RATIO), supplier_line or "")
        _save_debug_artifacts(base_dir, "customer_box", _crop_by_ratio(pil_image, CUSTOMER_BOX_RATIO), customer_line or "")

    return PartyInfo(
        supplier_name=supplier_name,
        customer_name=customer_name,
        supplier_tax_id=None,
        customer_tax_id=None,
        supplier_line_ocr=supplier_line or (" ".join(header_lines) if header_lines else None),
        customer_line_ocr=customer_line or (" ".join(header_lines) if header_lines else None),
    )


def _prepare_header(image: Image.Image) -> Image.Image:
    oriented = ImageOps.exif_transpose(image)
    grayscale = ImageOps.grayscale(oriented)
    contrasted = ImageOps.autocontrast(grayscale)
    sharpened = contrasted.filter(ImageFilter.SHARPEN)
    return sharpened


def _normalize_value(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = " ".join(value.split())
    cleaned = cleaned.strip(" \t:;,-")
    return cleaned or None


def _extract_after_keyword(line: str, keywords: Iterable[str]) -> str | None:
    import re

    for key in keywords:
        match = re.search(rf"{key}\s*[:\-]?\s*(.*)", line, re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def _extract_party(lines: list[str], keywords: Iterable[str]) -> str | None:
    for idx, line in enumerate(lines):
        normalized_line = line.strip()
        if not normalized_line:
            continue
        lowered = normalized_line.lower()
        if not any(key in lowered for key in keywords):
            continue
        candidate = _extract_after_keyword(normalized_line, keywords)
        if not candidate and idx + 1 < len(lines):
            continuation = lines[idx + 1].strip()
            if continuation and not any(key in continuation.lower() for key in keywords):
                candidate = continuation
        cleaned = _normalize_value(candidate)
        if cleaned:
            return cleaned
    return None


def extract_parties(image: Image.Image | "np.ndarray", doc_type: str | None = None) -> PartyInfo:
    pil_image = _ensure_pil(image)
    if doc_type == "товарная_накладная":
        return extract_parties_waybill(pil_image)

    prepared = _prepare_header(pil_image)
    header_text = pytesseract.image_to_string(prepared, lang="rus+eng", config="--psm 6")
    lines = [line for line in header_text.splitlines() if line.strip()]

    supplier = _extract_party(lines, ["грузоотправител"])
    customer = _extract_party(lines, ["грузополучател"])

    if supplier and len(supplier) < MIN_VALID_LENGTH:
        supplier = None
    if customer and len(customer) < MIN_VALID_LENGTH:
        customer = None

    logger.info(
        "[ocr_party_extractor] OCR supplier_name=%s, customer_name=%s",
        supplier,
        customer,
    )

    return PartyInfo(
        supplier_name=supplier,
        customer_name=customer,
        supplier_tax_id=None,
        customer_tax_id=None,
        supplier_line_ocr=header_text.strip() or None,
        customer_line_ocr=header_text.strip() or None,
    )


__all__ = [
    "extract_parties_waybill",
    "extract_parties",
    "PartyInfo",
    "OcrParties",
    "extract_party_name_from_line",
    "SUPPLIER_BOX_RATIO",
    "CUSTOMER_BOX_RATIO",
]
