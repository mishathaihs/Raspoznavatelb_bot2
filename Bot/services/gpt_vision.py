from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass
from typing import Any

from openai import AsyncOpenAI, OpenAIError

from bot.models import ParsedDocument
from config import get_settings

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "Ты — бухгалтерский ассистент. Твоя задача — читать сканы/фото первичных бухгалтерских "
    "документов на русском языке (Беларусь, Россия) и возвращать строго JSON по заданной схеме. "
    "Документы: товарные накладные, товарно-транспортные накладные, акты выполненных работ/оказанных "
    "услуг, счета-фактуры, счета-протоколы и похожие формы. Всегда считай, что язык документа — русский, "
    "даже если буквы плохо видны или наполовину латиница. Не придумывай данных; если реквизит неразборчив "
    "или отсутствует — ставь null и добавляй пояснение в массив warnings."
)


@dataclass(slots=True)
class GPTVisionSettings:
    api_key: str
    model: str
    timeout_seconds: int
    max_retries: int


class GPTVisionClient:
    def __init__(self, api_key: str, model: str, timeout_seconds: int = 60, max_retries: int = 2):
        self._settings = GPTVisionSettings(
            api_key=api_key, model=model, timeout_seconds=timeout_seconds, max_retries=max_retries
        )
        self._client = AsyncOpenAI(api_key=api_key, timeout=timeout_seconds, max_retries=max_retries)

    async def parse_document(
        self, image_bytes: bytes | None, text_from_docx: str | None, filename: str
    ) -> ParsedDocument:
        content = self._build_user_content(image_bytes=image_bytes, text_from_docx=text_from_docx, filename=filename)
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": content},
        ]
        try:
            response = await self._client.responses.create(
                model=self._settings.model,
                messages=messages,
            )
        except OpenAIError:
            logger.exception("GPT-Vision request failed")
            raise
        text = self._extract_text(response)
        try:
            payload: dict[str, Any] = json.loads(text)
        except json.JSONDecodeError:
            logger.error("GPT response is not valid JSON: %s", text[:500])
            raise
        try:
            return ParsedDocument.model_validate(payload)
        except Exception:
            logger.exception("Failed to validate ParsedDocument payload")
            raise

    def _build_user_content(
        self, image_bytes: bytes | None, text_from_docx: str | None, filename: str
    ) -> list[dict[str, Any]]:
        instructions = self._build_user_prompt()
        content: list[dict[str, Any]] = [{"type": "text", "text": instructions}]
        if image_bytes:
            b64 = base64.b64encode(image_bytes).decode()
            content.append(
                {
                    "type": "input_image",
                    "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                }
            )
        elif text_from_docx:
            content.append({"type": "text", "text": text_from_docx})
        else:
            raise ValueError("Either image_bytes or text_from_docx must be provided")
        content.append({"type": "text", "text": f"Имя файла: {filename}"})
        return content

    def _build_user_prompt(self) -> str:
        return (
            "Прочитай документ и верни строго один JSON по схеме ParsedDocument. "
            "Сначала определи тип документа, дату и номер рядом с названием. "
            "Схема: {doc_type, title, number, date, supplier, buyer, main_counterparty, subject, items, main_item, totals, currency, currency_code (BYN/RUB/UNKNOWN), currency_words_ru, raw_text, warnings}. "
            "Правила:\n"
            "1) Дата (date): ищи возле названия и номера, с предлогом 'от'. Игнорируй даты договоров, периодов оказания услуг и даты подписи внизу. Формат YYYY-MM-DD.\n"
            "2) Номер (number): номер около заголовка документа, не путай с договором/заказом. Для комбинированных документов выбирай номер акта, если он есть, иначе общий номер.\n"
            "3) Контрагент (main_counterparty): для актов — заказчик/покупатель; для накладных/ТТН — продавец или грузоотправитель. Если не уверен, выбери сторону, отличную от нашей компании, и добавь предупреждение.\n"
            "4) Суммы и НДС: amount_with_vat ≈ amount_without_vat + vat_amount (погрешность 0.02). Если суммы расходятся — заполнить по таблице документа и добавить предупреждение. totals.amount_with_vat должен соответствовать строке Итого/Всего.\n"
            "5) main_item: самая информативная позиция; если позиций много — можно объединить в 'Разные товары, см. исходный документ'.\n"
            "6) Валюта: currency_code = BYN или RUB, если по документу видно явную валюту или сумма прописью указывает валюту; иначе UNKNOWN. currency_words_ru = 'белорусских рублей' или 'российских рублей' или 'рублей'.\n"
            "7) raw_text: верни полный читабельный текст документа на русском, сохраняя порядок строк по возможности.\n"
            "8) Формат ответа: только JSON ParsedDocument, без пояснений. Числа с точкой как разделителем, даты YYYY-MM-DD."
        )

    @staticmethod
    def _extract_text(response: Any) -> str:
        if hasattr(response, "output_text") and response.output_text:
            return response.output_text
        try:
            output = response.output[0]
            content = output.content[0]
            if hasattr(content, "text"):
                return content.text
            if isinstance(content, dict) and "text" in content:
                return content["text"]
        except Exception:
            logger.exception("Failed to extract text from GPT response")
        return ""


_client_cache: GPTVisionClient | None = None


def get_gpt_client() -> GPTVisionClient:
    global _client_cache
    if _client_cache is None:
        settings = get_settings()
        _client_cache = GPTVisionClient(
            api_key=settings.openai_api_key,
            model=settings.openai_vision_model,
            timeout_seconds=settings.openai_timeout_seconds,
            max_retries=settings.openai_max_retries,
        )
    return _client_cache


__all__ = ["GPTVisionClient", "get_gpt_client"]
