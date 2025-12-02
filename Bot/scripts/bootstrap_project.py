from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

ROOT_DIR = os.path.dirname(os.path.dirname(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from config import get_settings
from gspread.exceptions import APIError, SpreadsheetNotFound
from services import get_drive_service, get_sheets_service
from services.exceptions import SpreadsheetInitError
from scripts import init_templates


async def _create_or_open_spreadsheet(sheets, settings):
    client = sheets._build_client()  # uses cached OAuth creds
    title = settings.google_sheets_worksheet_title or "Реестр входящих документов"
    spreadsheet_id = settings.google_sheets_spreadsheet_id
    created_new = False

    if spreadsheet_id:
        try:
            spreadsheet = await asyncio.to_thread(client.open_by_key, spreadsheet_id)
            print(f"Google Sheets: таблица найдена, использую существующую (ID: {spreadsheet_id}).")
        except SpreadsheetNotFound:
            print(
                "Google Sheets: таблица с указанным ID не найдена или нет доступа. "
                "Создаю новую таблицу с тем же именем."
            )
            spreadsheet = await asyncio.to_thread(client.create, title)
            created_new = True
            print(
                f"Новая таблица создана: '{spreadsheet.title}' (ID: {spreadsheet.id}). "
                f"Старый ID: {spreadsheet_id}"
            )
        except APIError as exc:
            status_code = getattr(getattr(exc, "response", None), "status_code", "?")
            raise SpreadsheetInitError(
                f"Ошибка Google Sheets API (код {status_code}): {exc}"
            ) from exc
    else:
        spreadsheet = await asyncio.to_thread(client.create, title)
        created_new = True
        print(f"Создана таблица Google Sheets: '{spreadsheet.title}' (ID: {spreadsheet.id}).")

    sheets.set_spreadsheet(spreadsheet)
    await sheets.ensure_initialized()
    return spreadsheet.id, created_new


def _update_env_file(env_path: Path, spreadsheet_id: str) -> bool:
    if not env_path.exists():
        return False

    updated = False
    lines = env_path.read_text(encoding="utf-8").splitlines()
    for idx, line in enumerate(lines):
        if line.startswith("GOOGLE_SHEETS_SPREADSHEET_ID="):
            lines[idx] = f"GOOGLE_SHEETS_SPREADSHEET_ID={spreadsheet_id}"
            updated = True
            break

    if not updated:
        lines.append(f"GOOGLE_SHEETS_SPREADSHEET_ID={spreadsheet_id}")
        updated = True

    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return True


def main() -> None:
    settings = get_settings()
    sheets = get_sheets_service()
    drive = get_drive_service()

    try:
        spreadsheet_id, created_new = asyncio.run(
            _create_or_open_spreadsheet(sheets, settings)
        )
        print("Google Sheets: структура проверена/обновлена.")
        if created_new:
            updated_env = _update_env_file(Path(".env"), spreadsheet_id)
            if updated_env:
                print("Файл .env обновлён новым GOOGLE_SHEETS_SPREADSHEET_ID.")
            else:
                print(
                    "Скопируйте этот ID в переменную GOOGLE_SHEETS_SPREADSHEET_ID в .env: "
                    f"{spreadsheet_id}"
                )
    except SpreadsheetInitError as exc:
        print(
            "Google Sheets: таблица не инициализирована "
            f"({exc}). Если таблица уже создана и ID/название листа указаны в .env, можно пропустить этот шаг."
        )
        return

    drive.ensure_drive_structure()
    print("Проверена/создана структура Google Drive…")

    init_templates.main()
    print("Сгенерированы шаблоны .docx…")


if __name__ == "__main__":
    main()
