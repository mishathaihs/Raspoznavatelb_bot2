import pytest

ImageModule = pytest.importorskip("PIL.Image", reason="Pillow is required")
Image = ImageModule.Image

from services import ocr_party_extractor

pytesseract = pytest.importorskip("pytesseract")


def test_extract_party_name_from_line_parses_prefix():
    line = "Грузоотправитель: Производственно-торговое частное унитарное предприятие \"Эдон-92\", г. Минск"
    result = ocr_party_extractor.extract_party_name_from_line(line)
    assert result.startswith("Производственно-торговое")
    assert "Эдон-92" in result
    assert "," not in result


def test_extract_parties_waybill(monkeypatch):
    header_text = (
        "Грузоотправитель: Производственно-торговое частное унитарное предприятие \"Эдон-92\"\n"
        "Грузополучатель: Общество с ограниченной ответственностью \"Тигервуд\""
    )

    monkeypatch.setattr(
        ocr_party_extractor,
        "_run_tesseract",
        lambda _img, **_kwargs: header_text,
    )

    image = Image.new("RGB", (1090, 1510), color="white")
    info = ocr_party_extractor.extract_parties_waybill(image)

    assert info.supplier_name_raw and "Эдон-92" in info.supplier_name_raw
    assert info.customer_name_raw and "Тигервуд" in info.customer_name_raw


def test_extract_parties_header(monkeypatch):
    fake_header = """
    Грузоотправитель: Производственно-торговое частное унитарное предприятие "Эдон-92"
    Грузополучатель: Общество с ограниченной ответственностью "Тигервуд"
    """

    monkeypatch.setattr(
        ocr_party_extractor.pytesseract,
        "image_to_string",
        lambda *_args, **_kwargs: fake_header,
    )

    image = Image.new("RGB", (800, 600), color="white")
    parties = ocr_party_extractor.extract_parties(image, doc_type="акт")

    assert parties.supplier_name_raw and "Эдон-92" in parties.supplier_name_raw
    assert parties.customer_name_raw and "Тигервуд" in parties.customer_name_raw
    assert parties.supplier_line_ocr
    assert parties.customer_line_ocr
