from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Iterable, Literal, Tuple

from config import get_settings
from models.parser_types import OCRDateCandidate, OCRResult, ParsedDocument, ParsedItem

MONTHS = {
    "января": 1,
    "февраля": 2,
    "марта": 3,
    "апреля": 4,
    "мая": 5,
    "июня": 6,
    "июля": 7,
    "августа": 8,
    "сентября": 9,
    "октября": 10,
    "ноября": 11,
    "декабря": 12,
}

AMOUNT_RE = re.compile(r"(?:(?:\d{1,3}[\s\u00A0])?\d{1,3}(?:[\s\u00A0]\d{3})*|\d+)(?:[,.]\d{1,2})?")
DATE_PATTERNS = [
    re.compile(r"\b(\d{1,2})[./](\d{1,2})[./](\d{2,4})\b"),
    re.compile(
        r"\b(\d{1,2})\s+"
        r"(января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)"
        r"\s+(\d{4})(?:\s*г(?:\.|ода)?)?",
        re.IGNORECASE,
    ),
]
SERVICE_DATE_MARKERS = (
    "форма установлена",
    "постановлением",
    "постановление",
    "код формы",
    "минфин",
    "министерства финансов",
)
CONTRACT_MARKERS = ("договор", "дог.", "контракт")


class _Candidate:
    def __init__(self, raw: str, parsed: date | None, start: int, context: str, kind: str = "unknown"):
        self.raw = raw
        self.parsed = parsed
        self.start = start
        self.context = context
        self.kind = kind

    def to_dict(self) -> dict[str, Any]:
        return {"raw": self.raw, "parsed": self.parsed, "start": self.start, "kind": self.kind, "context": self.context}


# Public API ---------------------------------------------------------------

def parse_document(ocr_result: OCRResult) -> ParsedDocument:
    text = ocr_result.text
    lower_text = text.lower()

    doc_type, type_debug = _detect_doc_type(lower_text)
    doc_number, number_debug = _extract_document_number(text, lower_text, doc_type)
    doc_date, date_debug, date_warnings = _extract_document_date(text, lower_text, doc_type, ocr_result.meta.get("dates") or [])
    supplier, customer, counterpart_debug = _extract_counterparties(text, lower_text, doc_type)
    counterparty = _resolve_counterparty(supplier, customer)
    items, totals_debug = _extract_items_and_totals(text)

    warnings = []
    warnings.extend(date_warnings)
    warnings.extend(totals_debug.get("warnings", []))

    confidence = _estimate_confidence(doc_number, doc_date, counterparty, items)

    debug_info: dict[str, Any] = {
        "doc_type": type_debug,
        "number": number_debug,
        "dates": date_debug,
        "counterparties": counterpart_debug,
        "items": totals_debug,
    }

    return ParsedDocument(
        doc_type=doc_type,
        doc_number=doc_number,
        doc_date=doc_date,
        supplier=supplier,
        customer=customer,
        counterparty=counterparty,
        items=items,
        currency=None,
        confidence=confidence,
        warnings=warnings,
        raw_text=text,
        total_sum=totals_debug.get("total_sum"),
        vat_sum=totals_debug.get("vat_sum"),
        debug_info=debug_info,
    )


# Detection helpers -------------------------------------------------------

def _detect_doc_type(lower_text: str) -> Tuple[str, dict[str, Any]]:
    debug = {"matched": None, "candidates": []}
    if "акт оказанных услуг" in lower_text or "акт выполненных работ" in lower_text:
        debug["matched"] = "акт оказанных услуг/выполненных работ"
        return "акт", debug
    if "товарная накладная" in lower_text:
        debug["matched"] = "товарная накладная"
        return "накладная", debug
    if "акт" in lower_text and "наклад" in lower_text:
        debug["matched"] = "оба типа, приоритет акт"
        return "акт", debug
    if "накладная" in lower_text or "накладн" in lower_text:
        debug["matched"] = "накладная"
        return "накладная", debug
    if "акт" in lower_text:
        debug["matched"] = "акт"
        return "акт", debug
    debug["matched"] = "fallback акт"
    return "акт", debug


def _extract_document_number(text: str, lower_text: str, doc_type: str) -> Tuple[str | None, dict[str, Any]]:
    debug = {"candidates": []}
    patterns: list[re.Pattern[str]] = []
    if doc_type == "накладная":
        patterns.append(re.compile(r"товарная\s+накладная\s+([\d\w-]+)", re.IGNORECASE))
        patterns.append(re.compile(r"накладная[^\n]{0,50}?№?\s*([\d\w/-]+)", re.IGNORECASE))
    else:
        patterns.append(re.compile(r"\bакт[^\n]{0,80}?№\s*([\d\w/ -]+?)(?:\s+от\b|\n|$)", re.IGNORECASE))

    for pattern in patterns:
        match = pattern.search(text)
        if match:
            number = match.group(1).strip().replace(" ", "")
            debug["candidates"].append({"value": number, "pattern": pattern.pattern})
            return number, debug

    nearby_number = _number_near_header(text, doc_type)
    if nearby_number:
        debug["candidates"].append({"value": nearby_number, "pattern": "header"})
    return nearby_number, debug


