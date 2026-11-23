# Call Operator — Omnichannel Avtonom AI Sotuv Agenti

O'zbek tilida gaplashuvchi, internet-magazinni **to'liq avtonom** yurituvchi AI agent:
telefon qo'ng'irog'ini ko'taradi, mahsulot topadi, zaxirani tranzaksion tekshiradi,
buyurtma yaratadi; qo'ng'iroq davomida mijozning Telegramiga kartochka yuboradi;
Instagram marketing postlari ostidagi izohlarni o'qib, DM'da suhbatni davom ettiradi.

## Kanallar

| Kanal | Holat | Qamrov |
| :--- | :--- | :--- |
| ☎ Ovoz (SIP / Asterisk) | M2 | Inbound + outbound qo'ng'iroq, o'zbek tili |
| 💬 Telegram | M3 | Qo'ng'iroq davomida rasm/xulosa/tasdiqlash tugmasi |
| 📷 Instagram | M4 | Izoh → private reply → DM → outbound qo'ng'iroq |

## Texnologiya

- **Ovoz AI:** Gemini 3.8 Live (native audio, speech-to-speech, o'zbek tili `uz`)
- **Telefoniya:** Asterisk 22 LTS + ARI + AudioSocket
- **Ovoz bridge:** Python 3.12 + asyncio + uvloop + python-soxr
- **Biznes yadro:** Django 5.2 LTS + DRF (CQS: `services` / `selectors`)
- **DB:** PostgreSQL 16 (`CheckConstraint`, PL/pgSQL append-only trigger, `pg_trgm`)
- **Kesh / navbat:** Redis 7 + Celery 5
- **Messaging:** aiogram 3 (Telegram), Meta Graph API (Instagram)

To'liq asoslar va rad etilgan alternativalar: [docs/01-texnologiya-stack.md](docs/01-texnologiya-stack.md)

## Muhandislik qoidalari

Loyiha [Enterprise Software Engineering konstitutsiyasiga](../../MY_SCILL/prompt/) qat'iy amal qiladi:

- **CQS qatlamlari:** `views` → `services` (yozish, `@transaction.atomic`, `select_for_update`)
  → `selectors` (o'qish, SQL agregatsiya) → `models` (constraints)
- **Hech qachon** Python siklida hisoblash — faqat SQL agregatsiya
- **Append-only ledger** — ombor va moliya yozuvlari PL/pgSQL trigger bilan himoyalangan
- **Test qamrovi ≥ 85%** — unit, integration, edge case, race condition (Hypothesis)
- **STRIDE** xavfsizlik modeli, ruxsatlar faqat server qatlamida
- `ruff` + `mypy --strict` + GitHub Actions CI

## Hujjatlar

1. [0-bosqich — Domen tahlili va FURPS+ talablar](docs/00-domen-tahlili.md)
2. [1-bosqich — Texnologiya stack qarorlari (ADR)](docs/01-texnologiya-stack.md)

## Ishlab chiqish bosqichlari

- [x] **M1** — Biznes yadro (`customers`, `catalog`, `inventory`, `orders`, `audit`)
      · 211 test, qamrov 96%, `ruff` + `mypy --strict` toza
- [ ] **M2** — Ovoz kanali (Asterisk + Gemini Live)
- [ ] **M3** — Telegram kanali
- [ ] **M4** — Instagram kanali

## Ishga tushirish

```bash
python3 -m venv .venv && .venv/bin/pip install ".[dev]"
cp .env.example .env          # qiymatlarni to'ldiring
docker compose up -d          # PostgreSQL 16 + Redis 7
.venv/bin/python manage.py migrate
.venv/bin/python -m pytest
```

Mashinada PostgreSQL allaqachon o'rnatilgan bo'lsa, Docker'siz ham ishlaydi —
`.env.example` dagi 2-variantga qarang.

## M1 nimasi tekshirilgan

| Invariant | Qanday tasdiqlangan |
| :--- | :--- |
| Parallel rezervatsiyada ortiqcha sotuv yo'q | `threading.Barrier` bilan ikki tranzaksiya bir vaqtda ishga tushiriladi; aynan bittasi o'tadi |
| Ombor daftari tahrirlanmaydi | `QuerySet.update()`, `QuerySet.delete()` va **xom SQL** uchun alohida testlar — PL/pgSQL trigger uchalasini ham to'sadi |
| Zaxira manfiyga tushmaydi | `CheckConstraint` ga to'g'ridan-to'g'ri `update()` bilan hujum qilinadi |
| Daftar va joriy qoldiq mos | Har amal ketma-ketligidan keyin `SUM(delta)` bilan solishtiriladi |
| AI tannarxni ko'rmaydi | Natija strukturasida maydon **umuman yo'q** (`hasattr` tekshiruvi) |
| AI boshqa mijozni ko'rmaydi | Urinish `ActorNotPermittedError` beradi va xavfsizlik jurnaliga tushadi |
| Tasdiqlanmagan AI sinonimi ishlamaydi | `FilteredRelation` bilan JOIN'dan chiqariladi |
| N+1 so'rov yo'q | `CaptureQueriesContext` bilan so'rov soni sanaladi (10 mahsulot = 1 so'rov) |
