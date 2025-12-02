from datetime import date
from datetime import date
from decimal import Decimal

from models.parser_types import OCRResult
from services import ocr
from services.parser import parse_document


RAW_TEXT = """
СЧЕТ-ФАКТУРА № 77 от 30.10.2025
АКТ оказанных услуг № 44/10 Э от 31.10.2025 года
Поставщик: ООО "Ромашка"
Покупатель: ООО "Тест"
1. ТОВАРНЫЙ РАЗДЕЛ
Услуга аренды шт 1 1 000,00 20% 200,00 1 200,00
"""


def _build_ocr_result(raw_text: str) -> OCRResult:
    normalized = ocr.normalize_ocr_text(raw_text)
    dates = ocr._extract_date_candidates(normalized)  # type: ignore[attr-defined]
    return OCRResult(text=normalized, language="ru", meta={"dates": dates}, file_type="pdf")


def test_act_type_priority_and_date_choice():
    parsed = parse_document(_build_ocr_result(RAW_TEXT))

    assert parsed.doc_type == "акт"
    assert parsed.doc_number and parsed.doc_number.startswith("44/10")
    assert parsed.doc_date == date(2025, 10, 31)
    assert parsed.items and parsed.items[0].total_with_vat == Decimal("1200.00")
