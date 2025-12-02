from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .google_drive import GoogleDriveClient
    from .google_sheets import GoogleSheetsService
    from .vision_extractor import VisionExtractor


@lru_cache(maxsize=1)
def get_sheets_service() -> GoogleSheetsService:
    """Return a cached instance of :class:`GoogleSheetsService`."""
    from .google_sheets import GoogleSheetsService

    return GoogleSheetsService()


@lru_cache(maxsize=1)
def get_drive_service() -> GoogleDriveClient:
    """Return a cached instance of :class:`GoogleDriveClient`."""
    from .google_drive import GoogleDriveClient

    return GoogleDriveClient()


@lru_cache(maxsize=1)
def get_vision_extractor() -> VisionExtractor:
    """Return a cached instance of :class:`VisionExtractor`."""
    from .vision_extractor import get_vision_extractor as _get

    return _get()


__all__ = ["get_drive_service", "get_sheets_service", "get_vision_extractor"]
