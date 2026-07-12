import os
import io
import json
import re
import fitz
import time
import gc
import csv
import hashlib
import shutil
import requests
import threading  # <--- TAMBAHKAN INI
from pathlib import Path
from PIL import Image
import torch
from transformers import AutoProcessor, Qwen2VLForConditionalGeneration
from mineru_vl_utils import MinerUClient
from sentence_transformers import SentenceTransformer
from qdrant_client import QdrantClient
from qdrant_client.models import Filter, FieldCondition, MatchValue
from qdrant_client.models import Distance, VectorParams, PointStruct, Filter, FieldCondition, MatchValue
from ollama import Client

def deterministic_id(key: str) -> int:
    """Generate deterministic integer ID from string to prevent Qdrant duplicates."""
    return int(hashlib.md5(key.encode()).hexdigest()[:16], 16)

# ── CONFIG ────────────────────────────────────────────────────

TEXT_MODEL = "llama3.1:8b"
VL_MODEL  = "llava"

# HAPUS BARIS INI KARENA SUDAH DIGANTI ACTIVE_OLLAMA:
# OLLAMA_HOST = 'http://localhost:11434'
# client = Client(host=OLLAMA_HOST)

QDRANT_URL     = "http://localhost:6333"

COLLECTION_DOCS = "collection_documents"
COLLECTION_QA   = "collection_qa"
EMBEDDING_MODEL = "BAAI/bge-m3"
VECTOR_SIZE     = 1024

QA_PER_LAYER = {0: 1, 1: 2, 2: 3, 3: 5}

PDF_INPUT_FOLDER = r"D:\Project Magang\Testing\data\DocumentPradiabet"
OUTPUT_BASE      = r"D:\Project Magang\Testing\data\output"

PROCESSED_FOLDER = r"D:\Project Magang\Testing\data\DocumentPradiabet_Done"
os.makedirs(PROCESSED_FOLDER, exist_ok=True)

HPC_OLLAMA_URL = "http://localhost:11435"
LOCAL_OLLAMA_URL = "http://localhost:11434"

def get_active_url(hpc_url, local_url, service_name):
    try:
        print(f"Mencoba menghubungi {service_name} di HPC (via Tunnel)...")
        requests.get(hpc_url, timeout=2)
        print(f"✅ BERHASIL: Menggunakan tenaga HPC untuk {service_name}!")
        return hpc_url
    except requests.exceptions.RequestException:
        print(f"⚠️ GAGAL: Terputus dari HPC. Beralih ke Lokal!")
        return local_url

# OTOMATIS MEMILIH HPC JIKA TERHUBUNG
ACTIVE_OLLAMA = get_active_url(HPC_OLLAMA_URL, LOCAL_OLLAMA_URL, "Ollama (Testing)")
client = Client(host=ACTIVE_OLLAMA) # LLAMA3.1 & LLAVA PAKAI HPC

# ── TAMBAHAN: THREAD PENJAGA TUNNEL HPC ──────────────────────
def keep_hpc_alive(url, interval=30):
    """Fungsi ini berjalan di background, mengetuk HPC setiap X detik agar SSH tidak timeout."""
    while True:
        try:
            # Kirim request sangat singkat ke Ollama HPC
            requests.get(url, timeout=2)
        except:
            pass # Abaikan jika gagal, yang penting ada trafik jaringan
        time.sleep(interval)

# Jalankan thread penjaga hanya jika berhasil terhubung ke HPC
if "11435" in ACTIVE_OLLAMA:
    keeper_thread = threading.Thread(target=keep_hpc_alive, args=(ACTIVE_OLLAMA, 30), daemon=True)
    keeper_thread.start()
    print("🛡️ Thread Penjaga Tunnel HPC aktif (mengetuk setiap 30 detik)...")
# ── AKHIR TAMBAHAN ───────────────────────────────────────────

os.makedirs(PDF_INPUT_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_BASE, exist_ok=True)

# ── INIT QDRANT ─────────────────────────────────────────────
print("Connecting to Qdrant...")
qdrant = QdrantClient(url=QDRANT_URL, timeout=60)


# ════════════════════════════════════════════════════════════════
# TAHAP 1: PARSING (MinerU + LLaVA)
# ════════════════════════════════════════════════════════════════

