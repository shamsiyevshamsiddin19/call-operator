"""Buyurtmalar, qatorlar va yetkazib berish oynalari.

**Narx nusxasi (snapshot) nima uchun.** `OrderLine.unit_price` katalogdan
*nusxa olinadi*. Katalog narxi ertaga o'zgarsa, kechagi buyurtma summasi
o'zgarmasligi shart — aks holda hisobot va mijoz bilan kelishuv buziladi.

**Qator summasi DB darajasida hisoblanadi** (`GeneratedField`): `quantity * unit_price`
Python'da emas, bazada saqlanadi. Shu sababli hech qanday kod yo'li bu ikki
maydonni bir-biriga mos kelmaydigan holatda qoldirolmaydi (konstitutsiya 5-band —
deklarativ relatsion agregatsiya).
"""

from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import F, Q
from django.utils.translation import gettext_lazy as _

from apps.catalog.models import Product
from apps.common.constants import ChannelKind
from apps.common.models import TimestampedModel
from apps.customers.models import Customer


class OrderStatus(models.TextChoices):
    """Buyurtma holati."""

    DRAFT = "DRAFT", _("Qoralama")
    RESERVED = "RESERVED", _("Zaxira ushlab turilgan")
    CONFIRMED = "CONFIRMED", _("Tasdiqlangan")
    DELIVERING = "DELIVERING", _("Yetkazilmoqda")
    COMPLETED = "COMPLETED", _("Yakunlangan")
    CANCELLED = "CANCELLED", _("Bekor qilingan")


# Holat mashinasi: qaysi holatdan qaysi holatga o'tish mumkin.
#
# Bu lug'at — yagona haqiqat manbasi. `services` har o'tishda shu yerdan
# tekshiradi, shuning uchun "tasdiqlangan buyurtmani qoralamaga qaytarish"
# kabi mantiqsiz o'tishlar kod bo'ylab tarqalgan `if` lar bilan emas,
# bitta joyda taqiqlanadi (SOLID S + Open/Closed: yangi holat qo'shish
# mavjud kodni emas, shu lug'atni o'zgartiradi).
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    OrderStatus.DRAFT: frozenset({OrderStatus.RESERVED, OrderStatus.CANCELLED}),
    OrderStatus.RESERVED: frozenset({OrderStatus.CONFIRMED, OrderStatus.CANCELLED}),
    OrderStatus.CONFIRMED: frozenset({OrderStatus.DELIVERING, OrderStatus.CANCELLED}),
    OrderStatus.DELIVERING: frozenset({OrderStatus.COMPLETED, OrderStatus.CANCELLED}),
    # Yakuniy holatlar: bu yerdan chiqish yo'q. Qaytarish kerak bo'lsa —
    # yangi qaytarish operatsiyasi yaratiladi, eski buyurtma o'zgartirilmaydi.
    OrderStatus.COMPLETED: frozenset(),
    OrderStatus.CANCELLED: frozenset(),
}


class PaymentMethod(models.TextChoices):
    """To'lov usuli.

    Pul harakatining o'zi AI ixtiyorida emas (domen tahlili §5): AI faqat
    mijozning tanlovini *qayd etadi*, hech qanday to'lovni amalga oshirmaydi.
    """

    CASH = "CASH", _("Naqd (kuryerga)")
    CARD_ON_DELIVERY = "CARD_ON_DELIVERY", _("Karta (kuryerga, terminal)")
    TRANSFER = "TRANSFER", _("Bank o'tkazmasi")


