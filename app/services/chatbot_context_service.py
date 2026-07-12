from __future__ import annotations

import re
from datetime import timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.progress_tracker import DailyProgressLog
from app.services.common_utils import monday_of_week, today
from app.services.food_service import food_public_payload, load_tracker_foods
from app.services.health_score_service import calculate_weekly_health_score
from app.services.progress_service import get_log_foods, summarize_week
from app.services.weekly_insight_service import build_weekly_insight


FOOD_QUESTION_TRIGGERS = {
    "boleh", "aman", "makan", "minum", "kalori", "gula", "natrium", "sodium",
    "gi", "indeks glikemik", "protein", "karbo", "lemak", "serat", "porsi",
    "rekomendasi", "tidak direkomendasikan", "diabetes", "prediabetes",
}

LOCAL_CONTEXT_TRIGGERS = {
    "health score", "skor", "progress", "minggu", "catatan", "hari ini", "kemarin",
    "aktivitas", "olahraga", "meal plan", "menu", "makan", "kalori", "diet saya",
    "target", "berat", "turun", "naik", "prediabeat",
}

STOPWORDS = {
    "aku", "saya", "user", "apakah", "apa", "boleh", "aman", "untuk", "buat", "makan", "minum",
    "di", "ke", "dari", "yang", "ini", "itu", "dan", "atau", "kalau", "kalo", "dengan", "tanpa",
    "berapa", "gimana", "bagaimana", "sebaiknya", "tolong", "kasih", "beri", "rekomendasi",
    "diabetes", "prediabetes", "gula", "kalori", "porsi", "tinggi", "rendah", "cocok", "ngga", "tidak",
}