def load_mineru():
    print("\n[TAHAP 1] Loading MinerU model...")
    m = Qwen2VLForConditionalGeneration.from_pretrained(
        "opendatalab/MinerU2.5-2509-1.2B",
        dtype="auto",
        device_map="auto"
    )
    p = AutoProcessor.from_pretrained(
        "opendatalab/MinerU2.5-2509-1.2B",
        use_fast=True
    )
    c = MinerUClient(backend="transformers", model=m, processor=p)
    return m, p, c

def unload_mineru(model, processor):
    print("[TAHAP 1] Unloading MinerU model (membebaskan VRAM)...")
    del model
    del processor
    torch.cuda.empty_cache()
    gc.collect()    
    print("[TAHAP 1] VRAM freed!\n")

def describe_image(cropped_img):
    buffer = io.BytesIO()
    cropped_img.save(buffer, format="PNG")
    img_bytes = buffer.getvalue()

    response = client.chat(
        model=VL_MODEL,
        messages=[{
            'role': 'user',
            'content': 'Jelaskan gambar atau grafik ini dengan jelas untuk keperluan penelitian. Gunakan Bahasa Indonesia.',
            'images': [img_bytes]
        }]
    )
    return response['message']['content']

def parse_page(mineru_client, page_image, page_num, images_dir):
    img_w, img_h = page_image.size
    elements     = mineru_client.two_step_extract(page_image)
    img_counter  = 1
    parsed       = []

    for el in elements:
        t       = el.get('type')
        content = el.get('content') or ''
        bbox    = el.get('bbox')

        item = {
            "type"    : t,
            "content" : content,
            "page"    : page_num,
            "bbox"    : bbox
        }

        if t == 'image' and bbox:
            x1 = int(bbox[0] * img_w)
            y1 = int(bbox[1] * img_h)
            x2 = int(bbox[2] * img_w)
            y2 = int(bbox[3] * img_h)

            cropped      = page_image.crop((x1, y1, x2, y2))
            img_filename = os.path.join(images_dir, f"page{page_num}_img{img_counter}.png")
            cropped.save(img_filename)

            print(f"    Describing page {page_num} image {img_counter}...")
            description        = describe_image(cropped)
            item['content']    = description
            item['image_path'] = img_filename
            img_counter += 1

        parsed.append(item)

    return parsed

def elements_to_markdown(elements):
    md = []
    for el in elements:
        t       = el.get('type')
        content = el.get('content') or ''
        page    = el.get('page')

        if t == 'title':
            md.append(f"## {content}")
        elif t == 'header':
            md.append(f"_{content}_")
        elif t == 'text':
            md.append(content)
        elif t == 'table_caption':
            md.append(f"**{content}**")
        elif t == 'table':
            md.append(content)
        elif t == 'image_caption':
            md.append(f"*{content}*")
        elif t == 'image':
            img_path = el.get('image_path', '')
            md.append(f"![image_p{page}]({img_path})")
            md.append(f"> **Figure description:** {content}")

        md.append("")
    return "\n".join(md)

def local_text_chat(prompt, temperature=0.2):
    """Chat menggunakan Ollama lokal (llama3.1:8b)."""
    response = client.chat(
        model=TEXT_MODEL,
        messages=[{
            "role": "user",
            "content": prompt
        }],
        options={
            "temperature": temperature
        }
    )
    raw = response['message']['content'].strip()
    return raw

def summarize_text(text):
    response = client.chat(
        model=TEXT_MODEL,
        messages=[{
            "role": "user",
            "content": (
                "Buatkan ringkasan dokumen berikut dalam satu paragraf yang komprehensif, "
                "mencakup topik utama, temuan, dan poin-poin penting. "
                "Gunakan Bahasa Indonesia.\n\n" + text
            )
        }]
    )
    return response['message']['content'].strip()

