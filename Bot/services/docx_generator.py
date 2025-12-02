from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable

from docx import Document
from docx.enum.text import WD_PARAGRAPH_ALIGNMENT
from docx.shared import Pt

from models.parser_types import ParsedDocument, ParsedItem

logger = logging.getLogger(__name__)


def generate_docx(parsed: ParsedDocument, ocr_raw_text: str, save_path: Path) -> None:
    doc = Document()

    title = doc.add_heading(level=0)
    title.alignment = WD_PARAGRAPH_ALIGNMENT.CENTER
    title_run = title.add_run(_build_title(parsed))
    title_run.bold = True
    title_run.font.size = Pt(16)

    doc.add_paragraph(f"Контрагент: {parsed.counterparty or 'не распознан'}")
    basis = parsed.debug_info.get("dates", {}).get("contract")
    if basis:
        contract_raw = basis.get("raw") if isinstance(basis, dict) else basis
        doc.add_paragraph(f"Основание: договор от {contract_raw}")

    if parsed.items:
        table = doc.add_table(rows=1, cols=7)
        headers = ["№", "Наименование", "Кол-во", "Цена", "Сумма", "Сумма НДС", "Всего с НДС"]
        hdr_cells = table.rows[0].cells
        for idx, text in enumerate(headers):
            hdr_cells[idx].text = text

        for idx, item in enumerate(parsed.items or [], start=1):
            _add_item_row(table, idx, item)

    doc.add_paragraph()
    doc.add_paragraph(f"Итого с НДС: {parsed.total_sum or '—'}")
    doc.add_paragraph(f"В том числе НДС: {parsed.vat_sum or '—'}")

    doc.add_paragraph("Сырый текст OCR (для проверки):")
    snippet = ocr_raw_text[:1200]
    raw = doc.add_paragraph(snippet)
    raw.style.font.size = Pt(8)

    doc.save(save_path)


def _add_item_row(table, idx: int, item: ParsedItem) -> None:
    row_cells = table.add_row().cells
    row_cells[0].text = str(idx)
    row_cells[1].text = item.name
    row_cells[2].text = _format_number(item.quantity)
    row_cells[3].text = _format_number(item.price)
    row_cells[4].text = _format_number(item.total_with_vat)
    row_cells[5].text = _format_number(item.vat_amount)
    row_cells[6].text = _format_number(item.total_with_vat)


def _format_number(value) -> str:
    if value is None:
        return ""
    try:
        return f"{value}" if isinstance(value, (int, float)) else f"{value:.2f}"  # type: ignore[call-arg]
    except Exception:
        return str(value)


def _build_title(parsed: ParsedDocument) -> str:
    date_part = parsed.doc_date.strftime("%d.%m.%Y") if parsed.doc_date else "—"
    number_part = parsed.doc_number or "—"
    type_part = "АКТ оказанных услуг" if parsed.doc_type == "акт" else "Товарная накладная"
    return f"{type_part} № {number_part} от {date_part}"


__all__ = ["generate_docx"]
