from __future__ import annotations

import logging
from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    telegram_bot_token: str = Field(..., validation_alias="TELEGRAM_BOT_TOKEN")
    google_auth_mode: Literal["service_account", "oauth"] = Field(
        "oauth", validation_alias="GOOGLE_AUTH_MODE"
    )
    google_oauth_client_secrets_file: str = Field(
        "credentials_drive.json", validation_alias="GOOGLE_OAUTH_CLIENT_SECRETS_FILE"
    )
    google_oauth_token_file: str = Field(
        "token_drive.json", validation_alias="GOOGLE_OAUTH_TOKEN_FILE"
    )
    google_sheets_spreadsheet_id: str = Field(
        "1k67HsXx1_-eXRcErzBSnQjXQfh1bAwr4HGq2NukG-CI",
        validation_alias="GOOGLE_SHEETS_SPREADSHEET_ID",
    )
    google_drive_folder_id: str = Field(..., validation_alias="GOOGLE_DRIVE_FOLDER_ID")
    openai_api_key: str = Field(..., validation_alias="OPENAI_API_KEY")
    openai_vision_model: str = Field("gpt-4.1", validation_alias="OPENAI_VISION_MODEL")
    openai_text_model: str = Field("gpt-4.1-mini", validation_alias="OPENAI_TEXT_MODEL")
    openai_timeout_seconds: int = Field(60, validation_alias="OPENAI_TIMEOUT_SECONDS")
    openai_max_retries: int = Field(2, validation_alias="OPENAI_MAX_RETRIES")
    min_field_confidence: float = Field(
        0.55, validation_alias="MIN_FIELD_CONFIDENCE", description="Минимальная уверенность Vision для поля"
    )
    min_item_confidence: float = Field(
        0.5, validation_alias="MIN_ITEM_CONFIDENCE", description="Минимальная уверенность для позиции"
    )
    log_level: str = Field("INFO", validation_alias="LOG_LEVEL")
    google_sheets_worksheet_title: str = Field(
        "Лист1",
        validation_alias="GOOGLE_SHEETS_WORKSHEET_TITLE",
    )
    allow_sheets_without_drive_file: bool = Field(
        False, validation_alias="ALLOW_SHEETS_WITHOUT_DRIVE_FILE"
    )
    our_org_name: str = Field("", validation_alias="OUR_ORG_NAME")
    our_companies: list[str] = Field(
        default_factory=lambda: [
            "ООО Тигервуд",
            "Общество с ограниченной ответственностью «Тигервуд»",
            "ООО \"Тигервуд\"",
            "OOO Tigerwood",
        ],
        validation_alias="OUR_COMPANIES",
    )
    docx_act_template: str = Field(
        "templates/act_v2.docx", validation_alias="DOCX_ACT_TEMPLATE"
    )
    docx_nakladnaya_template: str = Field(
        "templates/template_waybill.docx", validation_alias="DOCX_NAKLADNAYA_TEMPLATE"
    )
    docx_ttn_template: str = Field(
        "templates/template_waybill.docx", validation_alias="DOCX_TTN_TEMPLATE"
    )
    docx_schet_faktura_template: str = Field(
        "templates/act_v2.docx", validation_alias="DOCX_SCHET_FAKTURA_TEMPLATE"
    )
    docx_schet_protokol_template: str = Field(
        "templates/act_v2.docx", validation_alias="DOCX_SCHET_PROTOKOL_TEMPLATE"
    )
    docx_other_template: str = Field(
        "templates/act_v2.docx", validation_alias="DOCX_OTHER_TEMPLATE"
    )
    enable_layout_docx_copy: bool = Field(
        False, validation_alias="ENABLE_LAYOUT_DOCX_COPY"
    )
    debug_parties: bool = Field(False, validation_alias="DEBUG_PARTIES")

    @model_validator(mode="after")
    def _normalize(self) -> "Settings":
        self.telegram_bot_token = self._clean_env_value(self.telegram_bot_token)
        self.openai_api_key = self._clean_env_value(self.openai_api_key)
        self.google_auth_mode = self._clean_env_value(self.google_auth_mode).lower()
        self.google_oauth_client_secrets_file = self._clean_env_value(
            self.google_oauth_client_secrets_file
        )
        self.google_oauth_token_file = self._clean_env_value(self.google_oauth_token_file)
        self.google_drive_folder_id = self._clean_env_value(self.google_drive_folder_id)
        self.google_sheets_worksheet_title = self._clean_env_value(
            self.google_sheets_worksheet_title
        )
        self.google_sheets_spreadsheet_id = self._extract_spreadsheet_id(
            self._clean_env_value(self.google_sheets_spreadsheet_id)
        )
        self.our_org_name = self._clean_env_value(self.our_org_name)
        if isinstance(self.our_companies, str):
            self.our_companies = [
                part.strip()
                for part in self.our_companies.split(",")
                if part.strip()
            ]
        self.openai_vision_model = self._clean_env_value(self.openai_vision_model)
        self.openai_text_model = self._clean_env_value(self.openai_text_model)

        if ":" not in self.telegram_bot_token:
            raise ValueError(
                "TELEGRAM_BOT_TOKEN must contain ':' (looks like an invalid token from @BotFather)"
            )

        if self.google_auth_mode not in {"service_account", "oauth"}:
            raise ValueError("GOOGLE_AUTH_MODE must be service_account or oauth")

        return self

    @staticmethod
    def _extract_spreadsheet_id(raw: str) -> str:
        value = raw.strip()
        if "/d/" in value:
            value = value.split("/d/")[1]
            value = value.split("/", 1)[0]
        if value.endswith("#gid=0") or value.endswith("/edit"):
            value = value.split("/edit", 1)[0]
            value = value.split("#gid", 1)[0]
        if "gid=" in value:
            value = value.split("#", 1)[0]
            value = value.split("?", 1)[0]
        return value

    @staticmethod
    def _clean_env_value(value: str) -> str:
        return value.strip().strip("'").strip('"')


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()  # type: ignore[call-arg]
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("aiohttp").setLevel(logging.WARNING)
    return settings


__all__ = ["Settings", "get_settings"]
