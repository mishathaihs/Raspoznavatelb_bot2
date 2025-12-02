import importlib.util
import importlib

import pytest

if importlib.util.find_spec("openai") is None:  # pragma: no cover - optional dependency guard
    pytest.skip("openai is not installed", allow_module_level=True)

from services.vision_extractor import (
    PartyCandidate,
    _build_company_code_pool,
    _choose_party,
    _resolve_single_party,
)
from settings.companies import load_company_directory


def test_choose_party_preserves_vision_when_ocr_missing():
    vision = PartyCandidate(name="ПТЧУП \"Заря-агро\"", tax_id=None, source="vision")
    ocr = PartyCandidate(name=None, tax_id=None, source="ocr")

    chosen = _choose_party(vision, ocr)

    assert chosen.display_name == vision.name
    assert chosen.raw_name == vision.name
    assert chosen.source == "vision"


def test_choose_party_prefers_tax_id():
    vision = PartyCandidate(name="ООО \"Видение\"", tax_id=None, source="vision")
    ocr = PartyCandidate(name="ООО \"Ориентир\"", tax_id="123456789", source="ocr")

    chosen = _choose_party(vision, ocr)

    assert chosen.display_name == ocr.name
    assert chosen.raw_name == ocr.name
    assert chosen.tax_id == "123456789"
    assert chosen.source == "ocr"


def test_resolve_single_party_keeps_vision_name_when_no_dict_match():
    directory = load_company_directory()
    pool = _build_company_code_pool(directory)

    resolved = _resolve_single_party(
        vision_name='ПТЧУП "Заря-агро"',
        vision_tax_id=None,
        ocr_name=None,
        ocr_tax_id=None,
        header_text="",
        pool=pool,
        directory=directory,
    )

    assert resolved.display_name is not None
    assert resolved.raw_name is not None
    assert "Заря" in resolved.display_name
