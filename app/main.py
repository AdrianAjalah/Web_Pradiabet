from fastapi import FastAPI, Depends, HTTPException, Request, Form, File, UploadFile, BackgroundTasks
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from pydantic import BaseModel
from app.assessment import analisis_user, get_all_diets, get_diet_info, normalisasi_active_diet
from qdrant_client.http import models as qmodels
from app.rag_patch import smart_retrieve, patch_qdrant_payloads
from datetime import datetime, timedelta
import shutil
import os
import subprocess
import json
import requests
import csv
import math
import cohere
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")
# ── IMPORTS UNTUK AI & RAG ────────────────────────────────────
# from sentence_transformers import SentenceTransformer
from qdrant_client import QdrantClient
from groq import Groq
from ollama import Client as OllamaClient  # hanya untuk embedding lokal BGE-M3

# ── IMPORTS LOKAL ─────────────────────────────────────────────
from app.database import SessionLocal, UserDB, UserProfileDB, engine, Base
from app.auth import get_db, verify_password, get_password_hash, create_access_token, get_current_user, init_admin
from app.models import ProfilUser
from app.progress_tracker import router as progress_tracker_router, DailyProgressLog  # fitur Progress Tracker Mingguan
from app.services.chatbot_context_service import (
    build_chatbot_extra_context,
    should_answer_from_local_context,
)
from app.services.common_utils import monday_of_week
from app.services.weekly_insight_service import build_weekly_insight
COLLECTION_DOCS = "collection_documents"
COLLECTION_QA = "collection_qa"

# ── 1. DEFINISI URL BERDASARKAN SSH TUNNEL ────────────────────
# Port 11435 & 6334 terhubung ke HPC via SSH Tunnel
HPC_EMBED_URL = "http://localhost:8000/embed"
HPC_OLLAMA = os.environ.get("HPC_OLLAMA", "http://localhost:11435")
HPC_QDRANT = os.environ.get("HPC_QDRANT", "http://localhost:6334")

# Port 11434 & 6333 adalah Docker asli di laptop lokal
LOCAL_OLLAMA = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
LOCAL_QDRANT = os.environ.get("QDRANT_URL", "http://localhost:6333")
QDRANT_API_KEY = os.environ.get("QDRANT_API_KEY")

COHERE_API_KEY = os.environ.get("COHERE_API_KEY")
EMBEDDING_PROVIDER = os.environ.get("EMBEDDING_PROVIDER", "ollama").lower()

cohere_client = cohere.ClientV2(api_key=COHERE_API_KEY) if COHERE_API_KEY else None

# ── 2. FUNGSI SAKELAR PINTAR ──────────────────────────────────
def get_active_url(hpc_url, local_url, service_name):
    try:
        print(f"Mencoba menghubungi {service_name} di HPC (via Tunnel)...")
        requests.get(hpc_url, timeout=2) # Waktu tunggu sangat singkat (2 detik)
        print(f"✅ BERHASIL: Menggunakan tenaga HPC untuk {service_name}!")
        return hpc_url
    except requests.exceptions.RequestException:
        print(f"⚠️ GAGAL: Terputus dari HPC. Otomatis beralih ke {service_name} Lokal!")
        return local_url

# ── 3. INISIALISASI KONEKSI ──────────────────────────────────
# Catatan:
# - LLM lokal/Ollama untuk generate jawaban DIMATIKAN.
# - Ollama masih dipakai hanya untuk embedding lokal BGE-M3 agar RAG/Qdrant tetap jalan.
ACTIVE_OLLAMA_EMBED = get_active_url(HPC_OLLAMA, LOCAL_OLLAMA, "Ollama Embedding")

# Paksa Qdrant agar selalu membaca localhost (lokal)
ACTIVE_QDRANT = LOCAL_QDRANT

print(f"Menghubungkan ke Qdrant secara lokal di {ACTIVE_QDRANT}...")
qdrant = QdrantClient(
    url=ACTIVE_QDRANT,
    api_key=QDRANT_API_KEY if QDRANT_API_KEY else None,
    timeout=60
)

print("Menghubungkan ke Ollama hanya untuk embedding BGE-M3...")
embedding_client = OllamaClient(host=ACTIVE_OLLAMA_EMBED)

# LLM lokal dinonaktifkan — jawaban chatbot memakai Groq.
# ollama_client = OllamaClient(host=ACTIVE_OLLAMA_EMBED)

GROQ_MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
if not GROQ_API_KEY:
    print("⚠️ GROQ_API_KEY belum diset. Set dulu environment variable GROQ_API_KEY sebelum menjalankan chatbot Groq.")

groq_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

# print("Memuat model BGE-M3 (Embedding)...")
# embedder = SentenceTransformer("BAAI/bge-m3")

# Simpan riwayat chat per user di memori sementara
chat_histories = {}

# Field diet/scoring diet dihapus dari payload profil dan analisis.
DIET_KEYS_REMOVED = {
    "preferensi_diet",
    "diet_khusus",
    "diet_primary",
    "diet_alternatif",
    "diet_tambahan",
    "alasan_diet",
    "durasi_diet",
    "tingkat_ketaatan",
    "skor_kepatuhan_diet",
    "kategori_kepatuhan_diet",
}

def _hapus_field_diet(payload: dict) -> dict:
    if not isinstance(payload, dict):
        return payload
    for key in DIET_KEYS_REMOVED:
        payload.pop(key, None)
    return payload



def _safe_time_hour(jam: str):
    """Ambil jam sebagai integer dari string HH:MM. Return None kalau tidak valid."""
    if not jam:
        return None
    try:
        return int(str(jam).split(":", 1)[0])
    except Exception:
        return None


def _meal_time_aliases(label: str, jam: str, index: int, total: int, pola: str) -> list[str]:
    """Buat alias supaya chatbot paham 'makan siang' walaupun label IF = Makan 1/2/3."""
    label_lower = (label or "").strip().lower()
    aliases = []

    if label_lower:
        aliases.append(label_lower)

    if pola == "intermittent_fasting":
        aliases.append(f"makan {index} if")
        aliases.append(f"slot {index}")

    hour = _safe_time_hour(jam)
    if hour is not None:
        if 5 <= hour < 11:
            aliases.extend(["sarapan", "makan pagi", "pagi"])
        elif 11 <= hour < 15:
            aliases.extend(["makan siang", "siang", "lunch"])
        elif 15 <= hour < 18:
            aliases.extend(["makan sore", "sore", "camilan sore", "snack sore"])
        elif 18 <= hour <= 23 or 0 <= hour < 3:
            aliases.extend(["makan malam", "malam", "dinner"])

    if pola == "intermittent_fasting" and index == 1:
        aliases.extend(["makan pertama", "buka puasa", "makan siang jika jendela mulai siang"])
    if pola == "intermittent_fasting" and index == total:
        aliases.extend(["makan terakhir", "makan malam"])

    clean = []
    for a in aliases:
        a = str(a).strip().lower()
        if a and a not in clean:
            clean.append(a)
    return clean


