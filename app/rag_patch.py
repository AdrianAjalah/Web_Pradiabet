"""
PATCH ANTI-HALUSINASI untuk Dr. Predia
=======================================
Masalah yang ditemukan di data kamu:
  - QA pairs: pertanyaan sama ("kalori ABC?") punya 3 jawaban berbeda karena banyak produk "ABC"
  - Jawaban QA cuma angka mentah tanpa nama makanan → LLM tidak bisa verifikasi
  - Akibatnya: LLM ngasal/interpolasi

Solusi:
  1. Skip QA collection untuk pertanyaan nutrisi — pakai Docs langsung
  2. Tambah metadata 'food_name' di setiap chunk Qdrant (untuk exact match filter)
  3. Sistem dua jalur: coba exact match dulu, fallback ke semantic search
  4. System prompt didesingn ulang: LLM HANYA boleh baca dari context, dilarang improvise angka
"""

import re
from qdrant_client.http import models as qmodels

# ────────────────────────────────────────────────────────────────
# STEP 1: RE-UPLOAD CHUNKS KE QDRANT DENGAN METADATA food_name
# Jalankan sekali saja setelah patch ini.
# ────────────────────────────────────────────────────────────────

def extract_food_name(content: str) -> str:
    """Ekstrak nama makanan dari string chunk nutrisi."""
    match = re.search(r'Nama Makanan:\s*([^,\n]+)', content)
    if match:
        return match.group(1).strip().lower()
    return ""

def patch_qdrant_payloads(qdrant_client, collection_name: str):
    """
    Tambahkan field 'food_name' ke semua payload di collection_documents.
    Cukup dijalankan SEKALI. Idempoten — aman dijalankan ulang.
    """
    offset = None
    patched = 0
    print(f"Mulai patch payload collection '{collection_name}'...")
    
    while True:
        result = qdrant_client.scroll(
            collection_name=collection_name,
            offset=offset,
            limit=100,
            with_payload=True,
            with_vectors=False
        )
        points, next_offset = result
        
        if not points:
            break
        
        for point in points:
            content = point.payload.get("content", "")
            food_name = extract_food_name(content)
            if food_name and "food_name" not in point.payload:
                qdrant_client.set_payload(
                    collection_name=collection_name,
                    payload={"food_name": food_name},
                    points=[point.id]
                )
                patched += 1
        
        offset = next_offset
        if offset is None:
            break
    
    print(f"✅ Patch selesai. {patched} chunk diperbarui dengan field 'food_name'.")


# ────────────────────────────────────────────────────────────────
# STEP 2: FUNGSI RETRIEVAL BARU (gantikan blok hybrid RAG routing)
# ────────────────────────────────────────────────────────────────

def is_food_query(text: str) -> bool:
    """Deteksi apakah pertanyaan ini tentang nutrisi/makanan tertentu."""
    food_keywords = [
        "kalori", "karbohidrat", "protein", "lemak", "gula", "gizi",
        "nutrisi", "serat", "porsi", "makan", "minum", "boleh", "aman",
        "kandungan", "nilai gizi", "per 100g", "per porsi"
    ]
    lower = text.lower()
    return any(kw in lower for kw in food_keywords)


