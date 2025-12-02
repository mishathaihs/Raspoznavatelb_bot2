from __future__ import annotations

from datetime import date
from decimal import Decimal

from datetime import date
from decimal import Decimal

from models.parser_types import OCRResult
from services import ocr
from services.parser import parse_document


RAW_TEXT = """
ТОВАРНАЯ НАКЛАДНАЯ 0689789
30.06.2016 № 58 постановление Минфина
Накладная от 21 октября 2025 г.

Грузоотправитель
"Эдон-92"

Грузополучатель
"Тигервуд"

1. ТОВАРНЫЙ РАЗДЕЛ
Штамп (Заказ 123) шт 1 1 195,00 20% 239,00 1 434,00
"""


def _build_ocr_result(raw_text: str) -> OCRResult:
    normalized = ocr.normalize_ocr_text(raw_text)
    dates = ocr._extract_date_candidates(normalized)  # type: ignore[attr-defined]
    return OCRResult(text=normalized, language="ru", meta={"dates": dates}, file_type="pdf")


def test_belarus_invoice_parsing():
    ocr_result = _build_ocr_result(RAW_TEXT)
    parsed = parse_document(ocr_result)

    assert parsed.doc_type == "накладная"
    assert parsed.doc_number == "0689789"
    assert parsed.doc_date == date(2025, 10, 21)
    assert "эдон" in (parsed.supplier or "").lower()
    assert "тигервуд" in (parsed.customer or "").lower()
    assert parsed.items, "At least one item should be parsed"
    assert parsed.items[0].total_with_vat == Decimal("1434.00")


def test_template_date_ignored():
    ocr_result = _build_ocr_result(RAW_TEXT)
    parsed = parse_document(ocr_result)
    assert parsed.doc_date and parsed.doc_date.year == 2025

