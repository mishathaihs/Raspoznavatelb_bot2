from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any, Dict

from openai import AsyncOpenAI

from config import get_settings
from models.types import DocumentData, DocumentItem

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "Ты профессиональный бухгалтер. Тебе даётся текст документа (акт выполненных работ "
    "или товарная накладная). Нужно определить тип документа (допустимые значения: 'Акт' "
    "или 'Накладная') и извлечь структурированные данные. Ответь строго валидным JSON в "
    "следующем формате:"
    '{"document_type": "тип", "document_number": "строка", '
    '"document_date": "YYYY-MM-DD или null", "counterparty": "строка", '
    '"document_total": число или null, "vat_total": число или null, '
    '"items": [{"name": "строка", "amount": число или null, "vat_amount": число или null}]}'
)

USER_PROMPT_TEMPLATE = (
    "Исходный текст документа:\n"\
    "{text}\n\n"
    "Если данные невозможно определить, укажи null или пустую строку. "
    "Верни только один JSON-объект без комментариев и без пояснений."
)


async def _request_model(text: str) -> Dict[str, Any]:
    settings = get_settings()
    client = AsyncOpenAI(api_key=settings.openai_api_key)
    response = await client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": USER_PROMPT_TEMPLATE.format(text=text)},
        ],
        temperature=0,
    )
    content = response.choices[0].message.content
    if not content:
        raise ValueError("Empty response from LLM")
    content = content.strip()
    fenced_match = re.search(r"```(json)?\s*(?P<body>\{.*\})```", content, re.DOTALL | re.IGNORECASE)
    if fenced_match:
        content = fenced_match.group("body").strip()
    else:
        json_match = re.search(r"\{.*\}", content, re.DOTALL)
        if json_match:
            content = json_match.group(0).strip()
    try:
        return json.loads(content)
    except json.JSONDecodeError as exc:  # pragma: no cover - depends on remote API
        logger.error("Failed to parse JSON from LLM: %s", content)
        raise ValueError("LLM returned invalid JSON") from exc


async def parse_document_text(text: str) -> DocumentData:
    raw = await _request_model(text)

    items = []
    for item in raw.get("items", []) or []:
        name = (item or {}).get("name") or ""
        items.append(
            DocumentItem(
                name=name,
                amount=_safe_float(item.get("amount")) if item else None,
                vat_amount=_safe_float(item.get("vat_amount")) if item else None,
            )
        )

    document_date = _safe_date(raw.get("document_date"))

    data = DocumentData(
        document_type=(raw.get("document_type") or "").strip() or "Акт",
        document_number=_safe_str(raw.get("document_number")),
        document_date=document_date,
        counterparty=_safe_str(raw.get("counterparty")),
        document_total=_safe_float(raw.get("document_total")),
        vat_total=_safe_float(raw.get("vat_total")),
        items=items,
    )
    return data


def _safe_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):  # pragma: no cover - depends on remote API
        return None


def _safe_date(value: Any) -> datetime.date | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y"):
            try:
                return datetime.strptime(value, fmt).date()
            except ValueError:
                continue
    return None


def _safe_str(value: Any) -> str | None:
    if value is None:
        return None
    value = str(value).strip()
    return value or None


__all__ = ["parse_document_text"]