def _build_chatbot_profile_context(profile_data: dict, analysis: dict) -> str:
    """
    Buat konteks profil lengkap untuk chatbot.
    Konteks ini dikirim ke LLM setiap user bertanya, supaya chatbot tahu:
    - profil kesehatan user
    - diet aktif
    - target kalori dan makro
    - pola waktu makan / IF
    - meal plan yang sedang tampil di dashboard
    """
    profile_data = profile_data or {}
    analysis = analysis or {}

    active_diet = normalisasi_active_diet(
        analysis.get("active_diet") or profile_data.get("active_diet")
    )
    diet_info = get_diet_info(active_diet)
    diet_label = diet_info.get("nama") if diet_info else "Tidak ada diet aktif"

    lines = [
        "KONTEKS PROFIL USER SAAT INI:",
        f"- Usia: {profile_data.get('usia', '-') } tahun",
        f"- Jenis kelamin: {profile_data.get('jenis_kelamin', '-')}",
        f"- Berat/Tinggi: {profile_data.get('berat_badan', '-')} kg / {profile_data.get('tinggi_badan', '-')} cm",
        f"- BMI: {analysis.get('bmi', '-')} ({analysis.get('kategori_bmi', '-')})",
        f"- Risiko diabetes/prediabetes: {analysis.get('kategori_risiko', '-')} (skor {analysis.get('skor_risiko', '-')})",
        f"- Target kalori: {analysis.get('target_kalori', '-')} kkal/hari",
        f"- Target makro: karbo {analysis.get('target_karbo', '-')} g, protein {analysis.get('target_protein', '-')} g, lemak {analysis.get('target_lemak', '-')} g",
        f"- Diet aktif: {diet_label}",
        f"- Pantangan/alergi: {profile_data.get('pantangan_alergi') or 'Tidak ada'}",
        f"- Penyakit/obat: {profile_data.get('penyakit_lain_obat') or 'Tidak ada'}",
        f"- Hasil lab: {profile_data.get('hasil_lab') or 'Belum ada / tidak diisi'}",
    ]

    pola = analysis.get("pola_waktu_makan") or profile_data.get("pola_waktu_makan") or "normal"
    if pola == "intermittent_fasting":
        lines.append(
            f"- Pola waktu makan: Intermittent Fasting {analysis.get('pola_puasa') or profile_data.get('pola_puasa') or ''}, "
            f"jendela makan {analysis.get('jam_makan_mulai') or profile_data.get('jam_makan_mulai') or '-'}–{analysis.get('jam_makan_selesai') or profile_data.get('jam_makan_selesai') or '-'}"
        )
    else:
        lines.append("- Pola waktu makan: makan biasa")

    meal_plan = analysis.get("meal_plan") or []
    if meal_plan:
        lines.append("\nMEAL PLAN USER YANG SEDANG AKTIF:")
        total_slots = len(meal_plan)
        for idx, meal in enumerate(meal_plan, start=1):
            label = meal.get("waktu") or meal.get("waktu_asli") or f"Makan {idx}"
            jam = meal.get("jam") or "-"
            aliases = _meal_time_aliases(label, jam, idx, total_slots, pola)
            target_slot = meal.get("target_kalori_slot", "-")
            total_slot = meal.get("total_kalori_slot", "-")
            lines.append(
                f"\nSlot {idx}: {label} | jam {jam} | alias: {', '.join(aliases)}"
            )
            lines.append(f"- Target slot: {target_slot} kkal | total aktual slot: {total_slot} kkal")

            items = meal.get("items") or []
            if not items:
                lines.append("- Belum ada item makanan di slot ini.")
                continue

            for item in items:
                nama = item.get("nama", "-")
                slot_label = item.get("slot_label") or item.get("kategori") or "Item"
                porsi = item.get("porsi") or "-"
                kalori = item.get("kalori", 0)
                karbo = item.get("karbo", 0)
                protein = item.get("protein", 0)
                lemak = item.get("lemak", item.get("lemak_total", 0))
                gula_tambahan = item.get("gula_tambahan_g", 0)
                sodium = item.get("sodium", 0)
                lines.append(
                    f"  • {slot_label}: {nama} ({porsi}) — {kalori} kkal; "
                    f"karbo {karbo}g, protein {protein}g, lemak {lemak}g, "
                    f"gula tambahan {gula_tambahan}g, sodium {sodium}mg"
                )
    else:
        lines.append("\nMEAL PLAN USER YANG SEDANG AKTIF: belum tersedia.")

    if analysis.get("catatan_pola_makan"):
        lines.append("\nCATATAN VALIDASI / ADAPTASI MEAL PLAN:")
        for catatan in analysis.get("catatan_pola_makan") or []:
            lines.append(f"- {catatan}")

    lines.append(
        "\nINSTRUKSI UNTUK CHATBOT: "
        "Jika user bertanya tentang menu miliknya, makanan hari ini, makan siang, sarapan, makan malam, kalori menu, diet aktif, atau target gizi, "
        "jawab berdasarkan MEAL PLAN USER YANG SEDANG AKTIF di atas terlebih dahulu, bukan dari dokumen RAG. "
        "Untuk Intermittent Fasting, label di dashboard bisa berupa Makan 1, Makan 2, Makan 3; gunakan jam dan alias untuk memahami maksud user. "
        "Jika user bertanya 'makan siang', cari slot dengan alias makan siang/siang atau jam sekitar 11:00–14:59; jika tidak ada, jelaskan bahwa pada IF slotnya memakai nama Makan 1/2/3. "
        "Jangan merekomendasikan makanan baru dari referensi dokumen kalau user sedang menanyakan meal plan yang sudah tampil. "
        "Jangan mengarang data yang tidak ada. Jika data belum tersedia, katakan bahwa datanya belum tersedia di profil user."
    )

    return "\n".join(str(x) for x in lines)

def get_query_embedding(text: str):
    """
    Embedding untuk query chatbot.
    Lokal default bisa tetap Ollama.
    Render bisa pakai Cohere.
    """
    if EMBEDDING_PROVIDER == "cohere":
        if cohere_client is None:
            raise HTTPException(
                status_code=500,
                detail="COHERE_API_KEY belum diset."
            )

        response = cohere_client.embed(
            model="embed-multilingual-v3.0",
            texts=[text],
            input_type="search_query",
            embedding_types=["float"],
        )

        return response.embeddings.float[0]

    embed_response = embedding_client.embeddings(
        model="bge-m3",
        prompt=text
    )
    return embed_response["embedding"]


def generate_groq_answer(
    pertanyaan: str,
    context: str,
    profil_singkat: str = "",
    history: list | None = None,
) -> str:
    """Generate jawaban chatbot memakai Groq, bukan LLM lokal."""
    if groq_client is None:
        raise HTTPException(
            status_code=500,
            detail="GROQ_API_KEY belum diset. Set environment variable GROQ_API_KEY lalu restart server.",
        )

    history = history or []
    safe_history = []
    for msg in history[-4:]:
        role = msg.get("role")
        content = msg.get("content")
        if role in ["user", "assistant"] and content:
            safe_history.append({"role": role, "content": str(content)[:500]})

    system_prompt = """
Kamu adalah Dr. Predia, asisten nutrisi dan edukasi prediabetes.
Jawab dalam Bahasa Indonesia yang ramah, jelas, praktis, dan mudah dipahami user awam.

Gunakan data profil user, diet aktif, target gizi, dan meal plan yang diberikan.
Jangan mengarang data profil, target kalori, target makro, atau menu yang tidak tersedia.
Jika pertanyaan terkait diagnosis, obat, penyakit serius, atau kondisi medis berat, sarankan konsultasi dengan dokter/ahli gizi.
Jangan menyebut bahwa kamu memakai Groq, RAG, prompt, atau konteks internal kecuali ditanya teknis oleh developer.

ATURAN FORMAT JAWABAN:
- Jangan tampilkan rumus matematika, LaTeX, atau kode seperti \\text{}, \\frac{}, ^2, [ ... ], atau simbol rumus mentah kecuali user secara eksplisit meminta rumus.
- Untuk pertanyaan hitungan, tampilkan hasil akhir dan penjelasan singkat saja.
- Jangan membuat tabel Markdown yang terlalu lebar karena akan berantakan di tampilan chatbot.
- Gunakan heading pendek, bullet point, dan daftar bernomor agar mudah dibaca.
- Hindari paragraf panjang.
- Gunakan emoji secukupnya agar terasa ramah, jangan berlebihan.
- Fokus pada jawaban yang langsung menjawab pertanyaan user.

ATURAN UNTUK HITUNGAN:
- Jika user bertanya BMI, kalori, target berat badan, atau perhitungan gizi, cukup tampilkan hasil akhirnya.
- Boleh jelaskan cara berpikirnya secara singkat, tapi jangan tampilkan rumus panjang.
- Contoh gaya jawaban:
  "Untuk tinggi 157 cm, BMI 25 setara dengan berat sekitar 61,6 kg. Jadi batas atas berat badan normalnya kira-kira 61–62 kg."

ATURAN UNTUK MEAL PLAN:
- Jika user bertanya menu, makan siang, sarapan, makan malam, makan 1, makan 2, makan 3, diet aktif, atau target gizi, jawab berdasarkan meal plan user yang sedang aktif.
- Jangan merekomendasikan makanan baru dari referensi dokumen jika user hanya bertanya menu yang sudah tampil.
- Jika label di dashboard memakai Makan 1, Makan 2, atau Makan 3, gunakan jam makan untuk memahami apakah itu sarapan, makan siang, makan sore, atau makan malam.
- Untuk rekomendasi meal plan, jangan gunakan tabel lebar.
- Gunakan format:
  1. Judul slot makan
  2. Total kalori slot
  3. Daftar menu bernomor
  4. Saran penyesuaian maksimal 3 poin
- Setiap item makanan cukup tampilkan angka penting saja: kalori, karbohidrat, protein, lemak, gula, atau sodium jika relevan.

ATURAN REKOMENDASI MAKANAN:
- Jika user meminta rekomendasi makanan, berikan rekomendasi yang ringkas dan selesai.
- Jangan membuat tabel Markdown yang lebar.
- Gunakan format daftar bernomor.
- Maksimal tampilkan 2–3 slot makan atau 3–5 makanan utama saja.
- Untuk setiap makanan cukup tampilkan: nama makanan, porsi, manfaat, dan alasan singkat.
- Jangan menulis terlalu panjang sampai jawaban terpotong.
- Tutup jawaban dengan saran singkat yang jelas.

ATURAN UNTUK MAKANAN DAN NUTRISI:
- Jika user bertanya apakah suatu makanan aman, jawab dengan keputusan sederhana: boleh, batasi, atau sebaiknya hindari.
- Jelaskan alasannya secara singkat berdasarkan kalori, gula, sodium, GI/GR, atau kandungan lain yang tersedia.
- Jika data makanan tidak tersedia, katakan bahwa data belum tersedia, lalu beri saran umum tanpa mengarang angka.
- Bedakan antara GI dan Glikemik Risk. GI adalah nilai indeks glikemik, sedangkan Glikemik Risk adalah kategori risiko dari dataset.
- Semua keputusan makanan harus berdasarkan MEAL PLAN USER atau DATA MAKANAN LOKAL yang diberikan di konteks.
- Jangan mengarang angka kalori, gula, natrium, GI, protein, karbohidrat, lemak, atau manfaat makanan jika tidak ada di konteks.
- Jika konteks menyatakan DATA MAKANAN LOKAL TIDAK DITEMUKAN, jawab tegas: "Data makanan belum tersedia di PrediBeat."
- Jangan menawarkan makanan alternatif di luar dataset lokal atau meal plan user.
- Gunakan gula_g, bukan gula_tambahan_g, saat membahas gula makanan.
- Jika kategori makanan adalah Buah, jelaskan bahwa gula buah adalah gula alami dan tidak dipenalti seperti minuman manis, snack, dessert, atau ultra-proses.
- Jika status makanan Tidak direkomendasikan, sampaikan tegas dan singkat sesuai alasan dataset.
""".strip()

    messages = [
        {"role": "system", "content": system_prompt},
    ]

    if profil_singkat:
        messages.append({
            "role": "system",
            "content": "DATA PROFIL, DIET AKTIF, DAN MEAL PLAN USER:\n" + str(profil_singkat)[:4500],
        })

    if context:
        messages.append({
            "role": "system",
            "content": "REFERENSI DOKUMEN RAG YANG RELEVAN:\n" + str(context)[:3500],
        })

    messages.extend(safe_history)
    messages.append({"role": "user", "content": pertanyaan})

    response = groq_client.chat.completions.create(
        model=GROQ_MODEL,
        messages=messages,
        temperature=0.4,
        max_tokens=1100,
    )
    return response.choices[0].message.content or "Maaf, saya belum bisa membuat jawaban saat ini."



