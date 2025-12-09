from __future__ import annotations

import asyncio
import base64
import inspect
import io
import json
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

try:  # pragma: no cover - optional dependency for image handling
    from PIL import Image
except ImportError:  # pragma: no cover - optional dependency
    Image = None

from openai import APIError, APIStatusError, BadRequestError, OpenAI, RateLimitError

from bot.models import Item, ParsedDocument, Party, Totals
from config import get_settings
from services.amounts import format_amount_ru, parse_amount
from services.exceptions import ParseError
from services.image_preprocessing import preprocess_image_bytes
from services.layout_types import LayoutExtraction
from services.normalization import (
    _alias_score,
    _build_alias_pool,
    load_company_directory,
    normalize_company_name,
    normalize_company_name_loose,
    normalize_in_text,
)
from services.ocr_party_extractor import (
    OcrParties,
    PartyInfo,
    extract_parties,
    extract_parties_waybill,
)
from services.ocr import extract_layout_from_image_bytes
from rapidfuzz import fuzz, process
from settings.companies import Company, find_company_by_tax_id

logger = logging.getLogger(__name__)
AMOUNT_PATTERN = re.compile(r"(\d{1,3}(?:[ \u00A0]?\d{3})*,\d{2,5})")
VAT_RATE_PATTERN = re.compile(r"(\d{1,2}(?:[\.,]\d+)?)%")
TOTAL_MARKERS = {
    "total_without_vat": [
        "итого без ндс",
        "стоимость оказанных услуг без ндс",
        "сумма без ндс",
    ],
    "vat_amount": ["ндс", "сумма ндс"],
    "total_with_vat": [
        "итого с ндс",
        "всего с ндс",
        "стоимость оказанных услуг с ндс",
    ],
}
TARIFF_MARKERS = ["тариф", "цена", "тариф (руб)", "цена (руб)"]
MIN_PARTY_LENGTH = 10
HALLUCINATION_BLACKLIST = {
    "заря-агро",
    "завод х",
    "борисовский филиал областного унитарного предприятия",
}


def _open_image_or_raise(image_bytes: bytes):
    if Image is None:  # pragma: no cover - defensive
        raise ImportError("Pillow is required for OCR party extraction")
    return Image.open(io.BytesIO(image_bytes))


@dataclass
class VisionParties:
    supplier_name: str | None
    supplier_tax_id: str | None
    customer_name: str | None
    customer_tax_id: str | None


@dataclass
class PartyCandidate:
    name: str | None
    tax_id: str | None
    source: Literal["vision", "ocr"]


@dataclass
class ResolvedParty:
    raw_name: str | None
    display_name: str | None
    tax_id: str | None
    dict_company: Company | None
    source: Literal[
        "dict",
        "tax_vision",
        "tax_ocr",
        "name_match",
        "vision",
        "ocr",
        "vision_raw",
        "ocr_raw",
        "none",
    ]


@dataclass
class ResolvedParties:
    supplier: ResolvedParty
    customer: ResolvedParty

SYSTEM_PROMPT = """
You are an OCR and structured data extractor for Russian financial documents.

Task:
You receive a high resolution scan or photo of a Belarusian "товарная накладная" (goods waybill).
Your goal is to read all important fields and return a single JSON object that strictly follows the schema below.

General rules:
- The document language is Russian. Field names in the JSON must be exactly as in the schema.
- Read the data from the document as accurately as possible.
- NEVER invent or hallucinate values that are not clearly visible on the image.
- If you cannot confidently read a value, set it to null instead of guessing.
- All money and numeric fields MUST use a dot as decimal separator, no thousand separators, e.g.: 1195.00, 239.00, 1434.00.
- Totals must be copied exactly from the document totals row; preserve cents and do not round.
- doc_type for this kind of document MUST be exactly "товарная_накладная".
- Do NOT fabricate company types or addresses (like "ЗАО ХХХ 220039, г. Минск, ул. Долгобродская, 25") if they are not clearly written on the document.
- Company names must be copied exactly as printed on the document header. If you cannot read a name, return null instead of inventing neutral placeholders or generic names like "Заря-агро" or "Завод Х".

Waybill structure:
The waybill usually has:
- Document header with number, date, currency, VAT rate.
- Seller (supplier) and buyer (customer) blocks with full company names, sometimes UNP (tax ID).
- A table of line items with columns like item name, unit, quantity, price, VAT rate, VAT amount, total with VAT.
- A final row "ИТОГО" (or similar) with totals for the whole document.

You must output a JSON object with the following structure:

{
  "document_type": "товарная_накладная",

  "number": string or null,
  "date": string or null,                 // format "DD.MM.YYYY" if possible
  "currency": string or null,             // e.g. "BYN"
  "vat_rate_percent": number or null,     // e.g. 20.0

  "counterparty_customer": string or null,   // buyer / receiver
  "counterparty_supplier": string or null,   // supplier / sender
  "customer_name": string or null,           // same as counterparty_customer, copy text exactly from document
  "customer_tax_id": string or null,         // UNP of buyer, numbers only
  "supplier_name": string or null,           // same as counterparty_supplier, copy text exactly from document
  "supplier_tax_id": string or null,         // UNP of supplier, numbers only

  "totals": {
    "without_vat": number or null,
    "vat_amount": number or null,
    "vat_rate_percent": number or null,
    "with_vat": number or null
  },

  "items": [
    {
      "line_no": 1,
      "name": string,
      "quantity": number or null,
      "unit": string or null,
      "price_without_vat": number or null,
      "vat_rate_percent": number or null,
      "vat_amount": number or null,
      "total_without_vat": number or null,
      "total_with_vat": number or null
    },
    ...
  ],

  "warnings": []        // array of strings for the "Нужна проверка" block
}

IMPORTANT rules for items and totals:

1. Items:
   - Each element in "items" MUST correspond to a real line of the item table where quantity and amounts are filled.
   - DO NOT create an item for the "ИТОГО" (TOTAL) row.
   - DO NOT invent extra items. If there are 2 products in the table, "items" MUST have length 2.
   - If a numeric value in an item row is hard to read, set that specific field to null, but still return the item with other fields.

2. Totals:
   - "totals" should be filled from the dedicated "ИТОГО" row (or equivalent) if such a row exists.
   - If the totals row is not clearly visible, but you can safely deduce totals from a clearly printed totals block ("Всего стоимость" etc.), you may use that.
   - Otherwise, when you cannot confidently read totals, set these fields to null.

3. Organisations:
   - Always return the organisation names exactly as printed in the header (Грузоотправитель / Грузополучатель), without addresses. If unreadable, set to null and add a warning.
   - Do not invent placeholder names; every returned organisation must be visible as text in the document.
   - counterparty_customer — это покупатель/получатель; counterparty_supplier — продавец/поставщик/отправитель.

Checks:
- Compute the sum total_with_vat across all items (where numbers exist) and compare with totals.with_vat.
- If the difference is greater than 1 BYN, add a warning that totals mismatch, with both values.

Output format:
- Return ONLY the JSON object described above.
- No markdown, no explanations, no comments, no extra keys.
""".strip()

TEXT_CLEAN_PROMPT = """
Ты редактор OCR текста. Исправь очевидные ошибки распознавания (0/О, 1/І/л, Z/2, запятые в числах → точку),
не меняй смысл и суммы. Сохрани порядок строк, не добавляй пояснений и форматирование. Верни только исправленный текст.
""".strip()

VISION_USER_PROMPT = """
Проанализируй товарную накладную и верни ТОЛЬКО один JSON по схеме из системного промпта.
Ключевые правила:
- doc_type/document_type для накладной: "товарная_накладная".
- Названия организаций копируй из строк "Грузоотправитель" и "Грузополучатель" без адресов; если не читается — верни null и добавь предупреждение. Запрещено придумывать или подставлять типовые названия вроде "Заря-агро" или "Завод Х".
- Поля supplier_name, supplier_tax_id, customer_name, customer_tax_id заполняй только значениями, которые видны на документе; если не читаются — возвращай null.
- Строки таблицы (items) — только реальные позиции товаров, без строки "ИТОГО" и без дубликатов. Если в таблице 2 позиции, в items должно быть ровно 2 элемента.
- Суммы и числа пиши с точкой в качестве разделителя.
- Итоги (totals) бери из блока "ИТОГО" и переписывай точные значения (без округлений). totals.with_vat ≈ totals.without_vat + totals.vat_amount с допуском 0.02; если расхождение больше — всё равно сохрани цифры с документа и добавь предупреждение о несоответствии.
- Не придумывай компании или суммы; если не уверен — оставь поле пустым.
""".strip()



