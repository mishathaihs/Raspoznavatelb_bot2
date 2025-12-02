from __future__ import annotations

import copy
import logging
import re
from decimal import Decimal
import copy
import logging
import re
from pathlib import Path
from typing import Any, Iterable

from docx import Document
from docx.table import _Row

from bot.models import Item, ParsedDocument
from services.amounts import prefer_text_amount

logger = logging.getLogger(__name__)
PLACEHOLDER_PATTERN = re.compile(r"\{\{[^}]+\}\}")


def _fmt(value: Any, text: str | None = None) -> str:
    """Format values for docx output using comma decimal separator."""

    return prefer_text_amount(text, value)


def _fmt_with_currency(value: Any, text: str | None, currency: str | None) -> str:
    amount = prefer_text_amount(text, value)
    if not amount:
        return ""
    formatted = str(amount).replace(".", ",")
    return f"{formatted} {currency}" if currency else formatted


def _sanitize_replacements(replacements: dict[str, Any]) -> dict[str, str]:
    sanitized: dict[str, str] = {}
    for key, value in replacements.items():
        if value is None:
            sanitized[key] = "—"
            continue
        formatted = _fmt(value)
        sanitized[key] = formatted if formatted else "—"
        if not sanitized[key].strip():
            sanitized[key] = "—"
    return sanitized


def render_docx(
    parsed: ParsedDocument,
    template_path: Path,
    output_path: Path,
    *,
    layout_copy: bool | None = None,
    executor_name: str | None = None,
    buyer_name: str | None = None,
    doc_number: str | None = None,
    doc_date: Any | None = None,
    totals_without_vat: Any | None = None,
    totals_vat: Any | None = None,
    totals_with_vat: Any | None = None,
    currency: str | None = None,
) -> None:
    enforce_layout = layout_copy
    if enforce_layout is None:
        enforce_layout = parsed.doc_type in {"акт", "товарная_накладная"}

    doc = Document(template_path)
    replacements = _build_replacements(
        parsed,
        executor_name=executor_name,
        buyer_name=buyer_name,
        doc_number=doc_number,
        doc_date=doc_date,
        totals_without_vat=totals_without_vat,
        totals_vat=totals_vat,
        totals_with_vat=totals_with_vat,
        currency=currency,
    )
    replacements = _sanitize_replacements(replacements)
    _replace_in_document(doc, replacements)
    _fill_items_table(doc, parsed, layout_copy=enforce_layout)
    doc.save(output_path)
    logger.info(
        "Rendered docx template=%s layout_copy=%s items=%s executor=%s customer=%s totals_with_vat=%s",
        template_path,
        enforce_layout,
        len(parsed.items) if parsed.items else (1 if parsed.main_item else 0),
        replacements.get("{{EXECUTOR_NAME}}"),
        replacements.get("{{CUSTOMER}}") or replacements.get("{{customer_name}}"),
        replacements.get("{{TOTAL_WITH_VAT}}"),
    )