# ── FITUR DAFTAR MENU & NUTRISI ─────────────────────────────────
# Dataset utama sekarang sama dengan meal plan dan progress tracker.
# Letakkan file CSV di: data/master_makanan_kategori_flag_mealplan.csv
MENU_DATASET_PATHS = [
    "data/master_makanan_kategori_flag_mealplan.csv",
    "master_makanan_kategori_flag_mealplan.csv",
    "/mnt/data/master_makanan_kategori_flag_mealplan.csv",
    # fallback lama jika file baru belum dipindahkan
    "data/matched_nutrisi_hotlink_gabungan_kategori.csv",
    "matched_nutrisi_hotlink_gabungan_kategori.csv",
    "/mnt/data/matched_nutrisi_hotlink_gabungan_kategori.csv",
]

HIGH_SODIUM_MG = 400.0       # per 100g/serving data
HIGH_SUGAR_G = 22.5          # per 100g/serving data
HIGH_GI_VALUE = 70.0
HIGH_FIBER_G = 6.0
HIGH_PROTEIN_G = 10.0
HIGH_CALORIE_KCAL = 300.0


def _find_menu_dataset_path():
    for path in MENU_DATASET_PATHS:
        if os.path.exists(path):
            return path
    return None


def _parse_float_id(value, default=0.0):
    """Parse angka format Indonesia: 4.166,70 -> 4166.70; 12,50 -> 12.50."""
    if value is None:
        return default
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "null", "-"}:
        return default
    text = text.replace(" ", "")
    try:
        if "," in text:
            text = text.replace(".", "").replace(",", ".")
        return float(text)
    except Exception:
        return default


def _to_bool(value):
    if value is None:
        return False
    text = str(value).strip().lower()
    return text in {"1", "true", "ya", "yes", "y"}


def _fmt_number(value, decimals=1):
    try:
        value = float(value)
    except Exception:
        return "-"
    if abs(value - round(value)) < 0.05:
        return str(int(round(value)))
    return f"{value:.{decimals}f}"


def _clean_url(value):
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in {"nan", "none", "null", "-"}:
        return ""
    if text.startswith("http://") or text.startswith("https://"):
        return text
    return ""




def _row_value(row, *names, default=""):
    for name in names:
        val = row.get(name)
        if val is not None and str(val).strip() != "":
            return val
    return default


def _derive_glycemic_risk_label(gi_value: float, explicit: str = ""):
    risk = (explicit or "").strip().lower()
    if risk in ["high", "hifh", "tinggi"]:
        return "high", "High", "bg-red-50 text-red-700 border-red-100"
    if risk in ["medium", "moderate", "sedang"]:
        return "moderate", "Medium", "bg-amber-50 text-amber-700 border-amber-100"
    if risk in ["low", "rendah"]:
        return "low", "Low", "bg-emerald-50 text-emerald-700 border-emerald-100"
    if gi_value >= HIGH_GI_VALUE:
        return "high", "High", "bg-red-50 text-red-700 border-red-100"
    if 0 < gi_value <= 55:
        return "low", "Low", "bg-emerald-50 text-emerald-700 border-emerald-100"
    if gi_value > 0:
        return "moderate", "Medium", "bg-amber-50 text-amber-700 border-amber-100"
    return "", "-", "bg-slate-50 text-slate-500 border-slate-100"

def _menu_category_icon(kategori):
    k = (kategori or "").lower()
    if "buah" in k:
        return "🍎"
    if "sayur" in k:
        return "🥦"
    if "minuman" in k:
        return "🥤"
    if "seafood" in k:
        return "🐟"
    if "dairy" in k:
        return "🥛"
    if "snack" in k or "dessert" in k:
        return "🍪"
    if "bumbu" in k:
        return "🧂"
    if "mentah" in k:
        return "🌾"
    if "lauk" in k:
        return "🍗"
    if "ultra" in k:
        return "📦"
    return "🍽️"


_MENU_CACHE = {
    "path": None,
    "mtime": None,
    "items": [],
}



