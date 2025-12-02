import importlib.util

import pytest

if importlib.util.find_spec("openai") is None:  # pragma: no cover - optional dependency guard
    pytest.skip("openai is not installed", allow_module_level=True)

from services.vision_extractor import OcrParties, VisionParties, _resolve_parties


def test_merge_uses_vision_when_ocr_missing():
    vision = VisionParties(
        supplier_name="Производственно-торговое частное унитарное предприятие \"Заря-агро\"",
        supplier_tax_id=None,
        customer_name="Борисовский филиал областного унитарного предприятия",
        customer_tax_id=None,
    )

    resolved = _resolve_parties(vision, None, doc_type="товарная_накладная")

    assert "Заря" in (resolved.supplier.display_name or "")
    assert "Борисовский" in (resolved.customer.display_name or "")
    assert resolved.supplier.source == "vision"
    assert resolved.customer.source == "vision"


def test_merge_prefers_ocr_over_vision():
    vision = VisionParties(
        supplier_name="Заря-агро",
        supplier_tax_id=None,
        customer_name="Борисовский отдел образования",
        customer_tax_id=None,
    )
    ocr = OcrParties(
        supplier_name="Производственно-торговое частное унитарное предприятие \"Эдон-92\"",
        supplier_tax_id=None,
        customer_name="Общество с ограниченной ответственностью \"Тигервуд\"",
        customer_tax_id=None,
        header_text="",
    )

    resolved = _resolve_parties(vision, ocr, doc_type="товарная_накладная")

    assert "Эдон" in (resolved.supplier.display_name or "")
    assert "Тигервуд" in (resolved.customer.display_name or "")
    assert resolved.supplier.source == "ocr"
    assert resolved.customer.source == "ocr"
