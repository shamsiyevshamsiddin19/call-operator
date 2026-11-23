"""Audit va xavfsizlik jurnali — o'zgarmas (append-only).

**Nega bu jadvalda birorta ham `ForeignKey` yo'q.**
Audit jurnali tizimning boshqa hech bir qismiga qaram bo'lmasligi kerak:

* mijoz ma'lumotini o'chirishni so'rasa (maxfiylik talabi) `Customer` anonimlashadi —
  ammo "kim nima qildi" yozuvi qolishi shart;
* qo'ng'iroq transkripti 90 kundan keyin o'chadi — audit yozuvi qolishi shart;
* `PROTECT` zanjiri audit yozuvi tufayli boshqa yozuvni o'chirishga to'sqinlik
  qilmasligi kerak.

Shuning uchun bog'lanishlar oddiy `*_ref` butun sonlar sifatida saqlanadi.
Bu ataylab qilingan normalizatsiya buzilishi — audit jurnali uchun to'g'ri yechim.
"""

from typing import Any

from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.common.models import ImmutableModel


class AuditSeverity(models.TextChoices):
    """Hodisa darajasi.

    `SECURITY` yozuvlari alohida `call_operator.security` logeriga ham tushadi
    va hech qachon o'chirilmaydi (konstitutsiya 7-band).
    """

    INFO = "INFO", _("Ma'lumot")
    WARNING = "WARNING", _("Ogohlantirish")
    SECURITY = "SECURITY", _("Xavfsizlik hodisasi")


class AuditEventType(models.TextChoices):
    """Hodisa turlari.

    Yangi tur qo'shish mavjud kodni o'zgartirmaydi (Open/Closed) — `services`
    qatlami hodisani faqat yozib qo'yadi, talqin qilishni hisobot qatlami bajaradi.
    """

    # Biznes hodisalari
    ORDER_CREATED = "ORDER_CREATED", _("Buyurtma yaratildi")
    ORDER_STATUS_CHANGED = "ORDER_STATUS_CHANGED", _("Buyurtma holati o'zgardi")
    ORDER_CANCELLED = "ORDER_CANCELLED", _("Buyurtma bekor qilindi")
    STOCK_RESERVED = "STOCK_RESERVED", _("Zaxira rezerv qilindi")
    STOCK_RELEASED = "STOCK_RELEASED", _("Rezervatsiya bekor qilindi")
    STOCK_ADJUSTED = "STOCK_ADJUSTED", _("Zaxira tuzatildi")
    PRICE_CHANGED = "PRICE_CHANGED", _("Narx o'zgartirildi")
    DELIVERY_SLOT_BOOKED = "DELIVERY_SLOT_BOOKED", _("Yetkazib berish oynasi band qilindi")

    # Suhbat hodisalari
    CONVERSATION_STARTED = "CONVERSATION_STARTED", _("Suhbat boshlandi")
    ESCALATED_TO_HUMAN = "ESCALATED_TO_HUMAN", _("Odamga uzatildi")
    AI_TOOL_CALLED = "AI_TOOL_CALLED", _("AI amal chaqirdi")
    IDENTITY_VERIFIED = "IDENTITY_VERIFIED", _("Shaxs tasdiqlandi")

    # Xavfsizlik hodisalari (STRIDE)
    PERMISSION_DENIED = "PERMISSION_DENIED", _("Ruxsat berilmadi")
    RATE_LIMIT_HIT = "RATE_LIMIT_HIT", _("Chastota chegarasi oshdi")
    UNVERIFIED_DATA_REQUEST = "UNVERIFIED_DATA_REQUEST", _("Tasdiqlanmagan shaxs ma'lumot so'radi")
    INSUFFICIENT_STOCK_REQUEST = "INSUFFICIENT_STOCK_REQUEST", _("Zaxiradan ortiq miqdor so'raldi")
    SUSPICIOUS_INPUT = "SUSPICIOUS_INPUT", _("Shubhali kiruvchi matn")


class AuditEvent(ImmutableModel):
    """Audit jurnali yozuvi.

    `payload` ichiga hodisaga xos tafsilot yoziladi (JSONB). Ammo **shaxsiy
    ma'lumot to'liq ko'rinishda yozilmaydi**: telefon raqami `mask_phone()`
    bilan yashiriladi, to'liq manzil va transkript matni bu yerga tushmaydi.
    """

    event_type = models.CharField(
        _("hodisa turi"),
        max_length=40,
        choices=AuditEventType.choices,
        db_index=True,
    )
    severity = models.CharField(
        _("darajasi"),
        max_length=10,
        choices=AuditSeverity.choices,
        default=AuditSeverity.INFO,
        db_index=True,
    )
    actor_label = models.CharField(
        _("aktor"),
        max_length=100,
        db_index=True,
        help_text=_("Actor.label — `ai:VOICE:conv42`, `staff:7:WAREHOUSE`, `system`."),
    )
    summary = models.CharField(
        _("qisqa mazmuni"),
        max_length=300,
        help_text=_("Odam o'qishi uchun bir qatorli bayon."),
    )
    payload: "models.JSONField[dict[str, Any]]" = models.JSONField(
        _("tafsilotlar"),
        default=dict,
        blank=True,
    )

    # ── Bog'lanishlar (FK emas — yuqoridagi izohga qarang) ───────────────────
    customer_ref = models.BigIntegerField(_("mijoz havolasi"), null=True, blank=True, db_index=True)
    order_ref = models.BigIntegerField(_("buyurtma havolasi"), null=True, blank=True, db_index=True)
    conversation_ref = models.BigIntegerField(
        _("suhbat havolasi"), null=True, blank=True, db_index=True
    )
    trace_id = models.CharField(
        _("kuzatuv identifikatori"),
        max_length=64,
        blank=True,
        db_index=True,
    )

    # Xavfsizlik hodisalari uchun (STRIDE-R)
    source_ip = models.GenericIPAddressField(_("manba IP"), null=True, blank=True)

    class Meta:
        verbose_name = _("audit yozuvi")
        verbose_name_plural = _("audit jurnali")
        indexes = [
            models.Index(fields=["severity", "-created_at"], name="audit_severity_time_idx"),
            models.Index(fields=["event_type", "-created_at"], name="audit_type_time_idx"),
            models.Index(fields=["customer_ref", "-created_at"], name="audit_customer_time_idx"),
        ]

    def __str__(self) -> str:
        return f"[{self.severity}] {self.get_event_type_display()} — {self.actor_label}"