def _load_menu_items():
    """Load menu dari master_makanan_kategori_flag_mealplan.csv. Cache refresh kalau file berubah."""
    path = _find_menu_dataset_path()
    if not path:
        return []

    mtime = os.path.getmtime(path)
    if _MENU_CACHE["path"] == path and _MENU_CACHE["mtime"] == mtime:
        return _MENU_CACHE["items"]

    items = []
    with open(path, "r", encoding="utf-8-sig", newline="", errors="replace") as f:
        sample = f.read(2048)
        f.seek(0)
        delimiter = ";" if sample.count(";") >= sample.count(",") else ","
        reader = csv.DictReader(f, delimiter=delimiter)
        for row in reader:
            nama = (_row_value(row, "nama_makanan", "nama") or "").strip()
            if not nama:
                continue

            kalori = _parse_float_id(_row_value(row, "kalori_kkal", "kalori_kal", "kalori"))
            karbo = _parse_float_id(_row_value(row, "karbohidrat_g", "karbo", "karbo_g"))
            protein = _parse_float_id(_row_value(row, "protein_g", "protein"))
            lemak = _parse_float_id(_row_value(row, "lemak_g", "lemak"))
            serat = _parse_float_id(row.get("serat_g"))
            gula = _parse_float_id(row.get("gula_g"))
            sodium = _parse_float_id(_row_value(row, "natrium_mg", "sodium_mg", "sodium"))
            gi = _parse_float_id(_row_value(row, "indeks_glikemik", "glikemik_indeks"), default=0.0)
            est_gi = _parse_float_id(_row_value(row, "indeks_glikemik_estimasi", "estimated_glikemik_indeks", "estimated_glycemic_index"), default=0.0)

            if gi and gi > 0:
                gi_final = gi
                gi_source = "GI asli"
            elif est_gi and est_gi > 0:
                gi_final = est_gi
                gi_source = "Estimasi GI"
            else:
                gi_final = 0.0
                gi_source = "-"

            gl = _parse_float_id(_row_value(row, "beban_glikemik", "glikemik_load"), default=0.0)
            glikemik_risk, glikemik_risk_label, glikemik_risk_class = _derive_glycemic_risk_label(
                gi_final,
                _row_value(row, "glikemik_risk", default=""),
            )

            kategori = (_row_value(row, "kelompok_makanan", "kategori_utama", "kategori") or "Lainnya").strip() or "Lainnya"
            jenis_bahan = (_row_value(row, "jenis_bahan_utama", default="") or "").strip()
            tingkat_proses = (_row_value(row, "tingkat_proses", default="") or "").strip()
            slot_meal_plan = (_row_value(row, "slot_meal_plan", default="No Meal") or "No Meal").strip()
            detail_parts = [x for x in [jenis_bahan, tingkat_proses, slot_meal_plan] if x]
            kategori_detail = " • ".join(detail_parts) or (_row_value(row, "kategori_detail", default="Umum") or "Umum")

            is_high_sodium = sodium >= HIGH_SODIUM_MG
            is_high_sugar = gula >= HIGH_SUGAR_G
            is_high_glycemic = gi_final >= HIGH_GI_VALUE
            is_low_glycemic = 0 < gi_final <= 55
            is_high_glycemic_risk = glikemik_risk == "high"
            is_ultraproses = tingkat_proses.lower() == "ultraproses" or _to_bool(_row_value(row, "is_ultraproses", "is_ultra_processed"))
            is_gorengan = _to_bool(_row_value(row, "adalah_gorengan", "is_gorengan", "is_fried"))
            is_bahan_mentah = tingkat_proses.lower() == "mentah" or _to_bool(_row_value(row, "is_bahan_mentah", "is_raw"))
            is_minuman = kategori.lower() == "minuman" or _to_bool(_row_value(row, "is_minuman", "is_beverage", "is_minuman_jus", "is_minuman_bersoda"))
            is_high_fiber = serat >= HIGH_FIBER_G
            is_high_protein = protein >= HIGH_PROTEIN_G
            is_high_calorie = kalori >= HIGH_CALORIE_KCAL
            mengandung_babi = _to_bool(row.get("mengandung_babi"))
            mengandung_alkohol = _to_bool(row.get("mengandung_alkohol"))

            is_recommended = not any([
                is_high_sodium,
                is_high_sugar,
                is_high_glycemic,
                is_high_glycemic_risk,
                is_ultraproses,
                is_gorengan,
                is_bahan_mentah,
                mengandung_babi,
                mengandung_alkohol,
            ])

            flags = []
            if is_recommended:
                flags.append({"key": "recommended", "label": "Lebih aman", "class": "bg-emerald-50 text-emerald-700 border-emerald-100"})
            if is_high_sodium:
                flags.append({"key": "high_sodium", "label": "High sodium", "class": "bg-orange-50 text-orange-700 border-orange-100"})
            if is_high_sugar:
                flags.append({"key": "high_sugar", "label": "High sugar", "class": "bg-rose-50 text-rose-700 border-rose-100"})
            if is_high_glycemic:
                flags.append({"key": "high_glycemic", "label": "High GI", "class": "bg-red-50 text-red-700 border-red-100"})
            if is_high_glycemic_risk and not is_high_glycemic:
                flags.append({"key": "high_glycemic_risk", "label": "Risk glikemik tinggi", "class": "bg-fuchsia-50 text-fuchsia-700 border-fuchsia-100"})
            if is_ultraproses:
                flags.append({"key": "ultra_processed", "label": "Ultra-proses", "class": "bg-slate-100 text-slate-700 border-slate-200"})
            if is_gorengan:
                flags.append({"key": "fried", "label": "Gorengan", "class": "bg-amber-50 text-amber-700 border-amber-100"})
            if is_bahan_mentah:
                flags.append({"key": "raw", "label": "Bahan mentah", "class": "bg-zinc-100 text-zinc-700 border-zinc-200"})
            if mengandung_babi:
                flags.append({"key": "pork", "label": "Mengandung babi", "class": "bg-rose-100 text-rose-700 border-rose-200"})
            if mengandung_alkohol:
                flags.append({"key": "alcohol", "label": "Mengandung alkohol", "class": "bg-purple-50 text-purple-700 border-purple-100"})
            if is_low_glycemic:
                flags.append({"key": "low_gi", "label": "Low GI", "class": "bg-teal-50 text-teal-700 border-teal-100"})
            if is_high_fiber:
                flags.append({"key": "high_fiber", "label": "Tinggi serat", "class": "bg-green-50 text-green-700 border-green-100"})
            if is_high_protein:
                flags.append({"key": "high_protein", "label": "Tinggi protein", "class": "bg-blue-50 text-blue-700 border-blue-100"})

            net_karbo = _parse_float_id(_row_value(row, "net_karbo", default=""), default=max(0.0, karbo - serat))
            item = {
                "kode": (_row_value(row, "kode") or "").strip(),
                "nama": nama,
                "kategori_utama": kategori,
                "kelompok_makanan": kategori,
                "jenis_bahan_utama": jenis_bahan,
                "tingkat_proses": tingkat_proses,
                "slot_meal_plan": slot_meal_plan,
                "kategori_detail": kategori_detail,
                "kategori_pantangan": (row.get("kategori_pantangan") or "").strip(),
                "sumber_file": (_row_value(row, "file_sumber", "sumber_file") or "").strip(),
                "hotlink": _clean_url(_row_value(row, "gambar", "hotlink")),
                "icon": _menu_category_icon(kategori),
                "porsi_g": _parse_float_id(_row_value(row, "gram_porsi", "porsi_g"), default=100.0),

                "kalori_kal": kalori,
                "karbohidrat_g": karbo,
                "protein_g": protein,
                "lemak_g": lemak,
                "serat_g": serat,
                "gula_g": gula,
                "sodium_mg": sodium,
                "glikemik_indeks": gi_final,
                "gi_source": gi_source,
                "glikemik_load": gl,
                "glikemik_risk": glikemik_risk,
                "glikemik_risk_label": glikemik_risk_label,
                "glikemik_risk_class": glikemik_risk_class,
                "net_karbo": net_karbo,
                "lemak_jenuh_g": _parse_float_id(row.get("lemak_jenuh_g")),
                "lemak_trans_g": _parse_float_id(row.get("lemak_trans_g")),
                "mufa_g": _parse_float_id(_row_value(row, "lemak_tak_jenuh_tunggal_g", "lemak_tak_jenuh_tunggal_g(MUFA)")),
                "pufa_g": _parse_float_id(_row_value(row, "lemak_tak_jenuh_ganda_g", "lemak_tak_jenuh_ganda_g(PUFA)")),

                "kalori_fmt": _fmt_number(kalori, 0),
                "karbo_fmt": _fmt_number(karbo),
                "protein_fmt": _fmt_number(protein),
                "lemak_fmt": _fmt_number(lemak),
                "serat_fmt": _fmt_number(serat),
                "gula_fmt": _fmt_number(gula),
                "sodium_fmt": _fmt_number(sodium, 0),
                "gi_fmt": _fmt_number(gi_final, 0) if gi_final else "-",
                "gl_fmt": _fmt_number(gl) if gl else "-",

                "is_high_sodium": is_high_sodium,
                "is_high_sugar": is_high_sugar,
                "is_high_glycemic": is_high_glycemic,
                "is_low_glycemic": is_low_glycemic,
                "is_high_glycemic_risk": is_high_glycemic_risk,
                "is_ultraproses": is_ultraproses,
                "is_gorengan": is_gorengan,
                "is_bahan_mentah": is_bahan_mentah,
                "is_minuman": is_minuman,
                "is_high_fiber": is_high_fiber,
                "is_high_protein": is_high_protein,
                "is_high_calorie": is_high_calorie,
                "is_recommended": is_recommended,
                "mengandung_babi": mengandung_babi,
                "mengandung_alkohol": mengandung_alkohol,
                "flags": flags[:6],
                "search_text": " ".join([
                    nama,
                    _row_value(row, "kode") or "",
                    kategori,
                    jenis_bahan,
                    tingkat_proses,
                    slot_meal_plan,
                    row.get("catatan_kategori") or "",
                    _row_value(row, "file_sumber", "sumber_file") or "",
                ]).lower(),
            }
            items.append(item)

    _MENU_CACHE.update({"path": path, "mtime": mtime, "items": items})
    return items

