from __future__ import annotations

import csv
import os
from typing import Any, Optional

from app.services.common_utils import row_value, safe_float, to_bool, unique_keep_order

# Threshold lama dipertahankan. Perubahan sesuai arahan user:
# - gunakan gula_g, bukan gula_tambahan_g
# - kalau kelompok_makanan == Buah, gula dianggap gula alami dan tidak dipenalti sebagai gula tinggi
HIGH_SUGAR_G = 10.0
HIGH_SODIUM_MG = 400.0
HIGH_GI = 70.0

_FOOD_CACHE: Optional[list[dict[str, Any]]] = None


def food_dataset_paths() -> list[str]:
    return [
        os.path.join("data", "master_makanan_kategori_flag_mealplan.csv"),
        "master_makanan_kategori_flag_mealplan.csv",
        "/mnt/data/master_makanan_kategori_flag_mealplan.csv",
    ]


def find_food_dataset_path() -> Optional[str]:
    for path in food_dataset_paths():
        if os.path.exists(path):
            return path
    return None


def is_fruit(food: dict[str, Any]) -> bool:
    kelompok = str(food.get("kelompok_makanan") or "").strip().lower()
    return kelompok == "buah"


def recommendation_reasons(food: dict[str, Any]) -> list[str]:
    """Alasan makanan kurang direkomendasikan.

    Ini turunan logic lama dari progress_tracker.py, dengan aturan gula yang sudah dikunci:
    gula_g buah tidak dipenalti karena dianggap gula alami dari buah.
    """
    reasons: list[str] = []
    kelompok = str(food.get("kelompok_makanan") or "").strip().lower()
    tingkat = str(food.get("tingkat_proses") or "").strip().lower()
    slot = str(food.get("slot_meal_plan") or "").strip().lower()
    gula = float(food.get("gula_g") or 0)
    natrium = float(food.get("natrium_mg") or 0)
    gi = float(food.get("indeks_glikemik_estimasi") or food.get("indeks_glikemik") or 0)

    if tingkat == "ultraproses":
        reasons.append("makanan ultra proses")
    if tingkat == "mentah":
        reasons.append("bahan mentah")
    if food.get("adalah_gorengan"):
        reasons.append("gorengan")

    # Arahan user: pakai gula_g. Khusus Buah, anggap gula alami/gula sehat.
    if not is_fruit(food) and gula > HIGH_SUGAR_G:
        reasons.append("gula tinggi")

    if natrium > HIGH_SODIUM_MG:
        reasons.append("natrium/sodium tinggi")
    if gi >= HIGH_GI:
        reasons.append("GI tinggi")
    if food.get("mengandung_alkohol"):
        reasons.append("mengandung alkohol")
    if food.get("mengandung_babi"):
        reasons.append("mengandung babi")
    if kelompok in {"snack", "dessert", "minuman", "bumbu & pelengkap", "suplemen nutrisi"}:
        reasons.append(f"kategori {food.get('kelompok_makanan')} bukan menu utama")
    if slot == "no meal" or not slot:
        reasons.append("tidak masuk slot meal plan")

    return unique_keep_order(reasons)


def is_food_recommended(food: dict[str, Any]) -> bool:
    return len(recommendation_reasons(food)) == 0


