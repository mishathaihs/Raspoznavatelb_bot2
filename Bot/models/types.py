from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import List, Optional


@dataclass(slots=True)
class DocumentItem:
    name: str
    amount: Optional[float] = None
    vat_amount: Optional[float] = None


@dataclass(slots=True)
class DocumentData:
    document_type: str
    document_number: Optional[str]
    document_date: Optional[date]
    counterparty: Optional[str]
    document_total: Optional[float]
    vat_total: Optional[float]
    items: List[DocumentItem] = field(default_factory=list)

    def normalized_type(self) -> str:
        value = (self.document_type or "").strip().lower()
        if "наклад" in value:
            return "Накладная"
        if "акт" in value:
            return "Акт"
        return "Акт"
