from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramUnauthorizedError

from bot.handlers import router
from config import get_settings
from services import get_sheets_service
from services.exceptions import SpreadsheetInitError


async def main() -> None:
    settings = get_settings()
    missing = []
    if not settings.google_sheets_spreadsheet_id:
        missing.append("GOOGLE_SHEETS_SPREADSHEET_ID")
    if not settings.google_drive_folder_id:
        missing.append("GOOGLE_DRIVE_FOLDER_ID")
    if missing:
        logging.error(
            "Missing required configuration variables: %s. "
            "Заполните .env (можно использовать scripts/bootstrap_project.py для создания таблицы).",
            ", ".join(missing),
        )
        return

    sheets = get_sheets_service()
    try:
        await sheets.ensure_initialized()
    except SpreadsheetInitError as exc:
        logging.error(
            "Не удалось инициализировать Google Sheets: %s. "
            "Проверьте GOOGLE_SHEETS_SPREADSHEET_ID и доступы, затем перезапустите бота.",
            exc,
            exc_info=True,
        )
        return

    bot = Bot(token=settings.telegram_bot_token, parse_mode=ParseMode.HTML)
    dp = Dispatcher()
    dp.include_router(router)

    try:
        token_fp = f"{settings.telegram_bot_token[:4]}...{settings.telegram_bot_token[-4:]}"
        logging.info("Using Telegram bot token fingerprint: %s", token_fp)
        # Pre-flight validation to fail fast on invalid or revoked token before polling
        await bot.get_me()
        await dp.start_polling(bot)
    except TelegramUnauthorizedError as exc:
        logging.error(
            "Failed to start bot: unauthorized. Please verify TELEGRAM_BOT_TOKEN is correct and not revoked via @BotFather.",
            exc_info=exc,
        )
        return


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Bot stopped")
