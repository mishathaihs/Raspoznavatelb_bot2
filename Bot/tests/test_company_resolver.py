import pytest

pytest.importorskip("rapidfuzz")

from services.company_resolver import normalize_company_text_for_match, resolve_parties


def test_resolve_parties_matches_dictionary():
    resolved = resolve_parties(' ООО  "Эдон-92" ', 'ООО   "Тигервуд"   ')
    assert resolved.executor_name.startswith("Производственно-торговое")
    assert resolved.buyer_name.startswith("Общество с ограниченной")
    assert resolved.executor_in_dict is True
    assert resolved.buyer_in_dict is True
    assert resolved.executor_id
    assert resolved.buyer_id
    assert resolved.executor_raw == 'ООО  "Эдон-92"'


def test_normalize_company_text_handles_mixed_scripts():
    noisy = 'FPYSpeRnpasieHO-TOPrOBOe waCTHoE yHUTAPHOE npeanpruate "SnoH-92"'
    normalized = normalize_company_text_for_match(noisy)
    assert "ЭДОН" in normalized


def test_resolve_parties_uses_dict_when_available():
    resolved = resolve_parties(
        '  FPYSpeRnpasieHO-TOPrOBOe waCTHoE yHUTAPHOE npeanpruate "SnoH-92"  ',
        '   ООО   "Тигервуд"   ',
    )
    assert resolved.executor_in_dict is True
    assert resolved.buyer_in_dict is True
    assert resolved.executor_name.startswith("Производственно-торговое")
    assert resolved.buyer_name.startswith("Общество с ограниченной")


def test_resolve_parties_keeps_raw_when_unknown():
    resolved = resolve_parties("Неизвестная Компания", None)
    assert resolved.executor_raw == "Неизвестная Компания"
    assert resolved.executor_name is None
    assert resolved.executor_in_dict is False


def test_resolve_parties_defaults_customer_when_not_found():
    resolved = resolve_parties("Поставщик", "Борисовский филиал областного унитарного предприятия")
    assert resolved.buyer_name.startswith("Общество с ограниченной ответственностью \"Тигервуд\"")
    assert resolved.buyer_id == "TIGERWOOD"
    assert resolved.buyer_in_dict is True
    assert resolved.buyer_raw == "Борисовский филиал областного унитарного предприятия"
