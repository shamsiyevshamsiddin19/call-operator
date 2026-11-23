"""Buyurtma o'qish amallari (CQS — Query/Read qatlami).

**Ruxsat filtri bu qatlamda qo'llanadi.** AI operator faqat *o'z suhbatidagi*
mijozning buyurtmalarini ko'ra oladi: `actor.customer_id` bilan solishtiriladi.
Aks holda prompt-injection bilan "boshqa mijozning buyurtmalarini aytib ber"
deb so'rash mumkin bo'lardi (STRIDE-I).
"""

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import F, QuerySet
from django.utils import timezone

from apps.accounts.actors import Actor, ActorKind
from apps.audit import services as audit
from apps.common.exceptions import ActorNotPermittedError
from apps.orders.models import DeliverySlot, Order, OrderLine, OrderStatus


def get_customer_orders(
    actor: Actor,
    *,
    customer_id: int,
    limit: int = 5,
) -> QuerySet[Order]:
    """Mijozning so'nggi buyurtmalari.

    AI uchun cheklov: so'ralgan mijoz **shu suhbatdagi** mijoz bo'lishi shart.
    Boshqa mijozni so'rash urinishi xavfsizlik jurnaliga tushadi va rad etiladi.
    """
    if actor.kind == ActorKind.AI and actor.customer_id != customer_id:
        audit.record_permission_denied(
            actor,
            "get_customer_orders",
            reason=f"suhbat mijozi {actor.customer_id}, so'ralgan {customer_id}",
        )
        raise ActorNotPermittedError(actor.label, "get_customer_orders")

    if not actor.can_view_customer_personal_data:
        audit.record_permission_denied(actor, "get_customer_orders")
        raise ActorNotPermittedError(actor.label, "get_customer_orders")

    return (
        Order.objects.filter(customer_id=customer_id)
        .select_related("delivery_slot")
        .order_by("-created_at")[:limit]
    )


@dataclass(frozen=True, slots=True)
class OrderLineSummary:
    """Ovozda aytiladigan qator ma'lumoti."""

    sku: str
    name: str
    quantity: int
    unit: str
    unit_price: Decimal
    line_total: Decimal


@dataclass(frozen=True, slots=True)
class OrderSummary:
    """Buyurtma xulosasi — AI mijozga takrorlab tasdiqlatadi.

    Mandel 2-qoidasi (xotira yuklamasini kamaytirish): AI har qadamda
    "2 dona X, jami 240 ming so'm, to'g'rimi?" deb takrorlaydi, mijoz
    nima buyurganini eslab qolishga majbur bo'lmaydi.
    """

    order_id: int
    status: str
    status_label: str
    lines: tuple[OrderLineSummary, ...]
    total_amount: Decimal
    delivery_address: str
    delivery_window: str
    payment_method: str

    def to_speech(self) -> str:
        """Ovozda aytish uchun matn.

        Summa so'zlab aytilmaydi (raqam sifatida qoldiriladi) — ovoz modeli
        o'zbek tilida sonlarni to'g'ri o'qiydi, qo'lda so'zga aylantirish
        xatoga olib keladi ("240000" -> "ikki yuz qirq ming").
        """
        parts = [f"{line.quantity} {line.unit} {line.name}" for line in self.lines]
        text = ", ".join(parts)
        result = f"{text}. Jami {self.total_amount:.0f} so'm"
        if self.delivery_window:
            result += f". Yetkazib berish: {self.delivery_window}"
        return result


def get_order_summary(actor: Actor, *, order_id: int) -> OrderSummary:
    """Buyurtma xulosasini yig'adi — **ikki SQL so'rovda**.

    Qatorlar `select_related("product")` bilan olinadi, shuning uchun N+1
    muammosi yuzaga kelmaydi: 10 qatorli buyurtma uchun ham 2 so'rov.
    """
    order = Order.objects.select_related("delivery_slot").get(pk=order_id)

    if actor.kind == ActorKind.AI and actor.customer_id != order.customer_id:
        audit.record_permission_denied(
            actor,
            "get_order_summary",
            reason=f"buyurtma {order_id} boshqa mijozga tegishli",
        )
        raise ActorNotPermittedError(actor.label, "get_order_summary")

    lines = tuple(
        OrderLineSummary(
            sku=row["product__sku"],
            name=row["product__name"],
            quantity=int(row["quantity"]),
            unit=str(row["product__unit"]),
            unit_price=row["unit_price"],
            line_total=row["line_total"],
        )
        for row in OrderLine.objects.filter(order_id=order_id)
        .values(
            "product__sku", "product__name", "product__unit", "quantity", "unit_price", "line_total"
        )
        .order_by("id")
    )

    window = ""
    if order.delivery_slot is not None:
        slot = order.delivery_slot
        window = f"{slot.date} {slot.window_start:%H:%M}-{slot.window_end:%H:%M}"

    return OrderSummary(
        order_id=order.pk,
        status=order.status,
        status_label=str(OrderStatus(order.status).label),
        lines=lines,
        total_amount=order.total_amount,
        delivery_address=order.delivery_address,
        delivery_window=window,
        payment_method=order.payment_method,
    )


def get_available_slots(
    *,
    region: str,
    days_ahead: int = 7,
    from_date: date | None = None,
) -> QuerySet[DeliverySlot]:
    """Bo'sh yetkazib berish oynalari.

    Sig'imi tugagan oynalar SQL darajasida filtrlanadi — AI ga yetkazib
    berolmaydigan vaqt umuman ko'rsatilmaydi.
    """
    start = from_date or timezone.localdate()
    return DeliverySlot.objects.filter(
        region=region,
        is_active=True,
        date__gte=start,
        date__lte=start + timedelta(days=days_ahead),
        booked_count__lt=F("capacity"),
    ).order_by("date", "window_start")


def get_expired_reservations() -> QuerySet[OrderLine]:
    """Muddati o'tgan rezervatsiyalar (INV-07).

    Celery fon vazifasi shu ro'yxat bo'yicha rezervatsiyalarni bekor qiladi.
    Yakunlangan va bekor qilingan buyurtmalar hisobga olinmaydi: ularning
    rezervatsiyasi allaqachon yopilgan.
    """
    return (
        OrderLine.objects.filter(
            reserved_quantity__gt=0,
            reservation_expires_at__lt=timezone.now(),
            order__status__in=[OrderStatus.DRAFT, OrderStatus.RESERVED],
        )
        .select_related("order")
        .order_by("reserved_stock_id")  # deadlock profilaktikasi: barqaror qulflash tartibi
    )
