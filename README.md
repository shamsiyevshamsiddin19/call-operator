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

- [ ] **M1** — Biznes yadro (`customers`, `catalog`, `inventory`, `orders`, `audit`)
- [ ] **M2** — Ovoz kanali (Asterisk + Gemini Live)
- [ ] **M3** — Telegram kanali
- [ ] **M4** — Instagram kanali
