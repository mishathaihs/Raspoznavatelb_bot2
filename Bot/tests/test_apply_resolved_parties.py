import importlib.util

import pytest

if importlib.util.find_spec("pydantic") is None:  # pragma: no cover - optional dependency guard
    pytest.skip("pydantic is not installed", allow_module_level=True)

from bot.models import ParsedDocument
from services.vision_extractor import ResolvedParties, ResolvedParty, _apply_resolved_parties
from settings.companies import Company


def _make_doc():
    return ParsedDocument(doc_type="товарная_накладная")


def test_apply_resolved_uses_canonical_when_available():
    company = Company(canonical="Каноническое имя", aliases=[], code="CODE", tax_id="123456789")
    supplier = ResolvedParty(
        raw_name="Исходное", display_name="Отображаемое", tax_id="987654321", dict_company=company, source="dict"
    )
    customer = ResolvedParty(raw_name=None, display_name=None, tax_id=None, dict_company=None, source="none")
    doc = _make_doc()

    _apply_resolved_parties(doc, ResolvedParties(supplier=supplier, customer=customer), header_text=None)

    assert doc.executor == "Каноническое имя"
    assert doc.supplier_company_name == "Каноническое имя"
    assert "executor_from_vision_not_in_dict" not in doc.warnings


def test_apply_resolved_keeps_display_when_no_dictionary_entry():
    supplier = ResolvedParty(
        raw_name="Поставщик", display_name="Поставщик", tax_id=None, dict_company=None, source="vision_raw"
    )
    customer = ResolvedParty(
        raw_name="Покупатель", display_name="Покупатель", tax_id=None, dict_company=None, source="vision_raw"
    )
    doc = _make_doc()

    _apply_resolved_parties(doc, ResolvedParties(supplier=supplier, customer=customer), header_text=None)

    assert doc.executor == "Поставщик"
    assert doc.contractor == "Покупатель"
    assert "executor_from_vision_not_in_dict" in doc.warnings
    assert "customer_from_vision_not_in_dict" in doc.warnings


def test_apply_resolved_handles_missing_parties():
    doc = _make_doc()

    _apply_resolved_parties(doc, None, header_text=None)

    assert "parties_not_resolved" in doc.warnings
    assert doc.executor is None
    assert doc.contractor is None
