from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

from app.services.progress_service import get_attr, get_log_foods, progress_status, summarize_week


@dataclass(frozen=True)
class HealthScoreConfig:
    """Konfigurasi internal, tidak perlu ditampilkan ke user."""
    adherence_weight: float = 35.0
    activity_weight: float = 25.0
    calorie_balance_weight: float = 25.0
    food_quality_weight: float = 15.0
    weekly_activity_target_minutes: float = 150.0
    max_not_recommended_penalty: float = 10.0
    max_ultra_processed_penalty: float = 8.0
    max_high_sugar_penalty: float = 7.0


DEFAULT_HEALTH_SCORE_CONFIG = HealthScoreConfig()


def _calorie_balance_score(logs: list[Any], weight: float) -> float:
    if not logs:
        return 0.0
    per_day_scores: list[float] = []
    for log in logs:
        target = float(get_attr(log, "target_kalori", 0) or 0)
        actual = float(get_attr(log, "actual_kalori", 0) or 0)
        if target <= 0 or actual <= 0:
            per_day_scores.append(0.0)
            continue
        ratio = actual / target
        if 0.85 <= ratio <= 1.10:
            per_day_scores.append(1.0)
        elif 0.75 <= ratio < 0.85 or 1.10 < ratio <= 1.20:
            per_day_scores.append(0.7)
        elif 0.60 <= ratio < 0.75 or 1.20 < ratio <= 1.35:
            per_day_scores.append(0.4)
        else:
            per_day_scores.append(0.15)
    return round((sum(per_day_scores) / len(per_day_scores)) * weight, 1)


def _food_quality_score(logs: list[Any], config: HealthScoreConfig) -> tuple[float, dict[str, int]]:
    not_recommended = 0
    ultra_processed = 0
    high_sugar_non_fruit = 0

    for log in logs:
        for food in get_log_foods(log):
            reasons = [str(x).lower() for x in (food.get("not_recommended_reasons") or [])]
            if food.get("is_recommended") is False:
                not_recommended += 1
            if food.get("is_ultra_processed"):
                ultra_processed += 1
            # Arahan user: gula buah tidak dipenalti. Payload food_service membawa is_fruit.
            if (not food.get("is_fruit")) and any("gula tinggi" in r for r in reasons):
                high_sugar_non_fruit += 1

    penalty = min(not_recommended * 2.0, config.max_not_recommended_penalty)
    penalty += min(ultra_processed * 2.0, config.max_ultra_processed_penalty)
    penalty += min(high_sugar_non_fruit * 2.0, config.max_high_sugar_penalty)
    score = max(0.0, config.food_quality_weight - penalty)
    return round(score, 1), {
        "not_recommended_count": not_recommended,
        "ultra_processed_count": ultra_processed,
        "high_sugar_non_fruit_count": high_sugar_non_fruit,
    }


def calculate_weekly_health_score(logs: list[Any], config: HealthScoreConfig = DEFAULT_HEALTH_SCORE_CONFIG) -> dict[str, Any]:
    """Hitung satu Health Score untuk minggu berjalan.

    Ini belum mengubah logic lama progress; service ini berdiri sebagai fitur baru.
    """
    summary = summarize_week(logs)
    if not logs:
        return {
            "score": 0,
            "status": "Belum ada data",
            "summary": "Belum ada catatan minggu ini untuk menghitung Health Score.",
            "breakdown": {
                "meal_plan": 0,
                "activity": 0,
                "calorie_balance": 0,
                "food_quality": 0,
            },
            "metrics": {},
        }

    adherence_score = round(min(float(summary.get("avg_adherence") or 0), 100.0) / 100.0 * config.adherence_weight, 1)
    activity_minutes = float(summary.get("total_exercise_minutes") or 0)
    activity_score = round(min(activity_minutes / config.weekly_activity_target_minutes, 1.0) * config.activity_weight, 1)
    calorie_score = _calorie_balance_score(logs, config.calorie_balance_weight)
    food_quality_score, food_metrics = _food_quality_score(logs, config)

    score = round(adherence_score + activity_score + calorie_score + food_quality_score, 1)
    score = max(0.0, min(100.0, score))
    status = progress_status(score)

    if score >= 85:
        text = "Minggu ini sangat baik. Pertahankan pola makan, aktivitas, dan pilihan makanan yang sudah konsisten."
    elif score >= 70:
        text = "Minggu ini sudah baik. Fokuskan perbaikan kecil pada makanan yang kurang direkomendasikan atau aktivitas fisik."
    elif score >= 50:
        text = "Minggu ini cukup. Coba tingkatkan kepatuhan meal plan dan kurangi makanan yang kurang direkomendasikan."
    else:
        text = "Minggu ini masih perlu ditingkatkan. Mulai dari mencatat makanan lebih rutin dan memilih menu yang tersedia di meal plan."

    return {
        "score": score,
        "status": status,
        "summary": text,
        "breakdown": {
            "meal_plan": adherence_score,
            "activity": activity_score,
            "calorie_balance": calorie_score,
            "food_quality": food_quality_score,
        },
        "metrics": {
            "days_logged": summary.get("days_logged", 0),
            "avg_adherence": summary.get("avg_adherence", 0),
            "total_exercise_minutes": activity_minutes,
            **food_metrics,
        },
        "config": asdict(config),
    }
