"""
assessment.py — Modul analisis nutrisi & meal plan Nutrinusa
=============================================================
Refactor: memakai master_makanan_kategori_flag_mealplan.csv
- Kategori utama: kelompok_makanan, jenis_bahan_utama, tingkat_proses, slot_meal_plan
- Flag meal plan minimal: susu, telur, seafood, kacang, babi, santan, alkohol, sayur, gorengan
- Flag karbo: sumber_karbohidrat, karbohidrat_kompleks, karbohidrat_olahan
- Kelayakan meal plan dihitung di backend, bukan dari kolom bisa_mealplan lama
"""

from app.models import ProfilUser, HasilAnalisis
from typing import Tuple, List, Dict, Optional, Any
import re
import csv
import os
import random
import json
from datetime import datetime, timedelta


# ════════════════════════════════════════════════════════════════
# KONSTANTA KOLOM MASTER DATASET
# ════════════════════════════════════════════════════════════════

# Identitas & kategori
COL_ID = "id"
COL_KODE = "kode"
COL_NAMA = "nama_makanan"
COL_KELOMPOK = "kelompok_makanan"
COL_JENIS_BAHAN = "jenis_bahan_utama"
COL_TINGKAT_PROSES = "tingkat_proses"
COL_SLOT_MEAL_PLAN = "slot_meal_plan"
COL_PORSI = "gram_porsi"
COL_GAMBAR = "gambar"
COL_SOURCE_TYPE = "sumber_data"
COL_SOURCE_FILE = "file_sumber"
COL_DATA_NOTE = "catatan_kualitas_data"
COL_CATEGORY_NOTE = "catatan_kategori"

# Nutrisi
COL_KALORI = "kalori_kkal"
COL_KARBO = "karbohidrat_g"
COL_PROTEIN = "protein_g"
COL_LEMAK = "lemak_g"
COL_LEMAK_JENUH = "lemak_jenuh_g"
COL_LEMAK_TRANS = "lemak_trans_g"
COL_LEMAK_MUFA = "lemak_tak_jenuh_tunggal_g"
COL_LEMAK_PUFA = "lemak_tak_jenuh_ganda_g"
COL_SERAT = "serat_g"
COL_GULA = "gula_g"
COL_GULA_TAMBAHAN = "gula_tambahan_g"
COL_SODIUM = "natrium_mg"
COL_AIR = "air_g"
COL_ABU = "abu_g"
COL_MAGNESIUM = "magnesium_mg"
COL_CHROMIUM = "chromium_mcg"
COL_ZINC = "zinc_mg"
COL_GI = "indeks_glikemik"
COL_EST_GI = "indeks_glikemik_estimasi"
COL_GL = "beban_glikemik"

# Flag karbo
COL_SUMBER_KARBO = "sumber_karbohidrat"
COL_KARBO_KOMPLEKS = "karbohidrat_kompleks"
COL_KARBO_OLAHAN = "karbohidrat_olahan"

# Flag keamanan/pantangan minimal
BOOL_COLS_CSV = [
    "mengandung_susu",
    "mengandung_telur",
    "mengandung_seafood",
    "mengandung_kacang",
    "mengandung_babi",
    "mengandung_santan",
    "mengandung_alkohol",
    "mengandung_sayur",
    "adalah_gorengan",
    "sumber_karbohidrat",
    "karbohidrat_kompleks",
    "karbohidrat_olahan",
]

# Threshold derived flags
THRESHOLD_GULA_TINGGI = 10.0
THRESHOLD_GULA_TAMBAHAN_TINGGI = 10.0
THRESHOLD_SODIUM_TINGGI = 400.0
THRESHOLD_GI_LOW = 55
THRESHOLD_GI_HIGH = 70
THRESHOLD_SERAT_TINGGI = 6.0

# ════════════════════════════════════════════════════════════════
# FITUR DIET AKTIF
# - Mediterania: makro tetap, pemilihan makanan diprioritaskan.
# - Rendah Karbo: target makro berubah, pemilihan makanan ikut disesuaikan.
# ════════════════════════════════════════════════════════════════

DIET_CATALOG: Dict[str, Dict[str, Any]] = {
    "mediterania": {
        "id": "mediterania",
        "nama": "Diet Mediterania",
        "slug": "mediterania",
        "gambar": "https://images.unsplash.com/photo-1498837167922-ddd27525d352?auto=format&fit=crop&w=1200&q=80",
        "ringkas": "Pola makan sehat jangka panjang yang menekankan sayur, buah utuh, ikan, kacang/legum, karbo kompleks, dan lemak sehat.",
        "untuk_apa": "Membantu membangun pola makan sehat, menjaga kesehatan jantung dan metabolik, serta mendukung kontrol gula darah lewat kualitas makanan yang lebih baik.",
        "efek_makro": "Target kalori dan makro harian tetap mengikuti hasil analisis tubuh. Sistem mengubah prioritas makanan dan komposisi komponen meal plan.",
        "komposisi": [
            "Target makro harian tetap, tetapi makanan diprioritaskan dari pola Mediterania.",
            "Komposisi komponen per slot: ±30% karbo kompleks, 25% protein sehat, 30% sayur, dan 15% buah.",
            "Karbohidrat diprioritaskan dari karbo kompleks dan rendah GI.",
            "Protein diprioritaskan dari ikan, ayam/telur secukupnya, tahu, tempe, dan legum.",
            "Sayur dan buah utuh diprioritaskan sebagai sumber serat.",
            "Lemak sehat diprioritaskan dari kacang/biji dan sumber lemak tidak jenuh bila tersedia di dataset.",
        ],
        "dianjurkan": [
            "Sayur beragam", "Buah utuh", "Ikan", "Tahu/tempe", "Kacang dan biji", "Karbo kompleks", "Makanan tinggi serat"
        ],
        "dibatasi": [
            "Gorengan", "Makanan ultra proses", "Daging olahan", "Daging merah berlebihan", "Refined carb", "Makanan/minuman tinggi gula tambahan"
        ],
        "catatan": "Diet ini tidak mengubah target makro harian; sistem memilih makanan yang lebih sesuai dengan pola Mediterania dan menyesuaikan komposisi komponen per slot makan."
    },
    "rendah_karbo": {
        "id": "rendah_karbo",
        "nama": "Diet Rendah Karbo",
        "slug": "rendah-karbo",
        "gambar": "https://images.unsplash.com/photo-1543352634-a1c51d9f1fa7?auto=format&fit=crop&w=1200&q=80",
        "ringkas": "Pola makan yang menurunkan porsi karbohidrat, terutama karbo sederhana, refined carb, dan makanan/minuman manis.",
        "untuk_apa": "Membantu mengurangi lonjakan gula darah setelah makan dan mengontrol asupan karbohidrat harian secara lebih ketat.",
        "efek_makro": "Target kalori tetap dari analisis tubuh, tetapi target makro berubah: karbo dibatasi <26% energi atau maksimal ±130 g/hari, protein 30%, dan lemak mengisi sisa kalori.",
        "komposisi": [
            "Karbohidrat: <26% energi atau maksimal ±130 g/hari, memakai nilai yang lebih rendah.",
            "Protein: ±30% dari total energi harian.",
            "Lemak: mengisi sisa kalori setelah karbo dan protein dihitung; umumnya sekitar 44% dan bisa sedikit lebih tinggi jika karbo terkena batas 130 g.",
            "Karbo tetap ada, tetapi porsinya lebih kecil dan dipilih dari sumber lebih rendah GI/lebih kompleks.",
            "Sayur non-tepung dan protein lebih diprioritaskan dalam meal plan.",
        ],
        "dianjurkan": [
            "Protein tanpa tepung", "Ikan", "Ayam/telur", "Tahu/tempe secukupnya", "Sayur non-tepung", "Karbo kompleks porsi kecil", "Buah utuh secukupnya"
        ],
        "dibatasi": [
            "Nasi putih berlebihan", "Mie", "Roti putih", "Tepung-tepungan", "Minuman manis", "Snack manis", "Makanan tinggi GI"
        ],
        "catatan": "Ini bukan keto ekstrem. Sistem membatasi karbo sesuai definisi low-carbohydrate, menetapkan protein 30%, dan menghitung lemak sebagai sisa kalori."
    },
}

