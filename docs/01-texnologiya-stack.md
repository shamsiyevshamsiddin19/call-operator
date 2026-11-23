# 1-BOSQICH: TEXNOLOGIYA STACK QARORLARI (ADR)

> Har bir tanlov **asoslangan** va **rad etilgan alternativalar** sababi bilan yozilgan.
> "Mix" — moda uchun emas, har qatlamda o'z vazifasiga eng mos vosita uchun.
> Tasdiqlangan sana: 2026-11-23.

---

## ADR-001: Ovoz AI — Gemini 3.8 Live (native audio)

**Qaror:** `gemini-3.8-live` — speech-to-speech native audio model.

**Nega hal qiluvchi sabab — o'zbek tili.** Gemini Live API 99 tilni qo'llab-quvvatlaydi va
**o'zbek (`uz`) rasmiy ro'yxatda**: native audio dialog, live transcription (`uz-UZ`), live translation.
OpenAI Realtime (`gpt-realtime-2.1`) hujjatlarida o'zbek tasdiqlanmagan.

> **O'zbekcha ishlamaydigan mukammal SIP — foydasiz.** Til qo'llab-quvvatlashi boshqa
> barcha texnik afzallikdan ustun turadi.

**Arxitektura ustunligi:** uch bosqichli `STT → LLM → TTS` zanjiri **bitta model** bilan almashtiriladi:
- kechikish byudjeti 1500 ms → **~600 ms**
- VAD model ichida (tashqi Silero kerak emas)
- tool-calling model ichida → biznes amallari to'g'ridan-to'g'ri
- intonatsiya va emotsiya saqlanadi (matnga aylantirishda yo'qolmaydi)

**Rad etilgan alternativalar:**

| Variant | Nega rad etildi |
| :--- | :--- |
| OpenAI `gpt-realtime-2.1` | O'zbek tili tasdiqlanmagan. Native SIP (`sip.api.openai.com`) kuchli afzallik, lekin til to'sig'i hal qiluvchi. **Interfeys ortida qoldiriladi** — kelajakda rus tili yoki A/B test uchun. |
| Mohir.ai | Tashqi tarmoqdan DNS javob bermaydi; API hujjati ommaviy emas. |
| Aisha / NeuronAI / Muxlisa (UZ) | Realtime bor, lekin uch bosqichli zanjir (latency ↑), tool-calling yo'q, API shartnoma orqali ochiladi. Fallback sifatida qoladi. |
| Whisper + mahalliy TTS | O'zbekda aniqligi o'rtacha; zanjir latency'si byudjetga sig'maydi. |

### Gemini Live'ning 4 ta ma'lum kamchiligi va bizning himoyamiz

Bu kamchiliklar **bilib turib** qabul qilinadi va arxitekturada qoplanadi (Anti-Vibe tamoyili —
ma'lum muammoni yashirmaslik, unga qarshi himoya qurish):

| № | Kamchilik | Oqibat | Bizning himoyamiz |
| :-: | :--- | :--- | :--- |
| 1 | **SIP endpoint yo'q** | Telefon tarmog'iga to'g'ridan-to'g'ri ulanmaydi | Asterisk + AudioSocket bridge (ADR-002) — baribir kerak edi, chunki UZ SIP trunk mahalliy |
| 2 | **Sessiya 15 daq, ulanish ~10 daq** (`GoAway`) | Uzun qo'ng'iroq uziladi | `contextWindowCompression` (sliding window) + `sessionResumption` token (2 soat amal qiladi). **Majburiy integration test** — 20 daqiqalik qo'ng'iroq uzilmasligi |
| 3 | **Truncation API yo'q** | Barge-in'dan keyin model mijoz eshitmagan so'zni "aytdim" deb o'ylaydi | Bridge o'ynatilgan audio baytini hisoblaydi → to'xtash nuqtasini client-side note bilan kontekstga yozadi |
| 4 | **Chiqish 24 kHz, Asterisk 8 kHz** | Resampling bizda | `soxr` (libsoxr) — 8↔16↔24 kHz, sifatli va tez |

> ⚠️ Ochiq kodli `AVA-AI-Voice-Agent-for-Asterisk` loyihasida aynan **2-kamchilik ishlov berilmagani
> uchun** qo'ng'iroqlar 10 daqiqada uzilib qolgan (GitHub issue #756). Biz bu xatoni takrorlamaymiz —
> `sessionResumption` va `GoAway` ishlovi **MVP ning birinchi testiga** kiradi.

---

## ADR-002: Telefoniya — Asterisk 22 LTS + ARI + AudioSocket

**Qaror:** Asterisk 22, qo'ng'iroq boshqaruvi `ARI` (REST + WebSocket), audio oqimi `AudioSocket` (TCP, xom PCM).

**Nega:** O'zbekistonda telefon raqami **mahalliy SIP trunk** orqali olinadi (Uztelecom / Beeline Business).
Twilio UZ raqam bermaydi. Asterisk — ochiq kodli SIP standarti, har qanday trunk bilan ishlaydi.
`AudioSocket` 20 ms frame'larda xom `16-bit PCM` beradi — AI uchun aynan kerakli format,
transkodlash yo'qotishisiz.

**Rad etilganlar:** FreeSWITCH (kuchli, lekin ESL murakkab, hamjamiyat kichik) · Kamailio (faqat SIP
proxy, media yo'q) · Twilio/Vonage (UZ raqam yo'q) · LiveKit SIP (yaxshi, lekin butun media-server
og'irligi bizga kerak emas).

---

## ADR-003: Ovoz bridge — Python 3.12 + asyncio + uvloop

**Qaror:** Ovoz worker Python'da, `asyncio` + `uvloop`.

**Nega Go yoki Rust emas:** Yuklama **I/O-bound, CPU-bound emas** — AI tashqi WebSocket'da ishlaydi.
50 parallel qo'ng'iroq = 2500 frame/s, `asyncio` + `uvloop` buni erkin ko'taradi. Resampling `soxr`
(C kutubxona) ichida bajariladi. Gemini SDK va butun AI ekosistemasi Python'da eng to'liq.

> Go/Rust kiritish bu yuklamada **o'lchanadigan foyda bermaydi**, lekin ishlab chiqish tezligini
> 2–3 barobar sekinlashtiradi va ikkinchi til bilan birga ikkinchi CI, ikkinchi test infratuzilmasi
> keladi. Bu **Hooker 2 (KISS) buzilishi** — asossiz murakkashtirish. 500+ parallel qo'ng'iroqda
> yechim worker'ni **gorizontal ko'paytirish**, tilni o'zgartirish emas.

---

## ADR-004: Biznes yadro — Django 5.2 LTS + DRF

**Qaror:** Django 5.2 LTS, biznes mantiq va ma'lumotlar yaxlitligi uchun.

**Nega:** Loyiha konstitutsiyasi (`SYSTEM_PROMPT.txt`) to'g'ridan-to'g'ri Django terminologiyasida
yozilgan: `CheckConstraint`, `select_for_update()`, `ForeignKey(on_delete=PROTECT)`,
`services`/`selectors`. Migratsiyalar, admin panel va ORM constraint DSL bepul keladi.

**Muhim:** Django **ovoz yo'lida turmaydi** — ovoz bridge alohida protsess. Shuning uchun Django'ning
sinxronligi kechikishga ta'sir qilmaydi. Bridge biznes amallarini **ichki HTTP API** (DRF, `localhost`,
mTLS) orqali chaqiradi.

**Rad etilgan:** FastAPI + SQLAlchemy — async yaxshi, lekin admin yo'q, migration kuchsizroq,
constraint DSL zaifroq; async afzalligi bizga kerak emas (ADR yuqorisiga qarang).

---

## ADR-005: Ma'lumotlar bazasi — PostgreSQL 16

**Qaror:** PostgreSQL 16, `pg_trgm` kengaytmasi bilan.

**Nega:**
- `CheckConstraint` — INV-01…INV-03 (manfiy zaxira taqiqlanadi)
- **PL/pgSQL trigger** — append-only ledger (INV-04): `BEFORE UPDATE OR DELETE → RAISE EXCEPTION`.
  Dastur kodi aylanib o'tolmaydi.
- **`pg_trgm` + GIN indeks** — ovozli so'rov xatosiga chidamli mahsulot izlash:
  *"samsung galaksi"* → `Samsung Galaxy`. Bu ovozli interfeys uchun **hayotiy**.
- `SELECT ... FOR UPDATE` — poyga holatlari (INV-02)
- `JSONB` — transkript va AI qarorlari

---

## ADR-006: Qolgan qatlamlar

| Qatlam | Tanlov | Qisqa asos |
| :--- | :--- | :--- |
| Kesh / sessiya state / rate-limit | **Redis 7** | Qo'ng'iroq state, `sessionResumption` token, sliding-window limit, Celery broker |
| Fon vazifalar | **Celery 5 + Beat** | Rezervatsiya TTL (INV-07), SMS, 90 kunlik yozuv tozalash, Instagram navbat |
| Telegram | **aiogram 3** | To'liq async, webhook, tugma/rasm/ovozli xabar |
| Instagram | **Meta Graph API + webhook** | Comment → private reply → DM (cheklovlar: ADR-007) |
| Audio resampling | **python-soxr** | libsoxr — sifatli 8↔16↔24 kHz |
| Admin / monitoring UI | **Django + HTMX + Alpine.js + Tailwind** | Jonli qo'ng'iroq paneli SSE orqali; SPA murakkabligi asossiz (KISS) |
| Observability | **OpenTelemetry + Prometheus + Grafana + Loki** | 8-ustun talabi; `trace_id` audio→AI→DB→TTS zanjirida |
| Testlar | **pytest + pytest-django + Hypothesis + Locust** | Hypothesis — property-based: *hech qanday rezervatsiya ketma-ketligi zaxirani manfiyga tushirmaydi*. Locust — 50 parallel qo'ng'iroq |
| Lint / tiplar | **ruff + mypy (strict)** | Konstitutsiya talabi |
| CI/CD | **GitHub Actions** | Har commitda: ruff → mypy → migratsiya tekshiruvi → testlar |
| Konteyner | **Docker + docker compose** | 12-factor, bir buyruq bilan ko'tarilish |

---

## ADR-007: Omnichannel platforma cheklovlari (o'zgartirilmaydigan haqiqat)

Bu cheklovlar **bizning tanlovimiz emas** — platforma qoidalari. Arxitektura ularni hisobga olib
quriladi, aylanib o'tishga urinish **akkaunt bloklanishiga** olib keladi.

### Telegram
- ❌ Bot foydalanuvchiga **birinchi yoza olmaydi** → `403 Forbidden: bot can't initiate conversation`.
- ✅ Foydalanuvchi `/start` bossa yoki deep-link'ni ochsa — `chat_id` saqlanadi, keyin istalgan paytda yozish mumkin.
- **Bizning yechim:** qo'ng'iroq davomida AI SMS orqali imzolangan deep-link yuboradi
  (`t.me/<bot>?start=<signed_token>`). Mijoz bossa — `chat_id` ↔ `Customer` bog'lanadi (opt-in).
  Token bir martalik, 15 daqiqa amal qiladi, HMAC bilan imzolangan.

### Instagram
- ✅ **Private reply:** comment qoldirgan odamga DM yuborish mumkin — **har comment uchun faqat 1 marta**,
  comment'dan **7 kun** ichida, **750/soat** limit.
- ⚠️ Keyingi xabar faqat mijoz javob bersa mumkin → **24 soatlik window** ochiladi.
- 🚫 **`human_agent` tegi (7 kunlik window) — AI hech qachon ishlatmaydi.** Meta bu tegni faqat
  *haqiqiy odam* javob bergan holatga ruxsat beradi; bot uchun ishlatish —
  **API'dan mahrum bo'lishning eng tez yo'li**. Teg faqat `HUMAN_OPERATOR` javobida qo'yiladi.

**Oqibat (biznes qoidasi):** private reply — **bitta urinish**. Shuning uchun birinchi DM
*mukammal* bo'lishi shart: aniq javob + mijozni javob berishga undovchi savol (CTA), aks holda
24 soatlik window ochilmaydi va lead yo'qoladi.

---

## Yakuniy arxitektura chizmasi

```
   ☎ Mijoz (telefon)          💬 Telegram            📷 Instagram (comment + DM)
         │                         │                         │
   SIP trunk (UZ)             aiogram 3 webhook        Meta Graph webhook
         │                         │                         │
   ┌─────▼──────┐                  │                         │
   │ Asterisk 22│                  │                         │
   │ ARI + Audio│                  │                         │
   │   Socket   │                  │                         │
   └─────┬──────┘                  │                         │
    20ms │ PCM 8kHz                │                         │
   ┌─────▼───────────────────────────────────────────────────▼──────┐
   │        OVOZ / KANAL BRIDGE  (Python asyncio + uvloop)          │
   │  soxr resampling · GoAway+sessionResumption · barge-in hisobi  │
   └─────┬──────────────────────────────────────────────────┬───────┘
         │ PCM 16kHz (audio/pcm;rate=16000)                 │
   ┌─────▼─────────────────────┐                            │
   │   Gemini 3.8 Live         │   tool call (NON_BLOCKING) │
   │   native audio + tools    ├────────────────────────────┤
   │   99 til · uz · 128k ctx  │                            │
   └───────────────────────────┘                            │
                                              ichki HTTP API (mTLS)
   ┌──────────────────────────────────────────────────────────▼─────┐
   │              DJANGO 5.2 — BIZNES YADRO (CQS)                   │
   │   views → services (write, atomic, FOR UPDATE) → selectors     │
   │   customers · catalog · inventory · orders · audit             │
   └───────┬──────────────────────────────┬─────────────────────────┘
           │                              │
   ┌───────▼────────┐            ┌────────▼────────┐
   │ PostgreSQL 16  │            │    Redis 7      │
   │ constraints    │            │ state · limits  │
   │ PL/pgSQL       │            │ Celery broker   │
   │ pg_trgm        │            └─────────────────┘
   └────────────────┘
```

---

*1-bosqich tayyorlandi: 2026-11-23*
