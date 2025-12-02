from __future__ import annotations

import re
from typing import Tuple

RUS_CHARS = set(
    "абвгдеёжзийклмнопрстуфхцчшщъыьэюя" "ґєіїў" "АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ"
)

_WORD_RE = re.compile(r"\b[А-ЯЁа-яё]{3,}\b")
_CLEANUP_RE = re.compile(r"(https?://\S+|\b[a-zA-Z]{2,}\d{2,}|BY\d+|\+?\d[\d\s()-]{6,})")


def russian_share(text: str) -> float:
    letters = [ch.lower() for ch in text if ch.isalpha()]
    if not letters:
        return 0.0
    ru = sum(ch in RUS_CHARS for ch in letters)
    return ru / len(letters)


def _has_cyrillic_word(text: str) -> bool:
    return bool(_WORD_RE.search(text))


def _cleanup_text(text: str) -> str:
    return _CLEANUP_RE.sub(" ", text)


def is_russian_document(text: str) -> Tuple[bool, float, str]:
    share = russian_share(text)
    if share >= 0.6:
        return True, share, "Доля кириллицы >= 0.6"
    if share <= 0.15:
        return False, share, "Доля кириллицы <= 0.15"

    if len(text) < 200:
        return (_has_cyrillic_word(text), share, "Короткий текст, ищем слова")

    cleaned = _cleanup_text(text)
    cleaned_share = russian_share(cleaned)
    if cleaned_share >= 0.5:
        return True, cleaned_share, "После очистки доля кириллицы >= 0.5"

    return _has_cyrillic_word(text), cleaned_share, "Порог не достигнут, fallback по словам"


__all__ = ["is_russian_document", "russian_share"]
