from __future__ import annotations

from pathlib import Path
from typing import Sequence

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow


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

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                str(client_secrets_file),
                scopes=scopes,
            )
            creds = flow.run_local_server(port=0)
        token_file.write_text(creds.to_json(), encoding="utf-8")

    return creds


__all__ = ["get_oauth_credentials"]
