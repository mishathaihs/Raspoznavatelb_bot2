from __future__ import annotations

import re
from typing import Literal


CurrencyCode = Literal["BYN", "RUB"]


def detect_currency(raw_text: str, model_currency: str | None) -> str | None:
    """
    Возвращает 'BYN', 'RUB' или None, опираясь на ответ модели и текст raw_text.
    Приоритет: 1) валидное значение из model_currency; 2) детект по тексту; 3) None.
    """

    normalized = (raw_text or "").lower()
    if model_currency in {"BYN", "RUB"}:  # type: ignore[comparison-overlap]
        return model_currency

    if _contains_byn(normalized):
        return "BYN"
    if _contains_rub(normalized):
        return "RUB"

    return None


def _contains_byn(text: str) -> bool:
    patterns = [
        r"\bby[nm]\b",
        r"белорусск",
        r"бел\.\s*руб",
        r"бел\.руб",
        r"белорусских рублей",
        r"\bbr\b",
    ]
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)


def _contains_rub(text: str) -> bool:
    patterns = [
        r"\brub\b",
        r"российск",
        r"рос\.\s*руб",
        r"рос\.руб",
        r"рублей рф",
    ]
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)


__all__ = ["detect_currency"]