def _menu_filter_options():
    return [
        {"key": "all", "label": "Semua", "icon": "✨", "desc": "Semua data makanan"},
        {"key": "recommended", "label": "Lebih aman", "icon": "✅", "desc": "Tidak high sodium/gula/GI/GR, bukan ultra-proses/gorengan/mentah"},
        {"key": "high_sodium", "label": "High sodium", "icon": "🧂", "desc": f"Sodium ≥ {int(HIGH_SODIUM_MG)} mg"},
        {"key": "high_sugar", "label": "High sugar", "icon": "🍬", "desc": f"Gula ≥ {HIGH_SUGAR_G:g} g"},
        {"key": "high_glycemic", "label": "High GI", "icon": "📈", "desc": f"Hanya GI/estimated GI ≥ {int(HIGH_GI_VALUE)}"},
        {"key": "high_glycemic_risk", "label": "Risk glikemik tinggi", "icon": "⚠️", "desc": "GR/risk high dari dataset, bukan nilai GI"},
        {"key": "ultra_processed", "label": "Ultra-proses", "icon": "📦", "desc": "Produk kemasan/ultra-proses"},
        {"key": "fried", "label": "Gorengan", "icon": "🍟", "desc": "Makanan digoreng"},
        {"key": "raw", "label": "Bahan mentah", "icon": "🌾", "desc": "Belum siap jadi meal plan"},
        {"key": "low_gi", "label": "Low GI", "icon": "🟢", "desc": "GI rendah"},
        {"key": "high_fiber", "label": "Tinggi serat", "icon": "🥦", "desc": f"Serat ≥ {int(HIGH_FIBER_G)} g"},
        {"key": "high_protein", "label": "Tinggi protein", "icon": "💪", "desc": f"Protein ≥ {int(HIGH_PROTEIN_G)} g"},
        {"key": "beverage", "label": "Minuman", "icon": "🥤", "desc": "Kategori minuman"},
    ]


def _match_menu_filter(item, filter_key):
    if filter_key == "all":
        return True
    mapping = {
        "recommended": "is_recommended",
        "high_sodium": "is_high_sodium",
        "high_sugar": "is_high_sugar",
        "high_glycemic": "is_high_glycemic",
        "high_glycemic_risk": "is_high_glycemic_risk",
        "ultra_processed": "is_ultraproses",
        "fried": "is_gorengan",
        "raw": "is_bahan_mentah",
        "low_gi": "is_low_glycemic",
        "high_fiber": "is_high_fiber",
        "high_protein": "is_high_protein",
        "beverage": "is_minuman",
    }
    key = mapping.get(filter_key)
    return bool(item.get(key)) if key else True


def _sort_menu_items(items, sort_key):
    reverse = False
    key = sort_key or "nama_asc"
    if key.endswith("_desc"):
        reverse = True

    sort_map = {
        "nama_asc": lambda x: x["nama"].lower(),
        "kalori_desc": lambda x: x["kalori_kal"],
        "kalori_asc": lambda x: x["kalori_kal"],
        "protein_desc": lambda x: x["protein_g"],
        "gula_desc": lambda x: x["gula_g"],
        "sodium_desc": lambda x: x["sodium_mg"],
        "serat_desc": lambda x: x["serat_g"],
        "gi_desc": lambda x: x["glikemik_indeks"],
    }
    return sorted(items, key=sort_map.get(key, sort_map["nama_asc"]), reverse=reverse)


app = FastAPI()
app.include_router(progress_tracker_router)  # /progress

# ── SETUP STATIC FILES & TEMPLATES ────────────────────────────
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# Buat tabel database dan inisialisasi akun admin
Base.metadata.create_all(bind=engine)
init_admin()
try:
    patch_qdrant_payloads(qdrant, COLLECTION_DOCS)
except Exception as e:
    print(f"⚠️ Patch Qdrant dilewati: {e}")

# ── ROUTES: AUTENTIKASI & DASHBOARD ───────────────────────────

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(request=request, name="landing.html")

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse(request=request, name="login.html")

