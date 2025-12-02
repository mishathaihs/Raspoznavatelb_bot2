from datetime import date

import pytest

from bot.models import Item, ParsedDocument
from services.google_sheets import GoogleSheetsService, _safe_str


def test_safe_str_basic():
    assert _safe_str(None) == ""
    assert _safe_str("") == ""
    assert _safe_str("abc") == "abc"
    assert _safe_str(123) == "123"
    assert _safe_str(12.34) == "12.34"
    assert _safe_str("long", max_len=2) == "lo"


class _FakeWorksheet:
    def __init__(self):
        self.appended = []

    def row_values(self, _row: int):
        return [
            "Номер по порядку",
            "Тип документа",
            "Дата документа",
            "Номер документа",
            "Контрагент",
            "Наименование товара/услуги",
            "Валюта",
            "Сумма позиции (с НДС)",
            "Сумма НДС позиции",
            "Ссылка на файл в Drive",
        ]

    def append_row(self, values, value_input_option=None):  # noqa: ARG002
        self.appended.append(values)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio("asyncio")
async def test_append_document_rows_with_missing_item_name(monkeypatch):
    worksheet = _FakeWorksheet()
    service = GoogleSheetsService()

    async def _fake_ensure_spreadsheet():  # noqa: D401
        return object()

    def _fake_get_target_worksheet(_spreadsheet):  # noqa: D401
        return worksheet

    monkeypatch.setattr(service, "_ensure_spreadsheet", _fake_ensure_spreadsheet)
    monkeypatch.setattr(service, "_get_target_worksheet", _fake_get_target_worksheet)

    doc = ParsedDocument(
        doc_type="акт",
        number="7",
        date=date(2024, 5, 1),
        main_counterparty=None,
        main_item=Item(name=None),
        items=[Item(name=None)],
    )

    await service.append_document_rows(doc, internal_doc_id="x", order_number=1, file_id=None)

    assert worksheet.appended, "Row should be appended even with missing names"
    appended_row = worksheet.appended[0]
    # Item name column index 5 (0-based) according to HEADER ordering
    assert appended_row[5] == ""