def build_hierarchical_chunks(elements, doc_name):
    chunks   = []
    chunk_id = 0

    for el in elements:
        content = el.get('content', '') or ''
        if not content.strip():
            continue
        chunks.append({
            "chunk_id"  : f"{doc_name}_L0_{chunk_id}",
            "layer"     : 0,
            "type"      : el.get('type'),
            "page"      : el.get('page'),
            "content"   : content,
            "image_path": el.get('image_path', None)
        })
        chunk_id += 1

    section_chunks  = []
    current_section = {"title": "Introduction", "contents": [], "pages": set()}

    for el in elements:
        content = el.get('content', '') or ''
        if not content.strip():
            continue
        if el.get('type') == 'title':
            if current_section['contents']:
                section_chunks.append(current_section)
            current_section = {
                "title"   : content,
                "contents": [],
                "pages"   : set()
            }
        else:
            current_section['contents'].append(content)
            current_section['pages'].add(el.get('page'))

    if current_section['contents']:
        section_chunks.append(current_section)

    for sec in section_chunks:
        combined = f"{sec['title']}\n\n" + "\n".join(sec['contents'])
        chunks.append({
            "chunk_id": f"{doc_name}_L1_{chunk_id}",
            "layer"   : 1,
            "type"    : "section",
            "pages"   : sorted(sec['pages']),
            "title"   : sec['title'],
            "content" : combined
        })
        chunk_id += 1

    pages = {}
    for el in elements:
        p       = el.get('page')
        content = el.get('content', '') or ''
        if not content.strip():
            continue
        pages.setdefault(p, []).append(content)

    for page_num, contents in sorted(pages.items()):
        combined = "\n".join(contents)
        chunks.append({
            "chunk_id": f"{doc_name}_L2_{chunk_id}",
            "layer"   : 2,
            "type"    : "page",
            "page"    : page_num,
            "content" : combined
        })
        chunk_id += 1

    print("  Generating document summary (Layer 3)...")
    all_text = " ".join([
        el.get('content', '') for el in elements
        if el.get('content') and el.get('type') in ['text', 'title']
    ])[:8000]

    summary = summarize_text(all_text)
    chunks.append({
        "chunk_id": f"{doc_name}_L3_{chunk_id}",
        "layer"   : 3,
        "type"    : "document_summary",
        "content" : summary
    })

    return chunks

def parse_csv(csv_path, doc_name):
    import io, re, hashlib, csv  # ditambah csv

    def fix_csv_encoding(raw_text):
        lines = raw_text.splitlines()
        fixed = []
        for line in lines:
            if line.startswith('"'):
                line = line[1:]
            if line.endswith('"') and not line.endswith('""'):
                line = line[:-1]
            line = line.replace('""', '"')
            fixed.append(line)
        return "\n".join(fixed)

    def clean_value(val):
        val = val.strip()
        if not val:
            return val
        val = re.sub(r'^(\d+\.?\d*)\s+g$', r'\1', val)
        val = re.sub(r'^(\d+),(\d+)$', r'\1.\2', val)
        # Bersihkan sisa huruf "g" yang berdiri sendiri (data kotor di kolom air/abu)
        if val.lower() == 'g':
            return ''
        return val

    # ── GANTI: Key disesuaikan dengan header CSV baru ─────────
    FIELD_LABELS = {
        "Kode"             : "Kode",
        "Nama Makanan"     : "Nama Makanan",
        "Kategori gambar"  : "Kategori Gambar",
        "Kategori"         : "Kategori",
        "Cara Masak"       : "Cara Masak",
        "Porsi (g)"        : "Porsi (g)",
        "Kalori (Kal)"     : "Kalori (Kal)",
        "Karbohidrat (g)"  : "Karbohidrat (g)",
        "Serat (g)"        : "Serat (g)",
        "Gula (g)"         : "Gula (g)",
        "Protein (g)"      : "Protein (g)",
        "Lemak"            : "Lemak (g)",       # di CSV baru header-nya tanpa (g)
        "air_g"            : "Air (g)",
        "abu_g"            : "Abu (g)",
        "Glycemic Index"   : "Glycemic Index",
        "Glycemic Load"    : "Glycemic Load",
        "Net Karbo"        : "Net Karbo",
        "Magnesium"        : "Magnesium",
        "Chromium"         : "Chromium",
        "Zinc"             : "Zinc",
        "Sodium"           : "Sodium",
        "Glycemic Risk"    : "Glycemic Risk",
    }
    
    # ── GANTI: Exclude kolom teknis dan redundan ──────────────
    EXCLUDE_FIELDS = {"hardlink", "Kategori gambar"}

    with open(csv_path, "r", encoding="utf-8") as f:
        raw = f.read()

    fixed_text = fix_csv_encoding(raw)
    reader     = csv.DictReader(io.StringIO(fixed_text))
    headers    = reader.fieldnames or []

    chunks       = []
    chunk_id     = 0
    kategori_counts = {}

    for row in reader:
        # ── GANTI: dari "nama_makanan" → "Nama Makanan" ───────
        nama = row.get("Nama Makanan", "").strip() if row.get("Nama Makanan") else ""
        if not nama:
            continue

        parts = []
        for h in headers:
            if h in EXCLUDE_FIELDS or h is None:
                continue
            raw_val = row.get(h) or ""
            raw_val = str(raw_val).strip()
            if not raw_val:
                continue
            val = clean_value(raw_val)
            if not val:
                continue
            label = FIELD_LABELS.get(h, h.replace("_", " ").title())
            parts.append(f"{label}: {val}")

        content = ", ".join(parts)
        if not content.strip():
            continue

        # ── GANTI: dari "kategori_umum" → "Kategori" ──────────
        kat = row.get("Kategori", "").strip() if row.get("Kategori") else ""
        if kat:
            kategori_counts[kat] = kategori_counts.get(kat, 0) + 1

        chunks.append({
            "chunk_id"  : f"{doc_name}_L0_{chunk_id}",
            "layer"     : 0,
            "type"      : "nutrisi",
            "page"      : None,
            "content"   : content,
            "image_path": None
        })
        chunk_id += 1

    print(f"  Generating summary CSV (Layer 3)...")
    sample_text = " ".join([c["content"] for c in chunks[:50]])[:8000]
    summary     = summarize_text(sample_text)

    chunks.append({
        "chunk_id"  : f"{doc_name}_L3_{chunk_id}",
        "layer"     : 3,
        "type"      : "document_summary",
        "page"      : None,
        "content"   : summary,
        "image_path": None
    })

    return chunks


