from __future__ import annotations

from typing import Any

from app.services.health_score_service import calculate_weekly_health_score
from app.services.progress_service import summarize_week


def build_weekly_insight(logs: list[Any]) -> dict[str, Any]:
    """Insight mingguan dihitung ulang setiap halaman dibuka.

    Tidak disimpan ke database dulu, sesuai keputusan tahap awal.
    """
    summary = summarize_week(logs)
    health = calculate_weekly_health_score(logs)

    if not logs:
        return {
            "title": "Belum ada insight minggu ini",
            "message": "Isi catatan makanan dan aktivitas terlebih dahulu agar PrediBeat dapat membuat ringkasan mingguan.",
            "positives": [],
            "priorities": ["Mulai catat makanan hari ini dari meal plan atau database makanan lokal."],
            "health_score": health,
        }

    positives: list[str] = []
    priorities: list[str] = []

    if float(summary.get("avg_adherence") or 0) >= 70:
        positives.append("Kepatuhan terhadap meal plan sudah cukup baik.")
    else:
        priorities.append("Tingkatkan kepatuhan terhadap meal plan secara bertahap.")

    if float(summary.get("total_exercise_minutes") or 0) >= 150:
        positives.append("Target aktivitas fisik mingguan sudah tercapai.")
    else:
        priorities.append("Tambahkan aktivitas ringan sampai mendekati 150 menit per minggu.")

    if int(summary.get("ultra_processed_count") or 0) == 0:
        positives.append("Pilihan makanan minggu ini minim makanan ultra-proses.")
    else:
        priorities.append("Kurangi makanan ultra-proses dan pilih makanan yang lebih segar.")

    if int(summary.get("calorie_over_days") or 0) > 0:
        priorities.append("Perhatikan porsi pada hari yang melewati target kalori.")

    if int(summary.get("not_recommended_count") or 0) > 0:
        priorities.append("Cek makanan yang diberi label kurang direkomendasikan dan lihat alasannya.")

    if not positives:
        positives.append("Catatan minggu ini sudah menjadi langkah awal yang baik untuk membangun kebiasaan.")
    if not priorities:
        priorities.append("Pertahankan konsistensi dan lanjutkan pencatatan sampai akhir minggu.")

    return {
        "title": f"Insight minggu ini: {health['status']}",
        "message": health["summary"],
        "positives": positives[:3],
        "priorities": priorities[:3],
        "health_score": health,
        "summary": summary,
    }