_DIET_ALIAS = {
    "mediterania": "mediterania",
    "mediterranean": "mediterania",
    "diet mediterania": "mediterania",
    "mediterranean diet": "mediterania",
    "rendah_karbo": "rendah_karbo",
    "rendah karbo": "rendah_karbo",
    "diet rendah karbo": "rendah_karbo",
    "low carb": "rendah_karbo",
    "low-carb": "rendah_karbo",
    "low_carbohydrate": "rendah_karbo",
}


def normalisasi_active_diet(value: Optional[str]) -> Optional[str]:
    """Normalisasi pilihan diet aktif dari URL/form/database."""
    text = (value or "").strip().lower().replace("-", "_")
    if not text or text in ["none", "null", "-", "tidak ada"]:
        return None
    text_spasi = text.replace("_", " ")
    return _DIET_ALIAS.get(text) or _DIET_ALIAS.get(text_spasi)


def get_diet_info(active_diet: Optional[str]) -> Optional[Dict[str, Any]]:
    diet_id = normalisasi_active_diet(active_diet)
    if not diet_id:
        return None
    return DIET_CATALOG.get(diet_id)


def get_all_diets() -> List[Dict[str, Any]]:
    return list(DIET_CATALOG.values())


def _diet_label(active_diet: Optional[str]) -> Optional[str]:
    info = get_diet_info(active_diet)
    return info["nama"] if info else None


# ════════════════════════════════════════════════════════════════
# 1. HITUNG BMI (Standar Asia-Pasifik / Kemenkes RI)
# ════════════════════════════════════════════════════════════════

def hitung_bmi(berat: float, tinggi: float) -> Tuple[float, str]:
    if tinggi <= 0 or berat <= 0:
        return 0.0, "Data tidak valid"

    tinggi_m = tinggi / 100
    bmi = berat / (tinggi_m ** 2)

    if bmi < 18.5:
        kategori = "Underweight"
    elif bmi < 23.0:
        kategori = "Normal"
    elif bmi < 25.0:
        kategori = "Overweight"
    else:
        kategori = "Obesitas"

    return round(bmi, 1), kategori


# ════════════════════════════════════════════════════════════════
# 2. HITUNG TDEE
# ════════════════════════════════════════════════════════════════

def hitung_tdee(profil: ProfilUser) -> float:
    bb  = profil.berat_badan
    tb  = profil.tinggi_badan
    age = profil.usia
    olahraga = profil.frekuensi_olahraga.lower()

    if "tidak pernah" in olahraga or "jarang" in olahraga:
        am = 1.2
    elif "1-2" in olahraga:
        am = 1.375
    elif "3-4" in olahraga:
        am = 1.55
    else:
        am = 1.725

    if profil.jenis_kelamin.lower() == "laki-laki":
        bmr = (10 * bb) + (6.25 * tb) - (5 * age) + 5
    else:
        bmr = (10 * bb) + (6.25 * tb) - (5 * age) - 161

    return round(bmr * am, 0)


# ════════════════════════════════════════════════════════════════
# 3. HITUNG SKOR RISIKO PREDIABETES
# ════════════════════════════════════════════════════════════════

def hitung_skor_risiko(profil: ProfilUser, bmi: float) -> Tuple[int, str]:
    skor = 0
    hasil_lab = str(profil.hasil_lab).lower() if profil.hasil_lab else ""

    if "diabetes" in hasil_lab:
        return 100, "Diabetes Terkonfirmasi"
    if "prediabetes" in hasil_lab:
        return 75, "Prediabetes Terkonfirmasi"

    match_gdp   = re.search(r'(?:gula|gdp).*?(\d{3,})', hasil_lab)
    match_hba1c = re.search(r'hba1c.*?([\d\.]+)', hasil_lab)

    if match_gdp:
        gdp_val = float(match_gdp.group(1))
        if gdp_val >= 126:
            return 100, "Diabetes Terkonfirmasi"
        elif 100 <= gdp_val <= 125:
            return 75, "Prediabetes Terkonfirmasi"

    if match_hba1c:
        hba1c_val = float(match_hba1c.group(1))
        if hba1c_val >= 6.5:
            return 100, "Diabetes Terkonfirmasi"
        elif 5.7 <= hba1c_val < 6.5:
            return 75, "Prediabetes Terkonfirmasi"

    if profil.usia >= 55:     skor += 8
    elif profil.usia >= 45:   skor += 5
    elif profil.usia >= 35:   skor += 3

    if profil.riwayat_keluarga_diabetes:
        skor += 20

    if bmi >= 25.0:   skor += 15
    elif bmi >= 23.0: skor += 10

    if profil.lingkar_pinggang:
        lp = profil.lingkar_pinggang
        if profil.jenis_kelamin.lower() == "laki-laki" and lp >= 90:
            skor += 15
        elif profil.jenis_kelamin.lower() == "perempuan" and lp >= 80:
            skor += 15

    manis = profil.frekuensi_minum_manis.lower()
    if "setiap hari" in manis or "sering" in manis: skor += 15
    elif "3-5" in manis: skor += 10

    nasi = profil.porsi_nasi_per_hari.lower()
    if "lebih dari 2" in nasi: skor += 10
    elif "2 porsi" in nasi:    skor += 5

    rebahan = profil.durasi_duduk_rebahan.lower()
    if "lebih dari 8 jam" in rebahan or "lebih dari 6 jam" in rebahan:
        skor += 10

    olahraga = profil.frekuensi_olahraga.lower()
    if "3-4" in olahraga or "5 hari" in olahraga:
        skor -= 5
    elif "tidak pernah" in olahraga:
        skor += 5

    tidur = profil.durasi_tidur.lower()
    if "kurang dari 5" in tidur or "kurang dari 6" in tidur:
        skor += 5

    gejala_list = " ".join(profil.gejala_klasik).lower()
    if "haus"      in gejala_list: skor += 5
    if "kencing"   in gejala_list: skor += 5
    if "lapar"     in gejala_list: skor += 5
    if "luka"      in gejala_list: skor += 8
    if "kesemutan" in gejala_list: skor += 5

    penyakit = str(profil.penyakit_lain_obat).lower() if profil.penyakit_lain_obat else ""
    if any(p in penyakit for p in ["hipertensi", "tensi", "pcos", "kolesterol"]):
        skor += 10
    if "gestasional" in penyakit or "gula darah" in penyakit:
        skor += 15

    if skor >= 50:
        return skor, "Tinggi (Sangat Berisiko Prediabetes)"
    elif skor >= 25:
        return skor, "Sedang (Waspada)"
    else:
        return max(0, skor), "Rendah (Aman)"


# ════════════════════════════════════════════════════════════════
# 4. TARGET KALORI & MAKRONUTRIEN
# ════════════════════════════════════════════════════════════════

def hitung_target_kalori_makro(
    tdee: float,
    kategori_risiko: str,
    bmi: float,
    jenis_kelamin: str,
) -> Tuple[float, float, float, float]:

    if bmi >= 25.0:
        target_kalori = tdee - 500
    elif bmi >= 23.0:
        target_kalori = tdee - 300
    else:
        target_kalori = tdee

    # Batas aman minimal
    if jenis_kelamin.lower() == "laki-laki":
        target_kalori = max(target_kalori, 1500.0)
    else:
        target_kalori = max(target_kalori, 1200.0)

    if "Tinggi" in kategori_risiko or "Prediabetes" in kategori_risiko or "Diabetes" in kategori_risiko:
        if bmi >= 25.0:
            karbo_pct, protein_pct, lemak_pct = 0.45, 0.25, 0.30
        else:
            karbo_pct, protein_pct, lemak_pct = 0.45, 0.20, 0.35
    elif "Sedang" in kategori_risiko:
        if bmi >= 25.0:
            karbo_pct, protein_pct, lemak_pct = 0.50, 0.20, 0.30
        else:
            karbo_pct, protein_pct, lemak_pct = 0.50, 0.15, 0.35
    else:
        karbo_pct, protein_pct, lemak_pct = 0.55, 0.15, 0.30

    karbo_gram   = round((target_kalori * karbo_pct) / 4, 0)
    protein_gram = round((target_kalori * protein_pct) / 4, 0)
    lemak_gram   = round((target_kalori * lemak_pct) / 9, 0)

    return round(target_kalori, 0), karbo_gram, protein_gram, lemak_gram