# ════════════════════════════════════════════════════════════════
# TAHAP 2: EMBEDDING + SIMPAN KE QDRANT
# ════════════════════════════════════════════════════════════════

def load_embedder():
    print("\n[TAHAP 2] Loading BGE-M3 embedding model...")
    embedder = SentenceTransformer(EMBEDDING_MODEL)
    print("[TAHAP 2] BGE-M3 ready!\n")
    return embedder

def unload_embedder(embedder):
    print("[TAHAP 2] Unloading BGE-M3 (membebaskan VRAM)...")
    del embedder
    torch.cuda.empty_cache()
    gc.collect()
    print("[TAHAP 2] VRAM freed!\n")

def embed(embedder, text):
    return embedder.encode(text, normalize_embeddings=True).tolist()

def setup_collections():
    existing = [c.name for c in qdrant.get_collections().collections]
    for col_name in [COLLECTION_DOCS, COLLECTION_QA]:
        if col_name not in existing:
            qdrant.create_collection(
                collection_name=col_name,
                vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE)
            )
            print(f"  Created collection: {col_name}")
        else:
            print(f"  Collection sudah ada: {col_name}")

# ── FUNGSI BARU: HAPUS DATA LAMA BERDASARKAN NAMA DOKUMEN ──
def clear_doc_from_qdrant(doc_name):
    """Menghapus semua data (Docs & QA) dari Qdrant yang memiliki doc_name tertentu."""
    print(f"  Memeriksa dan membersihkan data lama untuk: {doc_name}...")
    
    filter_doc = Filter(
        must=[
            FieldCondition(
                key="doc_name",
                match=MatchValue(value=doc_name)
            )
        ]
    )
    
    # Hapus dari collection_documents
    qdrant.delete(
        collection_name=COLLECTION_DOCS,
        points_selector=filter_doc
    )
    
    # Hapus dari collection_qa
    qdrant.delete(
        collection_name=COLLECTION_QA,
        points_selector=filter_doc
    )
    print(f"  Data lama {doc_name} berhasil dihapus (jika ada)!")

def store_documents(embedder, chunks, doc_name):
    points = []
    for chunk in chunks:
        content = chunk.get("content", "")
        if not content.strip():
            continue
        points.append(PointStruct(
            id      = deterministic_id(chunk["chunk_id"]),
            vector  = embed(embedder, content),
            payload = {
                "chunk_id"  : chunk["chunk_id"],
                "doc_name"  : doc_name,
                "layer"     : chunk["layer"],
                "type"      : chunk["type"],
                "page"      : chunk.get("page"),
                "pages"     : chunk.get("pages"),
                "title"     : chunk.get("title"),
                "content"   : content,
                "image_path": chunk.get("image_path")
            }
        ))

    batch_size = 10
    for i in range(0, len(points), batch_size):
        batch   = points[i:i + batch_size]
        retries = 3
        for attempt in range(retries):
            try:
                qdrant.upsert(collection_name=COLLECTION_DOCS, points=batch)
                print(f"    Docs batch {i//batch_size + 1} uploaded ({len(batch)} points)")
                time.sleep(0.3)
                break
            except Exception as e:
                print(f"    Retry {attempt+1}/{retries} — {e}")
                time.sleep(2)