class VisionExtractor:
    def __init__(self, client: OpenAI, model: str, text_model: str | None = None):
        self._client = client
        safe_model = model
        if not model or model.startswith("gpt-5"):
            safe_model = "gpt-4.1"
            logger.warning("Unsupported model %s requested, forcing %s", model, safe_model)
        self._fallback_candidates = _build_model_fallback_chain(safe_model)
        self._fallback_index = 0
        self._model = self._fallback_candidates[self._fallback_index]
        self._text_model = text_model or "gpt-4.1-mini"
        if not self._text_model.startswith("gpt-4.1"):
            self._text_model = "gpt-4.1-mini"
        self._fallback_used = False
        self._supports_response_format = _supports_response_format(client)
        self._supports_temperature = _supports_param(client, "temperature")
        self._last_layout: LayoutExtraction | None = None
        if not self._supports_response_format:
            logger.warning(
                "response_format is not supported by this OpenAI client; falling back to JSON text parsing"
            )

    async def extract(
        self,
        image_bytes: bytes | None = None,
        text_content: str | None = None,
        *,
        strict_json: bool = False,
    ) -> ParsedDocument:
        if not image_bytes:
            raise VisionExtractionError("Пустое изображение")

        processed_bytes, processed_image = preprocess_image_bytes(image_bytes)
        ocr_layout = extract_layout_from_image_bytes(image_bytes)
        self._last_layout = ocr_layout

        parts = [SYSTEM_PROMPT, VISION_USER_PROMPT]
        parts.append(f"OCR текст (Tesseract):\n{ocr_layout.raw_text}")
        if strict_json:
            parts.append(
                "Верни ТОЛЬКО чистый JSON без ``` и без пояснений по схеме doc_type, doc_number, doc_date, currency, contractor, executor, totals, items."
            )
        extra_text = text_content.strip() if text_content else None
        if extra_text:
            parts.append(f"Дополнительный текст из файла: \n{extra_text}")
        instructions = "\n\n".join(parts)

        input_payload = self._build_input_payload(
            instructions=instructions,
            image_bytes=processed_bytes,
            processed_image=processed_image,
        )

        logger.debug(
            "Calling vision model=%s image_bytes=%s text_provided=%s",
            self._model,
            len(image_bytes) if image_bytes else 0,
            bool(text_content),
        )

        response = await self._call_model(input_payload, json_schema=_build_json_schema())

        parsed_payload = _extract_parsed_payload(response)
        try:
            document = _convert_to_parsed_document(parsed_payload)
        except Exception as exc:  # pragma: no cover - runtime safety
            logger.exception("Failed to convert parsed payload to ParsedDocument")
            logger.error("Vision payload preview: %s", str(parsed_payload)[:1000])
            raise VisionExtractionError("Vision response does not match expected schema") from exc

        vision_parties = _extract_vision_parties(parsed_payload, document)
        ocr_parties: OcrParties | None = None
        if document.doc_type == "товарная_накладная":
            try:
                original_image = _open_image_or_raise(image_bytes)
                party_info: PartyInfo | None = None
                header_preview = (ocr_layout.raw_text or "").upper()
                header_score = fuzz.partial_ratio(header_preview, "ТОВАРНАЯ НАКЛАДНАЯ")
                if "ТОВАРНАЯ НАКЛАДНАЯ" in header_preview or header_score >= 85:
                    party_info = extract_parties_waybill(
                        original_image, doc_id=document.doc_number or document.doc_type
                    )
                if party_info:
                    ocr_parties = OcrParties(
                        supplier_name=party_info.supplier_name,
                        supplier_tax_id=party_info.supplier_tax_id,
                        customer_name=party_info.customer_name,
                        customer_tax_id=party_info.customer_tax_id,
                        header_text=ocr_layout.raw_text or "",
                    )
            except Exception as exc:  # pragma: no cover - runtime safety
                logger.warning("Failed to extract parties from header: %s", exc)
        else:
            try:
                original_image = _open_image_or_raise(image_bytes)
                party_info = extract_parties(original_image, document.doc_type)
                ocr_parties = OcrParties(
                    supplier_name=party_info.supplier_name,
                    supplier_tax_id=party_info.supplier_tax_id,
                    customer_name=party_info.customer_name,
                    customer_tax_id=party_info.customer_tax_id,
                    header_text=party_info.supplier_line_ocr or party_info.customer_line_ocr or "",
                )
            except Exception as exc:  # pragma: no cover - runtime safety
                logger.warning("Failed to extract parties from header: %s", exc)

        raw_ocr_text = document.raw_ocr_text or ocr_layout.raw_text
        normalization_notes, blocking = _apply_company_normalization(document, raw_ocr_text)
        document.normalization_notes.extend(normalization_notes)
        document.warnings.extend([note for note in normalization_notes if "коррект" in note.lower()])
        if blocking:
            raise VisionExtractionError(
                "Не удалось надёжно распознать названия контрагентов"
            )

        resolved_parties = _resolve_parties(vision_parties, ocr_parties, document.doc_type)
        _apply_resolved_parties(document, resolved_parties, ocr_parties.header_text if ocr_parties else None)
        document.items = _postprocess_items(document.items, document.totals)
        _attach_company_codes(document)
        _flag_suspicious_items(document, self._last_layout)

        cleaned_text = await self._clean_text_with_model(
            (ocr_layout.cleaned_text or ocr_layout.raw_text)
        )
        if self._last_layout:
            self._last_layout.cleaned_text = cleaned_text or self._last_layout.cleaned_text
        document.raw_text = cleaned_text or raw_ocr_text
        if document.raw_ocr_text is None:
            document.raw_ocr_text = raw_ocr_text

        items_count = len(document.items)
        totals = document.totals
        logger.debug(
            "Vision parsed doc_type=%s items=%s totals_with_vat=%s model=%s text_len=%s",
            document.doc_type,
            items_count,
            getattr(totals, "total_with_vat", None) if totals else None,
            self._model,
            len(document.raw_text or ""),
        )
        return document

    def _build_input_payload(
        self,
        instructions: str,
        image_bytes: bytes,
        processed_image: Image.Image | None = None,
        extra_text: str | None = None,
    ) -> list[dict[str, Any]]:
        text_block = {"type": "input_text", "text": instructions}
        content: list[dict[str, Any]] = [text_block]
        if extra_text:
            content.append({"type": "input_text", "text": extra_text})

        images: list[bytes] = [image_bytes]
        if processed_image:
            top_crop_height = int(processed_image.height * 0.45)
            if top_crop_height > 0:
                crop = processed_image.crop((0, 0, processed_image.width, top_crop_height))
                buffer = io.BytesIO()
                crop.save(buffer, format="JPEG")
                images.append(buffer.getvalue())

        for payload in images:
            b64 = base64.b64encode(payload).decode("ascii")
            content.append({"type": "input_image", "image_url": f"data:image/jpeg;base64,{b64}"})

        return [{"role": "user", "content": content}]


    async def _call_model(self, input_payload: list[dict[str, Any]], *, json_schema: dict[str, Any]):
        def _sync_call():
            kwargs: dict[str, Any] = {
                "model": self._model,
                "input": input_payload,
                "max_output_tokens": 1200,
            }
            if self._supports_response_format:
                kwargs["response_format"] = {"type": "json_schema", "json_schema": json_schema}
            if self._supports_temperature:
                kwargs["temperature"] = 0
            return self._client.responses.create(**kwargs)

        try:
            return await asyncio.to_thread(_sync_call)
        except BadRequestError as exc:  # pragma: no cover - remote API
            if self._should_fallback(exc):
                return await self._retry_with_fallback(input_payload, json_schema)
            logger.exception("OpenAI BadRequestError", exc_info=exc)
            raise VisionExtractionError("Ошибка OpenAI API") from exc
        except RateLimitError as exc:  # pragma: no cover - remote API
            logger.exception("OpenAI rate limit exceeded", exc_info=exc)
            raise VisionExtractionError("Превышен лимит OpenAI API") from exc
        except APIStatusError as exc:  # pragma: no cover - remote API
            if self._should_fallback(exc):
                return await self._retry_with_fallback(input_payload, json_schema)
            logger.exception("OpenAI API status error", exc_info=exc)
            raise VisionExtractionError(f"Ошибка OpenAI API: {exc.status_code}") from exc
        except APIError as exc:  # pragma: no cover - remote API
            logger.exception("OpenAI API error", exc_info=exc)
            raise VisionExtractionError("Ошибка OpenAI API") from exc

    async def _clean_text_with_model(self, text: str | None) -> str | None:
        if not text:
            return None

        payload = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": f"{TEXT_CLEAN_PROMPT}\n\n{text}",
                    }
                ],
            }
        ]

        try:
            response = await asyncio.to_thread(
                lambda: self._client.responses.create(
                    model=self._text_model, input=payload, max_output_tokens=800
                )
            )
        except Exception as exc:  # pragma: no cover - remote API
            logger.warning("Text cleanup failed, using raw OCR text: %s", exc)
            return text

        cleaned = _extract_text_response(response)
        return cleaned or text

    def _should_fallback(self, exc: Exception) -> bool:
        if self._fallback_index >= len(self._fallback_candidates) - 1:
            return False

        error_dict = None
        error_code = None
        error_message = ""

        if isinstance(getattr(exc, "response", None), dict):
            error_dict = exc.response
        elif isinstance(getattr(exc, "error", None), dict):
            error_dict = exc.error

        if isinstance(error_dict, dict):
            nested_error = error_dict.get("error", {}) or {}
            error_code = nested_error.get("code") or error_dict.get("code")
            error_message = nested_error.get("message") or error_dict.get("message") or ""

        error_code = error_code or getattr(exc, "code", None)
        error_message = error_message or str(getattr(exc, "message", ""))
        if not error_message:
            error_message = str(exc)

        if error_code == "unsupported_value":
            return True

        lowered = error_message.lower()
        if "verify organization" in lowered or (
            "unsupported" in lowered and "model" in lowered
        ):
            return True
        return False

    def _advance_fallback_model(self) -> bool:
        if self._fallback_index >= len(self._fallback_candidates) - 1:
            return False
        self._fallback_index += 1
        self._model = self._fallback_candidates[self._fallback_index]
        self._fallback_used = True
        return True

    async def _retry_with_fallback(
        self, input_payload: list[dict[str, Any]], json_schema: dict[str, Any]
    ):
        if not self._advance_fallback_model():
            raise VisionExtractionError("Нет доступной модели для распознавания")
        logger.warning(
            "Model unsupported; switching to fallback model %s", self._model
        )
        return await self._call_model(input_payload, json_schema=json_schema)

    def get_last_layout(self) -> LayoutExtraction | None:
        return self._last_layout


def _ensure_responses_supported(client: OpenAI) -> None:
    """Ensure the OpenAI client supports the responses API."""

    if not hasattr(client, "responses"):
        raise RuntimeError(
            "Installed `openai` package does not support `client.responses` API. "
            "Update `openai` to a recent 1.x version (see requirements.txt)."
        )


def _supports_response_format(client: OpenAI) -> bool:
    """Return True if the client.responses.create supports `response_format`."""

    try:
        create_method = client.responses.create
        signature = inspect.signature(create_method)
        return "response_format" in signature.parameters
    except Exception:  # pragma: no cover - defensive guard
        return False


def _supports_param(client: OpenAI, param_name: str) -> bool:
    try:
        signature = inspect.signature(client.responses.create)
        return param_name in signature.parameters
    except Exception:  # pragma: no cover - defensive guard
        return False


def _build_model_fallback_chain(primary_model: str) -> list[str]:
    chain: list[str] = []
    preferred = [primary_model, "gpt-4.1", "gpt-4.1-mini", "gpt-4o-mini"]
    for model in preferred:
        if model and model not in chain:
            chain.append(model)
    return chain


def _extract_text_from_openai_response(response: Any) -> Any:
    """Extract raw text from an OpenAI Responses API response object."""

    if isinstance(response, str):
        return response

    output_blocks = getattr(response, "output", None) or (
        response.get("output") if isinstance(response, dict) else None
    )

    text_value: str | None = None
    parsed_value: Any = None

    if not output_blocks:
        text_value = getattr(response, "output_text", None)
        if isinstance(text_value, str) and text_value.strip():
            return text_value.strip()

    for block in output_blocks or []:
        content = getattr(block, "content", None) or (
            block.get("content") if isinstance(block, dict) else None
        )
        for item in content or []:
            parsed_candidate = (
                getattr(item, "parsed", None) if not isinstance(item, dict) else item.get("parsed")
            )
            if isinstance(parsed_candidate, dict):
                parsed_value = parsed_candidate
                break
            candidate = getattr(item, "text", None) if not isinstance(item, dict) else item.get("text")
            if isinstance(candidate, dict):
                candidate = candidate.get("value") or candidate.get("text")
            if isinstance(candidate, str) and candidate.strip():
                text_value = candidate.strip()
                break
        if parsed_value is not None or text_value:
            break

    if parsed_value is not None:
        return parsed_value
    return text_value or ""