def terapkan_diet_pada_target_makro(
    target_kalori: float,
    kategori_risiko: str,
    karbo_default: float,
    protein_default: float,
    lemak_default: float,
    active_diet: Optional[str] = None,
) -> Tuple[float, float, float, str]:
    """
    Ubah target makro hanya jika diet aktif memang perlu mengubah rasio makro.

    - Mediterania: target makro harian tetap, pemilihan makanan dan komposisi komponen meal plan berubah.
    - Rendah Karbo: karbo dibatasi <26% energi atau maksimal ±130 g/hari, protein 30%, lemak mengisi sisa kalori.
    """
    diet = normalisasi_active_diet(active_diet)

    if diet == "rendah_karbo":
        karbo_pct = 0.26
        max_karbo_g = 130.0
        protein_pct = 0.30

        # Karbo dibatasi:
        # 1. maksimal <26% dari total kalori
        # 2. maksimal ±130 g/hari
        karbo_dari_persen = (target_kalori * karbo_pct) / 4
        karbo_gram = round(min(karbo_dari_persen, max_karbo_g), 0)

        # Protein ditetapkan 30% dari total kalori
        protein_gram = round((target_kalori * protein_pct) / 4, 0)

        # Lemak mengisi sisa kalori
        sisa_kalori = target_kalori - (karbo_gram * 4) - (protein_gram * 4)
        lemak_gram = round(max(sisa_kalori, 0) / 9, 0)

        # Persentase aktual untuk catatan
        karbo_pct_aktual = round((karbo_gram * 4 / target_kalori) * 100, 1) if target_kalori else 0
        protein_pct_aktual = round((protein_gram * 4 / target_kalori) * 100, 1) if target_kalori else 0
        lemak_pct_aktual = round((lemak_gram * 9 / target_kalori) * 100, 1) if target_kalori else 0

        catatan = (
            "Diet Rendah Karbo aktif: target karbo dibatasi <26% energi "
            "atau maksimal ±130 g/hari. Protein ditetapkan sekitar 30% energi, "
            "dan lemak mengisi sisa kalori. "
            f"Komposisi aktual: karbo ±{karbo_pct_aktual}%, "
            f"protein ±{protein_pct_aktual}%, lemak ±{lemak_pct_aktual}%."
        )

        return karbo_gram, protein_gram, lemak_gram, catatan

    if diet == "mediterania":
        return karbo_default, protein_default, lemak_default, (
            "Diet Mediterania aktif: target makro tetap, tetapi sistem memprioritaskan sayur, buah utuh, ikan, legum, kacang/biji, karbo kompleks, dan makanan tinggi serat."
        )

    return karbo_default, protein_default, lemak_default, ""


# ════════════════════════════════════════════════════════════════
# 5. JADWAL & POLA WAKTU MAKAN (Intermittent Fasting)
# ════════════════════════════════════════════════════════════════


def _jam_to_datetime(jam: Optional[str], default: str = "10:00") -> datetime:
    jam = (jam or default).strip()
    try:
        return datetime.strptime(jam, "%H:%M")
    except ValueError:
        return datetime.strptime(default, "%H:%M")


def _durasi_jendela_makan(pola_puasa: Optional[str]) -> int:
    if not pola_puasa:
        return 12
    match = re.search(r"(\d{1,2})\s*:\s*(\d{1,2})", pola_puasa)
    if match:
        return int(match.group(2))
    return 12


_SLOT_DEFAULT_TIMES = {
    "pagi":        "07:00",
    "sarapan":     "07:00",
    "siang":       "12:00",
    "makan siang": "12:00",
    "sore":        "17:00",
    "camilan":     "17:00",
    "snack":       "17:00",
    "malam":       "19:00",
    "makan malam": "19:00",
    "makan 1":     None,
    "makan 2":     None,
    "makan 3":     None,
}