def _build_replacements(
    parsed: ParsedDocument,
    *,
    executor_name: str | None = None,
    buyer_name: str | None = None,
    doc_number: str | None = None,
    doc_date: Any | None = None,
    totals_without_vat: Any | None = None,
    totals_vat: Any | None = None,
    totals_with_vat: Any | None = None,
    currency: str | None = None,
) -> dict[str, str]:
    supplier = parsed.supplier or {}
    buyer = parsed.buyer or {}
    supplier_name = (
        executor_name
        or parsed.supplier_company_name
        or parsed.supplier_raw
        or getattr(supplier, "name", "")
        or ""
    )
    customer_name = (
        buyer_name
        or parsed.customer_company_name
        or parsed.customer_raw
        or getattr(buyer, "name", "")
        or ""
    )
    totals = parsed.totals or {}
    main_item = parsed.main_item or (parsed.items[0] if parsed.items else None)
    item_names = "; ".join(filter(None, [item.name or item.description for item in (parsed.items or [])]))
    if not item_names and main_item:
        item_names = main_item.name or main_item.description or ""
    currency_code = (currency or parsed.currency or parsed.currency_code)
    if currency_code == "UNKNOWN":
        currency_code = ""
    currency_words = parsed.currency_words_ru or (
        "белорусских рублей" if currency_code == "BYN" else "рублей"
    )

    doc_date = doc_date or parsed.date or parsed.doc_date
    doc_number = (doc_number or parsed.number or parsed.doc_number or "").strip()

    replacements = {
        "{{DOC_TYPE}}": parsed.title or parsed.doc_type,
        "{{DOC_NUMBER}}": doc_number,
        "{{ DOC_NUMBER }}": doc_number,
        "{{DOC_DATE}}": doc_date.strftime("%d.%m.%Y") if doc_date else "",
        "{{ DOC_DATE }}": doc_date.strftime("%d.%m.%Y") if doc_date else "",
        "{{SUPPLIER_NAME}}": supplier_name,
        "{{ SUPPLIER }}": supplier_name,
        "{{ CONTRACTOR }}": parsed.main_counterparty or supplier_name,
        "{{SUPPLIER_ADDRESS}}": getattr(supplier, "address", "") or "",
        "{{SUPPLIER_UNP}}": getattr(supplier, "unp", "") or "",
        "{{BUYER_NAME}}": customer_name,
        "{{ BUYER }}": customer_name,
        "{{BUYER_ADDRESS}}": getattr(buyer, "address", "") or "",
        "{{BUYER_UNP}}": getattr(buyer, "unp", "") or "",
        "{{MAIN_COUNTERPARTY}}": parsed.main_counterparty or customer_name,
        "{{CONTRACTOR_NAME}}": parsed.main_counterparty or parsed.contractor or customer_name,
        "{{ CONTRACTOR_NAME }}": parsed.main_counterparty or parsed.contractor or customer_name,
        "{{EXECUTOR_NAME}}": parsed.executor or supplier_name,
        "{{ EXECUTOR_NAME }}": parsed.executor or supplier_name,
        "{{SUBJECT}}": parsed.subject or (main_item.name if main_item else item_names),
        "{{ SUBJECT }}": parsed.subject or (main_item.name if main_item else item_names),
        "{{ ITEM_NAME }}": item_names,
        "{{TOTAL_WITH_VAT}}": _fmt_with_currency(
            totals_with_vat if totals_with_vat is not None else getattr(totals, "amount_with_vat", None) or getattr(totals, "total_with_vat", None),
            getattr(totals, "total_with_vat_text", None),
            currency or parsed.currency or currency_code,
        ),
        "{{ TOTAL_WITH_VAT }}": _fmt_with_currency(
            totals_with_vat if totals_with_vat is not None else getattr(totals, "amount_with_vat", None) or getattr(totals, "total_with_vat", None),
            getattr(totals, "total_with_vat_text", None),
            currency or parsed.currency or currency_code,
        ),
        "{{TOTAL_VAT}}": _fmt_with_currency(
            totals_vat if totals_vat is not None else getattr(totals, "vat_amount", None) or getattr(totals, "total_vat", None),
            getattr(totals, "vat_amount_text", None),
            currency or parsed.currency or currency_code,
        ),
        "{{ TOTAL_VAT }}": _fmt_with_currency(
            totals_vat if totals_vat is not None else getattr(totals, "vat_amount", None) or getattr(totals, "total_vat", None),
            getattr(totals, "vat_amount_text", None),
            currency or parsed.currency or currency_code,
        ),
        "{{TOTAL_WITHOUT_VAT}}": _fmt_with_currency(
            totals_without_vat if totals_without_vat is not None else getattr(totals, "amount_without_vat", None) or getattr(totals, "total_without_vat", None),
            getattr(totals, "total_without_vat_text", None),
            currency or parsed.currency or currency_code,
        ),
        "{{ TOTAL_WITHOUT_VAT }}": _fmt_with_currency(
            totals_without_vat if totals_without_vat is not None else getattr(totals, "amount_without_vat", None) or getattr(totals, "total_without_vat", None),
            getattr(totals, "total_without_vat_text", None),
            currency or parsed.currency or currency_code,
        ),
        "{{CURRENCY}}": currency or parsed.currency or currency_code,
        "{{ CURRENCY }}": currency or parsed.currency or currency_code,
        "{{CURRENCY_CODE}}": currency_code,
        "{{CURRENCY_WORDS}}": currency_words,
        "{{RAW_TEXT}}": (parsed.raw_text or "")[:4000],
        "{{ doc_number }}": doc_number,
        "{{ doc_date }}": doc_date.strftime("%d.%m.%Y") if doc_date else "",
        "{{ executor_name }}": supplier_name,
        "{{ customer_name }}": customer_name,
        "{{ amount_total }}": _fmt(
            totals_with_vat if totals_with_vat is not None else getattr(totals, "amount_with_vat", None),
            getattr(totals, "total_with_vat_text", None),
        ),
        "{{ amount_vat }}": _fmt(
            totals_vat if totals_vat is not None else getattr(totals, "vat_amount", None),
            getattr(totals, "vat_amount_text", None),
        ),
        "{{ currency }}": currency or parsed.currency or currency_code,
        "{{ position_name }}": parsed.subject or (main_item.name if main_item else item_names),
        "{{ EXECUTOR }}": supplier_name or parsed.executor or "",
        "{{ EXECUTOR}}": supplier_name or parsed.executor or "",
        "{{CUSTOMER}}": customer_name or parsed.main_counterparty or "",
        "{{ CUSTOMER }}": customer_name or parsed.main_counterparty or "",
        "{{ POSITION_NAME }}": parsed.subject or (main_item.name if main_item else item_names),
        "{{ POSITION_AMOUNT_WO_VAT }}": _fmt(
            getattr(main_item, "amount_without_vat", None) if main_item else None,
            getattr(main_item, "amount_without_vat_text", None) if main_item else None,
        ),
        "{{ POSITION_VAT }}": _fmt(
            getattr(main_item, "vat_amount", None) if main_item else None,
            getattr(main_item, "vat_amount_text", None) if main_item else None,
        ),
        "{{ POSITION_WITH_VAT }}": _fmt(
            getattr(main_item, "amount_with_vat", None) if main_item else None,
            getattr(main_item, "amount_with_vat_text", None) if main_item else None,
        ),
        "{{ POSITION_CURRENCY }}": parsed.currency or currency_code,
    }
    return replacements


