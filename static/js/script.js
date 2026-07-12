// ── CHATBOT LOGIC ───────────────────────────────────────
async function sendMessage() {
    const input = document.getElementById('chat-input');
    const text = input.value.trim();
    if (!text) return;

    // 1. Tambah pesan user ke layar
    addMessage(text, 'user');
    input.value = '';

    // 2. Simulasi loading
    const chatHistory = document.getElementById('chat-history');
    const loadingId = 'loading-' + Date.now();
    chatHistory.innerHTML += `<div id="${loadingId}" class="message bot" style="color:#aaa; font-style:italic;">Dr. Predia sedang mengetik...</div>`;
    chatHistory.scrollTop = chatHistory.scrollHeight;

    try {
        // KIRIM KE BACKEND (Pastikan endpoint ini ada di main.py)
        // Jika endpoint /tanya belum ada, kode ini akan error.
        // Untuk demo, kita bisa mock dulu atau buat endpointnya.
        const response = await fetch('/tanya', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ pertanyaan: text })
        });
        
        const data = await response.json();

        // Hapus loading
        const loadingEl = document.getElementById(loadingId);
        if(loadingEl) loadingEl.remove();

        // Tampilkan jawaban
        addMessage(data.jawaban || "Maaf, server sedang sibuk.", 'bot');

    } catch (error) {
        const loadingEl = document.getElementById(loadingId);
        if(loadingEl) loadingEl.remove();
        
        // Fallback Mock Response jika backend belum siap
        addMessage("Saya mengerti. Untuk saat ini, silakan cek dashboard untuk rekomendasi diet spesifik Anda.", 'bot');
    }
}

function addMessage(text, sender) {
    const history = document.getElementById('chat-history');
    const div = document.createElement('div');
    div.className = `message ${sender}`;
    
    // Format simple (Bold)
    let formattedText = text.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>').replace(/\n/g, '<br>');
    
    div.innerHTML = formattedText;
    history.appendChild(div);
    history.scrollTop = history.scrollHeight;
}

function handleChatEnter(e) {
    if (e.key === 'Enter') sendMessage();
}