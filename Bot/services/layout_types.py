from __future__ import annotations

from dataclasses import dataclass
from typing import List


@dataclass
class WordBox:
    text: str
    x: int
    y: int
    w: int
    h: int
    line_num: int | None = None


@dataclass
class LayoutExtraction:
    words: list[WordBox]
    raw_text: str
    cleaned_text: str | None = None

    @property
    def effective_text(self) -> str:
        return self.cleaned_text or self.raw_text


__all__ = ["WordBox", "LayoutExtraction"]
