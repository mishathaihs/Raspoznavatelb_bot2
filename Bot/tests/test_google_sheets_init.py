from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from gspread.exceptions import APIError, SpreadsheetNotFound

from services.google_sheets import GoogleSheetsService
from services.exceptions import SpreadsheetInitError
from scripts.bootstrap_project import _create_or_open_spreadsheet


@pytest.fixture(scope="module")
def anyio_backend():
    return "asyncio"


class _FakeWorksheet:
    def __init__(self) -> None:
        self.rows_appended = []

    def row_values(self, _index: int):
        return []

    def append_row(self, values, **_kwargs):
        self.rows_appended.append(values)


class _FakeSpreadsheet:
    def __init__(self, spreadsheet_id: str, title: str = "Test") -> None:
        self.id = spreadsheet_id
        self.title = title
        self.worksheets = {}

    def worksheet(self, _title: str):  # pragma: no cover - not used in these tests
        raise SpreadsheetNotFound()

    def add_worksheet(self, title: str, rows: int, cols: int):  # pragma: no cover
        ws = _FakeWorksheet()
        self.worksheets[title] = ws
        return ws


class _FakeClient:
    def __init__(self, *, spreadsheet: _FakeSpreadsheet | None = None, not_found: bool = False, api_error: APIError | None = None):
        self.spreadsheet = spreadsheet or _FakeSpreadsheet("EXISTING")
        self.not_found = not_found
        self.api_error = api_error
        self.open_calls = 0
        self.create_calls = 0

    def open_by_key(self, _key: str):
        self.open_calls += 1
        if self.api_error:
            raise self.api_error
        if self.not_found:
            raise SpreadsheetNotFound()
        return self.spreadsheet

    def create(self, title: str):
        self.create_calls += 1
        self.spreadsheet = _FakeSpreadsheet(f"NEW_{self.create_calls}", title)
        return self.spreadsheet


@pytest.mark.anyio("asyncio")
async def test_ensure_spreadsheet_success(monkeypatch):
    settings = SimpleNamespace(
        google_auth_mode="oauth",
        google_oauth_client_secrets_file="client.json",
        google_oauth_token_file="token.json",
        google_sheets_spreadsheet_id="EXISTING",
        google_sheets_worksheet_title="Лист1",
    )
    client = _FakeClient()

    monkeypatch.setattr("services.google_sheets.get_settings", lambda: settings)
    monkeypatch.setattr("services.google_sheets.get_oauth_credentials", lambda **_: "creds")
    monkeypatch.setattr("services.google_sheets.gspread.authorize", lambda _creds: client)
    monkeypatch.setattr(GoogleSheetsService, "ensure_sheet_structure", lambda self: None)

    service = GoogleSheetsService()
    await service.ensure_initialized()
    await service.ensure_initialized()  # cached branch

    assert service._spreadsheet is client.spreadsheet
    assert client.open_calls == 1


@pytest.mark.anyio("asyncio")
async def test_ensure_spreadsheet_not_found(monkeypatch):
    settings = SimpleNamespace(
        google_auth_mode="oauth",
        google_oauth_client_secrets_file="client.json",
        google_oauth_token_file="token.json",
        google_sheets_spreadsheet_id="BAD_ID",
        google_sheets_worksheet_title="Лист1",
    )
    client = _FakeClient(not_found=True)

    monkeypatch.setattr("services.google_sheets.get_settings", lambda: settings)
    monkeypatch.setattr("services.google_sheets.get_oauth_credentials", lambda **_: "creds")
    monkeypatch.setattr("services.google_sheets.gspread.authorize", lambda _creds: client)
    monkeypatch.setattr(GoogleSheetsService, "ensure_sheet_structure", lambda self: None)

    service = GoogleSheetsService()
    with pytest.raises(SpreadsheetInitError) as excinfo:
        await service.ensure_initialized()

    assert "BAD_ID" in str(excinfo.value)


@pytest.mark.anyio("asyncio")
async def test_bootstrap_creates_when_missing_id(monkeypatch):
    settings = SimpleNamespace(
        google_sheets_spreadsheet_id="",
        google_sheets_worksheet_title="Реестр входящих документов",
    )
    client = _FakeClient()

    class FakeSheets:
        def __init__(self):
            self._spreadsheet = None

        def _build_client(self):
            return client

        def set_spreadsheet(self, spreadsheet):
            self._spreadsheet = spreadsheet

        async def ensure_initialized(self):
            return None

    sheets = FakeSheets()
    spreadsheet_id, created = await _create_or_open_spreadsheet(sheets, settings)

    assert created is True
    assert spreadsheet_id.startswith("NEW_")
    assert client.create_calls == 1


@pytest.mark.anyio("asyncio")
async def test_bootstrap_recreates_on_not_found(monkeypatch):
    settings = SimpleNamespace(
        google_sheets_spreadsheet_id="OLD_ID",
        google_sheets_worksheet_title="SheetTitle",
    )
    client = _FakeClient(not_found=True)

    class FakeSheets:
        def __init__(self):
            self._spreadsheet = None

        def _build_client(self):
            return client

        def set_spreadsheet(self, spreadsheet):
            self._spreadsheet = spreadsheet

        async def ensure_initialized(self):
            return None

    sheets = FakeSheets()
    spreadsheet_id, created = await _create_or_open_spreadsheet(sheets, settings)

    assert created is True
    assert spreadsheet_id.startswith("NEW_")
    assert client.create_calls == 1


@pytest.mark.anyio("asyncio")
async def test_bootstrap_api_error(monkeypatch):
    class _FakeResponse:
        status_code = 500
        text = "boom"
        content = b"boom"
        url = "http://example.com"
        headers = {}

    settings = SimpleNamespace(
        google_sheets_spreadsheet_id="BAD_ID",
        google_sheets_worksheet_title="SheetTitle",
    )
    api_error = APIError(_FakeResponse())
    client = _FakeClient(api_error=api_error)

    class FakeSheets:
        def __init__(self):
            self._spreadsheet = None

        def _build_client(self):
            return client

        def set_spreadsheet(self, spreadsheet):
            self._spreadsheet = spreadsheet

        async def ensure_initialized(self):
            return None

    sheets = FakeSheets()
    with pytest.raises(SpreadsheetInitError):
        await _create_or_open_spreadsheet(sheets, settings)