def smart_retrieve(qdrant_client, collection_docs: str,
                   query_vector: list, pertanyaan: str, top_k: int = 8):
    """
    Strategi retrieval dua jalur anti-halusinasi:
    
    Jalur A (Exact Name Match):
        Ekstrak nama makanan dari pertanyaan user → filter Qdrant by food_name
        Hasilnya: hanya chunk yang BENAR-BENAR bernama itu yang masuk context
    
    Jalur B (Semantic Fallback):
        Jika tidak ada exact match → semantic search di Docs collection biasa
        QA collection TIDAK dipakai untuk pertanyaan nutrisi (karena jawaban QA
        cuma angka mentah tanpa nama makanan = rawan halusinasi)
    """
    contexts = []
    best_source = "-"
    best_confidence = 0.0
    
    # ── JALUR A: Coba exact food name match ──────────────────────
    # Ekstrak kandidat nama dari pertanyaan (kata-kata setelah keyword kunci)
    name_patterns = [
        r'(?:tentang|kalori|kandungan|gizi|porsi|boleh makan|makan)\s+([a-zA-Z0-9 ,]+?)(?:\?|$|\s+itu|\s+ini|\s+dong|\s+ya)',
        r'(?:makanan|minuman|produk)\s+([a-zA-Z0-9 ,]+?)(?:\?|$|\s+itu)',
    ]
    
    extracted_name = None
    for pat in name_patterns:
        m = re.search(pat, pertanyaan.lower())
        if m:
            extracted_name = m.group(1).strip()
            break
    
    if extracted_name:
        try:
            # Cari dengan filter: food_name mengandung kata kunci
            # Qdrant MatchText melakukan substring/token match
            exact_res = qdrant_client.query_points(
                collection_name=collection_docs,
                query=query_vector,
                query_filter=qmodels.Filter(
                    must=[
                        qmodels.FieldCondition(
                            key="food_name",
                            match=qmodels.MatchText(text=extracted_name)
                        )
                    ]
                ),
                limit=top_k,
                score_threshold=0.30  # Longgar karena sudah difilter by name
            )
            
            if exact_res.points:
                print(f"✅ Jalur A: Exact name match '{extracted_name}' → {len(exact_res.points)} hasil")
                best_confidence = exact_res.points[0].score
                best_source = f"Exact: {exact_res.points[0].payload.get('doc_name', '?')}"
                
                for r in exact_res.points:
                    contexts.append(
                        f"[Data Nutrisi - {r.payload.get('food_name', '?')}]\n"
                        f"{r.payload.get('content', '')}"
                    )
        except Exception as e:
            print(f"Jalur A gagal (mungkin field food_name belum ada): {e}")
    
    # ── JALUR B: Semantic search di Docs (fallback) ──────────────
    if not contexts:
        print("⚠️ Jalur A miss atau tidak ada nama. Beralih ke semantic search Docs...")
        doc_res = qdrant_client.query_points(
            collection_name=collection_docs,
            query=query_vector,
            limit=top_k,
            score_threshold=0.40
        )
        
        for r in doc_res.points:
            best_confidence = r.score
            best_source = f"Semantic: {r.payload.get('doc_name', '?')}"
            contexts.append(
                f"[Data Nutrisi]\n{r.payload.get('content', '')}"
            )
    
    # ── QA Collection: hanya untuk pertanyaan NON-nutrisi ────────
    # (edukasi diabetes, tips diet, penjelasan istilah medis, dll)
    if not contexts and not is_food_query(pertanyaan):
        print("📚 Pertanyaan non-nutrisi, coba QA collection...")
        try:
            qa_res = qdrant_client.query_points(
                query=query_vector,
                limit=5,
                score_threshold=0.75
            )
            if qa_res.points:
                best_confidence = qa_res.points[0].score
                best_source = f"FAQ: {qa_res.points[0].payload.get('doc_name', '?')}"
                for r in qa_res.points:
                    contexts.append(
                        f"[FAQ]\n"
                        f"Q: {r.payload.get('question', '')}\n"
                        f"A: {r.payload.get('answer', '')}"
                    )
        except Exception as e:
            print(f"QA collection error: {e}")
    
    return contexts, best_source, best_confidence


# ────────────────────────────────────────────────────────────────
# STEP 3: SYSTEM PROMPT BARU
# ────────────────────────────────────────────────────────────────

