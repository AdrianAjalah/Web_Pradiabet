import os
import time
import hashlib
import pandas as pd
import cohere

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct


# ===============================
# CONFIG
# ===============================

CSV_PATH = os.getenv(
    "FOOD_CSV_PATH",
    "data/master_makanan_kategori_flag_mealplan.csv"
)

QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY")

COHERE_API_KEY = os.getenv("COHERE_API_KEY")
COHERE_EMBED_MODEL = os.getenv("COHERE_EMBED_MODEL", "embed-multilingual-v3.0")

COLLECTION_DOCS = os.getenv("COLLECTION_DOCS", "collection_documents")
VECTOR_SIZE = 1024
BATCH_SIZE = 96


if not COHERE_API_KEY:
    raise RuntimeError("COHERE_API_KEY belum diset.")


cohere_client = cohere.ClientV2(api_key=COHERE_API_KEY)

qdrant = QdrantClient(
    url=QDRANT_URL,
    api_key=QDRANT_API_KEY if QDRANT_API_KEY else None,
    timeout=60
)


# ===============================
# HELPER
# ===============================

def deterministic_id(key: str) -> int:
    return int(hashlib.md5(key.encode()).hexdigest()[:16], 16)


def is_true(value) -> bool:
    if pd.isna(value):
        return False
    return str(value).strip().lower() in {"true", "1", "ya", "yes"}


def clean_value(value):
    if pd.isna(value):
        return ""
    value = str(value).strip()
    if value.lower() in {"nan", "none", "null", "-"}:
        return ""
    return value


def add_if(parts, label, value, suffix=""):
    value = clean_value(value)
    if value:
        parts.append(f"{label}: {value}{suffix}")


