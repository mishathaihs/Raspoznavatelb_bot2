from __future__ import annotations

from pathlib import Path
from typing import Sequence

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.exceptions import RefreshError


def get_oauth_credentials(
    scopes: Sequence[str],
    client_secrets_file: Path,
    token_file: Path,
) -> Credentials:
    """
    Возвращает валидные OAuth2 credentials.
    Если token_file отсутствует или просрочен без refresh_token —
    запускает InstalledAppFlow.run_local_server() и сохраняет токен.
    """

    creds: Credentials | None = None

    if token_file.exists():
        creds = Credentials.from_authorized_user_file(str(token_file), scopes=scopes)
        # Если токен сохранён с устаревшими или неполными scope, удаляем и переинициализируем ниже
        missing_scopes = set(scopes) - set(creds.scopes or [])
        if missing_scopes:
            token_file.unlink(missing_ok=True)
            creds = None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except RefreshError as exc:
                # invalid_scope или похожие ошибки – сбрасываем токен и запускаем полное согласование
                token_file.unlink(missing_ok=True)
                creds = None
                if "invalid_scope" not in str(exc):
                    raise

        if not creds or not creds.valid:
            flow = InstalledAppFlow.from_client_secrets_file(
                str(client_secrets_file),
                scopes=scopes,
            )
            creds = flow.run_local_server(port=0)
        token_file.write_text(creds.to_json(), encoding="utf-8")

    return creds


__all__ = ["get_oauth_credentials"]
