from decimal import Decimal

from services.amounts import format_amount_ru, parse_amount, prefer_text_amount


def test_parse_amount_variants():
    cases = {
        "701,94": Decimal("701.94"),
        "842,33": Decimal("842.33"),
        "140,39": Decimal("140.39"),
        "0,39479": Decimal("0.39479"),
        "70,94": Decimal("70.94"),
        "85,33": Decimal("85.33"),
        "1 234,56": Decimal("1234.56"),
        "12 345 678,90": Decimal("12345678.90"),
    }

    for raw, expected in cases.items():
        assert parse_amount(raw) == expected


def test_format_amount_ru():
    assert format_amount_ru(Decimal("701.94")) == "701,94"
    assert format_amount_ru(None) == ""
    assert format_amount_ru(0) == "0,00"


def test_prefer_text_amount():
    assert prefer_text_amount("0,39479", Decimal("0.39479")) == "0,39479"
    assert prefer_text_amount("", Decimal("1.10")) == "1,10"
