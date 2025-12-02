from decimal import Decimal

import pytest

pytest.importorskip("pydantic")

from bot.models import Item, Totals
from services.vision_extractor import _postprocess_items


def test_merge_duplicate_items():
    items = [
        Item(name="Товар", price=Decimal("10"), quantity=Decimal("1"), total_with_vat=Decimal("10")),
        Item(name="Товар", price=Decimal("10"), quantity=Decimal("2"), total_with_vat=Decimal("20")),
        Item(name="Товар", price=Decimal("10"), quantity=Decimal("3"), total_with_vat=Decimal("30")),
    ]
    totals = Totals(total_with_vat=Decimal("60"))

    merged = _postprocess_items(items, totals)

    assert len(merged) == 1
    assert merged[0].quantity == Decimal("6")
    assert merged[0].total_with_vat == Decimal("60")