def _extract_json_block(raw_text: str) -> str:
    """Return substring between first '{' and last '}', removing markdown fences."""

    cleaned = raw_text.strip().replace("\ufeff", "")
    fence_pattern = re.compile(r"^`{3,}\s*json\s*\n?|`{3,}$", re.IGNORECASE | re.MULTILINE)
    cleaned = fence_pattern.sub("", cleaned)

    first_brace = cleaned.find("{")
    last_brace = cleaned.rfind("}")
    if first_brace == -1 or last_brace == -1 or last_brace < first_brace:
        return cleaned
    return cleaned[first_brace : last_brace + 1].strip()


def _strip_raw_ocr_text(s: str) -> str:
    key = '"raw_ocr_text"'
    idx = s.find(key)
    if idx == -1:
        return s
    prefix = s[:idx]
    comma_idx = prefix.rfind(",")
    if comma_idx != -1:
        prefix = prefix[:comma_idx]
    if not prefix.strip().endswith("}"):
        prefix = prefix.rstrip()
        if prefix.endswith("{"):
            prefix = prefix[:-1]
        prefix = prefix.rstrip() + "\n}"
    return prefix


def _extract_parsed_payload(response: Any) -> dict[str, Any]:
    """Extract JSON payload from the vision model response."""

    raw_content = _extract_text_from_openai_response(response)
    if isinstance(raw_content, dict):
        return raw_content

    text = str(raw_content)
    json_str = _extract_json_block(text)
    if '"raw_ocr_text"' in json_str:
        json_str = _strip_raw_ocr_text(json_str)

    try:
        return json.loads(json_str)
    except json.JSONDecodeError as exc:
        logger.error("Failed to decode vision JSON: %s", exc)
        logger.error("Vision raw response preview: %s", json_str[:2000])
        raise VisionExtractionError("Vision response is not valid JSON") from exc