SYSTEM_PROMPT_ANTI_HALUSINASI = """Anda adalah Dr. Predia, ahli gizi virtual PrediaBeat untuk pasien pradiabetes.

ATURAN KERAS:
1. Angka gizi HANYA boleh dari blok [Data Nutrisi] di bawah. DILARANG mengarang.
2. Jika makanan tidak ada di referensi → jawab "data tidak tersedia", STOP.
3. Jika ada beberapa varian → tampilkan maksimal 3 varian teratas, tanya balik.
   KECUALI: jika user pakai kata "biasa", "biasanya", "umumnya" → pilih varian paling generik/sederhana langsung.
4. Jika sudah jelas satu produk → jawab langsung.
5. JANGAN menulis "saya tidak bisa memberikan jawaban" — selalu bantu semampu data.
6. Bahasa santai, pakai emoji, pakai Markdown.

PAHAMI INTENT PERTANYAAN:
- "boleh makan X?" atau "aman ngga X?" → 
  Jawab: boleh/tidak + alasan singkat medis + sebutkan nutrisi PENTING saja 
  (kalori, karbo, gula). JANGAN tampilkan semua angka gizi.
  Format: "Boleh kok makan [X]! Tapi [saran]. Per porsinya mengandung [kalori] kkal, 
  [karbo]g karbo, dan [gula]g gula. [1 kalimat saran untuk pradiabetes] 😊"

- "berapa kalori X?" → jawab kalori saja + konteks singkat.
- "rekomendasikan menu" → buat rencana makan harian.
- "boleh makan X sebanyak Y?" → hitung total, bandingkan target kalori, beri keputusan tegas.

═══════════════════════════════════════════
PANDUAN KLINIS
═══════════════════════════════════════════
- Kontrol porsi agar tidak melebihi target kalori harian pasien.
- Cek pantangan/alergi di profil sebelum rekomendasikan makanan.
- Berikan 1-2 kalimat alasan medis mengapa aman/tidak aman untuk gula darah.
- Tolak dengan sopan jika topik benar-benar di luar gizi/medis.
"""

def generate_rag_answer_v2(pertanyaan: str, context: str, profil_singkat: str, history: list, ollama_client, model: str = "llama3.1:8b"):
    """
    Versi baru generate_rag_answer dengan:
    - System prompt anti-halusinasi
    - Context diformat eksplisit sebagai "referensi terpercaya"
    - Instruksi verifikasi built-in di user prompt
    """
    user_prompt = (
        f"─── PROFIL PASIEN ───\n"
        f"{profil_singkat}\n\n"
        f"─── REFERENSI DATA NUTRISI (SATU-SATUNYA SUMBER YANG BOLEH DIPAKAI) ───\n"
        f"{context}\n\n"
        f"─── PERTANYAAN PASIEN ───\n"
        f"{pertanyaan}\n\n"
        f"INGAT: Jawab HANYA berdasarkan referensi di atas. "
        f"Jika data tidak ada di referensi, katakan 'data tidak tersedia'. "
        f"DILARANG mengarang angka gizi."
    )
    
    messages = [{"role": "system", "content": SYSTEM_PROMPT_ANTI_HALUSINASI}]
    for turn in history:
        messages.append(turn)
    messages.append({"role": "user", "content": user_prompt})
    
    response = ollama_client.chat(
        model=model,
        messages=messages,
        options={"temperature": 0.1, "num_predict": 1024}  # ← temperature 0.1, lebih deterministik
    )
    return response['message']['content'].strip()


# ────────────────────────────────────────────────────────────────
# STEP 4: CONTOH INTEGRASI KE ENDPOINT /tanya
# Gantikan blok "AWAL DARI HYBRID RAG ROUTING" sampai akhir dengan ini
# ────────────────────────────────────────────────────────────────

"""
Di file main.py kamu, ganti blok ini:

    # ── AWAL DARI HYBRID RAG ROUTING ──────────────────────────────
    contexts = []
    ... (semua kode routing lama)
    # ── AKHIR DARI HYBRID RAG ROUTING ─────────────────────────────

    if not contexts:
        return {...}

    full_context = "\n\n---\n\n".join(contexts)
    jawaban = generate_rag_answer(...)

Jadi:

    from app.rag_patch import smart_retrieve, generate_rag_answer_v2  # import dari file ini

    contexts, best_source, best_confidence = smart_retrieve(
        qdrant_client=qdrant,
        collection_docs=COLLECTION_DOCS,
        collection_qa=COLLECTION_QA,
        query_vector=query_vector,
        pertanyaan=request.pertanyaan,
    )

    if not contexts:
        return {
            "jawaban": "Maaf, informasi mengenai hal tersebut belum tersedia dalam database saya.",
            "sumber": "-",
            "confidence": 0.0
        }

    full_context = "\\n\\n---\\n\\n".join(contexts)
    jawaban = generate_rag_answer_v2(
        pertanyaan=request.pertanyaan,
        context=full_context,
        profil_singkat=profil_singkat,
        history=user_history,
        ollama_client=ollama_client,
        model="llama3.1:8b"
    )
"""