def _number_near_header(text: str, doc_type: str) -> str | None:
    lines = text.splitlines()[:5]
    for line in lines:
        if doc_type in line.lower():
            match = re.search(r"№\s*([\d\w/-]{2,})", line)
            if match:
                return match.group(1).replace(" ", "")
    return None


# Date extraction ---------------------------------------------------------

def _extract_document_date(
    text: str, lower_text: str, doc_type: str, raw_candidates: Iterable[OCRDateCandidate]
) -> Tuple[date | None, dict[str, Any], list[str]]:
    debug: dict[str, Any] = {"candidates": [], "rejected": []}
    warnings: list[str] = []

    candidates: list[_Candidate] = []
    for match in _iter_dates(text):
        parsed = _parse_date(match.group(0))
        start = match.start()
        context = _extract_context(text, start, match.end())
        candidates.append(_Candidate(match.group(0), parsed, start, context))

    for c in raw_candidates:
        parsed = _parse_date(c.raw_value)
        candidates.append(_Candidate(c.raw_value, parsed, c.start_index, c.context, c.kind))

    filtered: list[_Candidate] = []
    contract_capture: _Candidate | None = None
    for cand in candidates:
        context_low = cand.context.lower()
        if any(marker in context_low for marker in SERVICE_DATE_MARKERS) and not (
            "наклад" in context_low or "акт" in context_low
        ):
            debug["rejected"].append({"raw": cand.raw, "reason": "service"})
            continue
        if any(marker in context_low for marker in CONTRACT_MARKERS) and " от " in context_low:
            cand.kind = "contract"
            if contract_capture is None:
                contract_capture = cand
        elif "наклад" in context_low or "акт" in context_low or " от " in context_low:
            cand.kind = "document"
        filtered.append(cand)

    unique_dates = {c.raw for c in filtered}
    if len(unique_dates) >= 3:
        warnings.append("Найдено несколько разных дат — проверьте вручную")

    header_pos = _header_position(lower_text, doc_type)

    preferred = _date_near_title(text, doc_type)
    if preferred:
        debug["candidates"].append({"preferred": preferred.to_dict()})
        return preferred.parsed, debug, warnings

    document_candidates = [c for c in filtered if c.kind == "document" and c.parsed]
    fallback_candidates = [c for c in filtered if c.parsed and c.kind != "service"]
    pool = document_candidates or fallback_candidates
    if not pool:
        debug["candidates"] = [c.to_dict() for c in filtered]
        return None, debug, warnings

    pool = sorted(
        pool,
        key=lambda c: (-c.parsed.year if c.parsed else 0, abs((c.start or 0) - header_pos), c.start),
    )
    selected = pool[0]
    debug["candidates"] = [c.to_dict() for c in filtered]

    contract_dates = [c for c in filtered if c.kind == "contract" and c.parsed]
    if contract_capture:
        debug["contract"] = contract_capture.to_dict()
    if contract_dates and selected.parsed:
        for c in contract_dates:
            if c.parsed and abs(c.parsed.year - selected.parsed.year) >= 5:
                warnings.append("Год документа сильно отличается от года договора")
                break

    return selected.parsed, debug, warnings


def _iter_dates(text: str):
    for pattern in DATE_PATTERNS:
        for match in pattern.finditer(text):
            yield match


def _extract_context(text: str, start: int, end: int) -> str:
    start_idx = max(0, start - 60)
    end_idx = min(len(text), end + 60)
    return text[start_idx:end_idx]


def _header_position(lower_text: str, doc_type: str) -> int:
    idx = lower_text.find("товарная накладная" if doc_type == "накладная" else "акт")
    return idx if idx >= 0 else len(lower_text) // 2


def _date_near_title(text: str, doc_type: str) -> _Candidate | None:
    keyword = "наклад" if doc_type == "накладная" else "акт"
    header_regex = re.compile(rf"\b{keyword}[^\n]{{0,80}}?от\s+([^\n]+)", re.IGNORECASE)
    match = header_regex.search(text)
    if not match:
        return None
    for date_match in _iter_dates(match.group(0)):
        parsed = _parse_date(date_match.group(0))
        if parsed:
            start = match.start() + date_match.start()
            context = _extract_context(text, start, match.start() + date_match.end())
            return _Candidate(date_match.group(0), parsed, start, context, kind="document")
    return None


def _parse_date(value: str) -> date | None:
    value = value.strip()
    for fmt in ("%d.%m.%Y", "%d.%m.%y"):
        try:
            parsed = datetime.strptime(value, fmt).date()
            if parsed.year < 1900:
                continue
            return parsed
        except ValueError:
            continue
    month_match = re.match(
        r"(\d{1,2})\s+(января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)\s+(\d{4})",
        value,
        re.IGNORECASE,
    )
    if month_match:
        day = int(month_match.group(1))
        month = MONTHS[month_match.group(2).lower()]
        year = int(month_match.group(3))
        try:
            return date(year, month, day)
        except ValueError:
            return None
    return None