def _replace_in_document(doc: Document, replacements: dict[str, str]) -> None:
    _replace_in_paragraphs(doc.paragraphs, replacements)
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                _replace_in_paragraphs(cell.paragraphs, replacements)


def _replace_in_paragraphs(paragraphs: Iterable, replacements: dict[str, str]) -> None:
    for paragraph in paragraphs:
        text = paragraph.text
        for placeholder, value in replacements.items():
            text = text.replace(placeholder, value)
        if PLACEHOLDER_PATTERN.search(text):
            logger.warning("Unreplaced placeholder in run text: %s", text)
            text = PLACEHOLDER_PATTERN.sub("", text).strip()
        if paragraph.text != text:
            paragraph.text = text


def _find_items_table(doc: Document):
    for candidate in doc.tables:
        header_cells = candidate.rows[0].cells if candidate.rows else []
        header_text = " ".join(cell.text.lower() for cell in header_cells)
        if "наименование" in header_text and "ндс" in header_text:
            return candidate
    return doc.tables[0] if doc.tables else None


def _clear_row(row: _Row) -> None:
    for cell in row.cells:
        for paragraph in cell.paragraphs:
            for run in paragraph.runs:
                run.text = ""
        cell.text = ""


def _clone_row(table, template_row: _Row) -> _Row:
    new_tr = copy.deepcopy(template_row._tr)
    table._tbl.append(new_tr)
    return _Row(new_tr, table)


def _fill_item_row(row: _Row, item: Item, parsed: ParsedDocument) -> None:
    cells = row.cells
    values = [
        (item.name or item.description or "").strip(),
        _fmt(item.amount_without_vat, item.amount_without_vat_text),
        _fmt(item.vat_amount, item.vat_amount_text),
        _fmt(item.amount_with_vat or item.total_with_vat, item.amount_with_vat_text),
        item.currency or parsed.currency or parsed.currency_code or "",
    ]
    for idx, cell in enumerate(cells):
        if idx < len(values):
            cell.text = values[idx]


def _fill_items_table(doc: Document, parsed: ParsedDocument, *, layout_copy: bool) -> None:
    table = _find_items_table(doc)
    if table is None:
        return

    template_row: _Row | None = table.rows[1] if len(table.rows) > 1 else None
    if template_row is None:
        template_row = table.add_row()

    while len(table.rows) > 2:
        table._tbl.remove(table.rows[-1]._tr)

    items: list[Item] = parsed.items or ([] if parsed.main_item is None else [parsed.main_item])
    if not items:
        items = [Item(name=parsed.subject or parsed.main_counterparty)]

    _clear_row(template_row)
    for idx, item in enumerate(items):
        target_row = template_row if idx == 0 else _clone_row(table, template_row)
        _fill_item_row(target_row, item, parsed)

    if not layout_copy and len(items) == 0:
        _fill_item_row(template_row, Item(name=parsed.subject or parsed.main_counterparty), parsed)


__all__ = ["render_docx"]
