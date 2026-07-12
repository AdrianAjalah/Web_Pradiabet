from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any


# ════════════════════════════════════════════════════════════════
# MODEL AUTHENTICATION (Login & Register)
# ════════════════════════════════════════════════════════════════

class UserLogin(BaseModel):
    username: str
    password: str


class UserRegister(BaseModel):
    username: str
    password: str


class UserResponse(BaseModel):
    id: int
    username: str
    role: str


# ════════════════════════════════════════════════════════════════
# MODEL DATA (Pydantic Models untuk FastAPI)
# ════════════════════════════════════════════════════════════════

class ProfilUser(BaseModel):
    """Model data profil user berdasarkan kuesioner kesehatan & gaya hidup."""

    # ── Tahap 1: Data Dasar ──
    usia: int = Field(..., description="Usia user dalam tahun", gt=0)
    jenis_kelamin: str = Field(..., description="Laki-laki atau Perempuan")
    berat_badan: float = Field(default=0.0, description="Berat badan dalam kg", ge=0)
    tinggi_badan: float = Field(default=0.0, description="Tinggi badan dalam cm", ge=0)
    lingkar_pinggang: Optional[float] = Field(default=None, description="Lingkar pinggang dalam cm (Opsional)")

    # ── Tahap 2: Radar Risiko ──
    frekuensi_minum_manis: str = Field(default="", description="Frekuensi makan/minum manis")
    porsi_nasi_per_hari: str = Field(default="", description="Porsi nasi per hari")
    frekuensi_olahraga: str = Field(default="", description="Frekuensi olahraga mingguan")
    durasi_duduk_rebahan: str = Field(default="", description="Durasi duduk/rebahan di luar tidur")
    durasi_tidur: str = Field(default="", description="Durasi rata-rata tidur malam")

    riwayat_keluarga_diabetes: bool = Field(default=False, description="Riwayat keluarga diabetes (True/False)")
    gejala_klasik: List[str] = Field(default_factory=list, description="Gejala klasik diabetes yang dirasakan")

    # ── Tahap 3: Pelengkap Medis (Opsional) ──
    pantangan_alergi: Optional[str] = Field(default=None, description="Pantangan atau alergi makanan")
    penyakit_lain_obat: Optional[str] = Field(default=None, description="Penyakit penyerta atau obat yang rutin diminum")
    hasil_lab: Optional[str] = Field(default=None, description="Data Gula Darah, HbA1c, atau Tensi jika pernah tes")

    # ── Tahap 4: Preferensi Meal Plan ──
    frekuensi_makan: str = Field(default="3x", description="Frekuensi makan per hari: '2x' atau '3x'")
    waktu_makan: List[str] = Field(
        default_factory=list,
        description="Daftar waktu makan yang dipilih user, misal ['Pagi', 'Siang', 'Malam']"
    )

    # ── Diet aktif dari halaman dashboard ──
    # Nilai valid: None, "mediterania", atau "rendah_karbo".
    active_diet: Optional[str] = Field(
        default=None,
        description="Diet aktif pilihan user: mediterania/rendah_karbo/None"
    )

    # ── Tahap 5: Pola Waktu Makan (Opsional) ──
    # Intermittent Fasting bukan diet pilihan, melainkan pola jam makan.
    pola_waktu_makan: str = Field(
        default="normal",
        description="Pola waktu makan: normal atau intermittent_fasting"
    )
    pola_puasa: Optional[str] = Field(
        default=None,
        description="Pilihan IF opsional, misal: 12:12, 14:10, 16:8"
    )
    jam_makan_mulai: Optional[str] = Field(
        default=None,
        description="Jam mulai jendela makan, format HH:MM. Contoh: 10:00"
    )
    jam_makan_selesai: Optional[str] = Field(
        default=None,
        description="Jam selesai jendela makan, format HH:MM. Contoh: 18:00"
    )


class HasilAnalisis(BaseModel):
    """Model hasil analisis lengkap tanpa diet pilihan dan tanpa skor kecocokan diet."""

    bmi: float
    kategori_bmi: str
    tdee_mifflin: float
    kategori_tdee: str
    skor_risiko: int
    kategori_risiko: str

    target_kalori: float
    target_karbo: float
    target_protein: float
    target_lemak: float
    profil_singkat: str

    # ── Diet aktif ──
    active_diet: Optional[str] = Field(default=None, description="Diet aktif: mediterania/rendah_karbo/None")
    active_diet_label: Optional[str] = Field(default=None, description="Nama diet aktif untuk ditampilkan")

    pantangan: List[str] = Field(default_factory=list)

    # ── Meal Plan ──
    meal_plan: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Daftar rencana makan harian dengan gambar makanan"
    )
    frekuensi_makan: str = Field(default="3x", description="Frekuensi makan per hari")
    waktu_makan: List[str] = Field(default_factory=list, description="Waktu makan yang dipilih")

    # ── Output Pola Waktu Makan ──
    pola_waktu_makan: str = Field(default="normal", description="normal atau intermittent_fasting")
    pola_puasa: Optional[str] = Field(default=None, description="12:12, 14:10, 16:8, atau None")
    jam_makan_mulai: Optional[str] = Field(default=None, description="Jam mulai jendela makan")
    jam_makan_selesai: Optional[str] = Field(default=None, description="Jam selesai jendela makan")
    catatan_pola_makan: List[str] = Field(default_factory=list, description="Catatan keamanan/adaptasi pola waktu makan")

    class Config:
        from_attributes = True