def build_food_chunk(row) -> tuple[str, dict]:
    """
    Mengubah 1 baris makanan menjadi teks yang bisa dipahami LLM
    dan metadata yang bisa dipakai Qdrant.
    """

    nama = clean_value(row.get("nama_makanan"))
    kode = clean_value(row.get("kode"))

    kelompok = clean_value(row.get("kelompok_makanan"))
    jenis_bahan = clean_value(row.get("jenis_bahan_utama"))
    tingkat_proses = clean_value(row.get("tingkat_proses"))
    slot_meal_plan = clean_value(row.get("slot_meal_plan"))

    parts = []

    # Penting: format ini dibaca oleh rag_patch.py
    add_if(parts, "Nama Makanan", nama)
    add_if(parts, "Kode", kode)
    add_if(parts, "Kelompok Makanan", kelompok)
    add_if(parts, "Jenis Bahan Utama", jenis_bahan)
    add_if(parts, "Tingkat Proses", tingkat_proses)
    add_if(parts, "Slot Meal Plan", slot_meal_plan)

    add_if(parts, "Porsi", row.get("gram_porsi"), " g")
    add_if(parts, "Kalori", row.get("kalori_kkal"), " kkal")
    add_if(parts, "Karbohidrat", row.get("karbohidrat_g"), " g")
    add_if(parts, "Protein", row.get("protein_g"), " g")
    add_if(parts, "Lemak", row.get("lemak_g"), " g")
    add_if(parts, "Lemak jenuh", row.get("lemak_jenuh_g"), " g")
    add_if(parts, "Lemak trans", row.get("lemak_trans_g"), " g")
    add_if(parts, "Serat", row.get("serat_g"), " g")
    add_if(parts, "Gula", row.get("gula_g"), " g")
    add_if(parts, "Natrium", row.get("natrium_mg"), " mg")
    add_if(parts, "Magnesium", row.get("magnesium_mg"), " mg")
    add_if(parts, "Zinc", row.get("zinc_mg"), " mg")
    add_if(parts, "Chromium", row.get("chromium_mcg"), " mcg")
    add_if(parts, "Indeks Glikemik", row.get("indeks_glikemik"))
    add_if(parts, "Estimasi Indeks Glikemik", row.get("indeks_glikemik_estimasi"))
    add_if(parts, "Beban Glikemik", row.get("beban_glikemik"))

    # ===============================
    # FLAG dibuat jadi bahasa manusia
    # ===============================

    flag_labels = {
        "sumber_karbohidrat": "Sumber karbohidrat",
        "karbohidrat_kompleks": "Karbohidrat kompleks",
        "karbohidrat_olahan": "Karbohidrat olahan",
        "mengandung_susu": "Mengandung susu",
        "mengandung_telur": "Mengandung telur",
        "mengandung_seafood": "Mengandung seafood",
        "mengandung_kacang": "Mengandung kacang",
        "mengandung_babi": "Mengandung babi",
        "mengandung_santan": "Mengandung santan",
        "mengandung_alkohol": "Mengandung alkohol",
        "mengandung_sayur": "Mengandung sayur",
        "adalah_gorengan": "Gorengan",
    }

    flag_positif = []
    flag_perhatian = []

    for col, label in flag_labels.items():
        yes = is_true(row.get(col))
        parts.append(f"{label}: {'Ya' if yes else 'Tidak'}")

        if yes:
            if col in {
                "mengandung_susu",
                "mengandung_telur",
                "mengandung_seafood",
                "mengandung_kacang",
                "mengandung_babi",
                "mengandung_santan",
                "mengandung_alkohol",
                "adalah_gorengan",
                "karbohidrat_olahan",
            }:
                flag_perhatian.append(label)

            if col in {
                "karbohidrat_kompleks",
                "mengandung_sayur",
            }:
                flag_positif.append(label)

    if flag_perhatian:
        parts.append(
            "Catatan perhatian: "
            + ", ".join(flag_perhatian)
            + ". Perlu diperhatikan sesuai kondisi user, alergi, pantangan, dan risiko prediabetes."
        )

    if flag_positif:
        parts.append(
            "Catatan positif: "
            + ", ".join(flag_positif)
            + ". Dapat menjadi nilai tambah jika porsinya sesuai."
        )

    # ===============================
    # Interpretasi otomatis sederhana
    # ===============================

    proses_lower = tingkat_proses.lower()
    kelompok_lower = kelompok.lower()

    if "ultra" in proses_lower:
        parts.append(
            "Interpretasi: makanan ini termasuk ultra-proses, sehingga sebaiknya dibatasi untuk pengguna prediabetes."
        )

    if is_true(row.get("karbohidrat_olahan")):
        parts.append(
            "Interpretasi karbohidrat: mengandung karbohidrat olahan, sehingga perlu dikontrol porsinya."
        )

    if is_true(row.get("karbohidrat_kompleks")):
        parts.append(
            "Interpretasi karbohidrat: mengandung karbohidrat kompleks, biasanya lebih baik untuk kestabilan gula darah dibanding karbohidrat olahan."
        )

    if is_true(row.get("adalah_gorengan")):
        parts.append(
            "Interpretasi: makanan ini termasuk gorengan, sehingga sebaiknya tidak terlalu sering dikonsumsi."
        )

    gula = row.get("gula_g")
    try:
        gula_float = float(gula)
        if gula_float >= 10 and "buah" not in kelompok_lower:
            parts.append(
                "Interpretasi gula: kandungan gula cukup tinggi dan bukan berasal dari kategori buah, sehingga perlu dibatasi."
            )
        elif gula_float >= 10 and "buah" in kelompok_lower:
            parts.append(
                "Interpretasi gula: gula berasal dari kategori buah sehingga diperlakukan sebagai gula alami, tetapi porsi tetap perlu dijaga."
            )
    except Exception:
        pass

    add_if(parts, "Sumber Data", row.get("sumber_data"))
    add_if(parts, "File Sumber", row.get("file_sumber"))
    add_if(parts, "Catatan Kualitas Data", row.get("catatan_kualitas_data"))
    add_if(parts, "Catatan Kategori", row.get("catatan_kategori"))

    content = "\n".join(parts)

    payload = {
        "chunk_id": f"food_{kode or nama}",
        "doc_name": "master_makanan_kategori_flag_mealplan",
        "layer": 0,
        "type": "nutrisi",
        "content": content,

        # Penting untuk exact match di rag_patch.py
        "food_name": nama.lower(),
        "kode": kode,
        "kelompok_makanan": kelompok,
        "tingkat_proses": tingkat_proses,
        "slot_meal_plan": slot_meal_plan,

        # Metadata flag untuk filter jika nanti dibutuhkan
        "sumber_karbohidrat": is_true(row.get("sumber_karbohidrat")),
        "karbohidrat_kompleks": is_true(row.get("karbohidrat_kompleks")),
        "karbohidrat_olahan": is_true(row.get("karbohidrat_olahan")),
        "mengandung_susu": is_true(row.get("mengandung_susu")),
        "mengandung_telur": is_true(row.get("mengandung_telur")),
        "mengandung_seafood": is_true(row.get("mengandung_seafood")),
        "mengandung_kacang": is_true(row.get("mengandung_kacang")),
        "mengandung_babi": is_true(row.get("mengandung_babi")),
        "mengandung_santan": is_true(row.get("mengandung_santan")),
        "mengandung_alkohol": is_true(row.get("mengandung_alkohol")),
        "mengandung_sayur": is_true(row.get("mengandung_sayur")),
        "adalah_gorengan": is_true(row.get("adalah_gorengan")),
    }

    return content, payload