@app.post("/login")
async def login(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    username = form.get("username")
    password = form.get("password")
    
    user = db.query(UserDB).filter(UserDB.username == username).first()
    
    if not user or not verify_password(password, user.hashed_password):
        return templates.TemplateResponse(
            request=request,
            name="login.html", 
            context={"error": "Username atau Password salah!"}
        )
    
    access_token = create_access_token(data={"sub": user.username})
    response = RedirectResponse(url="/dashboard", status_code=302)
    response.set_cookie(key="access_token", value=f"Bearer {access_token}", httponly=True)
    return response

@app.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    return templates.TemplateResponse(request=request, name="register.html")    

@app.post("/register")
async def register(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    username = form.get("username")
    email = form.get("email")
    password = form.get("password")
    
    if db.query(UserDB).filter(UserDB.username == username).first():
        return templates.TemplateResponse(
            request=request,
            name="register.html", 
            context={"error": "Username sudah terpakai!"}
        )
    
    new_user = UserDB(
        username=username,
        email=email,
        hashed_password=get_password_hash(password),
        role="user"
    )
    db.add(new_user)
    db.commit()
    
    return RedirectResponse(url="/login", status_code=302)

@app.get("/logout")
async def logout(current_user: UserDB = Depends(get_current_user)):
    # Hapus history chat user dari memori server
    if current_user.id in chat_histories:
        del chat_histories[current_user.id]
    
    response = RedirectResponse(url="/login")
    response.delete_cookie("access_token")
    return response

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, db: Session = Depends(get_db), current_user: UserDB = Depends(get_current_user)):
    if current_user.role == "admin":
        processed_dir = "data/DocumentPradiabet_Done"
        uploaded_files = []
        if os.path.exists(processed_dir):
            uploaded_files = os.listdir(processed_dir)
        return templates.TemplateResponse(
            request=request, 
            name="admin_dashboard.html",
            context={"user": current_user, "uploaded_files": uploaded_files}
        )
    
    profile = db.query(UserProfileDB).filter(UserProfileDB.user_id == current_user.id).first()

    if not profile:
        return RedirectResponse(url="/questionnaire", status_code=302)
    
    # ✅ AUTO-REDIRECT: Langsung ke halaman cek kesehatan jika belum isi data
    profile_data = json.loads(profile.full_profile_data) # <--- Definisikan dulu di sini

    needs_update = False
    updated_at_str = profile_data.get("updated_at")
    
    if updated_at_str:
        try:
            last_updated = datetime.fromisoformat(updated_at_str)
            if datetime.now() - last_updated > timedelta(minutes=1):
                needs_update = True
        except ValueError:
            needs_update = True
    else:
        # Untuk data lama yang belum punya stempel waktu
        needs_update = True
    
    analysis_data = json.loads(profile.analysis_result)
    active_diet = normalisasi_active_diet(analysis_data.get("active_diet") or profile_data.get("active_diet"))

    context = {"user": current_user, "has_profile": True}
    context["needs_update"] = needs_update
    context["profile"] = profile_data
    context["analysis"] = analysis_data
    context["active_diet_info"] = get_diet_info(active_diet)

    # Ringkasan Health Score untuk Dashboard.
    # Detail lengkap tetap ada di halaman /progress.
    dashboard_date = datetime.now().date()
    week_start = monday_of_week(dashboard_date)
    week_end = week_start + timedelta(days=6)
    weekly_logs = (
        db.query(DailyProgressLog)
        .filter(DailyProgressLog.user_id == current_user.id)
        .filter(DailyProgressLog.tanggal >= week_start)
        .filter(DailyProgressLog.tanggal <= week_end)
        .order_by(DailyProgressLog.tanggal.asc())
        .all()
    )
    weekly_insight = build_weekly_insight(weekly_logs)
    context["weekly_insight"] = weekly_insight
    context["health_score"] = weekly_insight.get("health_score")
    context["dashboard_progress_summary"] = weekly_insight.get("summary", {})
    context["week_start"] = week_start
    context["week_end"] = week_end

    return templates.TemplateResponse(request=request, name="dashboard.html", context=context)


@app.get("/menu", response_class=HTMLResponse)
async def menu_page(
    request: Request,
    q: str = "",
    kategori: str = "",
    filter: str = "all",
    sort: str = "nama_asc",
    page: int = 1,
    per_page: int = 24,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    """
    Halaman daftar menu/nutrisi.
    Bisa filter high sodium, high sugar, high GI, ultra-proses, gorengan, bahan mentah, dll.
    """
    if current_user.role == "admin":
        # Admin tetap boleh lihat daftar menu; hapus blok ini kalau ingin admin diarahkan ke dashboard.
        pass

    items = _load_menu_items()
    dataset_path = _find_menu_dataset_path()

    q_clean = (q or "").strip().lower()
    kategori_clean = (kategori or "").strip()
    filter_key = (filter or "all").strip()

    categories = sorted({item["kategori_utama"] for item in items if item.get("kategori_utama")})

    filtered = items
    if q_clean:
        terms = [t for t in q_clean.split() if t]
        filtered = [
            item for item in filtered
            if all(term in item["search_text"] for term in terms)
        ]

    if kategori_clean:
        filtered = [
            item for item in filtered
            if item.get("kategori_utama") == kategori_clean
        ]

    filtered = [item for item in filtered if _match_menu_filter(item, filter_key)]
    filtered = _sort_menu_items(filtered, sort)

    per_page = max(12, min(int(per_page or 24), 60))
    total_items = len(items)
    total_filtered = len(filtered)
    total_pages = max(1, math.ceil(total_filtered / per_page))
    page = max(1, min(int(page or 1), total_pages))
    start = (page - 1) * per_page
    end = start + per_page
    page_items = filtered[start:end]

    filter_options = _menu_filter_options()
    counts_by_filter = {
        opt["key"]: sum(1 for item in items if _match_menu_filter(item, opt["key"]))
        for opt in filter_options
    }

    active_filter_label = next(
        (opt["label"] for opt in filter_options if opt["key"] == filter_key),
        "Semua"
    )

    return templates.TemplateResponse(
        request=request,
        name="menu_list.html",
        context={
            "user": current_user,
            "items": page_items,
            "total_items": total_items,
            "total_filtered": total_filtered,
            "page": page,
            "per_page": per_page,
            "total_pages": total_pages,
            "q": q,
            "kategori": kategori_clean,
            "filter": filter_key,
            "sort": sort,
            "categories": categories,
            "filter_options": filter_options,
            "counts_by_filter": counts_by_filter,
            "active_filter_label": active_filter_label,
            "dataset_path": dataset_path,
            "thresholds": {
                "high_sodium": HIGH_SODIUM_MG,
                "high_sugar": HIGH_SUGAR_G,
                "high_gi": HIGH_GI_VALUE,
            },
        },
    )


# ── ROUTES: FITUR DIET AKTIF ─────────────────────────────────

@app.get("/diet", response_class=HTMLResponse)
async def diet_list_page(request: Request, db: Session = Depends(get_db), current_user: UserDB = Depends(get_current_user)):
    if current_user.role == "admin":
        return RedirectResponse(url="/dashboard", status_code=302)

    profile_row = db.query(UserProfileDB).filter(UserProfileDB.user_id == current_user.id).first()
    active_diet = None
    if profile_row:
        try:
            profile_data = json.loads(profile_row.full_profile_data)
            analysis_data = json.loads(profile_row.analysis_result)
            active_diet = normalisasi_active_diet(analysis_data.get("active_diet") or profile_data.get("active_diet"))
        except Exception:
            active_diet = None

    return templates.TemplateResponse(
        request=request,
        name="diet_list.html",
        context={
            "user": current_user,
            "diets": get_all_diets(),
            "active_diet": active_diet,
        },
    )


@app.get("/diet/{diet_id}", response_class=HTMLResponse)
async def diet_detail_page(diet_id: str, request: Request, db: Session = Depends(get_db), current_user: UserDB = Depends(get_current_user)):
    if current_user.role == "admin":
        return RedirectResponse(url="/dashboard", status_code=302)

    diet_key = normalisasi_active_diet(diet_id)
    diet_info = get_diet_info(diet_key)
    if not diet_info:
        raise HTTPException(status_code=404, detail="Diet tidak ditemukan")

    profile_row = db.query(UserProfileDB).filter(UserProfileDB.user_id == current_user.id).first()
    active_diet = None
    if profile_row:
        try:
            profile_data = json.loads(profile_row.full_profile_data)
            analysis_data = json.loads(profile_row.analysis_result)
            active_diet = normalisasi_active_diet(analysis_data.get("active_diet") or profile_data.get("active_diet"))
        except Exception:
            active_diet = None

    return templates.TemplateResponse(
        request=request,
        name="diet_detail.html",
        context={
            "user": current_user,
            "diet": diet_info,
            "active_diet": active_diet,
        },
    )


@app.post("/api/activate-diet/{diet_id}")
async def activate_diet(diet_id: str, db: Session = Depends(get_db), current_user: UserDB = Depends(get_current_user)):
    """Aktifkan diet pilihan, lalu hitung ulang analisis dan meal plan."""
    diet_key = normalisasi_active_diet(diet_id)
    if not diet_key or not get_diet_info(diet_key):
        raise HTTPException(status_code=404, detail="Diet tidak ditemukan")

    profile_row = db.query(UserProfileDB).filter(UserProfileDB.user_id == current_user.id).first()
    if not profile_row:
        return RedirectResponse(url="/questionnaire", status_code=302)

    profile_data = _hapus_field_diet(json.loads(profile_row.full_profile_data))
    profile_data["active_diet"] = diet_key
    profile_data["diet_updated_at"] = datetime.now().isoformat()

    profile = ProfilUser(**profile_data)
    result = analisis_user(profile)

    profile_row.full_profile_data = json.dumps(profile_data)
    profile_row.analysis_result = json.dumps(_hapus_field_diet(result.model_dump()))
    db.commit()

    return RedirectResponse(url="/dashboard", status_code=302)


@app.post("/api/deactivate-diet")
async def deactivate_diet(db: Session = Depends(get_db), current_user: UserDB = Depends(get_current_user)):
    """Matikan diet aktif, lalu kembali ke meal plan standar."""
    profile_row = db.query(UserProfileDB).filter(UserProfileDB.user_id == current_user.id).first()
    if not profile_row:
        return RedirectResponse(url="/questionnaire", status_code=302)

    profile_data = _hapus_field_diet(json.loads(profile_row.full_profile_data))
    profile_data["active_diet"] = None
    profile_data["diet_updated_at"] = datetime.now().isoformat()

    profile = ProfilUser(**profile_data)
    result = analisis_user(profile)

    profile_row.full_profile_data = json.dumps(profile_data)
    profile_row.analysis_result = json.dumps(_hapus_field_diet(result.model_dump()))
    db.commit()

    return RedirectResponse(url="/dashboard", status_code=302)


# ── ROUTES: KUESIONER & PROFIL ────────────────────────────────

@app.get("/questionnaire", response_class=HTMLResponse)
async def questionnaire_page(request: Request, db: Session = Depends(get_db), current_user: UserDB = Depends(get_current_user)):
    if current_user.role == "admin":
        return RedirectResponse(url="/dashboard", status_code=302)

    profile = db.query(UserProfileDB).filter(UserProfileDB.user_id == current_user.id).first()
    
    # Jika user sudah punya profil, siapkan data lama untuk form edit
    profile_data = None
    if profile:
        profile_data = json.loads(profile.full_profile_data)
        

    return templates.TemplateResponse(
        request=request, 
        name="questionnaire.html",
        context={
            "user": current_user, 
            "profile_data": profile_data,  # Kirim data lama ke template
            "is_edit": profile is not None  # Flag untuk mengubah judul form
        }
    )



def _normalisasi_pola_waktu_makan(value):
    """Normalisasi input pola waktu makan dari form."""
    value = (value or "normal").strip().lower()
    if value in ["if", "intermittent", "intermittent fasting", "intermittent_fasting", "puasa"]:
        return "intermittent_fasting"
    return "normal"


@app.post("/api/submit-assessment")
async def submit_assessment(request: Request, db: Session = Depends(get_db), current_user: UserDB = Depends(get_current_user)):
    form_data = await request.form()
    
    riwayat_keluarga = str(form_data.get("riwayat_keluarga_diabetes")).lower() == "true"
    gejala_list = form_data.getlist("gejala_klasik")
    
    # Tangkap preferensi meal plan
    frekuensi_makan = form_data.get("frekuensi_makan") or "3x"
    waktu_makan_list = form_data.getlist("waktu_makan")  # getlist karena multiple values

    pola_waktu_makan = _normalisasi_pola_waktu_makan(form_data.get("pola_waktu_makan"))
    pola_puasa = form_data.get("pola_puasa") or None
    jam_makan_mulai = form_data.get("jam_makan_mulai") or None
    jam_makan_selesai = form_data.get("jam_makan_selesai") or None

    existing = db.query(UserProfileDB).filter(UserProfileDB.user_id == current_user.id).first()
    existing_profile_data = {}
    if existing:
        try:
            existing_profile_data = json.loads(existing.full_profile_data)
        except Exception:
            existing_profile_data = {}
    active_diet = normalisasi_active_diet(form_data.get("active_diet") or existing_profile_data.get("active_diet"))
    
    # Default waktu makan jika user tidak memilih (fallback).
    # IF hanya pola jam makan, bukan pemaksa 3x makan.
    if not waktu_makan_list:
        if frekuensi_makan == "2x":
            waktu_makan_list = ["Siang", "Malam"]
        elif pola_waktu_makan == "intermittent_fasting":
            waktu_makan_list = ["Siang", "Sore", "Malam"]
        else:
            waktu_makan_list = ["Pagi", "Siang", "Malam"]
    
    profile = ProfilUser(
        usia=int(form_data.get("usia") or 0),
        jenis_kelamin=form_data.get("jenis_kelamin") or "",
        berat_badan=float(form_data.get("berat_badan") or 0.0),
        tinggi_badan=float(form_data.get("tinggi_badan") or 0.0),
        lingkar_pinggang=float(form_data.get("lingkar_pinggang")) if form_data.get("lingkar_pinggang") else None,
        
        frekuensi_minum_manis=form_data.get("frekuensi_minum_manis") or "",
        porsi_nasi_per_hari=form_data.get("porsi_nasi_per_hari") or "",
        frekuensi_olahraga=form_data.get("frekuensi_olahraga") or "",
        durasi_duduk_rebahan=form_data.get("durasi_duduk_rebahan") or "",
        durasi_tidur=form_data.get("durasi_tidur") or "",
        
        riwayat_keluarga_diabetes=riwayat_keluarga,
        gejala_klasik=gejala_list,
        
        pantangan_alergi=form_data.get("pantangan_alergi") or None,
        penyakit_lain_obat=form_data.get("penyakit_lain_obat") or None,
        hasil_lab=form_data.get("hasil_lab") or None,
        
        # ── Field meal plan ──
        frekuensi_makan=frekuensi_makan,
        waktu_makan=waktu_makan_list,
        active_diet=active_diet,

        # ── Field pola waktu makan / IF ──
        pola_waktu_makan=pola_waktu_makan,
        pola_puasa=pola_puasa,
        jam_makan_mulai=jam_makan_mulai,
        jam_makan_selesai=jam_makan_selesai,
    )
    
    result = analisis_user(profile)
    
    profile_dict = _hapus_field_diet(profile.model_dump())
    profile_dict["updated_at"] = datetime.now().isoformat()
    analysis_dict = _hapus_field_diet(result.model_dump())

    if existing:
        existing.full_profile_data = json.dumps(profile_dict)
        existing.analysis_result = json.dumps(analysis_dict)
    else:
        new_profile = UserProfileDB(
            user_id=current_user.id,
            full_profile_data=json.dumps(profile_dict),
            analysis_result=json.dumps(analysis_dict)
        )
        db.add(new_profile)

    db.commit()
    return {"success": True}

@app.post("/api/regenerate-meal-plan")
async def regenerate_meal_plan(
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    """Re-generate meal plan dengan makanan berbeda, tanpa mengubah profil/analisis."""
    from app.assessment import generate_meal_plan_tervalidasi, _catatan_validasi_meal_plan
    
    profile_row = db.query(UserProfileDB).filter(UserProfileDB.user_id == current_user.id).first()
    if not profile_row:
        raise HTTPException(status_code=404, detail="Profil tidak ditemukan")
    
    analysis = json.loads(profile_row.analysis_result)
    profile_data = json.loads(profile_row.full_profile_data)
    
    # Ambil data yang diperlukan dari analisis yang sudah tersimpan.
    new_meal_plan, validasi_meal_plan = generate_meal_plan_tervalidasi(
        target_kalori=analysis["target_kalori"],
        target_karbo=analysis.get("target_karbo", 0),
        target_protein=analysis.get("target_protein", 0),
        target_lemak=analysis.get("target_lemak", 0),
        frekuensi_makan=analysis.get("frekuensi_makan", "3x"),
        waktu_makan=analysis.get("waktu_makan", ["Pagi", "Siang", "Malam"]),
        kategori_risiko=analysis["kategori_risiko"],
        pantangan=profile_data.get("pantangan_alergi"),
        pola_waktu_makan=profile_data.get("pola_waktu_makan", "normal"),
        pola_puasa=profile_data.get("pola_puasa"),
        jam_makan_mulai=profile_data.get("jam_makan_mulai"),
        jam_makan_selesai=profile_data.get("jam_makan_selesai"),
        active_diet=normalisasi_active_diet(analysis.get("active_diet") or profile_data.get("active_diet")),
    )
    
    # Update hanya meal_plan di analysis_result
    analysis["meal_plan"] = new_meal_plan
    if validasi_meal_plan:
        catatan = analysis.get("catatan_pola_makan", []) or []
        # Hapus catatan validasi lama agar tidak menumpuk saat regenerate.
        catatan = [c for c in catatan if not str(c).startswith("Validasi meal plan:")]
        catatan.append(_catatan_validasi_meal_plan(validasi_meal_plan))
        analysis["catatan_pola_makan"] = catatan
    _hapus_field_diet(analysis)
    _hapus_field_diet(profile_data)
    analysis["pola_waktu_makan"] = profile_data.get("pola_waktu_makan", "normal")
    analysis["pola_puasa"] = profile_data.get("pola_puasa")
    analysis["jam_makan_mulai"] = profile_data.get("jam_makan_mulai")
    active_diet = normalisasi_active_diet(analysis.get("active_diet") or profile_data.get("active_diet"))
    diet_info = get_diet_info(active_diet)
    analysis["active_diet"] = active_diet
    analysis["active_diet_label"] = diet_info.get("nama") if diet_info else None
    analysis["jam_makan_selesai"] = profile_data.get("jam_makan_selesai")
    profile_row.analysis_result = json.dumps(analysis)
    db.commit()
    
    return {"success": True}

# ── ROUTES: ADMIN UPLOAD ──────────────────────────────────────

def run_rag_pipeline():
    print("Memulai ekstraksi PDF dengan AI di latar belakang...")
    try:
        import sys
        subprocess.run([sys.executable, "app/testing.py"], check=True)
        print("Proses ekstraksi selesai!")
    except subprocess.CalledProcessError as e:
        print(f"Error saat menjalankan RAG pipeline: {e}")

@app.post("/admin/upload")
async def upload_document(
    request: Request, 
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...), 
    current_user: UserDB = Depends(get_current_user)
):
    if current_user.role != "admin":
        return RedirectResponse(url="/dashboard", status_code=302)
    
    upload_dir = "data/DocumentPradiabet"
    os.makedirs(upload_dir, exist_ok=True)
    file_path = os.path.join(upload_dir, file.filename)
    
    with open(file_path, "wb+") as buffer:
        shutil.copyfileobj(file.file, buffer)
        
    background_tasks.add_task(run_rag_pipeline)
    
    return templates.TemplateResponse(
        request=request,
        name="admin_dashboard.html", 
        context={
            "user": current_user, 
            "message": f"Dokumen '{file.filename}' berhasil diunggah dan sedang diproses AI!"
        }
    )

# ── API ADMIN: HAPUS DOKUMEN & VEKTOR ──
@app.delete("/admin/document/{filename}")
async def delete_document(filename: str, current_user: UserDB = Depends(get_current_user)):
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Akses ditolak")
    
    doc_name = os.path.splitext(filename)[0]
    
    file_path = os.path.join("data/DocumentPradiabet_Done", filename)
    if os.path.exists(file_path):
        os.remove(file_path)
        
    output_folder = os.path.join("data/output", doc_name)
    if os.path.exists(output_folder):
        shutil.rmtree(output_folder)
    
    try:
        filter_condition = qmodels.FilterSelector(
            filter=qmodels.Filter(
                must=[qmodels.FieldCondition(key="doc_name", match=qmodels.MatchValue(value=doc_name))]
            )
        )
        qdrant.delete(collection_name=COLLECTION_DOCS, points_selector=filter_condition)
        
        try:
            qdrant.delete(collection_name=COLLECTION_QA, points_selector=filter_condition)
        except Exception:
            pass

    except Exception as e:
        print(f"Gagal menghapus vektor dari Qdrant: {e}")
        
    return {"success": True, "message": f"Dokumen {filename}, folder output, dan data Qdrant berhasil dihapus bersih!"}


# ── API ADMIN: LIHAT DETAIL CHUNK & QA ──
@app.get("/admin/document/{filename}/details")
async def get_document_details(filename: str, current_user: UserDB = Depends(get_current_user)):
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Akses ditolak")
    
    doc_name = os.path.splitext(filename)[0]
    
    chunks = []
    qa_data = []

    try:
        docs_scroll = qdrant.scroll(
            collection_name=COLLECTION_DOCS,
            scroll_filter=qmodels.Filter(
                must=[qmodels.FieldCondition(key="doc_name", match=qmodels.MatchValue(value=doc_name))]
            ),
            limit=100,
            with_payload=True,
            with_vectors=False
        )
        for point in docs_scroll[0]:
            chunks.append(point.payload.get("content", ""))

        try:
            qa_scroll = qdrant.scroll(
                collection_name=COLLECTION_QA,
                scroll_filter=qmodels.Filter(
                    must=[qmodels.FieldCondition(key="doc_name", match=qmodels.MatchValue(value=doc_name))]
                ),
                limit=100,
                with_payload=True,
                with_vectors=False
            )
            for point in qa_scroll[0]:
                qa_data.append({
                    "q": point.payload.get("question", ""),    # ✅ FIX: "question" bukan "pertanyaan"
                    "a": point.payload.get("answer", "")       # ✅ FIX: "answer" bukan "jawaban"
                })
        except Exception as e:
            print(f"Error QA scroll: {e}")

    except Exception as e:
        print(f"Error mengambil detail Qdrant: {e}")

    return {
        "filename": filename,
        "chunks": chunks,
        "qa_data": qa_data
    }


# ── ROUTES: CHATBOT & RAG LOGIC ───────────────────────────────

@app.get("/chatbot", response_class=HTMLResponse)
async def chatbot_page(request: Request, db: Session = Depends(get_db), current_user: UserDB = Depends(get_current_user)):
    profile = db.query(UserProfileDB).filter(UserProfileDB.user_id == current_user.id).first()
    
    if not profile:
        return RedirectResponse(url="/questionnaire", status_code=302)
    
    return templates.TemplateResponse(
        request=request,
        name="chatbot.html", 
        context={
            "user": current_user,
            "has_profile": True,
            "analysis": json.loads(profile.analysis_result)
        }
    )

class ChatRequest(BaseModel):
    pertanyaan: str


def _is_profile_or_mealplan_question(text: str) -> bool:
    """
    Deteksi pertanyaan yang benar-benar tentang profil user, meal plan user,
    progress, atau Health Score. Jangan masukkan kata umum seperti kalori,
    aman, boleh, makan, gula, sodium, karena itu perlu masuk RAG makanan.
    """
    q = (text or "").lower()

    triggers = [
        "meal plan saya",
        "menu saya",
        "menu hari ini",
        "makanan saya",
        "catatan saya",
        "progress saya",
        "health score",
        "skor saya",
        "skor minggu ini",
        "minggu ini",
        "hari ini saya",
        "target kalori saya",
        "target gizi saya",
        "diet aktif",
        "diet saya",
        "jadwal makan saya",
        "makan 1",
        "makan 2",
        "makan 3",
        "sarapan saya",
        "makan siang saya",
        "makan malam saya",
        "aktivitas saya",
        "olahraga saya",
    ]

    return any(t in q for t in triggers)


@app.post("/tanya")
async def tanya_chatbot(request: ChatRequest, db: Session = Depends(get_db), current_user: UserDB = Depends(get_current_user)):
    # 1. Ambil profil user + hasil analisis terbaru untuk konteks chatbot
    profile = db.query(UserProfileDB).filter(UserProfileDB.user_id == current_user.id).first()
    profil_singkat = ""
    if profile:
        profile_data = json.loads(profile.full_profile_data)
        analysis = json.loads(profile.analysis_result)
        profil_singkat = _build_chatbot_profile_context(profile_data, analysis)
    
    extra_context = build_chatbot_extra_context(
        db=db,
        user_id=current_user.id,
        question=request.pertanyaan,
    )

    if extra_context:
        profil_singkat = (profil_singkat + "\n\n" + extra_context).strip()

    # 2. Ambil riwayat chat user
    user_history = chat_histories.get(current_user.id, [])

    # Fungsi helper: Buat search query yang sadar konteks
    def build_search_query(pertanyaan: str, history: list) -> str:
        """Gabungkan pertanyaan pendek dengan konteks percakapan sebelumnya"""
        short_question_triggers = ["porsinya", "berapa", "itu", "tadi", "yang itu", "gimana", "kenapa", "maksudnya"]
        is_short_or_vague = (
            len(pertanyaan.split()) <= 5 or 
            any(t in pertanyaan.lower() for t in short_question_triggers)
        )
        
        if is_short_or_vague and history:
            # Ambil 1-2 pesan terakhir sebagai konteks
            recent = history[-2:] if len(history) >= 2 else history[-1:]
            context_text = " ".join([h["content"] for h in recent])
            # Gabungkan konteks singkat + pertanyaan baru
            combined = f"{context_text[:200]} {pertanyaan}"
            print(f"[Search Query Diperluas]: {combined[:100]}...")
            return combined
        return pertanyaan

    search_query = build_search_query(request.pertanyaan, user_history)

    # 3. Untuk pertanyaan tentang profil/meal plan user, jangan ambil jawaban dari RAG dokumen.
    #    Ini mencegah chatbot merekomendasikan makanan lain padahal user hanya bertanya menu miliknya.
    if _is_profile_or_mealplan_question(request.pertanyaan):
        full_context = (
            "Pertanyaan ini berkaitan dengan profil, diet aktif, target gizi, meal plan, progress, atau Health Score user. "
            "Jawab berdasarkan DATA PROFIL, DIET AKTIF, MEAL PLAN USER, dan KONTEKS PROGRESS yang tersedia. "
            "Jangan mengambil rekomendasi makanan baru dari RAG kecuali user memang meminta edukasi makanan umum."
        )
        best_source = "Profil User / Meal Plan / Progress"
        best_confidence = 1.0
    else:
        query_vector = get_query_embedding(search_query)

        contexts, best_source, best_confidence = smart_retrieve(
            qdrant_client=qdrant,
            collection_docs=COLLECTION_DOCS,
            query_vector=query_vector,
            pertanyaan=request.pertanyaan,
            top_k=3,
        )

        if contexts:
            full_context = "\n\n---\n\n".join(contexts)
        else:
            full_context = "Tidak ada konteks dokumen RAG yang cocok. Jika data makanan tidak ditemukan, jawab bahwa data belum tersedia di PrediBeat."
            best_source = "Qdrant RAG"
            best_confidence = 0.0
    
    # 5. Generate jawaban dengan Groq, bukan LLM lokal/Ollama
    jawaban = generate_groq_answer(
        pertanyaan=request.pertanyaan,
        context=full_context,
        profil_singkat=profil_singkat,
        history=user_history,
    )


    # 6. Simpan riwayat chat user agar pertanyaan lanjutan seperti "itu berapa porsinya?" tetap nyambung
    user_history.append({"role": "user", "content": request.pertanyaan})
    user_history.append({"role": "assistant", "content": jawaban})
    chat_histories[current_user.id] = user_history[-10:]

    # 7. Kirim Balasan ke Frontend
    return {
        "jawaban": jawaban,
        "sumber": best_source, # Sekarang akan menampilkan sumber dari QA atau Docs
        "confidence": round(best_confidence * 100, 2)
    }