from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any, Optional

from app.services.common_utils import json_loads, safe_float, today
from app.services.food_service import food_public_payload, get_food_by_id

KG_PER_KCAL = 7700.0
BASELINE_ACTIVITY_FACTOR = 1.2


def get_attr(obj: Any, name: str, default: Any = None) -> Any:
    return getattr(obj, name, default)


def estimate_bmr(profile_data: dict[str, Any]) -> float:
    bb = safe_float(profile_data.get("berat_badan"), 0.0) or 0.0
    tb = safe_float(profile_data.get("tinggi_badan"), 0.0) or 0.0
    usia = safe_float(profile_data.get("usia"), 0.0) or 0.0
    jk = str(profile_data.get("jenis_kelamin") or "").lower()
    if bb <= 0 or tb <= 0 or usia <= 0:
        return 0.0
    if "laki" in jk:
        return round((10 * bb) + (6.25 * tb) - (5 * usia) + 5, 0)
    return round((10 * bb) + (6.25 * tb) - (5 * usia) - 161, 0)


def baseline_calories_out(profile_data: dict[str, Any]) -> float:
    bmr = estimate_bmr(profile_data)
    return round(bmr * BASELINE_ACTIVITY_FACTOR, 1) if bmr else 0.0


def meal_plan_item_options(meal_plan: list[dict[str, Any]]) -> list[dict[str, Any]]:
    options: list[dict[str, Any]] = []
    for meal_idx, meal in enumerate(meal_plan or []):
        meal_label = meal.get("waktu") or meal.get("waktu_asli") or f"Makan {meal_idx + 1}"
        for item_idx, item in enumerate(meal.get("items", []) or []):
            name = item.get("nama") or item.get("name") or "Makanan"
            calories = safe_float(item.get("kalori"), 0.0) or 0.0
            options.append({
                "id": f"mp:{meal_idx}:{item_idx}",
                "meal_label": meal_label,
                "nama": name,
                "kalori": calories,
                "karbo": safe_float(item.get("karbo"), 0.0) or 0.0,
                "protein": safe_float(item.get("protein"), 0.0) or 0.0,
                "lemak": safe_float(item.get("lemak", item.get("lemak_total")), 0.0) or 0.0,
                "source": "meal_plan",
                "is_recommended": True,
                "recommendation_label": "Rekomendasi meal plan",
                "not_recommended_reasons": [],
            })
    return options


def meal_plan_totals(meal_plan: list[dict[str, Any]]) -> dict[str, float]:
    total = {"kalori": 0.0, "karbo": 0.0, "protein": 0.0, "lemak": 0.0}
    for option in meal_plan_item_options(meal_plan):
        total["kalori"] += float(option.get("kalori") or 0)
        total["karbo"] += float(option.get("karbo") or 0)
        total["protein"] += float(option.get("protein") or 0)
        total["lemak"] += float(option.get("lemak") or 0)
    return {k: round(v, 1) for k, v in total.items()}


def selected_meal_item_totals(meal_plan: list[dict[str, Any]], selected_ids: list[str]) -> tuple[dict[str, float], list[dict[str, Any]]]:
    total = {"kalori": 0.0, "karbo": 0.0, "protein": 0.0, "lemak": 0.0}
    selected_set = {str(x) for x in selected_ids}
    selected_foods: list[dict[str, Any]] = []
    for option in meal_plan_item_options(meal_plan):
        if option["id"] not in selected_set:
            continue
        total["kalori"] += float(option.get("kalori") or 0)
        total["karbo"] += float(option.get("karbo") or 0)
        total["protein"] += float(option.get("protein") or 0)
        total["lemak"] += float(option.get("lemak") or 0)
        selected_foods.append({
            "source": "meal_plan",
            "id": option["id"],
            "nama": option["nama"],
            "meal_label": option["meal_label"],
            "kalori": round(float(option.get("kalori") or 0), 1),
            "karbo": round(float(option.get("karbo") or 0), 1),
            "protein": round(float(option.get("protein") or 0), 1),
            "lemak": round(float(option.get("lemak") or 0), 1),
            "is_recommended": True,
            "recommendation_label": "Rekomendasi meal plan",
            "not_recommended_reasons": [],
            "is_ultra_processed": False,
        })
    return {k: round(v, 1) for k, v in total.items()}, selected_foods