# ════════════════════════════════════════════════════════════════
# TAHAP 3: QA GENERATION
# ════════════════════════════════════════════════════════════════

def generate_qa(chunk_content, n_pairs, doc_name):
    prompt = f"""Kamu adalah pakar pembuat soal ujian. Berdasarkan teks berikut, buatkan {n_pairs} pasang pertanyaan dan jawaban dalam Bahasa Indonesia.

Aturan:
- Pertanyaan harus bisa dijawab HANYA dari teks yang diberikan
- Jawaban harus spesifik dan informatif
- Jangan membuat pertanyaan yang terlalu umum
- Format output HARUS berupa JSON array seperti ini:
[
  {{"question": "...", "answer": "..."}},
  {{"question": "...", "answer": "..."}}
]
Hanya output JSON saja, tanpa teks lain.

Teks:
{chunk_content[:3000]}"""

    try:
        raw = local_text_chat(prompt, temperature=0.1)

        # Bersihkan tag <think*> jika ada
        raw = re.sub(r'<think\>.*?</think\>', '', raw, flags=re.DOTALL).strip()

        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]

        return json.loads(raw)

    except Exception as e:
        print(f"    Warning: QA generation gagal — {e}")
        return []

def store_qa(embedder, chunks, doc_name):
    qa_points = []
    qa_id     = 0
    all_qa    = []

    for chunk in chunks:
        layer   = chunk.get("layer", 0)
        content = chunk.get("content", "")
        if not content.strip():
            continue

        n_pairs = QA_PER_LAYER.get(layer, 1)
        print(f"    Generating {n_pairs} QA untuk layer {layer} — {chunk['chunk_id']}...")

        qa_pairs = generate_qa(content, n_pairs, doc_name)
        time.sleep(0.5)

        for qa in qa_pairs:
            question = qa.get("question", "").strip()
            answer   = qa.get("answer", "").strip()
            if not question or not answer:
                continue

            qa_points.append(PointStruct(
                id      = deterministic_id(f"{doc_name}_qa_{qa_id}"),
                vector  = embed(embedder, question),
                payload = {
                    "qa_id"      : f"{doc_name}_qa_{qa_id}",
                    "doc_name"   : doc_name,
                    "chunk_id"   : chunk["chunk_id"],
                    "layer"      : layer,
                    "question"   : question,
                    "answer"     : answer,
                    "source_page": chunk.get("page") or chunk.get("pages")
                }
            ))
            all_qa.append({
                "question" : question,
                "answer"   : answer,
                "chunk_id" : chunk["chunk_id"],
                "layer"    : layer
            })
            qa_id += 1

    batch_size = 10
    for i in range(0, len(qa_points), batch_size):
        batch   = qa_points[i:i + batch_size]
        retries = 3
        for attempt in range(retries):
            try:
                qdrant.upsert(collection_name=COLLECTION_QA, points=batch)
                print(f"    QA batch {i//batch_size + 1} uploaded ({len(batch)} points)")
                time.sleep(0.3)
                break
            except Exception as e:
                print(f"    Retry {attempt+1}/{retries} — {e}")
                time.sleep(2)

    return qa_id, all_qa


# ════════════════════════════════════════════════════════════════
# MAIN
# ════════════════════════════════════════════════════════════════

pdf_files = [f for f in os.listdir(PDF_INPUT_FOLDER) if f.lower().endswith('.pdf')]
csv_files = [f for f in os.listdir(PDF_INPUT_FOLDER) if f.lower().endswith('.csv')]

if not pdf_files and not csv_files:
    print("Tidak ada PDF atau CSV...")
