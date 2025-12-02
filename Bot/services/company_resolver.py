from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

try:
    from rapidfuzz import fuzz
except ImportError:  # pragma: no cover - optional dependency
    from difflib import SequenceMatcher

    class _FuzzProxy:
        @staticmethod
        def token_sort_ratio(a: str, b: str) -> float:
            return SequenceMatcher(None, " ".join(sorted(a.split())), " ".join(sorted(b.split()))).ratio() * 100

        @staticmethod
        def partial_ratio(a: str, b: str) -> float:
            return SequenceMatcher(None, a, b).ratio() * 100

    fuzz = _FuzzProxy()

from settings.companies import load_company_directory

DEFAULT_CUSTOMER_CODE = "TIGERWOOD"

logger = logging.getLogger(__name__)


LAT_TO_CYR = {
    "A": "А",
    "B": "В",
    "C": "С",
    "E": "Е",
    "H": "Н",
    "K": "К",
    "M": "М",
    "O": "О",
    "P": "Р",
    "T": "Т",
    "X": "Х",
    "Y": "У",
    "0": "О",
    "3": "З",
}


@dataclass
class ResolvedParties:
    executor_name: str | None
    buyer_name: str | None
    executor_id: str | None
    buyer_id: str | None
    executor_in_dict: bool
    buyer_in_dict: bool
    executor_raw: str | None
    buyer_raw: str | None


def normalize_company_text_for_match(value: str | None) -> str:
    """Normalize noisy OCR text for fuzzy matching against the company directory."""

    if not value:
        return ""

    cleaned = value.upper()
    cleaned = "".join(LAT_TO_CYR.get(ch, ch) for ch in cleaned)
    cleaned = re.sub(r"[^А-ЯA-Z0-9\s\"']", " ", cleaned)
    cleaned = re.sub(r"[\"']", " ", cleaned)

    forms = [
        "ООО",
        "ОАО",
        "ЧУП",
        "УП",
        "ЗАО",
        "ОДО",
        "ПТЧУП",
    ]
    for form in forms:
        cleaned = re.sub(rf"\b{form}\b", "", cleaned)

    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _best_match(value: str, directory: dict[str, list[dict[str, Any]]]) -> tuple[str | None, str | None, bool]:
    if not value:
        return None, None, False

    candidates: list[tuple[str, str, str | None]] = []
    for bucket in ("our_companies", "counterparties"):
        for entry in directory.get(bucket, []):
            canonical = entry.get("canonical") or ""
            code = entry.get("code")
            candidates.append((canonical, canonical, code))
            for alias in entry.get("aliases") or []:
                candidates.append((alias, canonical, code))

    best_score = 0
    best_value: str | None = None
    best_code: str | None = None
    for alias, canonical, code in candidates:
        score = max(
            fuzz.token_sort_ratio(value, normalize_company_text_for_match(alias)),
            fuzz.partial_ratio(value, normalize_company_text_for_match(alias)),
        )
        if score > best_score:
            best_score = score
            best_value = canonical
            best_code = code

    if best_score >= 90:
        return best_value, best_code, True
    return None, None, False


def resolve_parties(raw_supplier: str | None, raw_customer: str | None) -> ResolvedParties:
    directory = load_company_directory()
    supplier_norm = normalize_company_text_for_match(raw_supplier)
    customer_norm = normalize_company_text_for_match(raw_customer)

    supplier_match, supplier_code, supplier_found = _best_match(supplier_norm, directory)
    customer_match, customer_code, customer_found = _best_match(customer_norm, directory)

    fallback_customer = False
    if not customer_found:
        for bucket in ("our_companies", "counterparties"):
            for entry in directory.get(bucket, []):
                if entry.get("code") == DEFAULT_CUSTOMER_CODE:
                    customer_match = entry.get("canonical") or customer_match
                    customer_code = entry.get("code") or customer_code
                    customer_found = True
                    fallback_customer = True
                    break
            if customer_found:
                break
        if not customer_found:
            logger.warning(
                "[company_resolver] default customer with code %s not found in directory", DEFAULT_CUSTOMER_CODE
            )

    resolved = ResolvedParties(
        executor_name=supplier_match if supplier_found else None,
        buyer_name=customer_match if customer_found else None,
        executor_id=supplier_code if supplier_found else None,
        buyer_id=customer_code if customer_found else None,
        executor_in_dict=supplier_found,
        buyer_in_dict=customer_found,
        executor_raw=(raw_supplier or "").strip() or None,
        buyer_raw=(raw_customer or "").strip() or None,
    )
    logger.info(
        "[company_resolver] supplier_raw=%s -> %s (code=%s, in_dict=%s) | customer_raw=%s -> %s (code=%s, in_dict=%s)",
        raw_supplier,
        resolved.executor_name,
        resolved.executor_id,
        resolved.executor_in_dict,
        raw_customer,
        resolved.buyer_name,
        resolved.buyer_id,
        resolved.buyer_in_dict,
    )
    if fallback_customer and resolved.buyer_name:
        logger.info(
            "[company_resolver] customer resolved via default code %s -> %s", DEFAULT_CUSTOMER_CODE, resolved.buyer_name
        )
    return resolved


__all__ = ["resolve_parties", "ResolvedParties", "normalize_company_text_for_match", "LAT_TO_CYR"]