def _norm(text: Any) -> str:
    text = str(text or "").lower()
    text = re.sub(r"[^a-z0-9\u00c0-\u024f\s]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _terms(text: str) -> list[str]:
    return [t for t in _norm(text).split() if len(t) >= 3 and t not in STOPWORDS]


def is_food_question(question: str) -> bool:
    q = _norm(question)
    return any(trigger in q for trigger in FOOD_QUESTION_TRIGGERS)


def should_answer_from_local_context(question: str) -> bool:
    q = _norm(question)
    return any(trigger in q for trigger in LOCAL_CONTEXT_TRIGGERS)


def find_relevant_local_foods(question: str, limit: int = 5) -> list[dict[str, Any]]:
    """Cari makanan lokal dari dataset tanpa membuka input makanan bebas.

    Fungsi ini hanya mengambil item yang sudah ada di dataset PrediBeat. Kalau tidak ketemu,
    chatbot harus mengatakan data belum tersedia, bukan mengarang nutrisi.
    """
    q = _norm(question)
    terms = _terms(question)
    if not q or not terms:
        return []

    ranked: list[tuple[int, dict[str, Any]]] = []
    for food in load_tracker_foods():
        name = _norm(food.get("nama"))
        search_text = _norm(food.get("search_text"))
        score = 0

        if name and name in q:
            score += 80
        if q and q in name:
            score += 50

        for term in terms:
            if term in name.split():
                score += 12
            elif term in name:
                score += 8
            elif term in search_text:
                score += 3

        # Hindari hasil terlalu lemah dari kata umum seperti "nasi" kalau pertanyaannya panjang tidak spesifik.
        if score >= 8:
            ranked.append((score, food))

    ranked.sort(key=lambda x: x[0], reverse=True)
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for _, food in ranked:
        key = str(food.get("nama") or "").lower()
        if key in seen:
            continue
        seen.add(key)
        results.append(food_public_payload(food))
        if len(results) >= limit:
            break
    return results


def _format_food_context(question: str) -> str:
    if not is_food_question(question):
        return ""

    foods = find_relevant_local_foods(question, limit=5)
    if not foods:
        return "\n".join([
            "DATA MAKANAN LOKAL TERKAIT PERTANYAAN: TIDAK DITEMUKAN.",
            "INSTRUKSI WAJIB:",
            "- Jika user meminta keputusan, kandungan gizi, keamanan, porsi, atau rekomendasi untuk makanan yang tidak ditemukan di dataset, jawab: Data makanan belum tersedia di PrediBeat.",
            "- Jangan mengarang angka kalori, gula, natrium, GI, protein, karbohidrat, lemak, atau klaim nutrisi untuk makanan tersebut.",
            "- Jangan menawarkan makanan di luar dataset sebagai pengganti, kecuali makanan itu memang ada pada meal plan user atau DATA MAKANAN LOKAL.",
        ])

    lines = [
        "DATA MAKANAN LOKAL TERKAIT PERTANYAAN:",
        "Gunakan hanya data makanan di bawah ini untuk keputusan makanan. Jangan mengarang makanan atau angka nutrisi di luar data ini.",
    ]
    for food in foods:
        status = "Sesuai" if food.get("is_recommended") else "Tidak direkomendasikan"
        reasons = food.get("not_recommended_reasons") or []
        lines.append(
            f"- {food.get('nama')} | kategori {food.get('kelompok_makanan') or '-'} | "
            f"kalori {food.get('kalori_kkal')} kkal | karbo {food.get('karbohidrat_g')}g | "
            f"protein {food.get('protein_g')}g | lemak {food.get('lemak_g')}g | serat {food.get('serat_g')}g | "
            f"gula_g {food.get('gula_g')}g | natrium {food.get('natrium_mg')}mg | "
            f"status {status} | alasan: {', '.join(reasons) if reasons else '-'} | buah: {bool(food.get('is_fruit'))}"
        )

    lines.extend([
        "ATURAN KHUSUS:",
        "- Gunakan gula_g untuk membahas gula.",
        "- Jika kategori makanan adalah Buah, jelaskan bahwa gula buah adalah gula alami dan tidak dipenalti seperti minuman manis/snack/dessert/ultra-proses.",
        "- Jika status Tidak direkomendasikan, sampaikan tegas dan singkat berdasarkan alasan dataset.",
    ])
    return "\n".join(lines)


def _format_week_progress_context(db: Session, user_id: int) -> str:
    today_date = today()
    start = monday_of_week(today_date)
    end = start + timedelta(days=6)

    logs = (
        db.query(DailyProgressLog)
        .filter(DailyProgressLog.user_id == user_id)
        .filter(DailyProgressLog.tanggal >= start)
        .filter(DailyProgressLog.tanggal <= end)
        .order_by(DailyProgressLog.tanggal.asc())
        .all()
    )

    summary = summarize_week(logs)
    health = calculate_weekly_health_score(logs)
    insight = build_weekly_insight(logs)
    today_log = next((log for log in logs if log.tanggal == today_date), None)

    lines = [
        "KONTEKS PROGRESS DAN HEALTH SCORE USER:",
        f"- Minggu berjalan: {start.isoformat()} sampai {end.isoformat()}",
        f"- Hari yang sudah dicatat: {summary.get('days_logged', 0)}/7",
        f"- Health Score: {health.get('score', 0)}/100 ({health.get('status', '-')})",
        f"- Insight singkat: {insight.get('message', health.get('summary', '-'))}",
        f"- Kepatuhan meal plan: {summary.get('avg_adherence', 0)}%",
        f"- Aktivitas minggu ini: {summary.get('total_exercise_minutes', 0)}/150 menit",
        f"- Rata-rata kalori dimakan: {summary.get('avg_actual_kalori', 0)} kkal/hari tercatat",
        f"- Total energi keluar: {summary.get('total_calories_out', 0)} kkal/minggu tercatat",
        f"- Estimasi berat: {summary.get('estimated_change_label', '-')} — {summary.get('estimated_change_text', '-')}",
    ]

    warnings = summary.get("warnings") or []
    if warnings:
        lines.append("- Peringatan minggu ini:")
        for warning in warnings[:3]:
            lines.append(f"  • {warning}")

    priorities = insight.get("priorities") or []
    if priorities:
        lines.append("- Fokus berikutnya:")
        for item in priorities[:3]:
            lines.append(f"  • {item}")

    positives = insight.get("positives") or []
    if positives:
        lines.append("- Yang sudah baik:")
        for item in positives[:3]:
            lines.append(f"  • {item}")

    if today_log:
        lines.append("\nCATATAN HARI INI:")
        lines.append(f"- Kalori dimakan: {float(today_log.actual_kalori or 0):.0f} kkal")
        lines.append(f"- Kalori keluar: {float(today_log.calories_out or 0):.0f} kkal")
        lines.append(f"- Aktivitas: {today_log.activity_name or 'Tidak ada'} {today_log.duration_minutes or 0:g} menit")
        foods = get_log_foods(today_log)
        if foods:
            lines.append("- Makanan hari ini:")
            for food in foods[:8]:
                status = "Sesuai" if food.get("is_recommended") else "Tidak direkomendasikan"
                reasons = food.get("not_recommended_reasons") or []
                lines.append(
                    f"  • {food.get('nama')} — {food.get('kalori', food.get('kalori_kkal', 0))} kkal, {status}"
                    + (f" ({', '.join(reasons)})" if reasons else "")
                )
        else:
            lines.append("- Makanan hari ini belum tercatat.")
    else:
        lines.append("\nCATATAN HARI INI: belum ada catatan untuk hari ini.")

    lines.append(
        "\nINSTRUKSI UNTUK CHATBOT: Jika user bertanya skor, progress, aktivitas, kalori minggu ini, catatan hari ini, atau kenapa Health Score rendah/tinggi, jawab berdasarkan konteks progress di atas. Jangan mengarang log yang tidak ada."
    )
    return "\n".join(lines)


def build_chatbot_extra_context(db: Session, user_id: int, question: str) -> str:
    """Konteks tambahan untuk /tanya.

    Disambungkan ke profil_singkat agar chatbot paham kondisi user terbaru tanpa mengubah logic lama.
    """
    parts = [_format_week_progress_context(db, user_id)]
    food_context = _format_food_context(question)
    if food_context:
        parts.append(food_context)
    return "\n\n".join(part for part in parts if part)
