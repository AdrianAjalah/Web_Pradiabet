from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from typing import Any, Optional


def safe_float(value: Any, default: Optional[float] = 0.0) -> Optional[float]:
    """Parse angka dari input form/CSV dengan aman.

    Mengikuti logic lama: nilai kosong, nan, none, null, '-' dikembalikan ke default.
    Format Indonesia seperti 1.234,5 juga ditangani sederhana.
    """
    if value is None:
        return default
    text = str(value).strip()
    if text == "" or text.lower() in {"nan", "none", "null", "-"}:
        return default
    try:
        if "," in text:
            text = text.replace(".", "").replace(",", ".")
        return float(text)
    except Exception:
        return default


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(str(value).strip()))
    except Exception:
        return default


def json_loads(value: Any, fallback: Any) -> Any:
    try:
        if not value:
            return fallback
        return json.loads(value)
    except Exception:
        return fallback


def today() -> date:
    return date.today()


def monday_of_week(d: date) -> date:
    return d - timedelta(days=d.weekday())


def parse_date(value: Optional[str], default: Optional[date] = None) -> date:
    if not value:
        return default or today()
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except Exception:
        return default or today()


def to_bool(value: Any) -> bool:
    text = str(value or "").strip().lower()
    return text in {"true", "1", "ya", "yes", "y", "benar"}


def row_value(row: dict[str, Any], *names: str, default: str = "") -> Any:
    for name in names:
        value = row.get(name)
        if value is not None and str(value).strip() != "":
            return value
    return default


def unique_keep_order(items: list[str]) -> list[str]:
    clean: list[str] = []
    for item in items:
        if item and item not in clean:
            clean.append(item)
    return clean