def selected_database_foods(food_ids: list[str]) -> tuple[dict[str, float], list[dict[str, Any]]]:
    total = {"kalori": 0.0, "karbo": 0.0, "protein": 0.0, "lemak": 0.0}
    selected_foods: list[dict[str, Any]] = []
    for food_id in food_ids:
        food_id = str(food_id or "").strip()
        if not food_id:
            continue
        food = get_food_by_id(food_id)
        if not food:
            # Safety penting: makanan di luar dataset tidak diproses.
            continue
        payload = food_public_payload(food)
        total["kalori"] += float(payload.get("kalori_kkal") or 0)
        total["karbo"] += float(payload.get("karbohidrat_g") or 0)
        total["protein"] += float(payload.get("protein_g") or 0)
        total["lemak"] += float(payload.get("lemak_g") or 0)
        selected_foods.append({
            "source": "database",
            "id": food_id,
            "nama": payload.get("nama"),
            "meal_label": "Di luar meal plan",
            "kalori": round(float(payload.get("kalori_kkal") or 0), 1),
            "karbo": round(float(payload.get("karbohidrat_g") or 0), 1),
            "protein": round(float(payload.get("protein_g") or 0), 1),
            "lemak": round(float(payload.get("lemak_g") or 0), 1),
            "serat": round(float(payload.get("serat_g") or 0), 1),
            "gula": round(float(payload.get("gula_g") or 0), 1),
            "is_recommended": bool(payload.get("is_recommended")),
            "recommendation_label": payload.get("recommendation_label"),
            "not_recommended_reasons": payload.get("not_recommended_reasons") or [],
            "is_ultra_processed": bool(payload.get("is_ultra_processed")),
            "is_fruit": bool(payload.get("is_fruit")),
        })
    return {k: round(v, 1) for k, v in total.items()}, selected_foods


def progress_status(score: float) -> str:
    if score >= 85:
        return "Sangat Baik"
    if score >= 70:
        return "Baik"
    if score >= 50:
        return "Cukup"
    return "Perlu Ditingkatkan"


def serialize_log_foods(selected_foods: list[dict[str, Any]]) -> str:
    return json.dumps({"version": 2, "selected_foods": selected_foods}, ensure_ascii=False)


def get_log_foods(log: Any) -> list[dict[str, Any]]:
    data = json_loads(get_attr(log, "notes", None), {})
    if isinstance(data, dict) and isinstance(data.get("selected_foods"), list):
        return data.get("selected_foods") or []
    return []


def log_selected_meal_ids(log: Optional[Any]) -> list[str]:
    if not log:
        return []
    data = json_loads(get_attr(log, "selected_meal_indices", None), [])
    if isinstance(data, list):
        return [str(x) for x in data]
    return []


