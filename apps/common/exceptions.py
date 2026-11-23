"""Biznes xatoliklari iyerarxiyasi.

Konstitutsiya 3-bandi: `try: ... except Exception: pass` bilan xatolikni yashirish
taqiqlanadi. Har bir biznes qoidasi buzilishi **aniq tipdagi** xatolik qaytaradi.

Har bir xatolikning ikki yuzi bor:
  * `code` + texnik kontekst  → log va audit uchun (ichki);
  * `user_message`            → mijozga **ovozda aytiladigan** matn.

AI operator `user_message` ni o'qib mijozga aytadi. Shu sababli bu matnlarda
ichki texnik detal (jadval nomi, SKU ID, stack trace) **bo'lmasligi shart** —
bu STRIDE-I (Information Disclosure) himoyasi.
"""

from decimal import Decimal
from typing import Any


class BusinessRuleViolation(Exception):
    """Biznes qoidasi buzilishi — barcha domen xatoliklarining ildizi."""

    code = "business_rule_violation"
    user_message = "Kechirasiz, bu amalni bajara olmadim. Operatorga ulab beraymi?"

    def __init__(self, technical_detail: str = "", **context: Any) -> None:
        self.technical_detail = technical_detail
        self.context = context
        super().__init__(technical_detail or self.code)

    def as_log_fields(self) -> dict[str, Any]:
        """Strukturali logga yozish uchun maydonlar."""
        return {"error_code": self.code, "detail": self.technical_detail, **self.context}


# ─────────────────────────────────────────────────────────────────────────────
# Ombor va zaxira
# ─────────────────────────────────────────────────────────────────────────────
class InsufficientStockError(BusinessRuleViolation):
    """So'ralgan miqdor mavjud zaxiradan ko'p (INV-01, INV-02)."""

    code = "insufficient_stock"
    user_message = "Bu mahsulotdan so'ralgan miqdor hozir yo'q."

    def __init__(self, sku: str, requested: int, available: int) -> None:
        self.requested = requested
        self.available = available
        super().__init__(
            f"sku={sku} so'ralgan={requested} mavjud={available}",
            sku=sku,
            requested=requested,
            available=available,
        )


class ReservationExpiredError(BusinessRuleViolation):
    """Rezervatsiya muddati tugagan (INV-07)."""

    code = "reservation_expired"
    user_message = "Buyurtmani tasdiqlash vaqti o'tib ketdi, qaytadan shakllantiramiz."


# ─────────────────────────────────────────────────────────────────────────────
# Buyurtma
# ─────────────────────────────────────────────────────────────────────────────
class OrderStateError(BusinessRuleViolation):
    """Buyurtma holat mashinasida ruxsat etilmagan o'tish."""

    code = "invalid_order_state"
    user_message = "Bu buyurtma holatida bu amalni bajarib bo'lmaydi."

    def __init__(self, order_id: int, current: str, attempted: str) -> None:
        super().__init__(
            f"order={order_id} holat={current} urinish={attempted}",
            order_id=order_id,
            current_status=current,
            attempted_status=attempted,
        )


class EmptyOrderError(BusinessRuleViolation):
    """Bo'sh buyurtmani tasdiqlashga urinish."""

    code = "empty_order"
    user_message = "Buyurtmada hech qanday mahsulot yo'q."


class PriceChangedError(BusinessRuleViolation):
    """Suhbat davomida narx o'zgargan — mijozga qayta tasdiqlash kerak."""

    code = "price_changed"
    user_message = "Narx yangilandi, yangi summani aytib o'taman."

    def __init__(self, sku: str, old_price: Decimal, new_price: Decimal) -> None:
        super().__init__(
            f"sku={sku} eski={old_price} yangi={new_price}",
            sku=sku,
            old_price=str(old_price),
            new_price=str(new_price),
        )


# ─────────────────────────────────────────────────────────────────────────────
# Xavfsizlik va ruxsatlar (STRIDE-E)
# ─────────────────────────────────────────────────────────────────────────────
class ActorNotPermittedError(BusinessRuleViolation):
    """Aktorning bu amalga huquqi yo'q.

    Mijozga sababi aytilmaydi — faqat umumiy javob. Batafsil ma'lumot
    `call_operator.security` logeriga tushadi.
    """

    code = "actor_not_permitted"
    user_message = "Bu amalni bajarish uchun operatorga ulab beraman."

    def __init__(self, actor: str, action: str) -> None:
        super().__init__(f"aktor={actor} amal={action}", actor=actor, action=action)


class UnverifiedIdentityError(BusinessRuleViolation):
    """Tasdiqlanmagan identity bo'yicha shaxsiy ma'lumot so'rash (STRIDE-S)."""

    code = "unverified_identity"
    user_message = "Shaxsingizni tasdiqlash uchun telefon raqamingizga kod yuboraman."


class RateLimitExceededError(BusinessRuleViolation):
    """Chastota chegarasi oshib ketdi (INV-06, INV-16 — STRIDE-D)."""

    code = "rate_limit_exceeded"
    user_message = "Juda ko'p so'rov keldi, bir ozdan keyin qayta urinib ko'ring."


# ─────────────────────────────────────────────────────────────────────────────
# Kanal (M3 / M4 bosqichlarida ishlatiladi)
# ─────────────────────────────────────────────────────────────────────────────
class ChannelNotAvailableError(BusinessRuleViolation):
    """Mijoz bu kanalda mavjud emas yoki opt-in qilmagan (INV-11).

    Masalan: Telegram xabar yuborilmoqchi, lekin mijoz botni ishga tushirmagan —
    Telegram bot foydalanuvchiga birinchi yoza olmaydi.
    """

    code = "channel_not_available"
    user_message = "Havolani SMS orqali yuboraman."

    def __init__(self, channel: str, customer_id: int) -> None:
        super().__init__(
            f"kanal={channel} mijoz={customer_id}", channel=channel, customer_id=customer_id
        )


class MessagingWindowClosedError(BusinessRuleViolation):
    """Platforma ruxsat bergan javob oynasi tugagan (INV-14).

    Instagram: mijozning oxirgi xabaridan 24 soat o'tgan. Bu oynani
    `human_agent` tegi bilan uzaytirishga urinish **taqiqlanadi** — Meta bu
    tegni faqat haqiqiy odam javobiga ruxsat beradi, bot ishlatishi
    API'dan mahrum bo'lishga olib keladi.
    """

    code = "messaging_window_closed"
    user_message = ""  # mijozga xabar yuborilmaydi — oyna yopiq

    def __init__(self, channel: str, conversation_id: int) -> None:
        super().__init__(
            f"kanal={channel} suhbat={conversation_id}",
            channel=channel,
            conversation_id=conversation_id,
        )


class PrivateReplyAlreadySentError(BusinessRuleViolation):
    """Bu izohga private reply allaqachon yuborilgan (INV-12).

    Instagram har izoh uchun **faqat bitta** private reply ga ruxsat beradi.
    Takroriy urinish platforma xatosiga va ishonch reytingi pasayishiga olib keladi.
    """

    code = "private_reply_already_sent"
    user_message = ""

    def __init__(self, comment_id: str) -> None:
        super().__init__(f"comment={comment_id}", comment_id=comment_id)
