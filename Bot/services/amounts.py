from __future__ import annotations

"""Helpers for safe numeric parsing/formatting without losing digits."""

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any


def parse_amount(raw: Any) -> Decimal | None:
    """Parse a numeric string into ``Decimal`` without truncating digits.

    Supports inputs with spaces as thousand separators and either comma or dot
    as the decimal separator. Returns ``None`` if the value cannot be parsed.
    """

    if raw is None:
        return None

    if isinstance(raw, (int, float, Decimal)):
        try:
            return Decimal(str(raw))
        except (InvalidOperation, ValueError):
            return None

    text = str(raw).strip()
    if not text:
        return None

    # Remove thousand separators and normalize decimal separator to dot.
    cleaned = text.replace("\xa0", " ")
    cleaned = cleaned.replace(" ", "")
    cleaned = cleaned.replace(",", ".")

    # Reject obviously invalid strings early.
    if cleaned.count(".") > 1 and "e" not in cleaned.lower():
        return None

    try:
        return Decimal(cleaned)
    except (InvalidOperation, ValueError):
        return None


def format_amount_ru(value: Decimal | float | int | None) -> str:
    """Format numeric values with a comma decimal separator.

    Always renders two decimal places when a numeric value is provided; returns
    an empty string for missing values.
    """

    if value is None:
        return ""

    try:
        dec = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError):
        return ""

    dec = dec.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return f"{dec:.2f}".replace(".", ",")


def prefer_text_amount(text: str | None, numeric: Decimal | float | int | None) -> str:
    """Return a display-ready money string preferring raw text if supplied.

    Text values from the model keep the exact digit structure (e.g. ``0,39479``)
    while the numeric fallback is formatted with ``format_amount_ru``.
    """

    if text:
        return str(text).replace(".", ",")
    return format_amount_ru(numeric)


__all__ = ["parse_amount", "format_amount_ru", "prefer_text_amount"]
