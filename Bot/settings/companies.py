from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from services.agents_directory import get_agents_directory

logger = logging.getLogger(__name__)


@dataclass
class Company:
    canonical: str
    aliases: list[str]
    code: str | None
    tax_id: str | None = None


DEFAULT_COMPANIES: dict[str, list[dict[str, Any]]] = {
    "our_companies": [
        {
            "canonical": "Общество с ограниченной ответственностью \"Тигервуд\"",
            "aliases": [
                "ООО \"Тигервуд\"",
                "ООО Тигервуд",
                "ООО Тигеруд",
                "Тигервуд",
                "Тигеруд",
                "OOO Tigerwood",
            ],
            "code": "TIGERWOOD",
            "tax_id": "693392507",
        }
    ],
    "counterparties": [
        {
            "canonical": "Общество с ограниченной ответственностью \"Тигервуд\"",
            "aliases": [
                "ООО \"Тигервуд\"",
                "ООО Тигервуд",
                "ООО Тигеруд",
                "Тигервуд",
                "Тигеруд",
                "OOO Tigerwood",
            ],
            "code": "TIGERWOOD",
            "tax_id": "693392507",
        },
        {
            "canonical": "УП «РСВ» Рева С.В.",
            "aliases": ["УП \"РСВ\" Рева С.В.", "УП РСВ", "РСВ Рева С.В.", "УП «РСВ»"],
            "code": "RSV_REVA",
            "tax_id": None,
        },
        {
            "canonical": "Производственно-торговое частное унитарное предприятие \"Эдон-92\"",
            "aliases": [
                "Эдон-92",
                "ООО \"Эдон-92\"",
                "ООО «Эдон-92»",
                "Эдон 92",
                "ПТЧУП \"Эдон-92\"",
                "ПТЧУП Эдон-92",
                "Производственно-торговое частное унитарное предприятие «Эдон-92»",
            ],
            "code": "EDON_92",
            "tax_id": "100152272",
        },
        {
            "canonical": "СООО «ЛойкоБелРус»",
            "aliases": ["СООО \"ЛойкоБелРус\"", "ЛойкоБелРус", "Лойко Бел Рус"],
            "code": "LOYKOBELRUS",
            "tax_id": None,
        },
        {
            "canonical": "ООО «Вест Лайн Коммерц»",
            "aliases": ["ООО \"Вест Лайн Коммерц\"", "Вест Лайн Коммерц"],
            "code": "WEST_LINE_KOMMERC",
            "tax_id": None,
        },
    ],
}


def load_company_directory(path: Path | None = None) -> dict[str, list[dict[str, Any]]]:
    target_path = path or Path(__file__).resolve().parent.parent / "data" / "companies.json"
    if not target_path.exists():
        logger.warning("Company directory %s is missing; using defaults", target_path)
        base_directory = DEFAULT_COMPANIES.copy()
    else:
        try:
            raw = json.loads(target_path.read_text(encoding="utf-8"))
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Failed to read company directory %s: %s", target_path, exc)
            base_directory = DEFAULT_COMPANIES.copy()
        else:
            def _normalize_entry(entry: dict[str, Any]) -> dict[str, Any]:
                canonical = entry.get("canonical") or ""
                aliases = entry.get("aliases") or []
                code = entry.get("code")
                tax_id = entry.get("tax_id")
                return {"canonical": canonical, "aliases": aliases, "code": code, "tax_id": tax_id}

            base_directory = {
                "our_companies": [_normalize_entry(entry) for entry in raw.get("our_companies", [])],
                "counterparties": [_normalize_entry(entry) for entry in raw.get("counterparties", [])],
            }

    try:
        agents_entries = get_agents_directory().get_agents()
    except Exception as exc:  # pragma: no cover - remote API
        logger.warning("Failed to sync agents directory: %s", exc)
        agents_entries = []

    if agents_entries:
        base_directory = base_directory or {"our_companies": [], "counterparties": []}
        merged_counterparties = agents_entries + base_directory.get("counterparties", [])
        base_directory["counterparties"] = merged_counterparties

    return base_directory


__all__ = ["load_company_directory", "DEFAULT_COMPANIES", "find_company_by_tax_id", "Company"]


def find_company_by_tax_id(tax_id: str, directory: dict[str, list[dict[str, Any]]] | None = None) -> Company | None:
    if not tax_id:
        return None
    directory = directory or load_company_directory()
    for bucket in ("our_companies", "counterparties"):
        for entry in directory.get(bucket, []):
            if entry.get("tax_id") and entry["tax_id"] == tax_id:
                return Company(
                    canonical=entry.get("canonical") or "",
                    aliases=entry.get("aliases") or [],
                    code=entry.get("code"),
                    tax_id=entry.get("tax_id"),
                )
    return None