def _slot_default_datetime(
    label: str, start: datetime, end: datetime, index: int, total_slots: int
) -> datetime:
    low = (label or "").strip().lower()
    default_time = _SLOT_DEFAULT_TIMES.get(low)

    if default_time:
        candidate = _jam_to_datetime(default_time, default_time)
        if end > start + timedelta(hours=20) and candidate < start:
            candidate = candidate + timedelta(days=1)
        if start <= candidate <= end:
            return candidate

    total_minutes = max(60, int((end - start).total_seconds() // 60))
    if total_slots <= 1:
        menit = total_minutes // 2
    else:
        menit = round(index * total_minutes / (total_slots - 1))
    return start + timedelta(minutes=menit)


def _susun_jadwal_if(
    waktu_makan: List[str],
    pola_waktu_makan: str = "normal",
    pola_puasa: Optional[str] = None,
    jam_makan_mulai: Optional[str] = None,
    jam_makan_selesai: Optional[str] = None,
) -> Tuple[List[Dict[str, str]], List[str], Optional[str], Optional[str]]:
    catatan = []
    pola = (pola_waktu_makan or "normal").lower()
    waktu_makan = waktu_makan or []

    if pola != "intermittent_fasting":
        jadwal = []
        for w in waktu_makan:
            jam = _SLOT_DEFAULT_TIMES.get((w or "").strip().lower())
            jadwal.append({"label": w, "jam": jam})
        return jadwal, catatan, jam_makan_mulai, jam_makan_selesai

    durasi_jam = _durasi_jendela_makan(pola_puasa)
    start = _jam_to_datetime(jam_makan_mulai, "10:00")
    end   = start + timedelta(hours=durasi_jam)
    jam_makan_mulai  = start.strftime("%H:%M")
    jam_makan_selesai = end.strftime("%H:%M")

    total_slots = max(1, len(waktu_makan))
    jadwal = []
    used_jam: set = set()

    for i, label in enumerate(waktu_makan):
        jam_dt = _slot_default_datetime(label, start, end, i, total_slots)
        jam = jam_dt.strftime("%H:%M")
        while jam in used_jam:
            jam_dt = jam_dt + timedelta(minutes=30)
            if jam_dt > end:
                break
            jam = jam_dt.strftime("%H:%M")
        used_jam.add(jam)
        jadwal.append({"label": label, "jam": jam})

    catatan.append(
        f"Intermittent fasting {pola_puasa or ''} aktif; "
        f"menu hanya dijadwalkan di jendela makan {jam_makan_mulai}–{jam_makan_selesai}."
    )
    return jadwal, catatan, jam_makan_mulai, jam_makan_selesai


def _distribusi_kalori_slots(waktu_makan: List[str]) -> List[float]:
    n = max(1, len(waktu_makan))
    DISTRIBUSI = {
        1: [1.0],
        2: [0.45, 0.55],
        3: [0.30, 0.40, 0.30],
    }
    return DISTRIBUSI.get(n, [round(1.0 / n, 3)] * n)


# Toleransi validasi hasil akhir meal plan.
# Kalori biasanya bisa ditargetkan lebih ketat, sedangkan makro dari dataset makanan nyata
# lebih sulit presisi karena komposisi tiap makanan berbeda-beda.
TOLERANSI_KALORI_MEALPLAN = 10.0   # persen
TOLERANSI_MAKRO_MEALPLAN  = 15.0   # persen
MAX_MEALPLAN_ATTEMPTS     = 40     # jumlah percobaan generate ulang


def _persen_error(actual: float, target: float) -> float:
    """Hitung selisih persen antara nilai aktual dan target."""
    if target is None or target <= 0:
        return 0.0
    return abs(actual - target) / target * 100


def _hitung_total_nutrisi_meal_plan(meal_plan: List[Dict]) -> Dict[str, float]:
    """Jumlahkan total kalori, makro, gula tambahan, dan sodium dari seluruh item meal plan."""
    total = {
        "kalori": 0.0,
        "karbo": 0.0,
        "protein": 0.0,
        "lemak": 0.0,
        "gula_total": 0.0,
        "gula_tambahan": 0.0,
        "sodium": 0.0,
    }

    for meal in meal_plan or []:
        for item in meal.get("items", []):
            total["kalori"]        += float(item.get("kalori", 0) or 0)
            total["karbo"]         += float(item.get("karbo", 0) or 0)
            total["protein"]       += float(item.get("protein", 0) or 0)
            total["lemak"]         += float(item.get("lemak", 0) or 0)
            total["gula_total"]    += float(item.get("gula_g", 0) or 0)
            total["gula_tambahan"] += float(item.get("gula_tambahan_g", 0) or 0)
            total["sodium"]        += float(item.get("sodium", 0) or 0)

    return {k: round(v, 1) for k, v in total.items()}


def _batas_gula_sodium_harian(kategori_risiko: str) -> Dict[str, float | str | bool]:
    """
    Tentukan batas gula tambahan dan sodium harian berdasarkan kategori risiko.

    Catatan:
    - Normal/rendah: gula tambahan <= 50 g/hari, sodium <= 2.000 mg/hari.
    - Sedang/prediabet: gula tambahan < 25 g/hari, sodium target 2.000 mg dan batas atas 2.300 mg/hari.
    - Tinggi/diabetes: gula tambahan dihindari atau < 25 g/hari, sodium target 2.000 mg dan batas atas 2.300 mg/hari.
    """
    kategori = (kategori_risiko or "").lower()

    if "tinggi" in kategori or "diabetes" in kategori or "terkonfirmasi" in kategori:
        return {
            "kategori_batas": "Diabetes / Risiko Tinggi",
            "batas_gula_tambahan_g": 25.0,
            "gula_harus_kurang_dari": True,
            "target_sodium_mg": 2000.0,
            "batas_sodium_mg": 2300.0,
            "catatan_gula": "Dihindari; bila ada harus < 25 g/hari",
        }

    if "sedang" in kategori or "prediabetes" in kategori:
        return {
            "kategori_batas": "Prediabet / Risiko Sedang",
            "batas_gula_tambahan_g": 25.0,
            "gula_harus_kurang_dari": True,
            "target_sodium_mg": 2000.0,
            "batas_sodium_mg": 2300.0,
            "catatan_gula": "< 25 g/hari",
        }

    return {
        "kategori_batas": "Normal / Risiko Rendah",
        "batas_gula_tambahan_g": 50.0,
        "gula_harus_kurang_dari": False,
        "target_sodium_mg": 2000.0,
        "batas_sodium_mg": 2000.0,
        "catatan_gula": "<= 50 g/hari",
    }


def _evaluasi_kesesuaian_meal_plan(
    meal_plan: List[Dict],
    target_kalori: float,
    target_karbo: float,
    target_protein: float,
    target_lemak: float,
    kategori_risiko: str,
    toleransi_kalori: float = TOLERANSI_KALORI_MEALPLAN,
    toleransi_makro: float = TOLERANSI_MAKRO_MEALPLAN,
) -> Dict[str, float | bool | str]:
    """
    Evaluasi apakah total meal plan sudah mendekati target kalori dan makro.
    Lolos jika:
    - kalori <= toleransi_kalori
    - karbo/protein/lemak <= toleransi_makro
    - gula tambahan sesuai batas kategori risiko
    - sodium tidak melewati batas atas harian
    """
    total = _hitung_total_nutrisi_meal_plan(meal_plan)
    batas = _batas_gula_sodium_harian(kategori_risiko)

    err_kalori  = _persen_error(total["kalori"], target_kalori)
    err_karbo   = _persen_error(total["karbo"], target_karbo)
    err_protein = _persen_error(total["protein"], target_protein)
    err_lemak   = _persen_error(total["lemak"], target_lemak)

    batas_gula = float(batas["batas_gula_tambahan_g"])
    batas_sodium = float(batas["batas_sodium_mg"])

    if batas.get("gula_harus_kurang_dari"):
        lolos_gula_tambahan = total["gula_tambahan"] < batas_gula
    else:
        lolos_gula_tambahan = total["gula_tambahan"] <= batas_gula

    lolos_sodium = total["sodium"] <= batas_sodium

    err_gula_tambahan = 0.0 if lolos_gula_tambahan else _persen_error(total["gula_tambahan"], batas_gula)
    err_sodium = 0.0 if lolos_sodium else _persen_error(total["sodium"], batas_sodium)

    lolos = (
        err_kalori <= toleransi_kalori and
        err_karbo <= toleransi_makro and
        err_protein <= toleransi_makro and
        err_lemak <= toleransi_makro and
        lolos_gula_tambahan and
        lolos_sodium
    )

    # Skor makin kecil makin baik. Kalori dan gula tambahan diberi bobot lebih besar
    # karena keduanya menjadi target utama meal plan pradiabetes/diabetes.
    skor_error = (
        err_kalori * 2.0 +
        err_karbo +
        err_protein +
        err_lemak +
        err_gula_tambahan * 2.0 +
        err_sodium
    )

    return {
        "lolos_toleransi": lolos,
        "skor_error": round(skor_error, 2),

        "target_kalori": round(target_kalori, 1),
        "target_karbo": round(target_karbo, 1),
        "target_protein": round(target_protein, 1),
        "target_lemak": round(target_lemak, 1),

        "total_kalori": total["kalori"],
        "total_karbo": total["karbo"],
        "total_protein": total["protein"],
        "total_lemak": total["lemak"],
        "total_gula_g": total["gula_total"],
        "total_gula_tambahan_g": total["gula_tambahan"],
        "total_sodium_mg": total["sodium"],

        "kategori_batas_gula_sodium": batas["kategori_batas"],
        "batas_gula_tambahan_g": batas["batas_gula_tambahan_g"],
        "catatan_batas_gula": batas["catatan_gula"],
        "target_sodium_mg": batas["target_sodium_mg"],
        "batas_sodium_mg": batas["batas_sodium_mg"],
        "lolos_gula_tambahan": lolos_gula_tambahan,
        "lolos_sodium": lolos_sodium,

        "selisih_kalori_pct": round(err_kalori, 1),
        "selisih_karbo_pct": round(err_karbo, 1),
        "selisih_protein_pct": round(err_protein, 1),
        "selisih_lemak_pct": round(err_lemak, 1),
        "selisih_gula_tambahan_pct": round(err_gula_tambahan, 1),
        "selisih_sodium_pct": round(err_sodium, 1),

        "toleransi_kalori_pct": toleransi_kalori,
        "toleransi_makro_pct": toleransi_makro,
    }


def _catatan_validasi_meal_plan(validasi: Dict) -> str:
    """Buat teks ringkas untuk ditampilkan ke frontend/catatan pola makan."""
    status = "sesuai toleransi" if validasi.get("lolos_toleransi") else "belum sepenuhnya sesuai toleransi"
    return (
        f"Validasi meal plan: {status}. "
        f"Total {validasi.get('total_kalori')} kkal vs target {validasi.get('target_kalori')} kkal "
        f"(selisih {validasi.get('selisih_kalori_pct')}%). "
        f"Makro aktual: karbo {validasi.get('total_karbo')}g "
        f"(selisih {validasi.get('selisih_karbo_pct')}%), "
        f"protein {validasi.get('total_protein')}g "
        f"(selisih {validasi.get('selisih_protein_pct')}%), "
        f"lemak {validasi.get('total_lemak')}g "
        f"(selisih {validasi.get('selisih_lemak_pct')}%). "
        f"Gula tambahan {validasi.get('total_gula_tambahan_g')}g "
        f"(batas {validasi.get('catatan_batas_gula')}); "
        f"sodium {validasi.get('total_sodium_mg')}mg "
        f"(batas atas {validasi.get('batas_sodium_mg')}mg)."
    )


def generate_meal_plan_tervalidasi(
    target_kalori: float,
    target_karbo: float,
    target_protein: float,
    target_lemak: float,
    frekuensi_makan: str,
    waktu_makan: List[str],
    kategori_risiko: str,
    pantangan: Optional[str],
    pola_waktu_makan: str = "normal",
    pola_puasa: Optional[str] = None,
    jam_makan_mulai: Optional[str] = None,
    jam_makan_selesai: Optional[str] = None,
    max_attempts: int = MAX_MEALPLAN_ATTEMPTS,
    active_diet: Optional[str] = None,
) -> Tuple[List[Dict], Dict]:
    """
    Generate meal plan beberapa kali, lalu ambil yang paling dekat dengan target.
    Jika ada yang lolos toleransi, langsung dipakai.
    Jika tidak ada, tetap ambil hasil dengan skor error paling kecil.
    """
    best_plan: List[Dict] = []
    best_validasi: Dict = {}

    for attempt in range(1, max_attempts + 1):
        meal_plan = generate_meal_plan(
            target_kalori=target_kalori,
            frekuensi_makan=frekuensi_makan,
            waktu_makan=waktu_makan,
            kategori_risiko=kategori_risiko,
            pantangan=pantangan,
            pola_waktu_makan=pola_waktu_makan,
            pola_puasa=pola_puasa,
            jam_makan_mulai=jam_makan_mulai,
            jam_makan_selesai=jam_makan_selesai,
            active_diet=active_diet,
        )

        validasi = _evaluasi_kesesuaian_meal_plan(
            meal_plan=meal_plan,
            target_kalori=target_kalori,
            target_karbo=target_karbo,
            target_protein=target_protein,
            target_lemak=target_lemak,
            kategori_risiko=kategori_risiko,
        )
        validasi["attempt"] = attempt

        if not best_validasi or validasi["skor_error"] < best_validasi.get("skor_error", 999999):
            best_plan = meal_plan
            best_validasi = validasi

        if validasi.get("lolos_toleransi"):
            break

    return best_plan, best_validasi


# ════════════════════════════════════════════════════════════════
# 6. LOAD DATASET — MEMBACA SEMUA METADATA DARI CSV
# ════════════════════════════════════════════════════════════════

_food_cache: Optional[List[Dict]] = None


def _safe_float(val, default: float = 0.0) -> float:
    """Konversi angka CSV Indonesia: 4.166,7 -> 4166.7; 12,5 -> 12.5."""
    if val is None:
        return default
    text = str(val).strip()
    if text == "" or text.lower() in ["nan", "none", "null", "-", "g", "#div/0!", "unknown", "tidak diketahui"]:
        return default
    text = text.replace(" ", "").replace("g", "")
    try:
        if "," in text:
            text = text.replace(".", "").replace(",", ".")
        return float(text)
    except Exception:
        return default


def _safe_bool(val, default: bool = False) -> bool:
    if isinstance(val, bool):
        return val
    if val is None:
        return default
    text = str(val).strip().lower()
    if text in ["true", "1", "yes", "ya", "y", "benar"]:
        return True
    if text in ["false", "0", "no", "tidak", "n", "salah", "", "nan", "none", "null"]:
        return False
    return default


def _clean_text(val, default: str = "") -> str:
    if val is None:
        return default
    text = str(val).strip()
    if text.lower() in ["nan", "none", "null"]:
        return default
    return text


def _has_numeric_value(val) -> bool:
    """Cek apakah kolom CSV benar-benar diisi angka, bukan kosong/unknown."""
    if val is None:
        return False
    text = str(val).strip().lower()
    return text not in ["", "nan", "none", "null", "-", "unknown", "tidak diketahui"]




def _norm(value: Any) -> str:
    return _clean_text(value).strip()


def _norm_lower(value: Any) -> str:
    return _norm(value).lower()


def _slot_is_no_meal(slot: str) -> bool:
    slot_l = _norm_lower(slot)
    return slot_l in {"", "no meal", "tidak", "no_meal", "none", "-"}


def _food_dataset_paths() -> List[str]:
    base_dir = os.path.dirname(__file__)
    return [
        os.path.join(base_dir, "..", "data", "master_makanan_kategori_flag_mealplan.csv"),
        os.path.join("data", "master_makanan_kategori_flag_mealplan.csv"),
        "master_makanan_kategori_flag_mealplan.csv",
        "/mnt/data/master_makanan_kategori_flag_mealplan.csv",
        # fallback lama supaya aplikasi tidak langsung mati kalau file baru belum dipindah
        os.path.join(base_dir, "..", "data", "FoodDataset_APP_READY_809_with_gambar_bing.csv"),
    ]


def _find_food_dataset_path() -> Optional[str]:
    for path in _food_dataset_paths():
        if os.path.exists(path):
            return path
    return None


def _derive_glycemic_risk(gi_value: float) -> str:
    if gi_value >= THRESHOLD_GI_HIGH:
        return "high"
    if 0 < gi_value <= THRESHOLD_GI_LOW:
        return "low"
    if gi_value > 0:
        return "moderate"
    return ""


def _derive_flags(food: Dict) -> None:
    """Buat alias flag lama dari master dataset baru agar fungsi diet/meal plan tetap jalan."""
    kelompok = _norm_lower(food.get("kelompok_makanan"))
    jenis = _norm_lower(food.get("jenis_bahan_utama"))
    proses = _norm_lower(food.get("tingkat_proses"))
    slot = _norm_lower(food.get("slot_meal_plan"))

    gi_val = float(food.get("glikemik_indeks", 0.0) or 0.0)
    food["glikemik_risk"] = food.get("glikemik_risk") or _derive_glycemic_risk(gi_val)

    food["gula_tambahan_g"] = max(0.0, food.get("gula_tambahan_g", 0.0) or 0.0)
    food["is_high_sugar"] = food.get("gula_g", 0.0) >= THRESHOLD_GULA_TINGGI
    food["is_high_added_sugar"] = food.get("gula_tambahan_g", 0.0) >= THRESHOLD_GULA_TAMBAHAN_TINGGI
    food["is_high_sodium"] = food.get("sodium", 0.0) >= THRESHOLD_SODIUM_TINGGI
    food["is_high_fiber"] = food.get("serat_g", 0.0) >= THRESHOLD_SERAT_TINGGI

    if food["glikemik_risk"] == "high":
        food["is_high_glycemic"] = True
        food["is_low_glycemic"] = False
    elif food["glikemik_risk"] == "low":
        food["is_high_glycemic"] = False
        food["is_low_glycemic"] = True
    elif gi_val > 0:
        food["is_high_glycemic"] = gi_val >= THRESHOLD_GI_HIGH
        food["is_low_glycemic"] = gi_val <= THRESHOLD_GI_LOW
    else:
        food["is_high_glycemic"] = False
        food["is_low_glycemic"] = False

    # Alias kompatibilitas untuk fungsi lama
    food["is_beverage"] = kelompok == "minuman"
    food["is_fried"] = bool(food.get("adalah_gorengan"))
    food["is_ultra_processed"] = proses == "ultraproses"
    food["ultra_prosess"] = food["is_ultra_processed"]
    food["is_santan"] = bool(food.get("mengandung_santan"))
    food["is_processed_meat"] = kelompok == "lauk hewani" and proses == "ultraproses"
    food["is_fish"] = jenis in {"seafood", "ikan"} or bool(food.get("mengandung_seafood"))
    food["is_seafood"] = bool(food.get("mengandung_seafood")) or jenis == "seafood"
    food["is_legume"] = jenis in {"kedelai", "kacang-kacangan", "kacang", "legum"} or kelompok == "lauk nabati"
    food["is_nut_seed"] = kelompok == "biji/kacang" or bool(food.get("mengandung_kacang"))
    food["is_peanut"] = bool(food.get("mengandung_kacang")) or food["is_nut_seed"] or "kacang" in jenis
    food["is_dairy"] = kelompok == "dairy" or bool(food.get("mengandung_susu"))
    food["is_fruit"] = kelompok == "buah" or slot == "buah"
    food["is_vegetable"] = kelompok == "sayur" or slot == "sayur"
    food["is_complex_carb"] = bool(food.get("karbohidrat_kompleks"))
    food["is_refined_carb"] = bool(food.get("karbohidrat_olahan"))
    food["is_carb"] = bool(food.get("sumber_karbohidrat"))
    food["is_poultry_egg"] = jenis in {"daging putih", "telur", "ayam", "unggas"} or bool(food.get("mengandung_telur"))
    food["is_red_meat"] = jenis == "daging merah"
    food["bumbu"] = kelompok == "bumbu & pelengkap"
    food["is_raw"] = proses == "mentah"
    food["is_raw_edible"] = proses == "mentah bisa dimakan"
    food["is_sweetened"] = bool(food.get("is_high_added_sugar")) or (
        food.get("is_high_sugar") and not food.get("is_fruit")
    )
    food["is_low_calorie"] = food.get("kalori", 0.0) < 100
    food["is_low_fat"] = food.get("lemak", 0.0) < 5
    food["is_low_carb"] = food.get("karbo", 0.0) < 15

    # Kelayakan awal meal plan dihitung, bukan kolom manual.
    excluded_groups = {"snack", "dessert", "minuman", "bumbu & pelengkap", "suplemen nutrisi"}
    food["bisa_mealplan"] = (
        not _slot_is_no_meal(food.get("slot_meal_plan"))
        and proses not in {"mentah", "ultraproses"}
        and kelompok not in excluded_groups
        and not food.get("mengandung_babi")
        and not food.get("mengandung_alkohol")
    )
    food["is_usable_mealplan"] = food["bisa_mealplan"]


def _load_food_data() -> List[Dict]:
    """Load master_makanan_kategori_flag_mealplan.csv dan ubah ke format internal meal plan."""
    global _food_cache
    if _food_cache is not None:
        return _food_cache

    csv_path = _find_food_dataset_path()
    if not csv_path:
        print("⚠️ File dataset master_makanan_kategori_flag_mealplan.csv tidak ditemukan di folder data.")
        _food_cache = []
        return _food_cache

    foods: List[Dict] = []
    with open(csv_path, newline="", encoding="utf-8-sig", errors="replace") as f:
        sample = f.read(2048)
        f.seek(0)
        delimiter = ";" if sample.count(";") >= sample.count(",") else ","
        reader = csv.DictReader(f, delimiter=delimiter)
        for row in reader:
            nama = _clean_text(row.get(COL_NAMA) or row.get("nama"))
            if not nama:
                continue

            kalori = _safe_float(row.get(COL_KALORI) or row.get("kalori"), 0.0)
            if kalori <= 0:
                continue

            kelompok = _clean_text(row.get(COL_KELOMPOK), "Lainnya")
            jenis = _clean_text(row.get(COL_JENIS_BAHAN))
            proses = _clean_text(row.get(COL_TINGKAT_PROSES))
            slot = _clean_text(row.get(COL_SLOT_MEAL_PLAN), "No Meal")
            gram_porsi = _safe_float(row.get(COL_PORSI), 100.0) or 100.0

            karbo = _safe_float(row.get(COL_KARBO))
            serat = _safe_float(row.get(COL_SERAT))
            gi_asli = _safe_float(row.get(COL_GI), 0.0)
            gi_estimasi = _safe_float(row.get(COL_EST_GI), 0.0)
            gi_final = gi_asli if gi_asli > 0 else gi_estimasi

            food: Dict = {
                "id": _clean_text(row.get(COL_ID)),
                "source_type": _clean_text(row.get(COL_SOURCE_TYPE)),
                "source_file": _clean_text(row.get(COL_SOURCE_FILE)),
                "kode": _clean_text(row.get(COL_KODE)),
                "nama": nama,
                "kelompok_makanan": kelompok,
                "jenis_bahan_utama": jenis,
                "tingkat_proses": proses,
                "slot_meal_plan": slot,
                # alias lama untuk tampilan/ranking yang belum diganti total
                "kategori": kelompok,
                "kategori_detail": " | ".join([x for x in [jenis, proses, slot] if x]),
                "porsi": f"{gram_porsi:g} g",
                "gram_porsi": gram_porsi,
                "siap_konsumsi": "",
                "cara_olah": proses,
                "gambar": _clean_text(row.get(COL_GAMBAR)) or None,

                "kalori": kalori,
                "karbo": karbo,
                "protein": _safe_float(row.get(COL_PROTEIN)),
                "lemak": _safe_float(row.get(COL_LEMAK)),
                "lemak_total": _safe_float(row.get(COL_LEMAK)),
                "lemak_jenuh": _safe_float(row.get(COL_LEMAK_JENUH)),
                "lemak_trans": _safe_float(row.get(COL_LEMAK_TRANS)),
                "lemak_tak_jenuh_tunggal": _safe_float(row.get(COL_LEMAK_MUFA)),
                "lemak_tak_jenuh_ganda": _safe_float(row.get(COL_LEMAK_PUFA)),
                "serat_g": serat,
                "gula_g": _safe_float(row.get(COL_GULA)),
                "gula_tambahan_g": _safe_float(row.get(COL_GULA_TAMBAHAN)),
                "air_g": _safe_float(row.get(COL_AIR)),
                "abu_g": _safe_float(row.get(COL_ABU)),
                "magnesium": _safe_float(row.get(COL_MAGNESIUM)),
                "chromium": _safe_float(row.get(COL_CHROMIUM)),
                "zinc_mg": _safe_float(row.get(COL_ZINC)),
                "glikemik_indeks": gi_final,
                "glikemik_indeks_asli": gi_asli,
                "estimated_glycemic_index": gi_estimasi,
                "glikemik_load": _safe_float(row.get(COL_GL)),
                "net_karbo": max(0.0, karbo - serat),
                "sodium": _safe_float(row.get(COL_SODIUM)),
                "glikemik_risk": "",
                "catatan_kualitas_data": _clean_text(row.get(COL_DATA_NOTE)),
                "catatan_kategori": _clean_text(row.get(COL_CATEGORY_NOTE)),
            }

            for col in BOOL_COLS_CSV:
                food[col] = _safe_bool(row.get(col), False)

            _derive_flags(food)
            foods.append(food)

    _food_cache = foods
    usable = sum(1 for f in foods if f.get("bisa_mealplan"))
    print(f"✅ Dataset master dimuat: {len(foods)} item | kandidat meal plan: {usable} | path: {csv_path}")
    return foods

# ════════════════════════════════════════════════════════════════
# 7. FILTER & SELEKSI MAKANAN UNTUK MEAL PLAN
# ════════════════════════════════════════════════════════════════


def _filter_foods_for_pantangan(foods: List[Dict], pantangan: Optional[str]) -> List[Dict]:
    """Filter alergi/pantangan memakai flag mengandung_* dari dataset baru."""
    if not pantangan:
        return foods
    pantangan_lower = pantangan.lower()
    filtered = []
    for food in foods:
        skip = False
        nama = food.get("nama", "").lower()

        if any(k in pantangan_lower for k in ["seafood", "ikan", "udang", "cumi", "kepiting", "kerang"]):
            if food.get("mengandung_seafood") or food.get("is_seafood") or food.get("is_fish"):
                skip = True

        if any(k in pantangan_lower for k in ["kacang", "nut", "almond", "mete", "kenari"]):
            if food.get("mengandung_kacang") or food.get("is_peanut") or food.get("is_nut_seed"):
                skip = True

        if any(k in pantangan_lower for k in ["dairy", "susu", "keju", "yogurt", "laktosa"]):
            if food.get("mengandung_susu") or food.get("is_dairy"):
                skip = True

        if any(k in pantangan_lower for k in ["telur", "egg"]):
            if food.get("mengandung_telur") or "telur" in nama:
                skip = True

        if any(k in pantangan_lower for k in ["babi", "pork", "non halal", "non-halal"]):
            if food.get("mengandung_babi"):
                skip = True

        if any(k in pantangan_lower for k in ["alkohol", "alcohol", "wine", "beer", "bir"]):
            if food.get("mengandung_alkohol"):
                skip = True

        if not skip:
            filtered.append(food)
    return filtered


_UNSAFE_NAME_KEYWORDS = [
    # makanan yang tidak cocok jadi rekomendasi otomatis walau mungkin masih bisa dicatat di tracker
    "permen", "candy", "chupa", "gummy", "sugus", "yupi", "pocky",
    "wafer", "biskuit", "biscuit", "cookies", "donat", "doughnut",
    "sirup", "syrup", "es krim", "ice cream", "dessert",
    "mie instan", "mi instan", "instant noodle", "ramen", "ramyun",
    "saus", "sambal", "kecap", "terasi", "bumbu", "minyak",
    "gula pasir", "gula merah", "gula aren",
    "beer", "bir", "radler", "vodka", "wine", "alkohol",
    "telur penyu", "tempe bongkrek",
]


def _is_runtime_unsafe_by_name(food: Dict) -> bool:
    text = " ".join([
        food.get("nama", ""),
        food.get("kelompok_makanan", ""),
        food.get("jenis_bahan_utama", ""),
        food.get("tingkat_proses", ""),
        food.get("slot_meal_plan", ""),
    ]).lower()
    return any(k in text for k in _UNSAFE_NAME_KEYWORDS)


def _is_whole_fruit_name(food: Dict) -> bool:
    return _norm_lower(food.get("kelompok_makanan")) == "buah" and _norm_lower(food.get("slot_meal_plan")) == "buah"


def _is_mealplan_safe(food: Dict) -> bool:
    """Kelayakan meal plan otomatis berdasarkan 3 filter + flag minimal."""
    kelompok = _norm_lower(food.get("kelompok_makanan"))
    proses = _norm_lower(food.get("tingkat_proses"))
    slot = _norm_lower(food.get("slot_meal_plan"))

    if _slot_is_no_meal(slot):
        return False
    if kelompok in {"snack", "dessert", "minuman", "bumbu & pelengkap", "suplemen nutrisi"}:
        return False
    if proses in {"mentah", "ultraproses"}:
        return False
    # Mentah bisa dimakan boleh untuk buah/sayur, tapi jangan auto-rekomendasikan hewani mentah seperti sashimi.
    if proses == "mentah bisa dimakan" and kelompok == "lauk hewani":
        return False
    if food.get("mengandung_babi") or food.get("mengandung_alkohol"):
        return False
    if food.get("adalah_gorengan"):
        return False
    if _is_runtime_unsafe_by_name(food):
        return False
    if food.get("is_high_added_sugar"):
        return False
    if food.get("is_high_sugar") and not _is_whole_fruit_name(food):
        return False
    return True


def _food_matches_slot(food: Dict, kategori_filter: List[str]) -> bool:
    """Cocokkan makanan ke slot_meal_plan: Karbo, Lauk, Sayur, Buah."""
    slot = _norm_lower(food.get("slot_meal_plan"))
    targets = [str(k).strip().lower() for k in kategori_filter]

    for target in targets:
        if target in ["karbo", "makanan utama", "makanan pokok", "makanan pokok/karbo"]:
            if slot in ["karbo", "makanan pokok/karbo"]:
                return True
        elif target == "lauk":
            if slot == "lauk":
                return True
        elif target == "sayur":
            if slot == "sayur":
                return True
        elif target == "buah":
            if slot == "buah":
                return True
        elif target in ["snack", "camilan", "camilan sehat"]:
            # Snack tidak dipakai di meal plan utama, tapi kompatibilitas jika suatu saat dipanggil.
            if _norm_lower(food.get("kelompok_makanan")) in ["buah", "biji/kacang"] and not _slot_is_no_meal(slot):
                return True
        elif target == "minuman":
            if _norm_lower(food.get("kelompok_makanan")) == "minuman":
                return True
    return False


def _rank_food(food: Dict, kategori_filter: List[str], kategori_risiko: str, active_diet: Optional[str] = None) -> float:
    """Skor prioritas pemilihan makanan berbasis dataset baru."""
    score = 0.0
    targets = [str(k).lower() for k in kategori_filter]
    kelompok = _norm_lower(food.get("kelompok_makanan"))
    jenis = _norm_lower(food.get("jenis_bahan_utama"))

    if "Tinggi" in kategori_risiko or "Terkonfirmasi" in kategori_risiko:
        if food.get("is_low_glycemic"):  score += 10
        if food.get("is_high_fiber"):    score += 8
        if food.get("is_high_glycemic"): score -= 25
        if food.get("is_high_added_sugar"): score -= 35
        if food.get("is_high_sugar") and not _is_whole_fruit_name(food): score -= 30
        if food.get("karbohidrat_olahan"): score -= 10

    if any(t in ["karbo", "makanan utama", "makanan pokok", "makanan pokok/karbo"] for t in targets):
        if food.get("karbohidrat_kompleks"): score += 12
        if food.get("karbohidrat_olahan"): score -= 12

    if food.get("adalah_gorengan"): score -= 30
    if _norm_lower(food.get("tingkat_proses")) == "ultraproses": score -= 25
    if food.get("is_high_sodium"): score -= 15
    if food.get("mengandung_santan"): score -= 6
    if food.get("gambar"): score += 2

    if "Sedang" in kategori_risiko and food.get("karbohidrat_kompleks"):
        score += 5

    diet = normalisasi_active_diet(active_diet)
    if diet == "mediterania":
        if kelompok == "sayur": score += 12
        if kelompok == "buah": score += 8
        if jenis == "seafood": score += 14
        if kelompok == "lauk nabati": score += 10
        if kelompok == "biji/kacang": score += 8
        if food.get("karbohidrat_kompleks"): score += 8
        if food.get("is_high_fiber"): score += 8
        if food.get("is_low_glycemic"): score += 5
        if jenis == "daging merah": score -= 8
        if food.get("karbohidrat_olahan"): score -= 12
        if food.get("mengandung_santan"): score -= 8

    elif diet == "rendah_karbo":
        karbo = float(food.get("karbo", 0) or 0)
        net_karbo = float(food.get("net_karbo", karbo) or karbo)
        if kelompok == "sayur": score += 14
        if kelompok in {"lauk hewani", "lauk nabati"}: score += 10
        if food.get("is_low_carb"): score += 10
        if food.get("is_low_glycemic"): score += 8
        if food.get("is_high_fiber"): score += 6
        if food.get("karbohidrat_olahan"): score -= 25
        if food.get("is_high_glycemic"): score -= 20
        if net_karbo > 30: score -= 12
        if net_karbo > 45: score -= 25
        if "karbo" in targets and food.get("karbohidrat_kompleks") and not food.get("is_high_glycemic"):
            score += 6

    return score

def _pick_food_for_slot(
    foods: List[Dict],
    kategori_filter: List[str],
    kalori_target: float,
    kalori_tolerance: float = 0.4,
    kategori_risiko: str = "",
    used_names: Optional[set] = None,
    active_diet: Optional[str] = None,
) -> Optional[Dict]:
    if used_names is None:
        used_names = set()

    candidates = [
        f for f in foods
        if f.get("nama") not in used_names
        and _is_mealplan_safe(f)
        and _food_matches_slot(f, kategori_filter)
    ]

    if not any(str(k).lower() == "minuman" for k in kategori_filter):
        candidates = [f for f in candidates if not f.get("is_beverage")]

    if not candidates:
        return None

    # Filter lebih ketat untuk risiko tinggi
    if "Tinggi" in kategori_risiko or "Terkonfirmasi" in kategori_risiko:
        safer = [
            f for f in candidates
            if not f.get("is_high_added_sugar")
            and not (f.get("is_high_sugar") and not _is_whole_fruit_name(f))
            and not f.get("is_high_glycemic")
            and not (f.get("is_refined_carb") and not f.get("is_complex_carb"))
        ]
        if len(safer) >= 3:
            candidates = safer

    diet = normalisasi_active_diet(active_diet)
    targets_lower = [str(k).lower() for k in kategori_filter]
    if diet == "rendah_karbo" and any(t in ["karbo", "makanan utama", "makanan pokok"] for t in targets_lower):
        lower_carb = [
            f for f in candidates
            if not f.get("is_refined_carb")
            and not f.get("is_high_glycemic")
            and float(f.get("net_karbo", f.get("karbo", 0)) or 0) <= 45
        ]
        if len(lower_carb) >= 2:
            candidates = lower_carb

    min_kal = kalori_target * (1 - kalori_tolerance)
    max_kal = kalori_target * (1 + kalori_tolerance)
    in_range = [f for f in candidates if min_kal <= f.get("kalori", 0) <= max_kal]
    pool = in_range if in_range else candidates

    ranked = sorted(
        pool,
        key=lambda f: (
            _rank_food(f, kategori_filter, kategori_risiko, active_diet),
            -abs(f.get("kalori", 0) - kalori_target),
        ),
        reverse=True,
    )
    top = ranked[:10] if len(ranked) > 10 else ranked
    return random.choice(top) if top else None


# ════════════════════════════════════════════════════════════════
# 8. GENERATE MEAL PLAN HARIAN
# ════════════════════════════════════════════════════════════════

def generate_meal_plan(
    target_kalori: float,
    frekuensi_makan: str,
    waktu_makan: List[str],
    kategori_risiko: str,
    pantangan: Optional[str],
    pola_waktu_makan: str = "normal",
    pola_puasa: Optional[str] = None,
    jam_makan_mulai: Optional[str] = None,
    jam_makan_selesai: Optional[str] = None,
    active_diet: Optional[str] = None,
) -> List[Dict]:
    """
    Generate meal plan harian.
    - semua makanan dipilih dari dataset langsung.
    - Komposisi default per slot: Karbo 40% | Lauk 30% | Sayur 15% | Buah 15% (kecuali malam).
    - Mediterania per slot: Karbo kompleks 30% | Protein sehat 25% | Sayur 30% | Buah 15%.
    - Rendah karbo per slot: Karbo 25% | Lauk 40% | Sayur 25% | Buah 10%.
    - Distribusi kalori: 30% Pagi / 40% Siang / 30% Malam, atau mengikuti jumlah slot makan.
    """
    all_foods = _load_food_data()
    if not all_foods:
        return []

    usable_foods = [f for f in all_foods if _is_mealplan_safe(f)]
    if not usable_foods:
        return []

    foods = _filter_foods_for_pantangan(usable_foods, pantangan)
    if not foods:
        foods = usable_foods

    distribusi  = _distribusi_kalori_slots(waktu_makan)
    jadwal_makan, _, _, _ = _susun_jadwal_if(
        waktu_makan=waktu_makan,
        pola_waktu_makan=pola_waktu_makan,
        pola_puasa=pola_puasa,
        jam_makan_mulai=jam_makan_mulai,
        jam_makan_selesai=jam_makan_selesai,
    )

    meal_plan  = []
    used_names: set = set()

    for i, waktu in enumerate(waktu_makan):
        slot_kalori = target_kalori * distribusi[i]

        diet = normalisasi_active_diet(active_diet)
        if diet == "rendah_karbo":
            # Rendah karbo: porsi karbo diturunkan, lauk dan sayur dinaikkan.
            karbo_kal = slot_kalori * 0.25
            lauk_kal  = slot_kalori * 0.40
            sayur_kal = slot_kalori * 0.25
            buah_kal  = slot_kalori * 0.10
        elif diet == "mediterania":
            # Mediterania: mendekati konsep piring sehat; sayur dinaikkan, karbo kompleks dikontrol.
            # Target makro harian tetap dari analisis, perubahan ini hanya untuk komposisi komponen meal plan.
            karbo_kal = slot_kalori * 0.30
            lauk_kal  = slot_kalori * 0.25
            sayur_kal = slot_kalori * 0.30
            buah_kal  = slot_kalori * 0.15
        else:
            karbo_kal = slot_kalori * 0.40
            lauk_kal  = slot_kalori * 0.30
            sayur_kal = slot_kalori * 0.15
            buah_kal  = slot_kalori * 0.15

        slot_items = []

        # Karbo / makanan pokok
        masakan = _pick_food_for_slot(
            foods, ["Karbo"], karbo_kal, 0.5, kategori_risiko, used_names, active_diet
        )
        if masakan:
            used_names.add(masakan["nama"])
            slot_items.append({**masakan, "slot_label": "Makanan Utama"})

        # Lauk / protein
        lauk = _pick_food_for_slot(
            foods, ["Lauk"], lauk_kal, 0.5, kategori_risiko, used_names, active_diet
        )
        if lauk:
            used_names.add(lauk["nama"])
            slot_items.append({**lauk, "slot_label": "Lauk"})

        # Sayur
        sayur = _pick_food_for_slot(
            foods, ["Sayur"], sayur_kal, 0.8, kategori_risiko, used_names, active_diet
        )
        if sayur:
            used_names.add(sayur["nama"])
            slot_items.append({**sayur, "slot_label": "Sayur"})

        # Buah — tidak muncul di makan malam / slot terakhir
        is_malam = str(waktu).lower() in ["malam", "makan malam", "makan 3"] or (
            pola_waktu_makan == "intermittent_fasting"
            and str(waktu).lower() == "makan 2"
            and len(waktu_makan) == 2
        )
        if not is_malam:
            buah = _pick_food_for_slot(
                foods, ["Buah"], buah_kal, 0.8, kategori_risiko, used_names, active_diet
            )
            if buah:
                used_names.add(buah["nama"])
                slot_items.append({**buah, "slot_label": "Buah"})

        total_kal_slot = sum(item["kalori"] for item in slot_items)
        jadwal_item = jadwal_makan[i] if i < len(jadwal_makan) else {"label": waktu, "jam": None}

        meal_plan.append({
            "waktu":              waktu,
            "waktu_asli":         waktu,
            "jenis_slot":         "makan_utama",
            "jam":                jadwal_item.get("jam"),
            "target_kalori_slot": round(slot_kalori),
            "total_kalori_slot":  round(total_kal_slot),
            "active_diet":        normalisasi_active_diet(active_diet),
            "items":              slot_items,
        })

    return meal_plan


# ════════════════════════════════════════════════════════════════
# 9. FUNGSI UTAMA: ANALISIS LENGKAP
# ════════════════════════════════════════════════════════════════

def analisis_user(profil: ProfilUser) -> HasilAnalisis:
    # 1. BMI & TDEE
    bmi, kategori_bmi = hitung_bmi(profil.berat_badan, profil.tinggi_badan)
    tdee = hitung_tdee(profil)

    # 2. Skoring risiko
    skor, kategori_risiko = hitung_skor_risiko(profil, bmi)

    # 3. Target kalori & makro
    active_diet = normalisasi_active_diet(getattr(profil, "active_diet", None))
    active_diet_label = _diet_label(active_diet)

    target_kalori, karbo_default, protein_default, lemak_default = hitung_target_kalori_makro(
        tdee, kategori_risiko, bmi, profil.jenis_kelamin
    )
    karbo, protein, lemak, catatan_diet_aktif = terapkan_diet_pada_target_makro(
        target_kalori=target_kalori,
        kategori_risiko=kategori_risiko,
        karbo_default=karbo_default,
        protein_default=protein_default,
        lemak_default=lemak_default,
        active_diet=active_diet,
    )

    kategori_tdee = (
        "deficit"     if target_kalori < tdee else
        "surplus"     if target_kalori > tdee else
        "maintenance"
    )

    # 4. Profil singkat
    pantangan = profil.pantangan_alergi if profil.pantangan_alergi else "Tidak ada"
    gejala    = ", ".join(profil.gejala_klasik) if profil.gejala_klasik else "Tidak ada"

    pola_waktu_makan = getattr(profil, "pola_waktu_makan", "normal") or "normal"
    pola_puasa       = getattr(profil, "pola_puasa", None)

    pola_label = pola_waktu_makan
    if pola_waktu_makan == "intermittent_fasting" and pola_puasa:
        pola_label = f"Intermittent Fasting {pola_puasa}"

    profil_singkat = (
        f"Pasien: Usia {profil.usia} thn, {profil.jenis_kelamin}. "
        f"BMI: {bmi} ({kategori_bmi} standar Asia). "
        f"Risiko: {kategori_risiko} (Skor {skor}). "
        f"Gejala: {gejala}. "
        f"Target Gizi: {target_kalori} kkal "
        f"(Karbo: {karbo}g, Protein: {protein}g, Lemak: {lemak}g). "
        f"Pola Makan: {pola_label}. "
        f"Diet Aktif: {active_diet_label or 'Tidak ada'}. "
        f"Pantangan: {pantangan}. "
        f"Penyakit/Obat: {profil.penyakit_lain_obat or 'Tidak ada'}."
    )

    # 6. Susun jadwal & meal plan
    frekuensi        = getattr(profil, "frekuensi_makan", "3x") or "3x"
    waktu_list       = getattr(profil, "waktu_makan", []) or []
    jam_makan_mulai  = getattr(profil, "jam_makan_mulai", None)
    jam_makan_selesai = getattr(profil, "jam_makan_selesai", None)

    if not waktu_list:
        waktu_list = ["Siang", "Malam"] if frekuensi == "2x" else ["Pagi", "Siang", "Malam"]

    _, catatan_pola_makan, jam_makan_mulai, jam_makan_selesai = _susun_jadwal_if(
        waktu_makan=waktu_list,
        pola_waktu_makan=pola_waktu_makan,
        pola_puasa=pola_puasa,
        jam_makan_mulai=jam_makan_mulai,
        jam_makan_selesai=jam_makan_selesai,
    )
    if catatan_diet_aktif:
        catatan_pola_makan.append(catatan_diet_aktif)

    meal_plan, validasi_meal_plan = generate_meal_plan_tervalidasi(
        target_kalori=target_kalori,
        target_karbo=karbo,
        target_protein=protein,
        target_lemak=lemak,
        frekuensi_makan=frekuensi,
        waktu_makan=waktu_list,
        kategori_risiko=kategori_risiko,
        pantangan=profil.pantangan_alergi,
        pola_waktu_makan=pola_waktu_makan,
        pola_puasa=pola_puasa,
        jam_makan_mulai=jam_makan_mulai,
        jam_makan_selesai=jam_makan_selesai,
        active_diet=active_diet,
    )

    if validasi_meal_plan:
        catatan_pola_makan.append(_catatan_validasi_meal_plan(validasi_meal_plan))

    return HasilAnalisis(
        bmi=bmi,
        kategori_bmi=kategori_bmi,
        tdee_mifflin=tdee,
        kategori_tdee=kategori_tdee,
        skor_risiko=skor,
        kategori_risiko=kategori_risiko,
        target_kalori=target_kalori,
        target_karbo=karbo,
        target_protein=protein,
        target_lemak=lemak,
        profil_singkat=profil_singkat,
        active_diet=active_diet,
        active_diet_label=active_diet_label,
        pantangan=[] if not profil.pantangan_alergi else [profil.pantangan_alergi],
        meal_plan=meal_plan,
        frekuensi_makan=frekuensi,
        waktu_makan=waktu_list,
        pola_waktu_makan=pola_waktu_makan,
        pola_puasa=pola_puasa,
        jam_makan_mulai=jam_makan_mulai,
        jam_makan_selesai=jam_makan_selesai,
        catatan_pola_makan=catatan_pola_makan,
    )