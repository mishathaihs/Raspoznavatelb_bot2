from __future__ import annotations

import asyncio
import logging
import mimetypes
from pathlib import Path

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseUpload

from config import get_settings
from services.exceptions import DriveQuotaExceededError, DriveUploadError
from services.google_auth import get_oauth_credentials

logger = logging.getLogger(__name__)

DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive.file"]


class GoogleDriveClient:
    def __init__(self) -> None:
        self._settings = get_settings()
        self._service = self._build_service()
        self._root_folder_id = None
        self._originals_folder_id = None
        self._generated_folder_id = None
        self.ensure_drive_structure()

    def _build_service(self):
        if self._settings.google_auth_mode == "oauth":
            creds = get_oauth_credentials(
                scopes=DRIVE_SCOPES,
                client_secrets_file=Path(self._settings.google_oauth_client_secrets_file),
                token_file=Path(self._settings.google_oauth_token_file),
            )
        else:
            raise RuntimeError("Service account mode is no longer supported")

        return build("drive", "v3", credentials=creds)

    def ensure_drive_structure(self) -> None:
        try:
            root_id = self._ensure_folder(self._settings.google_drive_folder_id, "Raspoznavatelb")
            originals_id = self._find_or_create_child(root_id, "originals")
            generated_id = self._find_or_create_child(root_id, "generated")
            self._root_folder_id = root_id
            self._originals_folder_id = originals_id
            self._generated_folder_id = generated_id
        except Exception:  # pragma: no cover - remote API
            logger.exception("Failed to ensure Drive structure")
            self._root_folder_id = self._settings.google_drive_folder_id

    def _ensure_folder(self, parent_id: str, name: str) -> str:
        if parent_id:
            return parent_id
        query = "mimeType='application/vnd.google-apps.folder' and name='Raspoznavatelb' and 'root' in parents"
        result = self._service.files().list(q=query, spaces="drive", fields="files(id,name)").execute()
        files = result.get("files", [])
        if files:
            return files[0]["id"]
        folder_metadata = {"name": name, "mimeType": "application/vnd.google-apps.folder"}
        file = self._service.files().create(body=folder_metadata, fields="id").execute()
        return file["id"]

    def _find_or_create_child(self, parent_id: str, name: str) -> str:
        query = (
            "mimeType='application/vnd.google-apps.folder' and "
            f"name='{name}' and '{parent_id}' in parents"
        )
        result = self._service.files().list(q=query, spaces="drive", fields="files(id,name)").execute()
        files = result.get("files", [])
        if files:
            return files[0]["id"]
        metadata = {
            "name": name,
            "mimeType": "application/vnd.google-apps.folder",
            "parents": [parent_id],
        }
        created = self._service.files().create(body=metadata, fields="id").execute()
        return created["id"]

    def _resolve_parent_folder(self, target: str) -> str | None:
        if target == "originals":
            return self._originals_folder_id or self._settings.google_drive_folder_id
        if target == "generated":
            return self._generated_folder_id or self._settings.google_drive_folder_id
        return self._settings.google_drive_folder_id

    async def upload_generated(self, path: Path, filename: str) -> str:
        return await asyncio.to_thread(self._upload_sync, path, filename, target="generated")

    async def upload_original(self, path: Path, filename: str) -> str:
        return await asyncio.to_thread(self._upload_sync, path, filename, target="originals")

    def _upload_sync(self, path: Path, filename: str, target: str = "generated") -> str:
        mime_type, _ = mimetypes.guess_type(filename)
        mime_type = mime_type or "application/octet-stream"
        parent_id = self._resolve_parent_folder(target)
        metadata = {"name": filename, "parents": [parent_id] if parent_id else None}

        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                with path.open("rb") as f:
                    media = MediaIoBaseUpload(f, mimetype=mime_type, resumable=False)
                    file = (
                        self._service.files()
                        .create(body=metadata, media_body=media, fields="id")
                        .execute()
                    )
                return file["id"]
            except HttpError as exc:  # pragma: no cover - depends on remote API
                last_exc = exc
                if attempt < 2:
                    logger.warning("Drive upload failed (attempt %s/3): %s", attempt + 1, exc)
                    continue
                _handle_http_error(exc)
            except Exception as exc:  # pragma: no cover - defensive
                last_exc = exc
                if attempt < 2:
                    logger.warning("Drive upload failed (attempt %s/3): %s", attempt + 1, exc)
                    continue
                raise
        if last_exc:
            raise last_exc
        raise DriveUploadError("Не удалось загрузить файл в Google Drive")

    async def delete_file(self, file_id: str) -> None:
        if not file_id:
            return

        def _delete():
            try:
                self._service.files().delete(fileId=file_id).execute()
            except HttpError as exc:  # pragma: no cover - depends on remote API
                if exc.resp.status == 404:
                    logger.warning("File %s not found in Drive", file_id)
                else:
                    _handle_http_error(exc)

        await asyncio.to_thread(_delete)


def _handle_http_error(exc: HttpError) -> None:
    message = getattr(exc, "_get_reason", lambda: "")() or str(exc)
    if exc.resp.status == 403 and (
        "storageQuotaExceeded" in message or "Service Accounts do not have storage quota" in message
    ):
        logger.error("Drive quota exceeded: %s", message)
        raise DriveQuotaExceededError(message)
    raise DriveUploadError(message)


__all__ = ["GoogleDriveClient"]
