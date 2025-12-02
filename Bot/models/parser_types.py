from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Literal


@dataclass(slots=True)
class OCRDateCandidate:
    raw_value: str
    context: str
    start_index: int
    kind: Literal["unknown", "template", "contract", "document"] = "unknown"


@dataclass(slots=True)
class OCRResult:
    text: str
    language: Literal["ru", "mixed", "other"]
    meta: dict = field(default_factory=dict)
    file_type: Literal["pdf", "image"] | None = None

    def lower_text(self) -> str:
        return self.text.lower()


@dataclass(slots=True)
class ParsedItem:
    name: str
    quantity: float | None = None
    price: Decimal | None = None
    total_with_vat: Decimal | None = None
    vat_amount: Decimal | None = None
    vat_rate: int | None = None


@dataclass(slots=True)
class ParsedDocument:
    doc_type: Literal["акт", "накладная"]
    doc_number: str | None
    doc_date: date | None
    supplier: str | None
    customer: str | None
    counterparty: str | None
    items: list[ParsedItem] = field(default_factory=list)
    currency: str | None = None
    confidence: float = 0.0
    warnings: list[str] = field(default_factory=list)
    raw_text: str = ""
    total_sum: Decimal | None = None
    vat_sum: Decimal | None = None
    debug_info: dict = field(default_factory=dict)

    @property
    def number(self) -> str | None:
        return self.doc_number

    @property
    def date(self) -> date | None:
        return self.doc_date

