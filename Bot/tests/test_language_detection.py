from __future__ import annotations

from services.lang_utils import is_russian_document


def test_non_russian_language_detected():
    text = "Invoice number 12345 issued in London"
    is_ru, share, _ = is_russian_document(text)
    assert is_ru is False
    assert share <= 0.15

