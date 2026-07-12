from __future__ import annotations

import csv
import os
from typing import Any, Optional

from app.services.common_utils import json_loads, safe_float

_ACTIVITY_CACHE: Optional[list[dict[str, Any]]] = None


def activity_dataset_paths() -> list[str]:
    return [
        os.path.join("data", "activities.csv"),
        os.path.join("data", "Nutrinusa database - activities.csv"),
        "activities.csv",
        "Nutrinusa database - activities.csv",
        "/mnt/data/activities.csv",
        "/mnt/data/Nutrinusa database - activities.csv",
    ]


def find_activity_dataset_path() -> Optional[str]:
    for path in activity_dataset_paths():
        if os.path.exists(path):
            return path
    return None


def load_activities(force_reload: bool = False) -> list[dict[str, Any]]:
    global _ACTIVITY_CACHE
    if _ACTIVITY_CACHE is not None and not force_reload:
        return _ACTIVITY_CACHE

    path = find_activity_dataset_path()
    if not path:
        _ACTIVITY_CACHE = []
        return _ACTIVITY_CACHE

    activities: list[dict[str, Any]] = []
    with open(path, "r", encoding="utf-8-sig", newline="", errors="replace") as f:
        reader = csv.DictReader(f)
        for row in reader:
            nama = (row.get("nama") or "").strip()
            if not nama:
                continue

            met_details = json_loads(row.get("met_details"), [])
            if not isinstance(met_details, list):
                met_details = []

            clean_met: list[dict[str, Any]] = []
            for item in met_details:
                if not isinstance(item, dict):
                    continue
                tipe = str(item.get("tipe") or "").strip()
                met = safe_float(item.get("met"), 0.0) or 0.0
                if tipe and met > 0:
                    clean_met.append({"tipe": tipe, "met": met})

            activities.append({
                "nama": nama,
                "kategori": (row.get("kategori") or "").strip(),
                "deskripsi": (row.get("deskripsi") or "").strip(),
                "estimasi_kalori": json_loads(row.get("estimasi_kalori"), {}),
                "manfaat_utama": json_loads(row.get("manfaat utama"), []),
                "met_details": clean_met,
            })

    _ACTIVITY_CACHE = activities
    return activities


def find_activity_met(activity_name: str, activity_type: str) -> tuple[Optional[dict[str, Any]], float]:
    activity_name = (activity_name or "").strip().lower()
    activity_type = (activity_type or "").strip().lower()
    for act in load_activities():
        if act["nama"].strip().lower() != activity_name:
            continue
        for detail in act.get("met_details", []):
            if detail.get("tipe", "").strip().lower() == activity_type:
                return act, float(detail.get("met") or 0.0)
        if not activity_type and act.get("met_details"):
            return act, float(act["met_details"][0].get("met") or 0.0)
        return act, 0.0
    return None, 0.0


def calculate_activity_calories(met: float, weight_kg: float, duration_minutes: float) -> float:
    if met <= 1 or weight_kg <= 0 or duration_minutes <= 0:
        return 0.0
    return round((met - 1.0) * weight_kg * (duration_minutes / 60.0), 1)