def load_tracker_foods(force_reload: bool = False) -> list[dict[str, Any]]:
    global _FOOD_CACHE
    if _FOOD_CACHE is not None and not force_reload:
        return _FOOD_CACHE

    path = find_food_dataset_path()
    if not path:
        _FOOD_CACHE = []
        return _FOOD_CACHE

    foods: list[dict[str, Any]] = []
    with open(path, "r", encoding="utf-8-sig", newline="", errors="replace") as f:
        sample = f.read(2048)
        f.seek(0)
        delimiter = ";" if sample.count(";") >= sample.count(",") else ","
        reader = csv.DictReader(f, delimiter=delimiter)
        for idx, row in enumerate(reader):
            nama = str(row_value(row, "nama_makanan", "nama") or "").strip()
            if not nama:
                continue
            tingkat = str(row.get("tingkat_proses") or "").strip()

            # Logic lama: sembunyikan bahan mentah yang memang tidak siap dimakan.
            if tingkat.lower() == "mentah":
                continue

            kalori = safe_float(row_value(row, "kalori_kkal", "kalori"), 0.0) or 0.0
            if kalori <= 0:
                continue

            gi = safe_float(row_value(row, "indeks_glikemik", "glikemik_indeks"), 0.0) or 0.0
            est_gi = safe_float(
                row_value(row, "indeks_glikemik_estimasi", "estimated_glycemic_index", "estimated_glikemik_indeks"),
                0.0,
            ) or 0.0

            food: dict[str, Any] = {
                "id": str(idx),
                "kode": str(row.get("kode") or idx).strip(),
                "nama": nama,
                "kelompok_makanan": str(row.get("kelompok_makanan") or row.get("kategori") or "").strip(),
                "jenis_bahan_utama": str(row.get("jenis_bahan_utama") or "").strip(),
                "tingkat_proses": tingkat,
                "slot_meal_plan": str(row.get("slot_meal_plan") or "No Meal").strip(),
                "gambar": str(row.get("gambar") or "").strip(),
                "gram_porsi": safe_float(row.get("gram_porsi"), 100.0) or 100.0,
                "kalori_kkal": kalori,
                "karbohidrat_g": safe_float(row.get("karbohidrat_g"), 0.0) or 0.0,
                "protein_g": safe_float(row.get("protein_g"), 0.0) or 0.0,
                "lemak_g": safe_float(row.get("lemak_g"), 0.0) or 0.0,
                "serat_g": safe_float(row.get("serat_g"), 0.0) or 0.0,
                "gula_g": safe_float(row.get("gula_g"), 0.0) or 0.0,
                # Tetap dibaca untuk kompatibilitas payload lama, tapi tidak dipakai untuk penalti gula.
                "gula_tambahan_g": safe_float(row.get("gula_tambahan_g"), 0.0) or 0.0,
                "natrium_mg": safe_float(row_value(row, "natrium_mg", "sodium"), 0.0) or 0.0,
                "indeks_glikemik": gi,
                "indeks_glikemik_estimasi": est_gi,
                "mengandung_susu": to_bool(row.get("mengandung_susu")),
                "mengandung_telur": to_bool(row.get("mengandung_telur")),
                "mengandung_seafood": to_bool(row.get("mengandung_seafood")),
                "mengandung_kacang": to_bool(row.get("mengandung_kacang")),
                "mengandung_babi": to_bool(row.get("mengandung_babi")),
                "mengandung_alkohol": to_bool(row.get("mengandung_alkohol")),
                "adalah_gorengan": to_bool(row.get("adalah_gorengan")),
                "search_text": " ".join([
                    nama,
                    str(row.get("kode") or ""),
                    str(row.get("kelompok_makanan") or row.get("kategori") or ""),
                    str(row.get("jenis_bahan_utama") or ""),
                    tingkat,
                    str(row.get("slot_meal_plan") or ""),
                ]).lower(),
            }
            reasons = recommendation_reasons(food)
            food["is_recommended"] = len(reasons) == 0
            food["recommendation_label"] = "Rekomendasi meal plan" if food["is_recommended"] else "Tidak direkomendasi"
            food["not_recommended_reasons"] = reasons
            foods.append(food)

    _FOOD_CACHE = foods
    return foods


def food_public_payload(food: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": food.get("id"),
        "kode": food.get("kode"),
        "nama": food.get("nama"),
        "kelompok_makanan": food.get("kelompok_makanan"),
        "tingkat_proses": food.get("tingkat_proses"),
        "slot_meal_plan": food.get("slot_meal_plan"),
        "gambar": food.get("gambar"),
        "gram_porsi": food.get("gram_porsi"),
        "kalori_kkal": round(float(food.get("kalori_kkal") or 0), 1),
        "karbohidrat_g": round(float(food.get("karbohidrat_g") or 0), 1),
        "protein_g": round(float(food.get("protein_g") or 0), 1),
        "lemak_g": round(float(food.get("lemak_g") or 0), 1),
        "serat_g": round(float(food.get("serat_g") or 0), 1),
        "gula_g": round(float(food.get("gula_g") or 0), 1),
        "natrium_mg": round(float(food.get("natrium_mg") or 0), 1),
        "is_recommended": bool(food.get("is_recommended")),
        "recommendation_label": food.get("recommendation_label"),
        "not_recommended_reasons": food.get("not_recommended_reasons") or [],
        "is_ultra_processed": str(food.get("tingkat_proses") or "").lower() == "ultraproses",
        "is_fruit": is_fruit(food),
    }


def search_tracker_foods(query: str, limit: int = 10) -> list[dict[str, Any]]:
    query = (query or "").strip().lower()
    if not query:
        return []
    terms = [t for t in query.split() if t]
    results: list[dict[str, Any]] = []
    for food in load_tracker_foods():
        text = food.get("search_text", "")
        if all(term in text for term in terms):
            results.append(food_public_payload(food))
        if len(results) >= max(1, min(limit, 20)):
            break
    return results


def get_food_by_id(food_id: str) -> Optional[dict[str, Any]]:
    food_id = str(food_id or "").strip()
    if not food_id:
        return None
    for food in load_tracker_foods():
        if str(food.get("id")) == food_id:
            return food
    return None
