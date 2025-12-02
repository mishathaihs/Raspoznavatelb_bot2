from __future__ import annotations

import logging
import sys
from pathlib import Path

# Ensure project root is on sys.path when running the script directly from scripts/
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import get_settings
from services.google_auth import get_oauth_credentials

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
]


if __name__ == "__main__":
    logging.info("Starting OAuth flow for Google Drive/Sheets. A browser window will open for authorization.")
    try:
        settings = get_settings()
        get_oauth_credentials(
            scopes=SCOPES,
            client_secrets_file=Path(settings.google_oauth_client_secrets_file),
            token_file=Path(settings.google_oauth_token_file),
        )
        logging.info("OAuth credentials saved to %s", settings.google_oauth_token_file)
    except Exception as exc:  # pragma: no cover - interactive flow
        logging.exception("Failed to complete OAuth flow: %s", exc)
