from datetime import date as datetime_date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class Party(BaseModel):
    name: str | None = None
    unp: str | None = None
    address: str | None = None
    account: str | None = None
    bank: str | None = None


class Item(BaseModel):
    line_number: int | None = None
    description: str | None = None
    name: str | None = None
    quantity: Decimal | None = None
    unit: str | None = None
    price: Decimal | None = None
    price_text: str | None = None
    amount_without_vat: Decimal | None = None
    amount_without_vat_text: str | None = None
    vat_rate: Decimal | None = None
    vat_rate_text: str | None = None
    vat_amount: Decimal | None = None
    vat_amount_text: str | None = None
    amount_with_vat: Decimal | None = None
    total_with_vat: Decimal | None = None
    amount_with_vat_text: str | None = None
    currency: Literal["BYN", "RUB"] | None = None

    @model_validator(mode="after")
    def _sync_fields(self) -> "Item":
        if not self.name and self.description:
            self.name = self.description
        if not self.description and self.name:
            self.description = self.name
        if self.total_with_vat is None and self.amount_with_vat is not None:
            self.total_with_vat = self.amount_with_vat
        if self.amount_with_vat is None and self.total_with_vat is not None:
            self.amount_with_vat = self.total_with_vat
        return self


class Totals(BaseModel):
    total_without_vat: Decimal | None = None
    total_without_vat_text: str | None = None
    vat_amount: Decimal | None = None
    vat_amount_text: str | None = None
    total_with_vat: Decimal | None = None
    total_with_vat_text: str | None = None
    currency: Literal["BYN", "RUB"] | None = None
    amount_without_vat: Decimal | None = None
    total_vat: Decimal | None = None
    amount_with_vat: Decimal | None = None
    vat_rate_percent: Decimal | None = None
    vat_rate_percent_text: str | None = None

    @model_validator(mode="after")
    def _sync_fields(self) -> "Totals":
        if self.total_without_vat is None and self.amount_without_vat is not None:
            self.total_without_vat = self.amount_without_vat
        if self.amount_without_vat is None and self.total_without_vat is not None:
            self.amount_without_vat = self.total_without_vat
        if self.total_with_vat is None and self.amount_with_vat is not None:
            self.total_with_vat = self.amount_with_vat
        if self.amount_with_vat is None and self.total_with_vat is not None:
            self.amount_with_vat = self.total_with_vat
        if self.total_vat is None and self.vat_amount is not None:
            self.total_vat = self.vat_amount
        if self.vat_amount is None and self.total_vat is not None:
            self.vat_amount = self.total_vat
        return self


class ParsedDocument(BaseModel):
    doc_type: str | None = None
    doc_number: str | None = None
    doc_date: datetime_date | None = None
    contractor: str | None = None
    executor: str | None = None
    counterparty: str | None = None
    supplier_raw: str | None = None
    customer_raw: str | None = None
    supplier_source: str | None = None
    customer_source: str | None = None
    supplier_company_code: str | None = None
    customer_company_code: str | None = None
    supplier_company_name: str | None = None
    customer_company_name: str | None = None
    party_header_text: str | None = None
    currency: Literal["BYN", "RUB"] | None = None
    contract_number: str | None = None
    service_month: str | None = None

    title: str | None = None
    number: str | None = None
    date: datetime_date | None = None
    supplier: Party | None = None
    buyer: Party | None = None
    main_counterparty: str | None = None
    subject: str | None = None
    items: list[Item] = Field(default_factory=list)
    main_item: Item | None = None
    totals: Totals | None = None
    llm_totals: Totals | None = None
    currency_code: Literal["BYN", "RUB", "UNKNOWN"] = "UNKNOWN"
    currency_words_ru: str | None = None
    raw_text: str | None = None
    raw_ocr_text: str | None = None
    notes: str | None = None
    warnings: list[str] = Field(default_factory=list)
    normalization_notes: list[str] = Field(default_factory=list)
    sums_suspect: bool = False

    @model_validator(mode="after")
    def _sync_fields(self) -> "ParsedDocument":
        if not self.number and self.doc_number:
            self.number = self.doc_number
        elif not self.doc_number and self.number:
            self.doc_number = self.number

        if not self.date and self.doc_date:
            self.date = self.doc_date
        elif not self.doc_date and self.date:
            self.doc_date = self.date

        if not self.main_counterparty and self.counterparty:
            self.main_counterparty = self.counterparty
        elif not self.counterparty and self.main_counterparty:
            self.counterparty = self.main_counterparty

        if not self.contractor and self.counterparty:
            self.contractor = self.counterparty
        if not self.counterparty and self.contractor:
            self.counterparty = self.contractor

        if self.currency is None and self.currency_code in ("BYN", "RUB"):
            self.currency = self.currency_code  # type: ignore[assignment]
        if self.currency and self.currency_code == "UNKNOWN":
            self.currency_code = self.currency  # type: ignore[assignment]

        return self

    class Config:
        extra = "ignore"