# ────────────────────────────────────────────────────────────────
# STEP 5: PERBAIKAN QA PAIRS (opsional tapi recommended)
# Generate ulang QA pairs yang lebih informatif
# ────────────────────────────────────────────────────────────────

def rebuild_qa_pairs(chunks_path: str, output_path: str):
    """
    Generate ulang QA pairs dari chunks dengan format jawaban yang lengkap.
    
    Masalah lama: answer = "311.1" (hanya angka, tanpa konteks makanan apa)
    Format baru: answer = "ABC saus sambal memiliki 311.1 kalori per 100g, 
                           dengan 66.7g karbohidrat, 55.6g gula. Kategori: Lauk."
    
    QA dengan format lengkap = LLM bisa verifikasi nama + angka sekaligus.
    """
    import json
    
    with open(chunks_path) as f:
        chunks = json.load(f)
    
    qa_pairs = []
    
    for chunk in chunks:
        if chunk.get("type") != "nutrisi":
            continue
        
        content = chunk["content"]
        
        # Parse fields dari content string
        fields = {}
        for pair in content.split(", "):
            if ": " in pair:
                k, v = pair.split(": ", 1)
                fields[k.strip()] = v.strip()
        
        nama = fields.get("Nama Makanan", "")
        if not nama:
            continue
        
        kalori = fields.get("Kalori (Kal)", "?")
        karbo = fields.get("Karbohidrat (g)", "?")
        gula = fields.get("Gula (g)", "?")
        protein = fields.get("Protein (g)", "?")
        lemak = fields.get("Lemak (g)", "?")
        serat = fields.get("Serat (g)", "?")
        kategori = fields.get("Kategori", "?")
        porsi = fields.get("Porsi (g)", "100")
        glycemic = fields.get("Glycemic Risk", "")
        
        # Buat answer yang lengkap dan self-contained
        answer_full = (
            f"**{nama}** ({kategori}) per {porsi}g mengandung: "
            f"{kalori} kalori, {karbo}g karbohidrat (gula: {gula}g), "
            f"{protein}g protein, {lemak}g lemak, {serat}g serat."
        )
        if glycemic:
            answer_full += f" Risiko glikemik: **{glycemic}**."
        
        # Beberapa variasi pertanyaan per makanan
        qa_pairs.extend([
            {
                "question": f"Berapa kalori {nama}?",
                "answer": answer_full,
                "chunk_id": chunk["chunk_id"],
                "food_name": nama.lower()
            },
            {
                "question": f"Apa kandungan gizi {nama}?",
                "answer": answer_full,
                "chunk_id": chunk["chunk_id"],
                "food_name": nama.lower()
            },
            {
                "question": f"Apakah {nama} aman untuk penderita diabetes?",
                "answer": answer_full + (
                    " ⚠️ Risiko glikemik tinggi, sebaiknya batasi konsumsinya." 
                    if glycemic == "high" else 
                    " Kandungan gula relatif rendah, bisa dikonsumsi dengan porsi terkontrol."
                ),
                "chunk_id": chunk["chunk_id"],
                "food_name": nama.lower()
            },
        ])
    
    with open(output_path, "w") as f:
        json.dump(qa_pairs, f, ensure_ascii=False, indent=2)
    
    print(f"✅ QA pairs baru: {len(qa_pairs)} pasang, disimpan ke {output_path}")
    return qa_pairs


# ────────────────────────────────────────────────────────────────
# ENTRYPOINT: jalankan patch saat startup
# ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Test rebuild QA
    rebuild_qa_pairs(
        chunks_path="/path/to/chunks.json",
        output_path="/path/to/qa_pairs_v2.json"
    )