else:
    if pdf_files:
        mineru_model, mineru_processor, mineru_client = load_mineru()

        for pdf_file in pdf_files:
            pdf_path = os.path.join(PDF_INPUT_FOLDER, pdf_file)
            doc_name = os.path.splitext(pdf_file)[0]
            print(f"\n{'='*60}")
            print(f"[TAHAP 1] Parsing: {pdf_file}")
            print(f"{'='*60}")

            doc_output_dir = os.path.join(OUTPUT_BASE, doc_name)
            images_dir     = os.path.join(doc_output_dir, "images")
            os.makedirs(doc_output_dir, exist_ok=True)
            os.makedirs(images_dir, exist_ok=True)

            pdf_doc      = fitz.open(pdf_path)
            all_elements = []

            for page_num in range(len(pdf_doc)):
                print(f"  Parsing page {page_num + 1}/{len(pdf_doc)}...")
                page     = pdf_doc[page_num]
                pix      = page.get_pixmap(dpi=150)
                img_data = pix.tobytes("png")
                page_img = Image.open(io.BytesIO(img_data))

                page_elements = parse_page(mineru_client, page_img, page_num + 1, images_dir)
                all_elements.extend(page_elements)

            pdf_doc.close()

            print("  Building markdown...")
            markdown = elements_to_markdown(all_elements)
            md_path  = os.path.join(doc_output_dir, "output.md")
            with open(md_path, "w", encoding="utf-8") as f:
                f.write(markdown)
            print(f"  Saved: {md_path}")

            print("  Building hierarchical chunks...")
            chunks    = build_hierarchical_chunks(all_elements, doc_name)
            json_path = os.path.join(doc_output_dir, "chunks.json")
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(chunks, f, indent=2, ensure_ascii=False)
            print(f"  Saved: {json_path}")
            print(f"  Total chunks: {len(chunks)} (L0: elemen, L1: section, L2: page, L3: summary)")

        unload_mineru(mineru_model, mineru_processor)

    for csv_file in csv_files:
        csv_path = os.path.join(PDF_INPUT_FOLDER, csv_file)
        doc_name = os.path.splitext(csv_file)[0]
        print(f"\n{'='*60}")
        print(f"[TAHAP 1] Parsing CSV: {csv_file}")
        print(f"{'='*60}")

        doc_output_dir = os.path.join(OUTPUT_BASE, doc_name)
        os.makedirs(doc_output_dir, exist_ok=True)

        chunks    = parse_csv(csv_path, doc_name)
        json_path = os.path.join(doc_output_dir, "chunks.json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(chunks, f, indent=2, ensure_ascii=False)
        print(f"  Saved: {json_path}")
        print(f"  Total chunks: {len(chunks)}")

    # ── TAHAP 2 & 3: Embedding + QA per dokumen ───────────────
    setup_collections()
    embedder = load_embedder()

    all_files = pdf_files + csv_files
    for doc_file in all_files:
        doc_name  = os.path.splitext(doc_file)[0]
        json_path = os.path.join(OUTPUT_BASE, doc_name, "chunks.json")

        if not os.path.exists(json_path):
            print(f"Skipping {doc_name} — chunks.json tidak ditemukan")
            continue

        print(f"\n{'='*60}")
        print(f"[TAHAP 2] Embedding & storing: {doc_name}")
        print(f"{'='*60}")

        # >>> PENTING: HAPUS DATA LAMA DULU SEBELUM INSERT BARU <<<
        clear_doc_from_qdrant(doc_name)
        time.sleep(1)  # Kasih jeda 1 detik agar Qdrant selesai memproses penghapusan

        with open(json_path, "r", encoding="utf-8") as f:
            chunks = json.load(f)

        print("  Storing documents ke Qdrant...")
        store_documents(embedder, chunks, doc_name)

        print("  Generating & storing QA ke Qdrant...")
        total_qa, all_qa = store_qa(embedder, chunks, doc_name)

        qa_path = os.path.join(OUTPUT_BASE, doc_name, "qa_pairs.json")
        with open(qa_path, "w", encoding="utf-8") as f:
            json.dump(all_qa, f, indent=2, ensure_ascii=False)
        print(f"  Saved: {qa_path}")
        print(f"  Total QA pairs: {total_qa}")

        src_path = os.path.join(PDF_INPUT_FOLDER, doc_file)
        dst_path = os.path.join(PROCESSED_FOLDER, doc_file)
        if os.path.exists(src_path):
            shutil.move(src_path, dst_path)
            print(f"  ✅ File {doc_file} berhasil dipindahkan ke folder Done.")

    unload_embedder(embedder)

print("\nSelesai!")