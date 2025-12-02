from __future__ import annotations

import re
from typing import Iterable, Tuple


def detect_currency(raw_text: str | None) -> tuple[str, str]:
    """
    Определяет валюту документа по тексту.

    Возвращает (currency_code, currency_words_ru).
    currency_code: 'BYN', 'RUB' или 'UNKNOWN'
    currency_words_ru: 'белорусских рублей', 'рублей' или 'российских рублей'
    """

    if not raw_text:
        return "UNKNOWN", "рублей"

    text = raw_text.lower()

    if _contains_any(text, _BYN_PATTERNS):
        return "BYN", "белорусских рублей"
    if _contains_any(text, _RUB_PATTERNS):
        return "RUB", "российских рублей"

    if _contains_any(text, _BYN_REGION_MARKERS):
        return "BYN", "белорусских рублей"
    if _contains_any(text, _RUB_REGION_MARKERS):
        return "RUB", "рублей"

    if _contains_any(text, _BYN_AMOUNT_WORDS):
        return "BYN", "белорусских рублей"
    if _contains_any(text, _RUB_AMOUNT_WORDS):
        return "RUB", "рублей"

    return "UNKNOWN", "рублей"


def _contains_any(text: str, patterns: Iterable[str]) -> bool:
    return any(re.search(pattern, text) for pattern in patterns)


_BYN_PATTERNS = [
    r"\bby[nь]?\b",
    r"белорусск[ихий]\s+руб",
    r"бел\.\s*руб",
    r"бел\s*\.\s*руб",
    r"белорусский\s+рубль",
    r"белорусских\s+рублей",
    r"\bbr\b",
]

_RUB_PATTERNS = [
    r"\brub\b",
    r"российск[ихий]\s+руб",
    r"рос\.\s*руб",
    r"рос\s*\.\s*руб",
    r"\bроссийский\s+рубль\b",
    r"\bроссийских\s+рублей\b",
]

_BYN_REGION_MARKERS = [
    r"республика\s+беларусь",
    r"\bунп\b",
    r"беларусбанк",
    r"асб\s+беларусбанк",
    r"белинвестбанк",
    r"белагропромбанк",
]

_RUB_REGION_MARKERS = [
    r"российская\s+федерация",
    r"\bинн\b",
    r"\bкпп\b",
    r"сбербанк",
    r"втб",
    r"тинькофф",
    r"газпромбанк",
]

_BYN_AMOUNT_WORDS = [
    r"белорусских\s+рублей",
]

_RUB_AMOUNT_WORDS = [
    r"российских\s+рублей",
    r"рублей\s+\d{1,2}\s*коп",
]


__all__ = ["detect_currency"]