def _strip_code_fences(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```") and cleaned.endswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:]
    return cleaned.strip()


def _build_json_schema() -> dict[str, Any]:
    return {
        "name": "parsed_document",
        "schema": {
            "type": "object",
            "properties": {
                "document_type": {"type": "string", "nullable": True},
                "doc_type": {"type": "string", "nullable": True},
                "number": {"type": "string", "nullable": True},
                "doc_number": {"type": "string", "nullable": True},
                "date": {"type": "string", "nullable": True},
                "doc_date": {"type": "string", "nullable": True},
                "currency": {"type": "string", "nullable": True},
                "vat_rate_percent": {"type": ["number", "string"], "nullable": True},
                "counterparty_customer": {"type": "string", "nullable": True},
                "counterparty_supplier": {"type": "string", "nullable": True},
                "customer_name": {"type": "string", "nullable": True},
                "customer_tax_id": {"type": "string", "nullable": True},
                "supplier_name": {"type": "string", "nullable": True},
                "supplier_tax_id": {"type": "string", "nullable": True},
                "totals": {
                    "type": "object",
                    "properties": {
                        "without_vat": {"type": ["number", "string"], "nullable": True},
                        "vat_amount": {"type": ["number", "string"], "nullable": True},
                        "vat_rate_percent": {"type": ["number", "string"], "nullable": True},
                        "with_vat": {"type": ["number", "string"], "nullable": True},
                    },
                },
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "line_no": {"type": "integer", "nullable": True},
                            "line_number": {"type": "integer", "nullable": True},
                            "name": {"type": "string", "nullable": True},
                            "quantity": {"type": ["number", "string"], "nullable": True},
                            "unit": {"type": "string", "nullable": True},
                            "price_without_vat": {"type": ["number", "string"], "nullable": True},
                            "vat_rate_percent": {"type": ["number", "string"], "nullable": True},
                            "vat_amount": {"type": ["number", "string"], "nullable": True},
                            "total_without_vat": {"type": ["number", "string"], "nullable": True},
                            "amount_without_vat": {"type": ["number", "string"], "nullable": True},
                            "total_with_vat": {"type": ["number", "string"], "nullable": True},
                            "amount_with_vat": {"type": ["number", "string"], "nullable": True},
                        },
                    },
                },
                "warnings": {
                    "type": "array",
                    "items": {"type": "string"},
                    "nullable": True,
                },
            },
            "required": [],
        },
    }

_client: OpenAI | None = None
_vision_extractor: VisionExtractor | None = None


def get_vision_extractor() -> VisionExtractor:
    global _client, _vision_extractor
    if _client is None:
        settings = get_settings()
        _client = OpenAI(api_key=settings.openai_api_key)
        _ensure_responses_supported(_client)
    if _vision_extractor is None:
        settings = get_settings()
        _vision_extractor = VisionExtractor(
            client=_client,
            model=settings.openai_vision_model,
            text_model=settings.openai_text_model,
        )
    return _vision_extractor


def _extract_text_response(response: Any) -> str | None:
    output_blocks = getattr(response, "output", None) or (
        response.get("output") if isinstance(response, dict) else None
    )
    if not output_blocks:
        text_value = getattr(response, "output_text", None)
        if isinstance(text_value, str):
            return text_value.strip()
        return None

    for block in output_blocks:
        content = getattr(block, "content", None) or (block.get("content") if isinstance(block, dict) else None) or []
        for item in content:
            candidate = getattr(item, "text", None) if not isinstance(item, dict) else item.get("text")
            if isinstance(candidate, dict):
                candidate = candidate.get("value") or candidate.get("text")
            if isinstance(candidate, str) and candidate.strip():
                return _strip_code_fences(candidate.strip())
    return None


def _convert_to_decimal(value: Any) -> Decimal | None:
    if isinstance(value, str):
        cleaned = value.strip()
        if cleaned in {"", "-", "—"}:
            logger.warning("Empty numeric field received: %s", value)
            return None
    parsed = parse_amount(value)
    if parsed is None and value not in (None, ""):
        logger.warning("Failed to parse numeric value: %s", value)
    return parsed


def _money_value(value: Any) -> tuple[Decimal | None, str | None]:
    """Return parsed decimal and original text representation."""

    text_value = None
    if value is not None:
        text_value = str(value).strip()
    return _convert_to_decimal(value), text_value if text_value else None


def _convert_to_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _first_amount_from_lines(lines: list[str]) -> tuple[Decimal | None, str | None]:
    for line in lines:
        if not line:
            continue
        matches = list(AMOUNT_PATTERN.finditer(line))
        if not matches:
            continue
        match = matches[-1]
        raw_value = match.group(1)
        parsed = parse_amount(raw_value)
        if parsed is not None:
            return parsed, raw_value
    return None, None


def _extract_totals_from_text(raw_text: str | None) -> dict[str, tuple[Decimal | None, str | None]]:
    if not raw_text:
        return {}

    lines = raw_text.splitlines()
    results: dict[str, tuple[Decimal | None, str | None]] = {}

    for idx, line in enumerate(lines):
        lowered = line.lower()
        for key, markers in TOTAL_MARKERS.items():
            if key in results:
                continue
            if any(marker in lowered for marker in markers):
                next_line = lines[idx + 1] if idx + 1 < len(lines) else ""
                parsed, raw = _first_amount_from_lines([line, next_line])
                if parsed is not None:
                    results[key] = (parsed, raw)
        if "ндс" in lowered and "без ндс" not in lowered:
            if "vat_rate_percent" not in results:
                rate_match = VAT_RATE_PATTERN.search(lowered)
                if rate_match:
                    results["vat_rate_percent"] = (
                        _convert_to_decimal(rate_match.group(1)),
                        rate_match.group(1),
                    )
    return results


def _extract_tariff_from_text(raw_text: str | None) -> tuple[Decimal | None, str | None]:
    if not raw_text:
        return None, None
    for line in raw_text.splitlines():
        lowered = line.lower()
        if any(marker in lowered for marker in TARIFF_MARKERS):
            parsed, raw = _first_amount_from_lines([line])
            if parsed is not None:
                return parsed, raw
    return None, None


def _has_significant_mismatch(model_value: Decimal, text_value: Decimal) -> bool:
    diff = abs(model_value - text_value)
    if diff > Decimal("1"):
        return True
    if model_value == 0:
        return diff > Decimal("0")
    return diff / abs(model_value) > Decimal("0.01")


def _has_price_mismatch(model_value: Decimal, text_value: Decimal) -> bool:
    diff = abs(model_value - text_value)
    if model_value == 0:
        return diff > Decimal("0")
    return diff / abs(model_value) > Decimal("0.20")


def _decimal_to_text(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format_amount_ru(value)


def _parse_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value:
        for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d-%m-%Y"):
            try:
                return datetime.strptime(value.strip(), fmt).date()
            except ValueError:
                continue
    return None


def _convert_items(items_payload: Any) -> list[Item]:
    if not isinstance(items_payload, list):
        return []
    items: list[Item] = []
    for row in items_payload:
        if not isinstance(row, dict):
            continue
        description = row.get("description") or row.get("name")
        price_raw = (
            row.get("unit_price")
            or row.get("price")
            or row.get("tariff")
            or row.get("price_without_vat")
            or row.get("price_wo_vat")
        )
        quantity = row.get("quantity") if row.get("quantity") is not None else row.get("qty")
        amount_without_vat_raw = (
            row.get("amount_without_vat")
            or row.get("amount_wo_vat")
            or row.get("sum_without_vat")
            or row.get("line_total_without_vat")
            or row.get("total_without_vat")
        )
        amount_with_vat_raw = (
            row.get("amount_with_vat")
            or row.get("total_with_vat")
            or row.get("line_total")
            or row.get("sum_with_vat")
            or row.get("line_total_with_vat")
        )
        vat_amount_raw = row.get("vat_amount") or row.get("total_vat")
        vat_rate_raw = row.get("vat_rate") or row.get("vat_rate_percent")
        price_dec, price_text = _money_value(price_raw)
        amount_without_vat_dec, amount_without_vat_text = _money_value(amount_without_vat_raw)
        amount_with_vat_dec, amount_with_vat_text = _money_value(amount_with_vat_raw)
        vat_amount_dec, vat_amount_text = _money_value(vat_amount_raw)
        vat_rate_dec, vat_rate_text = _money_value(vat_rate_raw)
        item = Item(
            line_number=_convert_to_int(row.get("line_number") or row.get("line_no")),
            description=description if isinstance(description, str) else None,
            quantity=_convert_to_decimal(quantity),
            unit=row.get("unit") if isinstance(row.get("unit"), str) else None,
            price=price_dec,
            price_text=price_text,
            amount_without_vat=amount_without_vat_dec,
            amount_without_vat_text=amount_without_vat_text,
            vat_rate=vat_rate_dec,
            vat_rate_text=vat_rate_text,
            vat_amount=vat_amount_dec,
            vat_amount_text=vat_amount_text,
            amount_with_vat=amount_with_vat_dec,
            amount_with_vat_text=amount_with_vat_text,
            currency=row.get("currency") if isinstance(row.get("currency"), str) else None,
        )
        items.append(item)
    return items


def _normalize_item_name(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", value).strip(" -;:\t")


def _clean_item_key(value: str | None) -> str:
    cleaned = _normalize_item_name(value)
    cleaned = re.sub(r"\(\d+\)$", "", cleaned)
    return cleaned.lower()


def _merge_item_pair(primary: Item, secondary: Item) -> Item:
    def _sum_decimal(a: Decimal | None, b: Decimal | None) -> Decimal | None:
        if a is None:
            return b
        if b is None:
            return a
        return a + b

    merged = primary.model_copy(deep=True)
    merged.quantity = _sum_decimal(primary.quantity, secondary.quantity)
    merged.amount_without_vat = _sum_decimal(primary.amount_without_vat, secondary.amount_without_vat)
    merged.vat_amount = _sum_decimal(primary.vat_amount, secondary.vat_amount)
    merged.amount_with_vat = _sum_decimal(primary.amount_with_vat or primary.total_with_vat, secondary.amount_with_vat or secondary.total_with_vat)
    merged.total_with_vat = merged.amount_with_vat
    merged.name = max(primary.name or "", secondary.name or "", key=len) or merged.name
    merged.description = merged.name or merged.description
    if not merged.unit:
        merged.unit = secondary.unit
    if merged.price is None:
        merged.price = secondary.price
    if merged.price_text is None:
        merged.price_text = secondary.price_text
    if merged.vat_rate is None:
        merged.vat_rate = secondary.vat_rate
    if merged.vat_rate_text is None:
        merged.vat_rate_text = secondary.vat_rate_text
    if merged.currency is None:
        merged.currency = secondary.currency
    return merged


def _merge_similar_items(items: list[Item]) -> list[Item]:
    merged: list[Item] = []
    used = [False] * len(items)

    for idx, item in enumerate(items):
        if used[idx]:
            continue
        base_key = _clean_item_key(item.name or item.description)
        current = item
        for j in range(idx + 1, len(items)):
            if used[j]:
                continue
            other = items[j]
            other_key = _clean_item_key(other.name or other.description)
            if not base_key or not other_key:
                continue
            if current.unit and other.unit and current.unit != other.unit:
                continue
            if fuzz.WRatio(base_key, other_key) >= 95:
                current = _merge_item_pair(current, other)
                used[j] = True
                base_key = _clean_item_key(current.name or current.description)
        used[idx] = True
        merged.append(current)

    for line_no, item in enumerate(merged, start=1):
        item.line_number = line_no
    return merged


def _filter_and_deduplicate_items(items: list[Item]) -> list[Item]:
    filtered: list[Item] = []

    def _all_numeric_empty(it: Item) -> bool:
        numeric_values = [
            it.quantity,
            it.amount_without_vat,
            it.vat_amount,
            it.amount_with_vat,
            it.total_with_vat,
            it.price,
        ]
        return all(value is None or value == Decimal("0") for value in numeric_values)

    for item in items:
        if not item:
            continue
        name = _normalize_item_name(item.name or item.description)
        if name and re.search(r"итого", name, re.IGNORECASE):
            continue
        if _all_numeric_empty(item):
            continue
        if name:
            item.name = item.name or name
            item.description = item.description or item.name
        filtered.append(item)

    deduped: list[Item] = []
    seen: set[tuple[str, Decimal | None]] = set()
    for item in filtered:
        key = (
            _normalize_item_name(item.name or item.description).lower(),
            item.total_with_vat or item.amount_with_vat,
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)

    merged_items = deduped
    while True:
        collapsed = _merge_similar_items(merged_items)
        if len(collapsed) < len(merged_items):
            logger.info(
                "[items] merged similar entries: before=%s after=%s", len(merged_items), len(collapsed)
            )
            merged_items = collapsed
            continue
        break
    return merged_items


def _extract_party_payload(
    payload: Any, *, short_fallback: str | None = None, full_fallback: str | None = None
) -> Party | None:
    name: str | None = None
    address: str | None = None
    unp: str | None = None

    if isinstance(payload, dict):
        name = payload.get("short") or payload.get("name") or payload.get("supplier_short") or payload.get("buyer_short")
        address = (
            payload.get("full")
            or payload.get("supplier_full")
            or payload.get("buyer_full")
            or payload.get("address")
        )
        unp = payload.get("unp") or payload.get("inn") or payload.get("tax_id")
    elif isinstance(payload, str):
        name = payload

    if not name and isinstance(short_fallback, str):
        name = short_fallback
    if not address and isinstance(full_fallback, str):
        address = full_fallback

    name = _clean_text_field(name)
    address = _clean_text_field(address)
    unp = _clean_text_field(unp)

    if not any([name, address, unp]):
        return None
    return Party(name=name, address=address, unp=unp)


def _warn_low_confidence(
    warnings: list[str], label: str, value: Any, confidence: float | None, threshold: float
) -> None:
    if value is None:
        return
    if confidence is None:
        return
    if confidence < threshold:
        warnings.append(f"{label} определено с низкой уверенностью: {value}")


def _attach_confidence_warnings(
    parsed: ParsedDocument,
    confidence_block: dict[str, Any],
    seller_payload: dict[str, Any],
    buyer_payload: dict[str, Any],
    items_payload: list[Any],
    settings,
) -> None:
    threshold_field = float(getattr(settings, "min_field_confidence", 0.55))
    threshold_item = float(getattr(settings, "min_item_confidence", threshold_field))

    _warn_low_confidence(parsed.warnings, "Тип документа", parsed.doc_type, confidence_block.get("document_type"), threshold_field)
    _warn_low_confidence(parsed.warnings, "Номер документа", parsed.doc_number, confidence_block.get("number"), threshold_field)
    _warn_low_confidence(parsed.warnings, "Дата документа", parsed.doc_date, confidence_block.get("date"), threshold_field)
    _warn_low_confidence(parsed.warnings, "Валюта", parsed.currency or parsed.currency_code, confidence_block.get("currency"), threshold_field)

    seller_conf = seller_payload.get("confidence") if isinstance(seller_payload, dict) else None
    buyer_conf = buyer_payload.get("confidence") if isinstance(buyer_payload, dict) else None
    _warn_low_confidence(parsed.warnings, "Контрагент", parsed.contractor, seller_conf, threshold_field)
    _warn_low_confidence(parsed.warnings, "Исполнитель", parsed.executor, buyer_conf, threshold_field)
    totals_conf = None
    totals_obj = confidence_block.get("totals") if isinstance(confidence_block, dict) else None
    if isinstance(totals_obj, (int, float)):
        totals_conf = totals_obj
    elif isinstance(totals_obj, dict):
        totals_conf = totals_obj.get("confidence")
    _warn_low_confidence(
        parsed.warnings,
        "Итоговые суммы",
        parsed.totals.total_with_vat if parsed.totals else None,
        totals_conf,
        threshold_field,
    )

    for idx, row in enumerate(items_payload):
        if not isinstance(row, dict):
            continue
        confidence = row.get("confidence") if isinstance(row.get("confidence"), (int, float)) else None
        if confidence is None:
            continue
        label = f"Позиция {idx + 1}"
        value = row.get("name") or row.get("description")
        if value:
            _warn_low_confidence(parsed.warnings, label, value, confidence, threshold_item)


def _clean_text_field(value: str | None) -> str:
    if not value:
        return ""
    cleaned = value.replace("“", '"').replace("”", '"')
    cleaned = cleaned.replace("«", '"').replace("»", '"').replace("’", "'")
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = cleaned.strip()
    cleaned = re.sub(r"(?<=[А-Яа-яЁё])[A-Za-z](?=[А-Яа-яЁё])", "", cleaned)
    return cleaned


_OCR_FIELD_MARKERS: list[re.Pattern[str]] = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in [
        "исполнитель",
        "заказчик",
        "поставщик",
        "плательщик",
        "грузоотправител",
        "грузополучател",
        "consignee",
        "shipper",
    ]
]


def _extract_relevant_ocr_lines(raw_ocr_text: str | None) -> list[str]:
    if not raw_ocr_text:
        return []
    lines = [line.strip() for line in raw_ocr_text.splitlines() if line.strip()]
    if not lines:
        return []
    candidates: list[str] = []
    for idx, line in enumerate(lines):
        lowered = line.lower()
        if any(pattern.search(lowered) for pattern in _OCR_FIELD_MARKERS):
            for offset in (-1, 0, 1):
                pos = idx + offset
                if 0 <= pos < len(lines):
                    candidate = lines[pos].strip()
                    candidate = re.sub(
                        r"^(исполнитель|заказчик|поставщик|плательщик|грузополучатель|грузоотправитель)\s*[:\-]*\s*",
                        "",
                        candidate,
                        flags=re.IGNORECASE,
                    ).strip()
                    if candidate and candidate not in candidates:
                        candidates.append(candidate)
    if not candidates:
        candidates.extend(lines[:3])
    return candidates


def _build_company_code_pool(
    directory: dict[str, list[dict[str, Any]]]
) -> dict[str, tuple[str, str | None, str | None]]:
    pool: dict[str, tuple[str, str | None, str | None]] = {}
    for entry in directory.get("our_companies", []) + directory.get("counterparties", []):
        canonical = entry.get("canonical") or ""
        aliases = entry.get("aliases") or []
        code = entry.get("code")
        tax_id = entry.get("tax_id")
        for alias in [canonical, *aliases]:
            cleaned = _clean_text_field(alias)
            if cleaned:
                pool[cleaned] = (canonical, code, tax_id)
    return pool


def _match_company_code(
    name: str | None, pool: dict[str, tuple[str, str | None, str | None]]
) -> tuple[str | None, str | None, str | None, float | None]:
    if not name or not pool:
        return None, None, None, None
    cleaned = _clean_text_field(name)
    if not cleaned:
        return None, None, None, None
    match = process.extractOne(cleaned, list(pool.keys()), scorer=fuzz.WRatio)
    if not match:
        return None, None, None, None
    alias, score, _ = match
    if score is None or score < 90:
        return None, None, None, float(score) if score is not None else None
    canonical, code, tax_id = pool.get(alias, (None, None, None))
    return canonical, code, tax_id, float(score)


def _extract_vision_parties(payload: dict[str, Any], document: ParsedDocument) -> VisionParties:
    supplier_name = _clean_text_field(
        payload.get("supplier_name")
        or payload.get("counterparty_supplier")
        or payload.get("supplier")
        or (document.supplier.name if document.supplier else None)
        or document.executor
    )
    customer_name = _clean_text_field(
        payload.get("customer_name")
        or payload.get("counterparty_customer")
        or payload.get("buyer")
        or (document.buyer.name if document.buyer else None)
        or document.contractor
    )
    supplier_tax_id = _clean_text_field(payload.get("supplier_tax_id") or payload.get("supplier_unp"))
    customer_tax_id = _clean_text_field(payload.get("customer_tax_id") or payload.get("customer_unp"))
    if document.supplier and document.supplier.unp:
        supplier_tax_id = supplier_tax_id or document.supplier.unp
    if document.buyer and document.buyer.unp:
        customer_tax_id = customer_tax_id or document.buyer.unp
    return VisionParties(
        supplier_name=supplier_name or None,
        supplier_tax_id=supplier_tax_id or None,
        customer_name=customer_name or None,
        customer_tax_id=customer_tax_id or None,
    )


def _is_blacklisted(value: str | None) -> bool:
    cleaned = (value or "").lower()
    return any(token in cleaned for token in HALLUCINATION_BLACKLIST)


def _choose_party(vision: PartyCandidate, ocr: PartyCandidate) -> ResolvedParty:
    """Pick the best party candidate without ever dropping a provided name to ``None``.

    Preference order:
    1. A candidate that has both name and tax_id (vision first, then OCR).
    2. Otherwise the first non-empty name (vision first, then OCR).
    3. Attach any available tax_id to the chosen name.
    """

    def _clean(value: str | None) -> str | None:
        return _clean_text_field(value) or None

    vision.name = _clean(vision.name)
    ocr.name = _clean(ocr.name)
    vision.tax_id = _clean(vision.tax_id)
    ocr.tax_id = _clean(ocr.tax_id)

    if vision.name and vision.tax_id:
        return ResolvedParty(
            raw_name=vision.name,
            display_name=vision.name,
            tax_id=vision.tax_id,
            dict_company=None,
            source="vision",
        )
    if ocr.name and ocr.tax_id:
        return ResolvedParty(
            raw_name=ocr.name,
            display_name=ocr.name,
            tax_id=ocr.tax_id,
            dict_company=None,
            source="ocr",
        )

    chosen_name = vision.name or ocr.name
    if chosen_name:
        tax_id = vision.tax_id or ocr.tax_id
        chosen_source: Literal["vision", "ocr"] = "vision" if chosen_name == vision.name else "ocr"
        return ResolvedParty(
            raw_name=chosen_name,
            display_name=chosen_name,
            tax_id=tax_id,
            dict_company=None,
            source=chosen_source,
        )

    return ResolvedParty(raw_name=None, display_name=None, tax_id=None, dict_company=None, source="none")


def _resolve_single_party(
    vision_name: str | None,
    vision_tax_id: str | None,
    ocr_name: str | None,
    ocr_tax_id: str | None,
    header_text: str,
    pool: dict[str, tuple[str, str | None, str | None]],
    directory: dict[str, list[dict[str, Any]]],
) -> ResolvedParty:
    header_text = header_text or ""
    vision_name = _clean_text_field(vision_name) or None
    ocr_name = _clean_text_field(ocr_name) or None
    vision_tax_id = _clean_text_field(vision_tax_id) or None
    ocr_tax_id = _clean_text_field(ocr_tax_id) or None
    # By tax id via dictionary
    for tax_id, source in ((vision_tax_id, "tax_vision"), (ocr_tax_id, "tax_ocr")):
        company = find_company_by_tax_id(tax_id or "", directory)
        if company:
            raw_name = vision_name or ocr_name or company.canonical
            return ResolvedParty(
                raw_name=raw_name,
                display_name=company.canonical,
                tax_id=company.tax_id,
                dict_company=company,
                source="dict",
            )
    # By fuzzy name vs dictionary
    for source, candidate in (("ocr_raw", ocr_name), ("vision_raw", vision_name)):
        if not candidate or len(candidate) < MIN_PARTY_LENGTH:
            continue
        canonical, code, tax_id, score = _match_company_code(candidate, pool)
        if canonical and (score or 0) >= 90:
            raw_name = candidate
            return ResolvedParty(
                raw_name=raw_name,
                display_name=canonical,
                tax_id=tax_id,
                dict_company=Company(canonical=canonical, aliases=[], code=code, tax_id=tax_id),
                source="name_match",
            )

    if ocr_name and len(ocr_name) >= MIN_PARTY_LENGTH:
        return ResolvedParty(
            raw_name=ocr_name,
            display_name=ocr_name,
            tax_id=ocr_tax_id or vision_tax_id,
            dict_company=None,
            source="ocr_raw",
        )
    if vision_name and len(vision_name) >= MIN_PARTY_LENGTH:
        return ResolvedParty(
            raw_name=vision_name,
            display_name=vision_name,
            tax_id=vision_tax_id or ocr_tax_id,
            dict_company=None,
            source="vision_raw",
        )
    return ResolvedParty(raw_name=None, display_name=None, tax_id=None, dict_company=None, source="none")


def _resolve_parties(
    vision_parties: VisionParties,
    ocr_parties: OcrParties | None,
    doc_type: str | None = None,
) -> ResolvedParties:
    if doc_type == "товарная_накладная":
        supplier = _choose_party(
            PartyCandidate(
                name=vision_parties.supplier_name,
                tax_id=vision_parties.supplier_tax_id,
                source="vision",
            ),
            PartyCandidate(
                name=ocr_parties.supplier_name if ocr_parties else None,
                tax_id=ocr_parties.supplier_tax_id if ocr_parties else None,
                source="ocr",
            ),
        )
        customer = _choose_party(
            PartyCandidate(
                name=vision_parties.customer_name,
                tax_id=vision_parties.customer_tax_id,
                source="vision",
            ),
            PartyCandidate(
                name=ocr_parties.customer_name if ocr_parties else None,
                tax_id=ocr_parties.customer_tax_id if ocr_parties else None,
                source="ocr",
            ),
        )

        logger.info(
            "[party_merge] Vision supplier=%s/%s | Vision customer=%s/%s | OCR supplier=%s/%s | OCR customer=%s/%s",
            vision_parties.supplier_name,
            vision_parties.supplier_tax_id,
            vision_parties.customer_name,
            vision_parties.customer_tax_id,
            ocr_parties.supplier_name if ocr_parties else None,
            ocr_parties.supplier_tax_id if ocr_parties else None,
            ocr_parties.customer_name if ocr_parties else None,
            ocr_parties.customer_tax_id if ocr_parties else None,
        )
        logger.info(
            "[party_merge] Chosen supplier=%s tax=%s source=%s | customer=%s tax=%s source=%s",
            supplier.display_name,
            supplier.tax_id,
            supplier.source,
            customer.display_name,
            customer.tax_id,
            customer.source,
        )
        return ResolvedParties(supplier=supplier, customer=customer)

    directory = load_company_directory()
    pool = _build_company_code_pool(directory)
    supplier = _resolve_single_party(
        vision_parties.supplier_name,
        vision_parties.supplier_tax_id,
        ocr_parties.supplier_name if ocr_parties else None,
        ocr_parties.supplier_tax_id if ocr_parties else None,
        ocr_parties.header_text if ocr_parties else "",
        pool,
        directory,
    )
    customer = _resolve_single_party(
        vision_parties.customer_name,
        vision_parties.customer_tax_id,
        ocr_parties.customer_name if ocr_parties else None,
        ocr_parties.customer_tax_id if ocr_parties else None,
        ocr_parties.header_text if ocr_parties else "",
        pool,
        directory,
    )

    logger.info(
        "[party_merge] Vision supplier=%s/%s | Vision customer=%s/%s | OCR supplier=%s/%s | OCR customer=%s/%s",
        vision_parties.supplier_name,
        vision_parties.supplier_tax_id,
        vision_parties.customer_name,
        vision_parties.customer_tax_id,
        ocr_parties.supplier_name if ocr_parties else None,
        ocr_parties.supplier_tax_id if ocr_parties else None,
        ocr_parties.customer_name if ocr_parties else None,
        ocr_parties.customer_tax_id if ocr_parties else None,
    )
    logger.info(
        "[party_merge] Chosen supplier=%s tax=%s source=%s | customer=%s tax=%s source=%s",
        supplier.display_name,
        supplier.tax_id,
        supplier.source,
        customer.display_name,
        customer.tax_id,
        customer.source,
    )
    return ResolvedParties(supplier=supplier, customer=customer)


def _apply_resolved_parties(document: ParsedDocument, resolved: ResolvedParties, header_text: str | None) -> None:
    if not document.warnings:
        document.warnings = []

    def _choose_company_name(resolved_party) -> tuple[str | None, str]:
        if resolved_party is None:
            return None, "none"

        company = getattr(resolved_party, "dict_company", None)
        if company is not None and getattr(company, "canonical", None):
            return company.canonical, "dict_canonical"

        display_name = getattr(resolved_party, "display_name", None)
        if display_name:
            return display_name, "display_name"

        raw_name = getattr(resolved_party, "raw_name", None)
        if raw_name:
            return raw_name, "raw_name"

        return None, "none"

    def _choose_company_tax_id(resolved_party) -> tuple[str | None, str]:
        if resolved_party is None:
            return None, "none"

        company = getattr(resolved_party, "dict_company", None)
        if company is not None and getattr(company, "tax_id", None):
            return company.tax_id, "dict_company"

        tax_id = getattr(resolved_party, "tax_id", None)
        if tax_id:
            return tax_id, "vision"

        return None, "none"

    document.party_header_text = header_text or document.party_header_text

    if resolved is None:
        document.warnings.append("parties_not_resolved")
        return

    supplier_name, supplier_name_source = _choose_company_name(resolved.supplier)
    customer_name, customer_name_source = _choose_company_name(resolved.customer)
    supplier_tax_id, _ = _choose_company_tax_id(resolved.supplier)
    customer_tax_id, _ = _choose_company_tax_id(resolved.customer)

    document.supplier_raw = (getattr(resolved.supplier, "raw_name", None) or getattr(resolved.supplier, "display_name", None)) or document.supplier_raw
    document.customer_raw = (getattr(resolved.customer, "raw_name", None) or getattr(resolved.customer, "display_name", None)) or document.customer_raw
    document.supplier_source = getattr(resolved.supplier, "source", None) or document.supplier_source
    document.customer_source = getattr(resolved.customer, "source", None) or document.customer_source

    document.supplier_company_code = (
        getattr(getattr(resolved.supplier, "dict_company", None), "code", None)
        or document.supplier_company_code
    )
    document.customer_company_code = (
        getattr(getattr(resolved.customer, "dict_company", None), "code", None)
        or document.customer_company_code
    )
    document.supplier_company_name = supplier_name or document.supplier_company_name
    document.customer_company_name = customer_name or document.customer_company_name

    if document.doc_type == "товарная_накладная":
        document.executor = supplier_name or document.executor
        document.contractor = customer_name or document.contractor
        document.counterparty = customer_name or document.counterparty
        document.main_counterparty = customer_name or document.main_counterparty

        if supplier_name_source == "none":
            document.warnings.append(
                "Не удалось прочитать название грузоотправителя — требуется ручная проверка"
            )
        elif supplier_name_source == "display_name":
            document.warnings.append("executor_from_vision_not_in_dict")

        if customer_name_source == "none":
            document.warnings.append(
                "Не удалось прочитать название грузополучателя — требуется ручная проверка"
            )
        elif customer_name_source == "display_name":
            document.warnings.append("customer_from_vision_not_in_dict")

        if supplier_name:
            if document.supplier:
                document.supplier.name = supplier_name
                document.supplier.unp = supplier_tax_id or document.supplier.unp
            else:
                document.supplier = Party(name=supplier_name, unp=supplier_tax_id)
        else:
            document.supplier = None

        if customer_name:
            if document.buyer:
                document.buyer.name = customer_name
                document.buyer.unp = customer_tax_id or document.buyer.unp
            else:
                document.buyer = Party(name=customer_name, unp=customer_tax_id)
        else:
            document.buyer = None
    else:
        document.supplier_raw = document.supplier_raw or document.executor
        document.customer_raw = document.customer_raw or document.contractor or document.main_counterparty


def _attach_company_codes(document: ParsedDocument) -> None:
    directory = load_company_directory()
    pool = _build_company_code_pool(directory)

    supplier_name, supplier_code, supplier_tax_id, _ = _match_company_code(document.supplier_raw, pool)
    customer_name, customer_code, customer_tax_id, _ = _match_company_code(document.customer_raw, pool)

    document.supplier_company_name = supplier_name or document.supplier_company_name
    document.customer_company_name = customer_name or document.customer_company_name
    document.supplier_company_code = supplier_code or document.supplier_company_code
    document.customer_company_code = customer_code or document.customer_company_code
    if document.supplier and supplier_tax_id:
        document.supplier.unp = document.supplier.unp or supplier_tax_id
    if document.buyer and customer_tax_id:
        document.buyer.unp = document.buyer.unp or customer_tax_id


def reconcile_company_field(model_value: str | None, raw_ocr_text: str | None) -> tuple[str | None, bool]:
    """Choose the best textual value between model output and OCR context."""

    cleaned_model = _clean_text_field(model_value)
    candidates = _extract_relevant_ocr_lines(raw_ocr_text)

    if not cleaned_model and candidates:
        best_candidate = _clean_text_field(candidates[0])
        return best_candidate or None, bool(best_candidate)

    best_value = cleaned_model
    best_score = 0.0

    for candidate in candidates:
        cleaned_candidate = _clean_text_field(candidate)
        if not cleaned_candidate:
            continue
        score = fuzz.partial_ratio(cleaned_model, cleaned_candidate)
        if score > best_score:
            best_score = score
            best_value = cleaned_candidate

    if best_score >= 85 and best_value:
        return best_value, best_value != cleaned_model
    return cleaned_model or None, False


def _convert_totals(totals_payload: Any) -> Totals:
    if not isinstance(totals_payload, dict):
        totals_payload = {}
    total_without_vat_raw = (
        totals_payload.get("total_without_vat")
        or totals_payload.get("amount_without_vat")
        or totals_payload.get("total_wo_vat")
        or totals_payload.get("sum_without_vat")
        or totals_payload.get("without_vat")
    )
    vat_amount_raw = totals_payload.get("vat_amount") or totals_payload.get("total_vat")
    total_with_vat_raw = (
        totals_payload.get("total_with_vat")
        or totals_payload.get("amount_with_vat")
        or totals_payload.get("total")
        or totals_payload.get("total_sum")
        or totals_payload.get("sum_with_vat")
        or totals_payload.get("with_vat")
    )
    vat_rate_raw = totals_payload.get("vat_rate") or totals_payload.get("vat_rate_percent")
    total_without_vat_dec, total_without_vat_text = _money_value(total_without_vat_raw)
    vat_amount_dec, vat_amount_text = _money_value(vat_amount_raw)
    total_with_vat_dec, total_with_vat_text = _money_value(total_with_vat_raw)
    vat_rate_dec, vat_rate_text = _money_value(vat_rate_raw)
    return Totals(
        total_without_vat=total_without_vat_dec,
        total_without_vat_text=total_without_vat_text,
        vat_amount=vat_amount_dec,
        vat_amount_text=vat_amount_text,
        total_with_vat=total_with_vat_dec,
        total_with_vat_text=total_with_vat_text,
        vat_rate_percent=vat_rate_dec,
        vat_rate_percent_text=vat_rate_text,
        currency=totals_payload.get("currency") if isinstance(totals_payload.get("currency"), str) else None,
    )


def _enrich_totals(parsed: ParsedDocument) -> None:
    totals = parsed.totals or Totals()
    if parsed.totals is None:
        parsed.totals = totals

    def _sum(values: list[Decimal | None]) -> Decimal | None:
        filtered = [v for v in values if v is not None]
        if not filtered:
            return None
        total = sum(filtered, start=Decimal("0"))
        if total < Decimal("0"):
            return None
        return total

    item_totals = [item.amount_with_vat or item.total_with_vat for item in parsed.items if item]
    item_without = [item.amount_without_vat for item in parsed.items if item]
    item_vat = [item.vat_amount for item in parsed.items if item]

    if totals.total_with_vat is None:
        totals.total_with_vat = _sum(item_totals)
    if totals.total_without_vat is None:
        totals.total_without_vat = _sum(item_without)
    if totals.vat_amount is None and item_vat:
        totals.vat_amount = _sum(item_vat)


def _compute_totals_from_items(items: list[Item]) -> Totals | None:
    valid_items = [
        it
        for it in items
        if it
        and _normalize_item_name(it.name or it.description)
        and any(
            field is not None
            for field in (
                it.amount_with_vat,
                it.total_with_vat,
                it.amount_without_vat,
                it.vat_amount,
            )
        )
    ]
    if not valid_items:
        return None

    def _sum(getter: callable) -> Decimal | None:
        total = Decimal("0")
        seen = False
        for item in valid_items:
            value = getter(item)
            if value is None:
                continue
            seen = True
            total += value
        return total if seen else None

    sum_without = _sum(lambda it: it.amount_without_vat)
    sum_vat = _sum(lambda it: it.vat_amount)
    sum_with = _sum(lambda it: it.amount_with_vat or it.total_with_vat)
    vat_rate = next((it.vat_rate for it in valid_items if it.vat_rate is not None), None)

    return Totals(
        total_without_vat=sum_without,
        vat_amount=sum_vat,
        total_with_vat=sum_with,
        vat_rate_percent=vat_rate,
    )


def _postprocess_items(items: list[Item], totals: Totals | None) -> list[Item]:
    if not items:
        return []
    total_with_vat = totals.total_with_vat if totals else None
    sum_items = Decimal("0")
    for item in items:
        if item.total_with_vat is not None:
            sum_items += item.total_with_vat
    if len(items) > 2 and total_with_vat is not None and abs(sum_items - total_with_vat) < Decimal("0.01"):
        grouped: dict[tuple[str, Decimal | None, Decimal | None], Item] = {}
        for item in items:
            key = (
                _normalize_item_name(item.name or item.description).lower(),
                item.price,
                item.vat_rate,
            )
            if key not in grouped:
                grouped[key] = item
                continue
            existing = grouped[key]
            if existing.quantity is not None and item.quantity is not None:
                existing.quantity += item.quantity
            existing.amount_with_vat = (existing.amount_with_vat or Decimal("0")) + (item.amount_with_vat or Decimal("0"))
            existing.total_with_vat = (existing.total_with_vat or Decimal("0")) + (item.total_with_vat or Decimal("0"))
            existing.amount_without_vat = (existing.amount_without_vat or Decimal("0")) + (item.amount_without_vat or Decimal("0"))
            existing.vat_amount = (existing.vat_amount or Decimal("0")) + (item.vat_amount or Decimal("0"))
        merged_items = list(grouped.values())
        logger.info(
            "[items_postprocess] merged %s raw items -> %s final items",
            len(items),
            len(merged_items),
        )
        return merged_items
    return items


def _flag_suspicious_items(document: ParsedDocument, layout: LayoutExtraction | None) -> None:
    words_count = len(layout.words) if layout and layout.words else 0
    if len(document.items) == 1 and words_count > 20:
        message = "⚠️ Количество позиций выглядит подозрительно, требуется проверка."
        if message not in document.warnings:
            document.warnings.append(message)
        logger.info(
            "[items_postprocess] flagged suspicious items_count=%s words=%s",
            len(document.items),
            words_count,
        )


def _apply_item_totals_priority(parsed: ParsedDocument) -> None:
    item_totals = _compute_totals_from_items(parsed.items)
    totals = parsed.totals or Totals()
    parsed.totals = totals
    eps = Decimal("1")

    def _is_close(a: Decimal | None, b: Decimal | None) -> bool:
        if a is None or b is None:
            return False
        return (a - b).copy_abs() <= eps

    if item_totals is None:
        if not any(
            getattr(totals, field)
            for field in ("total_without_vat", "vat_amount", "total_with_vat")
        ):
            parsed.warnings.append(
                "Не удалось надёжно распознать итоговые суммы по документу (ИТОГО)."
            )
        return

    for attr in ("total_without_vat", "vat_amount", "total_with_vat"):
        current = getattr(totals, attr)
        candidate = getattr(item_totals, attr)
        if current is None and candidate is not None:
            setattr(totals, attr, candidate)
            text_attr = f"{attr}_text"
            if hasattr(totals, text_attr):
                setattr(totals, text_attr, _decimal_to_text(candidate))

    mismatch = False
    llm_totals = parsed.llm_totals
    if llm_totals is None:
        parsed.warnings.append(
            "Не удалось надёжно распознать итоговые суммы по документу (ИТОГО)."
        )
    for attr in ("total_without_vat", "vat_amount", "total_with_vat"):
        llm_value = getattr(llm_totals, attr) if llm_totals else None
        candidate = getattr(item_totals, attr)
        if llm_value is None or candidate is None:
            continue
        if not _is_close(llm_value, candidate):
            mismatch = True
            break

    if mismatch:
        totals.total_without_vat = item_totals.total_without_vat
        totals.vat_amount = item_totals.vat_amount
        totals.total_with_vat = item_totals.total_with_vat
        totals.total_without_vat_text = _decimal_to_text(item_totals.total_without_vat)
        totals.vat_amount_text = _decimal_to_text(item_totals.vat_amount)
        totals.total_with_vat_text = _decimal_to_text(item_totals.total_with_vat)
        if item_totals.vat_rate_percent is not None:
            totals.vat_rate_percent = totals.vat_rate_percent or item_totals.vat_rate_percent
            totals.vat_rate_percent_text = _decimal_to_text(totals.vat_rate_percent)
        parsed.warnings.append(
            'Итоги по суммам в строке "ИТОГО" не совпадают с суммой по позициям. '
            "Взяты суммы, посчитанные по позициям."
        )
        parsed.sums_suspect = False


def _recompute_vat_from_rate(target: Totals, warnings: list[str]) -> None:
    if target.vat_amount is not None:
        return
    if target.total_without_vat is None:
        return
    if target.vat_rate_percent is None:
        return
    vat = (target.total_without_vat * target.vat_rate_percent / Decimal("100")).quantize(
        Decimal("0.01"),
    )
    target.vat_amount = vat
    target.total_vat = vat
    warnings.append(
        f"Сумма НДС пересчитана по ставке {format_amount_ru(target.vat_rate_percent)}%"
    )


def _validate_totals_consistency(parsed: ParsedDocument) -> None:
    totals = parsed.totals
    if totals is None:
        return
    warnings = parsed.warnings
    currency = parsed.currency or parsed.currency_code
    mismatch_detected = False

    if (
        totals.total_without_vat is not None
        and totals.vat_rate_percent is not None
        and totals.total_with_vat is not None
    ):
        expected = (totals.total_without_vat * (Decimal("1") + totals.vat_rate_percent / Decimal("100"))).quantize(
            Decimal("0.01")
        )
        if abs(expected - totals.total_with_vat) > Decimal("0.01"):
            warnings.append(
                "Суммы не сходятся: по ставке "
                f"{format_amount_ru(totals.vat_rate_percent)}% ожидается {format_amount_ru(expected)} {currency or ''}, "
                f"получено {format_amount_ru(totals.total_with_vat)} {currency or ''}"
            )
            parsed.sums_suspect = True
            mismatch_detected = True

    if (
        totals.total_without_vat is not None
        and totals.vat_amount is not None
        and totals.total_with_vat is not None
    ):
        expected = (totals.total_without_vat + totals.vat_amount).quantize(Decimal("0.01"))
        if abs(expected - totals.total_with_vat) > Decimal("0.01"):
            warnings.append(
                "Суммы не сходятся: сумма без НДС + НДС не равна итогу с НДС"
            )
            parsed.sums_suspect = True
            mismatch_detected = True

    if mismatch_detected:
        warnings.append("⚠️ Суммы в документе выглядят несогласованными, проверьте вручную.")


def _reconcile_items_with_text(parsed: ParsedDocument, raw_text: str | None) -> None:
    if not parsed.items:
        return
    tariff_value, tariff_text = _extract_tariff_from_text(raw_text)
    if tariff_value is None:
        return
    for item in parsed.items:
        if item.price is None:
            item.price = tariff_value
            item.price_text = tariff_text
            continue
        if _has_price_mismatch(item.price, tariff_value):
            parsed.warnings.append(
                "Несовпадение цены: по тексту "
                f"{format_amount_ru(tariff_value)} {parsed.currency or parsed.currency_code}, "
                f"модель вернула {format_amount_ru(item.price)}"
            )
            parsed.sums_suspect = True
            item.price = tariff_value
            item.price_text = tariff_text


def _reconcile_totals_with_text(parsed: ParsedDocument, raw_text: str | None) -> None:
    totals = parsed.totals or Totals()
    if parsed.totals is None:
        parsed.totals = totals
    text_totals = _extract_totals_from_text(raw_text)
    currency = parsed.currency or parsed.currency_code or totals.currency

    def _apply_value(attr: str, text_attr: str | None) -> None:
        text_value, text_raw = text_totals.get(attr, (None, None)) if text_totals else (None, None)
        current_value = getattr(totals, attr)
        if text_value is not None:
            if current_value is not None and _has_significant_mismatch(current_value, text_value):
                parsed.warnings.append(
                    "Несовпадение сумм: по тексту "
                    f"{format_amount_ru(text_value)} {currency or ''}, модель вернула "
                    f"{format_amount_ru(current_value)} {currency or ''}"
                )
                parsed.sums_suspect = True
            setattr(totals, attr, text_value)
            if text_attr:
                setattr(totals, text_attr, text_raw or _decimal_to_text(text_value))
        elif text_attr and getattr(totals, text_attr) is None:
            setattr(totals, text_attr, _decimal_to_text(current_value))

    _apply_value("total_without_vat", "total_without_vat_text")
    _apply_value("vat_amount", "vat_amount_text")
    _apply_value("total_with_vat", "total_with_vat_text")

    text_rate, text_rate_raw = text_totals.get("vat_rate_percent", (None, None)) if text_totals else (None, None)
    if text_rate is not None:
        totals.vat_rate_percent = text_rate
        totals.vat_rate_percent_text = text_rate_raw or _decimal_to_text(text_rate)

    _recompute_vat_from_rate(totals, parsed.warnings)

    if parsed.items:
        sum_without = sum((item.amount_without_vat or Decimal("0")) for item in parsed.items)
        sum_vat = sum((item.vat_amount or Decimal("0")) for item in parsed.items)
        sum_with = sum(
            ((item.amount_with_vat or item.total_with_vat or item.amount_without_vat or Decimal("0")))
            for item in parsed.items
        )

        if totals.total_without_vat is None:
            totals.total_without_vat = sum_without
            totals.total_without_vat_text = _decimal_to_text(sum_without)
        if totals.vat_amount is None:
            totals.vat_amount = sum_vat
            totals.vat_amount_text = _decimal_to_text(sum_vat)
        if totals.total_with_vat is None:
            totals.total_with_vat = sum_with
            totals.total_with_vat_text = _decimal_to_text(sum_with)

        if any(
            [
                totals.total_without_vat is not None
                and _has_significant_mismatch(totals.total_without_vat, sum_without),
                totals.vat_amount is not None
                and _has_significant_mismatch(totals.vat_amount, sum_vat),
                totals.total_with_vat is not None
                and _has_significant_mismatch(totals.total_with_vat, sum_with),
            ]
        ):
            totals.total_without_vat = sum_without
            totals.vat_amount = sum_vat
            totals.total_with_vat = sum_with
            totals.total_without_vat_text = _decimal_to_text(sum_without)
            totals.vat_amount_text = _decimal_to_text(sum_vat)
            totals.total_with_vat_text = _decimal_to_text(sum_with)

    if totals.total_with_vat is not None and parsed.items:
        sum_positions = sum(
            (item.amount_with_vat or item.total_with_vat or Decimal("0") for item in parsed.items),
            Decimal("0"),
        )
        if _has_significant_mismatch(totals.total_with_vat, sum_positions):
            parsed.warnings.append(
                "Сумма по позициям не совпадает с итогом с НДС"
            )
            parsed.sums_suspect = True


def _normalize_doc_type(raw_type: str | None) -> str:
    if not raw_type:
        return "прочее"
    raw = raw_type.lower().strip()
    mapping = {
        "invoice": "счет-фактура",
        "act": "акт",
        "combo": "акт",
        "unknown": "прочее",
        "акт": "акт",
        "акт выполненных работ": "акт",
        "счет-фактура": "счет-фактура",
        "счёт-фактура": "счет-фактура",
        "счет": "счет",
        "товарная накладная": "товарная_накладная",
        "накладная": "товарная_накладная",
        "waybill": "товарная_накладная",
        "ттн": "товарно-транспортная_накладная",
        "товарно-транспортная накладная": "товарно-транспортная_накладная",
        "счет-протокол": "счет-протокол",
        "счёт-протокол": "счет-протокол",
    }
    return mapping.get(raw, raw)


def _doc_type_from_text(raw_text: str | None, current: str | None) -> str | None:
    if current and current != "прочее":
        return current
    if not raw_text:
        return current
    lowered = raw_text.lower()
    if "акт" in lowered:
        return "акт"
    if "накладн" in lowered:
        return "товарная_накладная"
    return current


SERVICE_KEYWORDS = (
    "услуг",
    "оказан",
    "работ",
    "подряд",
    "монтаж",
    "ремонт",
    "обслужив",
    "договор",
    "аренд",
    "поддержк",
)


def _refine_doc_type(parsed: ParsedDocument) -> None:
    """Use text and item signals to disambiguate акт vs товарная накладная."""

    doc_type = parsed.doc_type or "прочее"
    text = (parsed.raw_text or parsed.raw_ocr_text or "").lower()
    has_act_phrase = "акт" in text
    has_waybill_phrase = "накладн" in text or "ттн" in text

    qty_items = sum(1 for item in parsed.items if item.quantity is not None)
    has_units = any(item.unit for item in parsed.items)
    service_named = any(
        any(keyword in (item.name or "").lower() for keyword in SERVICE_KEYWORDS)
        for item in parsed.items
    )
    goods_like = qty_items > 0 or has_units

    if has_act_phrase and not has_waybill_phrase:
        parsed.doc_type = "акт"
        return
    if has_waybill_phrase and not has_act_phrase:
        parsed.doc_type = "товарная_накладная"
        return

    if doc_type == "товарная_накладная" and service_named and not goods_like:
        parsed.doc_type = "акт"
        return
    if doc_type in {"акт", "прочее"} and goods_like and not service_named:
        parsed.doc_type = "товарная_накладная"


def _convert_to_parsed_document(payload: dict[str, Any]) -> ParsedDocument:
    settings = get_settings()
    seller_payload = (
        payload.get("contractor")
        or payload.get("seller")
        or payload.get("supplier")
        or payload.get("counterparty_supplier")
        or {}
    )
    buyer_payload = (
        payload.get("executor")
        or payload.get("buyer")
        or payload.get("counterparty_customer")
        or {}
    )
    if isinstance(seller_payload, str):
        seller_payload = {"name": seller_payload}
    if isinstance(buyer_payload, str):
        buyer_payload = {"name": buyer_payload}
    fields = payload.get("fields") if isinstance(payload.get("fields"), dict) else {}
    doc_type_raw = payload.get("doc_type") if isinstance(payload.get("doc_type"), str) else payload.get("document_type")
    doc_type = _normalize_doc_type(doc_type_raw)
    confidence_block = payload.get("confidence") if isinstance(payload.get("confidence"), dict) else {}

    doc_number = payload.get("doc_number") or payload.get("number") or fields.get("document_number")
    if not isinstance(doc_number, str):
        doc_number = None
    doc_date = _parse_date(payload.get("doc_date") or payload.get("date") or fields.get("document_date"))
    currency = payload.get("currency") if isinstance(payload.get("currency"), str) else fields.get("currency")

    seller = _extract_party_payload(
        seller_payload,
        short_fallback=payload.get("supplier_short"),
        full_fallback=payload.get("supplier_full"),
    )
    buyer = _extract_party_payload(
        buyer_payload,
        short_fallback=payload.get("buyer_short"),
        full_fallback=payload.get("buyer_full"),
    )

    totals_payload = payload.get("totals") if isinstance(payload.get("totals"), dict) else fields.get("totals")
    totals_row_payload = payload.get("totals_row") if isinstance(payload.get("totals_row"), dict) else None
    if totals_payload is None and totals_row_payload is not None:
        totals_payload = totals_row_payload
    elif isinstance(totals_row_payload, dict):
        totals_payload = totals_payload or {}
        totals_payload.setdefault("without_vat", totals_row_payload.get("sum_without_vat"))
        totals_payload.setdefault("vat_amount", totals_row_payload.get("vat_amount"))
        totals_payload.setdefault("with_vat", totals_row_payload.get("sum_with_vat"))
    if totals_payload is None:
        totals_payload = {
            "without_vat": payload.get("total_without_vat") or payload.get("total_wo_vat"),
            "vat_amount": payload.get("vat_amount") or payload.get("total_vat"),
            "with_vat": payload.get("total_with_vat") or payload.get("total_sum"),
            "currency": currency,
        }
    items_source = payload.get("items") or payload.get("positions")
    items = _filter_and_deduplicate_items(_convert_items(items_source))
    totals = _convert_totals(totals_payload)
    llm_totals_copy = totals.model_copy(deep=True) if totals else None
    subject_value = items[0].name if items else None

    warnings_raw = payload.get("warnings")
    warnings = [str(item) for item in warnings_raw] if isinstance(warnings_raw, list) else []
    notes_payload = payload.get("notes") if isinstance(payload.get("notes"), dict) else {}
    if isinstance(notes_payload.get("warnings"), list):
        warnings.extend(str(item) for item in notes_payload.get("warnings", []))
    if any(
        item
        for item in items
        if item
        and any(
            field is None
            for field in (
                item.price,
                item.amount_without_vat,
                item.vat_amount,
                item.amount_with_vat,
            )
        )
    ):
        warnings.append(
            "По отдельным позициям не удалось распознать цену или суммы, проверьте вручную."
        )
    raw_text = payload.get("raw_text") if isinstance(payload.get("raw_text"), str) else None
    raw_ocr_text = payload.get("raw_ocr_text") if isinstance(payload.get("raw_ocr_text"), str) else raw_text

    contractor_value = seller.name if seller else None
    executor_value = buyer.name if buyer else None
    if doc_type == "товарная_накладная":
        contractor_value = buyer.name if buyer else contractor_value
        executor_value = seller.name if seller else executor_value

    parsed = ParsedDocument(
        doc_type=doc_type,
        doc_number=doc_number,
        doc_date=doc_date,
        contractor=contractor_value,
        executor=executor_value,
        counterparty=contractor_value,
        currency=currency,  # type: ignore[arg-type]
        number=doc_number,
        date=doc_date,
        main_counterparty=contractor_value,
        subject=subject_value,
        items=items,
        main_item=items[0] if items else None,
        totals=totals,
        llm_totals=llm_totals_copy,
        warnings=warnings,
        service_month=None,
        contract_number=None,
        notes=None,
        raw_text=raw_text,
        raw_ocr_text=raw_ocr_text,
        supplier=seller,
        buyer=buyer,
    )

    parsed.doc_type = _doc_type_from_text(raw_text, parsed.doc_type) or parsed.doc_type

    if not parsed.main_item and parsed.items:
        parsed.main_item = parsed.items[0]

    raw_text_for_rules = raw_text or raw_ocr_text
    _reconcile_totals_with_text(parsed, raw_text_for_rules)
    _reconcile_items_with_text(parsed, raw_text_for_rules)

    _enrich_totals(parsed)
    _apply_item_totals_priority(parsed)
    totals = parsed.totals or totals
    _recompute_vat_from_rate(totals, parsed.warnings)
    _validate_totals_consistency(parsed)

    _refine_doc_type(parsed)

    _attach_confidence_warnings(
        parsed,
        confidence_block,
        seller_payload,
        buyer_payload,
        items_source if isinstance(items_source, list) else [],
        settings,
    )

    if not items:
        parsed.warnings.append("Не удалось распознать строки товаров/услуг")
    if not totals.total_with_vat and not totals.total_without_vat:
        parsed.warnings.append("Не удалось определить итоговые суммы")
    if parsed.currency_code == "UNKNOWN" and parsed.currency is None:
        if totals.currency:
            parsed.currency = totals.currency  # type: ignore[assignment]
            parsed.currency_code = totals.currency  # type: ignore[assignment]
        elif parsed.items:
            for item in parsed.items:
                if item.currency:
                    parsed.currency = item.currency  # type: ignore[assignment]
                    parsed.currency_code = item.currency  # type: ignore[assignment]
                    break
    return parsed


def _apply_company_normalization(
    document: ParsedDocument, raw_text: str | None
) -> tuple[list[str], bool]:
    warnings: list[str] = []
    blocking = False
    text_for_check = raw_text or ""
    settings = get_settings()
    directory = load_company_directory()
    our_pool = _build_alias_pool(directory.get("our_companies", []))
    counter_pool = _build_alias_pool(directory.get("counterparties", []))
    is_waybill = document.doc_type in {"товарная_накладная", "товарно-транспортная_накладная", "waybill"}
    if settings.our_org_name:
        our_pool[_clean_text_field(settings.our_org_name)] = settings.our_org_name
    for extra in getattr(settings, "our_companies", []):
        cleaned = _clean_text_field(extra)
        if cleaned:
            our_pool[cleaned] = extra

    def _trim_company_name(value: str | None) -> str | None:
        if not value:
            return value
        stripped = re.split(r",\s*(адрес|юр\.\s*адрес|место нахождения)", value, flags=re.IGNORECASE)[0]
        stripped = re.split(r",\s*\d{3,6}", stripped)[0]
        return stripped.strip()

    def _update_field(field_name: str, value: str | None) -> str | None:
        if not value:
            return value
        base_value = _trim_company_name(value)
        value = base_value
        reconciled, replaced = reconcile_company_field(value, raw_text)
        if replaced and reconciled:
            warnings.append(f"normalize:{field_name}:replaced_with_ocr")
            value = reconciled
        result = normalize_company_name(value)
        if result.normalized:
            if result.corrected:
                warnings.append(
                    f"normalize:{field_name}:{value} -> {result.normalized} (score={result.score})"
                )
            if not is_waybill:
                value = result.normalized
        else:
            loose = normalize_company_name_loose(value, raw_text)
            if loose:
                warnings.append(f"normalize:{field_name}:{value} -> {loose} (loose_match)")
                if not is_waybill:
                    value = loose
            else:
                warnings.append(f"normalize:{field_name}:not_recognized")
                if not is_waybill:
                    value = None
        if is_waybill and not value:
            value = base_value
        return value

    def _classify(name: str | None) -> tuple[str | None, str | None, float | None]:
        cleaned = _trim_company_name(name)
        if not cleaned:
            return None, None, None
        canonical_our, score_our = _alias_score(cleaned, our_pool, threshold=92)
        canonical_counter, score_counter = _alias_score(cleaned, counter_pool, threshold=92)
        if canonical_our:
            return canonical_our, "our", score_our
        if canonical_counter:
            return canonical_counter, "counterparty", score_counter
        best_score = max(score_our or 0, score_counter or 0) or None
        return cleaned, None, best_score

    document.executor = _update_field("executor", document.executor)
    document.contractor = _update_field("contractor", document.contractor)

    if document.supplier and document.supplier.name:
        document.supplier.name = _update_field("supplier", document.supplier.name)
    if document.buyer and document.buyer.name:
        document.buyer.name = _update_field("buyer", document.buyer.name)

    candidates = [
        (document.executor, "executor"),
        (document.contractor, "contractor"),
        (document.buyer.name if document.buyer else None, "buyer"),
        (document.supplier.name if document.supplier else None, "supplier"),
    ]

    classified = []
    for value, label in candidates:
        canonical, role, score = _classify(value)
        if canonical and role is None:
            warnings.append(f"Название {label} не найдено в словаре компаний")
        classified.append((canonical, role, label))

    our_company = next((c for c in classified if c[1] == "our" and c[0]), None)
    counterparty_company = next(
        (c for c in classified if c[1] == "counterparty" and c[0]), None
    )

    if our_company and not is_waybill:
        document.executor = our_company[0]
        if not document.supplier:
            document.supplier = Party(name=our_company[0])
    if counterparty_company and not (is_waybill and document.contractor):
        document.contractor = counterparty_company[0]
        document.main_counterparty = counterparty_company[0]
        document.counterparty = counterparty_company[0]

    if not document.main_counterparty and document.contractor:
        document.main_counterparty = document.contractor

    if (
        document.main_counterparty
        and our_company
        and document.main_counterparty == our_company[0]
        and not is_waybill
    ):
        fallback = next((c[0] for c in classified if c[0] and c[0] != our_company[0]), None)
        if fallback:
            document.main_counterparty = fallback
            document.contractor = fallback
            document.counterparty = fallback

    if text_for_check:
        for field_name in ("executor", "contractor"):
            value = getattr(document, field_name)
            if value and not normalize_in_text(value, text_for_check):
                warnings.append(f"Контрагент {field_name} не найден в raw_ocr_text")
    if not document.executor and not document.contractor:
        warnings.append(
            "Названия организаций (поставщик, покупатель) не удалось надёжно распознать из-за качества изображения"
        )
    return warnings, blocking


class VisionExtractionError(ParseError):
    """Raised when structured response cannot be parsed or validated."""


VisionParseError = VisionExtractionError


__all__ = ["VisionExtractor", "get_vision_extractor", "VisionExtractionError", "VisionParseError"]