def split_saved_foods(log: Optional[Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    foods = get_log_foods(log) if log else []
    meal_foods = [food for food in foods if food.get("source") == "meal_plan"]
    db_foods = [food for food in foods if food.get("source") == "database"]
    return meal_foods, db_foods


def daily_weight_change_label(deficit: float) -> str:
    change = abs(deficit) / KG_PER_KCAL
    if deficit > 0:
        return f"Estimasi turun {change:.2f} kg"
    if deficit < 0:
        return f"Estimasi naik {change:.2f} kg"
    return "Estimasi stabil"


def macro_validation_messages(analysis: dict[str, Any], actual_kalori: float, actual_karbo: float, actual_protein: float, actual_lemak: float) -> list[str]:
    messages: list[str] = []
    target_kalori = safe_float(analysis.get("target_kalori"), 0.0) or 0.0
    target_karbo = safe_float(analysis.get("target_karbo"), 0.0) or 0.0
    target_protein = safe_float(analysis.get("target_protein"), 0.0) or 0.0
    target_lemak = safe_float(analysis.get("target_lemak"), 0.0) or 0.0

    if target_kalori > 0 and actual_kalori > 0:
        if actual_kalori > target_kalori * 1.10:
            messages.append(f"Kalori hari ini melewati target sekitar {round(actual_kalori - target_kalori):g} kkal.")
        elif actual_kalori < target_kalori * 0.75:
            messages.append("Kalori hari ini masih jauh di bawah target. Jangan terlalu rendah agar tubuh tetap bertenaga.")

    macro_targets = [
        ("Karbohidrat", actual_karbo, target_karbo, "g"),
        ("Protein", actual_protein, target_protein, "g"),
        ("Lemak", actual_lemak, target_lemak, "g"),
    ]
    for label, actual, target, unit in macro_targets:
        if target <= 0 or actual <= 0:
            continue
        if actual > target * 1.15:
            messages.append(f"{label} lebih tinggi dari target sekitar {round(actual - target, 1):g}{unit}.")
        elif actual < target * 0.70:
            messages.append(f"{label} masih kurang dari target sekitar {round(target - actual, 1):g}{unit}.")

    return messages


def diet_status_message(profile_data: dict[str, Any], analysis: dict[str, Any]) -> Optional[str]:
    active_diet = str(profile_data.get("active_diet") or analysis.get("diet_primary") or "").strip()
    if not active_diet:
        return None
    readable = active_diet.replace("_", " ").replace("-", " ").title()
    return f"Kamu sedang menjalani pola {readable}. Usahakan pilihan makanan tetap mendekati target kalori dan makro harian."


def date_nav_payload(selected_date: date, selected_log: Optional[Any] = None) -> dict[str, Any]:
    today_date = today()
    min_log_date = today_date - timedelta(days=6)
    previous_date = selected_date - timedelta(days=1)
    next_date = selected_date + timedelta(days=1)

    is_future = selected_date > today_date
    is_too_old_for_input = selected_date < min_log_date
    has_existing_log = selected_log is not None

    can_edit = (not is_future) and (not is_too_old_for_input) and (selected_date == today_date or not has_existing_log)

    if is_future:
        edit_note = "Tanggal masa depan tidak dapat diisi."
    elif is_too_old_for_input:
        edit_note = "Pengisian catatan hanya dibuka untuk hari ini sampai 7 hari terakhir."
    elif selected_date < today_date and has_existing_log:
        edit_note = "Catatan tanggal sebelumnya sudah tersimpan dan tidak dapat diedit."
    elif selected_date < today_date:
        edit_note = "Tanggal sebelumnya masih dapat diisi karena belum ada catatan."
    else:
        edit_note = "Catatan hari ini masih dapat diperbarui."

    return {
        "selected_date": selected_date,
        "selected_date_iso": selected_date.isoformat(),
        "previous_date": previous_date.isoformat(),
        "next_date": next_date.isoformat() if next_date <= today_date else None,
        "today_iso": today_date.isoformat(),
        "min_log_date_iso": min_log_date.isoformat(),
        "can_edit_selected_date": can_edit,
        "can_delete_selected_date": bool(has_existing_log and selected_date == today_date),
        "edit_window_note": edit_note,
    }


def summarize_week(logs: list[Any]) -> dict[str, Any]:
    if not logs:
        return {
            "days_logged": 0,
            "total_deficit": 0.0,
            "estimated_loss_kg": 0.0,
            "estimated_change_label": "Belum ada data",
            "estimated_change_text": "Belum ada cukup catatan untuk menghitung estimasi perubahan berat badan.",
            "avg_adherence": 0.0,
            "total_exercise_minutes": 0.0,
            "total_exercise_calories": 0.0,
            "total_calories_out": 0.0,
            "avg_calories_out": 0.0,
            "avg_actual_kalori": 0.0,
            "avg_actual_karbo": 0.0,
            "avg_actual_protein": 0.0,
            "avg_actual_lemak": 0.0,
            "score": 0.0,
            "status": "Belum ada data",
            "ultra_processed_count": 0,
            "not_recommended_count": 0,
            "calorie_over_days": 0,
            "calorie_low_days": 0,
            "warnings": [],
            "feedback": ["Belum ada catatan progress untuk minggu ini."],
        }

    n = len(logs)
    total_deficit = round(sum(float(get_attr(x, "deficit_calories", 0) or 0) for x in logs), 1)
    estimated_loss_kg = round(total_deficit / KG_PER_KCAL, 2)
    if total_deficit > 0:
        estimated_change_label = "Estimasi turun"
        estimated_change_text = f"Turun sekitar {abs(estimated_loss_kg):.2f} kg dari catatan minggu ini."
    elif total_deficit < 0:
        estimated_change_label = "Estimasi naik"
        estimated_change_text = f"Naik sekitar {abs(estimated_loss_kg):.2f} kg dari catatan minggu ini."
    else:
        estimated_change_label = "Stabil"
        estimated_change_text = "Berat badan diperkirakan relatif stabil dari catatan minggu ini."

    avg_adherence = round(sum(float(get_attr(x, "meal_plan_adherence_percent", 0) or 0) for x in logs) / n, 1)
    total_minutes = round(sum(float(get_attr(x, "duration_minutes", 0) or 0) for x in logs), 1)
    total_activity_cal = round(sum(float(get_attr(x, "activity_calories", 0) or 0) for x in logs), 1)
    total_calories_out = round(sum(float(get_attr(x, "calories_out", 0) or 0) for x in logs), 1)
    avg_calories_out = round(total_calories_out / n, 1) if n else 0.0

    avg_kalori = round(sum(float(get_attr(x, "actual_kalori", 0) or 0) for x in logs) / n, 1)
    avg_karbo = round(sum(float(get_attr(x, "actual_karbo", 0) or 0) for x in logs) / n, 1)
    avg_protein = round(sum(float(get_attr(x, "actual_protein", 0) or 0) for x in logs) / n, 1)
    avg_lemak = round(sum(float(get_attr(x, "actual_lemak", 0) or 0) for x in logs) / n, 1)

    ultra_processed_count = 0
    not_recommended_count = 0
    calorie_over_days = 0
    calorie_low_days = 0
    for log in logs:
        target = float(get_attr(log, "target_kalori", 0) or 0)
        actual = float(get_attr(log, "actual_kalori", 0) or 0)
        if target > 0 and actual > target * 1.10:
            calorie_over_days += 1
        if target > 0 and 0 < actual < target * 0.75:
            calorie_low_days += 1
        for food in get_log_foods(log):
            if food.get("is_ultra_processed"):
                ultra_processed_count += 1
            if food.get("is_recommended") is False:
                not_recommended_count += 1

    # Logic lama progress score dipertahankan.
    adherence_score = min(avg_adherence, 100.0) * 0.40
    exercise_score = min(total_minutes / 150.0, 1.0) * 30.0
    if total_deficit <= 0:
        deficit_score = 0.0
    elif total_deficit < 1500:
        deficit_score = 15.0
    elif total_deficit <= 5000:
        deficit_score = 30.0
    elif total_deficit <= 7000:
        deficit_score = 22.0
    else:
        deficit_score = 12.0
    score = round(adherence_score + exercise_score + deficit_score, 1)

    warnings: list[str] = []
    if ultra_processed_count > 1:
        warnings.append(f"Minggu ini ada {ultra_processed_count} makanan ultra-proses. Coba batasi dan pilih makanan yang lebih segar.")
    if calorie_over_days > 0:
        warnings.append(f"Ada {calorie_over_days} hari yang melewati target kalori. Perhatikan porsi dan pilihan makanan tinggi gula atau lemak.")
    if calorie_low_days > 0:
        warnings.append(f"Ada {calorie_low_days} hari dengan asupan terlalu rendah. Jaga agar tetap cukup dan tidak terlalu ekstrem.")
    if not_recommended_count > 0:
        warnings.append(f"Ada {not_recommended_count} makanan yang kurang direkomendasikan. Lihat alasannya pada catatan harian.")

    feedback: list[str] = []
    if total_deficit > 0:
        feedback.append(f"Minggu ini kamu defisit sekitar {total_deficit:g} kkal. Estimasi perubahan berat: turun {abs(estimated_loss_kg):.2f} kg.")
    elif total_deficit < 0:
        feedback.append(f"Minggu ini kamu surplus sekitar {abs(total_deficit):g} kkal. Estimasi perubahan berat: naik {abs(estimated_loss_kg):.2f} kg.")
    else:
        feedback.append("Energi masuk dan keluar minggu ini relatif seimbang.")

    if avg_adherence < 70:
        feedback.append("Kepatuhan terhadap meal plan masih dapat ditingkatkan secara bertahap.")
    else:
        feedback.append("Kepatuhan terhadap meal plan sudah baik. Pertahankan konsistensinya.")

    if total_minutes < 150:
        feedback.append(f"Aktivitas fisik minggu ini {total_minutes:g}/150 menit. Tambahkan aktivitas ringan secara bertahap.")
    else:
        feedback.append("Target aktivitas fisik 150 menit/minggu sudah tercapai.")

    return {
        "days_logged": n,
        "total_deficit": total_deficit,
        "estimated_loss_kg": estimated_loss_kg,
        "estimated_change_label": estimated_change_label,
        "estimated_change_text": estimated_change_text,
        "avg_adherence": avg_adherence,
        "total_exercise_minutes": total_minutes,
        "total_exercise_calories": total_activity_cal,
        "total_calories_out": total_calories_out,
        "avg_calories_out": avg_calories_out,
        "avg_actual_kalori": avg_kalori,
        "avg_actual_karbo": avg_karbo,
        "avg_actual_protein": avg_protein,
        "avg_actual_lemak": avg_lemak,
        "score": score,
        "status": progress_status(score),
        "ultra_processed_count": ultra_processed_count,
        "not_recommended_count": not_recommended_count,
        "calorie_over_days": calorie_over_days,
        "calorie_low_days": calorie_low_days,
        "warnings": warnings,
        "feedback": feedback,
    }


def build_week_view(start: date, logs: list[Any], target_default: float = 0.0, baseline_weight: float = 0.0) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    by_date = {get_attr(log, "tanggal"): log for log in logs}
    days: list[dict[str, Any]] = []
    labels: list[str] = []
    actual: list[float] = []
    target: list[float] = []
    adherence: list[float] = []
    burned: list[float] = []
    estimated_weight: list[float] = []
    cumulative_deficit = 0.0
    for i in range(7):
        d = start + timedelta(days=i)
        log = by_date.get(d)
        label = d.strftime("%d/%m")
        labels.append(label)
        actual_val = round(float(get_attr(log, "actual_kalori", 0) or 0), 1) if log else 0.0
        target_val = round(float(get_attr(log, "target_kalori", target_default) or target_default or 0), 1) if log else round(float(target_default or 0), 1)
        burned_val = round(float(get_attr(log, "calories_out", 0) or 0), 1) if log else 0.0
        if log:
            cumulative_deficit += float(get_attr(log, "deficit_calories", 0) or 0)
        if baseline_weight > 0:
            estimated_weight.append(round(baseline_weight - (cumulative_deficit / KG_PER_KCAL), 2))
        else:
            estimated_weight.append(0.0)
        actual.append(actual_val)
        target.append(target_val)
        burned.append(burned_val)
        adherence.append(round(float(get_attr(log, "meal_plan_adherence_percent", 0) or 0), 1) if log else 0.0)
        foods = get_log_foods(log) if log else []
        warning = None
        if log and target_val > 0 and actual_val > target_val * 1.10:
            warning = "Melebihi target kalori"
        elif log and target_val > 0 and actual_val > 0 and actual_val < target_val * 0.75:
            warning = "Kalori terlalu rendah"
        days.append({
            "date": d,
            "date_iso": d.isoformat(),
            "label": label,
            "log": log,
            "foods": foods,
            "actual_kalori": actual_val,
            "target_kalori": target_val,
            "calories_out": burned_val,
            "deficit_calories": round(float(get_attr(log, "deficit_calories", 0) or 0), 1) if log else 0.0,
            "activity_calories": round(float(get_attr(log, "activity_calories", 0) or 0), 1) if log else 0.0,
            "adherence": adherence[-1],
            "warning": warning,
        })
    chart_data = {
        "labels": labels,
        "actual": actual,
        "target": target,
        "burned": burned,
        "adherence": adherence,
        "estimated_weight": estimated_weight,
    }
    return days, chart_data
