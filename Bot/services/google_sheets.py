from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Optional

import gspread
from gspread import Spreadsheet, Worksheet
from gspread.exceptions import APIError, SpreadsheetNotFound, WorksheetNotFound

from bot.models import ParsedDocument
from config import get_settings
from services.amounts import prefer_text_amount
from services.exceptions import SpreadsheetInitError
from services.google_auth import get_oauth_credentials

SCOPE = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
]

HEADER = [
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


def _safe_str(value: Any, max_len: Optional[int] = None) -> str:
    """
    Convert any value to a safe string for Sheets:
    - None -> ""
    - numbers -> string form
    - other -> str(value)
    Optionally trims to max_len characters.
    """

    if value is None:
        s = ""
    else:
        s = str(value)
    if max_len is not None:
        return s[:max_len]
    return s


class DuplicateInfo:
    def __init__(self, order_number: int, file_id: str | None) -> None:
        self.order_number = order_number
        self.file_id = file_id


class OrderReservation:
    def __init__(self, order_number: int, counter_row_index: int) -> None:
        self.order_number = order_number
        self.counter_row_index = counter_row_index


class GoogleSheetsService:
    COUNTER_SHEET_TITLE = "__order_counter"

    def __init__(self) -> None:
        self._settings = get_settings()
        self._client: gspread.Client | None = None
        self._spreadsheet: Spreadsheet | None = None

    def _build_client(self) -> gspread.Client:
        if self._client is not None:
            return self._client
        if self._settings.google_auth_mode != "oauth":
            raise SpreadsheetInitError("Service account mode is no longer supported")
        creds = get_oauth_credentials(
            scopes=SCOPE,
            client_secrets_file=Path(self._settings.google_oauth_client_secrets_file),
            token_file=Path(self._settings.google_oauth_token_file),
        )
        self._client = gspread.authorize(creds)
        return self._client

    def set_spreadsheet(self, spreadsheet: Spreadsheet) -> None:
        self._spreadsheet = spreadsheet

    async def ensure_initialized(self) -> None:
        await self._ensure_spreadsheet()
        await asyncio.to_thread(self.ensure_sheet_structure)

    async def _ensure_spreadsheet(self) -> Spreadsheet:
        if self._spreadsheet is not None:
            return self._spreadsheet

        def _authorize() -> Spreadsheet:
            if not self._settings.google_sheets_spreadsheet_id:
                raise SpreadsheetInitError(
                    "GOOGLE_SHEETS_SPREADSHEET_ID не задан. Запустите scripts/bootstrap_project.py "
                    "или укажите корректный ID в .env."
                )
            client = self._build_client()
            try:
                logging.info(
                    "Google Sheets: using spreadsheet %s, worksheet '%s'",
                    self._settings.google_sheets_spreadsheet_id,
                    self._settings.google_sheets_worksheet_title,
                )
                return client.open_by_key(self._settings.google_sheets_spreadsheet_id)
            except SpreadsheetNotFound as exc:
                raise SpreadsheetInitError(
                    "Таблица с ID "
                    f"'{self._settings.google_sheets_spreadsheet_id}' не найдена или доступ запрещён. "
                    "Проверьте ID и расшаривание для сервисного аккаунта."
                ) from exc
            except APIError as exc:
                status_code = getattr(getattr(exc, "response", None), "status_code", "?")
                raise SpreadsheetInitError(
                    f"Ошибка Google Sheets API (код {status_code}): {exc}"
                ) from exc

        try:
            self._spreadsheet = await asyncio.to_thread(_authorize)
        except SpreadsheetInitError:
            raise
        except Exception as exc:  # pragma: no cover - remote API
            logging.exception("Unexpected Sheets initialization error")
            raise SpreadsheetInitError("Не удалось подключиться к Google Spreadsheet") from exc
        return self._spreadsheet

    def _ensure_header(self, worksheet: Worksheet) -> None:
        header_row = worksheet.row_values(1)
        if header_row == HEADER:
            return
        if not header_row:
            worksheet.append_row(HEADER)
        else:
            worksheet.update(f"A1:{chr(ord('A') + len(HEADER) - 1)}1", [HEADER])

    def _get_target_worksheet(self, spreadsheet: Spreadsheet) -> Worksheet:
        title = self._settings.google_sheets_worksheet_title
        try:
            worksheet = spreadsheet.worksheet(title)
        except WorksheetNotFound:
            worksheet = spreadsheet.add_worksheet(title=title, rows=100, cols=len(HEADER))
        except Exception as exc:  # pragma: no cover - remote API
            raise SpreadsheetInitError("Не удалось открыть лист Google Spreadsheet") from exc
        self._ensure_header(worksheet)
        return worksheet

    def _ensure_counter_worksheet(self, spreadsheet: Spreadsheet) -> Worksheet:
        try:
            worksheet = spreadsheet.worksheet(self.COUNTER_SHEET_TITLE)
        except WorksheetNotFound:
            worksheet = spreadsheet.add_worksheet(title=self.COUNTER_SHEET_TITLE, rows=2, cols=1)
        except Exception as exc:  # pragma: no cover - remote API
            raise SpreadsheetInitError("Не удалось открыть лист счётчика Google Spreadsheet") from exc
        header = worksheet.row_values(1)
        if not header:
            worksheet.update("A1", [["order_number"]])
        return worksheet

    def ensure_sheet_structure(self) -> None:
        if self._spreadsheet is None:
            raise SpreadsheetInitError("Spreadsheet is not initialized")
        spreadsheet = self._spreadsheet
        worksheet = self._get_target_worksheet(spreadsheet)
        self._ensure_header(worksheet)
        self._ensure_counter_worksheet(spreadsheet)

    async def get_next_order_number(self) -> OrderReservation:
        spreadsheet = await self._ensure_spreadsheet()
        worksheet = await asyncio.to_thread(self._ensure_counter_worksheet, spreadsheet)

        def _reserve() -> OrderReservation:
            result = worksheet.append_row(["=ROW()-1"], value_input_option="USER_ENTERED", table_range="A1:A1")
            updates = result.get("updates", {}) if isinstance(result, dict) else {}
            updated_range = updates.get("updatedRange", "")
            try:
                row_ref = updated_range.split("!")[1].split(":")[0]
                row_number = int("".join(filter(str.isdigit, row_ref)))
            except (IndexError, ValueError):
                row_number = worksheet.row_count
            order_number = max(row_number - 1, 1)
            return OrderReservation(order_number=order_number, counter_row_index=row_number)

        return await asyncio.to_thread(_reserve)

    async def rollback_order_reservation(self, reservation: OrderReservation | None) -> None:
        if reservation is None:
            return

        spreadsheet = await self._ensure_spreadsheet()
        worksheet = await asyncio.to_thread(self._ensure_counter_worksheet, spreadsheet)

        def _rollback() -> None:
            try:
                worksheet.delete_rows(reservation.counter_row_index)
            except Exception:  # pragma: no cover - remote API
                # Best-effort cleanup, ignore rollback failures
                return

        await asyncio.to_thread(_rollback)

    def _format_date(self, value) -> str:
        if not value:
            return ""
        return value.strftime("%d.%m.%Y")

    def _resolve_document_number(self, doc: ParsedDocument) -> str:
        number = (doc.number or "").strip()
        return number

    def _format_amount(self, text: str | None, value) -> str:
        display = prefer_text_amount(text, value)
        if not display:
            return ""
        return str(display).replace(".", ",")

    def _build_drive_link(self, file_id: str | None) -> str:
        if not file_id:
            return ""
        return f"https://drive.google.com/file/d/{file_id}/view?usp=drive_link"

    async def append_document_rows(
        self,
        doc: ParsedDocument,
        internal_doc_id: str,
        order_number: int,
        file_id: str | None = None,
    ) -> None:
        spreadsheet = await self._ensure_spreadsheet()
        worksheet = await asyncio.to_thread(self._get_target_worksheet, spreadsheet)

        header_row = worksheet.row_values(1)
        header_map = {name: idx for idx, name in enumerate(header_row, start=1)}
        document_number = _safe_str(self._resolve_document_number(doc))

        if doc.main_item is not None:
            raw_item_name = getattr(doc.main_item, "name", None)
        else:
            raw_item_name = None

        if (not raw_item_name) and getattr(doc, "items", None):
            raw_item_name = getattr(doc.items[0], "name", None)

        item_name = _safe_str(raw_item_name, max_len=200)
        total_amount = None
        total_amount_text = None
        vat_amount = None
        vat_amount_text = None
        if doc.sums_suspect:
            total_amount = None
            total_amount_text = None
            vat_amount = None
            vat_amount_text = None
        totals = doc.totals or doc.llm_totals
        if totals:
            total_amount = (
                totals.total_with_vat
                or getattr(totals, "amount_with_vat", None)
                or totals.total_without_vat
            )
            total_amount_text = (
                getattr(totals, "total_with_vat_text", None)
                or getattr(totals, "amount_with_vat_text", None)
            )
            vat_amount = totals.vat_amount
            vat_amount_text = getattr(totals, "vat_amount_text", None)
        row_values = ["" for _ in header_row]
        row_data = {
            "Номер по порядку": order_number,
            "Тип документа": _safe_str(doc.doc_type),
            "Дата документа": self._format_date(doc.date),
            "Номер документа": document_number,
            "Контрагент": _safe_str(doc.main_counterparty),
            "Наименование товара/услуги": item_name,
            "Валюта": _safe_str(
                doc.currency or doc.currency_code or getattr(doc.totals, "currency", "UNKNOWN")
            ),
            "Сумма позиции (с НДС)": self._format_amount(total_amount_text, total_amount),
            "Сумма НДС позиции": self._format_amount(vat_amount_text, vat_amount),
            "Ссылка на файл в Drive": self._build_drive_link(file_id),
        }
        for key, value in row_data.items():
            idx = header_map.get(key)
            if idx is None or idx - 1 >= len(row_values):
                continue
            row_values[idx - 1] = value

        await asyncio.to_thread(worksheet.append_row, row_values, value_input_option="USER_ENTERED")

    async def delete_document_rows_by_order_number(self, order_number: int) -> str | None:
        spreadsheet = await self._ensure_spreadsheet()
        worksheet = await asyncio.to_thread(self._get_target_worksheet, spreadsheet)

        def _delete() -> str | None:
            matches = worksheet.findall(str(order_number), in_column=1)
            if not matches:
                return None

            rows = sorted({cell.row for cell in matches}, reverse=True)
            ranges = [f"A{row}:{chr(ord('A') + len(HEADER) - 1)}{row}" for row in rows]
            data_chunks = worksheet.batch_get(ranges) if ranges else []

            header_row = worksheet.row_values(1)
            link_index = header_row.index("Ссылка на файл в Drive") if "Ссылка на файл в Drive" in header_row else None

            file_id = None
            for chunk in data_chunks:
                if not chunk:
                    continue
                row_values = chunk[0]
                if link_index is not None and len(row_values) > link_index and row_values[link_index]:
                    file_id = self._extract_file_id(row_values[link_index])

            requests = [
                {
                    "deleteDimension": {
                        "range": {
                            "sheetId": worksheet.id,
                            "dimension": "ROWS",
                            "startIndex": row - 1,
                            "endIndex": row,
                        }
                    }
                }
                for row in rows
            ]
            if requests:
                worksheet.spreadsheet.batch_update({"requests": requests})

            return file_id

        return await asyncio.to_thread(_delete)

    def _extract_file_id(self, link: str) -> str | None:
        if not link:
            return None
        if "file/d/" in link:
            try:
                return link.split("file/d/")[1].split("/")[0]
            except IndexError:
                return None
        return link

    async def find_duplicate(self, doc: ParsedDocument) -> DuplicateInfo | None:
        spreadsheet = await self._ensure_spreadsheet()
        worksheet = await asyncio.to_thread(self._get_target_worksheet, spreadsheet)
        document_number = self._resolve_document_number(doc)
        formatted_date = self._format_date(doc.date)
        counterparty = (doc.main_counterparty or "").strip()
        if not (document_number and formatted_date and counterparty):
            return None

        def _search() -> DuplicateInfo | None:
            values = worksheet.get_all_values()
            for row in values[1:]:
                if len(row) < 5:
                    continue
                row_order = row[0]
                row_type = row[1]
                row_date = row[2]
                row_number = row[3]
                row_counterparty = row[4]
                if (
                    row_number.strip() == document_number
                    and row_date.strip() == formatted_date
                    and row_counterparty.strip().lower() == counterparty.lower()
                ):
                    file_id = self._extract_file_id(row[9]) if len(row) >= 10 else None
                    try:
                        order_number = int(row_order)
                    except ValueError:
                        continue
                    return DuplicateInfo(order_number=order_number, file_id=file_id)
            return None

        return await asyncio.to_thread(_search)


__all__ = ["GoogleSheetsService", "HEADER", "DuplicateInfo", "OrderReservation"]
