import sys
import types

import pytest

pytest.importorskip("pydantic", reason="pydantic is required for party merge helpers")

sys.modules.setdefault(
    "openai",
    types.SimpleNamespace(
        APIError=Exception,
        APIStatusError=Exception,
        BadRequestError=Exception,
        OpenAI=None,
        RateLimitError=Exception,
    ),
)

from services.ocr_party_extractor import extract_parties_waybill
from services import ocr_party_extractor
from services.vision_extractor import OcrParties, VisionParties, _resolve_parties


def _fake_header_text() -> str:
    return (
        "Грузоотправитель: Производственно-торговое частное унитарное предприятие \"Эдон-92\"\n"
        "Грузополучатель: Общество с ограниченной ответственностью \"Тигервуд\""
    )


def test_doc5_waybill_ocr(monkeypatch):
    class FakeImage:
        size = (1090, 1510)

        def crop(self, _box):
            return self

    image = FakeImage()

    monkeypatch.setattr(ocr_party_extractor, "_run_tesseract", lambda *_args, **_kwargs: _fake_header_text())
    monkeypatch.setattr(ocr_party_extractor, "_prepare_line_image", lambda img, **_kwargs: img)
    monkeypatch.setattr(ocr_party_extractor, "_ensure_pil", lambda img: img)
    monkeypatch.setattr(ocr_party_extractor, "_crop_by_ratio", lambda img, _ratio: img)

    info = extract_parties_waybill(image)

    assert info.supplier_name_raw is not None
    assert info.customer_name_raw is not None
    assert ("Эдон" in info.supplier_name_raw) or ("ЭДОН" in info.supplier_name_raw)
    assert ("Тигервуд" in info.customer_name_raw) or ("ТИГЕРВУД" in info.customer_name_raw)


def test_merge_does_not_use_vision_when_ocr_missing():
    vision = VisionParties(
        supplier_name='Производственно-торговое частное унитарное предприятие "Заря-агро"',
        supplier_tax_id=None,
        customer_name="Борисовский филиал областного унитарного предприятия",
        customer_tax_id=None,
    )
    ocr = OcrParties(
        supplier_name=None,
        supplier_tax_id=None,
        customer_name=None,
        customer_tax_id=None,
        header_text="",
    )

    resolved = _resolve_parties(vision, ocr, doc_type="товарная_накладная")

    assert resolved.supplier.display_name is None
    assert resolved.customer.display_name is None


def test_merge_prefers_ocr_over_vision():
    vision = VisionParties(
        supplier_name='Производственно-торговое частное унитарное предприятие "Заря-агро"',
        supplier_tax_id=None,
        customer_name="Борисовский филиал областного унитарного предприятия",
        customer_tax_id=None,
    )
    ocr = OcrParties(
        supplier_name='Производственно-торговое частное унитарное предприятие "Эдон-92"',
        supplier_tax_id=None,
        customer_name='Общество с ограниченной ответственностью "Тигервуд"',
        customer_tax_id=None,
        header_text="",
    )

    resolved = _resolve_parties(vision, ocr, doc_type="товарная_накладная")

    assert resolved.supplier.display_name == ocr.supplier_name
    assert resolved.customer.display_name == ocr.customer_name