def embed_documents(texts: list[str]) -> list[list[float]]:
    response = cohere_client.embed(
        model=COHERE_EMBED_MODEL,
        texts=texts,
        input_type="search_document",
        embedding_types=["float"],
    )
    return response.embeddings.float


def recreate_collection():
    existing = [c.name for c in qdrant.get_collections().collections]

    if COLLECTION_DOCS in existing:
        print(f"Deleting old collection: {COLLECTION_DOCS}")
        qdrant.delete_collection(collection_name=COLLECTION_DOCS)

    print(f"Creating collection: {COLLECTION_DOCS}")
    qdrant.create_collection(
        collection_name=COLLECTION_DOCS,
        vectors_config=VectorParams(
            size=VECTOR_SIZE,
            distance=Distance.COSINE
        )
    )


def main():
    print("Membaca dataset:", CSV_PATH)

    df = pd.read_csv(
        CSV_PATH,
        sep=";",
        decimal=",",
        encoding="utf-8-sig"
    )

    print(f"Total baris dataset: {len(df)}")

    recreate_collection()

    texts = []
    payloads = []

    for _, row in df.iterrows():
        content, payload = build_food_chunk(row)
        if not payload["food_name"]:
            continue
        texts.append(content)
        payloads.append(payload)

    print(f"Total data valid untuk di-index: {len(texts)}")

    total_uploaded = 0

    for start in range(0, len(texts), BATCH_SIZE):
        end = start + BATCH_SIZE
        batch_texts = texts[start:end]
        batch_payloads = payloads[start:end]

        print(f"Embedding batch {start // BATCH_SIZE + 1}: {len(batch_texts)} data")

        vectors = embed_documents(batch_texts)

        points = []
        for idx, (vector, payload) in enumerate(zip(vectors, batch_payloads)):
            point_id = deterministic_id(payload["chunk_id"])
            points.append(
                PointStruct(
                    id=point_id,
                    vector=vector,
                    payload=payload
                )
            )

        qdrant.upsert(
            collection_name=COLLECTION_DOCS,
            points=points
        )

        total_uploaded += len(points)
        print(f"Uploaded: {total_uploaded}/{len(texts)}")

        # Jaga rate limit trial Cohere
        time.sleep(1.6)

    print("✅ Selesai indexing dataset makanan baru ke Qdrant.")
    print(f"Total uploaded: {total_uploaded}")


if __name__ == "__main__":
    main()