class DeliverySlot(TimestampedModel):
    """Yetkazib berish oynasi va uning sig'imi.

    Sig'im nazorati — ikkinchi poyga holati manbasi (zaxiradan keyin): bir
    vaqtda ikki mijoz oxirgi bo'sh oynani band qilishga urinishi mumkin.
    Shu sababli band qilish `select_for_update()` bilan bajariladi.
    """

    date = models.DateField(_("sana"), db_index=True)
    window_start = models.TimeField(_("boshlanish vaqti"))
    window_end = models.TimeField(_("tugash vaqti"))
    region = models.CharField(_("hudud"), max_length=100, db_index=True)
    capacity = models.PositiveIntegerField(
        _("sig'imi"),
        help_text=_("Shu oynada nechta buyurtma yetkazib berilishi mumkin."),
    )
    booked_count = models.PositiveIntegerField(_("band qilingan"), default=0)
    is_active = models.BooleanField(_("faol"), default=True, db_index=True)

    class Meta:
        verbose_name = _("yetkazib berish oynasi")
        verbose_name_plural = _("yetkazib berish oynalari")
        constraints = [
            models.UniqueConstraint(
                fields=["date", "window_start", "region"],
                name="slot_unique_per_date_window_region",
            ),
            # Sig'imdan ortiq band qilish taqiqlanadi — AI mijozga yetkazib
            # berolmaydigan vaqtni aytib qo'ymasligi uchun baza darajasida kafolat.
            models.CheckConstraint(
                condition=Q(booked_count__lte=F("capacity")),
                name="slot_booked_not_exceeding_capacity",
            ),
            models.CheckConstraint(
                condition=Q(window_end__gt=F("window_start")),
                name="slot_window_end_after_start",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.date} {self.window_start:%H:%M}-{self.window_end:%H:%M} ({self.region})"

    @property
    def free_capacity(self) -> int:
        return self.capacity - self.booked_count


class Order(TimestampedModel):
    """Buyurtma.

    `total_amount` — qatorlar summasining nusxasi (INV-03). U `services`
    qatlamida **SQL agregatsiya** bilan qayta hisoblanadi, Python siklida emas.
    """

    customer = models.ForeignKey(
        Customer,
        on_delete=models.PROTECT,  # INV-09: buyurtmasi bor mijoz o'chirilmaydi
        related_name="orders",
        verbose_name=_("mijoz"),
    )
    status = models.CharField(
        _("holati"),
        max_length=20,
        choices=OrderStatus.choices,
        default=OrderStatus.DRAFT,
        db_index=True,
    )
    total_amount = models.DecimalField(
        _("jami summa"),
        max_digits=16,
        decimal_places=2,
        default=Decimal("0"),
        validators=[MinValueValidator(Decimal("0"))],
    )

    # ── Kelib chiqishi (qaysi kanal, kim yaratdi) ────────────────────────────
    source_channel = models.CharField(
        _("kelgan kanali"),
        max_length=20,
        choices=ChannelKind.choices,
        db_index=True,
    )
    created_by_label = models.CharField(
        _("yaratgan aktor"),
        max_length=100,
        help_text=_("Actor.label — AI yoki xodim (STRIDE-R)."),
    )
    conversation_ref = models.BigIntegerField(
        _("suhbat havolasi"),
        null=True,
        blank=True,
        db_index=True,
        help_text=_(
            "FK emas: suhbat yozuvi maxfiylik muddati bo'yicha o'chsa ham buyurtma qoladi."
        ),
    )
    trace_id = models.CharField(_("kuzatuv identifikatori"), max_length=64, blank=True)

    # ── Yetkazib berish ──────────────────────────────────────────────────────
    delivery_slot = models.ForeignKey(
        DeliverySlot,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="orders",
        verbose_name=_("yetkazib berish oynasi"),
    )
    delivery_address = models.CharField(_("manzil"), max_length=500, blank=True)
    recipient_name = models.CharField(_("qabul qiluvchi"), max_length=200, blank=True)
    recipient_phone = models.CharField(_("qabul qiluvchi telefoni"), max_length=20, blank=True)
    delivery_notes = models.CharField(_("yetkazib berish izohi"), max_length=500, blank=True)

    payment_method = models.CharField(
        _("to'lov usuli"),
        max_length=20,
        choices=PaymentMethod.choices,
        blank=True,
    )

    # ── Bekor qilish ─────────────────────────────────────────────────────────
    cancelled_at = models.DateTimeField(_("bekor qilingan vaqti"), null=True, blank=True)
    cancellation_reason = models.CharField(_("bekor qilish sababi"), max_length=300, blank=True)

    class Meta:
        verbose_name = _("buyurtma")
        verbose_name_plural = _("buyurtmalar")
        constraints = [
            models.CheckConstraint(
                condition=Q(total_amount__gte=0),
                name="order_total_non_negative",
            ),
            # Bekor qilingan buyurtmada sabab va vaqt bo'lishi shart.
            # "Shunchaki bekor qilindi" degan yozuv nizoda hech narsani isbotlamaydi.
            models.CheckConstraint(
                condition=~Q(status=OrderStatus.CANCELLED)
                | (Q(cancelled_at__isnull=False) & ~Q(cancellation_reason="")),
                name="order_cancelled_requires_reason_and_time",
            ),
            # Tasdiqlangan va undan keyingi holatlarda to'lov usuli tanlangan bo'lishi shart.
            models.CheckConstraint(
                condition=~Q(
                    status__in=[
                        OrderStatus.CONFIRMED,
                        OrderStatus.DELIVERING,
                        OrderStatus.COMPLETED,
                    ]
                )
                | ~Q(payment_method=""),
                name="order_confirmed_requires_payment_method",
            ),
        ]
        indexes = [
            models.Index(fields=["customer", "-created_at"], name="order_customer_time_idx"),
            models.Index(fields=["status", "-created_at"], name="order_status_time_idx"),
        ]

    def __str__(self) -> str:
        return f"Buyurtma #{self.pk} ({self.get_status_display()}) — {self.total_amount}"

    def can_transition_to(self, new_status: str) -> bool:
        """Holat mashinasi bo'yicha o'tish mumkinmi (enkapsulyatsiya)."""
        return new_status in ALLOWED_TRANSITIONS.get(self.status, frozenset())


class OrderLine(TimestampedModel):
    """Buyurtma qatori.

    Rezervatsiya holati shu qatorda saqlanadi: alohida `Reservation` jadvali
    qo'shilmadi, chunki bir qator uchun aynan bitta rezervatsiya bo'ladi —
    qo'shimcha jadval ma'lumotni ikkiga bo'lib, mosligini saqlash muammosini
    yaratardi (KISS — Hooker 2).
    """

    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,  # qator buyurtmasiz ma'nosiz
        related_name="lines",
        verbose_name=_("buyurtma"),
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,  # INV-09
        related_name="order_lines",
        verbose_name=_("mahsulot"),
    )
    quantity = models.PositiveIntegerField(_("miqdori"))
    unit_price = models.DecimalField(
        _("dona narxi"),
        max_digits=14,
        decimal_places=2,
        help_text=_("Buyurtma yaratilgan paytdagi katalog narxining nusxasi."),
    )
    line_total = models.GeneratedField(
        expression=F("quantity") * F("unit_price"),
        output_field=models.DecimalField(max_digits=16, decimal_places=2),
        db_persist=True,
        verbose_name=_("qator summasi"),
    )

    # ── Rezervatsiya holati (INV-07) ─────────────────────────────────────────
    reserved_stock = models.ForeignKey(
        "inventory.Stock",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="order_lines",
        verbose_name=_("rezerv qilingan zaxira"),
        help_text=_(
            "Qaysi ombordagi zaxiradan rezerv qilingani. Bekor qilishda "
            "aynan shu qatorga qaytarilishi uchun saqlanadi - ko'p omborli "
            "holatda mahsulot bo'yicha izlash noaniq natija berardi."
        ),
    )
    reserved_quantity = models.PositiveIntegerField(
        _("rezerv qilingan miqdor"),
        default=0,
        help_text=_("Omborda ushlab turilgan miqdor. Odatda `quantity` ga teng."),
    )
    reservation_expires_at = models.DateTimeField(
        _("rezervatsiya muddati"),
        null=True,
        blank=True,
        db_index=True,
        help_text=_("Shu vaqtdan keyin Celery rezervatsiyani avtomatik bekor qiladi."),
    )

    class Meta:
        verbose_name = _("buyurtma qatori")
        verbose_name_plural = _("buyurtma qatorlari")
        constraints = [
            # INV-08: bir buyurtmada bir mahsulot faqat bitta qatorda bo'ladi.
            # Aks holda AI bir mahsulotni ikki marta qo'shib, mijozga noto'g'ri
            # summa aytishi mumkin edi.
            models.UniqueConstraint(
                fields=["order", "product"],
                name="order_line_unique_product_per_order",
            ),
            models.CheckConstraint(
                condition=Q(quantity__gt=0), name="order_line_quantity_positive"
            ),
            models.CheckConstraint(
                condition=Q(unit_price__gte=0), name="order_line_unit_price_non_negative"
            ),
            # Rezerv miqdori buyurtma miqdoridan oshmaydi.
            models.CheckConstraint(
                condition=Q(reserved_quantity__lte=F("quantity")),
                name="order_line_reserved_not_exceeding_quantity",
            ),
            # Rezerv bor bo'lsa muddati va manbasi ham bo'lishi shart:
            # muddatsiz rezerv omborni abadiy bloklab qo'yardi (INV-07), manbasiz
            # rezervni esa bekor qilishda qaysi zaxiraga qaytarish noaniq bo'lardi.
            models.CheckConstraint(
                condition=Q(reserved_quantity=0)
                | (
                    Q(reserved_quantity__gt=0)
                    & Q(reservation_expires_at__isnull=False)
                    & Q(reserved_stock__isnull=False)
                ),
                name="order_line_reservation_requires_expiry_and_stock",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.product.sku} x {self.quantity}"
