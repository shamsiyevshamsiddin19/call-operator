"""Aktor abstraksiyasi — kim amal bajarayotganini bildiruvchi o'zgarmas obyekt.

**Nega bu kerak (Dependency Inversion — SOLID D).**
`services` qatlami HTTP so'rovni (`request`) qabul qilmaydi, chunki amallar uch xil
joydan chaqiriladi: ovoz bridge (Gemini tool-call), Celery fon vazifasi va admin panel.
Agar `services` `request` ga bog'lansa, ovoz bridge'da soxta `request` yasashga
to'g'ri keladi — bu "vibe coding" yamog'i bo'lardi.

Shuning uchun barcha `services` funksiyalari birinchi argument sifatida `Actor` oladi.

**Ruxsatlar shu yerda markazlashgan (SOLID S).** Ruxsat tekshiruvi kod bo'ylab
tarqab ketmaydi: `actor.can_view_cost_price` kabi bitta haqiqat manbasi bo'ladi.
AI operator uchun nozik huquqlar **hammasi `False`** — bu arxitektura kafolati,
prompt ko'rsatmasi emas (prompt buzilishi mumkin, kod buzilmaydi).
"""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from django.db import models
from django.utils.translation import gettext_lazy as _

if TYPE_CHECKING:
    from apps.accounts.models import User

from apps.accounts.models import StaffRole


class ActorKind(models.TextChoices):
    """Amalni bajaruvchi tomon turi."""

    STAFF = "STAFF", _("Xodim")
    AI = "AI", _("AI operator")
    CUSTOMER = "CUSTOMER", _("Mijoz")
    SYSTEM = "SYSTEM", _("Tizim (fon vazifasi)")


# Tannarx va foydani ko'rish huquqi bor rollar (STRIDE-I — Information Disclosure)
_COST_VISIBLE_ROLES = frozenset({StaffRole.WAREHOUSE, StaffRole.SUPERVISOR, StaffRole.ADMIN})

# Chegirma berish huquqi bor rollar (INV-05)
_DISCOUNT_ROLES = frozenset({StaffRole.SUPERVISOR, StaffRole.ADMIN})

# Zaxira miqdorini o'zgartirish huquqi bor rollar
_STOCK_ADJUST_ROLES = frozenset({StaffRole.WAREHOUSE, StaffRole.ADMIN})


@dataclass(frozen=True, slots=True)
class Actor:
    """Amal bajaruvchi tomon. O'zgarmas (frozen) — amal o'rtasida huquq o'zgarmaydi.

    `trace_id` butun zanjirni bog'laydi: audio frame → Gemini → tool-call →
    DB tranzaksiyasi → audit yozuvi. Nosozlikni kuzatish uchun hayotiy.
    """

    kind: ActorKind
    user: "User | None" = None
    customer_id: int | None = None
    conversation_id: int | None = None
    trace_id: str = ""
    channel: str = ""
    context: dict[str, str] = field(default_factory=dict)

    # ── Konstruktorlar (Factory Method — GoF) ────────────────────────────────

    @classmethod
    def staff(cls, user: "User", *, trace_id: str = "") -> "Actor":
        return cls(kind=ActorKind.STAFF, user=user, trace_id=trace_id)

    @classmethod
    def ai(
        cls,
        *,
        customer_id: int | None,
        conversation_id: int,
        channel: str,
        trace_id: str,
    ) -> "Actor":
        """AI operator aktori. Hech qanday nozik huquqqa ega emas."""
        return cls(
            kind=ActorKind.AI,
            customer_id=customer_id,
            conversation_id=conversation_id,
            channel=channel,
            trace_id=trace_id,
        )

    @classmethod
    def system(cls, *, trace_id: str = "") -> "Actor":
        """Fon vazifasi (Celery): rezervatsiya TTL, yozuvlarni tozalash."""
        return cls(kind=ActorKind.SYSTEM, trace_id=trace_id)

    # ── Identifikatsiya ──────────────────────────────────────────────────────

    @property
    def label(self) -> str:
        """Log va audit uchun qisqa nom."""
        if self.kind == ActorKind.STAFF and self.user is not None:
            return f"staff:{self.user.pk}:{self.user.role}"
        if self.kind == ActorKind.AI:
            return f"ai:{self.channel}:conv{self.conversation_id}"
        if self.kind == ActorKind.CUSTOMER:
            return f"customer:{self.customer_id}"
        return "system"

    @property
    def _role(self) -> str:
        return self.user.role if (self.kind == ActorKind.STAFF and self.user) else ""

    # ── Ruxsatlar: bitta haqiqat manbasi ─────────────────────────────────────

    @property
    def can_view_cost_price(self) -> bool:
        """Tannarx va foyda marjasini ko'rish. AI va mijoz uchun hech qachon."""
        return self._role in _COST_VISIBLE_ROLES

    @property
    def can_grant_discount(self) -> bool:
        """Chegirma berish yoki narxni o'zgartirish (INV-05). AI uchun taqiqlangan."""
        return self._role in _DISCOUNT_ROLES

    @property
    def can_adjust_stock(self) -> bool:
        """Zaxira miqdorini to'g'ridan-to'g'ri o'zgartirish.

        AI faqat *rezervatsiya* qila oladi — bu boshqa amal va o'z-o'zidan
        qaytariladigan (TTL bilan), shuning uchun xavfsiz.
        """
        return self._role in _STOCK_ADJUST_ROLES

    @property
    def can_use_human_agent_tag(self) -> bool:
        """Instagram `human_agent` tegi (7 kunlik oyna) — INV-13.

        Meta bu tegni **faqat haqiqiy odam** javob berganda ruxsat etadi.
        Bot uchun ishlatish — API'dan mahrum bo'lishning eng tez yo'li.
        Shu sababli AI aktori uchun qat'iy `False`.
        """
        return self._role in {StaffRole.HUMAN_OPERATOR, StaffRole.ADMIN}

    @property
    def can_view_customer_personal_data(self) -> bool:
        """Mijozning manzili, buyurtma tarixi, telefon raqami.

        AI ham ko'ra oladi — lekin faqat **tasdiqlangan identity** bo'yicha va
        faqat shu suhbatdagi mijoz uchun. Bu cheklovni `selectors` qatlami
        `actor.customer_id` bilan solishtirib qo'llaydi (STRIDE-S).
        """
        if self.kind == ActorKind.AI:
            return True
        return self._role in {
            StaffRole.HUMAN_OPERATOR,
            StaffRole.SUPERVISOR,
            StaffRole.WAREHOUSE,
            StaffRole.ADMIN,
        }

    @property
    def can_delete_records(self) -> bool:
        """Yozuvlarni o'chirish — faqat ma'mur, va faqat arxivlash orqali."""
        return self._role == StaffRole.ADMIN
