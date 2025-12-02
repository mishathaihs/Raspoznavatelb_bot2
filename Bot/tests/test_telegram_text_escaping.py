from decimal import Decimal

import pytest

pytest.importorskip("pdfplumber")

from bot.handlers import _build_summary
from bot.models import ParsedDocument, Totals


def test_summary_escaping_no_fake_tags():
    doc = ParsedDocument(
        doc_type="товарная_накладная",
        number="1",
        supplier_raw=None,
        customer_raw=None,
        totals=Totals(
            total_without_vat=Decimal("0"),
            vat_amount=Decimal("0"),
            total_with_vat=Decimal("0"),
        ),
        items=[],
    )

    text = _build_summary(doc, order_number=1, file_saved=True)

    assert "<не" not in text.lower()
    assert "не распознан" in text
