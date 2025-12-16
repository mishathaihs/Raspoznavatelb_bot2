from __future__ import annotations

import logging
import re
import threading
from datetime import datetime, timedelta
from typing import Any, List

import gspread

from config import get_settings
from services.google_auth import get_oauth_credentials

logger = logging.getLogger(__name__)

AGENT_SCOPE = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]


class AgentsDirectory:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._client: gspread.Client | None = None
        self._cached: list[dict[str, Any]] = []
        self._expires_at: datetime | None = None

    def _build_client(self) -> gspread.Client:
        if self._client is not None:
            return self._client
        settings = get_settings()
        creds = get_oauth_credentials(
            scopes=AGENT_SCOPE,
            client_secrets_file=settings.google_oauth_client_secrets_file,
            token_file=settings.google_oauth_token_file,
        )
        self._client = gspread.authorize(creds)
        return self._client

    def _slugify(self, value: str) -> str:
        cleaned = re.sub(r"[^A-Za-z0-9А-Яа-я]+", "_", value).strip("_")
        cleaned = re.sub(r"_+", "_", cleaned)
        if not cleaned:
            cleaned = "AGENT"
        return cleaned.upper()

    def _parse_rows(self, rows: List[List[str]]) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        if not rows:
            return entries

        header_present = any(cell.strip() for cell in rows[0])
        data_rows = rows[1:] if header_present else rows

        for row in data_rows:
            cells = [cell.strip() for cell in row if cell and cell.strip()]
            if not cells:
                continue
            canonical = cells[0]
            aliases = cells[1:]
            entries.append(
                {
                    "canonical": canonical,
                    "aliases": aliases,
                    "code": self._slugify(canonical),
                    "tax_id": None,
                }
            )
        return entries

    def _fetch(self) -> list[dict[str, Any]]:
        settings = get_settings()
        spreadsheet_id = settings.agents_spreadsheet_id or settings.google_sheets_spreadsheet_id
        worksheet_title = settings.agents_worksheet_title
        client = self._build_client()
        try:
            spreadsheet = client.open_by_key(spreadsheet_id)
            worksheet = spreadsheet.worksheet(worksheet_title)
            rows = worksheet.get_all_values()
        except Exception as exc:  # pragma: no cover - remote API
            logger.warning("Failed to pull agents from sheet %s/%s: %s", spreadsheet_id, worksheet_title, exc)
            return []
        return self._parse_rows(rows)

    def get_agents(self) -> list[dict[str, Any]]:
        settings = get_settings()
        now = datetime.utcnow()
        with self._lock:
            if self._expires_at and self._expires_at > now and self._cached:
                return list(self._cached)
            entries = self._fetch()
            self._cached = entries
            self._expires_at = now + timedelta(seconds=max(settings.agents_cache_seconds, 30))
            return list(self._cached)


_singleton: AgentsDirectory | None = None


def get_agents_directory() -> AgentsDirectory:
    global _singleton
    if _singleton is None:
        _singleton = AgentsDirectory()
    return _singleton


__all__ = ["get_agents_directory", "AgentsDirectory"]
