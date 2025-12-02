from __future__ import annotations

from decimal import Decimal
from typing import Any

from services.amounts import format_amount_ru, parse_amount


def format_money_ru(value: Any, *, empty: str = "") -> str:
    """Return a human-friendly money string with comma decimal separator."""

    if value is None:
        return empty
    parsed = parse_amount(value)
    if parsed is None:
        return empty if (isinstance(value, str) and not value.strip()) else str(value)
    return format_amount_ru(parsed)


__all__ = ["format_money_ru"]