# Counterparties ----------------------------------------------------------

def _extract_counterparties(
    text: str, lower_text: str, doc_type: str
) -> tuple[str | None, str | None, dict[str, Any]]:
    supplier = None
    customer = None
    debug: dict[str, Any] = {"matches": []}
    lines = [line.strip() for line in text.splitlines() if line.strip()]

    markers = {
        "supplier": ["грузоотправитель", "поставщик", "исполнитель"],
        "customer": ["грузополучатель", "покупатель", "заказчик", "арендатор"],
    }
    for idx, line in enumerate(lines):
        low = line.lower()
        for key, marker_list in markers.items():
            if any(marker in low for marker in marker_list):
                next_line = lines[idx + 1] if idx + 1 < len(lines) else ""
                value = _clean_name(next_line or line.split(":", 1)[-1])
                debug["matches"].append({"role": key, "line": line, "value": value})
                if key == "supplier" and not supplier:
                    supplier = value
                if key == "customer" and not customer:
                    customer = value

    return supplier or None, customer or None, debug


def _clean_name(name: str) -> str:
    cleaned = name.replace("\"", "").replace("'", "").strip()
    parts = cleaned.split(",")
    return parts[0].strip()


def _resolve_counterparty(supplier: str | None, customer: str | None) -> str | None:
    try:
        settings = get_settings()
        our_name = getattr(settings, "our_org_name", "").lower()
    except Exception:  # pragma: no cover
        our_name = ""
    normalized_supplier = (supplier or "").lower()
    normalized_customer = (customer or "").lower()
    if our_name and our_name in normalized_supplier:
        return customer or supplier
    if our_name and our_name in normalized_customer:
        return supplier or customer
    return customer or supplier


# Items and totals --------------------------------------------------------

def _extract_items_and_totals(text: str) -> tuple[list[ParsedItem], dict[str, Any]]:
    items: list[ParsedItem] = []
    debug: dict[str, Any] = {"rows": [], "warnings": []}
    lines = [line for line in text.splitlines() if line.strip()]
    started = False
    for line in lines:
        low = line.lower()
        if not started and ("товар" in low and "раздел" in low):
            started = True
            if not any(ch.isdigit() for ch in line):
                continue
        if not started:
            continue
        if not any(ch.isdigit() for ch in line):
            continue
        amounts = AMOUNT_RE.findall(line)
        if len(amounts) < 2:
            continue
        try:
            total = Decimal(amounts[-1].replace(" ", "").replace(",", "."))
        except Exception:
            continue
        vat_amount = None
        if len(amounts) >= 3:
            try:
                vat_amount = Decimal(amounts[-2].replace(" ", "").replace(",", "."))
            except Exception:
                vat_amount = None
        name_part = line.split(amounts[0])[0].strip()
        item = ParsedItem(
            name=name_part or "Товар",
            quantity=None,
            price=None,
            total_with_vat=total,
            vat_amount=vat_amount,
            vat_rate=20 if "20" in line else None,
        )
        items.append(item)
        debug["rows"].append({"line": line, "amounts": amounts})
        break

    vat_sum, total_sum = _scan_totals(lines)
    if vat_sum is not None and items and items[0].vat_amount is None:
        items[0].vat_amount = vat_sum
    if total_sum is not None and items and items[0].total_with_vat is None:
        items[0].total_with_vat = total_sum

    if items and items[0].total_with_vat and items[0].quantity and items[0].price:
        expected = items[0].quantity * items[0].price
        if abs(expected - items[0].total_with_vat) > Decimal("0.02"):
            debug["warnings"].append("Сумма позиции не сходится с количеством и ценой")

    debug["vat_sum"] = vat_sum
    debug["total_sum"] = total_sum
    return items, debug


def _scan_totals(lines: list[str]) -> tuple[Decimal | None, Decimal | None]:
    vat_sum = None
    total_sum = None
    for line in lines:
        low = line.lower()
        if vat_sum is None and ("сумма ндс" in low or "ндс" in low):
            amounts = AMOUNT_RE.findall(line)
            if amounts:
                try:
                    vat_sum = Decimal(amounts[-1].replace(" ", "").replace(",", "."))
                except Exception:
                    vat_sum = None
        if total_sum is None and ("итого" in low or "к оплате" in low or "с ндс" in low):
            amounts = AMOUNT_RE.findall(line)
            if amounts:
                try:
                    total_sum = Decimal(amounts[-1].replace(" ", "").replace(",", "."))
                except Exception:
                    total_sum = None
    return vat_sum, total_sum


# Confidence --------------------------------------------------------------

def _estimate_confidence(
    number: str | None, doc_date: date | None, counterparty: str | None, items: list[ParsedItem]
) -> float:
    score = 0.0
    score += 0.25 if number else 0
    score += 0.35 if doc_date else 0
    score += 0.25 if counterparty else 0
    score += 0.15 if items else 0
    return min(score, 1.0)


__all__ = ["parse_document"]
