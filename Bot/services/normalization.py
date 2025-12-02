from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable

from rapidfuzz import fuzz, process
from settings.companies import DEFAULT_COMPANIES, load_company_directory as load_company_directory_config

logger = logging.getLogger(__name__)

KNOWN_COMPANIES = [
    "ООО «Тигервуд»",
    'ООО "Тигервуд"',
    "УП «РСВ» Рева С.В.",
    'УП "РСВ" Рева С.В.',
    "Производственно-торговое частное унитарное предприятие «Эдон-92»",
    'Производственно-торговое частное унитарное предприятие "Эдон-92"',
    "Эдон-92",
    "СООО «ЛойкоБелРус»",
    'СООО "ЛойкоБелРус"',
    "ООО «Вест Лайн Коммерц»",
    'ООО "Вест Лайн Коммерц"',
]


@lru_cache(maxsize=1)
def load_company_directory(path: Path | None = None) -> dict:
    return load_company_directory_config(path)


def _build_alias_pool(entries: Iterable[dict]) -> dict[str, str]:
    pool: dict[str, str] = {}
    for entry in entries:
        canonical = entry.get("canonical") or ""
        aliases = entry.get("aliases") or []
        for alias in [canonical, *aliases]:
            cleaned = _clean_quotes(alias)
            if cleaned:
                pool[cleaned] = canonical
    return pool


def _alias_score(value: str, pool: dict[str, str], *, threshold: int = 90) -> tuple[str | None, float | None]:
    if not pool or not value:
        return None, None
    match = process.extractOne(value, list(pool.keys()), scorer=fuzz.WRatio)
    if match and match[1] >= threshold:
        return pool.get(match[0]), float(match[1])
    return None, float(match[1]) if match else None


@dataclass
class NormalizationResult:
    normalized: str | None
    corrected: bool
    score: float | None


def _clean_quotes(value: str) -> str:
    normalized = value.strip()
    normalized = normalized.replace("“", "\"").replace("”", "\"")
    normalized = normalized.replace("«", "\"").replace("»", "\"")
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized


def _strip_address_tail(value: str) -> str:
    address_markers = [
        "г.",
        "город",
        "ул",
        "улица",
        "пр-т",
        "просп",
        "пер.",
        "д.",
        "дом",
        "кв",
        "республика",
        "область",
        "район",
        "офис",
        "комната",
        "индекс",
    ]
    for marker in address_markers:
        idx = value.lower().find(marker)
        if idx != -1:
            value = value[:idx]
            break
    if "," in value:
        value = value.split(",", 1)[0]
    return value.strip()


def strip_address(name: str) -> str:
    if not name:
        return name
    parts = re.split(r"(?:,\s*(?:г\.|ул\.|просп\.|пер\.|д\.|дом|офис|кв\.|комната)\b)", name, maxsplit=1)
    base = parts[0]
    return base.strip(" ,;")


def normalize_company_name(raw_name: str, *, companies: Iterable[str] | None = None) -> NormalizationResult:
    directory = load_company_directory()
    if companies is None:
        entries: list[dict] = []
        entries.extend(directory.get("counterparties", []))
        entries.extend(directory.get("our_companies", []))
        alias_pool = _build_alias_pool(entries)
    else:
        alias_pool = {_clean_quotes(company): company for company in companies if company}

    cleaned = _clean_quotes(raw_name)
    cleaned = strip_address(_strip_address_tail(cleaned))

    if not alias_pool:
        return NormalizationResult(normalized=cleaned or None, corrected=False, score=None)

    match = process.extractOne(cleaned, list(alias_pool.keys()), scorer=fuzz.WRatio)
    if not match:
        return NormalizationResult(normalized=None, corrected=False, score=None)

    candidate_key, score, _ = match
    candidate = strip_address(alias_pool.get(candidate_key, candidate_key))
    if score is None or score < 80:
        return NormalizationResult(normalized=None, corrected=False, score=float(score) if score else None)

    corrected = cleaned != candidate and score >= 92
    if corrected:
        logger.info("[normalize] %s -> %s (score=%.1f)", cleaned, candidate, score)
    return NormalizationResult(normalized=candidate, corrected=corrected, score=float(score))


def normalize_in_text(value: str, text: str) -> bool:
    cleaned_value = _clean_quotes(value).lower()
    cleaned_text = _clean_quotes(text).lower()
    normalized_value = re.sub(r"\s+", " ", cleaned_value)
    return normalized_value in cleaned_text


def normalize_company_name_loose(raw_name: str, raw_text: str | None = None) -> str | None:
    """Return a canonical company name if it confidently matches known companies.

    Falls back to scanning raw_text for a known company mention; otherwise returns None
    to signal that the organization was not reliably recognized.
    """

    if not raw_name:
        return None

    base = strip_address(raw_name)
    match = process.extractOne(base, KNOWN_COMPANIES, scorer=fuzz.WRatio)
    if match and match[1] >= 80:
        return strip_address(match[0])

    if raw_text:
        cleaned_text = _clean_quotes(raw_text).lower()
        for company in KNOWN_COMPANIES:
            normalized_company = _clean_quotes(strip_address(company)).lower()
            if normalized_company and normalized_company in cleaned_text:
                return strip_address(company)

    return None


__all__ = [
    "normalize_company_name",
    "normalize_in_text",
    "NormalizationResult",
    "load_company_directory",
    "_build_alias_pool",
    "_alias_score",
    "strip_address",
    "normalize_company_name_loose",
]
